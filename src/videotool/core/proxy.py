"""预览代理：把选中片段转成一个低清小文件，供播放器原生循环预览。

为什么需要它：直接对原文件做 seek 回跳来循环，每次都要从最近的关键帧解码过来
（实测长 GOP 素材、入点距关键帧 3.67 秒时，每次回跳画面冻结约 143 毫秒），
表现就是「播放到结尾跳回开头」时卡一下。
改成播一个只包含选中片段的小文件、用播放器自身循环，全程零 seek，就不再卡。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .binaries import Toolchain, _decode, _no_window_kwargs
from .paths import temp_dir

PROXY_WIDTH = 480      # 预览画面宽度，够看清构图即可
PROXY_CRF = 28         # 预览不需要高画质
PROXY_AUDIO_KBPS = 128


def proxy_dir() -> Path:
    path = temp_dir() / "preview"
    path.mkdir(parents=True, exist_ok=True)
    return path


def proxy_path(source: str | Path, start_sec: float, end_sec: float) -> Path:
    """代理文件路径。按「源文件 + 区间」命名，同一区间复用同一文件。"""
    src = Path(source)
    tag = f"{int(round(start_sec * 1000))}_{int(round(end_sec * 1000))}"
    safe_stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in src.stem)[:40]
    return proxy_dir() / f"{safe_stem}__{tag}.mp4"


def proxy_argv(toolchain: Toolchain, source: str | Path, start_sec: float,
               end_sec: float, out: str | Path, *, has_audio: bool = True,
               source_width: int = 0) -> list[str]:
    """构造生成代理的 ffmpeg 命令。

    -ss 放在 -i 之前：这是 input seek，直接跳到最近关键帧，不逐帧解码，快得多；
    预览用不着帧精确。
    """
    duration = max(0.05, end_sec - start_sec)
    argv = [
        toolchain.ffmpeg,
        "-hide_banner", "-nostdin", "-y", "-v", "error",
        "-ss", f"{max(0.0, start_sec):.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
    ]
    if source_width > PROXY_WIDTH:
        argv += ["-vf", f"scale={PROXY_WIDTH}:-2:flags=fast_bilinear"]
    argv += [
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", str(PROXY_CRF),
        "-pix_fmt", "yuv420p",
        "-g", "30",          # 关键帧密一点，拖动进度条时定位更快
    ]
    if has_audio:
        argv += ["-c:a", "aac", "-b:a", f"{PROXY_AUDIO_KBPS}k"]
    else:
        argv += ["-an"]
    argv += ["-movflags", "+faststart", str(out)]
    return argv


def make_proxy(toolchain: Toolchain, source: str | Path, start_sec: float, end_sec: float,
               *, has_audio: bool = True, source_width: int = 0,
               timeout: float = 120.0) -> tuple[bool, str, Path]:
    """生成代理文件。返回 (是否成功, 错误信息, 路径)。已存在则直接复用。"""
    out = proxy_path(source, start_sec, end_sec)
    if out.exists() and out.stat().st_size > 0:
        return True, "", out

    argv = proxy_argv(toolchain, source, start_sec, end_sec, out,
                      has_audio=has_audio, source_width=source_width)
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc), out

    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        message = _decode(proc.stderr or b"").strip().splitlines()
        return False, (message[-1] if message else f"ffmpeg 退出码 {proc.returncode}"), out
    return True, "", out