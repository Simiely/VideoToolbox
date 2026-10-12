"""可视化选段裁切窗口。

左边是真正的视频预览（QtMultimedia 播放），下面是可拖拽的时间轴：
拖动两个把手圈出要保留的区间，边看边定，不用输时间。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.probe import MediaInfo
from ..core.proxy import make_proxy, proxy_path
from .range_slider import RangeSlider
from .theme import COLORS

DEFAULT_FRAME_MS = 40
DEBOUNCE_MS = 300      # 拖动把手停下多久后才生成预览代理


class ProxyWorker(QThread):
    """在后台生成预览代理，避免界面卡住。"""

    finished_proxy = Signal(bool, str, float, float)

    def __init__(self, toolchain, source, start_sec: float, end_sec: float,
                 has_audio: bool, source_width: int) -> None:
        super().__init__()
        self.toolchain = toolchain
        self.source = source
        self.start_sec = start_sec
        self.end_sec = end_sec
        self.has_audio = has_audio
        self.source_width = source_width

    def run(self) -> None:
        ok, error, _out = make_proxy(
            self.toolchain, self.source, self.start_sec, self.end_sec,
            has_audio=self.has_audio, source_width=self.source_width,
        )
        self.finished_proxy.emit(ok, error, self.start_sec, self.end_sec)


def fmt_ms(ms: int) -> str:
    """毫秒 -> m:ss.cc（两位小数足够表达到帧）。"""
    ms = max(0, int(ms))
    total_s, cs = divmod(ms, 1000)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    base = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return f"{base}.{cs // 10:02d}"


def parse_clock(text: str) -> int | None:
    """把 m:ss.cc / h:mm:ss.cc / 90 解析成毫秒。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        parts = text.split(":")
        if len(parts) == 1:
            seconds = float(parts[0])
        elif len(parts) == 2:
            seconds = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        else:
            return None
    except ValueError:
        return None
    return int(round(max(0.0, seconds) * 1000))


class VideoSurface(QWidget):
    """自己绘制视频帧，而不是用 QVideoWidget。

    QVideoWidget 走原生窗口表面，截图抓不到、在某些布局里还会出现黑块。
    这里直接接收 QVideoSink 的帧自己画，等比缩放居中，行为完全可控。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self.setMinimumHeight(300)
        self.setStyleSheet("background-color:#000000;")

    def set_frame(self, frame) -> None:
        image = frame.toImage()
        if image.isNull():
            return
        self._image = image
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self._image is not None:
            # 关键：不要把每帧都 scaled() 一遍再去画。
            # 1080p 每帧做一次平滑缩放会吃满 CPU，循环播放时直接卡成幻灯片。
            # 直接把目标矩形交给 drawImage，缩放交给绘制引擎，快得多。
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            size = self._image.size()
            if size.width() > 0 and size.height() > 0:
                scale = min(self.width() / size.width(), self.height() / size.height())
                w = max(1, int(size.width() * scale))
                h = max(1, int(size.height() * scale))
                painter.drawImage(
                    QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h),
                    self._image,
                )
        painter.end()


class TrimDialog(QDialog):
    """返回选中的时间区间（秒）。"""

    def __init__(self, info: MediaInfo, toolchain=None, parent=None) -> None:
        super().__init__(parent)
        self.info = info
        self.toolchain = toolchain
        self.frame_ms = int(round(1000 / info.video.fps)) if (info.video and info.video.fps) else DEFAULT_FRAME_MS
        self._duration_ms = max(1, int(round(info.duration * 1000)))
        self._loop_pending = False
        self._loop_at = 0.0
        self.loop_seek_count = 0   # 循环一共跳了几次（用于验证没有重复 seek）

        # 循环预览相关状态：代理文件 + 防抖定时器
        self._loop_mode = False          # True = 正在播放代理（原生循环，零 seek）
        self._proxy_path: Path | None = None
        self._proxy_range: tuple[int, int] | None = None
        self._proxy_worker: ProxyWorker | None = None
        self._pending_range: tuple[int, int] | None = None
        self._proxy_files: set[Path] = set()
        self._want_play = False          # 代理生成完后是否要立刻开始循环播放
        self.proxy_render_count = 0      # 一共生成了几次代理（用于验证防抖生效）

        self.setWindowTitle(f"选段裁切 — {info.path.name}")
        self.resize(920, 660)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)
        root.addWidget(self._build_header())
        root.addWidget(self._build_preview(), 1)
        root.addWidget(self._build_transport())
        root.addWidget(self._build_timeline())
        root.addWidget(self._build_selection_row())
        root.addWidget(self._build_buttons())

        self._setup_player()
        self._setup_shortcuts()

    # ------------------------------------------------------------------ 组成

    def _build_header(self) -> QLabel:
        label = QLabel()
        label.setTextFormat(Qt.RichText)
        label.setText(
            f"<div style='font-weight:bold; color:{COLORS['text']};'>{self.info.path.name}</div>"
            f"<div style='color:{COLORS['muted']};'>总时长 {fmt_ms(self._duration_ms)}　·　"
            f"拖动时间轴上的两个把手选出要保留的部分，只在选中区间内循环播放</div>"
        )
        return label

    def _build_preview(self) -> QWidget:
        self.surface = VideoSurface()
        return self.surface

    def _build_transport(self) -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        def button(text: str, tip: str, handler) -> QPushButton:
            btn = QPushButton(text)
            btn.setToolTip(tip)
            # 不要给固定高度：主题里 QPushButton 的 min-height 18 + 上下 padding 10
            # + 边框 2 = 至少 30px，写死 28 会把按钮底部裁掉。
            btn.setMinimumWidth(34)
            btn.clicked.connect(handler)
            row.addWidget(btn)
            return btn

        self.pos_label = QLabel(fmt_ms(0))
        self.pos_label.setMinimumWidth(74)
        self.pos_label.setStyleSheet(f"color:{COLORS['text']}; font-weight:bold;")
        row.addWidget(self.pos_label)

        button("|◀", "回到起点", lambda: self._seek_ms(self.range_bar.selection()[0]))
        button("◀◀", "后退 1 秒", lambda: self._nudge(-1000))
        button("◀|", "后退一帧", lambda: self._nudge(-self.frame_ms))
        self.play_btn = button("▶ 播放", "空格键播放 / 暂停", self._toggle_play)
        self.play_btn.setObjectName("primary")
        self.play_btn.setMinimumWidth(88)
        button("|▶", "前进一帧", lambda: self._nudge(self.frame_ms))
        button("▶▶", "前进 1 秒", lambda: self._nudge(1000))
        button("▶|", "跳到终点", lambda: self._seek_ms(self.range_bar.selection()[1]))

        self.status_label = QLabel()
        self.status_label.setObjectName("muted")
        row.addWidget(self.status_label)

        row.addStretch(1)

        self.loop_check = QCheckBox("在选中区间内循环")
        self.loop_check.setChecked(True)
        self.loop_check.setToolTip("播放到终点后自动回到起点，方便反复确认")
        row.addWidget(self.loop_check)

        row.addWidget(QLabel("音量"))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setFixedWidth(96)
        self.volume.valueChanged.connect(self._on_volume)
        row.addWidget(self.volume)
        return host

    def _build_timeline(self) -> QWidget:
        self.range_bar = RangeSlider()
        self.range_bar.setRange(0, self._duration_ms)
        self.range_bar.positionChanged.connect(self._on_position)
        self.range_bar.rangeChanged.connect(self._on_range_changed_by_user)
        return self.range_bar

    def _build_selection_row(self) -> QWidget:
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        grid.addWidget(QLabel("起点"), 0, 0)
        self.in_edit = QLineEdit(fmt_ms(0))
        self.in_edit.setFixedWidth(110)
        self.in_edit.editingFinished.connect(self._on_in_edited)
        grid.addWidget(self.in_edit, 0, 1)

        self.set_in_btn = QPushButton("设为起点 (I)")
        self.set_in_btn.clicked.connect(lambda: self._set_mark("in"))
        grid.addWidget(self.set_in_btn, 0, 2)

        grid.addWidget(QLabel("终点"), 0, 3)
        self.out_edit = QLineEdit(fmt_ms(self._duration_ms))
        self.out_edit.setFixedWidth(110)
        self.out_edit.editingFinished.connect(self._on_out_edited)
        grid.addWidget(self.out_edit, 0, 4)

        self.set_out_btn = QPushButton("设为终点 (O)")
        self.set_out_btn.clicked.connect(lambda: self._set_mark("out"))
        grid.addWidget(self.set_out_btn, 0, 5)

        self.selection_label = QLabel()
        self.selection_label.setStyleSheet(f"color:{COLORS['accent']}; font-weight:bold;")
        grid.addWidget(self.selection_label, 1, 0, 1, 6)

        grid.setColumnStretch(6, 1)
        self._refresh_selection_text()
        return host

    def _build_buttons(self) -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)

        box = QDialogButtonBox()
        ok = box.addButton("添加裁切任务", QDialogButtonBox.AcceptRole)
        ok.setToolTip("按选中的区间生成一个转码任务，之后在列表里点开始转换")
        ok.clicked.connect(self.accept)
        box.addButton("取消", QDialogButtonBox.RejectRole).clicked.connect(self.reject)
        row.addWidget(box)
        return host

    # ------------------------------------------------------------------ 播放器

    def _setup_player(self) -> None:
        # 播放器一：原文件，用于定位、逐帧查看（seek 只用在这一侧）
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player.setAudioOutput(self.audio)
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self.surface.set_frame)
        self.player.setVideoSink(self.sink)
        self.player.errorOccurred.connect(self._on_error)
        self.player.positionChanged.connect(self._on_player_position)
        self.player.durationChanged.connect(self._on_player_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.setSource(QUrl.fromLocalFile(str(self.info.path)))
        self.player.pause()

        # 播放器二：预览代理（只包含选中片段），用原生循环，全程不 seek
        self.loop_player = QMediaPlayer(self)
        self.loop_audio = QAudioOutput(self)
        self.loop_audio.setVolume(0.7)
        self.loop_player.setAudioOutput(self.loop_audio)
        self.loop_sink = QVideoSink(self)
        self.loop_sink.videoFrameChanged.connect(self.surface.set_frame)
        self.loop_player.setVideoSink(self.loop_sink)
        self.loop_player.setLoops(QMediaPlayer.Loops.Infinite)
        self.loop_player.positionChanged.connect(self._on_loop_position)
        self.loop_player.errorOccurred.connect(self._on_error)
        self.loop_player.playbackStateChanged.connect(self._on_state)

        # 拖动把手时不要每动一下就重新生成代理，停手 300ms 再生成
        self.proxy_debounce = QTimer(self)
        self.proxy_debounce.setSingleShot(True)
        self.proxy_debounce.setInterval(DEBOUNCE_MS)
        self.proxy_debounce.timeout.connect(self._on_debounce_fire)

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key_Space), self, self._toggle_play)
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self._nudge(-self.frame_ms))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self._nudge(self.frame_ms))
        QShortcut(QKeySequence("I"), self, lambda: self._set_mark("in"))
        QShortcut(QKeySequence("O"), self, lambda: self._set_mark("out"))

    # ------------------------------------------------------------------ 状态同步

    def _on_player_duration(self, ms: int) -> None:
        # 以播放器报的时长为准（更贴近实际可播放长度）
        if ms > 0:
            self._duration_ms = ms
            self.range_bar.setRange(0, ms)
            self.out_edit.setText(fmt_ms(ms))
            self._refresh_selection_text()

    def _on_player_position(self, ms: int) -> None:
        if self._loop_mode:
            return   # 循环播放时位置由代理播放器负责
        self.range_bar.blockSignals(True)
        self.range_bar.setPosition(ms)
        self.range_bar.blockSignals(False)
        self.pos_label.setText(fmt_ms(ms))

    def _on_loop_position(self, ms: int) -> None:
        """代理播放器的位置是「相对选中段」的，换算回源时间轴显示。"""
        if not self._loop_mode:
            # 退出循环模式时 loop_player.stop() 会发出 positionChanged(0)，
            # 若照单全收会被换算成「入点」把指针拉回去，这里直接忽略。
            return
        start, _end = self.range_bar.selection()
        source_ms = start + ms
        self.range_bar.blockSignals(True)
        self.range_bar.setPosition(source_ms)
        self.range_bar.blockSignals(False)
        self.pos_label.setText(fmt_ms(source_ms))

    def _on_position(self, ms: int) -> None:
        """用户拖动时间轴 / 点进度条：回到定位模式（这一侧允许 seek）。"""
        self._exit_loop_mode()
        self.pos_label.setText(fmt_ms(ms))
        self.player.setPosition(ms)
        self._restart_debounce()

    def _on_range_changed_by_user(self, start: int, end: int) -> None:
        """入点/出点被改动：更新文字、退出循环模式，并延迟重建代理。"""
        self._refresh_selection_text()
        self._exit_loop_mode()
        self._restart_debounce()

    # ------------------------------------------------------- 代理与循环播放

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _restart_debounce(self) -> None:
        if not self.loop_check.isChecked() or self.toolchain is None:
            return
        start, end = self.range_bar.selection()
        if end - start < 200:
            return
        self.proxy_debounce.start()

    def _on_debounce_fire(self) -> None:
        start, end = self.range_bar.selection()
        if end - start < 200:
            return
        if self._proxy_range == (start, end) and self._proxy_path and self._proxy_path.exists():
            return   # 区间没变，代理可直接复用
        self._render_proxy(start, end)

    def _render_proxy(self, start_ms: int, end_ms: int) -> None:
        if self.toolchain is None:
            return
        if self._proxy_worker is not None:
            # 已有一次在生成：记下来排队，否则这次请求会被丢掉，
            # 表现就是点了播放却没反应。
            self._pending_range = (start_ms, end_ms)
            return
        self.proxy_render_count += 1
        self._set_status(f"正在生成循环预览…（{fmt_ms(start_ms)} ~ {fmt_ms(end_ms)}）")
        width = self.info.video.width if self.info.video else 0
        self._proxy_worker = ProxyWorker(
            self.toolchain, self.info.path, start_ms / 1000.0, end_ms / 1000.0,
            self.info.has_audio, width,
        )
        self._proxy_worker.finished_proxy.connect(self._on_proxy_ready)
        self._proxy_worker.start()

    def _on_proxy_ready(self, ok: bool, error: str, start_sec: float, end_sec: float) -> None:
        self._proxy_worker = None
        if ok:
            path = proxy_path(self.info.path, start_sec, end_sec)
            self._proxy_files.add(path)
            self._proxy_path = path
            self._proxy_range = (int(start_sec * 1000), int(end_sec * 1000))
        else:
            self._set_status(f"预览生成失败：{error}")
            self._want_play = False

        # 有排队的请求先做掉
        if self._pending_range is not None:
            nxt = self._pending_range
            self._pending_range = None
            if nxt != self._proxy_range:
                self._render_proxy(*nxt)
                return

        if self._want_play and self._proxy_path and self._proxy_path.exists():
            self._want_play = False
            self._start_loop_playback()
        elif ok:
            self._set_status("循环预览已就绪")

    def _start_loop_playback(self) -> None:
        if self._proxy_path is None or not self._proxy_path.exists():
            return
        self.player.pause()
        self.loop_player.setSource(QUrl.fromLocalFile(str(self._proxy_path)))
        # 先置位再 play：让 play 触发的首个位置信号（0 → 入点）能正常显示，
        # 也让刚才那类「退出时的残留信号」被 _on_loop_position 的守卫拦下。
        self._loop_mode = True
        self.loop_player.play()
        self._set_status("循环预览中")

    def _exit_loop_mode(self) -> None:
        if not self._loop_mode:
            return
        self._loop_mode = False
        self.loop_player.stop()
        self._on_state(QMediaPlayer.PlaybackState.StoppedState)

    def _stop_all(self) -> None:
        self._loop_mode = False
        self.loop_player.stop()
        self.player.pause()

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("⏸ 暂停" if playing else "▶ 播放")

    def _on_error(self, error, message: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            self.selection_label.setText(f"播放出错：{message}（仍可按时输入裁切）")

    def _on_volume(self, value: int) -> None:
        level = value / 100.0
        self.audio.setVolume(level)
        self.loop_audio.setVolume(level)

    # ------------------------------------------------------------------ 操作

    def _toggle_play(self) -> None:
        # 任一侧在播 → 全部暂停（停在当前画面处）
        if (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                or self.loop_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState):
            self.player.pause()
            if self._loop_mode:
                # 循环播放在代理文件上进行，主播放器的位置还停在进入循环前（≈入点）。
                # 先按代理的当前位置换算回源时间轴并同步过去，指针才会停在当前画面处。
                start, _end = self.range_bar.selection()
                current_ms = start + self.loop_player.position()
                self._exit_loop_mode()
                self._seek_ms(current_ms)
            else:
                self._exit_loop_mode()
            return

        # 勾了「循环」就走代理原生循环（零 seek，不卡）
        if self.loop_check.isChecked():
            start, end = self.range_bar.selection()
            if end - start >= 200:
                if self._proxy_range == (start, end) and self._proxy_path and self._proxy_path.exists():
                    self._start_loop_playback()
                    return
                self._want_play = True
                self._render_proxy(start, end)
                return

        # 否则在源文件上普通播放（只在开始处 seek 一次）
        start, end = self.range_bar.selection()
        pos = self.player.position()
        if pos < start or pos >= end - 50:
            self.player.setPosition(start)
        self.player.play()

    def _nudge(self, delta_ms: int) -> None:
        self._seek_ms(self.player.position() + delta_ms)

    def _seek_ms(self, ms: int) -> None:
        ms = max(0, min(int(ms), self._duration_ms))
        self.range_bar.setPosition(ms)
        self.player.setPosition(ms)

    def _set_mark(self, which: str) -> None:
        pos = self.player.position()
        start, end = self.range_bar.selection()
        if which == "in":
            self.range_bar.setSelection(min(pos, end - 1), end)
            self.in_edit.setText(fmt_ms(min(pos, end - 1)))
        else:
            self.range_bar.setSelection(start, max(pos, start + 1))
            self.out_edit.setText(fmt_ms(max(pos, start + 1)))
        self._refresh_selection_text()

    def _on_in_edited(self) -> None:
        ms = parse_clock(self.in_edit.text())
        if ms is None:
            self.in_edit.setText(fmt_ms(self.range_bar.selection()[0]))
            return
        start, end = self.range_bar.selection()
        ms = min(ms, end - 1)
        self.range_bar.setSelection(ms, end)
        self.in_edit.setText(fmt_ms(ms))
        self._refresh_selection_text()

    def _on_out_edited(self) -> None:
        ms = parse_clock(self.out_edit.text())
        if ms is None:
            self.out_edit.setText(fmt_ms(self.range_bar.selection()[1]))
            return
        start, end = self.range_bar.selection()
        ms = max(ms, start + 1)
        self.range_bar.setSelection(start, ms)
        self.out_edit.setText(fmt_ms(ms))
        self._refresh_selection_text()

    def _refresh_selection_text(self) -> None:
        start, end = self.range_bar.selection()
        self.in_edit.setText(fmt_ms(start))
        self.out_edit.setText(fmt_ms(end))
        self.selection_label.setText(
            f"选中 {fmt_ms(start)} ~ {fmt_ms(end)}，共 {fmt_ms(end - start)}"
        )

    # ------------------------------------------------------------------ 结果

    def selection_seconds(self) -> tuple[float, float]:
        start, end = self.range_bar.selection()
        return start / 1000.0, end / 1000.0

    def closeEvent(self, event) -> None:
        self._cleanup()
        super().closeEvent(event)

    def accept(self) -> None:
        self._cleanup()
        super().accept()

    def _cleanup(self) -> None:
        """停播并清掉本次打开期间生成的预览代理，别在临时目录里堆文件。"""
        self.proxy_debounce.stop()
        self._stop_all()
        for path in self._proxy_files:
            try:
                path.unlink()
            except OSError:
                pass
        self._proxy_files.clear()


def can_preview(info: MediaInfo) -> bool:
    """能否用播放器预览：要有真实时长且不是图片/序列。"""
    if not info.ok or info.duration <= 0 or info.is_still_image:
        return False
    path = str(info.path)
    return "%" not in path and (info.has_video or info.has_audio)