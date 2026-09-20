"""从模板脚手架快速初始化一个探索型研究项目（通用配置框架内置）。

用法:
    python skills/exploration-project/bootstrap.py <目标目录> [--name <项目名>] [--pkg <包名>] [--git]

行为:
    1. 复制 skills/exploration-project/templates/ → 目标目录（须不存在或为空）
    2. 项目名: --name 显式指定；否则取目标目录名
    3. 包名: --pkg 显式指定；否则由项目名派生（小写 + 下划线，非法字符替换）
    4. 全文件文本替换: exploration_project → <包名>; exploration-project → <项目名>
    5. --git: 在目标目录 git init
    6. 打印下一步指引（uv sync → 冒烟 → pytest → 改配置/AGENTS.md → 首提交）

示例:
    python skills/exploration-project/bootstrap.py ../my-proj --name my-proj --git
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
TEMPLATES = SKILL_DIR / "templates"

# 占位字符串 → 替换目标（全文件文本替换；templates 内全部为文本文件）
PKG_PLACEHOLDER = "exploration_project"
NAME_PLACEHOLDER = "exploration-project"


def sanitize_pkg(name: str) -> str:
    """项目名 → 合法 Python 包名（小写 + 下划线；不能以数字开头）。"""
    s = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    if not s:
        s = "proj"
    if s[0].isdigit():
        s = "proj_" + s
    return s


def replace_in_text_files(root: Path, pkg: str, name: str) -> None:
    """全文件文本替换占位字符串（模板全部为文本文件）。"""
    for p in root.rglob("*"):
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # 非文本文件跳过
            new_text = text.replace(PKG_PLACEHOLDER, pkg).replace(
                NAME_PLACEHOLDER, name
            )
            if new_text != text:
                p.write_text(new_text, encoding="utf-8")


def bootstrap(target: str, name: str | None, pkg: str | None, do_git: bool) -> Path:
    dst = Path(target)
    if dst.exists() and any(dst.iterdir()):
        raise SystemExit(f"目标目录已存在且非空，拒绝覆盖: {dst}")
    if not TEMPLATES.is_dir():
        raise SystemExit(f"模板目录缺失: {TEMPLATES}")

    project_name = name if name else dst.name
    package_name = pkg if pkg else sanitize_pkg(project_name)

    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATES, dst, dirs_exist_ok=True)

    # 包目录重命名 + 全文件替换
    old_pkg_dir = dst / "src" / PKG_PLACEHOLDER
    new_pkg_dir = dst / "src" / package_name
    if old_pkg_dir.exists():
        old_pkg_dir.rename(new_pkg_dir)
    replace_in_text_files(dst, package_name, project_name)

    if do_git:
        subprocess.run(["git", "init"], cwd=dst, check=True)

    print(f"\n✅ 项目已初始化: {dst}")
    print(f"   项目名: {project_name}  |  包名: {package_name}")
    print("\n下一步:")
    print(f"  1. cd {dst}")
    print("  2. uv sync                          # 建 .venv + uv.lock")
    print("  3. .venv/bin/python scripts/example.py   # 冒烟：配置框架可用")
    print("  4. uv run pytest                    # 测试全过")
    print("  5. 按项目改 config/default.yaml、AGENTS.md")
    print(
        "  6. git add -A && git commit -m 'chore: init from exploration-project scaffold'"
    )
    return dst


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="目标目录（不存在或为空）")
    parser.add_argument("--name", default=None, help="项目名（默认取目标目录名）")
    parser.add_argument("--pkg", default=None, help="包名（默认由项目名派生）")
    parser.add_argument("--git", action="store_true", help="初始化后 git init")
    args = parser.parse_args()
    bootstrap(args.target, args.name, args.pkg, args.git)


if __name__ == "__main__":
    main()
