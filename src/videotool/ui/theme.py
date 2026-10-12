"""深色主题（VS Code 配色风格）。"""

from __future__ import annotations

COLORS = {
    "bg": "#1e1e1e",
    "panel": "#252526",
    "input": "#2d2d30",
    "input_hover": "#3a3a3d",
    "border": "#3f3f46",
    "border_light": "#4d4d52",
    "text": "#d4d4d4",
    "muted": "#888888",
    "accent": "#0e639c",
    "accent_hover": "#1177bb",
    "accent_pressed": "#0a4d7a",
    "success": "#4ec9b0",
    "warning": "#dcdcaa",
    "danger": "#f14c4c",
    "table_alt": "#232323",
    "selection": "#094771",
}

_QSS = """
* { outline: none; }

/* 注意：不要给 QWidget 设 background-color。
   一旦设置，QFormLayout 里的 QLabel、容器 QWidget 都会继承它，
   在分组框的浅色底上就会出现一块块异色背景。背景只设到具体控件上。 */
QWidget {
    color: %(text)s;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}

QMainWindow, QDialog, QScrollArea { background-color: %(bg)s; }

/* 标签一律透明，避免出现垫色块 */
QLabel { background: transparent; }

QToolTip {
    background-color: %(input)s;
    color: %(text)s;
    border: 1px solid %(border_light)s;
    padding: 4px 6px;
}

/* ---- 菜单 ---- */
QMenu {
    background-color: %(input)s;
    border: 1px solid %(border_light)s;
    padding: 4px;
}
QMenu::item {
    padding: 5px 22px 5px 14px;
    border-radius: 3px;
    background: transparent;
}
QMenu::item:selected { background-color: %(selection)s; color: #ffffff; }
QMenu::item:disabled { color: %(muted)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 4px 8px; }

/* ---- 分组框 ---- */
QGroupBox {
    background-color: %(panel)s;
    border: 1px solid %(border)s;
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 10px 8px 10px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
    color: %(muted)s;
    background-color: %(panel)s;
}

/* ---- 按钮 ---- */
QPushButton {
    background-color: %(input)s;
    color: %(text)s;
    border: 1px solid %(border_light)s;
    border-radius: 4px;
    padding: 5px 12px;
    min-height: 18px;
}
QPushButton:hover { background-color: %(input_hover)s; border-color: %(accent)s; }
QPushButton:pressed { background-color: %(bg)s; }
QPushButton:disabled { color: %(muted)s; border-color: %(border)s; background-color: %(panel)s; }

QPushButton#primary {
    background-color: %(accent)s;
    border: 1px solid %(accent)s;
    color: #ffffff;
    font-weight: bold;
    padding: 7px 16px;
}
QPushButton#primary:hover { background-color: %(accent_hover)s; border-color: %(accent_hover)s; }
QPushButton#primary:pressed { background-color: %(accent_pressed)s; }
QPushButton#primary:disabled { background-color: %(border)s; border-color: %(border)s; color: %(muted)s; }

QPushButton#danger { border-color: #6b2b2b; }
QPushButton#danger:hover { border-color: %(danger)s; }

/* 「说明」小按钮 */
QPushButton#help {
    padding: 4px 6px;
    color: %(muted)s;
    border-color: %(border)s;
}
QPushButton#help:hover { color: %(text)s; border-color: %(accent)s; background-color: %(input_hover)s; }

/* ---- 输入控件 ---- */
/* QSpinBox / QDoubleSpinBox 故意不写在这里：
   只要给它们套上 QSS 的边框/底色，Qt 就不再绘制上下箭头，只剩两个空方块。
   交给 Fusion 用调色板绘制，箭头才正常。 */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QTextBrowser {
    background-color: %(input)s;
    color: %(text)s;
    border: 1px solid %(border)s;
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: %(selection)s;
    selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus { border-color: %(accent)s; }
QLineEdit:disabled, QComboBox:disabled {
    color: %(muted)s;
    background-color: %(panel)s;
}

/* 下拉箭头与上下箭头交给 Fusion 样式自己绘制。
   踩过的坑：
   1) CSS 的"透明边框拼三角"是浏览器技巧，Qt 不支持，写了会渲染成实心方块；
   2) 一旦用 QSS 定制 ::drop-down / ::up-button 等子控件，Qt 就不再自动绘制箭头，
      必须自己用 image: 指定图片，否则箭头直接消失。
   所以这里完全不碰子控件，只保留控件本身的边框与底色。 */
QComboBox QAbstractItemView {
    background-color: %(input)s;
    border: 1px solid %(border_light)s;
    selection-background-color: %(selection)s;
    selection-color: #ffffff;
    padding: 2px;
}

/* ---- 勾选 ---- */
/* 同理不碰 ::indicator：QSS 一改方框，勾选标记就没了。
   交给 Fusion 用调色板绘制，选中态是 Highlight 蓝底 + 白色勾。 */
QCheckBox, QRadioButton { spacing: 6px; color: %(text)s; background: transparent; }

/* ---- 表格 ---- */
QTableView {
    background-color: %(bg)s;
    alternate-background-color: %(table_alt)s;
    gridline-color: %(border)s;
    border: 1px solid %(border)s;
    border-radius: 4px;
}
QTableView::item { padding: 3px 4px; }
QTableView::item:selected { background-color: %(selection)s; color: #ffffff; }
QHeaderView::section {
    background-color: %(panel)s;
    color: %(muted)s;
    border: none;
    border-right: 1px solid %(border)s;
    border-bottom: 1px solid %(border)s;
    padding: 6px 6px;
    font-weight: bold;
}
QHeaderView::section:hover { color: %(text)s; }
QTableCornerButton::section { background-color: %(panel)s; border: none; }

/* ---- 进度条 ---- */
QProgressBar {
    background-color: %(input)s;
    border: 1px solid %(border)s;
    border-radius: 3px;
    text-align: center;
    color: %(text)s;
    min-height: 14px;
}
QProgressBar::chunk { background-color: %(accent)s; border-radius: 2px; }
QProgressBar#done::chunk { background-color: #2e7d32; }
QProgressBar#failed::chunk { background-color: #8b2f2f; }

/* ---- 滚动条 ---- */
QScrollBar:vertical {
    background: %(bg)s; width: 12px; margin: 0;
}
QScrollBar::handle:vertical {
    background: #4a4a4f; min-height: 28px; border-radius: 6px; margin: 2px;
}
QScrollBar::handle:vertical:hover { background: #5f5f66; }
QScrollBar:horizontal { background: %(bg)s; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #4a4a4f; min-width: 28px; border-radius: 6px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background: #5f5f66; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---- 分隔条 / 标签页 ---- */
QSplitter::handle { background-color: %(border)s; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }

QLabel#muted { color: %(muted)s; }
QLabel#heading { color: %(text)s; font-weight: bold; font-size: 14px; }

QStatusBar { background-color: %(panel)s; color: %(muted)s; }
QStatusBar::item { border: none; }
"""


def stylesheet() -> str:
    return _QSS % COLORS


def apply_theme(app) -> None:
    """把深色主题应用到 QApplication。"""
    from PySide6.QtGui import QColor, QPalette

    # 固定用 Fusion：Windows 原生样式对 QSS 的箭头/子控件支持不稳定，
    # 会出现箭头变方块、深色配色被系统主题覆盖等问题。
    app.setStyle("Fusion")

    palette = QPalette()
    c = COLORS
    palette.setColor(QPalette.Window, QColor(c["bg"]))
    palette.setColor(QPalette.WindowText, QColor(c["text"]))
    palette.setColor(QPalette.Base, QColor(c["input"]))
    palette.setColor(QPalette.AlternateBase, QColor(c["table_alt"]))
    palette.setColor(QPalette.Text, QColor(c["text"]))
    palette.setColor(QPalette.Button, QColor(c["input"]))
    palette.setColor(QPalette.ButtonText, QColor(c["text"]))
    palette.setColor(QPalette.Highlight, QColor(c["selection"]))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipBase, QColor(c["input"]))
    palette.setColor(QPalette.ToolTipText, QColor(c["text"]))
    palette.setColor(QPalette.PlaceholderText, QColor(c["muted"]))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(c["muted"]))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["muted"]))
    # 交给 Fusion 自己绘制的控件（数字框的上下箭头、勾选框）用这几个颜色画边框与立体感，
    # 设成主题色才不会出现一块偏亮的灰边。
    palette.setColor(QPalette.Mid, QColor(c["border"]))
    palette.setColor(QPalette.Midlight, QColor(c["input_hover"]))
    palette.setColor(QPalette.Dark, QColor(c["border"]))
    palette.setColor(QPalette.Shadow, QColor(c["bg"]))
    palette.setColor(QPalette.Light, QColor(c["input_hover"]))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet())