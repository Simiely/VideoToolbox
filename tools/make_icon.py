"""生成程序图标 build/icon.ico（一次即可，产物提交进仓库）。

用法：python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "icon.ico"

SIZE = 512
BG = (14, 99, 156, 255)      # #0e639c，与界面主色一致
FG = (255, 255, 255, 255)
HOLE = (16, 32, 46, 255)


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底板
    d.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=int(SIZE * 0.18), fill=BG)

    # 胶片齿孔：左右两列
    pad = int(SIZE * 0.06)
    hole_w = int(SIZE * 0.075)
    hole_h = int(SIZE * 0.055)
    gap = int(SIZE * 0.035)
    y = pad
    while y + hole_h < SIZE - pad:
        d.rounded_rectangle([pad, y, pad + hole_w, y + hole_h],
                            radius=hole_h // 3, fill=HOLE)
        d.rounded_rectangle([SIZE - pad - hole_w, y, SIZE - pad, y + hole_h],
                            radius=hole_h // 3, fill=HOLE)
        y += hole_h + gap

    # 中央播放三角
    cx, cy = SIZE / 2, SIZE / 2
    r = SIZE * 0.21
    d.polygon(
        [(cx - r * 0.75, cy - r), (cx - r * 0.75, cy + r), (cx + r * 0.95, cy)],
        fill=FG,
    )
    return img


def main() -> int:
    img = draw()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, format="ICO",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"已生成 {OUT}（{OUT.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())