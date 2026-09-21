"""时序动量（TSMOM）方向信号。

预注册方案: docs/tsmom_plan.md（2026-09-20 批准）
规则（定死）:
  - 信号在 T-1 收盘决定（无前视）：
      rL_i[t] = adj_close_i[t-1] / adj_close_i[t-1-L] − 1
      sig_i[t] = +1（rL > 0）／ −1（rL < 0）／ 0（rL = 0 或数据不足）
  - T 日生效（close-to-close，由回测引擎执行）；
  - 历史不足（前 L+1 日）→ 信号 0（空仓，预热期）。
"""

from __future__ import annotations

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
