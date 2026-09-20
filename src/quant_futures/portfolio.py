"""波动率目标多品种回测引擎（含不变量检查）。

预注册方案: docs/infrastructure_plan.md §3.4/§3.5（2026-09-20 批准）
约定（定死）:
  - 收盘信号 → 次日成交：T-1 收盘决定的目标权重在 T 日生效
    （close-to-close 收益近似开盘成交，成本模型已含滑点覆盖）；
  - 相对权重 = 等权 × 波动率倒数（滚动 vol_window 日，下限 vol_min）；
    总杠杆 = vol_target / 组合波动（线性加权近似）；单品种名义上限 max_pos_ratio；
  - 每日再平衡；换手 = |目标权重 − 漂移权重|（单边）；
  - 不变量：净值重构一致；周期收益连乘 = 累计；无保证金穿透。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant_futures import config as cfgmod
from quant_futures.cost import CostModel

TRADING_DAYS = 252


def rolling_annual_vol(returns: pd.DataFrame, window: int) -> pd.DataFrame:
    """滚动年化波动率（ddof=0，min_periods=20）。"""
    return returns.rolling(window, min_periods=20).std(ddof=0) * np.sqrt(TRADING_DAYS)


def vol_target_weights(
    returns: pd.DataFrame, cfg: cfgmod.Config | None = None
) -> pd.DataFrame:
    """波动率目标权重（T 日生效，波动用截至 T-1 的收益——无前视）。

    规则: vol[t] = 滚动年化波动(returns[..t-1])（shift(1) 保证不含当天收益）；
    相对权重 = 1/vol 归一化；组合波动（线性加权）→ 杠杆 = vol_target / 组合波动；
    单品种名义上限 max_pos_ratio；波动/权重缺失（上市前/预热期）→ 0。
    """
    c = cfg or cfgmod.get_config()
    # shift(1): vol[t] 只用 returns[..t-1]，权重在 t 日生效（收盘信号→次日成交）
    vol = (
        rolling_annual_vol(returns, c.backtest.vol_window)
        .shift(1)
        .clip(lower=c.backtest.vol_min)
    )
    inv = 1.0 / vol
    w_rel = inv.div(inv.sum(axis=1), axis=0)  # 相对权重（和为 1；NaN 品种自然剔除）
    port_vol = (w_rel * vol).sum(axis=1)  # 组合波动近似（线性加权）
    leverage = (c.backtest.vol_target / port_vol).replace([np.inf, -np.inf], np.nan)
    w = w_rel.mul(leverage, axis=0)
    w = w.clip(upper=c.backtest.max_pos_ratio)
    return w.fillna(0.0)


@dataclass
class BacktestResult:
    nav: pd.Series
    gross_nav: pd.Series
    returns: pd.Series
    gross_returns: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    metrics: dict = field(default_factory=dict)
    invariants: dict = field(default_factory=dict)


def run_backtest(
    returns: pd.DataFrame, target_w: pd.DataFrame, cost: CostModel
) -> BacktestResult:
    """通用日频回测：T-1 目标权重 → T 日生效（close-to-close）。

    returns / target_w：index=日期，columns=品种；按交集对齐。
    """
    idx = returns.index.intersection(target_w.index)
    r = returns.loc[idx]
    r_safe = r.fillna(0.0)  # 上市前/停牌日：无价格 → 无贡献（权重应为 0）
    w = target_w.loc[idx].fillna(0.0)
    gross = (w * r_safe).sum(axis=1)
    prev_w = w.shift(1).fillna(0.0)
    drift_denom = (1.0 + gross).clip(lower=1e-8)
    drifted = prev_w.mul(1.0 + r_safe).div(drift_denom, axis=0)  # 权重随收益漂移
    turnover = (w - drifted).abs().sum(axis=1)
    net = gross - cost.one_side_pct * turnover
    nav = (1.0 + net).cumprod()
    gross_nav = (1.0 + gross).cumprod()
    return BacktestResult(
        nav=nav,
        gross_nav=gross_nav,
        returns=net,
        gross_returns=gross,
        weights=w,
        turnover=turnover,
        metrics=compute_metrics(nav, net, gross, turnover),
        invariants=check_invariants(nav, net, w, cost.margin_ratio),
    )


def compute_metrics(
    nav: pd.Series, net: pd.Series, gross: pd.Series, turnover: pd.Series
) -> dict:
    """指标：年化收益/波动、夏普、最大回撤、卡玛、月胜率、换手、成本拖累。"""
    n = len(net)
    if n == 0:
        return {}

    def _ann(r: pd.Series) -> float:
        return float((1.0 + r).prod() ** (TRADING_DAYS / n) - 1.0)

    ann_net = _ann(net)
    ann_gross = _ann(gross)
    vol = float(net.std(ddof=0) * np.sqrt(TRADING_DAYS))
    sharpe = ann_net / vol if vol > 0 else float("nan")
    dd = nav / nav.cummax() - 1.0
    mdd = float(dd.min())
    calmar = ann_net / abs(mdd) if mdd < 0 else float("nan")
    monthly = net.resample("ME").apply(lambda s: (1.0 + s).prod() - 1.0)
    return {
        "final_nav": float(nav.iloc[-1]),
        "days": n,
        "ann_return": ann_net,
        "ann_vol": vol,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "calmar": calmar,
        "monthly_win_rate": float((monthly > 0).mean())
        if len(monthly)
        else float("nan"),
        "turnover_daily": float(turnover.mean()),
        "cost_drag_ann": ann_gross - ann_net,
    }


def check_invariants(
    nav: pd.Series, net: pd.Series, weights: pd.DataFrame, margin_ratio: float
) -> dict:
    """不变量检查：① 净值重构一致；② 周期收益连乘 = 累计；③ 无保证金穿透。"""
    recon = (1.0 + net).cumprod()
    nav_err = float(np.max(np.abs(recon - nav))) if len(net) else float("nan")
    yearly = net.resample("YE").apply(lambda s: (1.0 + s).prod() - 1.0)
    compound_err = float(abs((1.0 + yearly).prod() - 1.0 - (nav.iloc[-1] - 1.0)))
    margin = weights.abs().mul(margin_ratio).sum(axis=1)
    return {
        "nav_reconstruct_max_err": nav_err,
        "yearly_compound_err": compound_err,
        "margin_used_max": float(margin.max()),
        "margin_breach": bool((margin > 1.0).any()),
    }


def sector_contribution(
    returns: pd.DataFrame, weights: pd.DataFrame, sector_map: dict[str, str]
) -> pd.DataFrame:
    """分板块累计名义贡献（Σ 权重×收益 的累计和）。"""
    out: dict[str, pd.Series] = {}
    for sec in sorted({s for s in sector_map.values() if s}):
        cols = [c for c in weights.columns if sector_map.get(c) == sec]
        if cols:
            out[sec] = (weights[cols] * returns[cols]).sum(axis=1).cumsum()
    return pd.DataFrame(out)
