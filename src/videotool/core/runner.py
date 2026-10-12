"""执行 Plan：跑 ffmpeg 子进程并解析进度，或直接调用 Pillow 转换图片。

对外只暴露 run_plan()，所有异常收敛为 RunResult，不向调用方抛出。
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .binaries import _decode, _no_window_kwargs
from .builder import Plan, PlanKind


@dataclass
class RunResult:
    ok: bool = False
    cancelled: bool = False
    returncode: int = 0
    error: str = ""
    output_size: int = 0
    log: list[str] = field(default_factory=list)


def _parse_progress_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or "=" not in line:
        return None
    key, _, value = line.partition("=")
    return key.strip(), value.strip()


class _ProgressReader:
    """把 ffmpeg -progress 的键值流换算成 0~1 的总体进度。"""

    def __init__(self, duration: float, pass_index: int, total_passes: int, on_progress):
        self.duration = duration
        self.pass_index = max(pass_index, 1)
        self.total_passes = max(total_passes, 1)
        self.on_progress = on_progress
        self.out_time = 0.0
        self.speed = ""

    def feed(self, key: str, value: str) -> None:
        if key in ("out_time_us", "out_time_ms"):
            try:
                self.out_time = float(value) / 1_000_000.0
            except ValueError:
                return
        elif key == "out_time":
            self.out_time = self._hms(value)
        elif key == "speed":
            cleaned = value.strip().rstrip("x").strip()
            self.speed = f"{cleaned}x" if cleaned not in ("", "N/A") else ""
        elif key in ("progress", "frame", "fps", "total_size", "bitrate"):
            pass
        else:
            return
        self._emit()

    @staticmethod
    def _hms(value: str) -> float:
        try:
            h, m, s = value.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        except (ValueError, AttributeError):
            return 0.0

    def _emit(self) -> None:
        ratio = self.out_time / self.duration if self.duration > 0 else 0.0
        ratio = max(0.0, min(ratio, 1.0))
        overall = (self.pass_index - 1 + ratio) / self.total_passes
        self.on_progress(overall, ratio, self.speed)


def _run_step(argv: list[str], duration: float, pass_index: int, total_passes: int,
              on_progress, cancel_event: threading.Event, log_sink: list[str]) -> int:
    """执行单个 ffmpeg step，返回 returncode；被取消返回 -1。"""
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            bufsize=0,
            **_no_window_kwargs(),
        )
    except OSError as exc:
        log_sink.append(f"无法启动 ffmpeg：{exc}")
        return -2

    reader = _ProgressReader(duration, pass_index, total_passes, on_progress)

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        buf: list[str] = []
        for raw in iter(proc.stderr.readline, b""):
            text = _decode(raw).rstrip("\r\n")
            if text:
                buf.append(text)
                if len(buf) > 400:
                    del buf[:200]
        log_sink.extend(buf)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    assert proc.stdout is not None
    for raw in iter(proc.stdout.readline, b""):
        if cancel_event.is_set():
            break
        parsed = _parse_progress_line(_decode(raw))
        if parsed:
            reader.feed(*parsed)

    if cancel_event.is_set():
        _terminate(proc)
        stderr_thread.join(timeout=3)
        return -1

    proc.wait()
    stderr_thread.join(timeout=5)
    return proc.returncode


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Pillow 图片转换
# --------------------------------------------------------------------------

def convert_image(src: str | Path, out: str | Path, options: dict) -> RunResult:
    """图片 -> 图片，使用 Pillow。"""
    result = RunResult()
    try:
        from PIL import Image
    except ImportError:
        result.error = "缺少 Pillow，无法进行静态图片转换"
        return result

    fmt = options.get("format", "PNG")
    quality = int(options.get("quality", 90))
    scale = options.get("scale")
    try:
        with Image.open(src) as im:
            try:
                im.seek(0)
            except (EOFError, AttributeError):
                pass
            if fmt in ("JPEG", "BMP") and im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            elif fmt == "PNG" and im.mode == "P":
                im = im.convert("RGBA")
            if scale:
                width = int(scale)
                height = max(1, round(im.height * width / im.width))
                im = im.resize((width, height), Image.LANCZOS)

            params: dict = {}
            if fmt == "JPEG":
                params = {"quality": quality, "subsampling": 0, "optimize": True}
            elif fmt == "WEBP":
                params = {"quality": quality, "lossless": bool(options.get("lossless")), "method": 5}
            elif fmt == "PNG":
                params = {"optimize": True}
            elif fmt == "TIFF":
                params = {"compression": "tiff_lzw"}
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            im.save(out, format=fmt, **params)
    except Exception as exc:  # Pillow 会抛多种异常，统一收敛
        result.error = f"图片转换失败：{exc}"
        return result

    result.ok = True
    try:
        result.output_size = Path(out).stat().st_size
    except OSError:
        pass
    return result


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def run_plan(plan: Plan, on_progress=None, on_log=None, cancel_event: threading.Event | None = None) -> RunResult:
    """执行一个 Plan。

    on_progress(overall: float, step_ratio: float, speed: str)
    on_log(line: str)
    """
    if on_progress is None:
        on_progress = lambda overall, ratio, speed: None
    if on_log is None:
        on_log = lambda line: None
    if cancel_event is None:
        cancel_event = threading.Event()

    result = RunResult()

    if plan.kind == PlanKind.PILLOW:
        if cancel_event.is_set():
            result.cancelled = True
            return result
        on_log("使用 Pillow 进行静态图片转换…")
        res = convert_image(_plan_source(plan), plan.output, plan.pillow)
        if res.ok:
            on_progress(1.0, 1.0, "")
        return res

    for note in plan.notes:
        on_log(f"提示：{note}")

    plan.output.parent.mkdir(parents=True, exist_ok=True)

    log_sink: list[str] = []
    for step in plan.steps:
        if cancel_event.is_set():
            result.cancelled = True
            return result
        on_log(f"执行：{' '.join(step.argv)}")
        code = _run_step(step.argv, plan.duration, step.pass_index,
                         step.total_passes, on_progress, cancel_event, log_sink)
        if code == -1:
            result.cancelled = True
            result.log = log_sink
            _cleanup(plan)
            return result
        if code != 0:
            result.returncode = code
            result.error = _last_error(log_sink) or f"ffmpeg 退出码 {code}"
            result.log = log_sink
            _cleanup(plan)
            for line in log_sink[-20:]:
                on_log(line)
            return result

    _cleanup(plan)
    result.ok = True
    result.log = log_sink
    try:
        result.output_size = plan.output.stat().st_size
    except OSError:
        result.error = "ffmpeg 执行成功但未找到输出文件"
        result.ok = False
    if result.ok:
        on_progress(1.0, 1.0, "")
    return result


def _plan_source(plan: Plan) -> str:
    """Pillow 计划里输入路径存在 output 无关的地方；由 plan.pillow['source'] 携带。"""
    return plan.pillow.get("source", "")


def _last_error(lines: list[str]) -> str:
    for line in reversed(lines):
        if any(token in line for token in ("Error", "error", "Invalid", "No such", "not found", "failed")):
            return line
    return lines[-1] if lines else ""


def _cleanup(plan: Plan) -> None:
    if plan.passlog is None:
        return
    base = plan.passlog
    for suffix in ("", "-0.log", "-0.log.mbtree", ".log", ".log.mbtree"):
        try:
            Path(str(base) + suffix).unlink()
        except OSError:
            pass