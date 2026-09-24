"""宏观信贷管道：M2 月度（2008+）→ data/macro/m2_monthly.csv。

预注册方案: docs/stock_macro_plan.md M1（2026-09-21 批准）
产物: data/macro/m2_monthly.csv（月份, m2_level, m2_yoy）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 引导：仓库根下 src/ 可直接导入（无需安装）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd

from quant_futures_01 import config as cfgmod


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cfgmod.add_config_arg(parser)
    args = parser.parse_args()
    cfg = cfgmod.load_config(args.config)

    import akshare as ak  # 惰性导入

    df = ak.macro_china_money_supply()
    if df is None or df.empty:
        raise RuntimeError("macro_china_money_supply 返回空")
    out = pd.DataFrame(
        {
            "month": df["月份"],
            "m2_level": pd.to_numeric(
                df["货币和准货币(M2)-数量(亿元)"], errors="coerce"
            ),
            "m2_yoy": pd.to_numeric(df["货币和准货币(M2)-同比增长"], errors="coerce"),
        }
    ).dropna(subset=["month"])
    d = Path(cfg.paths.data) / "macro"
    d.mkdir(parents=True, exist_ok=True)
    out.to_csv(d / "m2_monthly.csv", index=False)
    print(
        f"M2 月度: {len(out)} 行, {out['month'].iloc[-1]} ~ {out['month'].iloc[0]} → data/macro/m2_monthly.csv"
    )


if __name__ == "__main__":
    main()
