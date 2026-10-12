"""架构自检：直接导入层面的约束（AST 精确检查，零第三方依赖）。

import-linter 的 forbidden 契约会连带检查间接导入，无法表达
「ui 不得【直接】导入 subprocess」这类仅针对直接导入的约束
（ui 经由 core 合法地使用子进程是允许的）。
这里作为补充闸门，检查：
  1. src/videotool/ui 下任何模块不得直接 import subprocess；
  2. src/videotool/core 下任何模块不得直接 import PySide6。

用法：python tools/arch_check.py
退出码 0 表示通过，1 表示发现违规。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "videotool"

# (被检查目录, 禁止直接导入的顶层模块名, 约束说明)
RULES: tuple[tuple[Path, tuple[str, ...], str], ...] = (
    (SRC / "ui", ("subprocess",), "界面层不得直接调用 subprocess（子进程统一由 core 负责）"),
    (SRC / "core", ("PySide6",), "核心层不得依赖 PySide6（核心可脱离 Qt 运行）"),
)


def direct_imports(path: Path) -> set[str]:
    """返回文件里直接导入的顶层模块名集合（含 TYPE_CHECKING 块内的导入，从严检查）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def main() -> int:
    problems: list[str] = []
    checked = 0
    for folder, forbidden, label in RULES:
        for path in sorted(folder.glob("*.py")):
            checked += 1
            for name in sorted(direct_imports(path) & set(forbidden)):
                problems.append(f"{path.relative_to(ROOT)} 直接导入了 {name} —— {label}")

    if problems:
        print("架构自检未通过：")
        for line in problems:
            print(f"  - {line}")
        return 1

    print(f"架构自检通过：检查 {checked} 个文件，直接导入约束均满足。")
    return 0


if __name__ == "__main__":
    sys.exit(main())