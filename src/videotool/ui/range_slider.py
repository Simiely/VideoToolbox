"""时间轴控件：一条可以拖出「选中区间」的进度条。

Qt 没有内置的范围滑块，这里自绘：
- 底轨 + 已选中区间高亮
- 两个可拖拽把手（起/止）
- 播放头当前位置
- 点击空白处 = 移动播放头
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPolygon
from PySide6.QtWidgets import QWidget

from .theme import COLORS

HANDLE_W = 9
BAR_H = 10
GRAB_PX = 10


class RangeSlider(QWidget):
    """以毫秒为单位的区间选择条。"""

    positionChanged = Signal(int)
    rangeChanged = Signal(int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._min = 0
        self._max = 1
        self._in = 0
        self._out = 1
        self._pos = 0
        self._drag: str | None = None
        self.setMinimumHeight(40)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    # ------------------------------------------------------------------ 取值

    def setRange(self, minimum: int, maximum: int) -> None:
        self._min = int(minimum)
        self._max = max(int(maximum), self._min + 1)
        self._in = self._min
        self._out = self._max
        self._pos = self._min
        self.update()

    def setPosition(self, value: int) -> None:
        value = max(self._min, min(int(value), self._max))
        if value != self._pos:
            self._pos = value
            self.update()
        self.positionChanged.emit(value)

    def position(self) -> int:
        return self._pos

    def setSelection(self, start: int, end: int) -> None:
        start = max(self._min, min(int(start), self._max))
        end = max(start, min(int(end), self._max))
        changed = (start != self._in) or (end != self._out)
        self._in, self._out = start, end
        self.update()
        if changed:
            self.rangeChanged.emit(start, end)

    def selection(self) -> tuple[int, int]:
        return self._in, self._out

    # ------------------------------------------------------------------ 绘制

    def _x_of(self, value: int) -> int:
        span = max(1, self._max - self._min)
        usable = max(1, self.width() - HANDLE_W * 2)
        return HANDLE_W + int(round((value - self._min) / span * usable))

    def _value_of(self, x: int) -> int:
        span = self._max - self._min
        usable = max(1, self.width() - HANDLE_W * 2)
        ratio = (x - HANDLE_W) / usable
        return max(self._min, min(int(round(self._min + ratio * span)), self._max))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        top = (self.height() - BAR_H) // 2
        track = QRect(HANDLE_W, top, max(1, self.width() - HANDLE_W * 2), BAR_H)

        # 底轨
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(COLORS["input"]))
        painter.drawRoundedRect(track, 4, 4)

        # 选中区间
        x1, x2 = self._x_of(self._in), self._x_of(self._out)
        band = QRect(min(x1, x2), top, max(2, abs(x2 - x1)), BAR_H)
        painter.setBrush(QColor(COLORS["accent"]))
        painter.drawRoundedRect(band, 4, 4)

        # 播放头
        px = self._x_of(self._pos)
        painter.setBrush(QColor("#ffffff"))
        painter.drawRect(px - 1, top - 7, 2, BAR_H + 14)

        # 两个把手
        for value, up in ((self._in, True), (self._out, False)):
            hx = self._x_of(value)
            painter.setBrush(QColor(COLORS["accent_hover"]))
            painter.setPen(QColor("#ffffff"))
            if up:
                tri = QPolygon([
                    QPoint(hx, top + BAR_H + 2),
                    QPoint(hx - HANDLE_W, top + BAR_H + 12),
                    QPoint(hx + HANDLE_W, top + BAR_H + 12),
                ])
            else:
                tri = QPolygon([
                    QPoint(hx, top - 2),
                    QPoint(hx - HANDLE_W, top - 12),
                    QPoint(hx + HANDLE_W, top - 12),
                ])
            painter.drawPolygon(tri)
        painter.end()

    # ------------------------------------------------------------------ 交互

    def _hit(self, x: int) -> str:
        if abs(x - self._x_of(self._in)) <= GRAB_PX:
            return "in"
        if abs(x - self._x_of(self._out)) <= GRAB_PX:
            return "out"
        return "pos"

    def mousePressEvent(self, event) -> None:
        x = int(event.position().x())
        self._drag = self._hit(x)
        self._apply(x)

    def mouseMoveEvent(self, event) -> None:
        x = int(event.position().x())
        if self._drag is None:
            self.setCursor(Qt.SizeHorCursor if self._hit(x) != "pos" else Qt.PointingHandCursor)
            return
        self._apply(x)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None

    def _apply(self, x: int) -> None:
        value = self._value_of(x)
        if self._drag == "in":
            self.setSelection(min(value, self._out - 1 if self._out - 1 > self._min else self._min),
                              self._out)
            self.setPosition(value)
        elif self._drag == "out":
            self.setSelection(self._in,
                              max(value, self._in + 1 if self._in + 1 < self._max else self._max))
            self.setPosition(value)
        else:
            self.setPosition(value)

    def sizeHint(self) -> QSize:
        return QSize(560, 44)