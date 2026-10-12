"""根据 EncodeSpec + MediaInfo + 能力快照，生成可执行的 ffmpeg 命令或 Pillow 指令。

设计要点：
- 所有编码器都要先经 Capabilities 校验，不存在的自动降级并在 notes 里说明。
- 两遍编码仅对确认支持 `-pass` 的软件编码器启用，硬件编码器走单遍 VBR。
- GIF 采用 split + palettegen + paletteuse 的单命令高质量方案。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from uuid import uuid4

from .binaries import Toolchain
from .capabilities import Capabilities
from .paths import temp_dir
from .probe import MediaInfo
from .specs import (
    AUDIO_TARGETS,
    IMAGE_TARGETS,
    VIDEO_TARGETS,
    EncodeSpec,
    Mode,
    QualityMode,
    encoder_profile,
)

# 确认支持 ffmpeg `-pass 1/2` 两遍编码的软件编码器
_TWO_PASS_ENCODERS = {"libx264", "libx265", "libvpx-vp9", "libaom-av1", "libsvtav1"}

# 各 family 的软件编码器兜底顺序
_SOFTWARE_BY_FAMILY: dict[str, tuple[str, ...]] = {
    "h264": ("libx264",),
    "hevc": ("libx265",),
    "vp9": ("libvpx-vp9",),
    "av1": ("libsvtav1", "libaom-av1"),
}

# Pillow 输出格式名
_PILLOW_FORMAT: dict[str, str] = {
    "jpg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "bmp": "BMP",
    "tiff": "TIFF",
    "gif": "GIF",
    "webp_anim": "WEBP",
    "apng": "PNG",
}


class PlanKind(str, Enum):
    FFMPEG = "ffmpeg"
    PILLOW = "pillow"


@dataclass
class Step:
    """一次子进程调用（两遍编码时为两个 step）。"""

    argv: list[str]
    pass_index: int = 1
    total_passes: int = 1


@dataclass
class Plan:
    kind: PlanKind
    output: Path
    steps: list[Step] = field(default_factory=list)
    duration: float = 0.0
    passlog: Path | None = None
    pillow: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _head(toolchain: Toolchain) -> list[str]:
    return [
        toolchain.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel", "warning",
        "-progress", "pipe:1",
        "-nostats",
    ]


def effective_duration(spec: EncodeSpec, info: MediaInfo) -> float:
    """考虑裁剪后的有效时长（秒）。"""
    total = info.duration if info.duration > 0 else 0.0
    start = spec.trim_start or 0.0
    end = spec.trim_end if spec.trim_end is not None else total
    if end <= 0:
        end = total
    return max(0.0, end - start)


def _audio_kbps(spec: EncodeSpec, info: MediaInfo) -> int:
    """估算音频占用码率，用于目标体积反推视频码率。"""
    if spec.audio_mode == "none":
        return 0
    if spec.audio_mode == "copy":
        if info.audios and info.audios[0].bit_rate:
            return max(int(info.audios[0].bit_rate / 1000), 0)
        return 128
    return max(spec.audio_bitrate, 0)


def target_video_bitrate(spec: EncodeSpec, info: MediaInfo) -> int | None:
    """目标体积 -> 视频码率(kbps)。时长未知时返回 None。"""
    dur = effective_duration(spec, info)
    if dur <= 0:
        return None
    total_kbps = spec.target_size_mb * 8192.0 / dur
    video_kbps = total_kbps - _audio_kbps(spec, info)
    return max(int(video_kbps), 50)


def _resolve_encoder(spec: EncodeSpec, target_encoders: tuple[str, ...], caps: Capabilities,
                     notes: list[str]) -> str:
    """校验编码器可用性，不可用时降级。"""
    enc = spec.video_encoder or (target_encoders[0] if target_encoders else "libx264")
    if caps.has(enc):
        return enc
    profile = encoder_profile(enc)
    family = profile.family if profile else "h264"
    for candidate in _SOFTWARE_BY_FAMILY.get(family, ()) + ("libx264",):
        if caps.has(candidate):
            notes.append(f"编码器 {enc} 在当前 ffmpeg 中不可用，已改用 {candidate}")
            return candidate
    notes.append(f"编码器 {enc} 不可用且未找到替代，将回退到 libx264")
    return "libx264"


def _resolve_audio(spec: EncodeSpec, target, caps: Capabilities, notes: list[str]) -> str:
    enc = spec.audio_encoder or getattr(target, "default_audio", "") or "aac"
    if caps.has(enc):
        return enc
    fallback = "aac" if caps.has("aac") else "libmp3lame"
    notes.append(f"音频编码器 {enc} 不可用，已改用 {fallback}")
    return fallback


# --------------------------------------------------------------------------
# 滤镜
# --------------------------------------------------------------------------

def _video_filter(spec: EncodeSpec) -> str:
    parts: list[str] = []
    if spec.scale_width:
        parts.append(f"scale={spec.scale_width}:-2:flags=lanczos")
    if spec.fps:
        parts.append(f"fps={spec.fps}")
    if spec.extra_filters.strip():
        parts.append(spec.extra_filters.strip())
    return ",".join(parts)


def _gif_filter(spec: EncodeSpec) -> str:
    w = spec.anim_width or 480
    dither = spec.anim_dither or "bayer"
    paletteuse = f"paletteuse=dither={dither}"
    if dither == "bayer":
        paletteuse += ":bayer_scale=3"
    paletteuse += ":diff_mode=rectangle"
    return (
        f"fps={spec.anim_fps},scale={w}:-1:flags=lanczos,split[s0][s1];"
        f"[s0]palettegen=max_colors={spec.anim_max_colors}:stats_mode=diff[p];"
        f"[s1][p]{paletteuse}"
    )


def _anim_filter(spec: EncodeSpec) -> str:
    w = spec.anim_width or 480
    return f"fps={spec.anim_fps},scale={w}:-1:flags=lanczos"


# --------------------------------------------------------------------------
# 参数片段
# --------------------------------------------------------------------------

def _quality_args(spec: EncodeSpec, encoder: str, info: MediaInfo, target, notes: list[str]) -> list[str]:
    profile = encoder_profile(encoder)
    if profile is None:
        profile = encoder_profile("libx264")

    if spec.quality_mode == QualityMode.CRF:
        crf = max(profile.crf_range[0], min(spec.crf, profile.crf_range[1]))
        return profile.quality_argv(crf)

    if spec.quality_mode == QualityMode.BITRATE:
        return ["-b:v", f"{max(spec.video_bitrate, 50)}k"]

    # 目标体积
    bitrate = target_video_bitrate(spec, info)
    if bitrate is None:
        notes.append("无法确定时长，目标体积模式回退为 CRF")
        crf = max(profile.crf_range[0], min(spec.crf, profile.crf_range[1]))
        return profile.quality_argv(crf)
    return ["-b:v", f"{bitrate}k"]


def _preset_args(spec: EncodeSpec, encoder: str) -> list[str]:
    if spec.preset_value:
        profile = encoder_profile(encoder)
        if profile:
            for _label, value in profile.presets:
                if value == spec.preset_value:
                    return _preset_tokens(encoder, value)
            # 未匹配到则直接套用
            return _preset_tokens(encoder, spec.preset_value)
    profile = encoder_profile(encoder)
    return list(profile.default_preset_args) if profile else []


def _preset_tokens(encoder: str, value: str) -> list[str]:
    profile = encoder_profile(encoder)
    family = profile.family if profile else "h264"
    if family == "vp9" or encoder == "libaom-av1":
        return ["-deadline", "good", "-cpu-used", value]
    if encoder.endswith("_nvenc"):
        return ["-preset", value]
    if encoder.endswith("_qsv"):
        return ["-preset", value]
    if encoder.endswith("_amf"):
        return ["-quality", value]
    return ["-preset", value]


def _audio_args(spec: EncodeSpec, target, caps: Capabilities, notes: list[str]) -> list[str]:
    if spec.audio_mode == "none":
        return ["-an"]
    if spec.audio_mode == "copy":
        return ["-c:a", "copy"]
    enc = _resolve_audio(spec, target, caps, notes)
    args = ["-c:a", enc]
    if enc not in ("flac", "pcm_s16le"):
        args += ["-b:a", f"{max(spec.audio_bitrate, 32)}k"]
    return args


def _input_args(spec: EncodeSpec, src: str, pattern: bool = False) -> list[str]:
    args: list[str] = []
    if pattern and spec.fps:
        args += ["-framerate", str(spec.fps)]
    if pattern:
        args += ["-start_number", str(spec.start_number)]
    if spec.trim_start and spec.trim_start > 0:
        args += ["-ss", f"{spec.trim_start:.3f}"]
    args += ["-i", src]
    return args


def _trim_tail(spec: EncodeSpec, info: MediaInfo) -> list[str]:
    dur = effective_duration(spec, info)
    if spec.trim_end is not None and dur > 0:
        return ["-t", f"{dur:.3f}"]
    return []


# --------------------------------------------------------------------------
# 各模式构建
# --------------------------------------------------------------------------

def _build_video(spec, info, toolchain, caps, out, notes) -> Plan:
    target = VIDEO_TARGETS.get(spec.target) or VIDEO_TARGETS["mp4"]
    encoder = _resolve_encoder(spec, target.software_encoders, caps, notes)

    duration = effective_duration(spec, info)
    two_pass = (
        spec.quality_mode == QualityMode.TARGET_SIZE
        and encoder in _TWO_PASS_ENCODERS
        and duration > 0
    )
    passlog = temp_dir() / f"vt_pass_{uuid4().hex}"

    base = _head(toolchain)
    base += _input_args(spec, str(info.path))
    base += ["-map", "0:v:0"]
    if spec.audio_mode != "none":
        base += ["-map", "0:a?"]
    base += _trim_tail(spec, info)

    vf = _video_filter(spec)
    if vf:
        base += ["-vf", vf]

    base += ["-c:v", encoder]
    base += _quality_args(spec, encoder, info, target, notes)
    base += _preset_args(spec, encoder)
    # 保持广泛兼容：统一 8bit 4:2:0
    base += ["-pix_fmt", "yuv420p"]

    if encoder == "libx265" and target.container == "mp4":
        base += ["-tag:v", "hvc1"]

    base += _audio_args(spec, target, caps, notes)

    if target.faststart:
        base += ["-movflags", "+faststart"]
    if target.extra_args:
        base += list(target.extra_args)

    steps: list[Step] = []
    if two_pass:
        p1 = list(base) + ["-pass", "1", "-passlogfile", str(passlog), "-an", "-f", "null", "-"]
        p2 = list(base) + ["-pass", "2", "-passlogfile", str(passlog), "-f", target.container, str(out)]
        steps.append(Step(p1, 1, 2))
        steps.append(Step(p2, 2, 2))
        notes.append("目标体积模式：使用两遍编码，进度按两遍折算")
    else:
        if spec.quality_mode == QualityMode.TARGET_SIZE and encoder not in _TWO_PASS_ENCODERS:
            notes.append("该编码器不支持两遍编码，目标体积以单遍 VBR 近似")
        steps.append(Step(list(base) + ["-f", target.container, str(out)], 1, 1))

    return Plan(PlanKind.FFMPEG, Path(out), steps, duration, passlog if two_pass else None, notes=notes)


def _build_audio(spec, info, toolchain, caps, out, notes) -> Plan:
    target = AUDIO_TARGETS.get(spec.target) or AUDIO_TARGETS["mp3"]
    duration = effective_duration(spec, info)

    argv = _head(toolchain)
    if spec.trim_start and spec.trim_start > 0:
        argv += ["-ss", f"{spec.trim_start:.3f}"]
    argv += ["-i", str(info.path)]
    argv += _trim_tail(spec, info)
    argv += ["-vn", "-map", "0:a:0?"]
    argv += ["-c:a", target.encoder]
    if not target.lossless:
        argv += ["-b:a", f"{max(spec.audio_bitrate, 32)}k"]
    argv += ["-f", target.key if target.key != "m4a" else "ipod"]
    argv += [str(out)]

    return Plan(PlanKind.FFMPEG, Path(out), [Step(argv, 1, 1)], duration, notes=notes)


def _build_gif(spec, info, toolchain, caps, out, notes) -> Plan:
    duration = effective_duration(spec, info)
    argv = _head(toolchain)
    argv += _input_args(spec, str(info.path))
    argv += _trim_tail(spec, info)
    argv += ["-map", "0:v:0"]
    argv += ["-vf", _gif_filter(spec)]
    argv += ["-loop", str(spec.anim_loop)]
    argv += ["-an"]
    argv += ["-f", "gif", str(out)]
    notes.append("GIF 使用调色板两阶段（palettegen/paletteuse）以保证画质")
    return Plan(PlanKind.FFMPEG, Path(out), [Step(argv, 1, 1)], duration, notes=notes)


def _build_webp_anim(spec, info, toolchain, caps, out, notes) -> Plan:
    duration = effective_duration(spec, info)
    argv = _head(toolchain)
    argv += _input_args(spec, str(info.path))
    argv += _trim_tail(spec, info)
    argv += ["-map", "0:v:0"]
    argv += ["-vf", _anim_filter(spec)]
    argv += ["-c:v", "libwebp_anim" if caps.has("libwebp_anim") else "libwebp"]
    if not caps.has("libwebp_anim"):
        notes.append("当前 ffmpeg 无 libwebp_anim，动图 WebP 可能只输出首帧")
    argv += ["-loop", str(spec.anim_loop)]
    if spec.anim_lossless:
        argv += ["-lossless", "1"]
    else:
        argv += ["-lossless", "0", "-quality", str(spec.anim_quality)]
    argv += ["-an"]
    argv += ["-f", "webp", str(out)]
    return Plan(PlanKind.FFMPEG, Path(out), [Step(argv, 1, 1)], duration, notes=notes)


def _build_apng(spec, info, toolchain, caps, out, notes) -> Plan:
    duration = effective_duration(spec, info)
    argv = _head(toolchain)
    argv += _input_args(spec, str(info.path))
    argv += _trim_tail(spec, info)
    argv += ["-map", "0:v:0"]
    argv += ["-vf", _anim_filter(spec)]
    if not caps.has("apng"):
        notes.append("当前 ffmpeg 无 apng 编码器，将回退为静态 PNG")
    argv += ["-c:v", "apng", "-plays", str(spec.anim_loop)]
    argv += ["-an"]
    argv += ["-f", "apng", str(out)]
    return Plan(PlanKind.FFMPEG, Path(out), [Step(argv, 1, 1)], duration, notes=notes)


def _build_image_from_video(spec, info, toolchain, caps, out, notes) -> Plan:
    touch = IMAGE_TARGETS.get(spec.target) or IMAGE_TARGETS["jpg"]
    argv = _head(toolchain)
    if spec.image_time:
        argv += ["-ss", f"{spec.image_time:.3f}"]
    argv += ["-i", str(info.path)]
    argv += ["-map", "0:v:0", "-frames:v", "1"]
    if spec.image_scale_width:
        argv += ["-vf", f"scale={spec.image_scale_width}:-2:flags=lanczos"]
    if touch.key == "jpg":
        # jpg 用 2~31 的 qscale，越小越好
        q = max(2, min(31, round((100 - spec.image_quality) / 100 * 30) + 2))
        argv += ["-q:v", str(q)]
    # 单帧输出必须显式声明，否则 image2 封装器会因缺少 %0Nd 模式而告警
    argv += ["-update", "1", str(out)]
    return Plan(PlanKind.FFMPEG, Path(out), [Step(argv, 1, 1)], 0.0, notes=notes)


def _build_pillow_image(spec, info, out, notes) -> Plan:
    touch = IMAGE_TARGETS.get(spec.target) or IMAGE_TARGETS["jpg"]
    fmt = _PILLOW_FORMAT.get(touch.key, "PNG")
    pillow = {
        "format": fmt,
        "quality": spec.image_quality,
        "lossless": spec.anim_lossless,
        "scale": spec.image_scale_width,
        "loop": spec.anim_loop,
        "source": str(info.path),
    }
    notes.append("静态图片转换使用 Pillow，无需调用 ffmpeg")
    return Plan(PlanKind.PILLOW, Path(out), duration=0.0, pillow=pillow, notes=notes)


def _build_merge(spec, info, toolchain, caps, out, notes) -> Plan:
    """视频 + 外部音频：合并/替换音轨。

    默认视频直接复制（不重编码，最快且无损），只处理音频。
    两个输入都支持时间裁切，裁切区间同样生效。
    """
    target = VIDEO_TARGETS.get(spec.target) or VIDEO_TARGETS["mp4"]
    duration = effective_duration(spec, info)

    audio_path = Path(spec.merge_audio)
    if not audio_path.is_file():
        raise FileNotFoundError(f"找不到音频文件：{audio_path}")

    argv = _head(toolchain)
    argv += _input_args(spec, str(info.path))
    argv += ["-i", str(audio_path)]

    argv += ["-map", "0:v:0"]
    if spec.merge_audio_mode == "keep" and info.has_audio:
        argv += ["-map", "0:a:0?"]
    argv += ["-map", "1:a:0"]

    argv += _trim_tail(spec, info)

    if spec.merge_copy_video:
        argv += ["-c:v", "copy"]
        notes.append("视频直接复制，不重编码（要求目标容器支持原视频编码）")
    else:
        encoder = _resolve_encoder(spec, target.software_encoders, caps, notes)
        argv += ["-c:v", encoder]
        argv += _quality_args(spec, encoder, info, target, notes)
        argv += _preset_args(spec, encoder)
        argv += ["-pix_fmt", "yuv420p"]
        if encoder == "libx265" and target.container == "mp4":
            argv += ["-tag:v", "hvc1"]

    if spec.merge_copy_audio:
        argv += ["-c:a", "copy"]
        notes.append("音频直接复制，不重编码（要求目标容器支持原音频编码）")
    else:
        argv += _audio_args(spec.copy_with(audio_mode="encode"), target, caps, notes)

    if target.faststart:
        argv += ["-movflags", "+faststart"]
    if target.extra_args:
        argv += list(target.extra_args)

    argv += ["-f", target.container, str(out)]
    return Plan(PlanKind.FFMPEG, Path(out), [Step(argv, 1, 1)], duration, notes=notes)


def _build_image_seq(spec, info, toolchain, caps, out, notes) -> Plan:
    target = VIDEO_TARGETS.get(spec.target) or VIDEO_TARGETS["mp4"]
    encoder = _resolve_encoder(spec, target.software_encoders, caps, notes)
    argv = _head(toolchain)
    argv += ["-framerate", str(spec.fps or 25)]
    argv += ["-start_number", str(spec.start_number)]
    argv += ["-i", str(info.path)]
    vf = _video_filter(spec)
    if vf:
        argv += ["-vf", vf]
    argv += ["-c:v", encoder]
    argv += _quality_args(spec, encoder, info, target, notes)
    argv += _preset_args(spec, encoder)
    argv += ["-pix_fmt", "yuv420p"]
    if encoder == "libx265" and target.container == "mp4":
        argv += ["-tag:v", "hvc1"]
    argv += ["-an"]
    if spec.frame_count > 0:
        # 只取指定帧数：序列帧的"选择部分导出"
        argv += ["-frames:v", str(spec.frame_count)]
    if target.faststart:
        argv += ["-movflags", "+faststart"]
    argv += ["-f", target.container, str(out)]
    notes.append(f"图片序列从第 {spec.start_number} 帧开始按 %0Nd 编号读取，帧率取自「帧率」设置")
    if spec.frame_count > 0:
        notes.append(f"只导出 {spec.frame_count} 帧")
    return Plan(PlanKind.FFMPEG, Path(out), [Step(argv, 1, 1)], 0.0, notes=notes)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build(spec: EncodeSpec, info: MediaInfo, toolchain: Toolchain,
          caps: Capabilities, output: str | Path) -> Plan:
    """构建一次任务的执行计划。"""
    out = Path(output)
    notes: list[str] = []

    if spec.mode == Mode.MERGE:
        return _build_merge(spec, info, toolchain, caps, out, notes)

    if spec.mode == Mode.AUDIO:
        return _build_audio(spec, info, toolchain, caps, out, notes)

    if spec.mode == Mode.GIF:
        return _build_gif(spec, info, toolchain, caps, out, notes)

    if spec.mode == Mode.WEBP_ANIM:
        return _build_webp_anim(spec, info, toolchain, caps, out, notes)

    if spec.mode == Mode.APNG:
        return _build_apng(spec, info, toolchain, caps, out, notes)

    if spec.mode == Mode.IMAGE:
        touch = IMAGE_TARGETS.get(spec.target)
        # 统一用 MediaInfo.is_still_image 判断，不要在这里另写一套条件：
        # 图片文件的时长未必是 0（JPG 常被报成 0.04 秒），只看时长会把图片当成视频，
        # 白白多跑一次 ffmpeg 子进程。
        is_image_source = info.is_still_image
        if touch and touch.animated:
            # 用户选了动图目标：能拿到多帧就走 anim，否则退化为静态图片
            if not is_image_source:
                return build(spec.copy_with(mode=Mode.GIF if touch.key == "gif" else
                                            (Mode.APNG if touch.key == "apng" else Mode.WEBP_ANIM)),
                             info, toolchain, caps, out)
        if is_image_source:
            return _build_pillow_image(spec, info, out, notes)
        return _build_image_from_video(spec, info, toolchain, caps, out, notes)

    if spec.mode == Mode.IMAGE_SEQ:
        return _build_image_seq(spec, info, toolchain, caps, out, notes)

    return _build_video(spec, info, toolchain, caps, out, notes)