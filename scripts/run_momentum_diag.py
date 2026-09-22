"""动量长多腿板块诊断：板块分解（D1）+ 剔除贵金属有色（D2）+ 时段（D3）+ 基准对照（D4）。

预注册方案: docs/momentum_diag_plan.md（2026-09-21 批准）
用法:
    .venv/bin/python scripts/run_momentum_diag.py [--config ...]
产物:
    output/momentum_diag_longleg_sector.csv   D1 长多腿板块累计贡献 + 两板块占比
    output/momentum_diag_strip.csv            D2 剔除贵金属+有色后长多腿复算
    output/momentum_diag_subperiod.csv        D3 时段结构
    output/momentum_diag_bench.csv            D4 基准 A 板块对照
    output/momentum_diag_gates.json           C1/C2 判定
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
from backtest import generate_weights, load_adj_close_panel

MODES = ["tsmom", "tsmom_vol", "xsmom"]
STRIP = {"AU0", "AG0", "CU0", "AL0", "ZN0", "NI0", "SN0", "SS0"}  # 贵金属2 + 有色6
SUB_PERIODS = [("2018-01-01", "2021-12-31"), ("2022-01-01", "2026-09-18")]


def _to_native(v):
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, (np.bool_, np.floating, np.integer)):
        return v.item()
    return v


def _long_leg_series(weights: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    """长多腿逐日累计贡献（权重>0 腿 × 收益）。"""
    r = returns.fillna(0.0)
    return (weights.clip(lower=0.0) * r).sum(axis=1).cumsum()


def _sector_long_leg(
    weights: pd.DataFrame, returns: pd.DataFrame, sector_map: dict
) -> pd.DataFrame:
    """每板块长多腿累计贡献（末值）。pandas3 groupby 不再支持 axis=1，用转置法。"""
    r = returns.fillna(0.0)
    contrib = weights.clip(lower=0.0) * r
    by_sector = contrib.T.groupby(sector_map).sum().T.cumsum()
    return by_sector.iloc[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)
    out = Path(cfg.paths.output)
    out.mkdir(parents=True, exist_ok=True)

    closes = load_adj_close_panel(cfg)
    returns = closes.pct_change()
    sector_map = {u["symbol"]: u["sector"] for u in cfg.universe}

    # ---- D1/D4: 长多腿板块分解 + 基准对照 ----
    d1_rows = []
    for mode in MODES:
        w = generate_weights(mode, closes, cfg)
        sec = _sector_long_leg(w, returns, sector_map)
        total = sec.sum()
        metal = sec.get("有色", 0.0) + sec.get("贵金属", 0.0)
        row = {"mode": mode, "total_long_leg": total}
        for c in sec.index:
            row[f"{c}_share"] = sec[c] / total if total != 0 else float("nan")
        row["metal_precious_share"] = metal / total if total != 0 else float("nan")
        d1_rows.append(row)
    d1_df = pd.DataFrame(d1_rows)
    d1_df.to_csv(out / "momentum_diag_longleg_sector.csv", index=False)
    print("=== D1 长多腿板块分解 ===")
    print(d1_df.round(4).to_string(index=False))

    w_bench = generate_weights("equal", closes, cfg)
    bench_sec = _sector_long_leg(w_bench, returns, sector_map)
    d4_df = pd.DataFrame(
        {"sector": bench_sec.index, "bench_long_leg": bench_sec.values}
    )
    d4_df.to_csv(out / "momentum_diag_bench.csv", index=False)
    print("\n=== D4 基准 A（等权多头）板块对照 ===")
    print(d4_df.round(4).to_string(index=False))

    # ---- D2: 剔除贵金属+有色后长多腿复算 ----
    cols = [c for c in closes.columns if c not in STRIP]
    closes_s = closes[cols]
    returns_s = closes_s.pct_change()
    d2_rows = []
    for mode in MODES:
        w = generate_weights(mode, closes_s, cfg)
        long_full = _long_leg_series(generate_weights(mode, closes, cfg), returns).iloc[
            -1
        ]
        long_strip = _long_leg_series(w, returns_s).iloc[-1]
        d2_rows.append(
            {
                "mode": mode,
                "long_leg_full": float(long_full),
                "long_leg_strip": float(long_strip),
            }
        )
    d2_df = pd.DataFrame(d2_rows)
    d2_df.to_csv(out / "momentum_diag_strip.csv", index=False)
    print("\n=== D2 剔除贵金属+有色（22 品种复算）===")
    print(d2_df.round(4).to_string(index=False))

    # ---- D3: 时段结构（全品种长多腿） ----
    d3_rows = []
    for mode in MODES:
        w = generate_weights(mode, closes, cfg)
        series = _long_leg_series(w, returns)
        for a, b in SUB_PERIODS:
            mask = (series.index >= pd.Timestamp(a)) & (series.index <= pd.Timestamp(b))
            seg = series.loc[mask]
            d3_rows.append(
                {
                    "mode": mode,
                    "period": f"{a}~{b}",
                    "long_leg_cum": float(seg.iloc[-1]) if len(seg) else 0.0,
                }
            )
    d3_df = pd.DataFrame(d3_rows)
    d3_df.to_csv(out / "momentum_diag_subperiod.csv", index=False)
    print("\n=== D3 时段结构 ===")
    print(
        d3_df.pivot(index="mode", columns="period", values="long_leg_cum")
        .round(4)
        .to_string()
    )

    # ---- C1/C2 判定 ----
    metal_share = d1_df["metal_precious_share"].mean()
    c1 = bool(metal_share >= 0.60)
    n_survive = int((d2_df["long_leg_strip"] > 0).sum())
    c2_survive = n_survive >= 2
    gates = {
        "C1_metal_precious_concentration": {
            "pass": c1,
            "mean_share": float(metal_share),
            "shares": [round(float(x), 3) for x in d1_df["metal_precious_share"]],
        },
        "C2_survivors_after_strip": {
            "pass": c2_survive,
            "n_survive_of_3": n_survive,
            "stripped_long_legs": [round(float(x), 4) for x in d2_df["long_leg_strip"]],
        },
    }
    with open(out / "momentum_diag_gates.json", "w", encoding="utf-8") as f:
        json.dump(_to_native(gates), f, ensure_ascii=False, indent=2)
    print("\n=== C1/C2 判定 ===")
    for k, v in gates.items():
        print(f"  {k}: {'✅' if v['pass'] else '❌'}  {v}")
    if c1 and not c2_survive:
        print(
            "  → 综合: '动量长多腿为正'降级为'贵金属+有色板块 beta'，全部信号发现证伪 → 建议转组合/风控层"
        )
    elif c1 and c2_survive:
        print(
            "  → 综合: 长多腿高度集中于两板块，但剔除后仍有设计为正——结论保留但强度下降，① 谨慎推进"
        )
    elif not c1 and c2_survive:
        print(
            "  → 综合: 长多腿跨板块仍为正（未高度集中）——结论保留，① 可推进（承担多重检验）"
        )
    else:
        print("  → 综合: 集中度不高但剔除后多数为负——结论模糊，谨慎处理")


if __name__ == "__main__":
    main()
