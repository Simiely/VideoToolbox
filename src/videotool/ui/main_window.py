"""主窗口：任务列表 + 参数面板 + 进度与日志。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME
from ..core.binaries import Toolchain
from ..core.capabilities import Capabilities, detect
from ..core.probe import MediaInfo, probe
from ..core.queue import Task, TaskStatus
from ..core.specs import Mode, output_container_label
from .info_dialog import MediaInfoDialog
from .merge_dialog import MergeDialog
from .settings_panel import SettingsPanel
from .task_controller import TaskController
from .task_table import PROGRESS_COLUMN, TaskTableModel
from .trim_dialog import TrimDialog, can_preview, fmt_ms


class CapabilityWorker(QThread):
    finished_detecting = Signal(object)

    def __init__(self, toolchain: Toolchain) -> None:
        super().__init__()
        self.toolchain = toolchain

    def run(self) -> None:
        self.finished_detecting.emit(detect(self.toolchain))


class SingleProbeWorker(QThread):
    """单独探测一个文件（用于「查看参数」），避免界面卡住。"""

    probed = Signal(object)
    failed = Signal(str)

    def __init__(self, toolchain: Toolchain, path: Path) -> None:
        super().__init__()
        self.toolchain = toolchain
        self.path = path

    def run(self) -> None:
        try:
            self.probed.emit(probe(self.toolchain, self.path))
        except Exception as exc:  # 兜底，避免线程里抛异常导致界面毫无反应
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class MainWindow(QMainWindow):
    def __init__(self, toolchain: Toolchain, caps: Capabilities | None = None) -> None:
        super().__init__()
        self.toolchain = toolchain
        self.caps = caps or Capabilities()
        self._workers: list[QThread] = []

        # 任务与队列的动作统一走控制器；self.queue 只留给只读查询
        self.tasks = TaskController(toolchain, self.caps, self)
        self.queue = self.tasks.queue
        self.tasks.taskAdded.connect(self._on_task_added)
        self.tasks.taskUpdated.connect(self._refresh_task_by_id)
        self.tasks.logged.connect(self._append_log)
        self.tasks.idle.connect(self._on_idle)

        # 参数面板自己管控件联动，只把「设置变了」通知主窗口
        self.panel = SettingsPanel(self.caps, self)
        self.panel.settingsChanged.connect(self._on_settings_changed)
        self.panel.outputChanged.connect(self._on_output_changed)

        self.setWindowTitle(f"{APP_NAME} — 视频压缩转换工具")
        self.resize(1200, 780)
        self.setAcceptDrops(True)

        self._build_ui()
        self._update_actions()

        if caps is None:
            self._start_capability_detect()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(6)

        root.addLayout(self._build_toolbar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_table())
        splitter.addWidget(self.panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([820, 360])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_log())

        self.setCentralWidget(central)

        self.statusBar().showMessage(self._toolchain_text())

    def _toolchain_text(self) -> str:
        source = "内置" if self.toolchain.source == "bundled" else "系统 PATH"
        return f"ffmpeg {self.toolchain.version}（{source}）  ·  拖入文件即可添加任务"

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self.add_files_btn = QPushButton("添加文件")
        self.add_files_btn.clicked.connect(self._choose_files)
        self.add_dir_btn = QPushButton("添加文件夹")
        self.add_dir_btn.clicked.connect(self._choose_dir)
        self.inspect_btn = QPushButton("查看参数")
        self.inspect_btn.setToolTip("选择任意媒体文件，读取并显示它的详细参数（类似 MediaInfo）")
        self.inspect_btn.clicked.connect(self._inspect_file)
        self.merge_btn = QPushButton("合并音视频")
        self.merge_btn.setToolTip("把外部音频合进视频（默认视频直接复制，秒完成）")
        self.merge_btn.clicked.connect(self._merge_media)
        self.trim_btn = QPushButton("选段裁切")
        self.trim_btn.setToolTip("边看边选：预览播放 + 拖动时间轴圈出要保留的片段")
        self.trim_btn.clicked.connect(self._visual_trim)
        self.remove_btn = QPushButton("移除选中")
        self.remove_btn.clicked.connect(self._remove_selected)
        self.clear_btn = QPushButton("清空列表")
        self.clear_btn.clicked.connect(self._clear_all)

        for btn in (self.add_files_btn, self.add_dir_btn, self.inspect_btn,
                    self.trim_btn, self.merge_btn,
                    self.remove_btn, self.clear_btn):
            bar.addWidget(btn)

        bar.addStretch(1)

        bar.addWidget(QLabel("并发任务"))
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 8)
        self.concurrency.setValue(1)
        self.concurrency.setFixedWidth(60)
        self.concurrency.setToolTip("同时进行的转码数量。视频编码吃 CPU，建议保持 1。")
        self.concurrency.valueChanged.connect(self.queue.set_max_workers)
        bar.addWidget(self.concurrency)

        return bar

    def _build_table(self) -> QWidget:
        self.table = QTableView()
        self.model = TaskTableModel(self.table)
        self.table.setModel(self.model)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.doubleClicked.connect(self._open_row_output)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        header = self.table.horizontalHeader()
        # 各列宽度要能在默认窗口下全部放下（右侧还有设置面板占位），
        # 否则最后的「结果」列会被挤出可视区，需要横向滚动才看得到。
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        for column in range(1, 6):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        header.setSectionResizeMode(6, QHeaderView.Stretch)   # 结果列吃掉剩余宽度
        self.table.setColumnWidth(0, 160)
        self.table.setColumnWidth(1, 118)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 152)   # 裁切区间
        self.table.setColumnWidth(4, 62)
        self.table.setColumnWidth(5, 80)
        return self.table

    def _build_log(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setFixedHeight(96)
        self.log_view.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log_view)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.overall_label = QLabel("就绪")
        self.overall_label.setObjectName("muted")
        actions.addWidget(self.overall_label)

        self.overall_bar = QProgressBar()
        self.overall_bar.setTextVisible(False)
        # 高度要跟主题里 QProgressBar 的 min-height(14) 对齐，写小了底部会被裁掉
        self.overall_bar.setFixedHeight(14)
        self.overall_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        actions.addWidget(self.overall_bar, 1)

        self.retry_btn = QPushButton("重试选中")
        self.retry_btn.clicked.connect(self._retry_selected)
        actions.addWidget(self.retry_btn)

        self.cancel_btn = QPushButton("取消全部")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.clicked.connect(self.tasks.cancel_all)
        actions.addWidget(self.cancel_btn)

        self.start_btn = QPushButton("开始转换")
        self.start_btn.setObjectName("primary")
        self.start_btn.setMinimumWidth(120)
        self.start_btn.clicked.connect(self._start)
        actions.addWidget(self.start_btn)

        layout.addLayout(actions)
        return host

    # -------------------------------------------------------- 面板信号联动

    def _on_output_changed(self) -> None:
        """面板改了输出目录/命名模板：同步给任务控制器，并刷新等待中的行。"""
        same_dir, out_dir, template = self.panel.output_prefs()
        self.tasks.set_output_prefs(same_dir, out_dir, template)
        for task in self.queue.ordered():
            if task.status == TaskStatus.WAITING:
                self._refresh_row(task)

    def _on_settings_changed(self) -> None:
        """面板改了任何影响 EncodeSpec 的设置：同步给等待中的任务。"""
        self.tasks.apply_spec_to_waiting(self.panel.current_spec())
        self._update_actions()

    # ------------------------------------------------------------ 任务操作

    def _selected_rows(self) -> list[int]:
        """当前选中的行号：去重、升序。

        selectedIndexes() 会为选中行的每一列各返回一个 index，
        必须按行号去重，否则同一个任务会被处理多次。
        """
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def _selected_tasks(self) -> list[Task]:
        """当前选中的任务，按行号升序；没有任务的行自动跳过。"""
        tasks: list[Task] = []
        for row in self._selected_rows():
            task = self.model.task_at(row)
            if task is not None:
                tasks.append(task)
        return tasks

    def _on_task_added(self, task_id: str) -> None:
        """控制器每加一个任务，界面补一行并刷新按钮状态。"""
        task = self.queue.get(task_id)
        if task is not None:
            self._append_row(task)
            self._update_actions()

    def _choose_files(self) -> None:
        mode, _ = self.panel.target_combo.currentData()
        if mode == Mode.IMAGE_SEQ:
            caption = "选择图片序列中的任意一张"
            pattern = "图片 (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp);;所有文件 (*.*)"
        else:
            caption = "选择要转换的文件"
            pattern = ("媒体文件 (*.mp4 *.mkv *.mov *.avi *.flv *.ts *.webm *.wmv *.m4v *.mpg);;"
                       "图片 (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif *.avif);;"
                       "音频 (*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus *.wma);;所有文件 (*.*)")
        files, _ = QFileDialog.getOpenFileNames(self, caption, "", pattern)
        if files:
            self._add_paths(files)

    def _choose_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择包含媒体文件的文件夹")
        if directory:
            self._add_paths([directory])

    # ------------------------------------------------------------ 查看参数

    def _inspect_file(self) -> None:
        """选一个媒体文件看它的详细参数。默认打开已选中任务所在目录。"""
        first = next(iter(self._selected_tasks()), None)
        start_dir = str(first.source.parent) if first is not None else ""

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择要查看参数的文件",
            start_dir,
            "媒体文件 (*.*)",
        )
        if path:
            self._inspect_path(Path(path))

    def _inspect_path(self, path: Path) -> None:
        """后台探测后弹出参数窗口。"""
        worker = SingleProbeWorker(self.toolchain, path)
        worker.probed.connect(self._show_info_dialog)
        worker.failed.connect(lambda msg: QMessageBox.warning(
            self, "读取失败", f"无法读取文件参数：\n{msg}"))
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _show_info_dialog(self, info: MediaInfo) -> None:
        MediaInfoDialog(info, self).exec()

    # ------------------------------------------------------------ 可视化选段

    def _visual_trim(self) -> None:
        """打开「边看边选」窗口，用播放器 + 时间轴圈出要保留的片段。"""
        target = self._selected_video_task()
        if target is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择要裁切的视频", "", "媒体文件 (*.*)")
            if not path:
                return
            info = probe(self.toolchain, path)
            if not can_preview(info):
                QMessageBox.information(
                    self, "无法预览",
                    "这个文件没有可用时长（可能是图片或序列帧），"
                    "无法用播放器预览。\n可以在右侧「时间裁切」面板里直接填写时间。",
                )
                return
            source = Path(path)
        else:
            source = target.source
            info = target.info

        dialog = TrimDialog(info, self.toolchain, self)
        if dialog.exec() != QDialog.Accepted:
            return

        start, end = dialog.selection_seconds()
        if end - start < 0.05:
            QMessageBox.information(self, "区间太小", "选中的区间不到 0.05 秒，请重新选择。")
            return

        spec = self.panel.current_spec()
        if spec.mode == Mode.IMAGE:
            # 单帧模式没有意义，改用视频模式
            spec = spec.copy_with(mode=Mode.VIDEO, target="mp4")
        spec = spec.copy_with(trim_start=start, trim_end=end)
        # 参数由可视化窗口定好（spec_locked），不会再被面板覆盖
        task = self.tasks.add_single(source, spec)
        self._append_log(
            f"[{task.name}] 已添加裁切任务：{fmt_ms(int(start * 1000))} ~ "
            f"{fmt_ms(int(end * 1000))}（{end - start:.2f} 秒）"
        )

    def _selected_video_task(self) -> Task | None:
        """列表里选中的、能预览的视频任务。"""
        for task in self._selected_tasks():
            if task.info and can_preview(task.info):
                return task
        return None

    # ------------------------------------------------------------ 合并音视频

    def _merge_media(self) -> None:
        """打开合并对话框，确定后把任务加入队列。选中任务时把它预填为视频源。"""
        first = next(iter(self._selected_tasks()), None)
        preset = str(first.source) if first is not None else ""

        dialog = MergeDialog(self.caps, video=preset, audio="", parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        spec = dialog.spec()
        video = dialog.video_path()
        # 参数由对话框定好（spec_locked），不跟随右侧面板
        task = self.tasks.add_single(video, spec)
        self._append_log(f"[{task.name}] 已添加合并任务：{output_container_label(spec)}")

    # ------------------------------------------------------------ 时间裁切

    def _on_selection_changed(self) -> None:
        """选中变化：把当前任务的源时长提示交给参数面板显示。"""
        self.panel.set_selected_duration(self._selected_duration_text())

    def _selected_duration_text(self) -> str:
        for task in self._selected_tasks():
            if task.info and task.info.ok and task.info.duration > 0:
                return f"　源时长 {task.info.duration_label}（{task.info.duration:.2f} 秒）"
        return ""

    def _add_paths(self, paths: list[str]) -> None:
        """添加文件/文件夹（展开、过滤媒体扩展名等细节由控制器负责）。"""
        self.tasks.add_paths(paths, self.panel.current_spec())

    def _remove_selected(self) -> None:
        for row in reversed(self._selected_rows()):
            task = self.model.task_at(row)
            if task is None:
                continue
            if self.tasks.remove(task.id):
                self.model.remove_at(row)
        self._update_actions()

    def _clear_all(self) -> None:
        if not self.queue.tasks:
            return
        if self.queue.active():
            answer = QMessageBox.question(self, "清空列表", "有任务正在运行，是否取消并清空？")
            if answer != QMessageBox.Yes:
                return
            self.tasks.cancel_all()
        self.tasks.clear_all()
        self.model.clear()
        self._update_actions()

    def _retry_selected(self) -> None:
        for task in self._selected_tasks():
            if task.status.is_terminal:
                self.tasks.retry(task.id)

    def _start(self) -> None:
        if not self.tasks.start(self.panel.current_spec(), self.concurrency.value()):
            self.statusBar().showMessage("没有等待中的任务", 4000)
            return
        self._update_actions()

    def _on_idle(self) -> None:
        counts = self.queue.counts()
        self.overall_label.setText(
            f"完成 {counts['done']} · 失败 {counts['failed']} · 取消 {counts['cancelled']}"
        )
        self.overall_bar.setValue(100 if counts["done"] else 0)
        self._append_log("队列已空闲")
        self._update_actions()

    def _update_actions(self) -> None:
        counts = self.queue.counts()
        waiting = counts["waiting"]

        # 开始按钮的可用条件只有一个：有没有等待中的任务。
        # 不要用 queue.active() 判断——它的语义是"队列还有事情要做"，
        # 把 WAITING 也算在内，会导致 not active 恒为假、按钮永远点不动（曾经的 bug）。
        # 也不要在运行中禁用：运行中再加入的任务同样需要它来启动。
        self.start_btn.setEnabled(waiting > 0)
        self.cancel_btn.setEnabled(self.queue.active())
        self.retry_btn.setEnabled(any(t.status.is_terminal for t in self.queue.ordered()))

        busy = counts["running"] + counts["probing"] > 0
        if busy:
            total = sum(counts[s.value] for s in TaskStatus)
            finished = counts["done"] + counts["failed"] + counts["cancelled"]
            self.overall_label.setText(f"进行中 {finished}/{total}")
            self.overall_bar.setValue(int(self._average_progress() * 100))
        elif waiting:
            self.overall_label.setText(f"待转换 {waiting} 个，点「开始转换」")

    def _average_progress(self) -> float:
        tasks = self.queue.ordered()
        if not tasks:
            return 0.0
        done_weight = {"done": 1.0, "failed": 1.0, "cancelled": 1.0}
        total = 0.0
        for task in tasks:
            if task.status.value in done_weight:
                total += done_weight[task.status.value]
            else:
                total += task.progress
        return total / len(tasks)

    # ------------------------------------------------------------ 表格渲染

    def _append_row(self, task: Task) -> None:
        """新任务入表：模型补一行，「进度」列外挂一个进度条。"""
        row = self.model.append(task)
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setTextVisible(True)
        bar.setFormat("%p%")
        bar.setFixedHeight(16)
        self.table.setIndexWidget(self.model.index(row, PROGRESS_COLUMN), bar)
        self._refresh_row(task)

    def _refresh_task_by_id(self, task_id: str) -> None:
        task = self.queue.get(task_id)
        if task is not None:
            self._refresh_row(task)
            # 任务状态一变（等待→运行→完成）就要重算按钮可用性，
            # 漏掉这一步会让「开始转换」停留在过时的可用状态。
            self._update_actions()

    def _refresh_row(self, task: Task) -> None:
        """刷新一行：文字与颜色由模型重画，进度条在视图这侧更新。"""
        row = self.model.refresh(task)
        if row < 0:
            return
        bar: QProgressBar = self.table.indexWidget(self.model.index(row, PROGRESS_COLUMN))
        if bar is not None:
            bar.setValue(int(max(0.0, min(task.progress, 1.0)) * 1000))
            bar.setObjectName("done" if task.status == TaskStatus.DONE else
                              ("failed" if task.status == TaskStatus.FAILED else ""))
            bar.setFormat("" if task.status == TaskStatus.DONE else "%p%")
            bar.style().unpolish(bar)
            bar.style().polish(bar)

    # ------------------------------------------------------------ 交互事件

    def _table_menu(self, position) -> None:
        menu = QMenu(self)
        trim_action = menu.addAction("选段裁切（预览后圈选）")
        info_action = menu.addAction("查看媒体参数")
        menu.addSeparator()
        open_action = menu.addAction("打开输出文件")
        dir_action = menu.addAction("打开输出目录")
        menu.addSeparator()
        retry_action = menu.addAction("重试选中")
        remove_action = menu.addAction("移除选中")

        action = menu.exec(self.table.viewport().mapToGlobal(position))
        if action is None:
            return
        if action == trim_action:
            self._visual_trim()
        elif action == info_action:
            task = self.model.task_at(self.table.indexAt(position).row())
            if task:
                self._inspect_path(task.source)
        elif action == open_action:
            index = self.table.indexAt(position)
            self._open_row_output(index)
        elif action == dir_action:
            task = self.model.task_at(self.table.indexAt(position).row())
            if task and task.output:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(task.output.parent)))
        elif action == retry_action:
            self._retry_selected()
        elif action == remove_action:
            self._remove_selected()

    def _open_row_output(self, index) -> None:
        task = self.model.task_at(index.row())
        if task and task.output and task.output.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(task.output)))

    def _append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _start_capability_detect(self) -> None:
        """后台探测硬件编码器。不锁「开始转换」：
        探测只影响编码器下拉列表，就算探测失败也不该让按钮卡死。"""
        self.statusBar().showMessage("正在检测可用的硬件编码器…")
        worker = CapabilityWorker(self.toolchain)
        worker.finished_detecting.connect(self._on_caps_ready)
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _on_caps_ready(self, caps: Capabilities) -> None:
        self.caps = caps
        self.queue.caps = caps
        hw = ", ".join(label for _enc, label in caps.hardware_options()) or "无"
        self._append_log(f"能力检测完成，可用硬件编码器：{hw}")
        if caps.hw_failed:
            self._append_log(f"以下硬件编码器存在但不可用：{', '.join(caps.hw_failed)}")
        self.statusBar().showMessage(self._toolchain_text())
        self.panel.set_caps(caps)
        self._update_actions()

    # ------------------------------------------------------------ 拖拽支持

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self._add_paths(paths)
            event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        if self.queue.active():
            answer = QMessageBox.question(self, "退出", "仍有任务在运行，确定退出吗？")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.queue.shutdown()
        super().closeEvent(event)