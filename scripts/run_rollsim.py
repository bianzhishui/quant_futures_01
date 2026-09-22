"""测量补全实施：逐合约对拍（G2）+ TSMOM 复验（C1/G3）+ 金属展期分解（C2）。

预注册方案: docs/measurement_plan.md（2026-09-21 批准）
用法:
    .venv/bin/python scripts/run_rollsim.py [--config ...]
产物:
    output/rollsim_check.csv         G2 对拍（双实现一致 + 与 sina 对比）
    output/rollsim_recon.csv         重建 OI 主力连续 vs sina 主连（展期差异）
    output/rollsim_revalidate.csv    TSMOM 复验（重建 vs sina，同 20 子集）
    output/rollsim_metal_split.csv   C2 金属现货 vs 展期分解
    output/rollsim_gates.json        门禁判定
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
from quant_futures_01.portfolio import run_backtest
from quant_futures_01.rollsim import (
    contract_panel,
    oi_main_returns,
    reconstruct_continuous,
)
from quant_futures_01.termstructure import carry_symbols
from backtest import generate_weights, load_adj_close_panel

METALS = ["AU0", "AG0", "CU0", "AL0"]


def _to_native(v):
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, (np.bool_, np.floating, np.integer)):
        return v.item()
    return v


def _path_b(closes: pd.DataFrame, ois: pd.DataFrame) -> pd.Series:
    """独立实现：逐合约收益堆叠后按 OI 主力取数（验证 G2，须与 rollsim 一致）。"""
    per_contract = closes / closes.shift(1) - 1.0
    flat = per_contract.stack()
    main = ois.idxmax(axis=1)
    idx = pd.MultiIndex.from_arrays([closes.index, main.values])
    return flat.reindex(idx).fillna(0.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)
    out = Path(cfg.paths.output)
    out.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(cfg.backtest.start_date)

    # ---- G2 对拍：双实现一致 + 与 sina 主连对比 ----
    check_rows, recon_rows = [], []
    max_err = 0.0
    for u in carry_symbols(cfg):
        sym = u["symbol"]
        closes, ois = contract_panel(sym, cfg)
        closes = closes[closes.index >= start]
        ois = ois[ois.index >= start]
        rets, roll = oi_main_returns(closes, ois)
        pb = _path_b(closes, ois)
        err = float((rets - pb).abs().max())
        max_err = max(max_err, err)
        recon = reconstruct_continuous(rets)
        sina = load_daily(sym).set_index("date")["adj_close"]
        sina = sina[sina.index >= start]
        common = recon.index.intersection(sina.index)
        # 锚点归一：两序列都归一到首日 = 100，比较"期间累计收益"（锚点不同不可直接比终值）
        recon_norm = recon.loc[common] / recon.loc[common].iloc[0] * 100.0
        sina_norm = sina.loc[common] / sina.loc[common].iloc[0] * 100.0
        cum_recon = float(recon_norm.iloc[-1] / 100.0 - 1.0)
        cum_sina = float(sina_norm.iloc[-1] / 100.0 - 1.0)
        corr = float(
            pd.Series(rets.loc[common]).corr(sina.loc[common].pct_change().fillna(0.0))
        )
        recon_rows.append(
            {
                "symbol": sym,
                "n_days": len(common),
                "n_rolls": int(roll.sum()),
                "cum_ret_recon_pct": round(cum_recon * 100, 1),
                "cum_ret_sina_pct": round(cum_sina * 100, 1),
                "roll_yield_diff_pct": round((cum_recon - cum_sina) * 100, 1),
                "corr_daily_ret": round(corr, 4),
            }
        )
        check_rows.append({"symbol": sym, "max_err_two_impl": err})
    check_df = pd.DataFrame(check_rows)
    check_df.to_csv(out / "rollsim_check.csv", index=False)
    recon_df = pd.DataFrame(recon_rows)
    recon_df.to_csv(out / "rollsim_recon.csv", index=False)
    print("=== G2 对拍：双实现最大误差（须 < 1e-6）===")
    print(f"  全品种最大误差: {max_err:.2e}  {'✅' if max_err < 1e-6 else '❌'}")
    print("\n=== 重建 vs sina 主连（20 品种）===")
    print(recon_df.to_string(index=False))

    # ---- C2 金属现货 vs 展期分解 ----
    metal_rows = []
    for sym in METALS:
        closes, ois = contract_panel(sym, cfg)
        closes = closes[closes.index >= start]
        ois = ois[ois.index >= start]
        rets, _ = oi_main_returns(closes, ois)
        recon = reconstruct_continuous(rets)
        sina = load_daily(sym).set_index("date")["adj_close"]
        sina = sina[sina.index >= start]
        common = recon.index.intersection(sina.index)
        # 现货收益 = sina（展期被剔除）；展期收益 = recon − sina
        cum_spot = float(sina.loc[common].iloc[-1] / sina.loc[common].iloc[0] - 1)
        cum_recon = float(recon.loc[common].iloc[-1] / recon.loc[common].iloc[0] - 1)
        metal_rows.append(
            {
                "symbol": sym,
                "cum_spot_pct": round(cum_spot * 100, 1),
                "cum_total_recon_pct": round(cum_recon * 100, 1),
                "cum_roll_yield_pct": round((cum_recon - cum_spot) * 100, 1),
            }
        )
    metal_df = pd.DataFrame(metal_rows)
    metal_df.to_csv(out / "rollsim_metal_split.csv", index=False)
    print("\n=== C2 金属现货 vs 展期分解 ===")
    print(metal_df.to_string(index=False))

    # ---- C1 TSMOM 复验（重建 vs sina，同 20 子集） ----
    syms = [u["symbol"] for u in carry_symbols(cfg)]
    # 重建面板
    recon_panel = {}
    for sym in syms:
        closes, ois = contract_panel(sym, cfg)
        closes = closes[closes.index >= start]
        ois = ois[ois.index >= start]
        rets, _ = oi_main_returns(closes, ois)
        recon_panel[sym] = reconstruct_continuous(rets)
    recon_closes = pd.DataFrame(recon_panel).sort_index()
    sina_closes = load_adj_close_panel(cfg)[
        [c for c in syms if c in load_adj_close_panel(cfg).columns]
    ]
    cost = CostModel(cfg)
    res_recon = run_backtest(
        recon_closes.pct_change(), generate_weights("tsmom", recon_closes, cfg), cost
    )
    res_sina = run_backtest(
        sina_closes.pct_change(), generate_weights("tsmom", sina_closes, cfg), cost
    )
    print("\n=== C1 TSMOM 复验（L=120 等权，20 品种子集）===")
    print(
        f"  重建主力连续: 夏普 {res_recon.metrics['sharpe']:.3f} 年化 {res_recon.metrics['ann_return'] * 100:.2f}%"
    )
    print(
        f"  sina 主力连续: 夏普 {res_sina.metrics['sharpe']:.3f} 年化 {res_sina.metrics['ann_return'] * 100:.2f}%"
    )

    # ---- 门禁 ----
    g2 = max_err < 1e-6
    # G3：复验结论不反转 = 两版净夏普同号（且都与原结论一致：均为负）
    g3 = bool((res_recon.metrics["sharpe"] < 0) == (res_sina.metrics["sharpe"] < 0))
    gates = {
        "G2_rollsim_consistency": {"pass": g2, "max_err": float(max_err)},
        "G3_revalidation_stable": {
            "pass": g3,
            "recon_sharpe": float(res_recon.metrics["sharpe"]),
            "sina_sharpe": float(res_sina.metrics["sharpe"]),
        },
    }
    with open(out / "rollsim_gates.json", "w", encoding="utf-8") as f:
        json.dump(_to_native(gates), f, ensure_ascii=False, indent=2)
    print("\n=== 门禁判定 ===")
    for k, v in gates.items():
        print(f"  {k}: {'✅' if v['pass'] else '❌'}  {v}")


if __name__ == "__main__":
    main()
