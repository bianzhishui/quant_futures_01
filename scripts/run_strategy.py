"""动量族策略方案实施：主运行 + 灵敏度 + 子时段 + 成本敏感性 + 多空分解 + 门禁判定。

预注册方案: docs/tsmom_plan.md / docs/tsmom_vol_plan.md / docs/xsmom_plan.md（2026-09-20 批准）
用法:
    .venv/bin/python scripts/run_strategy.py [--mode tsmom|tsmom_vol|xsmom] [--bench equal|vol] [--config ...]
  - --mode tsmom:     等权幅度时序动量（对照基准 A 等权多头）
  - --mode tsmom_vol: 波动率目标幅度时序动量（对照基准 V = 波动率目标长多占位，增量门禁）
  - --mode xsmom:     横截面动量 25/75 分位多空（对照基准 A；G3d = 多头腿必须有效）
产物:
    output/{mode}_{L}_*.csv/json            主运行（指标/净值/分板块贡献）
    output/{mode}_sensitivity.csv           灵敏度 L∈{20,60,120,250} 净夏普
    output/{mode}_quantile.csv              分位诊断 15/85、25/75、40/60（xsmom 模式）
    output/{mode}_subperiod.csv             子时段净夏普
    output/{mode}_cost_sens.csv             成本敏感性 ×0.5/×1/×2 净夏普
    output/{mode}_legs.csv                  多空腿分解
    output/{mode}_gates.json                门禁判定
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
from quant_futures_01.portfolio import run_backtest, sector_contribution
from quant_futures_01.strategy import xsmom_weights
from backtest import generate_weights, load_adj_close_panel

LOOKBACKS = [20, 60, 120, 250]
QUANTILES = [0.15, 0.25, 0.40]  # 分位诊断（xsmom；短腿 ≤ q / 长腿 ≥ 1−q）
SUB_PERIODS = [("2018-01-01", "2021-12-31"), ("2022-01-01", "2026-09-18")]
COST_MULTIPLIERS = [0.5, 1.0, 2.0]


def _to_native(v):
    """把 numpy 标量/嵌套结构转成可 JSON 序列化的原生 Python 类型。"""
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, (np.bool_, np.floating, np.integer)):
        return v.item()
    return v


def _metrics_for(
    closes: pd.DataFrame,
    mode: str,
    cfg,
    cost: CostModel,
    lookback: int | None = None,
    mask: pd.Series | None = None,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """在指定切片上跑一种模式，返回 (metrics, sector_df, weights_df)。"""
    returns = closes.pct_change()
    target_w = generate_weights(mode, closes, cfg, lookback=lookback)
    if mask is not None:
        returns = returns.loc[mask]
        target_w = target_w.loc[mask]
    res = run_backtest(returns, target_w, cost)
    sector_map = {u["symbol"]: u["sector"] for u in cfg.universe}
    sec = sector_contribution(returns, res.weights, sector_map)
    return res.metrics, sec, res.weights


def _legs_contribution(returns: pd.DataFrame, weights: pd.DataFrame) -> dict:
    """多空腿分解：长多腿（w>0）与空头腿（w<0）的累计名义贡献。"""
    long_leg = (weights.clip(lower=0.0) * returns.fillna(0.0)).sum(axis=1).cumsum()
    short_leg = (weights.clip(upper=0.0) * returns.fillna(0.0)).sum(axis=1).cumsum()
    return {
        "long_leg_cum": float(long_leg.iloc[-1]) if len(long_leg) else float("nan"),
        "short_leg_cum": float(short_leg.iloc[-1]) if len(short_leg) else float("nan"),
        "total_cum": float(long_leg.iloc[-1] + short_leg.iloc[-1])
        if len(long_leg)
        else float("nan"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    parser.add_argument(
        "--mode", default="tsmom", choices=["tsmom", "tsmom_vol", "xsmom"]
    )
    parser.add_argument(
        "--bench",
        default=None,
        choices=["equal", "vol"],
        help="G2 对照基准（默认 tsmom/xsmom→equal，tsmom_vol→vol）",
    )
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)
    mode = args.mode
    prefix = {"tsmom": "tsmom", "tsmom_vol": "tsmom_vol", "xsmom": "xsmom"}[mode]
    bench = args.bench or ("vol" if mode == "tsmom_vol" else "equal")
    out = Path(cfg.paths.output)
    out.mkdir(parents=True, exist_ok=True)

    closes = load_adj_close_panel(cfg)
    returns = closes.pct_change()
    base_cost = CostModel(cfg)
    L = cfg.strategy.tsmom_lookback if mode != "xsmom" else cfg.strategy.xsmom_lookback

    # ---- M2 主运行 ----
    metrics, sec, w_main = _metrics_for(closes, mode, cfg, base_cost, lookback=L)
    sec.to_csv(out / f"{prefix}_{L}_sector.csv")
    with open(out / f"{prefix}_{L}_metrics.json", "w", encoding="utf-8") as f:
        json.dump(_to_native(metrics), f, ensure_ascii=False, indent=2)
    print(f"=== M2 主运行 mode={mode} L={L} ===")
    for k, v in metrics.items():
        print(f"  {k:20s} {v:.4f}" if isinstance(v, float) else f"  {k:20s} {v}")

    # 对照基准（G2）
    bench_metrics, _, _ = _metrics_for(closes, bench, cfg, base_cost)
    bench_sharpe = bench_metrics["sharpe"]
    main_sharpe = metrics["sharpe"]

    # ---- M3 灵敏度 ----
    sens = []
    for lb in LOOKBACKS:
        m, _, _ = _metrics_for(closes, mode, cfg, base_cost, lookback=lb)
        sens.append(
            {
                "lookback": lb,
                "sharpe": m["sharpe"],
                "ann_return": m["ann_return"],
                "final_nav": m["final_nav"],
            }
        )
    sens_df = pd.DataFrame(sens)
    sens_df.to_csv(out / f"{prefix}_sensitivity.csv", index=False)
    print("\n=== M3 灵敏度 ===")
    print(sens_df.to_string(index=False))

    # ---- M3b 分位诊断（xsmom 模式） ----
    if mode == "xsmom":
        q_rows = []
        for q in QUANTILES:
            wq = xsmom_weights(closes, L, q)
            mq = run_backtest(returns, wq, base_cost).metrics
            q_rows.append(
                {"quantile": q, "sharpe": mq["sharpe"], "ann_return": mq["ann_return"]}
            )
        q_df = pd.DataFrame(q_rows)
        q_df.to_csv(out / f"{prefix}_quantile.csv", index=False)
        print("\n=== M3b 分位诊断 ===")
        print(q_df.to_string(index=False))

    # ---- M4a 子时段 ----
    sub = []
    for a, b in SUB_PERIODS:
        mask = (closes.index >= pd.Timestamp(a)) & (closes.index <= pd.Timestamp(b))
        m, _, _ = _metrics_for(closes, mode, cfg, base_cost, lookback=L, mask=mask)
        sub.append(
            {"period": f"{a}~{b}", "sharpe": m["sharpe"], "ann_return": m["ann_return"]}
        )
    sub_df = pd.DataFrame(sub)
    sub_df.to_csv(out / f"{prefix}_subperiod.csv", index=False)
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
        m, _, _ = _metrics_for(closes, mode, cfg, cm, lookback=L)
        cost_sens.append(
            {"cost_mult": mult, "sharpe": m["sharpe"], "ann_return": m["ann_return"]}
        )
    cost_df = pd.DataFrame(cost_sens)
    cost_df.to_csv(out / f"{prefix}_cost_sens.csv", index=False)
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

    # ---- M4d 多空腿分解（tsmom_vol 诊断） ----
    legs = _legs_contribution(returns, w_main)
    pd.DataFrame([legs]).to_csv(out / f"{prefix}_legs.csv", index=False)
    print("\n=== M4d 多空腿分解（累计名义贡献） ===")
    for k, v in legs.items():
        print(f"  {k}: {v:+.4f}")

    # ---- G 门禁判定 ----
    s_map = {row.lookback: row.sharpe for row in sens_df.itertuples()}
    g2 = main_sharpe > bench_sharpe
    s_p1, s_p2 = [r.sharpe for r in sub_df.itertuples()]
    g3a = (s_p1 > 0) == (s_p2 > 0)
    g3b = (s_map.get(60, -9) >= 0 or s_map.get(250, -9) >= 0) and (
        s_map.get(120, -9) - max(s_map.get(60, -9), s_map.get(250, -9)) < 0.5
    )
    g3c = max_sec_ratio <= 0.5 if not np.isnan(max_sec_ratio) else False
    c05 = float(cost_df.loc[cost_df["cost_mult"] == 0.5, "sharpe"].iloc[0])
    c20 = float(cost_df.loc[cost_df["cost_mult"] == 2.0, "sharpe"].iloc[0])
    g4 = c05 > 0 and c20 > 0
    long_leg, short_leg = legs["long_leg_cum"], legs["short_leg_cum"]
    # G3d：xsmom 要求多头腿（赢家）必须有效；其余模式要求"非仅空头腿在赚钱"
    g3d = (long_leg > 0) if mode == "xsmom" else (long_leg > 0 or short_leg < 0)
    gates = {
        "G2_incremental_vs_bench": {
            "pass": g2,
            f"{mode}": main_sharpe,
            f"bench_{bench}": bench_sharpe,
        },
        "G3a_subperiod_same_sign": {"pass": g3a, "p1": s_p1, "p2": s_p2},
        "G3b_plateau": {
            "pass": g3b,
            "S20": s_map.get(20),
            "S60": s_map.get(60),
            "S120": s_map.get(120),
            "S250": s_map.get(250),
        },
        "G3c_sector_concentration": {"pass": g3c, "max_ratio": max_sec_ratio},
        "G3d_legs_decomposition": {"pass": g3d, "long": long_leg, "short": short_leg},
        "G4_cost_sensitivity": {"pass": g4, "x0.5": c05, "x2.0": c20},
    }
    with open(out / f"{prefix}_gates.json", "w", encoding="utf-8") as f:
        json.dump(_to_native(gates), f, ensure_ascii=False, indent=2)
    print("\n=== 门禁判定 ===")
    for k, v in gates.items():
        print(f"  {k}: {'✅' if v['pass'] else '❌'}  {v}")


if __name__ == "__main__":
    main()
