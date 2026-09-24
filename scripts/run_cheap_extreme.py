"""贱极候选池扫描 + 历史事件验证（阶段一，docs/cheap_extreme_plan.md）。

用法:
    .venv/bin/python scripts/run_cheap_extreme.py [--config ...]
产物:
    output/cheap_extreme_snapshot.csv  当前市场各品种维度（D1-D5）与候选
    output/cheap_extreme_events.csv   历史入池事件 + 6/12 个月持有 vs 基准
    output/cheap_extreme_stats.json   统计与门禁
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
from quant_futures_01.cheap_extreme import (
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
MIN_GAP = 60  # 事件去重最小间隔（交易日）
HOLDS = {"6m": 126, "12m": 252}
W1_CUT = pd.Timestamp("2018-01-01")


def _to_native(v):
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, (np.bool_, np.floating, np.integer)):
        return v.item()
    return v


def _dedup_events(
    dates: pd.DatetimeIndex, idx: pd.DatetimeIndex, min_gap: int
) -> list[pd.Timestamp]:
    """事件去重：同品种相邻事件间隔 ≥ min_gap 交易日。"""
    kept: list[pd.Timestamp] = []
    pos = {d: i for i, d in enumerate(idx)}
    for d in dates:
        if not kept or pos[d] - pos[kept[-1]] >= min_gap:
            kept.append(d)
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)
    out = Path(cfg.paths.output)
    out.mkdir(parents=True, exist_ok=True)
    cost = CostModel(cfg)
    one_side = cost.one_side_pct

    closes = load_adj_close_panel(cfg)  # 2010-2026 全历史
    slope_panel = load_slope_panel("oi1", cfg)
    sector_map = {u["symbol"]: u["sector"] for u in cfg.universe}

    # ---- 维度计算 ----
    dims: dict[str, pd.DataFrame] = {}
    for sym in closes.columns:
        c = closes[sym]
        rets = c.pct_change()
        d1 = price_percentile(c)
        d2 = drawdown_from_high(c)
        d3 = vol_percentile(rets)
        d4 = (
            term_slope_percentile(slope_panel[sym])
            if sym in slope_panel.columns
            else pd.Series(np.nan, index=c.index)
        )
        dims[sym] = pd.DataFrame(
            {"D1_pct": d1, "D2_dd": d2, "D3_volpct": d3, "D4_tslope_pct": d4}
        )

    # ---- 当前快照 ----
    last = closes.index[-1]
    snap_rows = []
    for sym in closes.columns:
        d = dims[sym].loc[last]
        cand = bool(
            pd.notna(d["D1_pct"]) and d["D1_pct"] <= P_LO and d["D2_dd"] <= DD_LO
        )
        # D4 期限结构数据可能比主连日线晚 1-2 日（slope 末日在 closes 末日之前）：
        # 取最近可用的 D4（前向，避免快照显示全 None 的误导）
        d4 = dims[sym]["D4_tslope_pct"].dropna()
        d4v = d4.iloc[-1] if len(d4) else float("nan")
        snap_rows.append(
            {
                "symbol": sym,
                "sector": sector_map.get(sym, ""),
                "D1_price_pct": round(float(d["D1_pct"]), 3)
                if pd.notna(d["D1_pct"])
                else None,
                "D2_dd_from_high": round(float(d["D2_dd"]), 3)
                if pd.notna(d["D2_dd"])
                else None,
                "D3_vol_pct": round(float(d["D3_volpct"]), 3)
                if pd.notna(d["D3_volpct"])
                else None,
                "D4_tslope_pct": round(float(d4v), 3) if pd.notna(d4v) else None,
                "candidate": cand,
            }
        )
    snap_df = pd.DataFrame(snap_rows)
    snap_df.to_csv(out / "cheap_extreme_snapshot.csv", index=False)
    print(f"=== 当前市场快照（{last.date()}）===")
    print(snap_df.to_string(index=False))

    # ---- 历史事件 + 持有回测 ----
    idx = closes.index
    pos = {d: i for i, d in enumerate(idx)}
    event_rows = []
    for sym in closes.columns:
        d1 = dims[sym]["D1_pct"]
        d2 = dims[sym]["D2_dd"]
        hit = (d1 <= P_LO) & (d2 <= DD_LO)
        ev_dates = _dedup_events(hit.index[hit], idx, MIN_GAP)
        c = closes[sym]
        for t in ev_dates:
            i = pos[t]
            for hname, h in HOLDS.items():
                j = i + h
                if j >= len(idx):
                    continue
                end = idx[j]
                r_sym = c.loc[end] / c.loc[t] - 1.0
                # 基准：同时点有数据的全品种等权买入持有
                both = closes.loc[t].notna() & closes.loc[end].notna()
                r_bench = float(
                    (closes.loc[end, both] / closes.loc[t, both] - 1.0).mean()
                )
                event_rows.append(
                    {
                        "symbol": sym,
                        "sector": sector_map.get(sym, ""),
                        "event_date": t,
                        "hold": hname,
                        "event_ret": r_sym,
                        "bench_ret": r_bench,
                        "excess": r_sym - r_bench - 2.0 * one_side,
                    }
                )
    ev_df = pd.DataFrame(event_rows)
    ev_df.to_csv(out / "cheap_extreme_events.csv", index=False)
    if ev_df.empty:
        print("\n无入池事件（当前无贱极候选，历史也无）")
    else:
        stats_rows = []
        for hname in HOLDS:
            sub = ev_df[ev_df["hold"] == hname]
            if sub.empty:
                continue
            w1 = sub[sub["event_date"] < W1_CUT]
            w2 = sub[sub["event_date"] >= W1_CUT]
            stats_rows.append(
                {
                    "hold": hname,
                    "n_events": len(sub),
                    "mean_excess": round(float(sub["excess"].mean()), 4),
                    "mean_event_ret": round(float(sub["event_ret"].mean()), 4),
                    "mean_bench": round(float(sub["bench_ret"].mean()), 4),
                    "mean_excess_W1": round(float(w1["excess"].mean()), 4)
                    if len(w1)
                    else None,
                    "n_W1": len(w1),
                    "mean_excess_W2": round(float(w2["excess"].mean()), 4)
                    if len(w2)
                    else None,
                    "n_W2": len(w2),
                }
            )
        stats_df = pd.DataFrame(stats_rows)
        stats_df.to_csv(out / "cheap_extreme_stats.csv", index=False)
        print("\n=== 历史事件统计（净超额 = 事件收益 − 等权基准 − 往返成本）===")
        print(stats_df.to_string(index=False))
        # 板块分布
        sec_share = (
            ev_df.groupby(["hold", "sector"])
            .size()
            .groupby(level=0)
            .transform(lambda x: x / x.sum())
        )
        max_share = float(sec_share.max()) if len(sec_share) else 0.0
        print(f"\n单板块事件最大占比: {max_share:.1%}")

        # ---- 门禁 ----
        def _f(x):
            """None/NaN → None，否则 float（防 JSON/门禁崩溃）。"""
            if x is None or (isinstance(x, float) and np.isnan(x)):
                return None
            return float(x)

        s6 = stats_df[stats_df["hold"] == "6m"]
        s12 = stats_df[stats_df["hold"] == "12m"]
        w1v, w2v = (
            _f(s6["mean_excess_W1"].iloc[0]) if len(s6) else None,
            _f(s6["mean_excess_W2"].iloc[0]) if len(s6) else None,
        )
        g2 = bool(
            len(s6)
            and len(s12)
            and _f(s6["mean_excess"].iloc[0]) > 0
            and _f(s12["mean_excess"].iloc[0]) > 0
        )
        g3 = bool(
            len(s6) and w1v is not None and w2v is not None and (w1v > 0) == (w2v > 0)
        )
        g4 = bool(max_share <= 0.60)
        gates = {
            "G2_excess_positive_6m_12m": {
                "pass": g2,
                "mean_excess_6m": _f(s6["mean_excess"].iloc[0]) if len(s6) else None,
                "mean_excess_12m": _f(s12["mean_excess"].iloc[0]) if len(s12) else None,
            },
            "G3_subperiod_same_sign": {"pass": g3, "W1": w1v, "W2": w2v},
            "G4_sector_diversified": {"pass": g4, "max_sector_share": max_share},
        }
        with open(out / "cheap_extreme_gates.json", "w", encoding="utf-8") as f:
            json.dump(_to_native(gates), f, ensure_ascii=False, indent=2)
        print("\n=== 门禁判定 ===")
        for k, v in gates.items():
            print(f"  {k}: {'✅' if v['pass'] else '❌'}  {v}")
        if g2 and g3 and g4:
            print("→ ✅ 贱极代理有统计边际，作为筛选器纳入（供人工确认）")
        elif g2 or g3:
            print("→ 🟡 部分通过（标注 regime 依赖等）")
        else:
            print("→ ❌ 价格极低分位代理无边际，止步于扫描工具")

        # ============ 阶段二：V1 政策转向 / V2 库存低位（docs/stock_macro_plan.md） ============
        # 宏观状态
        macro_path = Path(cfg.paths.data) / "macro" / "m2_monthly.csv"
        if macro_path.exists():
            m2 = pd.read_csv(macro_path)
            policy = m2_policy_state(m2, pd.DatetimeIndex(ev_df["event_date"]))
            ev_df["policy_loose"] = policy.values
            # 仓单库存（SHFE 品种）
            wh_dir = Path(cfg.paths.data) / "warehouse"
            stock_flags = {}
            for var in SHFE_VARS:
                p = wh_dir / f"{var}.parquet"
                if p.exists():
                    wh = pd.read_parquet(p)
                    rec = wh.set_index("date")["receipt"]
                    stock_flags[var] = warehouse_low(rec)

            def _stock_low(row):
                var = row["symbol"][:-1]
                s = stock_flags.get(var)
                if s is None:
                    return None
                hist = s[s.index <= row["event_date"]]
                return bool(hist.iloc[-1]) if len(hist) else None

            ev_df["stock_low"] = ev_df.apply(_stock_low, axis=1)
            # V1 政策分组
            v1_rows = []
            for state, label in [(True, "宽松"), (False, "收紧")]:
                sub = ev_df[ev_df["policy_loose"] == state]
                if sub.empty:
                    continue
                v1_rows.append(
                    {
                        "policy": label,
                        "n_events_6m": int((sub["hold"] == "6m").sum()),
                        "excess_6m": round(
                            float(sub[sub["hold"] == "6m"]["excess"].mean()), 4
                        )
                        if (sub["hold"] == "6m").any()
                        else None,
                        "excess_12m": round(
                            float(sub[sub["hold"] == "12m"]["excess"].mean()), 4
                        )
                        if (sub["hold"] == "12m").any()
                        else None,
                    }
                )
            v1_df = pd.DataFrame(v1_rows)
            v1_df.to_csv(out / "stage2_policy.csv", index=False)
            print("\n=== V1 政策转向分组（M2 宽松 vs 收紧）===")
            print(v1_df.to_string(index=False))
            # V2 库存分组（SHFE 品种）
            sub_wh = ev_df[ev_df["stock_low"].notna()]
            if len(sub_wh):
                v2_rows = []
                for low, label in [(True, "库存低位"), (False, "库存非低")]:
                    s2 = sub_wh[sub_wh["stock_low"] == low]
                    if s2.empty:
                        continue
                    v2_rows.append(
                        {
                            "stock": label,
                            "n_events_6m": int((s2["hold"] == "6m").sum()),
                            "excess_6m": round(
                                float(s2[s2["hold"] == "6m"]["excess"].mean()), 4
                            )
                            if (s2["hold"] == "6m").any()
                            else None,
                            "excess_12m": round(
                                float(s2[s2["hold"] == "12m"]["excess"].mean()), 4
                            )
                            if (s2["hold"] == "12m").any()
                            else None,
                        }
                    )
                v2_df = pd.DataFrame(v2_rows)
                v2_df.to_csv(out / "stage2_stock.csv", index=False)
                print(
                    "\n=== V2 库存分组（仓单滚动 3 年分位 ≤20% = 低位，SHFE 品种）==="
                )
                print(v2_df.to_string(index=False))
                loose6 = (
                    v1_df[v1_df["policy"] == "宽松"]["excess_6m"].iloc[0]
                    if len(v1_df[v1_df["policy"] == "宽松"])
                    else None
                )
                tight6 = (
                    v1_df[v1_df["policy"] == "收紧"]["excess_6m"].iloc[0]
                    if len(v1_df[v1_df["policy"] == "收紧"])
                    else None
                )
                low6 = (
                    v2_df[v2_df["stock"] == "库存低位"]["excess_6m"].iloc[0]
                    if len(v2_df[v2_df["stock"] == "库存低位"])
                    else None
                )
                nonlow6 = (
                    v2_df[v2_df["stock"] == "库存非低"]["excess_6m"].iloc[0]
                    if len(v2_df[v2_df["stock"] == "库存非低"])
                    else None
                )
                g2 = bool(
                    loose6 is not None
                    and tight6 is not None
                    and loose6 > tight6
                    and loose6 > 0
                )
                g3 = bool(
                    low6 is not None
                    and nonlow6 is not None
                    and low6 > nonlow6
                    and low6 > 0
                )
                stage2_gates = {
                    "G2_policy_loose_enhances": {
                        "pass": g2,
                        "loose_6m": loose6,
                        "tight_6m": tight6,
                    },
                    "G3_stock_low_enhances": {
                        "pass": g3,
                        "low_6m": low6,
                        "nonlow_6m": nonlow6,
                    },
                }
                with open(out / "stage2_gates.json", "w", encoding="utf-8") as f:
                    json.dump(_to_native(stage2_gates), f, ensure_ascii=False, indent=2)
                print("\n=== 阶段二门禁 ===")
                for k, v in stage2_gates.items():
                    print(f"  {k}: {'✅' if v['pass'] else '❌'}  {v}")


if __name__ == "__main__":
    main()
