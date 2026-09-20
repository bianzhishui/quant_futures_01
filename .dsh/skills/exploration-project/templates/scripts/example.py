"""示例脚本：展示配置框架用法（冒烟：.venv/bin/python scripts/example.py）。

规范：逻辑放函数、main() 只做 argparse 壳；模块级不读 sys.argv（只做 sys.path 引导）。
"""

import argparse
import sys
from pathlib import Path

# 引导：把仓库根加入 sys.path，使 src/ 下的包可直接导入（无需安装）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from exploration_project import config


def main() -> None:
    parser = argparse.ArgumentParser()
    config.add_config_arg(parser)
    args = parser.parse_args()
    cfg = config.load_config(args.config)
    print(f"params.window = {cfg.params.window}")
    print(f"paths.output = {cfg.paths.output}")


if __name__ == "__main__":
    main()
