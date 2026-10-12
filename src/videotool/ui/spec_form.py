"""参数面板与 EncodeSpec 的映射。

主窗口只负责摆放控件与订阅信号，「读控件、写字段」的细节集中在这里：
改面板字段时不用翻主窗口，也能单独核对这些映射。
"""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox

from ..core.specs import EncodeSpec, Mode, QualityMode

# 需要「时间裁切」面板的模式：图片是单帧、序列按帧算，都不走时间裁切
TRIM_MODES = {Mode.VIDEO, Mode.AUDIO, Mode.GIF, Mode.WEBP_ANIM, Mode.APNG}
ANIM_MODES = {Mode.GIF, Mode.WEBP_ANIM, Mode.APNG}


def parse_time(text: str) -> tuple[float | None, bool]:
    """解析时间输入，返回 (秒, 是否可识别)。

    支持 "90"（秒）、"1:23"、"1:23.5"（分:秒）、"0:01:23"（时:分:秒）。
    空字符串表示不设限，返回 (None, True)。
    """
    text = (text or "").strip()
    if not text:
        return None, True
    try:
        parts = text.split(":")
        if len(parts) == 1:
            value = float(parts[0])
        elif len(parts) == 2:
            value = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            value = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        else:
            return None, False
    except ValueError:
        return None, False
    if value < 0:
        return None, False
    return value, True


def apply_video(spec: EncodeSpec, *, encoder_combo: QComboBox, quality_combo: QComboBox,
                quality_value: QDoubleSpinBox, preset_combo: QComboBox,
                resolution_combo: QComboBox, fps_combo: QComboBox) -> None:
    """视频 / 图片序列的编码参数。"""
    spec.video_encoder = encoder_combo.currentData() or "libx264"
    spec.quality_mode = quality_combo.currentData() or QualityMode.CRF
    value = quality_value.value()
    if spec.quality_mode == QualityMode.CRF:
        spec.crf = int(value)
    elif spec.quality_mode == QualityMode.BITRATE:
        spec.video_bitrate = int(value)
    else:
        spec.target_size_mb = float(value)
    spec.preset_value = preset_combo.currentData() or ""
    spec.scale_width = resolution_combo.currentData()
    spec.fps = fps_combo.currentData()


def apply_trim(spec: EncodeSpec, *, start_edit: QLineEdit, end_edit: QLineEdit) -> None:
    """时间裁切：只导出选中的一段。"""
    spec.trim_start, _ = parse_time(start_edit.text())
    spec.trim_end, _ = parse_time(end_edit.text())


def apply_sequence(spec: EncodeSpec, *, start_spin: QSpinBox, count_spin: QSpinBox,
                   fps_combo: QComboBox) -> None:
    """图片序列的帧范围；序列没有音轨，帧率给个稳妥默认值。"""
    spec.start_number = max(1, start_spin.value())
    spec.frame_count = count_spin.value()
    spec.fps = float(fps_combo.currentData() or 25)
    spec.audio_mode = "none"


def apply_audio(spec: EncodeSpec, *, mode_combo: QComboBox, encoder_combo: QComboBox,
                bitrate_spin: QSpinBox) -> None:
    """视频模式的音轨设置。"""
    spec.audio_mode = mode_combo.currentData() or "encode"
    spec.audio_encoder = encoder_combo.currentData() or ""
    spec.audio_bitrate = bitrate_spin.value()


def apply_anim(spec: EncodeSpec, *, fps_spin: QSpinBox, width_spin: QSpinBox,
               loop_combo: QComboBox, quality_spin: QSpinBox, lossless_check: QCheckBox,
               colors_spin: QSpinBox, dither_combo: QComboBox) -> None:
    """动图（GIF / WebP / APNG）参数。"""
    spec.anim_fps = float(fps_spin.value())
    spec.anim_width = width_spin.value() or None
    spec.anim_loop = loop_combo.currentData() or 0
    spec.anim_quality = quality_spin.value()
    spec.anim_lossless = lossless_check.isChecked()
    spec.anim_max_colors = colors_spin.value()
    spec.anim_dither = dither_combo.currentData() or "bayer"


def apply_image(spec: EncodeSpec, *, quality_spin: QSpinBox, width_spin: QSpinBox,
                time_spin: QDoubleSpinBox) -> None:
    """静态图片参数。"""
    spec.image_quality = quality_spin.value()
    spec.image_scale_width = width_spin.value() or None
    time_value = time_spin.value()
    spec.image_time = time_value if time_value > 0 else None