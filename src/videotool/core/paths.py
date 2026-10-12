"""应用资源路径解析（兼容开发态与 PyInstaller 打包态）。"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """当前是否运行在 PyInstaller 打包出的可执行文件中。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """返回资源基准目录。

    - 打包态：PyInstaller 解包目录（onedir 模式即 exe 同级的 _internal）。
    - 开发态：仓库根目录。
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[3]


def ffmpeg_dir() -> Path:
    """ffmpeg / ffprobe 二进制所在目录。"""
    if is_frozen():
        return app_root() / "ffmpeg"
    return app_root() / "assets" / "ffmpeg"


def temp_dir() -> Path:
    """本程序专用的临时工作目录（两遍编码的日志文件等）。"""
    import tempfile

    path = Path(tempfile.gettempdir()) / "VideoToolbox"
    path.mkdir(parents=True, exist_ok=True)
    return path