"""任务列表的表格模型。

每个任务的显示文本、颜色、tooltip 都由模型的 data() 提供，视图只管显示；
「进度」列由视图外挂 QProgressBar（用 setIndexWidget），模型只提供行。
原来散在主窗口里的行号映射（_row_of）与区间文案也集中到这里。
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..core.probe import human_size
from ..core.queue import Task, TaskStatus
from ..core.specs import Mode, output_container_label, output_ext
from .trim_dialog import fmt_ms

COLUMN_TITLES = ("文件名", "媒体信息", "目标", "裁切区间", "状态", "进度", "结果")
PROGRESS_COLUMN = 5

COLOR_DONE = "#4ec9b0"
COLOR_FAILED = "#f14c4c"
COLOR_RUNNING = "#dcdcaa"
COLOR_IDLE = "#888888"
COLOR_RANGE = "#ce9178"


def range_label(task: Task) -> str:
    """「裁切区间」列的简短文字。

    裁切过的任务必须在这里看得出来，否则用户无从判断某个任务是整片还是片段。
    时长不放在这里（列宽有限会被省略号吃掉），放进 tooltip。
    """
    spec = task.spec
    pieces: list[str] = []
    if task.part_label:
        pieces.append(f"第 {task.part_label} 段")

    if spec.trim_start is not None or spec.trim_end is not None:
        start_ms = int((spec.trim_start or 0.0) * 1000)
        if spec.trim_end is None:
            pieces.append(f"{fmt_ms(start_ms)} ~ 结尾")
        else:
            pieces.append(f"{fmt_ms(start_ms)} ~ {fmt_ms(int(spec.trim_end * 1000))}")
    elif spec.mode == Mode.IMAGE_SEQ and (spec.frame_count or spec.start_number > 1):
        end_frame = (spec.start_number + spec.frame_count - 1) if spec.frame_count else None
        pieces.append(
            f"帧 {spec.start_number} ~ {'结尾' if end_frame is None else end_frame}"
            + (f"（{spec.frame_count} 帧）" if spec.frame_count else "")
        )

    if not pieces:
        return ""
    return "　".join(pieces) if len(pieces) > 1 else pieces[0]


def range_tooltip(task: Task, label: str) -> str:
    """区间列的悬浮提示：给出完整信息，包括时长。"""
    if not label:
        return "整个文件，未裁切"
    spec = task.spec
    if spec.trim_start is not None or spec.trim_end is not None:
        start = spec.trim_start or 0.0
        end = spec.trim_end
        span = f"，共 {max(0.0, end - start):.2f} 秒" if end is not None else "，到文件结尾"
        return f"裁切区间：{fmt_ms(int(start * 1000))} ~ " \
               f"{'结尾' if end is None else fmt_ms(int(end * 1000))}{span}"
    return label


class TaskTableModel(QAbstractTableModel):
    """一行一个任务。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[Task] = []
        self._row_of: dict[str, int] = {}

    # ------------------------------------------------------------ 行管理

    def append(self, task: Task) -> int:
        """追加一行，返回行号。"""
        row = len(self._rows)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rows.append(task)
        self._row_of[task.id] = row
        self.endInsertRows()
        return row

    def remove_at(self, row: int) -> None:
        if not 0 <= row < len(self._rows):
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        removed = self._rows.pop(row)
        self._row_of.pop(removed.id, None)
        for index in range(row, len(self._rows)):
            self._row_of[self._rows[index].id] = index
        self.endRemoveRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self._row_of.clear()
        self.endResetModel()

    def task_at(self, row: int) -> Task | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def row_of(self, task_id: str) -> int:
        return self._row_of.get(task_id, -1)

    def refresh(self, task: Task) -> int:
        """任务数据变了，通知视图重画这一行；返回行号（-1 = 不在表里）。"""
        row = self._row_of.get(task.id)
        if row is None:
            return -1
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMN_TITLES) - 1))
        return row

    # ------------------------------------------------- QAbstractTableModel

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMN_TITLES)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMN_TITLES[section]
        return None

    def flags(self, index) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        task = self._rows[index.row()]
        column = index.column()

        if role == Qt.DisplayRole:
            return self._display(task, column)
        if role == Qt.ToolTipRole:
            return self._tooltip(task, column)
        if role == Qt.ForegroundRole:
            return self._foreground(task, column)
        return None

    # ------------------------------------------------------------ 取值

    def _display(self, task: Task, column: int) -> str:
        if column == 0:
            return task.name
        if column == 1:
            return task.summary or ("—" if not task.info else "")
        if column == 2:
            return f"{output_container_label(task.spec)}{output_ext(task.spec)}"
        if column == 3:
            return range_label(task)
        if column == 4:
            return task.status.label
        if column == 6:
            return self._result_text(task)
        return ""

    def _tooltip(self, task: Task, column: int) -> str:
        if column == 0:
            return str(task.source)
        if column == 3:
            return range_tooltip(task, range_label(task))
        if column == 6:
            if task.status == TaskStatus.DONE and task.output:
                return str(task.output)
            if task.status == TaskStatus.FAILED:
                return task.error
        return ""

    def _foreground(self, task: Task, column: int) -> QColor | None:
        if column == 3:
            return QColor(COLOR_RANGE) if range_label(task) else QColor(COLOR_IDLE)
        if column == 4:
            return QColor({
                TaskStatus.DONE: COLOR_DONE,
                TaskStatus.FAILED: COLOR_FAILED,
                TaskStatus.RUNNING: COLOR_RUNNING,
            }.get(task.status, COLOR_IDLE))
        return None

    def _result_text(self, task: Task) -> str:
        if task.status == TaskStatus.DONE:
            return human_size(task.output_size)
        if task.status == TaskStatus.FAILED:
            return task.error[:60]
        if task.status == TaskStatus.RUNNING:
            return task.speed or ""
        return ""