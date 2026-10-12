"""使用 ffprobe 读取媒体信息。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .binaries import Toolchain, _decode, _no_window_kwargs


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_fraction(value: str | None) -> float:
    """把 "30000/1001" 之类的分数字符串转成浮点帧率。"""
    if not value:
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        d = _to_float(den, 0.0)
        return _to_float(num) / d if d else 0.0
    return _to_float(value)


# 音轨语言的常见值，其余原样显示
LANGUAGE_NAMES = {
    "chi": "中文", "zho": "中文", "cmn": "普通话", "yue": "粤语",
    "eng": "英语", "jpn": "日语", "kor": "韩语", "fra": "法语",
    "deu": "德语", "spa": "西班牙语", "rus": "俄语", "por": "葡萄牙语",
    "ita": "意大利语", "tha": "泰语", "vie": "越南语", "ara": "阿拉伯语",
}

# 色彩相关字段的友好名称
COLOR_SPACE_NAMES = {
    "bt709": "BT.709", "bt470bg": "BT.601 PAL", "smpte170m": "BT.601 NTSC",
    "bt2020nc": "BT.2020", "bt2020c": "BT.2020 (常数亮度)",
    "gbr": "GBR", "yuvj420p": "YUV 全范围",
}
COLOR_TRANSFER_NAMES = {
    "bt709": "BT.709", "smpte2084": "PQ (HDR10)", "arib-std-b67": "HLG",
    "bt2020-10": "BT.2020 (10bit)", "bt2020-12": "BT.2020 (12bit)",
    "iec61966-2-1": "sRGB", "smpte170m": "BT.601 NTSC",
}


def _color_name(value: str, table: dict[str, str]) -> str:
    return table.get(value.lower(), value) if value else ""


# 单帧图片容器的 format_name 特征（不含 gif：GIF 可能是动图）
STILL_IMAGE_FORMATS = (
    "image2",
    "png_pipe",
    "jpeg_pipe",
    "webp_pipe",
    "bmp_pipe",
    "tiff_pipe",
)


@dataclass
class VideoStream:
    index: int
    codec: str
    width: int
    height: int
    fps: float
    pix_fmt: str
    bit_rate: int
    rotation: int = 0
    profile: str = ""
    level: int = 0
    bit_depth: int = 0
    color_space: str = ""
    color_transfer: str = ""
    color_primaries: str = ""
    field_order: str = ""
    sample_aspect_ratio: str = ""
    display_aspect_ratio: str = ""
    nb_frames: int = 0
    encoder_tag: str = ""

    @property
    def display_size(self) -> tuple[int, int]:
        """考虑旋转元数据后的显示宽高。"""
        if self.rotation % 180 == 90:
            return self.height, self.width
        return self.width, self.height

    @property
    def resolution_label(self) -> str:
        w, h = self.display_size
        return f"{w}x{h}"

    @property
    def profile_label(self) -> str:
        if not self.profile:
            return ""
        if self.level and self.level > 0:
            # H.264/H.265 的 level 用 10 表示 1.0，60 表示 6.0
            return f"{self.profile}@L{self.level / 10:g}"
        return self.profile

    @property
    def is_hdr(self) -> bool:
        return self.color_transfer.lower() in ("smpte2084", "arib-std-b67")

    @property
    def field_label(self) -> str:
        return {
            "progressive": "逐行",
            "tt": "隔行（上场优先）",
            "bb": "隔行（下场优先）",
            "tb": "隔行（上场优先）",
            "bt": "隔行（下场优先）",
        }.get(self.field_order.lower(), self.field_order)


@dataclass
class AudioStream:
    index: int
    codec: str
    channels: int
    sample_rate: int
    bit_rate: int
    language: str = ""
    channel_layout: str = ""
    sample_fmt: str = ""
    bit_depth: int = 0
    title: str = ""
    default: bool = False

    @property
    def channel_label(self) -> str:
        return {1: "单声道", 2: "立体声"}.get(self.channels, f"{self.channels}声道")

    @property
    def language_label(self) -> str:
        if not self.language or self.language.lower() in ("und", "unknown"):
            return "未标注"
        return LANGUAGE_NAMES.get(self.language.lower(), self.language)


@dataclass
class MediaInfo:
    """一个媒体文件的探测结果。"""

    path: Path
    duration: float = 0.0
    size_bytes: int = 0
    format_name: str = ""
    format_long_name: str = ""
    bit_rate: int = 0
    video: VideoStream | None = None
    audios: list[AudioStream] = field(default_factory=list)
    subtitle_count: int = 0
    format_tags: dict[str, str] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def has_audio(self) -> bool:
        return bool(self.audios)

    @property
    def is_still_image(self) -> bool:
        """单帧静态图：时长缺失，或容器本身就是单帧图片容器。

        图片文件的时长常常不是 0（例如 JPG 会被报成 0.04 秒），所以只看时长会漏判，
        漏判的后果是图片转图片白跑一次 ffmpeg 子进程。
        故意不把 gif 算进来：GIF 可能是动图，要保留动画处理路径。
        """
        if not self.has_video:
            return False
        if self.duration <= 0.0:
            return True
        return any(token in self.format_name for token in STILL_IMAGE_FORMATS)

    @property
    def size_label(self) -> str:
        return human_size(self.size_bytes)

    @property
    def duration_label(self) -> str:
        if self.duration <= 0:
            return "--:--"
        total = int(round(self.duration))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def summary(self) -> str:
        """给界面显示的一行摘要。"""
        if not self.ok:
            return f"读取失败：{self.error}"
        parts: list[str] = []
        if self.video:
            parts.append(f"{self.video.codec.upper()} {self.video.resolution_label}")
            if self.video.fps:
                parts.append(f"{self.video.fps:.3g}fps")
        if self.audios:
            a = self.audios[0]
            parts.append(f"{a.codec.upper()} {a.channel_label}")
        if self.is_still_image:
            parts.append("静态图")
        else:
            parts.append(self.duration_label)
        return " · ".join(parts)


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} TB"


def probe(toolchain: Toolchain, path: str | Path) -> MediaInfo:
    """探测单个文件。任何异常都收敛为 info.error，不抛给调用方。"""
    p = Path(path)
    info = MediaInfo(path=p)
    try:
        info.size_bytes = p.stat().st_size
    except OSError as exc:
        info.error = str(exc)
        return info

    data, error = _ffprobe_json(toolchain, p)
    if data is None:
        info.error = error
        return info

    _fill_format(info, data.get("format") or {})
    for stream in data.get("streams") or []:
        _fill_stream(info, stream)
    _finalize(info)
    return info


def _ffprobe_json(toolchain: Toolchain, path: Path) -> tuple[dict[str, Any] | None, str]:
    """调用 ffprobe 并解析 JSON，返回 (数据, 错误信息)；失败时数据为 None。"""
    cmd = [
        toolchain.ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)

    if proc.returncode != 0:
        message = _decode(proc.stderr).strip().splitlines()[-1] if proc.stderr else "ffprobe 返回错误"
        return None, message

    try:
        return json.loads(_decode(proc.stdout) or "{}"), ""
    except json.JSONDecodeError as exc:
        return None, f"解析 ffprobe 输出失败：{exc}"


def _stream_tags(stream: dict[str, Any]) -> dict[str, str]:
    """流级标签，键统一转小写。"""
    return {str(k).lower(): str(v) for k, v in (stream.get("tags") or {}).items()}


def _stream_rotation(stream: dict[str, Any]) -> int:
    """从 side_data_list 里取旋转角度（度，0~359）。"""
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            return int(_to_float(side["rotation"])) % 360
    return 0


def _make_video_stream(stream: dict[str, Any]) -> VideoStream:
    return VideoStream(
        index=_to_int(stream.get("index")),
        codec=str(stream.get("codec_name") or "?"),
        width=_to_int(stream.get("width")),
        height=_to_int(stream.get("height")),
        fps=_parse_fraction(stream.get("avg_frame_rate")) or _parse_fraction(stream.get("r_frame_rate")),
        pix_fmt=str(stream.get("pix_fmt") or ""),
        bit_rate=_to_int(stream.get("bit_rate")),
        rotation=_stream_rotation(stream),
        profile=str(stream.get("profile") or ""),
        level=_to_int(stream.get("level"), 0),
        bit_depth=_to_int(stream.get("bits_per_raw_sample"), 0),
        color_space=_color_name(str(stream.get("color_space") or ""), COLOR_SPACE_NAMES),
        color_transfer=_color_name(str(stream.get("color_transfer") or ""), COLOR_TRANSFER_NAMES),
        color_primaries=_color_name(str(stream.get("color_primaries") or ""), COLOR_SPACE_NAMES),
        field_order=str(stream.get("field_order") or ""),
        sample_aspect_ratio=str(stream.get("sample_aspect_ratio") or ""),
        display_aspect_ratio=str(stream.get("display_aspect_ratio") or ""),
        nb_frames=_to_int(stream.get("nb_frames"), 0),
        encoder_tag=_stream_tags(stream).get("encoder", ""),
    )


def _make_audio_stream(stream: dict[str, Any]) -> AudioStream:
    tags = _stream_tags(stream)
    return AudioStream(
        index=_to_int(stream.get("index")),
        codec=str(stream.get("codec_name") or "?"),
        channels=_to_int(stream.get("channels"), 1),
        sample_rate=_to_int(stream.get("sample_rate"), 0),
        bit_rate=_to_int(stream.get("bit_rate")),
        language=str(tags.get("language") or ""),
        channel_layout=str(stream.get("channel_layout") or ""),
        sample_fmt=str(stream.get("sample_fmt") or ""),
        bit_depth=_to_int(stream.get("bits_per_raw_sample"), 0),
        title=str(tags.get("title") or ""),
        default=bool(stream.get("disposition", {}).get("default", 0)),
    )


def _fill_format(info: MediaInfo, fmt: dict[str, Any]) -> None:
    """把 format 段的字段写进 MediaInfo。"""
    info.duration = _to_float(fmt.get("duration"))
    info.format_name = str(fmt.get("format_name") or "")
    info.format_long_name = str(fmt.get("format_long_name") or "")
    info.bit_rate = _to_int(fmt.get("bit_rate"), 0)
    info.format_tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}


def _fill_stream(info: MediaInfo, stream: dict[str, Any]) -> None:
    """按流类型填充：视频取第一条非封面流、音频全部收集、字幕只计数。"""
    kind = stream.get("codec_type")
    if kind == "video" and stream.get("disposition", {}).get("attached_pic", 0) == 0:
        if info.video is None:
            info.video = _make_video_stream(stream)
    elif kind == "audio":
        info.audios.append(_make_audio_stream(stream))
    elif kind == "subtitle":
        info.subtitle_count += 1


def _finalize(info: MediaInfo) -> None:
    """收尾判断：图片容器时长兜底、没有可用流时标记错误。"""
    # 单帧图片容器常常没有 duration，用时长为 0 兜底
    if info.video and info.duration <= 0 and "image" in info.format_name:
        info.duration = 0.0

    if info.video is None and not info.audios:
        info.error = "未找到可用的音视频流"


# --------------------------------------------------------------------------
# 参数报告（类似 MediaInfo 的文本输出）
# --------------------------------------------------------------------------

def human_bitrate(bits_per_second: float) -> str:
    if bits_per_second <= 0:
        return "未知"
    if bits_per_second >= 1_000_000:
        return f"{bits_per_second / 1_000_000:.2f} Mbps"
    return f"{bits_per_second / 1000:.0f} kbps"


def human_duration(seconds: float) -> str:
    if seconds <= 0:
        return "未知"
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def video_rows(v: VideoStream) -> list[tuple[str, str]]:
    """视频轨的 (参数名, 值) 列表，空值不返回。"""
    rows: list[tuple[str, str]] = [("编码", v.codec.upper())]
    if v.profile_label:
        rows.append(("Profile", v.profile_label))
    rows.append(("分辨率", f"{v.width}x{v.height}"))
    if v.display_size != (v.width, v.height):
        rows.append(("显示分辨率", f"{v.display_size[0]}x{v.display_size[1]}（旋转 {v.rotation}°）"))
    if v.display_aspect_ratio:
        rows.append(("画面比例", v.display_aspect_ratio))
    if v.sample_aspect_ratio and v.sample_aspect_ratio != "1:1":
        rows.append(("像素宽高比", v.sample_aspect_ratio))
    if v.fps:
        rows.append(("帧率", f"{v.fps:.3f} fps"))
    if v.nb_frames:
        rows.append(("总帧数", f"{v.nb_frames}"))
    if v.field_label:
        rows.append(("扫描方式", v.field_label))
    if v.pix_fmt:
        rows.append(("像素格式", v.pix_fmt))
    if v.bit_depth:
        rows.append(("位深", f"{v.bit_depth} bit"))
    if v.color_space:
        rows.append(("色彩空间", v.color_space))
    if v.color_primaries:
        rows.append(("色域原色", v.color_primaries))
    if v.color_transfer:
        rows.append(("传递特性", v.color_transfer + ("　← HDR" if v.is_hdr else "")))
    rows.append(("码率", human_bitrate(v.bit_rate)))
    if v.encoder_tag:
        rows.append(("编码器标记", v.encoder_tag))
    return rows


def audio_rows(a: AudioStream, ordinal: int) -> list[tuple[str, str]]:
    title = f"音频轨 {ordinal}"
    if a.title:
        title += f"（{a.title}）"
    if a.default:
        title += " [默认]"
    rows: list[tuple[str, str]] = [(title, a.codec.upper())]
    rows.append(("声道", f"{a.channel_label}"
                          + (f" / {a.channel_layout}" if a.channel_layout else "")))
    if a.sample_rate:
        rows.append(("采样率", f"{a.sample_rate} Hz"))
    if a.bit_depth:
        rows.append(("位深", f"{a.bit_depth} bit"))
    if a.sample_fmt:
        rows.append(("采样格式", a.sample_fmt))
    rows.append(("码率", human_bitrate(a.bit_rate)))
    rows.append(("语言", a.language_label))
    return rows


def summary_rows(info: MediaInfo) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    rows.append(("完整路径", str(info.path)))
    rows.append(("文件大小", f"{info.size_label}（{info.size_bytes} 字节）"))
    if info.format_long_name:
        rows.append(("容器", f"{info.format_long_name}（{info.format_name}）"))
    else:
        rows.append(("容器", info.format_name or "未知"))
    rows.append(("总时长", f"{human_duration(info.duration)}（{info.duration:.3f} 秒）"))
    rows.append(("总码率", human_bitrate(info.bit_rate)))
    rows.append(("流数量", f"视频 {1 if info.video else 0} / 音频 {len(info.audios)} / 字幕 {info.subtitle_count}"))
    if info.is_still_image:
        rows.append(("类型", "静态图片"))
    return rows


def _display_width(text: str) -> int:
    """按显示宽度算长度：中文/全角算 2，其余算 1。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(1, width - _display_width(text))


def text_report(info: MediaInfo) -> str:
    """生成可直接复制粘贴的参数文本，按显示宽度对齐（中英文混排也能对齐）。"""
    if not info.ok:
        return f"{info.path.name}\n读取失败：{info.error}"

    lines: list[str] = []
    pad = 22

    def put(name: str, value: str) -> None:
        lines.append(f"  {_pad(name, pad)}: {value}")

    lines.append(f"【基本】{info.path.name}")
    for name, value in summary_rows(info):
        put(name, value)

    if info.video:
        lines.append("")
        lines.append("【视频】")
        for name, value in video_rows(info.video):
            put(name, value)
    else:
        lines.append("")
        lines.append("【视频】无")

    if info.audios:
        for i, audio in enumerate(info.audios, start=1):
            lines.append("")
            lines.append(f"【音频 {i}】")
            for name, value in audio_rows(audio, i)[1:]:
                put(name, value)
    else:
        lines.append("")
        lines.append("【音频】无")

    if info.format_tags:
        lines.append("")
        lines.append("【容器标签】")
        for key, value in sorted(info.format_tags.items()):
            put(key, value)

    return "\n".join(lines)