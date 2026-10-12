"""输出文件名生成：命名模板 + 重名避让。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

DEFAULT_TEMPLATE = "{name}"
TEMPLATE_TOKENS: tuple[tuple[str, str], ...] = (
    ("{name}", "原文件名（不含扩展名）"),
    ("{ext}", "目标扩展名"),
    ("{index}", "任务序号，如 01"),
    ("{part}", "分段序号，如 01（分段导出时用）"),
    ("{date}", "日期，如 20261012"),
    ("{time}", "时间，如 143005"),
)


def render_name(src: str | Path, ext: str, template: str = DEFAULT_TEMPLATE,
                index: int = 1, part: str = "") -> str:
    stem = Path(src).stem or "output"
    now = datetime.now()
    values = {
        "name": stem,
        "ext": ext.lstrip("."),
        "index": f"{index:02d}",
        "part": part or "01",
        "date": now.strftime("%Y%m%d"),
        "time": now.strftime("%H%M%S"),
    }
    try:
        rendered = template.format(**values)
    except (KeyError, IndexError, ValueError):
        rendered = stem
    rendered = _INVALID.sub("_", rendered).strip().strip(".")
    # 模板里没写 {part} 时自动补后缀，避免多段导出互相覆盖
    if part and "{part}" not in template:
        rendered = f"{rendered}_part{part}"
    return rendered or stem


def build_output_path(src: str | Path, out_dir: str | Path, ext: str,
                      template: str = DEFAULT_TEMPLATE, index: int = 1,
                      avoid_conflict: bool = True, part: str = "") -> Path:
    """生成最终输出路径；重名时追加 _1、_2 …"""
    directory = Path(out_dir)
    name = render_name(src, ext, template, index, part)
    candidate = directory / f"{name}{ext}"
    if not avoid_conflict:
        return candidate

    counter = 1
    while candidate.exists():
        candidate = directory / f"{name}_{counter}{ext}"
        counter += 1
    return candidate