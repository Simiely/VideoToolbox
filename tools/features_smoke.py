"""新功能实测：时间裁切、分段导出、音视频合并（全部走真实 ffmpeg）。

用法：python tools/features_smoke.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from videotool.core.binaries import locate  # noqa: E402
from videotool.core.builder import build  # noqa: E402
from videotool.core.capabilities import detect  # noqa: E402
from videotool.core.naming import build_output_path  # noqa: E402
from videotool.core.probe import human_size, probe  # noqa: E402
from videotool.core.runner import run_plan  # noqa: E402
from videotool.core.specs import EncodeSpec, Mode  # noqa: E402

WORK = Path(tempfile.gettempdir()) / "vt_features"
WORK.mkdir(parents=True, exist_ok=True)

results: list[str] = []


def record(ok: bool, text: str) -> None:
    results.append(f"[{'OK ' if ok else 'FAIL'}] {text}")


def make_samples(toolchain) -> tuple[Path, Path, Path]:
    """10 秒彩色视频（带 440Hz 音）、10 秒不同音高的音频、图片序列。"""
    video = WORK / "合并 素材.mp4"
    audio = WORK / "配乐.mp3"
    seq_dir = WORK / "序列"
    seq_dir.mkdir(exist_ok=True)
    first = seq_dir / "frame_0001.png"

    if not video.exists():
        subprocess.run(
            [toolchain.ffmpeg, "-hide_banner", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(video)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
    if not audio.exists():
        subprocess.run(
            [toolchain.ffmpeg, "-hide_banner", "-y",
             "-f", "lavfi", "-i", "sine=frequency=880:duration=10",
             "-c:a", "libmp3lame", "-b:a", "192k", str(audio)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
    if not first.exists():
        subprocess.run(
            [toolchain.ffmpeg, "-hide_banner", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=5",
             str(seq_dir / "frame_%04d.png")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
    return video, audio, first


def duration_of(toolchain, path: Path) -> float:
    info = probe(toolchain, path)
    return info.duration


def main() -> int:
    toolchain = locate()
    caps = detect(toolchain)
    video, audio, first_frame = make_samples(toolchain)

    vinfo = probe(toolchain, video)
    print(f"素材：{video.name} {vinfo.duration:.2f}s / {audio.name} / 序列 {first_frame.name}")
    print()

    # ---- 1. 时间裁切：只导出 3.0s ~ 6.0s（应为 3 秒） ----
    out = build_output_path(video, WORK, ".mp4", "{name}_trim", avoid_conflict=True)
    spec = EncodeSpec(mode=Mode.VIDEO, target="mp4", video_encoder="libx264", crf=30,
                      preset_value="ultrafast", trim_start=3.0, trim_end=6.0)
    res = run_plan(build(spec, vinfo, toolchain, caps, out))
    if res.ok:
        dur = duration_of(toolchain, out)
        record(abs(dur - 3.0) < 0.35,
               f"时间裁切 3.0~6.0s -> 实际 {dur:.2f}s（{human_size(res.output_size)}）")
    else:
        record(False, f"时间裁切失败：{res.error}")

    # ---- 2. 只给起始（到结尾） ----
    out = build_output_path(video, WORK, ".mp4", "{name}_tail", avoid_conflict=True)
    spec = EncodeSpec(mode=Mode.VIDEO, target="mp4", video_encoder="libx264", crf=30,
                      preset_value="ultrafast", trim_start=7.0)
    res = run_plan(build(spec, vinfo, toolchain, caps, out))
    if res.ok:
        dur = duration_of(toolchain, out)
        record(abs(dur - 3.0) < 0.4, f"只填起始 7.0s 到结尾 -> 实际 {dur:.2f}s")
    else:
        record(False, f"只填起始失败：{res.error}")

    # ---- 3. 分段导出：3 段，文件名带 _part01 ----
    seg_specs = []
    for i, (start, end) in enumerate([(0.0, 3.333), (3.333, 6.667), (6.667, 10.0)], start=1):
        seg_specs.append((f"{i:02d}", EncodeSpec(
            mode=Mode.VIDEO, target="mp4", video_encoder="libx264", crf=30,
            preset_value="ultrafast", trim_start=start, trim_end=end)))

    ok_count = 0
    names = []
    for part, s in seg_specs:
        out = build_output_path(video, WORK, ".mp4", "{name}_seg", part=part)
        res = run_plan(build(s, vinfo, toolchain, caps, out))
        names.append(out.name)
        if res.ok:
            ok_count += 1
    record(ok_count == 3 and all("part" in n for n in names),
           f"分段导出 3 段全部成功，命名如 {names[1]}")
    durations = sorted(duration_of(toolchain, WORK / n) for n in names)
    record(abs(sum(durations) - 10.0) < 0.6,
           f"三段时长合计 {sum(durations):.2f}s（源 10.00s），各段 {[round(d, 2) for d in durations]}")

    # ---- 4. 图片序列取部分帧：从第 10 帧取 12 帧（10fps -> 1.2 秒） ----
    pattern = str(first_frame.with_name("frame_%04d.png"))
    info_seq = probe(toolchain, pattern)
    out = build_output_path(first_frame, WORK, ".mp4", "{name}_seqpart", avoid_conflict=True)
    spec = EncodeSpec(mode=Mode.IMAGE_SEQ, target="mp4", video_encoder="libx264", crf=30,
                      preset_value="ultrafast", fps=10.0, start_number=10, frame_count=12)
    plan = build(spec, info_seq, toolchain, caps, out)
    res = run_plan(plan)
    if res.ok:
        dur = duration_of(toolchain, out)
        record(abs(dur - 1.2) < 0.25,
               f"序列帧只取 12 帧（从第 10 帧）-> 实际 {dur:.2f}s，期望约 1.20s")
    else:
        record(False, f"序列取部分帧失败：{res.error}")

    # ---- 5. 音视频合并：视频直接复制 + 音频重编码 ----
    out = build_output_path(video, WORK, ".mp4", "{name}_mux", avoid_conflict=True)
    spec = EncodeSpec(mode=Mode.MERGE, target="mp4", merge_audio=str(audio),
                      merge_copy_video=True, merge_copy_audio=False,
                      audio_encoder="aac", audio_bitrate=192)
    started = time.monotonic()
    res = run_plan(build(spec, vinfo, toolchain, caps, out))
    elapsed = time.monotonic() - started
    if res.ok:
        merged = probe(toolchain, out)
        has_audio = merged.has_audio
        video_ok = merged.video is not None and merged.video.codec == vinfo.video.codec
        record(has_audio and video_ok,
               f"合并（视频复制+音频重编码）-> 视频轨 {merged.video.codec} 保持、"
               f"音频轨 {merged.audios[0].codec if has_audio else '无'}，耗时 {elapsed:.1f}s")
    else:
        record(False, f"合并失败：{res.error}")

    # ---- 6. 合并时保留原音轨（双音轨） ----
    out = build_output_path(video, WORK, ".mkv", "{name}_dual", avoid_conflict=True)
    spec = EncodeSpec(mode=Mode.MERGE, target="mkv", merge_audio=str(audio),
                      merge_audio_mode="keep", merge_copy_video=True, merge_copy_audio=True)
    res = run_plan(build(spec, vinfo, toolchain, caps, out))
    if res.ok:
        merged = probe(toolchain, out)
        record(len(merged.audios) == 2,
               f"保留原音轨合并 -> 音频轨 {len(merged.audios)} 条（期望 2）")
    else:
        record(False, f"双音轨合并失败：{res.error}")

    # ---- 7. 合并 + 裁切同时生效 ----
    out = build_output_path(video, WORK, ".mp4", "{name}_muxtrim", avoid_conflict=True)
    spec = EncodeSpec(mode=Mode.MERGE, target="mp4", merge_audio=str(audio),
                      merge_copy_video=True, merge_copy_audio=True,
                      trim_start=2.0, trim_end=5.0)
    res = run_plan(build(spec, vinfo, toolchain, caps, out))
    if res.ok:
        dur = duration_of(toolchain, out)
        record(abs(dur - 3.0) < 0.4, f"合并同时裁切 2~5s -> 实际 {dur:.2f}s")
    else:
        record(False, f"合并+裁切失败：{res.error}")

    print()
    for line in results:
        print(line)
    failures = sum(1 for line in results if line.startswith("[FAIL"))
    print()
    print("新功能实测通过" if failures == 0 else f"有 {failures} 项失败")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())