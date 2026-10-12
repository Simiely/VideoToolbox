"""任务队列：调度多个转码任务，控制并发，跟踪状态，支持取消与重试。

不依赖任何 GUI 库，通过回调把变化通知给界面（界面负责切回主线程）。
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .binaries import Toolchain
from .builder import build
from .capabilities import Capabilities
from .probe import MediaInfo, probe
from .runner import run_plan
from .specs import EncodeSpec


class TaskStatus(str, Enum):
    WAITING = "waiting"
    PROBING = "probing"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            "waiting": "等待中",
            "probing": "读取信息",
            "running": "转换中",
            "done": "已完成",
            "failed": "失败",
            "cancelled": "已取消",
        }[self.value]

    @property
    def is_terminal(self) -> bool:
        return self in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)


@dataclass
class Task:
    id: str
    source: Path
    spec: EncodeSpec
    output: Path | None = None
    info: MediaInfo | None = None
    status: TaskStatus = TaskStatus.WAITING
    progress: float = 0.0
    speed: str = ""
    message: str = ""
    error: str = ""
    output_size: int = 0
    notes: list[str] = field(default_factory=list)
    # 图片序列任务的输入通配（如 frame_%04d.png）；源文件本身是首帧
    input_pattern: str = ""
    # 分段导出：该任务代表第几段（用于文件名后缀 {part}）
    part_label: str = ""
    # True 表示参数由专门对话框定好（分段/合并），不跟随右侧面板的改动
    spec_locked: bool = False

    @property
    def name(self) -> str:
        return self.source.name

    @property
    def summary(self) -> str:
        if self.info and self.info.ok:
            return self.info.summary()
        if self.info and self.info.error:
            return f"读取失败：{self.info.error}"
        return ""


class TaskQueue:
    """多线程任务调度器。回调均在工作线程触发。"""

    def __init__(
        self,
        toolchain: Toolchain,
        caps: Capabilities,
        max_workers: int = 1,
        on_update: Callable[[Task], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_idle: Callable[[], None] | None = None,
        output_provider: Callable[[Task], Path] | None = None,
    ) -> None:
        self.toolchain = toolchain
        self.caps = caps
        self.on_update = on_update or (lambda task: None)
        self.on_log = on_log or (lambda line: None)
        self.on_idle = on_idle or (lambda: None)
        self.output_provider = output_provider

        self.max_workers = max(1, int(max_workers))
        self.tasks: dict[str, Task] = {}
        self._order: list[str] = []
        self._pending: queue.Queue[str | None] = queue.Queue()
        self._queued: set[str] = set()   # 已进入待执行队列、尚未被取走的 task id
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: list[threading.Thread] = []
        self._lock = threading.RLock()
        self._started = False
        self._running = 0

    # ---- 任务管理 ---------------------------------------------------------

    def add(self, source: str | Path, spec: EncodeSpec, index: int = 0) -> Task:
        """登记一个新任务，状态为「等待中」。

        这里不自动入队：任务一律由用户按「开始转换」触发，
        否则"加个文件就自己跑起来"会让人措手不及。
        """
        task = Task(id=uuid4().hex, source=Path(source), spec=spec)
        with self._lock:
            self.tasks[task.id] = task
            self._order.append(task.id)
        self.on_update(task)
        return task

    def get(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def ordered(self) -> list[Task]:
        with self._lock:
            return [self.tasks[tid] for tid in self._order if tid in self.tasks]

    def remove(self, task_id: str) -> None:
        with self._lock:
            task = self.tasks.get(task_id)
            if task and task.status == TaskStatus.RUNNING:
                return
            self.tasks.pop(task_id, None)
            if task_id in self._order:
                self._order.remove(task_id)
            self._queued.discard(task_id)

    def clear_all(self) -> bool:
        """清空全部任务；有任务运行时返回 False。"""
        with self._lock:
            if any(t.status in (TaskStatus.RUNNING, TaskStatus.PROBING) for t in self.tasks.values()):
                return False
            self.tasks.clear()
            self._order.clear()
            self._queued.clear()
        return True

    # ---- 生命周期 ---------------------------------------------------------

    def start(self) -> None:
        """启动队列；可重复调用，每次都会把新出现的等待任务入队。"""
        with self._lock:
            self._started = True
            for tid in self._order:
                task = self.tasks.get(tid)
                if task and task.status == TaskStatus.WAITING:
                    self._enqueue(tid)
            self._ensure_workers()

    def _enqueue(self, task_id: str) -> None:
        """入队一次，避免重复投递。需在持有 _lock 的情况下调用。"""
        if task_id in self._queued:
            return
        self._queued.add(task_id)
        self._pending.put(task_id)

    def set_max_workers(self, count: int) -> None:
        with self._lock:
            count = max(1, min(int(count), 8))
            if count == self.max_workers:
                return
            if count > self.max_workers:
                self.max_workers = count
                if self._started:
                    self._ensure_workers()
            else:
                drop = self.max_workers - count
                self.max_workers = count
                for _ in range(drop):
                    self._pending.put(None)

    def _ensure_workers(self) -> None:
        while len(self._threads) < self.max_workers:
            thread = threading.Thread(target=self._worker, daemon=True, name="videotool-worker")
            self._threads.append(thread)
            thread.start()

    def cancel(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None:
            return
        if task.status == TaskStatus.WAITING:
            task.status = TaskStatus.CANCELLED
            task.message = "已取消"
            self.on_update(task)
            return
        event = self._cancel_events.get(task_id)
        if event:
            event.set()

    def cancel_all(self) -> None:
        for task in self.ordered():
            if task.status in (TaskStatus.WAITING, TaskStatus.RUNNING, TaskStatus.PROBING):
                self.cancel(task.id)

    def retry(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None or not task.status.is_terminal:
            return
        task.status = TaskStatus.WAITING
        task.progress = 0.0
        task.error = ""
        task.message = ""
        task.speed = ""
        task.output_size = 0
        task.notes = []
        self._cancel_events.pop(task_id, None)
        self.on_update(task)
        with self._lock:
            self._ensure_workers()
            self._enqueue(task_id)

    def shutdown(self) -> None:
        self.cancel_all()
        for _ in self._threads:
            self._pending.put(None)

    # ---- 统计 -------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        result = {s.value: 0 for s in TaskStatus}
        with self._lock:
            for task in self.tasks.values():
                result[task.status.value] += 1
        return result

    def active(self) -> bool:
        with self._lock:
            return any(
                t.status in (TaskStatus.WAITING, TaskStatus.RUNNING, TaskStatus.PROBING)
                for t in self.tasks.values()
            )

    # ---- 工作线程 ---------------------------------------------------------

    def _worker(self) -> None:
        while True:
            task_id = self._pending.get()
            if task_id is None:
                return
            with self._lock:
                self._queued.discard(task_id)
            task = self.tasks.get(task_id)
            if task is None or task.status != TaskStatus.WAITING:
                if not self._has_work():
                    self.on_idle()
                continue
            self._run_task(task)
            if not self._has_work():
                self.on_idle()

    def _has_work(self) -> bool:
        with self._lock:
            return any(
                t.status in (TaskStatus.WAITING, TaskStatus.PROBING, TaskStatus.RUNNING)
                for t in self.tasks.values()
            )

    def _run_task(self, task: Task) -> None:
        cancel_event = threading.Event()
        self._cancel_events[task.id] = cancel_event
        last_emit = [0.0]

        def emit(force: bool = False) -> None:
            now = time.monotonic()
            if force or now - last_emit[0] >= 0.08:
                last_emit[0] = now
                self.on_update(task)

        def on_progress(overall: float, step_ratio: float, speed: str) -> None:
            task.progress = overall
            if speed:
                task.speed = speed
            emit()

        def on_log(line: str) -> None:
            self.on_log(f"[{task.name}] {line}")

        # 1. 探测
        task.status = TaskStatus.PROBING
        task.message = "读取媒体信息"
        emit(force=True)
        if task.info is None:
            task.info = probe(self.toolchain, task.source)
        if not task.info.ok:
            task.status = TaskStatus.FAILED
            task.error = task.info.error or "无法读取媒体信息"
            task.message = task.error
            emit(force=True)
            self._finish(task)
            return
        if cancel_event.is_set():
            self._mark_cancelled(task)
            self._finish(task)
            return

        # 2. 输出路径
        try:
            if task.output is None and self.output_provider is not None:
                task.output = self.output_provider(task)
        except Exception as exc:  # 命名模板异常不能让任务崩掉
            task.status = TaskStatus.FAILED
            task.error = f"无法确定输出路径：{exc}"
            task.message = task.error
            emit(force=True)
            self._finish(task)
            return

        # 3. 构建计划
        try:
            build_info = task.info
            if task.input_pattern:
                build_info = replace(task.info, path=Path(task.input_pattern), duration=0.0)
            plan = build(task.spec, build_info, self.toolchain, self.caps, task.output)
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error = f"生成命令失败：{exc}"
            task.message = task.error
            emit(force=True)
            self._finish(task)
            return

        task.notes = list(plan.notes)
        for note in plan.notes:
            self.on_log(f"[{task.name}] 提示：{note}")

        # 4. 执行
        task.status = TaskStatus.RUNNING
        task.progress = 0.0
        emit(force=True)

        result = run_plan(plan, on_progress=on_progress, on_log=on_log, cancel_event=cancel_event)

        if result.cancelled:
            self._mark_cancelled(task)
        elif result.ok:
            task.status = TaskStatus.DONE
            task.progress = 1.0
            task.output_size = result.output_size
            task.message = "已完成"
            self.on_log(f"[{task.name}] 完成：{task.output}")
        else:
            task.status = TaskStatus.FAILED
            task.error = result.error or "转换失败"
            task.message = task.error
            self.on_log(f"[{task.name}] 失败：{task.error}")

        self._finish(task)
        emit(force=True)

    def _mark_cancelled(self, task: Task) -> None:
        task.status = TaskStatus.CANCELLED
        task.message = "已取消"
        task.speed = ""

    def _finish(self, task: Task) -> None:
        self._cancel_events.pop(task.id, None)
        self.on_update(task)