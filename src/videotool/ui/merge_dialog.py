"""音视频合并对话框。

把外部音频合进视频：默认视频直接复制（不重编码、无损、极快），
只处理音频，因此这个操作基本是秒完成。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..core.capabilities import Capabilities
from ..core.specs import EncodeSpec, Mode

# 直接复制视频时比较稳妥的容器
CONTAINERS: tuple[tuple[str, str], ...] = (
    ("MP4（最通用）", "mp4"),
    ("MKV（最灵活，几乎不挑编码）", "mkv"),
    ("MOV（苹果/剪辑）", "mov"),
    ("TS（流媒体）", "ts"),
    ("FLV（旧网页）", "flv"),
)

AUDIO_ENCODERS: tuple[tuple[str, str], ...] = (
    ("AAC（兼容性最好）", "aac"),
    ("MP3", "libmp3lame"),
    ("Opus（同码率音质最好）", "libopus"),
    ("FLAC（无损）", "flac"),
)


class MergeDialog(QDialog):
    """收集合并参数，点确定后由调用方取 spec()。"""

    def __init__(self, caps: Capabilities, video: str = "", audio: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self.caps = caps
        self.setWindowTitle("合并音视频")
        self.resize(560, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        form.addRow("视频文件", self._file_row("video_edit", video, "选择视频文件"))
        form.addRow("音频文件", self._file_row("audio_edit", audio, "选择音频文件"))

        self.container_combo = QComboBox()
        for label, key in CONTAINERS:
            self.container_combo.addItem(label, key)
        form.addRow("输出格式", self.container_combo)

        self.video_mode = QComboBox()
        self.video_mode.addItem("直接复制视频（推荐，快且无损）", "copy")
        self.video_mode.addItem("重新编码视频（可缩放/压体积）", "encode")
        self.video_mode.currentIndexChanged.connect(self._refresh)
        form.addRow("视频处理", self.video_mode)

        self.audio_mode = QComboBox()
        self.audio_mode.addItem("重新编码音频（兼容性最好）", "encode")
        self.audio_mode.addItem("直接复制音频（不重编码）", "copy")
        self.audio_mode.currentIndexChanged.connect(self._refresh)
        form.addRow("音频处理", self.audio_mode)

        self.audio_enc_combo = QComboBox()
        for label, key in AUDIO_ENCODERS:
            if caps.has(key):
                self.audio_enc_combo.addItem(label, key)
        if self.audio_enc_combo.count() == 0:
            self.audio_enc_combo.addItem("AAC", "aac")
        form.addRow("音频编码器", self.audio_enc_combo)

        self.audio_bitrate = QSpinBox()
        self.audio_bitrate.setRange(32, 512)
        self.audio_bitrate.setSingleStep(16)
        self.audio_bitrate.setValue(192)
        self.audio_bitrate.setSuffix(" kbps")
        form.addRow("音频码率", self.audio_bitrate)

        self.keep_original = QCheckBox("保留视频原有音轨（新音轨作为第二条）")
        form.addRow("", self.keep_original)

        layout.addLayout(form)

        hint = QLabel(
            "「直接复制」不改动原视频流，速度最快且无画质损失，"
            "但目标容器必须支持原视频编码（H.264/H.265 用 MP4/MKV/MOV 都没问题）。"
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch(1)

        buttons = QDialogButtonBox()
        ok = buttons.addButton("添加到任务", QDialogButtonBox.AcceptRole)
        ok.clicked.connect(self._accept)
        buttons.addButton("取消", QDialogButtonBox.RejectRole).clicked.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh()

    # ------------------------------------------------------------------ 内部

    def _file_row(self, attr: str, value: str, caption: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        edit = QLineEdit(value)
        edit.setPlaceholderText("未选择")
        btn = QPushButton("浏览")
        btn.setFixedWidth(56)
        btn.clicked.connect(lambda: self._pick(edit, caption))
        row.addWidget(edit, 1)
        row.addWidget(btn)
        setattr(self, attr, edit)
        return row

    def _pick(self, edit: QLineEdit, caption: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, caption, "", "所有文件 (*.*)")
        if path:
            edit.setText(path)

    def _refresh(self) -> None:
        encoding_audio = self.audio_mode.currentData() == "encode"
        self.audio_enc_combo.setEnabled(encoding_audio)
        self.audio_bitrate.setEnabled(encoding_audio)

    def _accept(self) -> None:
        video = self.video_edit.text().strip()
        audio = self.audio_edit.text().strip()
        if not video:
            QMessageBox.information(self, "还差一步", "请先选择视频文件。")
            return
        if not Path(video).is_file():
            QMessageBox.warning(self, "文件不存在", f"找不到视频文件：\n{video}")
            return
        if not audio:
            QMessageBox.information(self, "还差一步", "请先选择音频文件。")
            return
        if not Path(audio).is_file():
            QMessageBox.warning(self, "文件不存在", f"找不到音频文件：\n{audio}")
            return
        self.accept()

    # ------------------------------------------------------------------ 结果

    def video_path(self) -> str:
        return self.video_edit.text().strip()

    def spec(self) -> EncodeSpec:
        return EncodeSpec(
            mode=Mode.MERGE,
            target=self.container_combo.currentData() or "mp4",
            merge_audio=self.audio_edit.text().strip(),
            merge_audio_mode="keep" if self.keep_original.isChecked() else "replace",
            merge_copy_video=self.video_mode.currentData() == "copy",
            merge_copy_audio=self.audio_mode.currentData() == "copy",
            audio_encoder=self.audio_enc_combo.currentData() or "aac",
            audio_bitrate=self.audio_bitrate.value(),
        )