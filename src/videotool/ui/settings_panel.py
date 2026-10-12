"""右侧参数面板：输出格式 / 视频编码 / 时间裁切 / 序列帧范围 / 音频 / 动图 / 图片 / 输出。

面板自己管控件的创建与联动（哪种模式显示哪组、编码器列表重建、质量控制切换、
输出开关……），只把「当前设置」暴露给主窗口：
  - current_spec()：把面板读成 EncodeSpec
  - output_prefs()：输出目录 / 命名模板
  - settingsChanged / outputChanged 信号：主窗口据此同步等待中的任务与按钮

不触碰任务列表：裁切提示里的「源时长」由主窗口用 set_selected_duration() 注入。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core.capabilities import HARDWARE_ENCODERS, Capabilities
from ..core.naming import DEFAULT_TEMPLATE, TEMPLATE_TOKENS
from ..core.specs import (
    EncodeSpec,
    FPS_PRESETS,
    Mode,
    QualityMode,
    RESOLUTION_PRESETS,
    VIDEO_TARGETS,
    encoder_profile,
)
from .help_text import encoder_help_html
from .spec_form import (
    ANIM_MODES,
    TRIM_MODES,
    apply_anim,
    apply_audio,
    apply_image,
    apply_sequence,
    apply_trim,
    apply_video,
    parse_time,
)
from .theme import COLORS

# (显示名, 模式, 目标 key)
TARGET_CHOICES: tuple[tuple[str, Mode, str], ...] = (
    ("视频 · MP4（最通用）", Mode.VIDEO, "mp4"),
    ("视频 · MKV（封装最灵活）", Mode.VIDEO, "mkv"),
    ("视频 · MOV（苹果/剪辑）", Mode.VIDEO, "mov"),
    ("视频 · WebM（网页）", Mode.VIDEO, "webm"),
    ("视频 · AVI（老设备）", Mode.VIDEO, "avi"),
    ("视频 · FLV（直播/旧网页）", Mode.VIDEO, "flv"),
    ("视频 · TS（流媒体）", Mode.VIDEO, "ts"),
    ("音频 · MP3", Mode.AUDIO, "mp3"),
    ("音频 · M4A / AAC", Mode.AUDIO, "m4a"),
    ("音频 · Opus", Mode.AUDIO, "opus"),
    ("音频 · FLAC（无损）", Mode.AUDIO, "flac"),
    ("音频 · WAV（未压缩）", Mode.AUDIO, "wav"),
    ("音频 · OGG Vorbis", Mode.AUDIO, "ogg"),
    ("图片 · JPG", Mode.IMAGE, "jpg"),
    ("图片 · PNG", Mode.IMAGE, "png"),
    ("图片 · WebP（静态）", Mode.IMAGE, "webp"),
    ("图片 · BMP", Mode.IMAGE, "bmp"),
    ("图片 · TIFF", Mode.IMAGE, "tiff"),
    ("动图 · GIF", Mode.GIF, "gif"),
    ("动图 · WebP 动图", Mode.WEBP_ANIM, "webp_anim"),
    ("动图 · APNG", Mode.APNG, "apng"),
    ("序列 · 图片序列 → MP4", Mode.IMAGE_SEQ, "mp4"),
)


class SettingsPanel(QScrollArea):
    """右侧参数面板。"""

    settingsChanged = Signal()   # 任何影响 EncodeSpec 的改动
    outputChanged = Signal()     # 输出目录 / 命名模板的改动

    def __init__(self, caps: Capabilities, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.caps = caps
        self._selected_duration = ""   # 「源时长」提示文案，由主窗口注入

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(330)
        self.setMaximumWidth(420)

        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(4, 4, 8, 4)
        layout.setSpacing(8)

        layout.addWidget(self._build_target_group())
        layout.addWidget(self._build_video_group())
        layout.addWidget(self._build_trim_group())
        layout.addWidget(self._build_seq_group())
        layout.addWidget(self._build_audio_group())
        layout.addWidget(self._build_anim_group())
        layout.addWidget(self._build_image_group())
        layout.addWidget(self._build_output_group())
        layout.addStretch(1)
        self.setWidget(host)

        self.refresh_for_target()

    # ------------------------------------------------------------ 对外接口

    def set_caps(self, caps: Capabilities) -> None:
        """能力探测完成后更新（重建编码器与音轨列表）。"""
        self.caps = caps
        self.refresh_for_target()

    def set_selected_duration(self, text: str) -> None:
        """主窗口注入当前选中任务的源时长提示（空串表示没有选中任务）。"""
        self._selected_duration = text
        self._refresh_trim_hint()

    def current_spec(self) -> EncodeSpec:
        """把面板上的设置读成一个 EncodeSpec。"""
        mode, target = self._current_choice()
        spec = EncodeSpec(mode=mode, target=target)

        if mode in (Mode.VIDEO, Mode.IMAGE_SEQ):
            apply_video(
                spec,
                encoder_combo=self.encoder_combo,
                quality_combo=self.quality_combo,
                quality_value=self.quality_value,
                preset_combo=self.preset_combo,
                resolution_combo=self.resolution_combo,
                fps_combo=self.fps_combo,
            )

        # 时间裁切（视频/音频/动图）
        if mode in TRIM_MODES:
            apply_trim(spec, start_edit=self.trim_start_edit, end_edit=self.trim_end_edit)

        if mode == Mode.IMAGE_SEQ:
            apply_sequence(spec, start_spin=self.seq_start_spin,
                           count_spin=self.seq_count_spin, fps_combo=self.fps_combo)
        elif mode == Mode.VIDEO:
            apply_audio(spec, mode_combo=self.audio_mode_combo,
                        encoder_combo=self.audio_enc_combo, bitrate_spin=self.audio_bitrate)
        elif mode == Mode.AUDIO:
            spec.audio_bitrate = self.audio_bitrate.value()

        if mode in ANIM_MODES:
            apply_anim(
                spec,
                fps_spin=self.anim_fps,
                width_spin=self.anim_width,
                loop_combo=self.anim_loop_combo,
                quality_spin=self.anim_quality,
                lossless_check=self.anim_lossless,
                colors_spin=self.anim_colors,
                dither_combo=self.anim_dither_combo,
            )

        if mode == Mode.IMAGE:
            apply_image(spec, quality_spin=self.image_quality,
                        width_spin=self.image_width, time_spin=self.image_time)

        return spec

    def output_prefs(self) -> tuple[bool, str, str]:
        """(输出到源目录, 输出目录, 命名模板)。"""
        return (self.same_dir_check.isChecked(),
                self.out_dir_edit.text().strip(),
                self.template_edit.text().strip() or DEFAULT_TEMPLATE)

    def help_dialog(self) -> QDialog:
        """「编码器怎么选」说明对话框（只构建，不 exec）。"""
        dialog = QDialog(self)
        dialog.setWindowTitle("编码器怎么选")
        dialog.resize(660, 690)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(encoder_help_html(self._hardware_summary()))
        layout.addWidget(browser, 1)

        buttons = QDialogButtonBox()
        close_btn = buttons.addButton("知道了", QDialogButtonBox.AcceptRole)
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        return dialog

    # ------------------------------------------------------------ 分组构建

    def _build_target_group(self) -> QGroupBox:
        group = QGroupBox("输出格式")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.target_combo = QComboBox()
        for label, mode, key in TARGET_CHOICES:
            self.target_combo.addItem(label, (mode, key))
        self.target_combo.currentIndexChanged.connect(self.refresh_for_target)
        form.addRow("转换为", self.target_combo)

        self.detail_label = QLabel()
        self.detail_label.setObjectName("muted")
        self.detail_label.setWordWrap(True)
        form.addRow("说明", self.detail_label)
        return group

    def _build_video_group(self) -> QGroupBox:
        self.video_group = QGroupBox("视频编码")
        form = QFormLayout(self.video_group)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.encoder_combo = QComboBox()
        # 编码器名很长（如「H.264 / x264（软编，兼容性最好）」），若按最长项撑宽度，
        # 会把同一行的「说明」按钮挤出面板可视区（需要横向滚动才看得到）。
        # 按 10 个字符估宽，默认宽度下按钮就能完整显示。
        self.encoder_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.encoder_combo.setMinimumContentsLength(10)
        self.encoder_combo.currentIndexChanged.connect(self._on_encoder_changed)

        encoder_row = QWidget()
        encoder_layout = QHBoxLayout(encoder_row)
        encoder_layout.setContentsMargins(0, 0, 0, 0)
        encoder_layout.setSpacing(4)
        encoder_layout.addWidget(self.encoder_combo, 1)
        self.encoder_help_btn = QPushButton("说明")
        self.encoder_help_btn.setObjectName("help")
        self.encoder_help_btn.setFixedWidth(48)
        self.encoder_help_btn.setToolTip("不知道该选哪个编码器？点这里看选择建议")
        self.encoder_help_btn.clicked.connect(self._show_encoder_help)
        encoder_layout.addWidget(self.encoder_help_btn)
        form.addRow("编码器", encoder_row)

        self.quality_combo = QComboBox()
        self.quality_combo.addItem("恒定质量（推荐）", QualityMode.CRF)
        self.quality_combo.addItem("恒定码率", QualityMode.BITRATE)
        self.quality_combo.addItem("指定目标体积（两遍）", QualityMode.TARGET_SIZE)
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        form.addRow("质量控制", self.quality_combo)

        self.quality_value = QDoubleSpinBox()
        self.quality_value.setDecimals(0)
        self.quality_value.setRange(0, 63)
        self.quality_value.setValue(23)
        self.quality_label = QLabel("CRF 值")
        form.addRow(self.quality_label, self.quality_value)

        self.preset_combo = QComboBox()
        form.addRow("速度预设", self.preset_combo)

        self.resolution_combo = QComboBox()
        for label, width in RESOLUTION_PRESETS:
            self.resolution_combo.addItem(label, width)
        form.addRow("分辨率", self.resolution_combo)

        self.fps_combo = QComboBox()
        for label, value in FPS_PRESETS:
            self.fps_combo.addItem(label, value)
        form.addRow("帧率", self.fps_combo)

        self.quality_value.valueChanged.connect(self._emit_settings_changed)
        self.preset_combo.currentIndexChanged.connect(self._emit_settings_changed)
        self.resolution_combo.currentIndexChanged.connect(self._emit_settings_changed)
        self.fps_combo.currentIndexChanged.connect(self._emit_settings_changed)
        return self.video_group

    def _build_trim_group(self) -> QGroupBox:
        """时间裁切：只导出选中时间区间。"""
        self.trim_group = QGroupBox("时间裁切（只导出选中的一段）")
        form = QFormLayout(self.trim_group)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.trim_start_edit = QLineEdit()
        self.trim_start_edit.setPlaceholderText("留空 = 从开头")
        self.trim_start_edit.textChanged.connect(self._on_trim_changed)
        form.addRow("起始", self.trim_start_edit)

        self.trim_end_edit = QLineEdit()
        self.trim_end_edit.setPlaceholderText("留空 = 到结尾")
        self.trim_end_edit.textChanged.connect(self._on_trim_changed)
        form.addRow("结束", self.trim_end_edit)

        self.trim_hint = QLabel()
        self.trim_hint.setObjectName("muted")
        self.trim_hint.setWordWrap(True)
        form.addRow("", self.trim_hint)

        quick = QWidget()
        quick_layout = QHBoxLayout(quick)
        quick_layout.setContentsMargins(0, 0, 0, 0)
        quick_layout.setSpacing(4)
        reset_btn = QPushButton("清空裁切")
        reset_btn.clicked.connect(self._clear_trim)
        quick_layout.addWidget(reset_btn)
        quick_layout.addStretch(1)
        form.addRow("", quick)

        self._refresh_trim_hint()
        return self.trim_group

    def _build_seq_group(self) -> QGroupBox:
        """图片序列的帧区间（序列没有时长，只能按帧选）。"""
        self.seq_group = QGroupBox("序列帧范围（只导出选中的帧）")
        form = QFormLayout(self.seq_group)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.seq_start_spin = QSpinBox()
        self.seq_start_spin.setRange(0, 10_000_000)
        self.seq_start_spin.setValue(1)
        self.seq_start_spin.valueChanged.connect(self._emit_settings_changed)
        form.addRow("起始帧", self.seq_start_spin)

        self.seq_count_spin = QSpinBox()
        self.seq_count_spin.setRange(0, 10_000_000)
        self.seq_count_spin.setValue(0)
        self.seq_count_spin.setSpecialValueText("全部")
        self.seq_count_spin.valueChanged.connect(self._emit_settings_changed)
        form.addRow("帧数", self.seq_count_spin)

        hint = QLabel("按文件名中的连续编号读取，起始帧对应编号最小的那张图")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow("", hint)
        return self.seq_group

    def _build_audio_group(self) -> QGroupBox:
        self.audio_group = QGroupBox("音频")
        form = QFormLayout(self.audio_group)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.audio_mode_combo = QComboBox()
        self.audio_mode_combo.addItem("重新编码", "encode")
        self.audio_mode_combo.addItem("直接复制原音轨", "copy")
        self.audio_mode_combo.addItem("移除音频", "none")
        self.audio_mode_combo.currentIndexChanged.connect(self._on_audio_mode_changed)
        form.addRow("处理方式", self.audio_mode_combo)

        self.audio_enc_combo = QComboBox()
        self.audio_enc_combo.currentIndexChanged.connect(self._emit_settings_changed)
        form.addRow("编码器", self.audio_enc_combo)

        self.audio_bitrate = QSpinBox()
        self.audio_bitrate.setRange(32, 512)
        self.audio_bitrate.setSingleStep(16)
        self.audio_bitrate.setValue(192)
        self.audio_bitrate.setSuffix(" kbps")
        self.audio_bitrate.valueChanged.connect(self._emit_settings_changed)
        form.addRow("码率", self.audio_bitrate)
        return self.audio_group

    def _build_anim_group(self) -> QGroupBox:
        self.anim_group = QGroupBox("动图参数")
        form = QFormLayout(self.anim_group)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.anim_fps = QSpinBox()
        self.anim_fps.setRange(1, 60)
        self.anim_fps.setValue(15)
        self.anim_fps.setSuffix(" fps")
        self.anim_fps.valueChanged.connect(self._emit_settings_changed)
        form.addRow("帧率", self.anim_fps)

        self.anim_width = QSpinBox()
        self.anim_width.setRange(0, 4096)
        self.anim_width.setSingleStep(20)
        self.anim_width.setValue(480)
        self.anim_width.setSuffix(" px")
        self.anim_width.setSpecialValueText("原始宽度")
        self.anim_width.valueChanged.connect(self._emit_settings_changed)
        form.addRow("宽度", self.anim_width)

        self.anim_loop_combo = QComboBox()
        self.anim_loop_combo.addItem("无限循环", 0)
        self.anim_loop_combo.addItem("播放 1 次", 1)
        self.anim_loop_combo.addItem("播放 3 次", 3)
        self.anim_loop_combo.addItem("播放 5 次", 5)
        self.anim_loop_combo.currentIndexChanged.connect(self._emit_settings_changed)
        form.addRow("循环", self.anim_loop_combo)

        self.anim_quality = QSpinBox()
        self.anim_quality.setRange(1, 100)
        self.anim_quality.setValue(75)
        self.anim_quality.valueChanged.connect(self._emit_settings_changed)
        self.anim_quality_label = QLabel("WebP 质量")
        form.addRow(self.anim_quality_label, self.anim_quality)

        self.anim_lossless = QCheckBox("无损（体积很大）")
        self.anim_lossless.stateChanged.connect(self._emit_settings_changed)
        form.addRow("", self.anim_lossless)

        self.anim_colors = QSpinBox()
        self.anim_colors.setRange(2, 256)
        self.anim_colors.setValue(256)
        self.anim_colors.valueChanged.connect(self._emit_settings_changed)
        self.anim_colors_label = QLabel("颜色数")
        form.addRow(self.anim_colors_label, self.anim_colors)

        self.anim_dither_combo = QComboBox()
        for label, value in (("Bayer（推荐）", "bayer"), ("Sierra2（更细腻）", "sierra2_4a"),
                             ("无抖动（色块）", "none")):
            self.anim_dither_combo.addItem(label, value)
        self.anim_dither_combo.currentIndexChanged.connect(self._emit_settings_changed)
        self.anim_dither_label = QLabel("抖动算法")
        form.addRow(self.anim_dither_label, self.anim_dither_combo)
        return self.anim_group

    def _build_image_group(self) -> QGroupBox:
        self.image_group = QGroupBox("图片参数")
        form = QFormLayout(self.image_group)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.image_quality = QSpinBox()
        self.image_quality.setRange(1, 100)
        self.image_quality.setValue(90)
        self.image_quality.valueChanged.connect(self._emit_settings_changed)
        form.addRow("质量", self.image_quality)

        self.image_width = QSpinBox()
        self.image_width.setRange(0, 8192)
        self.image_width.setSingleStep(50)
        self.image_width.setValue(0)
        self.image_width.setSuffix(" px")
        self.image_width.setSpecialValueText("原始尺寸")
        self.image_width.valueChanged.connect(self._emit_settings_changed)
        form.addRow("缩放宽度", self.image_width)

        self.image_time = QDoubleSpinBox()
        self.image_time.setRange(0.0, 36000.0)
        self.image_time.setDecimals(2)
        self.image_time.setSuffix(" 秒")
        self.image_time.valueChanged.connect(self._emit_settings_changed)
        hint = QLabel("从视频抽帧时的取帧时间点，0 表示第一帧")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow("取帧位置", self.image_time)
        form.addRow("", hint)
        return self.image_group

    def _build_output_group(self) -> QGroupBox:
        group = QGroupBox("输出")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.same_dir_check = QCheckBox("输出到源文件所在目录")
        self.same_dir_check.setChecked(True)
        self.same_dir_check.stateChanged.connect(self._on_output_changed)
        form.addRow("", self.same_dir_check)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        self.out_dir_edit = QLineEdit()
        self.out_dir_edit.setPlaceholderText("选择输出目录")
        self.out_dir_edit.setEnabled(False)
        self.out_dir_edit.textChanged.connect(self._on_output_changed)
        self.out_dir_btn = QPushButton("浏览")
        self.out_dir_btn.setEnabled(False)
        self.out_dir_btn.setFixedWidth(56)
        self.out_dir_btn.clicked.connect(self._choose_output_dir)
        row_layout.addWidget(self.out_dir_edit, 1)
        row_layout.addWidget(self.out_dir_btn)
        form.addRow("输出目录", row)

        self.template_edit = QLineEdit(DEFAULT_TEMPLATE)
        self.template_edit.textChanged.connect(self._on_output_changed)
        self.template_edit.setToolTip("\n".join(f"{tok}  {desc}" for tok, desc in TEMPLATE_TOKENS))
        form.addRow("命名模板", self.template_edit)
        return group

    # ------------------------------------------------------------ 面板联动

    def _current_choice(self) -> tuple[Mode, str]:
        return self.target_combo.currentData()

    def refresh_for_target(self) -> None:
        """目标格式变化：切换各分组的显隐、重建编码器列表、刷新提示。"""
        mode, target = self._current_choice()
        self.video_group.setVisible(mode in (Mode.VIDEO, Mode.IMAGE_SEQ))
        self.audio_group.setVisible(mode == Mode.VIDEO)
        self.anim_group.setVisible(mode in ANIM_MODES)
        self.image_group.setVisible(mode == Mode.IMAGE)
        self.trim_group.setVisible(mode in TRIM_MODES)
        self.seq_group.setVisible(mode == Mode.IMAGE_SEQ)

        is_gif = mode == Mode.GIF
        is_webp = mode == Mode.WEBP_ANIM
        for widget in (self.anim_colors, self.anim_colors_label,
                       self.anim_dither_combo, self.anim_dither_label):
            widget.setVisible(is_gif)
        for widget in (self.anim_quality, self.anim_quality_label):
            widget.setVisible(is_webp)
        self.anim_lossless.setVisible(is_webp)

        if mode in (Mode.VIDEO, Mode.IMAGE_SEQ):
            self._rebuild_encoders()
            self._on_encoder_changed()
        if mode == Mode.VIDEO:
            self._rebuild_audio()

        self.detail_label.setText(self._mode_hint(mode, target))
        self.settingsChanged.emit()

    def _mode_hint(self, mode: Mode, target: str) -> str:
        if mode == Mode.VIDEO:
            return "视频转码，可缩放分辨率、改帧率、压制体积。"
        if mode == Mode.AUDIO:
            return "只保留音频轨道并转码为所选格式。"
        if mode == Mode.IMAGE:
            return "静态图片转换；来源是视频时按「取帧位置」抽取一帧。"
        if mode == Mode.GIF:
            return "转 GIF 动图，使用调色板两阶段保证画质，色数越少体积越小。"
        if mode == Mode.WEBP_ANIM:
            return "转 WebP 动图，比 GIF 体积更小、色彩更丰富。"
        if mode == Mode.APNG:
            return "转 APNG 动图，保留透明通道，质量最好但体积最大。"
        if mode == Mode.IMAGE_SEQ:
            return "把同目录、同前缀且连续编号的图片合成视频，添加任一张即可。"
        return ""

    def _rebuild_encoders(self) -> None:
        mode, target = self._current_choice()
        previous = self.encoder_combo.currentData()
        self.encoder_combo.blockSignals(True)
        self.encoder_combo.clear()

        if mode == Mode.IMAGE_SEQ:
            families = ("h264", "hevc", "vp9", "av1")
            software = ("libx264", "libx265", "libvpx-vp9", "libsvtav1", "libaom-av1")
        else:
            vt = VIDEO_TARGETS.get(target)
            families = vt.video_families if vt else ("h264",)
            software = vt.software_encoders if vt else ("libx264",)

        added: set[str] = set()
        for enc in software:
            if not self.caps.has(enc) or enc in added:
                continue
            profile = encoder_profile(enc)
            self.encoder_combo.addItem(profile.label if profile else enc, enc)
            added.add(enc)

        for enc, label in self.caps.hardware_options():
            profile = encoder_profile(enc)
            if profile is None or profile.family not in families or enc in added:
                continue
            self.encoder_combo.addItem(f"{label}（硬件加速）", enc)
            added.add(enc)

        if self.encoder_combo.count() == 0:
            self.encoder_combo.addItem("H.264 / x264（软编）", "libx264")

        index = self.encoder_combo.findData(previous)
        self.encoder_combo.setCurrentIndex(index if index >= 0 else 0)
        self.encoder_combo.blockSignals(False)

    def _on_encoder_changed(self) -> None:
        encoder = self.encoder_combo.currentData()
        profile = encoder_profile(encoder) if encoder else None

        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        if profile and profile.presets:
            for label, value in profile.presets:
                self.preset_combo.addItem(label, value)
            recommended = 0
            for i in range(self.preset_combo.count()):
                if "推荐" in self.preset_combo.itemText(i):
                    recommended = i
                    break
            self.preset_combo.setCurrentIndex(recommended)
        else:
            self.preset_combo.addItem("默认", "")
        self.preset_combo.blockSignals(False)

        if profile:
            lo, hi = profile.crf_range
            self.quality_value.setRange(lo, hi)
            if self.quality_combo.currentData() == QualityMode.CRF:
                self.quality_value.setValue(profile.default_crf)
        self.settingsChanged.emit()

    # ------------------------------------------------------------ 编码器说明

    def _show_encoder_help(self) -> None:
        self.help_dialog().exec()

    def _hardware_summary(self) -> str:
        """本机可用硬件编码器的一行描述（帮助文案的输入）。"""
        groups: list[str] = []
        for vendor, mapping in HARDWARE_ENCODERS.items():
            formats = [label for enc, label in mapping.items() if enc in self.caps.hardware]
            if formats:
                groups.append(f"{vendor}（{'/'.join(formats)}）")
        return "、".join(groups) if groups else "无（本机没有检测到可用的硬件编码器）"

    def _rebuild_audio(self) -> None:
        mode, target = self._current_choice()
        vt = VIDEO_TARGETS.get(target)
        encoders = vt.audio_encoders if vt else ("aac",)
        previous = self.audio_enc_combo.currentData()

        self.audio_enc_combo.blockSignals(True)
        self.audio_enc_combo.clear()
        for enc in encoders:
            if self.caps.has(enc):
                self.audio_enc_combo.addItem(enc, enc)
        if self.audio_enc_combo.count() == 0:
            self.audio_enc_combo.addItem("aac", "aac")
        index = self.audio_enc_combo.findData(previous)
        self.audio_enc_combo.setCurrentIndex(index if index >= 0 else 0)
        self.audio_enc_combo.blockSignals(False)

    def _on_quality_changed(self) -> None:
        mode = self.quality_combo.currentData()
        if mode == QualityMode.CRF:
            self.quality_label.setText("CRF 值")
            self.quality_value.setDecimals(0)
            self.quality_value.setSingleStep(1)
            self.quality_value.setRange(0, 63)
            encoder = self.encoder_combo.currentData()
            profile = encoder_profile(encoder) if encoder else None
            self.quality_value.setValue(profile.default_crf if profile else 23)
            self.quality_value.setToolTip("数值越小画质越好、体积越大")
        elif mode == QualityMode.BITRATE:
            self.quality_label.setText("视频码率")
            self.quality_value.setDecimals(0)
            self.quality_value.setSingleStep(100)
            self.quality_value.setRange(50, 200000)
            self.quality_value.setValue(2500)
            self.quality_value.setToolTip("单位 kbps")
        else:
            self.quality_label.setText("目标体积")
            self.quality_value.setDecimals(1)
            self.quality_value.setSingleStep(1.0)
            self.quality_value.setRange(0.1, 20000.0)
            self.quality_value.setValue(50.0)
            self.quality_value.setToolTip("单位 MB，使用两遍编码逼近目标体积")
        self.settingsChanged.emit()

    def _on_audio_mode_changed(self) -> None:
        encoding = self.audio_mode_combo.currentData() == "encode"
        self.audio_enc_combo.setEnabled(encoding)
        self.audio_bitrate.setEnabled(encoding)
        self.settingsChanged.emit()

    def _choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if directory:
            self.out_dir_edit.setText(directory)

    def _on_output_changed(self) -> None:
        same_dir = self.same_dir_check.isChecked()
        self.out_dir_edit.setEnabled(not same_dir)
        self.out_dir_btn.setEnabled(not same_dir)
        self.outputChanged.emit()

    def _emit_settings_changed(self) -> None:
        self.settingsChanged.emit()

    # ------------------------------------------------------------ 裁切提示

    def _on_trim_changed(self) -> None:
        self._refresh_trim_hint()
        self.settingsChanged.emit()

    def _clear_trim(self) -> None:
        self.trim_start_edit.blockSignals(True)
        self.trim_end_edit.blockSignals(True)
        self.trim_start_edit.clear()
        self.trim_end_edit.clear()
        self.trim_start_edit.blockSignals(False)
        self.trim_end_edit.blockSignals(False)
        self._refresh_trim_hint()
        self.settingsChanged.emit()

    def _refresh_trim_hint(self) -> None:
        mode, _ = self._current_choice()
        if mode not in TRIM_MODES:
            self.trim_hint.setText("")
            return
        start, start_ok = parse_time(self.trim_start_edit.text())
        end, end_ok = parse_time(self.trim_end_edit.text())
        if not start_ok or not end_ok:
            self.trim_hint.setText("时间格式不对，请用「90」「1:23」「0:01:23.5」这类写法")
            self.trim_hint.setStyleSheet(f"color:{COLORS['danger']};")
            return
        self.trim_hint.setStyleSheet("")

        # 「源时长」文案由主窗口注入（面板不碰任务列表）
        duration_text = self._selected_duration
        if start is None and end is None:
            self.trim_hint.setText(f"留空表示导出整个文件。{duration_text}")
        else:
            span = ""
            if end is not None:
                span = f"（{max(0.0, end - (start or 0)):.1f} 秒）"
            self.trim_hint.setText(
                f"只导出 {start or 0:.1f} 秒 ~ "
                f"{'结尾' if end is None else f'{end:.1f} 秒'}{span}{duration_text}"
            )