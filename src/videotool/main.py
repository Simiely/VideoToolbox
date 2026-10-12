"""VideoToolbox 入口。

启动流程：定位 ffmpeg -> 立即显示窗口 -> 后台探测硬件编码器能力。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 让 `python src/videotool/main.py` 与打包态都能 import 到包
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from videotool import APP_NAME, __version__
from videotool.core.binaries import locate
from videotool.ui.main_window import MainWindow
from videotool.ui.theme import apply_theme


def _selftest(report_path: str) -> int:
    """自检模式：离屏跑一遍工具链定位、能力探测与界面构建，把结果写进报告文件。

    窗口程序没有控制台输出，所以结果写文件。用于打包后验证与故障排查：
        VideoToolbox.exe --selftest C:\\Temp\\report.txt
    """
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    lines: list[str] = []
    ok = True

    def log(text: str) -> None:
        lines.append(text)

    log(f"{APP_NAME} {__version__} 自检")
    log(f"Python {sys.version.split()[0]}  冻结={bool(getattr(sys, 'frozen', False))}")

    try:
        from PySide6 import __version__ as pyside_version

        log(f"PySide6 {pyside_version}")
    except Exception as exc:
        log(f"[失败] PySide6 不可用：{exc}")
        ok = False

    try:
        from PIL import __version__ as pillow_version

        log(f"Pillow {pillow_version}")
    except Exception as exc:
        log(f"[失败] Pillow 不可用：{exc}")
        ok = False

    toolchain = locate()
    if toolchain is None:
        log("[失败] 未找到 ffmpeg / ffprobe")
        _write_report(report_path, lines)
        return 1
    log(f"ffmpeg {toolchain.version}（来源 {toolchain.source}）")
    log(f"  {toolchain.ffmpeg}")

    from videotool.core.capabilities import detect

    try:
        caps = detect(toolchain)
    except Exception as exc:
        log(f"[失败] 能力探测异常：{exc}")
        _write_report(report_path, lines)
        return 1
    log(f"可用编码器 {len(caps.encoders)} 个")
    log(f"可用硬件编码器：{', '.join(e for e, _ in caps.hardware_options()) or '无'}")
    if caps.hw_failed:
        log(f"不可用（列表存在但试编码失败）：{', '.join(caps.hw_failed)}")

    for required in ("libx264", "aac", "libwebp", "gif"):
        if not caps.has(required):
            log(f"[失败] 缺少必需编码器：{required}")
            ok = False

    try:
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(toolchain, caps)
        log(f"界面构建成功：任务列 {window.model.columnCount()} 列，"
            f"输出格式 {window.panel.target_combo.count()} 种")
        window.close()
    except Exception as exc:
        log(f"[失败] 界面构建异常：{type(exc).__name__}: {exc}")
        ok = False

    ok = _selftest_convert(toolchain, caps, log) and ok
    ok = _selftest_player(toolchain, log) and ok

    log("自检结果：" + ("通过" if ok else "有问题"))
    _write_report(report_path, lines)
    return 0 if ok else 1


def _selftest_player(toolchain, log) -> bool:
    """验证预览播放器可用：QtMultimedia 的后端插件与 FFmpeg 运行库是否打进了包。

    这是打包最容易漏的一环（插件没收集到就不会报错，只是播不出画面）。
    """
    import subprocess
    import tempfile
    import time

    from videotool.core.binaries import _no_window_kwargs

    work = Path(tempfile.gettempdir()) / "VideoToolbox_selftest_player"
    work.mkdir(parents=True, exist_ok=True)
    clip = work / "播放自检.mp4"

    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
    except Exception as exc:
        log(f"[失败] 无法导入 QtMultimedia：{exc}")
        return False

    try:
        made = subprocess.run(
            [toolchain.ffmpeg, "-hide_banner", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=2",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(clip)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
            **_no_window_kwargs(),
        )
        if made.returncode != 0:
            log("[失败] 播放自检素材生成失败")
            return False

        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        player = QMediaPlayer()
        sink = QVideoSink()
        player.setVideoSink(sink)
        frames: list[int] = []
        errors: list[str] = []
        sink.videoFrameChanged.connect(lambda f: frames.append(f.width()))
        player.errorOccurred.connect(lambda err, msg: errors.append(str(msg)))

        player.setSource(QUrl.fromLocalFile(str(clip)))
        deadline = time.time() + 8
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
            if player.duration() > 0:
                break

        if player.duration() <= 0:
            log(f"[失败] 播放器读不到时长（后端插件可能没打包）：{errors}")
            return False
        log(f"播放器读到时长 {player.duration()} ms")

        player.play()
        deadline = time.time() + 6
        while time.time() < deadline and len(frames) < 5:
            app.processEvents()
            time.sleep(0.02)
        player.stop()

        if not frames:
            log(f"[失败] 播放器没有输出任何视频帧（FFmpeg 运行库可能缺失）：{errors}")
            return False
        log(f"播放器成功输出视频帧：{len(frames)} 帧，画面宽度 {frames[0]} px")
        return True
    except Exception as exc:
        log(f"[失败] 播放器自检异常：{type(exc).__name__}: {exc}")
        return False
    finally:
        for leftover in work.glob("*"):
            try:
                leftover.unlink()
            except OSError:
                pass


def _selftest_convert(toolchain, caps, log) -> bool:
    """端到端验证：造一段 1 秒测试素材，真实走一遍转码与图片转换管线。"""
    import subprocess
    import tempfile
    import time

    from videotool.core.binaries import _no_window_kwargs
    from videotool.core.builder import build
    from videotool.core.probe import probe
    from videotool.core.runner import run_plan
    from videotool.core.specs import EncodeSpec, Mode

    work = Path(tempfile.gettempdir()) / "VideoToolbox_selftest"
    work.mkdir(parents=True, exist_ok=True)
    source = work / "自检 素材.mp4"

    try:
        made = subprocess.run(
            [toolchain.ffmpeg, "-hide_banner", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=15:duration=1",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(source)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
            **_no_window_kwargs(),
        )
        if made.returncode != 0:
            log("[失败] 无法生成自检素材")
            return False

        info = probe(toolchain, source)
        if not info.ok:
            log(f"[失败] 自检素材探测失败：{info.error}")
            return False

        output = work / "自检 输出.mp4"
        spec = EncodeSpec(mode=Mode.VIDEO, target="mp4", video_encoder="libx264",
                          crf=30, preset_value="ultrafast", scale_width=160, audio_mode="none")
        started = time.monotonic()
        result = run_plan(build(spec, info, toolchain, caps, output))
        elapsed = time.monotonic() - started
        if not result.ok or not output.exists() or output.stat().st_size == 0:
            log(f"[失败] 端到端转码失败：{result.error or '输出为空'}")
            return False
        log(f"端到端转码成功：输出 {output.stat().st_size} 字节，耗时 {elapsed:.1f}s")

        # 图片转换（Pillow 路径）
        from PIL import Image

        image = work / "自检 图片.png"
        Image.new("RGB", (64, 48), (14, 99, 156)).save(image)
        jpg = work / "自检 图片.jpg"
        converted = run_plan(build(
            EncodeSpec(mode=Mode.IMAGE, target="jpg", image_quality=80),
            probe(toolchain, image), toolchain, caps, jpg,
        ))
        if not converted.ok or not jpg.exists():
            log(f"[失败] 图片转换失败：{converted.error}")
            return False
        log(f"图片转换成功：输出 {jpg.stat().st_size} 字节")
    except Exception as exc:
        log(f"[失败] 端到端验证异常：{type(exc).__name__}: {exc}")
        return False
    finally:
        for leftover in work.glob("*"):
            try:
                leftover.unlink()
            except OSError:
                pass
    return True


def _write_report(path: str, lines: list[str]) -> None:
    text = "\n".join(lines) + "\n"
    try:
        Path(path).write_text(text, encoding="utf-8")
    except OSError:
        pass
    print(text)


def main() -> int:
    if "--selftest" in sys.argv:
        index = sys.argv.index("--selftest") + 1
        report = sys.argv[index] if index < len(sys.argv) else str(
            Path(os.environ.get("TEMP", ".")) / "VideoToolbox_selftest.txt"
        )
        return _selftest(report)

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    apply_theme(app)

    toolchain = locate()
    if toolchain is None:
        QMessageBox.critical(
            None,
            f"{APP_NAME} 无法启动",
            "没有找到 ffmpeg / ffprobe。\n\n"
            "请把 ffmpeg.exe 和 ffprobe.exe 放到程序目录下的 ffmpeg 文件夹中，"
            "或将其所在目录加入系统 PATH 后重试。",
        )
        return 1

    window = MainWindow(toolchain)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())