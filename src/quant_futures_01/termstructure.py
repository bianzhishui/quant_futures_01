"""期限结构数据管道：交易所逐合约日线 → 主力-次主力斜率因子（carry）。

预注册方案: docs/carry_plan.md（2026-09-20 批准）
规则（定死）:
  - 数据源：ak.get_futures_daily（交易所官方），SHFE/CZCE/INE（DCE 接口失效，一期排除）；
  - 每品种每日：当日合约按 rank_col（持仓量/成交量）排序 → F1=最大、F2=第 far_rank+1 大；
  - 斜率 slope = F1.close/F2.close − 1（同日两合约原始价，无需复权）；
  - 变体：oi1（持仓量+次主力，主信号）/ vol1（成交量+次主力）/ oi2（持仓量+第3远月），用于定义稳健性诊断；
  - 落盘 data/term_structure/{SYMBOL}_{variant}.parquet，gitignore。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from quant_futures_01 import config as cfgmod

VARIANTS = ("oi1", "vol1", "oi2")
RANK_COL = {"oi1": "open_interest", "vol1": "volume", "oi2": "open_interest"}
FAR_RANK = {"oi1": 1, "vol1": 1, "oi2": 2}


def carry_symbols(cfg=None) -> list[dict]:
    """carry 品种池：SHFE/CZCE/INE（DCE 数据源失效，一期排除）。"""
    c = cfg or cfgmod.get_config()
    return [u for u in c.universe if u["exchange"] in ("SHFE", "CZCE", "INE")]


def termstructure_dir() -> Path:
    cfg = cfgmod.get_config()
    return Path(cfg.paths.data) / "term_structure"


def fetch_exchange_year(
    market: str, year: int, end_fallback_days: int = 5
) -> pd.DataFrame:
    """拉取某交易所某年的逐合约日线（end 日遇节假日自动回退）。

    注意：INE（上期能源）2018-03-26 才上市原油期货，该交易所最小起始日按此设定。
    """
    import akshare as ak  # 惰性导入：仅 fetch 路径依赖

    min_start = (
        "20180326" if (market == "INE" and year == 2018) else f"{year}0102"
    )  # INE 2018-03-26 上市原油；其余年份从当年 01-02 起（避免跨年重复）
    last = None
    for back in range(end_fallback_days):
        end = f"{year}1231" if year != 2026 else "20260920"
        if back:
            end = f"{year}12{31 - back:02d}"
        try:
            df = ak.get_futures_daily(start_date=min_start, end_date=end, market=market)
            if df is not None and len(df):
                # 官方数据部分行数值列为空字符串（''）→ 强制转 NaN，保证可落盘
                for col in df.columns:
                    if col not in ("symbol", "variety", "date"):
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                return df
            last = ValueError(f"{market} {year} 返回空")
        except Exception as e:  # noqa: BLE001 —— 单次回退重试
            last = e
    raise RuntimeError(f"{market} {year} 拉取失败: {last}")


def build_slopes(daily: pd.DataFrame, variant: str) -> pd.DataFrame:
    """从交易所逐合约日线构建单品种期限结构斜率（按日）。

    稳健处理：日期兼容 int(20180102)/str/'datetime'；按 (symbol, date) 去重；
    同日无两个 close>0 合约 → 该日跳过（防 divide-by-zero）。
    """
    df = daily.copy()
    required = {"date", "symbol", "close", RANK_COL[variant]}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"build_slopes({variant}) 缺列: {sorted(missing)}，实际列: {list(df.columns)}"
        )
    # 稳健日期归一：兼容 int(20180102)/str('20180326')/ISO('2018-01-02')/datetime；
    # 注意 concat 混入空 DataFrame 后日期列可能被提升为 object dtype，须统一处理。
    s = df["date"].astype(str).str.strip()
    df["date"] = pd.to_datetime(s, format="%Y%m%d", errors="coerce").fillna(
        pd.to_datetime(s, errors="coerce")
    )
    df = df.dropna(subset=["date"]).drop_duplicates(subset=["symbol", "date"])
    for col in ("close", "open_interest", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close", RANK_COL[variant]])
    if df.empty:
        return pd.DataFrame()
    rows: list[dict] = []
    for dt, g in df.groupby("date"):
        g = g.sort_values(RANK_COL[variant], ascending=False)
        if len(g) < FAR_RANK[variant] + 1:
            continue
        f1, f2 = g.iloc[0], g.iloc[FAR_RANK[variant]]
        if float(f1["close"]) <= 0 or float(f2["close"]) <= 0:
            continue
        rows.append(
            {
                "date": dt,
                "F1": f1["symbol"],
                "F2": f2["symbol"],
                "f1_close": float(f1["close"]),
                "f2_close": float(f2["close"]),
                "slope": float(f1["close"] / f2["close"] - 1.0),
                "oi1": float(f1["open_interest"]),
                "oi2": float(f2["open_interest"]),
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("date").reset_index(drop=True)
    return out


def save_slope(
    symbol: str, df: pd.DataFrame, variant: str, out_dir: Path | None = None
) -> Path:
    d = out_dir or termstructure_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{symbol}_{variant}.parquet"
    df.to_parquet(p, index=False)
    return p


def load_slope(
    symbol: str, variant: str = "oi1", out_dir: Path | None = None
) -> pd.DataFrame:
    p = (out_dir or termstructure_dir()) / f"{symbol}_{variant}.parquet"
    if not p.exists():
        raise FileNotFoundError(
            f"期限结构数据缺失: {p}（先运行 scripts/fetch_termstructure.py）"
        )
    return pd.read_parquet(p)


def load_slope_panel(variant: str = "oi1", cfg=None) -> pd.DataFrame:
    """全品种斜率面板（date × symbol）。"""
    c = cfg or cfgmod.get_config()
    panel = {}
    for u in carry_symbols(c):
        try:
            df = load_slope(u["symbol"], variant)
        except FileNotFoundError:
            continue
        panel[u["symbol"]] = df.set_index("date")["slope"]
    out = pd.DataFrame(panel).sort_index()
    return out
