"""定位 ffmpeg / ffprobe 可执行文件。

优先级：随程序打包的二进制 -> 系统 PATH。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .paths import ffmpeg_dir

_EXE = ".exe" if os.name == "nt" else ""

# 子进程一律不弹黑窗、输出按 UTF-8 解码
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _no_window_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def _decode(data: bytes) -> str:
    """ffmpeg 在 Windows 上可能输出 UTF-8 或本地代码页，做容错解码。"""
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class Toolchain:
    """一组可用的 ffmpeg 工具路径。"""

    ffmpeg: str
    ffprobe: str
    version: str
    source: str  # "bundled" 或 "system"


def _probe_version(exe: str) -> str:
    try:
        out = subprocess.run(
            [exe, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            **_no_window_kwargs(),
        )
        first = _decode(out.stdout).splitlines()[0] if out.stdout else ""
        # 形如 "ffmpeg version 9.0.2-full_build-www.gyan.dev Copyright ..."
        parts = first.split()
        return parts[2] if len(parts) >= 3 else first.strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


def locate() -> Toolchain | None:
    """查找可用的 ffmpeg / ffprobe。找不到返回 None。"""
    candidates: list[tuple[Path, str]] = []

    bundled = ffmpeg_dir()
    candidates.append((bundled / f"ffmpeg{_EXE}", "bundled"))
    candidates.append((bundled / f"ffprobe{_EXE}", "bundled"))

    bundled_ffmpeg = candidates[0][0]
    bundled_ffprobe = candidates[1][0]
    if bundled_ffmpeg.is_file() and bundled_ffprobe.is_file():
        return Toolchain(
            ffmpeg=str(bundled_ffmpeg),
            ffprobe=str(bundled_ffprobe),
            version=_probe_version(str(bundled_ffmpeg)),
            source="bundled",
        )

    sys_ffmpeg = shutil.which("ffmpeg")
    sys_ffprobe = shutil.which("ffprobe")
    if sys_ffmpeg and sys_ffprobe:
        return Toolchain(
            ffmpeg=sys_ffmpeg,
            ffprobe=sys_ffprobe,
            version=_probe_version(sys_ffmpeg),
            source="system",
        )

    return None