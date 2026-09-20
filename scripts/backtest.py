"""回测引擎 CLI：波动率目标 / 等权 / 简单均线 三种权重模式。

预注册方案: docs/infrastructure_plan.md M5/M6（2026-09-20 批准）
用法:
    .venv/bin/python scripts/backtest.py --weights vol|equal|sma20 [--sma N] [--out 名称]
产物:
    output/backtest_{out}_{mode}.csv          （净值/换手/分板块贡献）
    output/backtest_{out}_{mode}_metrics.json （指标 + 不变量）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 引导：仓库根下 src/ 可直接导入（无需安装）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import pandas as pd

from quant_futures import config as cfgmod
from quant_futures.cost import CostModel
from quant_futures.data import load_daily
from quant_futures.portfolio import (
    run_backtest,
    sector_contribution,
    vol_target_weights,
)


def load_adj_close_panel(cfg) -> pd.DataFrame:
    """加载品种池 adj_close 面板（2018 起，剔除行数不足的品种）。"""
    closes = {}
    for u in cfg.universe:
        try:
            df = load_daily(u["symbol"])
        except FileNotFoundError:
            continue
        closes[u["symbol"]] = df.set_index("date")["adj_close"]
    panel = pd.DataFrame(closes).sort_index()
    panel = panel[panel.index >= pd.Timestamp(cfg.backtest.start_date)]
    panel = panel.dropna(how="all")
    panel = panel.loc[:, panel.notna().sum() >= cfg.limits.min_n]
    return panel


def generate_weights(
    mode: str, closes: pd.DataFrame, cfg, sma: int = 20
) -> pd.DataFrame:
    """目标权重（信号用 ≤T-1 数据，T 日生效；上市前品种权重 0，按当日可交易品种归一化）。"""
    tradable = closes.notna()
    cnt = tradable.sum(axis=1).replace(0, np.nan)  # 当日可交易品种数
    if mode == "equal":
        w = tradable.div(cnt, axis=0).fillna(0.0)  # 等权多头（每日再平衡）
        return w
    if mode == "sma20":
        ma = closes.rolling(sma).mean()
        sign = np.where(closes > ma, 1.0, -1.0)
        w = pd.DataFrame(sign, index=closes.index, columns=closes.columns)
        w = w.where(tradable, 0.0).div(cnt, axis=0).fillna(0.0)
        return w.shift(1).fillna(0.0)  # T-1 决定 → T 生效
    if mode == "vol":
        returns = closes.pct_change()
        return vol_target_weights(returns, cfg)
    raise ValueError(f"未知权重模式: {mode}")


def run_mode(
    mode: str, cfg, sma: int = 20
) -> tuple[dict, pd.DataFrame, dict, pd.DataFrame]:
    """跑一种权重模式，返回 (metrics, nav_df, invariants, sector_df)。"""
    closes = load_adj_close_panel(cfg)
    returns = closes.pct_change()  # 保留 NaN（上市前），引擎内部处理
    target_w = generate_weights(mode, closes, cfg, sma=sma)
    res = run_backtest(returns, target_w, CostModel(cfg))
    sector_map = {u["symbol"]: u["sector"] for u in cfg.universe}
    sec = sector_contribution(returns, res.weights, sector_map)
    nav_df = pd.DataFrame(
        {
            "nav": res.nav,
            "gross_nav": res.gross_nav,
            "turnover": res.turnover,
        }
    )
    return res.metrics, nav_df, res.invariants, sec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    parser.add_argument("--weights", default="vol", choices=["vol", "equal", "sma20"])
    parser.add_argument("--sma", type=int, default=20)
    parser.add_argument(
        "--out", default=None, help="输出文件名前缀（默认取 --weights）"
    )
    args = parser.parse_args()

    cfg = cfgmod.load_config(args.config)
    name = args.out or args.weights
    metrics, nav_df, inv, sec = run_mode(args.weights, cfg, sma=args.sma)

    out = Path(cfg.paths.output)
    out.mkdir(parents=True, exist_ok=True)
    nav_df.to_csv(out / f"backtest_{name}_{args.weights}.csv")
    sec.to_csv(out / f"backtest_{name}_{args.weights}_sector.csv")
    with open(
        out / f"backtest_{name}_{args.weights}_metrics.json", "w", encoding="utf-8"
    ) as f:
        json.dump(
            {"metrics": metrics, "invariants": inv}, f, ensure_ascii=False, indent=2
        )

    print(f"模式: {args.weights}（sma={args.sma}）  回测天数: {len(nav_df)}")
    print("指标:")
    for k, v in metrics.items():
        print(f"  {k:20s} {v:.4f}" if isinstance(v, float) else f"  {k:20s} {v}")
    print("不变量:")
    for k, v in inv.items():
        print(f"  {k:24s} {v}")


if __name__ == "__main__":
    main()
