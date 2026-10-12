"""探测当前 ffmpeg 构建所具备的能力：软件编码器与可用的硬件编码器。

硬件编码器不能只看 `-encoders` 列表是否存在：列表里有不代表本机有可用显卡/
驱动。因此这里对候选硬件编码器做一次 1 帧真实试编码来确认可用性。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from functools import lru_cache

from .binaries import Toolchain, _decode, _no_window_kwargs

# 硬件编码器注册表：厂商 -> {编码器名: 显示名}
HARDWARE_ENCODERS: dict[str, dict[str, str]] = {
    "NVIDIA": {
        "h264_nvenc": "H.264",
        "hevc_nvenc": "H.265",
        "av1_nvenc": "AV1",
    },
    "Intel QSV": {
        "h264_qsv": "H.264",
        "hevc_qsv": "H.265",
        "av1_qsv": "AV1",
    },
    "AMD AMF": {
        "h264_amf": "H.264",
        "hevc_amf": "H.265",
        "av1_amf": "AV1",
    },
    "MediaFoundation": {
        "h264_mf": "H.264",
        "hevc_mf": "H.265",
    },
}


@dataclass
class Capabilities:
    """一次探测得到的完整能力快照。"""

    encoders: set[str] = field(default_factory=set)
    hardware: dict[str, str] = field(default_factory=dict)  # encoder -> 显示名
    hw_failed: list[str] = field(default_factory=list)  # 在列表中但试编码失败

    def has(self, encoder: str) -> bool:
        return encoder in self.encoders

    def hardware_options(self) -> list[tuple[str, str]]:
        """返回 [(编码器名, "NVIDIA H.265"), ...]，用于界面下拉框。"""
        options: list[tuple[str, str]] = []
        for vendor, mapping in HARDWARE_ENCODERS.items():
            for enc, label in mapping.items():
                if enc in self.hardware:
                    options.append((enc, f"{vendor} {label}"))
        return options


@lru_cache(maxsize=8)
def list_encoders(ffmpeg: str) -> frozenset[str]:
    """解析 `ffmpeg -encoders`，返回全部可用编码器名。"""
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()

    names: set[str] = set()
    for line in _decode(proc.stdout or b"").splitlines():
        # 形如 " V....D libx264              libx264 H.264 / AVC ..."
        stripped = line.strip()
        if not stripped or stripped.startswith(("Encoders:", "------", "V.....")) and " " not in stripped:
            continue
        parts = stripped.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and set(parts[0]) <= set("VASFXD."):
            names.add(parts[1])
    return frozenset(names)


def _test_encoder(ffmpeg: str, encoder: str) -> bool:
    """对单个硬件编码器做一次极小的真实试编码。"""
    cmd = [
        ffmpeg,
        "-hide_banner", "-v", "error",
        "-f", "lavfi",
        "-i", "color=c=black:s=256x144:r=1",
        "-frames:v", "1",
        "-c:v", encoder,
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def detect(toolchain: Toolchain) -> Capabilities:
    """完整探测一遍能力。耗时主要在硬件试编码，约 1~3 秒。"""
    encoders = set(list_encoders(toolchain.ffmpeg))
    caps = Capabilities(encoders=encoders)

    for vendor, mapping in HARDWARE_ENCODERS.items():
        for enc, label in mapping.items():
            if enc not in encoders:
                continue
            if _test_encoder(toolchain.ffmpeg, enc):
                caps.hardware[enc] = label
            else:
                caps.hw_failed.append(enc)

    return caps