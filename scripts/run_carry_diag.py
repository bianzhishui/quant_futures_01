"""Carry 稳健性诊断：有色剥离（G2c）+ 远月定义一致性（G2b）+ 板块统计。

预注册方案: docs/carry_diag_plan.md（2026-09-21 批准）
用法:
    .venv/bin/python scripts/run_carry_diag.py [--config ...]
产物:
    output/carry_diag_sector.csv       D1 有色剥离（非有色/仅有色 × carry/基准 A）
    output/carry_diag_farmonth.csv     D2 oi1 vs oi2 符号一致率 + 相关
    output/carry_diag_slope_stats.csv  D3 板块 backwardation 占比
判定（写死）:
    C1: D1a 非有色 carry 夏普 > 0 且 > 同子集基准 A → 溢价非有色专属
    C2: 全品种符号一致率中位数 ≥ 70% → 斜率定义基本稳定
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
from quant_futures_01.portfolio import run_backtest
from quant_futures_01.termstructure import carry_symbols, load_slope, load_slope_panel
from backtest import carry_weights, generate_weights, load_adj_close_panel


def _to_native(v):
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, (np.bool_, np.floating, np.integer)):
        return v.item()
    return v


def _run_group(closes: pd.DataFrame, weights: pd.DataFrame, cost) -> dict:
    """跑一组权重，返回 (metrics, long_leg, short_leg)。"""
    returns = closes.pct_change()
    res = run_backtest(returns, weights, cost)
    r_safe = returns.fillna(0.0)
    w = res.weights
    long_leg = float((w.clip(lower=0) * r_safe).sum(axis=1).cumsum().iloc[-1])
    short_leg = float((w.clip(upper=0) * r_safe).sum(axis=1).cumsum().iloc[-1])
    return {**res.metrics, "long_leg": long_leg, "short_leg": short_leg}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)
    out = Path(cfg.paths.output)
    out.mkdir(parents=True, exist_ok=True)

    closes_all = load_adj_close_panel(cfg)
    slope_oi1 = load_slope_panel("oi1", cfg)
    slope_oi2 = load_slope_panel("oi2", cfg)
    if closes_all.empty or slope_oi1.shape[1] == 0:
        raise SystemExit(
            "数据缺失：先运行 scripts/fetch_data.py 与 scripts/fetch_termstructure.py"
        )
    base_cost = CostModel(cfg)

    # ---- D1 有色剥离 ----
    sectors = {u["symbol"]: u["sector"] for u in carry_symbols(cfg)}
    syms = list(slope_oi1.columns)
    non_col = [s for s in syms if sectors.get(s) != "有色"]
    metal_col = [s for s in syms if sectors.get(s) == "有色"]
    d1_rows = []
    for label, cols in [("非有色(14)", non_col), ("仅有色(6)", metal_col)]:
        if not cols:
            continue
        c = closes_all[[x for x in cols if x in closes_all.columns]]
        sl = slope_oi1[cols].reindex(index=c.index)
        w_carry = carry_weights(c, sl, cfg)
        m_carry = _run_group(c, w_carry, base_cost)
        w_bench = generate_weights("equal", c, cfg)
        m_bench = _run_group(c, w_bench, base_cost)
        d1_rows.append(
            {
                "group": label,
                "n_symbols": len(cols),
                "carry_sharpe": m_carry["sharpe"],
                "benchA_sharpe": m_bench["sharpe"],
                "carry_ann": m_carry["ann_return"],
                "carry_long_leg": m_carry["long_leg"],
                "carry_short_leg": m_carry["short_leg"],
            }
        )
    d1_df = pd.DataFrame(d1_rows)
    d1_df.to_csv(out / "carry_diag_sector.csv", index=False)
    print("=== D1 有色剥离 ===")
    print(d1_df.to_string(index=False))

    # ---- D2 远月定义符号一致性 ----
    d2_rows = []
    pairs = []
    for sym in slope_oi1.columns:
        if sym not in slope_oi2.columns:
            continue
        j = pd.concat([slope_oi1[sym], slope_oi2[sym]], axis=1, join="inner").dropna()
        if len(j) < 30:
            continue
        j.columns = ["a", "b"]
        both = j[(j["a"].abs() > 1e-4) & (j["b"].abs() > 1e-4)]
        if len(both) < 30:
            continue
        agree = float(((both["a"] > 0) == (both["b"] > 0)).mean())
        pairs.append(j)
        d2_rows.append(
            {"symbol": sym, "n": len(both), "sign_agree_pct": round(agree * 100, 1)}
        )
    d2_df = pd.DataFrame(d2_rows)
    d2_df.to_csv(out / "carry_diag_farmonth.csv", index=False)
    # 面板相关
    if pairs:
        p = pd.concat(pairs)
        corr = float(p["a"].corr(p["b"]))
    else:
        corr = float("nan")
    median_agree = (
        float(d2_df["sign_agree_pct"].median()) if len(d2_df) else float("nan")
    )
    print("\n=== D2 远月定义符号一致性 ===")
    print(
        f"  全品种符号一致率中位数: {median_agree:.1f}%   面板 Pearson 相关: {corr:.3f}"
    )
    print(d2_df.to_string(index=False))

    # ---- D3 板块 backwardation 占比 ----
    d3_rows = []
    for u in carry_symbols(cfg):
        df = load_slope(u["symbol"], "oi1")
        d3_rows.append(
            {
                "sector": u["sector"],
                "symbol": u["symbol"],
                "backwardation_pct": round(float((df["slope"] > 0).mean()) * 100, 1),
                "mean_slope": round(float(df["slope"].mean()), 5),
            }
        )
    d3_df = pd.DataFrame(d3_rows)
    d3_df.to_csv(out / "carry_diag_slope_stats.csv", index=False)
    print("\n=== D3 板块 backwardation 占比（按板块汇总） ===")
    print(
        d3_df.groupby("sector")[["backwardation_pct", "mean_slope"]]
        .agg(["mean", "count"])
        .to_string()
    )

    # ---- C1/C2 判定 ----
    d1a = d1_df[d1_df["group"] == "非有色(14)"]
    c1 = bool(
        len(d1a)
        and d1a["carry_sharpe"].iloc[0] > 0
        and d1a["carry_sharpe"].iloc[0] > d1a["benchA_sharpe"].iloc[0]
    )
    c2 = bool(np.isfinite(median_agree) and median_agree >= 70.0)
    verdict = {
        "C1_nonmetal_independent": {
            "pass": c1,
            "nonmetal_carry_sharpe": float(d1a["carry_sharpe"].iloc[0])
            if len(d1a)
            else None,
            "nonmetal_benchA_sharpe": float(d1a["benchA_sharpe"].iloc[0])
            if len(d1a)
            else None,
        },
        "C2_definition_stable": {"pass": c2, "median_sign_agree_pct": median_agree},
    }
    with open(out / "carry_diag_gates.json", "w", encoding="utf-8") as f:
        json.dump(_to_native(verdict), f, ensure_ascii=False, indent=2)
    print("\n=== C1/C2 判定 ===")
    for k, v in verdict.items():
        print(f"  {k}: {'✅' if v['pass'] else '❌'}  {v}")
    if c1 and c2:
        print("  → 综合: carry 正溢价真实且跨板块、定义稳定")
    elif c1 and not c2:
        print("  → 综合: 溢价真实但远月定义敏感，须先解决定义再论")
    elif not c1 and c2:
        print("  → 综合: 正溢价主要来自有色，修正主结论为'有色 backwardation 溢价'")
    else:
        print("  → 综合: carry 结论不可靠，倾向放弃该方向")


if __name__ == "__main__":
    main()
