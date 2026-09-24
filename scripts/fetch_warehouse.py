"""SHFE 历史仓单管道：get_receipt 月批量拉取 → data/warehouse/{VAR}.parquet。

预注册方案: docs/stock_macro_plan.md M1（2026-09-21 批准）
用法:
    .venv/bin/python scripts/fetch_warehouse.py [--start 2015-01] [--pause 0.5]
产物:
    data/warehouse/{VAR}.parquet（date, receipt, receipt_chg；SHFE 13 品种 2015+）
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

SHFE_VARS = [
    "RB",
    "HC",
    "CU",
    "AL",
    "ZN",
    "NI",
    "SN",
    "SS",
    "AU",
    "AG",
    "RU",
    "FU",
    "BU",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    parser.add_argument("--start", default="2015-01", help="起始月（YYYY-MM）")
    parser.add_argument("--pause", type=float, default=0.5, help="请求间停顿秒数")
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)

    import akshare as ak  # 惰性导入

    months = pd.period_range(args.start, pd.Timestamp.now().strftime("%Y-%m"), freq="M")
    out_dir = Path(cfg.paths.data) / "warehouse"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[pd.DataFrame] = []
    for i, m in enumerate(months, 1):
        start = f"{m.start_time.strftime('%Y%m%d')}"
        end = f"{m.end_time.strftime('%Y%m%d')}"
        try:
            df = ak.get_receipt(start_date=start, end_date=end, vars_list=SHFE_VARS)
            if df is not None and len(df):
                df["date"] = pd.to_datetime(df["date"])
                all_rows.append(df[["var", "receipt", "receipt_chg", "date"]])
            print(
                f"[{i}/{len(months)}] {m}: {len(df) if df is not None else 0} 行",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001 —— 单月失败记录并继续
            print(
                f"[{i}/{len(months)}] {m}: ❌ {type(e).__name__} {str(e)[:80]}",
                flush=True,
            )
        time.sleep(args.pause)

    if not all_rows:
        raise RuntimeError("仓单拉取全部失败")
    raw = pd.concat(all_rows, ignore_index=True).sort_values(["var", "date"])
    for var, g in raw.groupby("var"):
        g[["date", "receipt", "receipt_chg"]].to_parquet(
            out_dir / f"{var}.parquet", index=False
        )
        print(
            f"  {var}: {len(g)} 行 {g['date'].min().date()} ~ {g['date'].max().date()}"
        )
    print(f"\n✅ 完成：{len(all_rows)} 个月 → data/warehouse/")


if __name__ == "__main__":
    main()
