"""逐合约执行模型（roll simulation）与 OI 主力连续重建。

预注册方案: docs/measurement_plan.md（2026-09-21 批准）
规则（定死）:
  - 每品种每日持有 open_interest 最大合约（与期限结构 F1 定义一致）；
  - 收益 = 持有合约的 close-to-close 真实收益（无拼接跳变，含展期收益）；
  - 换月 = OI 排名变化（T-1 决定、T 日生效，由回测引擎权重执行）；
  - 数据源：data/_raw_term/ 缓存（SHFE/CZCE/INE 20 品种；DCE 反爬不可得，范围局限）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant_futures_01 import config as cfgmod


def _raw_dir() -> Path:
    cfg = cfgmod.get_config()
    return Path(cfg.paths.data) / "_raw_term"


def symbol_raw(symbol: str, cfg=None) -> pd.DataFrame:
    """从缓存原始数据收集某品种的逐合约长表（date/symbol/close/open_interest）。"""
    c = cfg or cfgmod.get_config()
    u = next((x for x in c.universe if x["symbol"] == symbol), None)
    if u is None:
        raise ValueError(f"未知品种: {symbol}")
    prefix = symbol[:-1]  # RB0 → RB
    parts = []
    for f in sorted(_raw_dir().glob("*.parquet")):
        market = f.stem.split("_")[0]
        if market != u["exchange"]:
            continue
        df = pd.read_parquet(f)
        sub = df[df["variety"] == prefix][["date", "symbol", "close", "open_interest"]]
        if not sub.empty:
            parts.append(sub)
    if not parts:
        raise FileNotFoundError(
            f"{symbol} 无逐合约缓存（需先运行 scripts/fetch_termstructure.py）"
        )
    out = pd.concat(parts, ignore_index=True)
    # 日期稳健归一（int/str/datetime）
    s = out["date"].astype(str).str.strip()
    out["date"] = pd.to_datetime(s, format="%Y%m%d", errors="coerce").fillna(
        pd.to_datetime(s, errors="coerce")
    )
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out["open_interest"] = pd.to_numeric(out["open_interest"], errors="coerce")
    return out.dropna(subset=["date", "close", "open_interest"]).drop_duplicates(
        subset=["date", "symbol"]
    )


def contract_panel(symbol: str, cfg=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """宽表面板：(date × contract) 的 close 与 open_interest。"""
    df = symbol_raw(symbol, cfg)
    closes = df.pivot(index="date", columns="symbol", values="close").sort_index()
    ois = df.pivot(index="date", columns="symbol", values="open_interest").sort_index()
    return closes, ois


def oi_main_returns(
    closes: pd.DataFrame, ois: pd.DataFrame
) -> tuple[pd.Series, pd.Series]:
    """逐合约 OI 主力收益路径（无成本）与换月标记。

    收益[t] = 持有合约 close[t]/close[t-1] − 1（同合约，真实收益，无拼接跳变）；
    换月[t] = 当日 OI 最大合约 ≠ 前一日（T 日生效的新合约，其自身收益真实）。
    """
    main = ois.idxmax(axis=1)
    prev = closes.shift(1)
    rets = np.zeros(len(closes))
    for i, t in enumerate(closes.index):
        c = main.iloc[i]
        cur = closes.at[t, c]
        pv = prev.at[t, c]
        if pd.notna(pv) and pv > 0:
            rets[i] = cur / pv - 1.0
        # 合约上市首日无前收盘 → 收益 0（罕见）
    roll_flag = (main != main.shift(1)).astype(int)
    roll_flag.iloc[0] = 0
    return pd.Series(rets, index=closes.index), roll_flag


def reconstruct_continuous(returns: pd.Series) -> pd.Series:
    """由逐合约收益路径重建连续价格（锚定首日 = 100）。"""
    return 100.0 * (1.0 + returns.fillna(0.0)).cumprod()
