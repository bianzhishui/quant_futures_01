"""贱极候选池维度计算（阶段一，docs/cheap_extreme_plan.md）。

规则（定死）:
  - D1 价格分位：现价在近 5 年（1260 交易日）滚动窗口的分位（后复权价）；
  - D2 深跌：距 250 日滚动高点回撤；
  - D3 波动位置：60 日年化波动的历史分位（情绪标注）；
  - D4 期限结构位置：斜率的历史分位（contango 深 = 悲观标注，20 品种）。
入池硬门槛：D1 ≤ 0.10 且 D2 ≥ 0.30。

注意：面板列（联合索引）含 NaN 空隙，rolling 须显式 min_periods（按非 NaN 计）并在
窗口内剔除 NaN 再算分位，否则整段返回 NaN。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PCT_WINDOW = 1260  # 近 5 年交易日
PCT_MIN = 500  # 至少 2 年非 NaN 历史才计算分位
DD_WINDOW = 250  # 深跌回撤窗口
VOL_WINDOW = 60


def _rolling_pct(close: pd.Series, window: int, min_periods: int) -> pd.Series:
    """滚动分位：现价在窗口内非 NaN 过去值中的分位（0~1；不足/当日 NaN → NaN）。"""

    def _pct(x: np.ndarray) -> float:
        cur = x[-1]
        if not np.isfinite(cur):
            return float("nan")
        past = x[:-1][np.isfinite(x[:-1])]
        if len(past) < min_periods - 1:
            return float("nan")
        return float((past <= cur).mean())

    return close.rolling(window, min_periods=min_periods).apply(_pct, raw=True)


def price_percentile(
    close: pd.Series, window: int = PCT_WINDOW, min_periods: int = PCT_MIN
) -> pd.Series:
    """现价在滚动窗口内的分位（0~1；窗口不足 → NaN）。"""
    return _rolling_pct(close, window, min_periods)


def drawdown_from_high(close: pd.Series, window: int = DD_WINDOW) -> pd.Series:
    """距滚动高点回撤（0~1 负值；窗口不足 → NaN）。"""
    roll_max = close.rolling(window, min_periods=20).max()
    return close / roll_max - 1.0


def vol_percentile(
    returns: pd.Series, vol_window: int = VOL_WINDOW, hist_window: int = PCT_WINDOW
) -> pd.Series:
    """60 日年化波动的历史分位（0~1；历史不足 → NaN）。"""
    vol = returns.rolling(vol_window, min_periods=20).std(ddof=0) * np.sqrt(252)
    return _rolling_pct(vol, hist_window, min_periods=200)


def term_slope_percentile(slope: pd.Series, window: int = PCT_WINDOW) -> pd.Series:
    """期限结构斜率的历史分位（低分位 = 深度 contango/悲观）。"""
    return _rolling_pct(slope, window, PCT_MIN)
