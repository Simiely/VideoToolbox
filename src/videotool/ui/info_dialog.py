"""媒体参数对话框：类似小丸工具箱的 MediaInfo 面板。

展示 ffprobe 读到的全部关键参数，并支持一键复制为文本。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from ..core.probe import (
    MediaInfo,
    audio_rows,
    summary_rows,
    text_report,
    video_rows,
)
from .theme import COLORS


class MediaInfoDialog(QDialog):
    """只读的参数展示窗口。"""

    def __init__(self, info: MediaInfo, parent=None) -> None:
        super().__init__(parent)
        self.info = info
        self.setWindowTitle(f"媒体参数 — {info.path.name}")
        self.resize(760, 660)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        layout.addWidget(self._build_header())

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setHtml(self._build_html())
        layout.addWidget(self.browser, 1)

        buttons = QHBoxLayout()
        self.copy_btn = QPushButton("复制参数")
        self.copy_btn.setToolTip("把全部参数以文本形式复制到剪贴板")
        self.copy_btn.clicked.connect(self._copy)
        buttons.addWidget(self.copy_btn)

        self.copy_hint = QLabel()
        self.copy_hint.setObjectName("muted")
        buttons.addWidget(self.copy_hint)
        buttons.addStretch(1)

        box = QDialogButtonBox()
        close_btn = box.addButton("关闭", QDialogButtonBox.AcceptRole)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(box)

        layout.addLayout(buttons)

    # ------------------------------------------------------------------ 组成

    def _build_header(self) -> QLabel:
        c = COLORS
        label = QLabel()
        label.setTextFormat(Qt.RichText)
        label.setWordWrap(True)
        label.setText(
            f"<div style='font-size:15px; font-weight:bold; color:{c['text']};'>{self.info.path.name}</div>"
            f"<div style='color:{c['muted']}; font-size:12px;'>{self.info.path.parent}</div>"
        )
        return label

    def _table(self, rows: list[tuple[str, str]], label_width: int = 130) -> str:
        c = COLORS
        html: list[str] = [
            "<table width='100%' cellspacing='0' cellpadding='0' "
            "style='margin:2px 0 6px 0;'>"
        ]
        for i, (name, value) in enumerate(rows):
            shade = c["table_alt"] if i % 2 else "transparent"
            html.append(
                f"<tr style='background-color:{shade};'>"
                f"<td width='{label_width}' style='padding:3px 8px; color:{c['muted']};'>{name}</td>"
                f"<td style='padding:3px 8px; color:{c['text']};'>{_escape(value)}</td>"
                f"</tr>"
            )
        html.append("</table>")
        return "".join(html)

    def _section(self, title: str, extra: str = "") -> str:
        c = COLORS
        suffix = f"<span style='color:{c['muted']}; font-weight:normal;'>　{extra}</span>" if extra else ""
        return (
            f"<p style='margin:12px 0 4px 0; font-weight:bold; color:{c['accent']};'>"
            f"{title}{suffix}</p>"
        )

    def _build_html(self) -> str:
        info = self.info
        if not info.ok:
            c = COLORS
            return (
                f"<div style='color:{c['danger']}; padding:12px;'>"
                f"读取失败：{_escape(info.error)}</div>"
            )

        parts: list[str] = []
        parts.append(self._section("概要"))
        parts.append(self._table(summary_rows(info)))

        if info.video:
            v = info.video
            notes: list[str] = []
            if v.is_hdr:
                notes.append("HDR")
            if v.display_size != (v.width, v.height):
                notes.append("带旋转")
            parts.append(self._section("视频", "、".join(notes)))
            parts.append(self._table(video_rows(v)))
        else:
            parts.append(self._section("视频"))
            parts.append(f"<div style='color:{COLORS['muted']}; padding:4px 8px;'>无视频轨</div>")

        if info.audios:
            for i, audio in enumerate(info.audios, start=1):
                rows = audio_rows(audio, i)
                codec = rows[0][1]
                parts.append(self._section(f"音频 {i}", codec))
                parts.append(self._table(rows[1:]))
        else:
            parts.append(self._section("音频"))
            parts.append(f"<div style='color:{COLORS['muted']}; padding:4px 8px;'>无音频轨</div>")

        if info.format_tags:
            parts.append(self._section("容器标签"))
            parts.append(self._table(sorted(info.format_tags.items())))

        return f"<div style='color:{COLORS['text']};'>{''.join(parts)}</div>"

    # ------------------------------------------------------------------ 交互

    def _copy(self) -> None:
        QApplication.clipboard().setText(text_report(self.info))
        self.copy_hint.setText("已复制到剪贴板")


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )