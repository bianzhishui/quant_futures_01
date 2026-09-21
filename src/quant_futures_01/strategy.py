"""动量族策略信号：时序动量（TSMOM）方向 + 横截面动量（XSMOM）权重。

预注册方案: docs/tsmom_plan.md / docs/xsmom_plan.md（2026-09-20 批准）
共同规则（定死）:
  - 信号在 T-1 收盘决定（无前视），T 日生效（close-to-close，由回测引擎执行）；
  - 历史不足（前 L+1 日）→ 信号 0（空仓，预热期）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def tsmom_signal(closes: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """时序动量方向信号矩阵（+1 多 / −1 空 / 0 平）。

    closes: 后复权收盘价面板（index=日期，columns=品种）。
    返回与 closes 同形状的 sig 矩阵；无前视（用截至 T-1 的 L 日收益）。
    """
    # pct_change(L) 在第 t 行 = close[t]/close[t-L]−1（用到 t 收盘）；
    # shift(1) 后第 t 行 = close[t-1]/close[t-1-L]−1（T-1 收盘决定）✓
    rL = closes.pct_change(lookback).shift(1)
    sig = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    sig[rL > 0] = 1.0
    sig[rL < 0] = -1.0
    # NaN（数据不足/上市前）→ 保持 0
    return sig


def xsmom_weights(
    closes: pd.DataFrame, lookback: int, quantile: float = 0.25
) -> pd.DataFrame:
    """横截面动量权重（T-1 决定，T 生效；无前视）。

    预注册方案: docs/xsmom_plan.md（2026-09-20 批准）
    规则（定死）:
      - 排序收益 rL_i[t] = adj_close[t-1]/adj_close[t-1-L] − 1；
      - 当日可交易品种内截面分位：rL ≥ 1−quantile → 多头，≤ quantile → 空头，中间 → 0；
      - 多头腿内等权（总名义 +0.5）、空头腿内等权（总名义 −0.5），dollar-neutral；
      - 上市前/数据不足（rL NaN）不参与排名（权重 0）。
    """
    rL = closes.pct_change(lookback).shift(1)
    pct = rL.rank(axis=1, pct=True)  # NaN 保留 NaN（不参与排名）
    long_mask = pct >= (1.0 - quantile)
    short_mask = pct <= quantile
    n_long = long_mask.sum(axis=1).replace(0, np.nan)
    n_short = short_mask.sum(axis=1).replace(0, np.nan)
    w = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    w = w + long_mask.mul(0.5 / n_long, axis=0) - short_mask.mul(0.5 / n_short, axis=0)
    return w.fillna(0.0)
