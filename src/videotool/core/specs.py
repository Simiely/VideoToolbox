"""输出格式矩阵与编码参数规格。

这里定义程序"能转出什么"，以及每种编码器的质量/速度参数映射。
所有默认值都取自各编码器官方推荐区间，并做了保守取值以保证"能用"。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    """任务类型。"""

    VIDEO = "video"          # 视频 -> 视频
    AUDIO = "audio"          # 提取/转码音频
    GIF = "gif"              # 视频/图片序列 -> GIF
    WEBP_ANIM = "webp_anim"  # 视频/图片序列 -> 动图 WebP
    APNG = "apng"            # 视频/图片序列 -> APNG
    IMAGE = "image"          # 视频 -> 单帧图片；图片 -> 图片
    IMAGE_SEQ = "image_seq"  # 图片序列 -> 视频
    MERGE = "merge"          # 视频 + 外部音频 -> 合并成一个文件


class QualityMode(str, Enum):
    CRF = "crf"                  # 恒定质量
    BITRATE = "bitrate"          # 恒定码率
    TARGET_SIZE = "target_size"  # 目标体积（两遍编码）


# --------------------------------------------------------------------------
# 编码器参数表
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class EncoderProfile:
    """单个视频编码器的参数写法。"""

    name: str
    family: str            # h264 / hevc / vp9 / av1
    label: str
    default_crf: int
    crf_range: tuple[int, int]
    # 质量模式 -> 参数模板，{q} 会被替换为质量值
    quality_args: tuple[str, ...]
    default_preset_args: tuple[str, ...]
    presets: tuple[tuple[str, str], ...] = ()   # (显示名, 参数值列表以逗号分隔) 用于界面

    def quality_argv(self, value: int) -> list[str]:
        return [a.replace("{q}", str(value)) for a in self.quality_args]


_PRESETS_X26X = (
    ("最快", "ultrafast"),
    ("很快", "superfast"),
    ("快", "veryfast"),
    ("较快", "faster"),
    ("中等（推荐）", "medium"),
    ("较慢（更小体积）", "slow"),
    ("慢", "slower"),
    ("最慢", "veryslow"),
)

_PRESETS_SVTAV1 = (
    ("最快", "13"), ("很快", "12"), ("快", "10"), ("中等（推荐）", "8"),
    ("较慢（更小体积）", "6"), ("慢", "4"), ("最慢", "2"),
)

_PRESETS_NVENC = (
    ("最快 P1", "p1"), ("很快 P2", "p2"), ("快 P3", "p3"), ("较快 P4", "p4"),
    ("中等（推荐）P5", "p5"), ("较慢（更小体积）P6", "p6"), ("最慢 P7", "p7"),
)

_PRESETS_VP9 = (
    ("最快", "5"), ("快", "4"), ("中等（推荐）", "2"), ("较慢（更小体积）", "1"), ("最慢", "0"),
)


ENCODER_PROFILES: dict[str, EncoderProfile] = {
    # ---- 软件编码 ----
    "libx264": EncoderProfile(
        name="libx264", family="h264", label="H.264 / x264（软编，兼容性最好）",
        default_crf=23, crf_range=(0, 51),
        quality_args=("-crf", "{q}"),
        default_preset_args=("-preset", "medium"),
        presets=_PRESETS_X26X,
    ),
    "libx265": EncoderProfile(
        name="libx265", family="hevc", label="H.265 / x265（软编，同画质体积更小）",
        default_crf=28, crf_range=(0, 51),
        quality_args=("-crf", "{q}"),
        default_preset_args=("-preset", "medium"),
        presets=_PRESETS_X26X,
    ),
    "libvpx-vp9": EncoderProfile(
        name="libvpx-vp9", family="vp9", label="VP9 / libvpx（软编，用于 WebM）",
        default_crf=32, crf_range=(0, 63),
        quality_args=("-crf", "{q}", "-b:v", "0"),
        default_preset_args=("-deadline", "good", "-cpu-used", "2"),
        presets=_PRESETS_VP9,
    ),
    "libsvtav1": EncoderProfile(
        name="libsvtav1", family="av1", label="AV1 / SVT-AV1（软编，压缩率最高）",
        default_crf=35, crf_range=(0, 63),
        quality_args=("-crf", "{q}"),
        default_preset_args=("-preset", "8"),
        presets=_PRESETS_SVTAV1,
    ),
    "libaom-av1": EncoderProfile(
        name="libaom-av1", family="av1", label="AV1 / libaom（软编，很慢）",
        default_crf=30, crf_range=(0, 63),
        quality_args=("-crf", "{q}", "-b:v", "0"),
        default_preset_args=("-cpu-used", "6", "-row-mt", "1"),
        presets=_PRESETS_VP9,
    ),
    # ---- NVIDIA NVENC ----
    "h264_nvenc": EncoderProfile(
        name="h264_nvenc", family="h264", label="NVIDIA H.264",
        default_crf=23, crf_range=(1, 51),
        quality_args=("-rc", "vbr", "-cq", "{q}", "-b:v", "0"),
        default_preset_args=("-preset", "p5"),
        presets=_PRESETS_NVENC,
    ),
    "hevc_nvenc": EncoderProfile(
        name="hevc_nvenc", family="hevc", label="NVIDIA H.265",
        default_crf=28, crf_range=(1, 51),
        quality_args=("-rc", "vbr", "-cq", "{q}", "-b:v", "0"),
        default_preset_args=("-preset", "p5"),
        presets=_PRESETS_NVENC,
    ),
    "av1_nvenc": EncoderProfile(
        name="av1_nvenc", family="av1", label="NVIDIA AV1",
        default_crf=35, crf_range=(1, 63),
        quality_args=("-rc", "vbr", "-cq", "{q}", "-b:v", "0"),
        default_preset_args=("-preset", "p5"),
        presets=_PRESETS_NVENC,
    ),
    # ---- Intel QSV ----
    "h264_qsv": EncoderProfile(
        name="h264_qsv", family="h264", label="Intel H.264",
        default_crf=23, crf_range=(1, 51),
        quality_args=("-global_quality", "{q}"),
        default_preset_args=("-preset", "medium"),
        presets=_PRESETS_X26X,
    ),
    "hevc_qsv": EncoderProfile(
        name="hevc_qsv", family="hevc", label="Intel H.265",
        default_crf=28, crf_range=(1, 51),
        quality_args=("-global_quality", "{q}"),
        default_preset_args=("-preset", "medium"),
        presets=_PRESETS_X26X,
    ),
    "av1_qsv": EncoderProfile(
        name="av1_qsv", family="av1", label="Intel AV1",
        default_crf=35, crf_range=(1, 63),
        quality_args=("-global_quality", "{q}"),
        default_preset_args=("-preset", "medium"),
        presets=_PRESETS_X26X,
    ),
    # ---- AMD AMF ----
    "h264_amf": EncoderProfile(
        name="h264_amf", family="h264", label="AMD H.264",
        default_crf=22, crf_range=(1, 51),
        quality_args=("-rc", "cqp", "-qp_i", "{q}", "-qp_p", "{q}"),
        default_preset_args=("-quality", "quality"),
        presets=(("速度优先", "speed"), ("均衡", "balanced"), ("质量优先", "quality")),
    ),
    "hevc_amf": EncoderProfile(
        name="hevc_amf", family="hevc", label="AMD H.265",
        default_crf=26, crf_range=(1, 51),
        quality_args=("-rc", "cqp", "-qp_i", "{q}", "-qp_p", "{q}"),
        default_preset_args=("-quality", "quality"),
        presets=(("速度优先", "speed"), ("均衡", "balanced"), ("质量优先", "quality")),
    ),
    "av1_amf": EncoderProfile(
        name="av1_amf", family="av1", label="AMD AV1",
        default_crf=30, crf_range=(1, 63),
        quality_args=("-rc", "cqp", "-qp_i", "{q}", "-qp_p", "{q}"),
        default_preset_args=("-quality", "quality"),
        presets=(("速度优先", "speed"), ("均衡", "balanced"), ("质量优先", "quality")),
    ),
    # ---- MediaFoundation ----
    "h264_mf": EncoderProfile(
        name="h264_mf", family="h264", label="MediaFoundation H.264",
        default_crf=23, crf_range=(1, 51),
        quality_args=("-rate_control", "quality", "-quality", "{q}"),
        default_preset_args=(),
        presets=(),
    ),
    "hevc_mf": EncoderProfile(
        name="hevc_mf", family="hevc", label="MediaFoundation H.265",
        default_crf=28, crf_range=(1, 51),
        quality_args=("-rate_control", "quality", "-quality", "{q}"),
        default_preset_args=(),
        presets=(),
    ),
}


def encoder_profile(name: str) -> EncoderProfile | None:
    return ENCODER_PROFILES.get(name)


# --------------------------------------------------------------------------
# 输出目标
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class VideoTarget:
    key: str
    label: str
    ext: str
    container: str
    video_families: tuple[str, ...]
    software_encoders: tuple[str, ...]
    audio_encoders: tuple[str, ...]
    default_audio: str
    faststart: bool = False
    extra_args: tuple[str, ...] = ()


VIDEO_TARGETS: dict[str, VideoTarget] = {
    "mp4": VideoTarget(
        key="mp4", label="MP4（最通用）", ext=".mp4", container="mp4",
        video_families=("h264", "hevc", "av1"),
        software_encoders=("libx264", "libx265", "libsvtav1", "libaom-av1"),
        audio_encoders=("aac", "libmp3lame"), default_audio="aac",
        faststart=True,
    ),
    "mkv": VideoTarget(
        key="mkv", label="MKV（封装最灵活）", ext=".mkv", container="matroska",
        video_families=("h264", "hevc", "vp9", "av1"),
        software_encoders=("libx264", "libx265", "libvpx-vp9", "libsvtav1", "libaom-av1"),
        audio_encoders=("aac", "libopus", "libmp3lame", "libvorbis", "flac"), default_audio="libopus",
    ),
    "mov": VideoTarget(
        key="mov", label="MOV（苹果/剪辑）", ext=".mov", container="mov",
        video_families=("h264", "hevc"),
        software_encoders=("libx264", "libx265"),
        audio_encoders=("aac",), default_audio="aac",
    ),
    "webm": VideoTarget(
        key="webm", label="WebM（网页）", ext=".webm", container="webm",
        video_families=("vp9", "av1"),
        software_encoders=("libvpx-vp9", "libsvtav1", "libaom-av1"),
        audio_encoders=("libopus", "libvorbis"), default_audio="libopus",
    ),
    "avi": VideoTarget(
        key="avi", label="AVI（老设备）", ext=".avi", container="avi",
        video_families=("h264",),
        software_encoders=("libx264",),
        audio_encoders=("libmp3lame",), default_audio="libmp3lame",
    ),
    "flv": VideoTarget(
        key="flv", label="FLV（直播/旧网页）", ext=".flv", container="flv",
        video_families=("h264",),
        software_encoders=("libx264",),
        audio_encoders=("aac",), default_audio="aac",
    ),
    "ts": VideoTarget(
        key="ts", label="TS（流媒体）", ext=".ts", container="mpegts",
        video_families=("h264", "hevc"),
        software_encoders=("libx264", "libx265"),
        audio_encoders=("aac",), default_audio="aac",
    ),
}


@dataclass(frozen=True)
class AudioTarget:
    key: str
    label: str
    ext: str
    encoder: str
    default_bitrate: int
    lossless: bool = False


AUDIO_TARGETS: dict[str, AudioTarget] = {
    "mp3": AudioTarget("mp3", "MP3（最通用）", ".mp3", "libmp3lame", 192),
    "m4a": AudioTarget("m4a", "M4A / AAC", ".m4a", "aac", 192),
    "opus": AudioTarget("opus", "Opus（同码率音质最好）", ".opus", "libopus", 128),
    "flac": AudioTarget("flac", "FLAC（无损）", ".flac", "flac", 0, lossless=True),
    "wav": AudioTarget("wav", "WAV（无损未压缩）", ".wav", "pcm_s16le", 0, lossless=True),
    "ogg": AudioTarget("ogg", "OGG Vorbis", ".ogg", "libvorbis", 160),
}


@dataclass(frozen=True)
class ImageTarget:
    key: str
    label: str
    ext: str
    animated: bool = False
    default_quality: int = 85


IMAGE_TARGETS: dict[str, ImageTarget] = {
    "jpg": ImageTarget("jpg", "JPG（照片）", ".jpg", default_quality=90),
    "png": ImageTarget("png", "PNG（无损/透明）", ".png"),
    "webp": ImageTarget("webp", "WebP（静态）", ".webp", default_quality=85),
    "bmp": ImageTarget("bmp", "BMP（无损无压缩）", ".bmp"),
    "tiff": ImageTarget("tiff", "TIFF（印刷）", ".tiff"),
    "gif": ImageTarget("gif", "GIF（可动图）", ".gif", animated=True),
    "webp_anim": ImageTarget("webp_anim", "WebP 动图", ".webp", animated=True, default_quality=75),
    "apng": ImageTarget("apng", "APNG 动图（保留透明）", ".png", animated=True),
}


# 常用分辨率预设
RESOLUTION_PRESETS: tuple[tuple[str, int | None], ...] = (
    ("保持原分辨率", None),
    ("3840 宽（4K）", 3840),
    ("2560 宽（2K）", 2560),
    ("1920 宽（1080P）", 1920),
    ("1280 宽（720P）", 1280),
    ("854 宽（480P）", 854),
    ("640 宽（360P）", 640),
    ("480 宽（GIF 常用）", 480),
    ("320 宽（小图）", 320),
)

# 常用帧率预设
FPS_PRESETS: tuple[tuple[str, float | None], ...] = (
    ("保持原帧率", None),
    ("60", 60.0),
    ("50", 50.0),
    ("30", 30.0),
    ("25", 25.0),
    ("24", 24.0),
    ("20", 20.0),
    ("15（GIF/动图常用）", 15.0),
    ("12", 12.0),
    ("10", 10.0),
)


@dataclass
class EncodeSpec:
    """一次转码任务的全部参数。"""

    mode: Mode = Mode.VIDEO
    target: str = "mp4"                     # VIDEO_TARGETS / AUDIO_TARGETS / IMAGE_TARGETS 的 key
    video_encoder: str = "libx264"
    quality_mode: QualityMode = QualityMode.CRF
    crf: int = 23
    video_bitrate: int = 2500               # kbps
    target_size_mb: float = 50.0
    preset_value: str = ""                  # 空表示用编码器默认
    scale_width: int | None = None          # None = 保持原分辨率
    fps: float | None = None
    audio_mode: str = "encode"              # encode / copy / none
    audio_encoder: str = ""                 # 空表示用目标默认
    audio_bitrate: int = 192
    trim_start: float | None = None         # 秒
    trim_end: float | None = None
    extra_filters: str = ""                 # 追加的 -vf 片段
    start_number: int = 1                   # 图片序列起始索引
    frame_count: int = 0                    # 图片序列取多少帧，0 = 到末尾

    # 音视频合并
    merge_audio: str = ""                   # 外部音频文件路径
    merge_audio_mode: str = "replace"       # replace = 替换原音轨；keep = 保留原音轨再追加
    merge_copy_video: bool = True           # 视频直接复制（最快、无损）
    merge_copy_audio: bool = False          # 音频直接复制（不重编码）

    # GIF / 动图参数
    anim_fps: float = 15.0
    anim_width: int | None = 480
    anim_loop: int = 0                      # 0 = 无限循环
    anim_quality: int = 75                  # WebP 质量
    anim_lossless: bool = False
    anim_dither: str = "bayer"              # GIF 抖动算法
    anim_max_colors: int = 256

    # 静态图片参数
    image_quality: int = 90
    image_time: float | None = None         # 抽帧时间点（秒）
    image_scale_width: int | None = None

    def copy_with(self, **kwargs) -> "EncodeSpec":
        from dataclasses import replace

        return replace(self, **kwargs)


def output_ext(spec: EncodeSpec) -> str:
    """根据 spec 推断输出扩展名（含点号）。"""
    if spec.mode == Mode.AUDIO:
        target = AUDIO_TARGETS.get(spec.target) or AUDIO_TARGETS["mp3"]
        return target.ext
    if spec.mode in (Mode.VIDEO, Mode.IMAGE_SEQ, Mode.MERGE):
        target = VIDEO_TARGETS.get(spec.target) or VIDEO_TARGETS["mp4"]
        return target.ext
    target = IMAGE_TARGETS.get(spec.target) or IMAGE_TARGETS["jpg"]
    return target.ext


def output_container_label(spec: EncodeSpec) -> str:
    """给界面显示的目标名称。"""
    if spec.mode == Mode.AUDIO:
        return (AUDIO_TARGETS.get(spec.target) or AUDIO_TARGETS["mp3"]).label
    if spec.mode in (Mode.VIDEO, Mode.IMAGE_SEQ, Mode.MERGE):
        return (VIDEO_TARGETS.get(spec.target) or VIDEO_TARGETS["mp4"]).label
    return (IMAGE_TARGETS.get(spec.target) or IMAGE_TARGETS["jpg"]).label