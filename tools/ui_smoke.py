"""界面冒烟测试：离屏创建主窗口，走完整「添加 -> 参数 -> 开始 -> 完成」流程。

用法：python tools/ui_smoke.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402
from PySide6.QtWidgets import QApplication, QTextBrowser  # noqa: E402

from videotool.core.binaries import locate  # noqa: E402
from videotool.core.queue import TaskStatus  # noqa: E402
from videotool.core.specs import FPS_PRESETS, RESOLUTION_PRESETS, Mode, QualityMode  # noqa: E402
from videotool.ui.main_window import MainWindow  # noqa: E402
from videotool.ui.task_table import PROGRESS_COLUMN  # noqa: E402
from videotool.ui.theme import apply_theme  # noqa: E402

WORK = Path(tempfile.gettempdir()) / "vt_ui_smoke"
WORK.mkdir(parents=True, exist_ok=True)


def pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_until(app, predicate, timeout: float, label: str) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    print(f"超时等待：{label}")
    return False


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)

    toolchain = locate()
    if toolchain is None:
        print("找不到 ffmpeg")
        return 1

    sample = WORK / "界面 测试 视频.mp4"
    if not sample.exists():
        subprocess.run([
            toolchain.ffmpeg, "-hide_banner", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=480x270:rate=24:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(sample),
        ], check=True)

    window = MainWindow(toolchain)
    window.show()
    pump(app, 0.3)

    ok = wait_until(app, lambda: bool(window.caps.encoders), timeout=60, label="能力探测")
    print(f"能力探测完成：硬件={window.caps.hardware or '无'}，编码器 {len(window.caps.encoders)} 个")
    if not ok:
        return 2

    results: list[str] = []

    def press_start(name: str) -> bool:
        """按真实用户路径启动：检查按钮状态 -> 点按钮。
        早期版本这里直接调 _start()，绕过了按钮的可用性判断，
        结果「开始转换按钮点不动」这种 bug 测试完全发现不了。"""
        pump(app, 0.3)
        if not window.start_btn.isEnabled():
            counts = window.queue.counts()
            results.append(
                f"[FAIL] {name}: 添加后「开始转换」按钮不可用"
                f"（waiting={counts['waiting']} running={counts['running']}）"
            )
            return False
        window.start_btn.click()
        pump(app, 0.4)
        if window.queue.counts()["waiting"] and not window.queue.active():
            results.append(f"[FAIL] {name}: 点击后队列没有启动")
            return False
        return True

    def run_case(name: str, target_index: int, expect_ext: str) -> bool:
        window.panel.target_combo.setCurrentIndex(target_index)
        pump(app, 0.1)
        window._add_paths([str(sample)])
        if not press_start(name):
            return False
        done = wait_until(app, lambda: not window.queue.active(), timeout=180, label=name)
        tasks = window.queue.ordered()
        task = tasks[-1] if tasks else None
        if not done or task is None:
            results.append(f"[FAIL] {name}: 未完成")
            return False
        if task.status != TaskStatus.DONE:
            results.append(f"[FAIL] {name}: {task.status.label} / {task.error}")
            return False
        exists = bool(task.output and task.output.exists())
        ext_ok = task.output.suffix.lower() == expect_ext
        results.append(
            f"[{'OK ' if exists and ext_ok else 'FAIL'}] {name} -> {task.output.name} "
            f"({task.output.stat().st_size if exists else 0} bytes, {task.speed})"
        )
        return exists and ext_ok

    # 先单独验证按钮状态机（曾出过「按钮永远点不动」的 bug）
    def check_start_button_states() -> None:
        empty = window.start_btn.isEnabled()
        window._add_paths([str(sample)])
        pump(app, 0.4)
        after_add = window.start_btn.isEnabled()
        window.start_btn.click()
        task = window.queue.ordered()[-1]
        wait_until(app, lambda: not window.queue.active(), timeout=180, label="按钮状态机")
        pump(app, 0.3)
        after_done = window.start_btn.isEnabled()
        # 用"任务真的完成了"来证明这次点击确实启动了转换（比抢时间读状态可靠）
        started = task.status == TaskStatus.DONE
        ok = (not empty) and after_add and started and (not after_done)
        results.append(
            f"[{'OK ' if ok else 'FAIL'}] 按钮状态机 空表={empty} 添加后={after_add} "
            f"点击后任务完成={started} 完成后按钮={after_done}（期望 False/True/True/False）"
        )

    check_start_button_states()

    run_case("界面 MP4 转码", 0, ".mp4")       # 视频 · MP4
    run_case("界面 GIF 动图", 18, ".gif")      # 动图 · GIF
    run_case("界面 动图 WebP", 19, ".webp")    # 动图 · WebP
    run_case("界面 提取 MP3", 7, ".mp3")       # 音频 · MP3
    run_case("界面 图片 JPG", 13, ".jpg")      # 图片 · JPG

    # 界面上切到目标体积模式再跑一次，验证参数联动
    window.panel.target_combo.setCurrentIndex(0)
    pump(app, 0.1)
    window.panel.quality_combo.setCurrentIndex(2)
    pump(app, 0.1)
    window.panel.quality_value.setValue(1.0)
    pump(app, 0.1)
    window._add_paths([str(sample)])
    window.start_btn.click()
    ok_target = wait_until(app, lambda: not window.queue.active(), timeout=180, label="目标体积")
    task = window.queue.ordered()[-1]
    if ok_target and task.status == TaskStatus.DONE:
        size_mb = task.output.stat().st_size / 1024 / 1024
        results.append(f"[OK ] 界面 目标体积 1MB -> {size_mb:.2f} MB")
    else:
        results.append(f"[FAIL] 界面 目标体积：{task.status.label} / {task.error}")

    # 取消功能：用 VP9 最慢预设保证编码足够久，来得及取消
    window.panel.target_combo.setCurrentIndex(3)   # 视频 · WebM
    pump(app, 0.1)
    window.panel.preset_combo.setCurrentIndex(window.panel.preset_combo.count() - 1)
    pump(app, 0.1)
    window._add_paths([str(sample)])
    window.start_btn.click()
    pump(app, 0.6)
    window.queue.cancel_all()
    wait_until(app, lambda: not window.queue.active(), timeout=60, label="取消")
    task = window.queue.ordered()[-1]
    results.append(f"[{'OK ' if task.status == TaskStatus.CANCELLED else 'FAIL'}] 界面 取消任务 -> {task.status.label}")

    # 「查看参数」：探测 + 参数窗口构建（窗口用构造而非 exec，避免阻塞测试）
    from videotool.core.probe import probe as probe_one, text_report
    from videotool.ui.info_dialog import MediaInfoDialog

    info_one = probe_one(window.toolchain, sample)
    dialog = MediaInfoDialog(info_one, window)
    plain = dialog.browser.toPlainText()
    dialog.copy_btn.click()
    clip = app.clipboard().text()
    need = ("概要", "视频", "音频", "分辨率", "帧率")
    missing = [t for t in need if t not in plain]
    clip_ok = "【视频】" in clip and "分辨率" in clip
    dialog.close()
    ok_info = info_one.ok and not missing and clip_ok
    results.append(
        f"[{'OK ' if ok_info else 'FAIL'}] 界面 查看参数 -> "
        f"分区{'齐全' if not missing else '缺少' + str(missing)}，"
        f"复制文本 {len(clip)} 字符，报告 {len(text_report(info_one).splitlines())} 行"
    )

    # 编码器说明：帮助文案外置到 ui/help_text.py 后仍要完整渲染
    help_dialog = window.panel.help_dialog()
    help_browser = help_dialog.findChild(QTextBrowser)
    help_text = help_browser.toPlainText() if help_browser is not None else ""
    help_dialog.close()
    help_need = ("结论：追求质量就保持默认", "本机可用的硬件编码器",
                 "质量控制怎么选", "这些地方跟编码器无关")
    help_missing = [t for t in help_need if t not in help_text]
    # 「说明」按钮必须在面板可视区内：编码器名很长时曾被撑出边界，要横向滚动才看得到
    help_btn = window.panel.encoder_help_btn
    btn_right = help_btn.mapTo(window.panel.viewport(), help_btn.rect().topLeft()).x() + help_btn.width()
    btn_visible = help_btn.isVisible() and btn_right <= window.panel.viewport().width()
    results.append(
        f"[{'OK ' if not help_missing and btn_visible else 'FAIL'}] 界面 编码器说明 -> "
        f"渲染 {len(help_text)} 字符，关键文案{'齐全' if not help_missing else '缺少' + str(help_missing)}，"
        f"「说明」按钮{'在可视区内' if btn_visible else '被挤出可视区'}"
    )

    # 参数映射（ui/spec_form.py）：面板设成非默认值，核对读出的 spec 字段
    def check_spec(name: str, index: int, set_inputs, expect: dict) -> None:
        window.panel.target_combo.setCurrentIndex(index)
        pump(app, 0.1)
        set_inputs()
        pump(app, 0.2)
        spec = window.panel.current_spec()
        bad = [f"{key}={getattr(spec, key)!r}≠{value!r}"
               for key, value in expect.items() if getattr(spec, key) != value]
        results.append(
            f"[{'OK ' if not bad else 'FAIL'}] 参数映射 {name} -> "
            + ("字段全部一致" if not bad else "；".join(bad))
        )

    def set_video_inputs() -> None:
        window.panel.quality_combo.setCurrentIndex(0)        # 恒定质量
        window.panel.quality_value.setValue(17)
        window.panel.resolution_combo.setCurrentIndex(1)
        window.panel.fps_combo.setCurrentIndex(1)
        window.panel.trim_start_edit.setText("1")
        window.panel.trim_end_edit.setText("2")
        window.panel.audio_bitrate.setValue(128)

    check_spec("视频 MP4", 0, set_video_inputs, {
        "mode": Mode.VIDEO, "quality_mode": QualityMode.CRF, "crf": 17,
        "scale_width": RESOLUTION_PRESETS[1][1], "fps": FPS_PRESETS[1][1],
        "trim_start": 1.0, "trim_end": 2.0, "audio_bitrate": 128,
    })

    def set_audio_inputs() -> None:
        window.panel.audio_bitrate.setValue(96)

    check_spec("音频 MP3", 7, set_audio_inputs, {
        "mode": Mode.AUDIO, "audio_bitrate": 96,
    })

    def set_image_inputs() -> None:
        window.panel.image_quality.setValue(88)
        window.panel.image_width.setValue(640)
        window.panel.image_time.setValue(2.5)

    check_spec("图片 JPG", 13, set_image_inputs, {
        "mode": Mode.IMAGE, "image_quality": 88,
        "image_scale_width": 640, "image_time": 2.5,
    })

    def set_gif_inputs() -> None:
        window.panel.anim_fps.setValue(12)
        window.panel.anim_width.setValue(320)
        window.panel.anim_loop_combo.setCurrentIndex(2)      # 播放 3 次
        window.panel.anim_colors.setValue(64)
        window.panel.anim_dither_combo.setCurrentIndex(1)    # Sierra2

    check_spec("动图 GIF", 18, set_gif_inputs, {
        "mode": Mode.GIF, "anim_fps": 12.0, "anim_width": 320,
        "anim_loop": 3, "anim_max_colors": 64, "anim_dither": "sierra2_4a",
    })

    def set_webp_inputs() -> None:
        window.panel.anim_quality.setValue(60)
        window.panel.anim_lossless.setChecked(True)

    check_spec("动图 WebP", 19, set_webp_inputs, {
        "mode": Mode.WEBP_ANIM, "anim_quality": 60, "anim_lossless": True,
    })

    def set_seq_inputs() -> None:
        window.panel.seq_start_spin.setValue(2)
        window.panel.seq_count_spin.setValue(5)
        window.panel.fps_combo.setCurrentIndex(1)

    check_spec("图片序列", 21, set_seq_inputs, {
        "mode": Mode.IMAGE_SEQ, "start_number": 2, "frame_count": 5,
        "fps": float(FPS_PRESETS[1][1]), "audio_mode": "none",
    })

    # 映射核对完把面板恢复默认（分辨率/帧率/裁切），避免拖慢后面的真实转码用例
    window.panel.target_combo.setCurrentIndex(0)
    window.panel.resolution_combo.setCurrentIndex(0)
    window.panel.fps_combo.setCurrentIndex(0)
    window.panel.trim_start_edit.setText("")
    window.panel.trim_end_edit.setText("")
    pump(app, 0.2)

    # 时间裁切：面板输入 -> spec -> 真实转码时长
    window.panel.target_combo.setCurrentIndex(0)
    pump(app, 0.1)
    window.panel.trim_start_edit.setText("0:01")
    window.panel.trim_end_edit.setText("0:02")
    pump(app, 0.2)
    spec_trim = window.panel.current_spec()
    parse_ok = abs((spec_trim.trim_start or -1) - 1.0) < 1e-6 and abs((spec_trim.trim_end or -1) - 2.0) < 1e-6
    results.append(
        f"[{'OK ' if parse_ok else 'FAIL'}] 界面 时间输入解析 -> "
        f"0:01 与 0:02 解析为 {spec_trim.trim_start} / {spec_trim.trim_end} 秒"
    )

    window._add_paths([str(sample)])
    window.start_btn.click()
    wait_until(app, lambda: not window.queue.active(), timeout=180, label="裁切转码")
    trim_task = window.queue.ordered()[-1]
    if trim_task.status == TaskStatus.DONE and trim_task.output:
        trimmed = probe_one(window.toolchain, trim_task.output)
        len_ok = abs(trimmed.duration - 1.0) < 0.35
        results.append(
            f"[{'OK ' if len_ok else 'FAIL'}] 界面 时间裁切转码 -> "
            f"输出时长 {trimmed.duration:.2f}s（期望约 1.00s）"
        )
    else:
        results.append(f"[FAIL] 界面 时间裁切转码：{trim_task.status.label} / {trim_task.error}")

    # 分段任务：命名带 part 后缀，且参数不被面板覆盖
    window._add_paths([str(sample)])
    seg_task = window.queue.ordered()[-1]
    seg_task.part_label = "02"
    seg_task.spec = window.panel.current_spec().copy_with(trim_start=2.0, trim_end=4.0)
    seg_task.spec_locked = True
    window.panel.trim_start_edit.setText("")
    window.panel.trim_end_edit.setText("")
    pump(app, 0.2)
    locked_kept = (seg_task.spec.trim_start == 2.0 and seg_task.spec.trim_end == 4.0)
    out_path = window.tasks.output_for(seg_task)
    name_ok = "part02" in out_path.name
    results.append(
        f"[{'OK ' if locked_kept and name_ok else 'FAIL'}] 界面 分段任务 -> "
        f"面板改动后区间{'保留' if locked_kept else '被覆盖'}，输出名 {out_path.name}"
    )
    window.tasks.remove(seg_task.id)
    window._update_actions()

    # 锁定任务不被「开始转换」覆盖：选段裁切/合并任务的参数由对话框定好。
    # 曾出过 bug——点开始时把面板 spec 无条件写进所有等待任务，裁切区间被冲掉。
    locked_spec = window.panel.current_spec().copy_with(trim_start=1.0, trim_end=2.0)
    locked_task = window.tasks.add_single(sample, locked_spec)
    locked_task.info = probe_one(window.toolchain, sample)
    pump(app, 0.2)
    window.start_btn.click()
    wait_until(app, lambda: not window.queue.active(), timeout=180, label="锁定任务")
    kept = locked_task.spec.trim_start == 1.0 and locked_task.spec.trim_end == 2.0
    out_seconds = 0.0
    if locked_task.status == TaskStatus.DONE and locked_task.output:
        out_seconds = probe_one(window.toolchain, locked_task.output).duration
    locked_ok = kept and locked_task.status == TaskStatus.DONE and abs(out_seconds - 1.0) < 0.35
    results.append(
        f"[{'OK ' if locked_ok else 'FAIL'}] 界面 锁定任务参数 -> "
        f"点开始后区间{'保留' if kept else '被覆盖'}，输出时长 {out_seconds:.2f}s（期望约 1.00s）"
    )

    # 多选去重：selectedIndexes 会为选中行的每一列各返回一个 index，
    # 早期实现直接遍历它逐一处理，同一个任务会被重复操作。
    before_rows = window.model.rowCount()
    before_tasks = len(window.queue.tasks)
    window._add_paths([str(sample), str(sample)])
    pump(app, 0.4)
    model = window.table.model()
    selection = window.table.selectionModel()
    selection.clearSelection()
    for row in (before_rows, before_rows + 1):
        selection.select(model.index(row, 0),
                         QItemSelectionModel.Select | QItemSelectionModel.Rows)
    pump(app, 0.2)
    picked = window._selected_tasks()
    picked_rows = window._selected_rows()
    unique_ok = (len(picked) == 2 and len({t.id for t in picked}) == 2
                 and picked_rows == [before_rows, before_rows + 1])
    window._remove_selected()
    pump(app, 0.2)
    removed_ok = (len(window.queue.tasks) == before_tasks
                  and window.model.rowCount() == before_rows)
    results.append(
        f"[{'OK ' if unique_ok and removed_ok else 'FAIL'}] 界面 多选去重 -> "
        f"选中 2 行得到 {len(picked)} 个任务（未去重会是 14），行号 {picked_rows}；"
        f"移除后剩余 {len(window.queue.tasks)} 任务 / {window.model.rowCount()} 行"
    )

    # 表格模型一致性：行数、进度条 widget、移除后行号映射与 widget 同步
    window._add_paths([str(sample)])
    pump(app, 0.4)
    rows = window.model.rowCount()
    bars = sum(1 for r in range(rows)
               if window.table.indexWidget(window.model.index(r, PROGRESS_COLUMN)) is not None)
    last_task = window.model.task_at(rows - 1)
    texts_ok = (last_task is not None
                and window.model.index(rows - 1, 0).data() == last_task.name
                and window.model.index(rows - 1, 4).data() == last_task.status.label)
    selection = window.table.selectionModel()
    selection.clearSelection()
    selection.select(window.model.index(rows - 1, 0),
                     QItemSelectionModel.Select | QItemSelectionModel.Rows)
    picked_before = [t.id for t in window._selected_tasks()]
    window._remove_selected()
    pump(app, 0.2)
    rows_after = window.model.rowCount()
    removed_ok = (rows_after == rows - 1
                  and window.model.row_of(last_task.id) == -1
                  and last_task.id in picked_before)
    bars_after = sum(1 for r in range(rows_after)
                     if window.table.indexWidget(window.model.index(r, PROGRESS_COLUMN)) is not None)
    model_ok = bars == rows and texts_ok and removed_ok and bars_after == rows_after
    results.append(
        f"[{'OK ' if model_ok else 'FAIL'}] 表格模型一致性 -> {rows} 行/{bars} 个进度条，"
        f"移除后 {rows_after} 行/{bars_after} 个进度条，"
        f"行号映射{'已清理' if removed_ok else '未清理'}"
    )

    # 选段裁切窗口：播放器读时长 + 时间轴拖出区间 + 快捷键设起止
    from videotool.ui.trim_dialog import TrimDialog, can_preview, fmt_ms, parse_clock

    trim_info = probe_one(window.toolchain, sample)
    dialog_ok = can_preview(trim_info)
    tdlg = TrimDialog(trim_info, window.toolchain, window)
    end_t = time.time() + 12
    while time.time() < end_t and tdlg.player.duration() <= 0:
        app.processEvents()
        time.sleep(0.02)
    dur_ms = tdlg.player.duration()

    # 拖时间轴圈选 1.0s ~ 2.5s
    tdlg.range_bar.setSelection(1000, 2500)
    tdlg._refresh_selection_text()
    sel_start, sel_end = tdlg.selection_seconds()

    # 播放头移到 1.8s 后按「设为起点」
    tdlg._seek_ms(1800)
    tdlg._set_mark("in")
    mark_start, mark_end = tdlg.selection_seconds()

    clock_ok = parse_clock("0:02.50") == 2500 and parse_clock("1:02") == 62000
    trim_ui_ok = (
        dialog_ok and dur_ms > 0
        and abs(sel_start - 1.0) < 0.01 and abs(sel_end - 2.5) < 0.01
        and abs(mark_start - 1.8) < 0.05 and abs(mark_end - 2.5) < 0.01
        and clock_ok
    )
    results.append(
        f"[{'OK ' if trim_ui_ok else 'FAIL'}] 界面 选段裁切窗口 -> "
        f"可预览={dialog_ok} 播放器时长={dur_ms}ms 拖选={sel_start:.2f}~{sel_end:.2f}s "
        f"设起点后={mark_start:.2f}s 显示={fmt_ms(1800)}"
    )
    tdlg.close()

    # 循环预览流畅度：曾经「第一次正常、一循环就卡」。
    # 素材必须是多关键帧的长视频，否则 seek 便宜得测不出问题。
    big = WORK / "循环压测_长gop_1080p.mp4"
    if not big.exists():
        subprocess.run(
            [toolchain.ffmpeg, "-hide_banner", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=60",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-g", "250", "-keyint_min", "250", "-sc_threshold", "0", str(big)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
    big_info = probe_one(window.toolchain, big)
    ldlg = TrimDialog(big_info, window.toolchain, window)
    end_l = time.time() + 15
    while time.time() < end_l and ldlg.player.duration() <= 0:
        app.processEvents()
        time.sleep(0.02)

    frame_times: list[float] = []
    ldlg.loop_sink.videoFrameChanged.connect(lambda f: frame_times.append(time.monotonic()))
    # 入点故意选在关键帧之间（关键帧在 0 / 8.33 / 16.67 秒）
    ldlg.range_bar.setSelection(12000, 14000)
    ldlg._refresh_selection_text()
    ldlg._toggle_play()          # 应触发：生成预览代理 → 原生循环播放
    started_play = wait_until(
        app,
        lambda: ldlg._loop_mode
        and ldlg.loop_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
        timeout=40,
        label="等待循环预览就绪",
    )

    frames_before = len(frame_times)
    started = time.monotonic()
    while time.monotonic() - started < 8.0:
        app.processEvents()
        time.sleep(0.005)
    elapsed = time.monotonic() - started

    # 暂停应停在当前画面处，而不是跳回入点（入点是 12000ms）——
    # 循环播放在代理上进行，旧实现暂停后指针会回到进入循环前的旧位置。
    pause_before = ldlg.range_bar.position()
    ldlg._toggle_play()          # 暂停
    pump(app, 0.4)
    paused_at = ldlg.range_bar.position()
    pause_ok = abs(paused_at - pause_before) < 800 and paused_at > 11800
    results.append(
        f"[{'OK ' if pause_ok else 'FAIL'}] 界面 循环预览暂停位置 -> "
        f"暂停前 {pause_before}ms，暂停后停在 {paused_at}ms（入点 12000ms，不回跳）"
    )

    ldlg._cleanup()
    ldlg.close()

    loop_frames = frame_times[frames_before:]
    fps = len(loop_frames) / elapsed if elapsed else 0.0
    # 帧间隔的最大值 = 是否卡顿的直接指标。旧实现每次回跳冻结约 143ms。
    intervals = [b - a for a, b in zip(loop_frames, loop_frames[1:])][2:]
    worst = max(intervals) * 1000 if intervals else 9999
    smooth_ok = started_play and fps >= 25.0 and worst < 80.0 and ldlg.proxy_render_count >= 1
    results.append(
        f"[{'OK ' if smooth_ok else 'FAIL'}] 循环预览流畅度 -> 1080p 长GOP 循环 2 秒区间 {elapsed:.1f}s，"
        f"平均 {fps:.1f} fps（源 30fps），最大帧间隔 {worst:.0f} ms（旧实现约 143ms），"
        f"生成代理 {ldlg.proxy_render_count} 次"
    )

    window.close()
    pump(app, 0.2)

    print()
    for line in results:
        print(line)
    failures = sum(1 for line in results if line.startswith("[FAIL"))
    print()
    print("界面冒烟通过" if failures == 0 else f"界面冒烟有 {failures} 项失败")
    return 0 if failures == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())