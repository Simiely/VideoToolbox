"""引擎冒烟测试：用真实 ffmpeg 跑通主要转换路径。

放在 tools/ 下，仅用于开发验证，不参与打包。
用法：python tools/smoke.py
"""

from __future__ import annotations

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
from videotool.core.queue import TaskQueue  # noqa: E402
from videotool.core.runner import run_plan  # noqa: E402
from videotool.core.specs import EncodeSpec, Mode, QualityMode  # noqa: E402

WORK = Path(tempfile.gettempdir()) / "vt_smoke"
WORK.mkdir(parents=True, exist_ok=True)


def make_samples(toolchain) -> tuple[Path, Path]:
    video = WORK / "样例 视频.mp4"
    image = WORK / "样例 图片.png"
    if not video.exists():
        rc = __import__("subprocess").run([
            toolchain.ffmpeg, "-hide_banner", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(video),
        ]).returncode
        assert rc == 0, "生成测试视频失败"
    if not image.exists():
        rc = __import__("subprocess").run([
            toolchain.ffmpeg, "-hide_banner", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=800x600", "-frames:v", "1",
            str(image),
        ]).returncode
        assert rc == 0, "生成测试图片失败"
    return video, image


CASES = [
    ("视频→MP4 (x264 CRF)", Mode.VIDEO, "mp4", EncodeSpec(mode=Mode.VIDEO, target="mp4", video_encoder="libx264", crf=26)),
    ("视频→MKV (x265 CRF)", Mode.VIDEO, "mkv", EncodeSpec(mode=Mode.VIDEO, target="mkv", video_encoder="libx265", crf=30)),
    ("视频→WebM (VP9)", Mode.VIDEO, "webm", EncodeSpec(mode=Mode.VIDEO, target="webm", video_encoder="libvpx-vp9", crf=40)),
    ("视频→MP4 (目标体积 1MB 两遍)", Mode.VIDEO, "mp4", EncodeSpec(mode=Mode.VIDEO, target="mp4", video_encoder="libx264", quality_mode=QualityMode.TARGET_SIZE, target_size_mb=1.0, scale_width=480, preset_value="veryfast")),
    ("视频→GIF", Mode.GIF, "gif", EncodeSpec(mode=Mode.GIF, target="gif", anim_fps=12, anim_width=320)),
    ("视频→动图WebP", Mode.WEBP_ANIM, "webp", EncodeSpec(mode=Mode.WEBP_ANIM, target="webp_anim", anim_fps=12, anim_width=320, anim_quality=70)),
    ("视频→APNG", Mode.APNG, "png", EncodeSpec(mode=Mode.APNG, target="apng", anim_fps=10, anim_width=320)),
    ("视频→MP3", Mode.AUDIO, "mp3", EncodeSpec(mode=Mode.AUDIO, target="mp3", audio_bitrate=192)),
    ("视频→抽帧 JPG", Mode.IMAGE, "jpg", EncodeSpec(mode=Mode.IMAGE, target="jpg", image_time=2.0, image_quality=90)),
    ("视频→FLAC", Mode.AUDIO, "flac", EncodeSpec(mode=Mode.AUDIO, target="flac")),
]


def main() -> int:
    toolchain = locate()
    if toolchain is None:
        print("找不到 ffmpeg/ffprobe")
        return 1
    print(f"工具链：{toolchain.source} {toolchain.version}")
    print(f"ffmpeg: {toolchain.ffmpeg}")

    t0 = time.time()
    caps = detect(toolchain)
    print(f"能力探测耗时 {time.time() - t0:.1f}s，硬件编码器：{caps.hardware or '无'}")
    if caps.hw_failed:
        print(f"列表有但试编码失败：{caps.hw_failed}")

    video, image = make_samples(toolchain)
    info = probe(toolchain, video)
    print(f"样例视频：{info.summary()} {info.size_label}，时长 {info.duration:.2f}s")

    failures = 0
    for title, mode, ext, spec in CASES:
        out = build_output_path(video, WORK, f".{ext}", "{name}", avoid_conflict=True)
        plan = build(spec, info, toolchain, caps, out)
        result = run_plan(plan)
        status = "OK " if result.ok else "FAIL"
        if not result.ok:
            failures += 1
        size = human_size(result.output_size) if result.ok else "-"
        print(f"[{status}] {title:28s} -> {out.name} ({size})")
        if not result.ok:
            print(f"       错误：{result.error}")
            for line in result.log[-6:]:
                print(f"       {line}")

    # Pillow 图片路径
    img_info = probe(toolchain, image)
    for ext in ("jpg", "webp", "bmp", "tiff"):
        out = build_output_path(image, WORK, f".{ext}", "{name}", avoid_conflict=True)
        spec = EncodeSpec(mode=Mode.IMAGE, target=ext, image_quality=88, image_scale_width=400)
        result = run_plan(build(spec, img_info, toolchain, caps, out))
        status = "OK " if result.ok else "FAIL"
        if not result.ok:
            failures += 1
        print(f"[{status}] 图片→{ext.upper():5s} (Pillow)            -> {out.name} ({human_size(result.output_size) if result.ok else '-'})")

    # 队列并发测试
    q = TaskQueue(toolchain, caps, max_workers=2)
    for i in range(3):
        q.add(video, EncodeSpec(mode=Mode.VIDEO, target="mp4", video_encoder="libx264", crf=30,
                                scale_width=320, preset_value="ultrafast"))
    q.output_provider = lambda task: build_output_path(task.source, WORK, ".mp4", "{name}_q", avoid_conflict=True)
    q.start()
    deadline = time.time() + 120
    while q.active() and time.time() < deadline:
        time.sleep(0.2)
    counts = q.counts()
    print(f"队列结果：完成 {counts['done']} / 失败 {counts['failed']} / 取消 {counts['cancelled']}")
    failures += counts["failed"]
    q.shutdown()

    print()
    print("全部通过" if failures == 0 else f"有 {failures} 项失败")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())