"""任务控制器：任务生命周期与转码队列之间的桥接。

「添加 / 探测 / 启动 / 取消 / 重试 / 移除 / 输出路径」这些动作集中在这里，
主窗口只负责界面：订阅信号更新表格与按钮，不再直接驱动队列。
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ..core.binaries import Toolchain
from ..core.capabilities import Capabilities
from ..core.naming import DEFAULT_TEMPLATE, build_output_path
from ..core.probe import MediaInfo, probe
from ..core.queue import Task, TaskQueue, TaskStatus
from ..core.specs import EncodeSpec, Mode, output_ext

VIDEO_EXT = {
    ".mp4", ".mkv", ".mov", ".avi", ".flv", ".ts", ".wmv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".3gp", ".ogv", ".rmvb", ".vob", ".m2ts", ".ts",
}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".ac3", ".aiff", ".ape"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif", ".jfif", ".heic"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT | IMAGE_EXT


def to_pattern(path: Path) -> str:
    """把 frame_0001.png 转成 frame_%04d.png 形式。"""
    stem = path.stem
    match = re.search(r"(\d+)(?!.*\d)", stem)
    if not match:
        return str(path)
    digits = match.group(1)
    pattern = stem[: match.start(1)] + f"%0{len(digits)}d" + stem[match.end(1):]
    return str(path.with_name(pattern + path.suffix))


class ProbeWorker(QThread):
    """批量探测媒体信息。ffprobe 要起子进程，放后台避免界面卡住。"""

    probed = Signal(str, object)

    def __init__(self, toolchain: Toolchain, items: list[tuple[str, str]]) -> None:
        super().__init__()
        self.toolchain = toolchain
        self.items = items

    def run(self) -> None:
        for task_id, path in self.items:
            info = probe(self.toolchain, path)
            self.probed.emit(task_id, info)


class TaskController(QObject):
    """任务集合的操作入口：添加 / 探测 / 启动 / 取消 / 重试 / 移除 / 输出路径。

    查询任务请直接读 queue 属性（它是任务的唯一数据源），本类不转发查询方法。
    """

    taskAdded = Signal(str)      # 新任务加入，界面需要补一行
    taskUpdated = Signal(str)    # 任务状态/进度变化，界面需要刷新这一行
    logged = Signal(str)
    idle = Signal()

    def __init__(self, toolchain: Toolchain, caps: Capabilities,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.toolchain = toolchain
        self._same_dir = True
        self._out_dir = ""
        self._template = DEFAULT_TEMPLATE
        self._probe_workers: list[QThread] = []

        self.queue = TaskQueue(
            toolchain,
            caps,
            max_workers=1,
            on_update=lambda task: self.taskUpdated.emit(task.id),
            on_log=self.logged.emit,
            on_idle=self.idle.emit,
            output_provider=self.output_for,
        )

    # ------------------------------------------------------------ 查询

    def has_waiting(self) -> bool:
        return any(task.status == TaskStatus.WAITING for task in self.queue.ordered())

    # ------------------------------------------------------ 输出设置与路径

    def set_output_prefs(self, same_dir: bool, out_dir: str, template: str) -> None:
        """同步「输出」面板的设置（源目录/指定目录、命名模板）。"""
        self._same_dir = same_dir
        self._out_dir = out_dir
        self._template = template

    def output_for(self, task: Task) -> Path:
        """任务的输出路径：按命名模板生成，并自动避让重名。"""
        ext = output_ext(task.spec)
        if self._same_dir or not self._out_dir:
            directory = task.source.parent
        else:
            directory = Path(self._out_dir)
        ordered = self.queue.ordered()
        index = ordered.index(task) + 1 if task in ordered else 1
        return build_output_path(task.source, directory, ext, self._template,
                                 index, part=task.part_label)

    # ------------------------------------------------------------ 添加任务

    def add_paths(self, paths: list[str | Path], spec: EncodeSpec) -> int:
        """添加文件（或文件夹里的全部媒体），返回实际新增的任务数。"""
        files: list[Path] = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                files.extend(sorted(f for f in path.rglob("*")
                                    if f.is_file() and f.suffix.lower() in MEDIA_EXT))
            elif path.suffix.lower() in MEDIA_EXT:
                files.append(path)

        if not files:
            self.logged.emit("没有找到可处理的媒体文件")
            return 0

        items: list[tuple[str, str]] = []
        for path in files:
            task = self.queue.add(path, spec)
            if spec.mode == Mode.IMAGE_SEQ:
                task.input_pattern = to_pattern(path)
                self.logged.emit(f"[{path.name}] 序列通配：{Path(task.input_pattern).name}")
            self.taskAdded.emit(task.id)
            items.append((task.id, str(path)))

        self.logged.emit(f"已添加 {len(files)} 个文件")
        self._probe_async(items)
        return len(files)

    def add_single(self, source: str | Path, spec: EncodeSpec) -> Task:
        """加一个参数已固定的任务（选段裁切 / 合并音视频）。

        spec_locked=True：参数由对话框或裁切窗口定好，不跟随右侧面板。
        """
        source = Path(source)
        task = self.queue.add(source, spec)
        task.spec_locked = True
        self.taskAdded.emit(task.id)
        self._probe_async([(task.id, str(source))])
        return task

    # ------------------------------------------------------------ 探测

    def _probe_async(self, items: list[tuple[str, str]]) -> None:
        if not items:
            return
        worker = ProbeWorker(self.toolchain, items)
        worker.probed.connect(self._on_probed)
        worker.finished.connect(
            lambda: self._probe_workers.remove(worker) if worker in self._probe_workers else None)
        self._probe_workers.append(worker)
        worker.start()

    def _on_probed(self, task_id: str, info: MediaInfo) -> None:
        task = self.queue.get(task_id)
        if task is None:
            return
        task.info = info
        self.taskUpdated.emit(task.id)

    # -------------------------------------------------- 启动 / 取消 / 重试

    def apply_spec_to_waiting(self, spec: EncodeSpec) -> None:
        """把面板参数写到等待中的任务。

        spec_locked 的任务（选段裁切/合并/分段）参数由对话框定好，跳过，
        否则改一次面板就会把它们的区间/合轨参数冲掉。
        """
        for task in self.queue.ordered():
            if task.status == TaskStatus.WAITING and not task.spec_locked:
                task.spec = spec
                self.taskUpdated.emit(task.id)

    def start(self, spec: EncodeSpec, max_workers: int) -> bool:
        """把等待中的任务交给队列执行；返回 False 表示没有等待中的任务。"""
        self.apply_spec_to_waiting(spec)
        if not self.has_waiting():
            return False
        self.logged.emit("开始转换…")
        self.queue.set_max_workers(max_workers)
        self.queue.start()
        return True

    def cancel_all(self) -> None:
        self.queue.cancel_all()

    def retry(self, task_id: str) -> None:
        self.queue.retry(task_id)

    def remove(self, task_id: str) -> bool:
        """移除任务；正在运行/探测的不能移除（记日志并返回 False）。"""
        task = self.queue.get(task_id)
        if task and task.status in (TaskStatus.RUNNING, TaskStatus.PROBING):
            self.logged.emit(f"任务正在运行，无法移除：{task.name}")
            return False
        self.queue.remove(task_id)
        return True

    def clear_all(self) -> None:
        self.queue.clear_all()