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


def _shfe_receipt_day_robust(date_str: str, vars_list: list[str]) -> pd.DataFrame:
    """SHFE 单日仓单健壮解析（回退路径）。

    akshare `get_shfe_receipt_3` 在 2026-06 起有两个 bug：
      1) `int('2961000.0000')` 数值为浮点串 → 崩溃（本函数用 int(float())）；
      2) `chinese_to_english('20号胶(仓库)')` 映射表缺新增品类 → 崩溃（本函数跳过未知品类）。
    """
    from io import StringIO

    import requests
    from akshare.futures import cons

    try:
        from akshare.futures.cons import chinese_to_english
    except ImportError:  # 兼容旧版
        from akshare.futures.receipt import chinese_to_english

    url = (
        "https://www.shfe.com.cn/data/tradedata/future/stockdata/"
        f"dailystock_{date_str}/ZH/all.html"
    )
    r = requests.get(url, headers=cons.shfe_headers, timeout=30)
    tabs = pd.read_html(StringIO(r.text))
    recs = []
    for t in tabs[1:]:
        try:
            name = chinese_to_english(str(t.iloc[0, 1]))
        except Exception:  # noqa: BLE001 —— 未知/新增品类（不在品种池）跳过
            continue
        recs.append(
            {
                "var": name,
                "receipt": int(float(t.iloc[-1, 2])),
                "receipt_chg": int(float(t.iloc[-1, 3])),
            }
        )
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    df = df.groupby("var", as_index=False)[["receipt", "receipt_chg"]].sum()
    df["date"] = pd.to_datetime(date_str)
    return df[df["var"].isin(vars_list)]


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
        except Exception as e:  # noqa: BLE001 —— akshare 失败 → 逐日健壮回退
            rows2 = []
            for d in pd.bdate_range(start=m.start_time, end=m.end_time):
                try:
                    dd = _shfe_receipt_day_robust(d.strftime("%Y%m%d"), SHFE_VARS)
                    if len(dd):
                        rows2.append(dd)
                except Exception:  # noqa: BLE001 —— 非交易日/取数失败跳过
                    continue
                time.sleep(args.pause)
            if rows2:
                all_rows.append(pd.concat(rows2, ignore_index=True))
            print(
                f"[{i}/{len(months)}] {m}: akshare ❌ {type(e).__name__} → "
                f"回退 {len(rows2)} 日 {'✅' if rows2 else '❌'}",
                flush=True,
            )
        time.sleep(args.pause)

    if not all_rows:
        raise RuntimeError("仓单拉取全部失败")
    raw = pd.concat(all_rows, ignore_index=True).sort_values(["var", "date"])
    for var, g in raw.groupby("var"):
        p = out_dir / f"{var}.parquet"
        new = g[["date", "receipt", "receipt_chg"]].copy()
        new["date"] = pd.to_datetime(new["date"])
        if p.exists():  # 增量合并（--start 指定新月份时不覆盖历史）
            old = pd.read_parquet(p)
            old["date"] = pd.to_datetime(old["date"])
            new = (
                pd.concat([old, new], ignore_index=True)
                .drop_duplicates(subset="date", keep="last")
                .sort_values("date")
            )
        new.to_parquet(p, index=False)
        print(
            f"  {var}: {len(new)} 行 {new['date'].min().date()} ~ {new['date'].max().date()}"
        )
    print(f"\n✅ 完成：{len(all_rows)} 个月 → data/warehouse/")


if __name__ == "__main__":
    main()
