"""基础设施测试：数据清洗/复权、成本模型、波动率目标、回测对拍与不变量。

全部使用合成数据，绝不读写真实数据文件（AGENTS.md §6 / 方案 §3.2）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_futures import config as cfgmod
from quant_futures.cost import CostModel
from quant_futures.data import back_adjust, clean_daily
from quant_futures.portfolio import (
    check_invariants,
    run_backtest,
    sector_contribution,
    vol_target_weights,
)


# ---------- 数据层 ----------


def test_back_adjust_no_jump() -> None:
    df = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=4),
            "close": [100.0, 101.0, 102.0, 103.0],
        }
    )
    out = back_adjust(df, threshold=0.2)
    raw = out["close"].pct_change().dropna()
    assert (out["is_roll"] == False).all()  # noqa: E712
    assert np.allclose(out["adj_factor"], 1.0)
    assert np.allclose(out["adj_close"], out["close"])
    assert np.allclose(out["r_adj"].iloc[1:], raw)


def test_back_adjust_roll() -> None:
    df = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=4),
            "close": [100.0, 101.0, 130.0, 131.0],
        }
    )
    out = back_adjust(df, threshold=0.2)
    # 换月日在 index=2（130/101-1≈0.287>0.2）
    assert out["is_roll"].iloc[2] == True  # noqa: E712
    assert out["r_adj"].iloc[2] == 0.0
    # 锚定最新价
    assert out["adj_close"].iloc[-1] == 131.0
    # 非换月日复权收益 == 原始收益
    assert np.isclose(out["r_adj"].iloc[1], 101.0 / 100.0 - 1)
    assert np.isclose(out["r_adj"].iloc[3], 131.0 / 130.0 - 1)
    # 换月后序列连续（无跳变）
    assert np.allclose(out["adj_close"].iloc[2:], [130.0, 131.0])


def test_clean_daily_normalize_dedupe_sort() -> None:
    df = pd.DataFrame(
        {
            "日期": ["2020-01-03", "2020-01-02", "2020-01-02", "2020-01-03"],
            "开盘": [3, 2, 2, 3],
            "收盘": [3.1, 2.1, 2.1, 3.1],
            "成交量": [100, 200, 200, 100],
        }
    )
    out = clean_daily(df)
    assert list(out.columns) == ["date", "open", "close", "volume"]
    assert out["date"].is_monotonic_increasing
    assert out["date"].nunique() == 2  # 去重
    assert out["close"].dtype.kind == "f"


def test_clean_daily_nan_raises() -> None:
    df = pd.DataFrame(
        {
            "date": ["2020-01-01", "2020-01-02"],
            "open": [1, 2],
            "high": [1, 2],
            "low": [1, 2],
            "close": [1.0, np.nan],
        }
    )
    with pytest.raises(ValueError):
        clean_daily(df)


def test_clean_daily_missing_close_raises() -> None:
    df = pd.DataFrame({"date": ["2020-01-01"], "foo": [1]})
    with pytest.raises(ValueError):
        clean_daily(df)


# ---------- 成本模型 ----------


def test_cost_model_from_config() -> None:
    cm = CostModel()
    assert np.isclose(cm.slippage, 5 / 1e4)
    assert np.isclose(cm.commission, 2 / 1e4)
    assert np.isclose(cm.one_side_pct, 7 / 1e4)
    assert np.isclose(cm.round_trip_pct(), 14 / 1e4)
    assert np.isclose(cm.margin_ratio, 0.10)


# ---------- 波动率目标 ----------


def test_vol_target_weights_bounds() -> None:
    idx = pd.date_range("2020-01-01", periods=120, freq="B")
    rng = np.random.default_rng(42)
    returns = pd.DataFrame(
        {c: rng.normal(0, 0.01, len(idx)) for c in ("A", "B", "C")}, index=idx
    )
    w = vol_target_weights(returns)
    assert not w.isna().any().any()
    assert (w >= 0).all().all()
    assert (w <= cfgmod.get_config().backtest.max_pos_ratio + 1e-9).all().all()
    assert np.allclose(w.iloc[-1].sum() > 0, True)


# ---------- 回测对拍（G3：手算 vs 引擎，逐日一致） ----------


def _manual_backtest(
    returns: pd.DataFrame, w: pd.DataFrame, one_side: float
) -> np.ndarray:
    """独立手算：逐日 毛收益 → 漂移权重 → 换手 → 净收益 → 净值。"""
    r = returns.to_numpy()
    wt = w.to_numpy()
    n = len(r)
    nav = np.empty(n)
    prev = np.zeros(wt.shape[1])
    equity = 1.0
    for t in range(n):
        gross = float(wt[t] @ r[t])
        denom = max(1.0 + gross, 1e-8)
        drifted = prev * (1.0 + r[t]) / denom
        turnover = float(np.abs(wt[t] - drifted).sum())
        net = gross - one_side * turnover
        equity *= 1.0 + net
        nav[t] = equity
        prev = wt[t]
    return nav


def test_backtest_matches_manual() -> None:
    idx = pd.date_range("2020-01-01", periods=5, freq="B")
    returns = pd.DataFrame(
        {
            "A": [0.01, -0.02, 0.005, 0.03, -0.01],
            "B": [-0.005, 0.03, 0.01, -0.02, 0.02],
        },
        index=idx,
    )
    w = pd.DataFrame(0.5, index=idx, columns=["A", "B"])
    cost = CostModel()
    res = run_backtest(returns, w, cost)
    manual = _manual_backtest(returns, w, cost.one_side_pct)
    assert np.allclose(res.nav.to_numpy(), manual, rtol=1e-9, atol=1e-9)


# ---------- 不变量（G4） ----------


def test_invariants_pass() -> None:
    idx = pd.date_range("2019-01-01", periods=400, freq="B")
    rng = np.random.default_rng(7)
    returns = pd.DataFrame(
        {c: rng.normal(0.0002, 0.01, len(idx)) for c in ("A", "B", "C", "D")}, index=idx
    )
    w = vol_target_weights(returns)
    res = run_backtest(returns, w, CostModel())
    inv = res.invariants
    assert inv["nav_reconstruct_max_err"] < 1e-9
    assert inv["yearly_compound_err"] < 1e-9
    assert inv["margin_breach"] is False


def test_check_invariants_direct() -> None:
    idx = pd.date_range("2020-01-01", periods=60, freq="B")
    net = pd.Series(0.001, index=idx)
    nav = (1 + net).cumprod()
    w = pd.DataFrame(0.1, index=idx, columns=["A", "B"])
    inv = check_invariants(nav, net, w, margin_ratio=0.1)
    assert inv["nav_reconstruct_max_err"] < 1e-12
    assert inv["yearly_compound_err"] < 1e-9
    assert inv["margin_breach"] is False


# ---------- 分板块贡献 ----------


def test_sector_contribution() -> None:
    idx = pd.date_range("2020-01-01", periods=4, freq="B")
    returns = pd.DataFrame(
        {"A": [0.01] * 4, "B": [0.02] * 4, "C": [-0.01] * 4}, index=idx
    )
    weights = pd.DataFrame({"A": [0.5] * 4, "B": [0.3] * 4, "C": [0.2] * 4}, index=idx)
    sec = sector_contribution(returns, weights, {"A": "黑色", "B": "有色", "C": "黑色"})
    assert set(sec.columns) == {"黑色", "有色"}
    # 黑色 = A(0.5*0.01) + C(0.2*-0.01) = 0.003/日
    assert np.isclose(sec["黑色"].iloc[-1], 4 * (0.5 * 0.01 + 0.2 * -0.01))
    assert np.isclose(sec["有色"].iloc[-1], 4 * (0.3 * 0.02))
