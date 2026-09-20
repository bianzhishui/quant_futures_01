"""期货主连日线数据管道：akshare 拉取 → 清洗 → 后复权拼接 → parquet 落盘/加载。

预注册方案: docs/infrastructure_plan.md §3.2（2026-09-20 批准）
规则（定死）:
  - 日线，自 config.data.start_date 起；
  - 停牌/无交易行删除；价格 NaN 或非正 → 报错，不静默填充；
  - 后复权：单日 |收益| > adjust_threshold 视为换月跳变，该日复权收益置 0（透明可复现）；
  - 复权因子随数据落盘（{symbol}_adj.parquet），原始数据落盘（{symbol}.parquet）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant_futures import config as cfgmod

# 标准列名 → akshare/sina 可能的原始列名（英文/中文兼容）
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "日期", "交易日"),
    "open": ("open", "开盘", "开盘价"),
    "high": ("high", "最高", "最高价"),
    "low": ("low", "最低", "最低价"),
    "close": ("close", "收盘", "收盘价"),
    "volume": ("volume", "vol", "成交量"),
    "hold": ("hold", "持仓量"),
    "settle": ("settle", "结算", "结算价"),
}


def main_daily_dir() -> Path:
    """主连日线 parquet 根目录（CWD 无关）。"""
    cfg = cfgmod.get_config()
    return Path(cfg.paths.data) / cfg.data.main_dir


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """列名归一化（兼容英文/中文列名）→ 标准列名子集。"""
    rename: dict[str, str] = {}
    for std, names in _COLUMN_ALIASES.items():
        for n in names:
            if n in df.columns:
                rename[n] = std
                break
    if "close" not in set(rename.values()):
        raise ValueError(f"缺少收盘价列，实际列: {list(df.columns)}")
    out = df.rename(columns=rename)
    keep = [
        c
        for c in ("date", "open", "high", "low", "close", "volume", "hold", "settle")
        if c in out.columns
    ]
    return out[keep]


def clean_daily(df: pd.DataFrame) -> pd.DataFrame:
    """清洗：列归一化 → 类型转换 → 去重 → 排序 → NaN/非正检查。

    权威数据缺失要暴露：价格 NaN 或 ≤ 0 → 报错，不静默填充。
    """
    df = _normalize_columns(df).copy()
    if "date" not in df.columns:
        raise ValueError(f"缺少日期列，实际列: {list(df.columns)}")
    df["date"] = pd.to_datetime(df["date"])
    price_cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
    for c in price_cols:
        df[c] = pd.to_numeric(df[c], errors="raise")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    if "hold" in df.columns:
        df["hold"] = pd.to_numeric(df["hold"], errors="coerce")
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    bad = df[price_cols].isna() | (df[price_cols] <= 0)
    if bad.any().any():
        n_bad = int(bad.any(axis=1).sum())
        raise ValueError(f"价格存在 NaN 或非正值（{n_bad} 行），拒绝落盘")
    return df


def back_adjust(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """后复权：消除主力连续换月跳变（锚定最新收盘价，向前累积）。

    规则（定死，透明可复现）:
      1. 原始日收益 r[t] = close[t] / close[t-1] - 1（close-to-close）；
      2. |r[t]| > threshold → 视为换月跳变：复权收益 r_adj[t] = 0，标记 is_roll；
      3. adj_close 末值锚定：adj_close[t] = adj_close[t+1] / (1 + r_adj[t+1])；
      4. 复权因子 adj_factor[t] = adj_close[t] / close[t]。

    输出附加列: adj_close / adj_factor / is_roll / r_adj。
    """
    out = df.copy()
    close = out["close"].to_numpy(dtype=float)
    n = len(close)
    adj_close = np.empty(n)
    r_adj = np.zeros(n)
    is_roll = np.zeros(n, dtype=bool)
    adj_close[-1] = close[-1]  # 锚定最新价，最后一期因子 = 1
    for t in range(n - 2, -1, -1):
        r_raw = close[t + 1] / close[t] - 1.0
        if abs(r_raw) > threshold:
            is_roll[t + 1] = True
            r_adj[t + 1] = 0.0
        else:
            r_adj[t + 1] = r_raw
        denom = 1.0 + r_adj[t + 1]
        adj_close[t] = adj_close[t + 1] / denom if denom != 0 else adj_close[t + 1]
    out["adj_close"] = adj_close
    out["adj_factor"] = adj_close / close
    out["is_roll"] = is_roll
    out["r_adj"] = r_adj
    return out


def fetch_akshare_main_daily(symbol_code: str, start_date: str) -> pd.DataFrame:
    """akshare 拉取主力连续日线（sina），返回清洗后、≥ start_date 的原始数据。"""
    import akshare as ak  # 惰性导入：仅 fetch 路径依赖

    df = ak.futures_zh_daily_sina(symbol=symbol_code)
    if df is None or df.empty:
        raise RuntimeError(f"akshare 返回空数据: {symbol_code}")
    out = clean_daily(df)
    out = out[out["date"] >= pd.Timestamp(start_date)].reset_index(drop=True)
    return out


def save_daily(symbol_code: str, df: pd.DataFrame, out_dir: Path | None = None) -> Path:
    """落盘 parquet（含后复权列的原始数据）。"""
    d = out_dir or main_daily_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{symbol_code}.parquet"
    df.to_parquet(p, index=False)
    return p


def load_daily(symbol_code: str, out_dir: Path | None = None) -> pd.DataFrame:
    """从 parquet 加载（列含 adj_close/adj_factor/is_roll/r_adj，若已复权落盘）。"""
    p = (out_dir or main_daily_dir()) / f"{symbol_code}.parquet"
    if not p.exists():
        raise FileNotFoundError(f"数据缺失: {p}（先运行 scripts/fetch_data.py）")
    return pd.read_parquet(p)
