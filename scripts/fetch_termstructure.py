"""拉取交易所逐合约日线 → 构建期限结构斜率因子（carry）。

预注册方案: docs/carry_plan.md P1（2026-09-20 批准）
用法:
    .venv/bin/python scripts/fetch_termstructure.py [--markets SHFE,CZCE,INE] [--years 2018-2026]
缓存:
    data/_raw_term/{MARKET}_{YEAR}.parquet（原始逐合约，可断点续跑）
产物:
    data/term_structure/{SYMBOL}_{variant}.parquet（20 品种 × 3 变体）
    output/termstructure_summary.csv
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 引导：仓库根下 src/ 可直接导入（无需安装）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd

from quant_futures_01 import config as cfgmod
from quant_futures_01.termstructure import (
    VARIANTS,
    build_slopes,
    carry_symbols,
    fetch_exchange_year,
    save_slope,
)

YEARS = list(range(2018, 2027))  # 2018-2026


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    parser.add_argument(
        "--markets",
        default="SHFE,CZCE,INE",
        help="交易所（逗号分隔，默认 SHFE,CZCE,INE）",
    )
    parser.add_argument(
        "--years", default=None, help="年份范围，如 2018-2026（默认全部）"
    )
    parser.add_argument("--pause", type=float, default=0.5, help="请求间停顿秒数")
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)

    markets = [m.strip().upper() for m in args.markets.split(",")]
    if args.years:
        a, b = args.years.split("-")
        years = list(range(int(a), int(b) + 1))
    else:
        years = YEARS

    raw_dir = Path(cfg.paths.data) / "_raw_term"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1) 拉取原始逐合约（缓存，可续跑） ----
    raw: dict[str, list[pd.DataFrame]] = {m: [] for m in markets}
    for m in markets:
        for y in years:
            cache = raw_dir / f"{m}_{y}.parquet"
            if cache.exists():
                raw[m].append(pd.read_parquet(cache))
                print(f"[cache] {m} {y}（{cache.name}）", flush=True)
                continue
            print(f"[fetch] {m} {y} ...", flush=True)
            df = fetch_exchange_year(m, y)
            df.to_parquet(cache, index=False)
            raw[m].append(df)
            time.sleep(args.pause)

    # ---- 2) 构建斜率因子 ----
    syms = carry_symbols(cfg)
    summary = []
    for u in syms:
        prefix = u["symbol"][:-1]  # RB0 → RB
        parts = [d[d["variety"] == prefix] for m in markets if m in raw for d in raw[m]]
        if not parts or all(p.empty for p in parts):
            print(f"  ⚠️ {u['symbol']} 无数据（variety={prefix}）", flush=True)
            continue
        daily = pd.concat(parts, ignore_index=True)
        for v in VARIANTS:
            slope_df = build_slopes(daily, v)
            save_slope(u["symbol"], slope_df, v)
            if v == "oi1":
                summary.append(
                    {
                        "symbol": u["symbol"],
                        "name": u["name"],
                        "sector": u["sector"],
                        "exchange": u["exchange"],
                        "rows": len(slope_df),
                        "first_date": str(slope_df["date"].iloc[0].date())
                        if len(slope_df)
                        else "",
                        "last_date": str(slope_df["date"].iloc[-1].date())
                        if len(slope_df)
                        else "",
                        "n_slope_pos": int((slope_df["slope"] > 0).sum())
                        if len(slope_df)
                        else 0,
                        "backwardation_pct": round(
                            float((slope_df["slope"] > 0).mean()) * 100, 2
                        )
                        if len(slope_df)
                        else float("nan"),
                    }
                )
    out = Path(cfg.paths.output)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summary).to_csv(out / "termstructure_summary.csv", index=False)
    print(f"\n✅ 完成：{len(summary)} 品种 × {len(VARIANTS)} 变体")
    print("摘要输出: output/termstructure_summary.csv")
    if summary:
        print(pd.DataFrame(summary).to_string(index=False))


if __name__ == "__main__":
    main()
