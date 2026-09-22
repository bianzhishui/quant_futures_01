"""组合/风控层评估：基准 V vs V2（协方差波动目标）vs V3（+回撤减仓）+ 诊断。

预注册方案: docs/portfolio_risk_plan.md（2026-09-21 批准）
用法:
    .venv/bin/python scripts/run_risk_layer.py [--config ...]
产物:
    output/risk_layer_compare.csv    V / V2 / V3 指标对比
    output/risk_layer_sensitivity.csv  阈值/窗口灵敏度（诊断）
    output/risk_layer_shortcap.csv    空头约束诊断（C3）
    output/risk_layer_gates.json      门禁判定
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
    drawdown_scale,
    run_backtest,
    vol_target_weights,
)
from backtest import load_adj_close_panel

METRICS = [
    "final_nav",
    "ann_return",
    "ann_vol",
    "sharpe",
    "max_drawdown",
    "calmar",
    "turnover_daily",
    "cost_drag_ann",
]


def _to_native(v):
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, (np.bool_, np.floating, np.integer)):
        return v.item()
    return v


def _run(returns, weights, cost):
    res = run_backtest(returns, weights, cost)
    return {k: res.metrics[k] for k in METRICS}


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

    # ---- V / V2 / V3 ----
    w_v = vol_target_weights(returns, cfg)
    w_v2 = cov_vol_target_weights(returns, cfg)
    res_v2 = run_backtest(returns, w_v2, cost)
    scale = drawdown_scale(res_v2.returns)  # 回撤减仓基于 V2 净值（无前视，T-1 决定）
    w_v3 = w_v2.mul(scale, axis=0)
    rows = {
        "V_linear": _run(returns, w_v, cost),
        "V2_cov": _run(returns, w_v2, cost),
        "V3_cov_dd": _run(returns, w_v3, cost),
    }
    cmp_df = pd.DataFrame(rows).T
    cmp_df.to_csv(out / "risk_layer_compare.csv")
    print("=== V / V2 / V3 对比 ===")
    print(cmp_df.round(4).to_string())

    # ---- 灵敏度诊断（阈值/窗口） ----
    sens = []
    for dd_mid in [0.096, 0.12, 0.144]:
        s = drawdown_scale(res_v2.returns, dd_mid=dd_mid)
        m = _run(returns, w_v2.mul(s, axis=0), cost)
        sens.append(
            {
                "param": f"dd_mid={dd_mid}",
                **{k: m[k] for k in ["ann_vol", "sharpe", "max_drawdown", "calmar"]},
            }
        )
    for win in [40, 80]:
        cfg2 = cfgmod.Config(
            {
                **cfg.to_dict(),
                "backtest": {**cfg.to_dict()["backtest"], "vol_window": win},
            }
        )
        w2 = cov_vol_target_weights(returns, cfg2)
        m = _run(returns, w2, cost)
        sens.append(
            {
                "param": f"cov_window={win}",
                **{k: m[k] for k in ["ann_vol", "sharpe", "max_drawdown", "calmar"]},
            }
        )
    sens_df = pd.DataFrame(sens)
    sens_df.to_csv(out / "risk_layer_sensitivity.csv", index=False)
    print("\n=== 灵敏度诊断（仅报告）===")
    print(sens_df.round(4).to_string(index=False))

    # ---- C3 空头约束诊断（基准 B sma20，空头名义 ≤30%） ----
    w_b = (
        pd.DataFrame(
            np.where(closes > closes.rolling(20).mean(), 1.0, -1.0) / closes.shape[1],
            index=closes.index,
            columns=closes.columns,
        )
        .shift(1)
        .fillna(0.0)
    )
    short_abs = w_b.clip(upper=0.0).abs().sum(axis=1)
    cap = short_abs.clip(upper=0.30)
    w_cap = w_b.clip(upper=0.0).mul(
        (cap / short_abs.replace(0, np.nan)).fillna(1.0), axis=0
    ) + w_b.clip(lower=0.0)
    short_rows = {
        "B_sma20": _run(returns, w_b, cost),
        "B_shortcap30": _run(returns, w_cap, cost),
    }
    short_df = pd.DataFrame(short_rows).T
    short_df.to_csv(out / "risk_layer_shortcap.csv")
    print("\n=== C3 空头约束诊断（仅报告）===")
    print(short_df.round(4).to_string())

    # ---- 门禁 ----
    v = rows["V_linear"]
    v2 = rows["V2_cov"]
    v3 = rows["V3_cov_dd"]
    g1 = (12.0 <= v2["ann_vol"] * 100 <= 18.0) and (12.0 <= v3["ann_vol"] * 100 <= 18.0)
    g2 = (
        abs(v3["max_drawdown"]) <= abs(v["max_drawdown"]) * 0.8
        and v3["calmar"] >= v["calmar"]
    )
    gates = {
        "G1_vol_in_band_12_18": {
            "pass": g1,
            "V": round(v["ann_vol"] * 100, 1),
            "V2": round(v2["ann_vol"] * 100, 1),
            "V3": round(v3["ann_vol"] * 100, 1),
        },
        "G2_dd_overlay_value": {
            "pass": g2,
            "V_mdd": round(v["max_drawdown"], 4),
            "V3_mdd": round(v3["max_drawdown"], 4),
            "V_calmar": round(v["calmar"], 4),
            "V3_calmar": round(v3["calmar"], 4),
        },
    }
    with open(out / "risk_layer_gates.json", "w", encoding="utf-8") as f:
        json.dump(_to_native(gates), f, ensure_ascii=False, indent=2)
    print("\n=== 门禁判定 ===")
    for k, vv in gates.items():
        print(f"  {k}: {'✅' if vv['pass'] else '❌'}  {vv}")
    if g1 and g2:
        print("  → 组合层升级采纳为默认风控框架（V3 入基准体系）")
    elif g1 or g2:
        print("  → 部分采纳（注明未过项）")
    else:
        print("  → 风控层不增价值，维持现状（如实记录）")


if __name__ == "__main__":
    main()
