"""成本模型：滑点 + 手续费 + 保证金率（统一保守近似，冻结）。

预注册方案: docs/infrastructure_plan.md §3.3（2026-09-20 批准）
规则（定死）:
  - 滑点 cost.slippage_bps（0.05% 单边）；
  - 手续费 cost.commission_bps（0.02% 单边，≈ 交易所标准×2 的统一保守近似）；
  - 保证金率 cost.margin_ratio（10%，用于杠杆/仓位计算，非实盘精确值）；
  - 以上全部为冻结参数（config.py FROZEN_PARAMS），改动须预注册。
"""

from __future__ import annotations

from quant_futures_01 import config as cfgmod


class CostModel:
    """单边成本（小数）：slippage + commission；保证金率单独暴露。"""

    def __init__(self, cfg: cfgmod.Config | None = None):
        c = cfg or cfgmod.get_config()
        self.slippage = c.cost.slippage_bps / 1e4
        self.commission = c.cost.commission_bps / 1e4
        self.margin_ratio = c.cost.margin_ratio

    @property
    def one_side_pct(self) -> float:
        """单边总成本（小数）。"""
        return self.slippage + self.commission

    def round_trip_pct(self) -> float:
        """往返总成本（小数）。"""
        return 2.0 * self.one_side_pct
