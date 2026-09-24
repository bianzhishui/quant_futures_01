"""组合筛选器：贱极 × 政策宽松（docs/screener_plan.md，2026-09-21 批准）。

功能:
  1) 组合历史验证：贱极∧宽松 vs 纯贱极 vs 贱极∧收紧 的 6m/12m 净超额；
  2) 当前市场扫描：全品种维度 + 政策/库存标注 + 组合候选池（供人工二次确认）。

用法:
    .venv/bin/python scripts/run_screener.py [--config ...]
产物:
    output/screener_stats.csv       分组超额统计
    output/screener_candidates.csv  当前全品种维度表 + 组合候选
    output/screener_gates.json      门禁判定
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 引导：仓库根下 src/ 可直接导入（无需安装）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from quant_futures_01 import config as cfgmod
from quant_futures_01.cost import CostModel
from quant_futures_01.cheap_extreme import (
    _parse_cn_month,
    _rolling_pct,
    drawdown_from_high,
    m2_policy_state,
    price_percentile,
    term_slope_percentile,
    vol_percentile,
    warehouse_low,
)
from quant_futures_01.termstructure import load_slope_panel
from backtest import load_adj_close_panel
from fetch_warehouse import SHFE_VARS

P_LO = 0.10  # 贱极分位门槛
DD_LO = -0.30  # 深跌门槛
MIN_GAP = 60  # 事件去重最小间隔
HOLDS = {"6m": 126, "12m": 252}
M2_WINDOW = 36  # 政策宽松：M2 同比 > 36 个月中位数


def _to_native(v):
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, (np.bool_, np.floating, np.integer)):
        return v.item()
    return v


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)
    out = Path(cfg.paths.output)
    out.mkdir(parents=True, exist_ok=True)
    one_side = CostModel(cfg).one_side_pct

    closes = load_adj_close_panel(cfg)
    slope_panel = load_slope_panel("oi1", cfg)
    sector_map = {u["symbol"]: u["sector"] for u in cfg.universe}

    # 宏观
    macro_path = Path(cfg.paths.data) / "macro" / "m2_monthly.csv"
    if not macro_path.exists():
        raise SystemExit("M2 数据缺失：先运行 scripts/fetch_macro.py")
    m2 = pd.read_csv(macro_path)

    # 仓单（SHFE）
    wh_dir = Path(cfg.paths.data) / "warehouse"
    stock_flags = {}
    wh_pct = {}
    for var in SHFE_VARS:
        p = wh_dir / f"{var}.parquet"
        if p.exists():
            rec = pd.read_parquet(p).set_index("date")["receipt"]
            stock_flags[var] = warehouse_low(rec)
            wh_pct[var] = _rolling_pct(rec, 756, 300)

    # ---- 维度 + 事件 ----
    idx = closes.index
    pos = {d: i for i, d in enumerate(idx)}
    events: list[dict] = []
    dims: dict[str, pd.DataFrame] = {}
    for sym in closes.columns:
        c = closes[sym]
        d1 = price_percentile(c)
        d2 = drawdown_from_high(c)
        d3 = vol_percentile(c.pct_change())
        d4 = (
            term_slope_percentile(slope_panel[sym])
            if sym in slope_panel.columns
            else pd.Series(np.nan, index=c.index)
        )
        dims[sym] = pd.DataFrame({"D1": d1, "D2": d2, "D3": d3, "D4": d4})
        hit = (d1 <= P_LO) & (d2 <= DD_LO)
        kept: list[pd.Timestamp] = []
        for d in hit.index[hit]:
            if not kept or pos[d] - pos[kept[-1]] >= MIN_GAP:
                kept.append(d)
        for t in kept:
            i = pos[t]
            for hn, h in HOLDS.items():
                j = i + h
                if j >= len(idx):
                    continue
                end = idx[j]
                r_sym = c.loc[end] / c.loc[t] - 1.0
                both = closes.loc[t].notna() & closes.loc[end].notna()
                r_bench = float(
                    (closes.loc[end, both] / closes.loc[t, both] - 1.0).mean()
                )
                events.append(
                    {
                        "symbol": sym,
                        "sector": sector_map.get(sym, ""),
                        "date": t,
                        "hold": hn,
                        "excess": r_sym - r_bench - 2.0 * one_side,
                    }
                )
    ev_df = pd.DataFrame(events)
    if ev_df.empty:
        raise SystemExit("无历史事件")

    # 政策状态 + 库存标注
    policy = m2_policy_state(m2, pd.DatetimeIndex(ev_df["date"]), window=M2_WINDOW)
    ev_df["loose"] = policy.values

    def _stock_low_at(var: str, t: pd.Timestamp):
        s = stock_flags.get(var)
        if s is None:
            return None
        hist = s[s.index <= t]
        return bool(hist.iloc[-1]) if len(hist) else None

    ev_df["stock_low"] = [
        _stock_low_at(r["symbol"][:-1], r["date"]) for _, r in ev_df.iterrows()
    ]

    # ---- 组合分组统计 ----
    stats_rows = []
    for label, mask in [
        ("组合(贱极∧宽松)", ev_df["loose"] == True),  # noqa: E712
        ("纯贱极(全部)", pd.Series(True, index=ev_df.index)),
        ("贱极∧收紧", ev_df["loose"] == False),  # noqa: E712
    ]:
        sub = ev_df[mask]
        if sub.empty:
            continue
        row = {"group": label}
        for hn in HOLDS:
            s = sub[sub["hold"] == hn]
            row[f"n_{hn}"] = len(s)
            row[f"excess_{hn}"] = (
                round(float(s["excess"].mean()), 4) if len(s) else None
            )
        stats_rows.append(row)
    stats_df = pd.DataFrame(stats_rows)
    stats_df.to_csv(out / "screener_stats.csv", index=False)
    print("=== 组合历史验证（净超额，扣往返成本）===")
    print(stats_df.to_string(index=False))

    # ---- 当前市场扫描 ----
    last = closes.index[-1]
    # 政策状态用最新已知 M2 月（当月 M2 通常滞后 1 个月发布）
    last_m2_date = pd.Timestamp(m2["month"].map(lambda s: _parse_cn_month(s)).max())
    last_m2 = m2_policy_state(
        m2, pd.DatetimeIndex([last_m2_date]), window=M2_WINDOW
    ).iloc[0]
    snap_rows = []
    for sym in closes.columns:
        d = dims[sym].loc[last]
        d1v = d["D1"] if pd.notna(d["D1"]) else None
        d2v = d["D2"] if pd.notna(d["D2"]) else None
        d4s = dims[sym]["D4"].dropna()
        d4v = d4s.iloc[-1] if len(d4s) else None
        var = sym[:-1]
        whp = wh_pct.get(var)
        whv = None
        if whp is not None:
            h = whp[whp.index <= last]
            whv = h.iloc[-1] if len(h) else None
        cheap = bool(
            d1v is not None and d2v is not None and d1v <= P_LO and d2v <= DD_LO
        )
        snap_rows.append(
            {
                "symbol": sym,
                "sector": sector_map.get(sym, ""),
                "D1_price_pct": round(float(d1v), 3) if d1v is not None else None,
                "D2_dd_from_high": round(float(d2v), 3) if d2v is not None else None,
                "D3_vol_pct": round(float(d["D3"]), 3) if pd.notna(d["D3"]) else None,
                "D4_tslope_pct": round(float(d4v), 3) if d4v is not None else None,
                "policy_loose": bool(last_m2) if last_m2 is not None else None,
                "wh_pct": round(float(whv), 3) if whv is not None else None,
                "is_cheap": cheap,
                "combo_candidate": bool(cheap and last_m2),
            }
        )
    snap_df = pd.DataFrame(snap_rows)
    snap_df.to_csv(out / "screener_candidates.csv", index=False)
    print(f"\n=== 当前市场扫描（{last.date()}）===")
    print(snap_df.to_string(index=False))
    combos = snap_df[snap_df["combo_candidate"]]
    print(f"\n当前组合候选（贱极∧宽松）: {len(combos)} 个")
    if len(combos):
        print(combos.to_string(index=False))
    else:
        print("  （无——等待信号，'静若处子'）")

    # ---- 门禁 ----
    combo = stats_df[stats_df["group"] == "组合(贱极∧宽松)"]
    pure = stats_df[stats_df["group"] == "纯贱极(全部)"]
    g2 = bool(
        len(combo)
        and len(pure)
        and combo["excess_6m"].iloc[0] is not None
        and pure["excess_6m"].iloc[0] is not None
        and combo["excess_12m"].iloc[0] is not None
        and pure["excess_12m"].iloc[0] is not None
        and combo["excess_6m"].iloc[0] > pure["excess_6m"].iloc[0]
        and combo["excess_12m"].iloc[0] > pure["excess_12m"].iloc[0]
    )
    g3 = bool(len(combo) and combo["n_6m"].iloc[0] >= 20)
    g4 = bool(
        len(combo)
        and combo["excess_6m"].iloc[0] is not None
        and combo["excess_12m"].iloc[0] is not None
        and combo["excess_6m"].iloc[0] > 0
        and combo["excess_12m"].iloc[0] > 0
    )
    gates = {
        "G2_combo_enhances": {
            "pass": g2,
            "combo_6m": combo["excess_6m"].iloc[0] if len(combo) else None,
            "pure_6m": pure["excess_6m"].iloc[0] if len(pure) else None,
            "combo_12m": combo["excess_12m"].iloc[0] if len(combo) else None,
            "pure_12m": pure["excess_12m"].iloc[0] if len(pure) else None,
        },
        "G3_combo_sample": {
            "pass": g3,
            "n_6m": combo["n_6m"].iloc[0] if len(combo) else 0,
        },
        "G4_combo_positive": {
            "pass": g4,
            "combo_6m": combo["excess_6m"].iloc[0] if len(combo) else None,
            "combo_12m": combo["excess_12m"].iloc[0] if len(combo) else None,
        },
    }
    with open(out / "screener_gates.json", "w", encoding="utf-8") as f:
        json.dump(_to_native(gates), f, ensure_ascii=False, indent=2)
    print("\n=== 门禁判定 ===")
    for k, v in gates.items():
        print(f"  {k}: {'✅' if v['pass'] else '❌'}  {v}")
    if g2 and g3 and g4:
        print("→ ✅ 组合筛选器正式采纳（含使用流程：定期扫描 → 人工二次确认 → 决策）")
    elif g2 or g4:
        print("→ 🟡 组合部分有效（标注）")
    else:
        print("→ ❌ 组合不成立，维持'贱极+政策状态标注'现状")


if __name__ == "__main__":
    main()
