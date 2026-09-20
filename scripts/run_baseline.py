"""基准 A/B 对比报告：等权买入持有 vs 简单 20 日均线多空。

预注册方案: docs/infrastructure_plan.md M6（2026-09-20 批准）
用法:
    .venv/bin/python scripts/run_baseline.py [--config config/custom.yaml]
产物:
    output/baseline/compare.csv（基准 A/B 指标 + 不变量对比）
    逐日净值用 scripts/backtest.py --weights equal|sma20 另行生成
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 引导：仓库根下 src/ 与 scripts/ 可直接导入（import 模块而非 subprocess）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from quant_futures_01 import config as cfgmod
from backtest import run_mode  # 复用引擎逻辑（同仓库脚本 import 调用）


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)

    results = {}
    for mode in ("equal", "sma20"):
        metrics, _, inv, _ = run_mode(mode, cfg)
        results[mode] = {**metrics, **inv}
        print(f"\n=== 基准 {mode} ===")
        for k, v in metrics.items():
            print(f"  {k:20s} {v:.4f}" if isinstance(v, float) else f"  {k:20s} {v}")
        for k, v in inv.items():
            print(f"  {k:24s} {v}")

    out = Path(cfg.paths.output) / "baseline"
    out.mkdir(parents=True, exist_ok=True)
    compare = pd.DataFrame(results).T
    compare.to_csv(out / "compare.csv")
    print("\n对比输出: output/baseline/compare.csv")
    print(compare.round(4).to_string())


if __name__ == "__main__":
    main()
