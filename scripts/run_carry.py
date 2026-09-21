"""Carry（期限结构）方案实施：主运行 + 定义稳健性 + 子时段 + 成本 + 多空分解 + 门禁。

预注册方案: docs/carry_plan.md P2–P5（2026-09-20 批准）
用法:
    .venv/bin/python scripts/run_carry.py [--config ...]
产物:
    output/carry_120_*.csv/json       主运行（指标/净值/分板块贡献）
    output/carry_variants.csv         定义稳健性（vol1/oi2/幅度加权）
    output/carry_subperiod.csv        子时段
    output/carry_cost_sens.csv        成本敏感性
    output/carry_legs.csv             多空腿分解
    output/carry_gates.json           门禁判定
    output/carry_f1_check.csv         F1 与主力连续对拍（G0a）
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
from quant_futures_01.data import load_daily
from quant_futures_01.portfolio import run_backtest, sector_contribution
from quant_futures_01.termstructure import carry_symbols, load_slope, load_slope_panel
from backtest import carry_weights, load_adj_close_panel

SUB_PERIODS = [("2018-01-01", "2021-12-31"), ("2022-01-01", "2026-09-18")]
COST_MULTIPLIERS = [0.5, 1.0, 2.0]


def _to_native(v):
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, (np.bool_, np.floating, np.integer)):
        return v.item()
    return v


def _run(closes, weights, cost):
    returns = closes.pct_change()
    res = run_backtest(returns, weights, cost)
    return res.metrics, res.weights


def _subset_closes(cfg):
    closes = load_adj_close_panel(cfg)
    syms = [u["symbol"] for u in carry_symbols(cfg)]
    return closes[[c for c in syms if c in closes.columns]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)
    out = Path(cfg.paths.output)
    out.mkdir(parents=True, exist_ok=True)

    closes = _subset_closes(cfg)
    returns = closes.pct_change()
    base_cost = CostModel(cfg)

    # ---- G0a: F1 与主力连续对拍（抽查 3 品种 × 3 日） ----
    checks = []
    for sym in ["RB0", "CU0", "TA0"]:
        slope = load_slope(sym, "oi1")
        main = load_daily(sym).set_index("date")
        for day in ["2021-06-15", "2024-06-03", "2026-06-01"]:
            day = pd.Timestamp(day)
            row = slope[slope["date"] <= day]
            if row.empty or day not in main.index:
                continue
            r = row.iloc[-1]
            diff_pct = (r["f1_close"] / main.loc[day, "close"] - 1) * 100
            checks.append(
                {
                    "symbol": sym,
                    "date": str(day.date()),
                    "F1": r["F1"],
                    "f1_close": round(float(r["f1_close"]), 2),
                    "main_close": round(float(main.loc[day, "close"]), 2),
                    "diff_pct": round(float(diff_pct), 3),
                }
            )
    pd.DataFrame(checks).to_csv(out / "carry_f1_check.csv", index=False)
    print("=== G0a: F1 vs 主力连续对拍 ===")
    print(pd.DataFrame(checks).to_string(index=False))

    # ---- M2 主运行（oi1 符号，等权） ----
    slope_main = load_slope_panel("oi1", cfg)
    w_main = carry_weights(closes, slope_main, cfg)
    metrics, w_main2 = _run(closes, w_main, base_cost)
    sector_map = {u["symbol"]: u["sector"] for u in carry_symbols(cfg)}
    sec = sector_contribution(returns, w_main2, sector_map)
    sec.to_csv(out / "carry_120_sector.csv")
    with open(out / "carry_120_metrics.json", "w", encoding="utf-8") as f:
        json.dump(_to_native(metrics), f, ensure_ascii=False, indent=2)
    print("\n=== 主运行 carry（oi1 符号） ===")
    for k, v in metrics.items():
        print(f"  {k:20s} {v:.4f}" if isinstance(v, float) else f"  {k:20s} {v}")

    # 基准 A（同子集）
    bench_metrics, _ = _run(
        closes,
        pd.DataFrame(
            1.0 / len(closes.columns), index=closes.index, columns=closes.columns
        ),
        base_cost,
    )
    bench_sharpe = bench_metrics["sharpe"]
    main_sharpe = metrics["sharpe"]

    # ---- M3 定义稳健性 ----
    variants = []
    for v in ["vol1", "oi2"]:
        w = carry_weights(closes, load_slope_panel(v, cfg), cfg)
        m, _ = _run(closes, w, base_cost)
        variants.append(
            {"variant": v, "sharpe": m["sharpe"], "ann_return": m["ann_return"]}
        )
    w_amp = carry_weights(closes, slope_main, cfg, amplitude=True)
    m_amp, _ = _run(closes, w_amp, base_cost)
    variants.append(
        {
            "variant": "amp(|slope|)",
            "sharpe": m_amp["sharpe"],
            "ann_return": m_amp["ann_return"],
        }
    )
    var_df = pd.DataFrame(variants)
    var_df.to_csv(out / "carry_variants.csv", index=False)
    print("\n=== M3 定义稳健性 ===")
    print(var_df.to_string(index=False))

    # ---- M4a 子时段 ----
    sub = []
    for a, b in SUB_PERIODS:
        mask = (closes.index >= pd.Timestamp(a)) & (closes.index <= pd.Timestamp(b))
        m, _ = _run(closes, w_main.loc[mask], base_cost)
        sub.append(
            {"period": f"{a}~{b}", "sharpe": m["sharpe"], "ann_return": m["ann_return"]}
        )
    sub_df = pd.DataFrame(sub)
    sub_df.to_csv(out / "carry_subperiod.csv", index=False)
    print("\n=== M4a 子时段 ===")
    print(sub_df.to_string(index=False))

    # ---- M4b 成本敏感性 ----
    cost_sens = []
    for mult in COST_MULTIPLIERS:
        cm = CostModel(
            cfg,
            slippage_bps=cfg.cost.slippage_bps * mult,
            commission_bps=cfg.cost.commission_bps * mult,
        )
        m, _ = _run(closes, w_main, cm)
        cost_sens.append(
            {"cost_mult": mult, "sharpe": m["sharpe"], "ann_return": m["ann_return"]}
        )
    cost_df = pd.DataFrame(cost_sens)
    cost_df.to_csv(out / "carry_cost_sens.csv", index=False)
    print("\n=== M4b 成本敏感性 ===")
    print(cost_df.to_string(index=False))

    # ---- M4c 板块集中度 ----
    total = sec.sum(axis=1).iloc[-1]
    sec_contrib = {c: float(sec[c].iloc[-1]) for c in sec.columns}
    max_sec_ratio = (
        max(abs(v) / abs(total) for v in sec_contrib.values())
        if total != 0
        else float("nan")
    )
    print("\n=== M4c 板块集中度 ===")
    for c, v in sec_contrib.items():
        print(f"  {c}: {v:+.3f} ({v / total * 100:+.1f}%)")

    # ---- M4d 多空腿分解 ----
    r_safe = returns.fillna(0.0)
    long_leg = float((w_main2.clip(lower=0) * r_safe).sum(axis=1).cumsum().iloc[-1])
    short_leg = float((w_main2.clip(upper=0) * r_safe).sum(axis=1).cumsum().iloc[-1])
    legs = {"long_leg_cum": long_leg, "short_leg_cum": short_leg}
    pd.DataFrame([legs]).to_csv(out / "carry_legs.csv", index=False)
    print("\n=== M4d 多空腿分解 ===")
    for k, v in legs.items():
        print(f"  {k}: {v:+.4f}")

    # ---- G 门禁判定 ----
    s_p1, s_p2 = [r.sharpe for r in sub_df.itertuples()]
    v_map = {r.variant: r.sharpe for r in var_df.itertuples()}
    g1 = main_sharpe > bench_sharpe
    g2a = (s_p1 > 0) == (s_p2 > 0)
    g2b = np.sign(v_map.get("vol1", -9)) == np.sign(main_sharpe) and np.sign(
        v_map.get("oi2", -9)
    ) == np.sign(main_sharpe)
    g2c = max_sec_ratio <= 0.5 if not np.isnan(max_sec_ratio) else False
    g2d = long_leg > 0
    c05 = float(cost_df.loc[cost_df["cost_mult"] == 0.5, "sharpe"].iloc[0])
    c20 = float(cost_df.loc[cost_df["cost_mult"] == 2.0, "sharpe"].iloc[0])
    g3 = c05 > 0 and c20 > 0
    gates = {
        "G1_vs_bench_A": {"pass": g1, "carry": main_sharpe, "bench_A": bench_sharpe},
        "G2a_subperiod_same_sign": {"pass": g2a, "p1": s_p1, "p2": s_p2},
        "G2b_definition_robust": {
            "pass": g2b,
            "oi1": main_sharpe,
            "vol1": v_map.get("vol1"),
            "oi2": v_map.get("oi2"),
            "amp": v_map.get("amp(|slope|)"),
        },
        "G2c_sector_concentration": {"pass": g2c, "max_ratio": max_sec_ratio},
        "G2d_long_leg_positive": {"pass": g2d, "long": long_leg, "short": short_leg},
        "G3_cost_sensitivity": {"pass": g3, "x0.5": c05, "x2.0": c20},
    }
    with open(out / "carry_gates.json", "w", encoding="utf-8") as f:
        json.dump(_to_native(gates), f, ensure_ascii=False, indent=2)
    print("\n=== 门禁判定 ===")
    for k, v in gates.items():
        print(f"  {k}: {'✅' if v['pass'] else '❌'}  {v}")


if __name__ == "__main__":
    main()
