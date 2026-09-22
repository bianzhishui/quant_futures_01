"""样本外扩展复验：W1(2010-2017) vs W2(2018-2026) 关键结论。

预注册方案: docs/outsample_plan.md（2026-09-21 批准）
用法:
    .venv/bin/python scripts/run_outsample.py [--config ...]
产物:
    output/outsample_momentum.csv  R1/R2 两窗口：TSMOM vs 基准 A + 多空腿
    output/outsample_sector.csv    R3 长多腿板块分解（两窗口）
    output/outsample_risk.csv      R4 协方差风控框架实现波动（两窗口）
    output/outsample_gates.json    门禁/结论
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

from quant_futures_01 import config as cfgmod
from quant_futures_01.cost import CostModel
from quant_futures_01.portfolio import (
    cov_vol_target_weights,
    run_backtest,
    sector_contribution,
)
from backtest import generate_weights, load_adj_close_panel

W1 = ("2010-01-01", "2017-12-31")
W2 = ("2018-01-01", "2026-12-31")


def _to_native(v):
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, (np.bool_, np.floating, np.integer)):
        return v.item()
    return v


def _run_window(closes, returns, mode, cfg, cost, a, b, lookback=None):
    mask = (closes.index >= pd.Timestamp(a)) & (closes.index <= pd.Timestamp(b))
    w = generate_weights(mode, closes, cfg, lookback=lookback).loc[mask]
    r = returns.loc[mask]
    res = run_backtest(r, w, cost)
    r_safe = r.fillna(0.0)
    long_leg = float((res.weights.clip(lower=0) * r_safe).sum(axis=1).cumsum().iloc[-1])
    short_leg = float(
        (res.weights.clip(upper=0) * r_safe).sum(axis=1).cumsum().iloc[-1]
    )
    return res.metrics, long_leg, short_leg, res.weights


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)
    out = Path(cfg.paths.output)
    out.mkdir(parents=True, exist_ok=True)
    cost = CostModel(cfg)

    closes = load_adj_close_panel(cfg)
    returns = closes.pct_change()
    sector_map = {u["symbol"]: u["sector"] for u in cfg.universe}
    L = cfg.strategy.tsmom_lookback

    # ---- R1/R2: TSMOM vs 基准 A + 多空腿（两窗口） ----
    rows = []
    for a, b in [W1, W2]:
        m_ts, ll_ts, sl_ts, w_ts = _run_window(
            closes, returns, "tsmom", cfg, cost, a, b, lookback=L
        )
        m_ba, _, _, _ = _run_window(closes, returns, "equal", cfg, cost, a, b)
        rows.append(
            {
                "window": f"{a[:4]}-{b[:4]}",
                "tsmom_sharpe": m_ts["sharpe"],
                "benchA_sharpe": m_ba["sharpe"],
                "tsmom_ann": m_ts["ann_return"],
                "tsmom_long_leg": ll_ts,
                "tsmom_short_leg": sl_ts,
            }
        )
    r12_df = pd.DataFrame(rows)
    r12_df.to_csv(out / "outsample_momentum.csv", index=False)
    print("=== R1/R2 两窗口：TSMOM vs 基准 A + 多空腿 ===")
    print(r12_df.round(4).to_string(index=False))

    # ---- R3: TSMOM 长多腿板块分解（两窗口） ----
    r3_rows = []
    for a, b in [W1, W2]:
        mask = (closes.index >= pd.Timestamp(a)) & (closes.index <= pd.Timestamp(b))
        w_ts = generate_weights("tsmom", closes, cfg, lookback=L).loc[mask]
        r = returns.loc[mask]
        contrib = w_ts.clip(lower=0.0) * r.fillna(0.0)
        by_sec = contrib.T.groupby(sector_map).sum().T.cumsum().iloc[-1]
        total = by_sec.sum()
        row = {"window": f"{a[:4]}-{b[:4]}", "long_leg_total": total}
        for c in by_sec.index:
            row[f"{c}"] = by_sec[c] / total if total != 0 else float("nan")
        r3_rows.append(row)
    r3_df = pd.DataFrame(r3_rows)
    r3_df.to_csv(out / "outsample_sector.csv", index=False)
    print("\n=== R3 长多腿板块分解 ===")
    print(r3_df.round(4).to_string(index=False))

    # ---- R4: 协方差风控框架实现波动（两窗口） ----
    r4_rows = []
    for a, b in [W1, W2]:
        mask = (closes.index >= pd.Timestamp(a)) & (closes.index <= pd.Timestamp(b))
        w2 = cov_vol_target_weights(returns).loc[mask]
        r = returns.loc[mask]
        res = run_backtest(r, w2, cost)
        r4_rows.append(
            {
                "window": f"{a[:4]}-{b[:4]}",
                "realized_vol_pct": round(res.metrics["ann_vol"] * 100, 1),
                "target_pct": round(cfg.backtest.vol_target * 100, 1),
            }
        )
    r4_df = pd.DataFrame(r4_rows)
    r4_df.to_csv(out / "outsample_risk.csv", index=False)
    print("\n=== R4 协方差风控框架实现波动 ===")
    print(r4_df.to_string(index=False))

    # ---- 结论判定（写死：窗口不可事后换） ----
    w1 = r12_df.iloc[0]
    w2 = r12_df.iloc[1]
    # R1: 两窗口 TSMOM 是否均无边际（夏普 ≤ 基准 A）
    r1_no_edge = (w1["tsmom_sharpe"] <= w1["benchA_sharpe"]) and (
        w2["tsmom_sharpe"] <= w2["benchA_sharpe"]
    )
    # R2: 两窗口空头腿是否均无正贡献（short_leg <= 0）
    r2_short_useless = (w1["tsmom_short_leg"] <= 0) and (w2["tsmom_short_leg"] <= 0)
    # 结构性 = R1 与 R2 在 W1 均复现 W2 的失败
    structural = r1_no_edge and r2_short_useless
    verdict = {
        "R1_no_signal_edge_W1": {
            "tsmom": float(w1["tsmom_sharpe"]),
            "benchA": float(w1["benchA_sharpe"]),
            "no_edge": bool(w1["tsmom_sharpe"] <= w1["benchA_sharpe"]),
        },
        "R2_short_useless_W1": {
            "short_leg": float(w1["tsmom_short_leg"]),
            "useless": bool(w1["tsmom_short_leg"] <= 0),
        },
        "structural_conclusion": bool(structural),
    }
    with open(out / "outsample_gates.json", "w", encoding="utf-8") as f:
        json.dump(_to_native(verdict), f, ensure_ascii=False, indent=2)
    print("\n=== 结论判定 ===")
    print(json.dumps(_to_native(verdict), ensure_ascii=False, indent=2))
    if structural:
        print(
            "→ ✅ 结论结构性：中国商品 2010-2026 简单信号/空头无普适 edge，研究可收官"
        )
    else:
        print(
            "→ 🟡 结论窗口特定：旧 regime 出现有效信号/空头，须修正表述并探索旧 regime 方向"
        )


if __name__ == "__main__":
    main()
