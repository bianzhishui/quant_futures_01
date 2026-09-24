# 研究进展索引（2026-09-21 更新）

本文件汇总 quant_futures_01 的全部预注册研究方案及其结论，作为研究"账本"。
每个方案的完整记录（规则、门禁、§0 归档）见对应 plan 文档。

## 方案状态总览

| # | 方案 | 文档 | 结论 | 净夏普（vs 基准） | 一句话 |
|---|---|---|---|---|---|
| 0 | 基础设施（数据/成本/回测/基准） | [infrastructure_plan.md](infrastructure_plan.md) | ✅ 通过 | — | G1–G6 全过，两轮 review 修复 3 处真实问题 |
| 1 | TSMOM 等权 | [tsmom_plan.md](tsmom_plan.md) | ❌ 否决 | −0.46 vs 基准 A +0.45 | 时序动量方向信号无边际；空头腿失效 |
| 2 | vol-TSMOM | [tsmom_vol_plan.md](tsmom_vol_plan.md) | ❌ 否决 | −0.32 vs 基准 V +0.47 | 换幅度框架未救回信号；长多 +0.20 / 空头 −0.21 |
| 3 | XSMOM 横截面 | [xsmom_plan.md](xsmom_plan.md) | ❌ 否决 | −0.99 vs 基准 A +0.45 | 三方案最差；长多 +0.07 / 空头 **−0.61** |
| 4 | Carry 期限结构 | [carry_plan.md](carry_plan.md) + [carry_diag_plan.md](carry_diag_plan.md) | 🟡→❌ 诊断降级 | +0.52 vs 基准 A +0.51（首跑赢，但诊断后不可靠） | 超额主要来自 6 个有色品种、远月定义不稳定（符号一致率 65%）→ 倾向放弃 |
| 5 | 动量长多腿诊断 | [momentum_diag_plan.md](momentum_diag_plan.md) | ❌ 板块 beta | — | 长多腿正贡献 94~471% 来自贵金属+有色；剔除后残余 ≈0（XSMOM 转负）→ 最后一个发现也证伪 |
| 6 | 测量补全（逐合约执行） | [measurement_plan.md](measurement_plan.md) | ✅ 复验稳健 | 重建 TSMOM −0.16 vs sina −0.30（同号） | 金属 beta 结论在干净测量下稳健；金属牛市为**现货驱动**（沪银展期 −69%）；发现 sina 连续系统性低估 backwardated/高估 contango |
| 7 | 组合/风控层 | [portfolio_risk_plan.md](portfolio_risk_plan.md) | 🟡 部分采纳 | V2 夏普 0.472 / 波动 15.3% | 协方差波动率目标**采纳为新默认**（波动缺口 8.5%→15.3% 收敛、夏普不变）；回撤减仓 overlay **证伪否决**（夏普 0.47→0.27） |
| 8 | 样本外扩展 | [outsample_plan.md](outsample_plan.md) | 🟡 **窗口特定** | W1 TSMOM +0.07 vs 基准 −0.25；空头腿 +0.11 | **2018-26 结论不普适**：2010-2017 趋势/空头有效（危机 regime）；"金属 beta"是 2018-26 独有；风控框架精度也 regime 依赖 |
| 9 | 贱极扫描器（傅海棠） | [cheap_extreme_plan.md](cheap_extreme_plan.md) | ✅ **价格代理有边际** | 事件 6m 净超额 +3.6% / 12m +8.4% | **首个被量化确认的正向发现**：价格 5 年低分位+深跌后 6/12 个月跑赢等权基准（两窗口同号、板块分散 47%）；纳入筛选器（供人工用成本/库存确认） |

## 核心研究结论（跨方案，2026-09-21 终版，含样本外修正）

1. **2018-2026 的信号发现归结为 贵金属+有色 板块 beta（该窗口特有）**：
   - 动量长多腿：正贡献 **94%（TSMOM）/ 93%（vol-TSMOM）/ 471%（XSMOM）** 来自贵金属+有色；剔除后残余 ≈10%；
   - Carry 正溢价：仅有色 6 品种；沪金 ≈4.7 倍、沪银 ≈10.5 倍——该窗口被金属超级牛市主导；
   - **但样本外复验（outsample_plan.md）证明这是窗口特定的**：2010-2017 动量赢家是农产品 50%+黑色 35%（分散），非金属。
2. **空头腿：2018-2026 无效（−0.40 等），2010-2017 正贡献（+0.11）**——空头失效同样是窗口特定，不是结构性规律。
3. **趋势信号（TSMOM）**：2018-2026 无边际（−0.47 vs 基准 +0.46）；**2010-2017 有边际（+0.07 vs 深亏基准 −0.25，危机段有效）**。
4. **样本警示（更新）**：**窗口结构比策略设计更决定结果**——2018-26 单边金属牛（多头信号显效是 beta 幻觉、空头必亏）；2010-17 危机主导（趋势/做空有效）。两窗口结论不能互相外推；未做家族级多重检验校正。
5. **风控层**：协方差波动率目标采纳为新默认（2018-26 实现波动 15.3%）；但 W1 仅 9.5%（未达目标）——框架精度 regime 依赖；回撤减仓 overlay 证伪否决。
6. **测量升级（measurement_plan.md）**：金属牛市现货驱动（贵金属展期为负）；sina 主力连续系统性低估 backwardated/高估 contango——"吃展期"策略须用逐合约执行。
7. **对下一步的含义**：扩窗后默认口径为 2010-2026 全样本；趋势/空头在危机 regime 的价值值得重估（新预注册）；"金属 beta"仅代表 2018-26 窗口。
8. **贱极代理（傅海棠框架，2026-09-21）**：价格 5 年低分位 + 深跌 ≥30% 后 6/12 个月**净超额 +3.6%/+8.4%**（两窗口同号、板块分散）——**contrarian 深价值是唯一被量化确认的边际**，与趋势/期限结构的负结论形成对照；纳入筛选器，供人工用成本/库存/供给二次确认。

## 数据资产与复现

| 数据 | 位置（gitignore） | 复现命令 |
|---|---|---|
| 主力连续日线（30 品种，后复权） | data/futures_main_daily/ | `scripts/fetch_data.py` |
| 期限结构斜率（20 品种 × 3 变体） | data/term_structure/ | `scripts/fetch_termstructure.py`（~30 分钟，原始缓存 data/_raw_term/） |
| 交易所原始逐合约（缓存） | data/_raw_term/ | 同上（断点续跑） |

> 注：DCE（大商所）官方接口当前与 akshare 不兼容，期限结构一期仅覆盖 SHFE/CZCE/INE 20 品种；DCE 主力连续日线不受影响。

## 复现各方案结果

```bash
.venv/bin/python scripts/run_baseline.py            # 基准 A/B
.venv/bin/python scripts/run_strategy.py --mode tsmom --bench equal     # 方案 1
.venv/bin/python scripts/run_strategy.py --mode tsmom_vol --bench vol   # 方案 2
.venv/bin/python scripts/run_strategy.py --mode xsmom --bench equal     # 方案 3
.venv/bin/python scripts/run_carry.py               # 方案 4（需先 fetch_termstructure）
uv run pytest                                       # 35 个测试
```

输出落在 output/（gitignore），门禁判定见各方案 §0 与 output/*_gates.json。

## 纪律回顾（本仓库 AGENTS.md §5）

预注册 → 批准 → 实施 → §0 归档（含失败）→ commit；判定写死、事后不调参；用数据说话。
