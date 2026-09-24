"""基础设施测试：数据清洗/复权、成本模型、波动率目标、回测对拍与不变量。

全部使用合成数据，绝不读写真实数据文件（AGENTS.md §6 / 方案 §3.2）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_futures_01 import config as cfgmod
from quant_futures_01.cost import CostModel
from quant_futures_01.data import back_adjust, clean_daily
from quant_futures_01.portfolio import (
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


def test_clean_daily_drops_zero_volume() -> None:
    """无交易行（volume=0）应被删除（方案 §3.2），价格停滞≠可交易。"""
    df = pd.DataFrame(
        {
            "date": ["2020-01-01", "2020-01-02", "2020-01-03"],
            "open": [1, 1, 2],
            "high": [1, 1, 2],
            "low": [1, 1, 2],
            "close": [1.0, 1.0, 2.0],
            "volume": [0, 100, 200],
        }
    )
    out = clean_daily(df)
    assert len(out) == 2  # 2020-01-01 无交易被删
    assert out["date"].iloc[0].date().isoformat() == "2020-01-02"


def test_clean_daily_all_zero_volume_raises() -> None:
    df = pd.DataFrame(
        {
            "date": ["2020-01-01", "2020-01-02"],
            "open": [1, 1],
            "high": [1, 1],
            "low": [1, 1],
            "close": [1.0, 1.0],
            "volume": [0, 0],
        }
    )
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


def test_run_backtest_empty_raises() -> None:
    """空面板应清晰报错（提示先 fetch 数据），而非抛出 TypeError。"""
    empty = pd.DataFrame()
    cost = CostModel()
    with pytest.raises(ValueError, match="无数据"):
        run_backtest(empty, empty, cost)


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


# ---------- 前视与上市前处理（review 回归） ----------


def test_vol_weights_no_lookahead() -> None:
    """波动率目标权重不得用当天收益（vol[t] 仅含 returns[..t-1]）。"""
    idx = pd.date_range("2020-01-01", periods=80, freq="B")
    base = np.zeros(80)
    r1 = pd.DataFrame({"A": base}, index=idx)
    r1.loc[idx[60], "A"] = 0.0
    r2 = r1.copy()
    r2.loc[idx[60], "A"] = 0.30  # 当天 ±0.3 的极端收益
    w1 = vol_target_weights(r1)
    w2 = vol_target_weights(r2)
    # r[60] 只允许影响 t>60 的权重；t<=60 必须逐日一致（vol[t] 不含当天收益）
    assert np.allclose(
        w1["A"].iloc[:61].to_numpy(), w2["A"].iloc[:61].to_numpy(), atol=1e-12
    )
    # 预热期（前 ~21 日无波动估计）权重应为 0
    assert w1["A"].iloc[0] == 0.0


def test_equal_weights_prelisting() -> None:
    """晚上市品种在上市前权重应为 0，已上市品种按当日可交易数归一化（generate_weights）。"""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from backtest import generate_weights  # 测真实脚本函数

    idx = pd.date_range("2020-01-01", periods=60, freq="B")
    closes = pd.DataFrame(index=idx, columns=["A", "B"])
    closes["A"] = 100.0
    closes["B"] = np.nan
    start = 30  # B 从第 30 天起上市
    closes.iloc[start:, 1] = 100.0
    w = generate_weights("equal", closes, cfgmod.get_config())
    # B 上市前: A 权重 1.0, B 权重 0；上市后各 0.5
    assert w["B"].iloc[start - 1] == 0.0
    assert w["A"].iloc[start - 1] == 1.0
    assert np.isclose(w["A"].iloc[start], 0.5)
    assert np.isclose(w["B"].iloc[start], 0.5)


# ---------- TSMOM 信号（docs/tsmom_plan.md） ----------


def _ts_closes(
    periods: int = 200, slope: float = 0.5, start: float = 100.0
) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=periods, freq="B")
    price = start + slope * np.arange(periods)
    return pd.DataFrame({"A": price}, index=idx)


def test_tsmom_signal_direction() -> None:
    from quant_futures_01.strategy import tsmom_signal

    up = tsmom_signal(_ts_closes(slope=0.5), lookback=20)
    down = tsmom_signal(_ts_closes(slope=-0.5), lookback=20)
    flat = tsmom_signal(_ts_closes(slope=0.0), lookback=20)
    # 稳定上升 → 信号 +1（预热期后）；下降 → -1；横盘 → 0
    assert (up.iloc[30:] == 1.0).all().all()
    assert (down.iloc[30:] == -1.0).all().all()
    assert (flat.iloc[30:] == 0.0).all().all()


def test_tsmom_signal_no_lookahead() -> None:
    """sig[t] 不得受 close[t] 影响（只用到 T-1 及以前）。"""
    from quant_futures_01.strategy import tsmom_signal

    closes = _ts_closes(slope=0.5)
    sig1 = tsmom_signal(closes, lookback=20)
    closes2 = closes.copy()
    # 在第 60 天制造极端下跌（close[60] 从 130 → 50）
    closes2.iloc[60, 0] = 50.0
    sig2 = tsmom_signal(closes2, lookback=20)
    # t<=60 的信号不得因 close[60] 改变
    assert np.allclose(sig1.iloc[:61].to_numpy(), sig2.iloc[:61].to_numpy())
    # 预热期前 lookback 行为 0
    assert (sig1.iloc[:20] == 0).all().all()


def test_tsmom_generate_weights_integration() -> None:
    """generate_weights('tsmom')：权重 = 方向信号 × 等权 1/N，且上市前为 0。"""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from backtest import generate_weights

    idx = pd.date_range("2020-01-01", periods=150, freq="B")
    closes = pd.DataFrame(index=idx, columns=["A", "B"])
    closes["A"] = 100.0 + np.arange(150)  # 上升
    closes["B"] = np.nan
    closes.iloc[80:, 1] = 100.0 - 0.5 * np.arange(70)  # B 从第 80 天起上市并下跌
    w = generate_weights("tsmom", closes, cfgmod.get_config(), lookback=20)
    # B 上市前权重 0；上市后 A 多(+1/N)、B 空(-1/N)
    assert w["B"].iloc[79] == 0.0
    assert np.isclose(w["A"].iloc[120], 0.5)
    assert np.isclose(w["B"].iloc[120], -0.5)
    # 预热期（前 20 日）A 权重 0
    assert (w["A"].iloc[:20] == 0).all()


# ---------- XSMOM 横截面动量（docs/xsmom_plan.md） ----------


def _xs_closes(periods: int = 160) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=periods, freq="B")
    n = np.arange(periods)
    return pd.DataFrame(
        {
            "A": 100.0 + 1.0 * n,  # 最强上升
            "B": 100.0 + 0.5 * n,  # 次强
            "C": 100.0 - 0.5 * n,  # 次弱
            "D": 100.0 - 1.0 * n,  # 最弱
        },
        index=idx,
    )


def test_xsmom_weights_ranking_and_legs() -> None:
    from quant_futures_01.strategy import xsmom_weights

    w = xsmom_weights(_xs_closes(), lookback=20, quantile=0.25)
    # 预热期后某日：A(最升) 与 B(次升) 在多头腿（pct≥0.75），D(最跌) 在空头腿（pct≤0.25），C 中间
    row = w.iloc[120]
    assert np.isclose(row["A"], 0.5 / 2)  # 多头腿 2 只，等权 +0.25
    assert np.isclose(row["B"], 0.5 / 2)
    assert np.isclose(row["D"], -0.5)  # 空头腿 1 只，−0.5
    assert row["C"] == 0.0
    # 腿名义：多头 +0.5、空头 −0.5（dollar-neutral）
    assert np.isclose(row.clip(lower=0).sum(), 0.5)
    assert np.isclose(row.clip(upper=0).sum(), -0.5)
    # 预热期（前 20 日）全 0
    assert (w.iloc[:20] == 0).all().all()


def test_xsmom_no_lookahead() -> None:
    """w[t] 不得受 close[t] 影响（排序只用 T-1 及以前）。"""
    from quant_futures_01.strategy import xsmom_weights

    closes = _xs_closes()
    w1 = xsmom_weights(closes, lookback=20, quantile=0.25)
    closes2 = closes.copy()
    closes2.iloc[60, 0] = 50.0  # 第 60 天 A 暴跌
    w2 = xsmom_weights(closes2, lookback=20, quantile=0.25)
    assert np.allclose(w1.iloc[:61].to_numpy(), w2.iloc[:61].to_numpy(), atol=1e-12)


def test_xsmom_prelisting_excluded() -> None:
    """晚上市品种不参与排名，权重 0；上市且预热期满后进入对应腿。"""
    from quant_futures_01.strategy import xsmom_weights

    idx = pd.date_range("2020-01-01", periods=120, freq="B")
    n = np.arange(120)
    closes = pd.DataFrame(index=idx, columns=["A", "B", "C", "D", "E"])
    closes["A"] = 100.0 + 1.0 * n
    closes["B"] = 100.0 + 0.5 * n
    closes["C"] = 100.0 - 0.5 * n
    closes["D"] = 100.0 - 0.2 * n
    closes["E"] = np.nan
    closes.iloc[60:, 4] = 300.0 - 3.0 * np.arange(60)  # E 第 60 天上市且为截面最弱
    w = xsmom_weights(closes, lookback=20, quantile=0.25)
    assert (w["E"].iloc[:60] == 0).all()  # 上市前不参与
    # 上市且预热期满后，E 是截面最弱 → 空头腿
    assert w["E"].iloc[100] < 0


# ---------- 期限结构 / carry（docs/carry_plan.md） ----------


def test_build_slopes_basic() -> None:
    """斜率 = F1/F2−1，F1/F2 按持仓量排序；同日不足 2 合约跳过。"""
    from quant_futures_01.termstructure import build_slopes

    df = pd.DataFrame(
        {
            "date": ["20200102", "20200102", "20200103", "20200103", "20200103"],
            "symbol": ["RB01", "RB05", "RB01", "RB05", "RB10"],
            "close": [3500.0, 3600.0, 3510.0, 3610.0, 3700.0],
            "open_interest": [100, 80, 110, 90, 5],
            "volume": [1000, 800, 1100, 900, 50],
        }
    )
    s = build_slopes(df, "oi1")
    assert len(s) == 2
    # 2020-01-02: F1=RB01(oi100) F2=RB05(oi80) → slope=3500/3600−1
    r0 = s[s["date"] == pd.Timestamp("2020-01-02")].iloc[0]
    assert r0["F1"] == "RB01" and r0["F2"] == "RB05"
    assert np.isclose(r0["slope"], 3500 / 3600 - 1)
    # 2020-01-03: F1=RB01(oi110) F2=RB05(oi90)
    r1 = s[s["date"] == pd.Timestamp("2020-01-03")].iloc[0]
    assert r1["F1"] == "RB01" and r1["F2"] == "RB05"


def test_build_slopes_date_robust() -> None:
    """int/str/object（混入空表）三种日期输入都能正确解析。"""
    from quant_futures_01.termstructure import build_slopes

    rows = {
        "date": [20180102, 20180102, 20180103, 20180103],  # int
        "symbol": ["RB01", "RB05", "RB01", "RB05"],
        "close": [3500.0, 3600.0, 3510.0, 3610.0],
        "open_interest": [100, 80, 110, 90],
        "volume": [1000, 800, 1100, 900],
    }
    for make in [
        lambda: pd.DataFrame(rows),
        lambda: pd.DataFrame({k: [str(v) for v in vals] for k, vals in rows.items()}),
    ]:
        s = build_slopes(make(), "oi1")
        assert len(s) == 2
        assert s["date"].iloc[0] == pd.Timestamp("2018-01-02")


def test_carry_weights_sign_lag() -> None:
    """carry 权重 = sign(slope) × 1/N；slope[t] 影响 w[t+1]（T-1 决定、无前视）。"""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from backtest import carry_weights

    idx = pd.date_range("2020-01-01", periods=8, freq="B")
    closes = pd.DataFrame({"A": 100.0, "B": 100.0}, index=idx)
    slope = pd.DataFrame(
        {
            "A": [np.nan, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01],
            "B": [np.nan, -0.01, -0.01, -0.01, -0.01, -0.01, -0.01, -0.01],
        },
        index=idx,
    )
    w = carry_weights(closes, slope, cfgmod.get_config())
    # slope[1]=+0.01(A)/-0.01(B) → w[2] = A 多、B 空；w[1] 应为 0（信号 1 日后生效）
    assert w["A"].iloc[1] == 0.0
    assert np.isclose(w["A"].iloc[2], 0.5)
    assert np.isclose(w["B"].iloc[2], -0.5)
    # slope 恒 NaN 行（第 0 天）→ 0
    assert w["A"].iloc[0] == 0.0


# ---------- 逐合约执行模型（docs/measurement_plan.md） ----------


def test_oi_main_returns_and_roll() -> None:
    """OI 主力收益 = 持有合约同合约 close-to-close；换月标记正确；重建连续自洽。"""
    from quant_futures_01.rollsim import oi_main_returns, reconstruct_continuous

    idx = pd.date_range("2020-01-01", periods=6, freq="B")
    g = 1.01 ** np.arange(6)
    closes = pd.DataFrame(
        {
            "A": 100.0 * g,  # 每日 +1%
            "B": 90.0 * g,  # 每日 +1%
        },
        index=idx,
    )
    ois = pd.DataFrame(
        {"A": [100, 100, 50, 50, 50, 50], "B": [50, 50, 100, 100, 100, 100]},
        index=idx,
    )  # 第 3 天起 B 取代 A 成为 OI 最大
    rets, roll = oi_main_returns(closes, ois)
    # 第 0 天无前收盘 → 0；此后每日 +1%（A/B 均为几何 +1%/日，同合约自身收益）
    assert rets.iloc[0] == 0.0
    assert np.allclose(rets.iloc[1:], 0.01)
    # 第 3 天（index=2）换月
    assert roll.iloc[2] == 1 and roll.iloc[3] == 0 and roll.iloc[0] == 0
    # 重建连续自洽：recon[t]/recon[t-1]−1 == rets[t]
    recon = reconstruct_continuous(rets)
    assert np.allclose(recon.pct_change().fillna(0.0), rets, atol=1e-12)


def test_oi_main_new_contract_first_day() -> None:
    """持有合约在换月日无前收盘（新上市首日）→ 收益 0，不崩。"""
    from quant_futures_01.rollsim import oi_main_returns

    idx = pd.date_range("2020-01-01", periods=3, freq="B")
    closes = pd.DataFrame(
        {"A": [100.0, 101.0, 102.0], "B": [np.nan, np.nan, 91.0]},
        index=idx,
    )  # B 第 3 天才上市
    ois = pd.DataFrame({"A": [100, 90, 10], "B": [0, 0, 100]}, index=idx)
    rets, roll = oi_main_returns(closes, ois)
    assert np.isfinite(rets).all()
    assert roll.iloc[2] == 1
    assert np.isclose(rets.iloc[2], 0.0)  # B 上市首日无前收盘 → 0


# ---------- 组合/风控层（docs/portfolio_risk_plan.md） ----------


def test_cov_vol_target_no_lookahead() -> None:
    """协方差波动率目标权重不得用当天收益（cov[t] 仅含 returns[..t-1]）。"""
    from quant_futures_01.portfolio import cov_vol_target_weights

    idx = pd.date_range("2020-01-01", periods=100, freq="B")
    rng = np.random.default_rng(3)
    base = pd.DataFrame(
        {c: rng.normal(0, 0.01, len(idx)) for c in ("A", "B", "C")}, index=idx
    )
    r1 = base.copy()
    r2 = base.copy()
    r2.iloc[70, 0] = 0.30  # 第 70 天 A 极端收益
    w1 = cov_vol_target_weights(r1)
    w2 = cov_vol_target_weights(r2)
    assert np.allclose(w1.iloc[:71].to_numpy(), w2.iloc[:71].to_numpy(), atol=1e-12)
    assert not np.isnan(w1.to_numpy()).any()


def test_drawdown_scale_rules() -> None:
    """回撤减仓阶梯规则 + T-1 决定（无前视）。"""
    from quant_futures_01.portfolio import drawdown_scale

    # 净值：先涨 10% 再跌 25%（回撤达 20%+），再涨回
    idx = pd.date_range("2020-01-01", periods=10, freq="B")
    nav = np.array([1.0, 1.05, 1.10, 1.05, 0.95, 0.85, 0.90, 0.95, 1.0, 1.05])
    prices = pd.Series(nav, index=idx)
    returns = prices.pct_change().fillna(0.0)
    scale = drawdown_scale(returns)
    # t=4: DD(prev)=-4.5% <6% → 1.0；t=6: DD(prev)=1-0.85/1.10=-22.7% ≥20% → 0.25
    assert np.isclose(scale.iloc[4], 1.0)
    assert np.isclose(scale.iloc[6], 0.25)
    assert np.isclose(scale.iloc[5], 0.5)  # DD(prev)=1-0.95/1.10=-13.6% ≥12% → 0.5
    # 恢复：t=8 DD(prev)=1-0.95/1.10=-13.6% 仍 ≥12%（未 <6%）→ 0.5 或 0.25，不得为 1
    assert scale.iloc[8] < 1.0


# ---------- 贱极候选池维度（docs/cheap_extreme_plan.md） ----------


def test_price_percentile_and_drawdown() -> None:
    """现价在窗口低分位 → D1 低；从高点回撤 → D2 为负（深跌）。"""
    from quant_futures_01.cheap_extreme import drawdown_from_high, price_percentile

    idx = pd.date_range("2020-01-01", periods=520, freq="B")
    # 前 500 日高位（105±5），最后 20 日暴跌到 70——暴跌须在窗口内
    n = np.arange(520)
    price = np.where(n < 500, 105.0 + np.sin(n) * 5.0, 70.0 + np.sin(n) * 2.0)
    close = pd.Series(price, index=idx)
    d1 = price_percentile(close, window=300, min_periods=250)
    d2 = drawdown_from_high(close, window=250)
    assert d1.iloc[-1] < 0.10  # 暴跌后处于窗口内低分位
    assert d2.iloc[-1] < -0.20  # 从 ~105 跌到 ~70 → 回撤 > 30%
    # 预热期（历史不足）→ NaN
    assert pd.isna(d1.iloc[100])


def test_vol_percentile_sanity() -> None:
    """恒定波动序列的分位应接近 0.5（非极端）。"""
    from quant_futures_01.cheap_extreme import vol_percentile

    idx = pd.date_range("2020-01-01", periods=800, freq="B")
    rng = np.random.default_rng(11)
    returns = pd.Series(rng.normal(0, 0.01, len(idx)), index=idx)
    vp = vol_percentile(returns)
    assert 0.2 < vp.iloc[-1] < 0.8


def test_dedup_events() -> None:
    """事件去重：相邻事件间隔 ≥ min_gap 交易日才保留新事件。"""
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from run_cheap_extreme import _dedup_events

    idx = pd.date_range("2020-01-01", periods=200, freq="B")
    # 事件：0、10（<60 被去重）、70、130、135（<60 被去重）
    dates = pd.DatetimeIndex([idx[0], idx[10], idx[70], idx[130], idx[135]])
    kept = _dedup_events(dates, idx, min_gap=60)
    assert kept == [idx[0], idx[70], idx[130]]
    # 空输入
    assert _dedup_events(pd.DatetimeIndex([]), idx, min_gap=60) == []


# ---------- 阶段二：库存/政策维度（docs/stock_macro_plan.md） ----------


def test_parse_cn_month() -> None:
    from quant_futures_01.cheap_extreme import _parse_cn_month

    assert _parse_cn_month("2008年01月份") == pd.Timestamp("2008-01-01")
    assert _parse_cn_month("2026年08月份") == pd.Timestamp("2026-08-01")
    assert pd.isna(_parse_cn_month("bad"))


def test_warehouse_low() -> None:
    """仓单近期处于低分位 → 库存低位标记 True。"""
    from quant_futures_01.cheap_extreme import warehouse_low

    idx = pd.date_range("2020-01-01", periods=800, freq="B")
    n = np.arange(800)
    rec = pd.Series(
        np.where(n < 760, 5000 + (n % 100) * 20, 200.0 + (n % 5) * 10), index=idx
    )
    low = warehouse_low(rec, window=300, min_periods=250)
    assert low.iloc[-1] == True  # noqa: E712 —— 仓单骤降到低位


def test_m2_policy_state() -> None:
    """M2 同比上升段 → 宽松（True）；平稳/下降 → 收紧。"""
    from quant_futures_01.cheap_extreme import m2_policy_state

    months = pd.date_range("2015-01-01", periods=48, freq="MS")
    m2 = pd.DataFrame(
        {
            "month": [f"{d.year}年{d.month:02d}月份" for d in months],
            "m2_yoy": [8.0] * 24 + [12.0] * 24,  # 后 2 年明显上升
        }
    )
    dates = pd.DatetimeIndex([pd.Timestamp("2016-06-15"), pd.Timestamp("2018-06-15")])
    state = m2_policy_state(m2, dates, window=36)
    assert bool(state.iloc[0]) is False  # 2016-06 仍在 8% 段 → 收紧
    assert bool(state.iloc[1]) is True  # 2018-06 已进入 12% 段 → 宽松
