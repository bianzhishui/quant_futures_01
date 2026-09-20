"""拉取期货主力连续日线 → 清洗 → 后复权 → parquet 落盘 + 数据质量摘要。

预注册方案: docs/infrastructure_plan.md M2（2026-09-20 批准）
用法:
    .venv/bin/python scripts/fetch_data.py [--config config/custom.yaml] [--symbols RB0,CU0]
产物:
    data/futures_main_daily/{SYMBOL}.parquet   （含 adj_close/adj_factor/is_roll/r_adj）
    output/data_summary.csv                     （每品种：行数/区间/复权跳变统计）
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 引导：仓库根下 src/ 可直接导入（无需安装）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd

from quant_futures import config as cfgmod
from quant_futures.data import (
    back_adjust,
    clean_daily,
    fetch_akshare_main_daily,
    main_daily_dir,
    save_daily,
)


def fetch_one(symbol: str, name: str, sector: str, cfg) -> dict:
    """单品种：拉取 → 清洗 → 后复权 → 落盘，返回质量摘要行。"""
    df = fetch_akshare_main_daily(symbol, cfg.data.start_date)
    raw = clean_daily(df)
    adj = back_adjust(raw, cfg.data.adjust_threshold)
    save_daily(symbol, adj)
    r = adj["adj_close"].pct_change().dropna()
    return {
        "symbol": symbol,
        "name": name,
        "sector": sector,
        "rows": len(adj),
        "first_date": str(adj["date"].iloc[0].date()),
        "last_date": str(adj["date"].iloc[-1].date()),
        "n_roll_days": int(adj["is_roll"].sum()),
        "max_abs_ret_pct": round(float(r.abs().max()) * 100, 2),
        "pct_days_gt2pct": round(float((r.abs() > 0.02).mean()) * 100, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    parser.add_argument(
        "--symbols", default=None, help="仅拉取指定品种（逗号分隔，默认全部）"
    )
    parser.add_argument(
        "--pause", type=float, default=None, help="请求间停顿秒数（默认取配置）"
    )
    args = parser.parse_args()

    cfg = cfgmod.load_config(args.config)
    pause = args.pause if args.pause is not None else cfg.fetch.sample.pause

    uni = cfg.universe
    if args.symbols:
        want = {s.strip().upper() for s in args.symbols.split(",")}
        uni = [u for u in uni if u["symbol"].upper() in want]

    rows: list[dict] = []
    fails: list[str] = []
    for i, u in enumerate(uni, 1):
        print(
            f"[{i}/{len(uni)}] {u['symbol']} {u['name']} ({u['sector']}) ...",
            flush=True,
        )
        try:
            rows.append(fetch_one(u["symbol"], u["name"], u["sector"], cfg))
        except Exception as e:  # noqa: BLE001 —— 单品种失败记录并继续，摘要如实呈现
            fails.append(f"{u.symbol}: {type(e).__name__}: {str(e)[:120]}")
            print(f"    ❌ {fails[-1]}", flush=True)
        time.sleep(pause)

    out = Path(cfg.paths.output)
    out.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(rows)
    summary.to_csv(out / "data_summary.csv", index=False)
    print("\n数据落盘目录:", main_daily_dir())
    print("摘要输出: output/data_summary.csv")
    if len(summary):
        print(summary.to_string(index=False))
    if fails:
        print(f"\n⚠️ {len(fails)} 个品种失败（未落盘）:")
        for f in fails:
            print("  ", f)


if __name__ == "__main__":
    main()
