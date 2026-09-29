"""大商所期限结构管道：sina 逐合约 → 斜率（2018-09+，DCE 10 品种）。

预注册方案: docs/dce_data_plan.md M1（2026-09-21 批准）
背景: 大商所官方接口被 WAF 封锁；sina 逐合约对 2018-09 起合约有效。
用法:
    .venv/bin/python scripts/fetch_dce_term.py [--pause 0.1]
产物:
    data/_raw_dce/{SYM}.parquet      原始逐合约（date,symbol,close,open_interest,volume）
    data/term_structure/{SYM}_oi1.parquet  斜率（与 SHFE/CZCE/INE 同一定义/命名）
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
from quant_futures_01.termstructure import build_slopes, save_slope

DCE_SYMBOLS = ["I", "J", "JM", "L", "PP", "EG", "M", "Y", "P", "C", "JD"]
START_YEAR = 2018  # sina 逐合约约 2018-09 起


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    parser.add_argument("--pause", type=float, default=0.1, help="请求间停顿秒数")
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)

    import akshare as ak  # 惰性导入

    raw_dir = Path(cfg.paths.data) / "_raw_dce"
    raw_dir.mkdir(parents=True, exist_ok=True)
    years = list(range(START_YEAR, pd.Timestamp.now().year + 1))
    months = list(range(1, 13))

    for i, sym in enumerate(DCE_SYMBOLS, 1):
        rows: list[pd.DataFrame] = []
        for yy in years:
            for mm in months:
                code = f"{sym}{yy % 100:02d}{mm:02d}"
                try:
                    df = ak.futures_zh_daily_sina(symbol=code)
                    if df is None or df.empty:
                        continue
                    df = df.rename(columns={"hold": "open_interest"})
                    df["symbol"] = code
                    rows.append(
                        df[["date", "symbol", "close", "open_interest", "volume"]]
                    )
                except Exception:  # noqa: BLE001 —— 不存在的合约直接跳过
                    continue
                time.sleep(args.pause)
        if not rows:
            print(f"[{i}/{len(DCE_SYMBOLS)}] {sym}: 无有效合约 ❌", flush=True)
            continue
        raw = pd.concat(rows, ignore_index=True)
        raw.to_parquet(raw_dir / f"{sym}.parquet", index=False)
        slope = build_slopes(raw, "oi1")
        if slope.empty:
            print(f"[{i}/{len(DCE_SYMBOLS)}] {sym}: 斜率空 ❌", flush=True)
            continue
        save_slope(f"{sym}0", slope, "oi1")  # 与现有命名一致：I0_oi1.parquet
        print(
            f"[{i}/{len(DCE_SYMBOLS)}] {sym}0: {len(raw)} 合约行, 斜率 {len(slope)} 日 "
            f"{slope['date'].min().date()}~{slope['date'].max().date()} ✅",
            flush=True,
        )
    print("✅ 完成 → data/term_structure/{SYM}0_oi1.parquet")


if __name__ == "__main__":
    main()
