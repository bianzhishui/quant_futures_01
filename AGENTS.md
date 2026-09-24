# AGENTS.md — 给 AI 代理与本仓库维护者的操作指南

本文件给在本仓库工作的 AI 代理（及任何人）提供**关键上下文、纪律与禁忌**。
改动任何策略/参数前先读本文件；本文是"怎么干活"细则。

---

## 1. 这是什么项目

期货量化研究项目（quant_futures_01）：**全品种商品期货**量化研究（回测探索，非实盘）。
已批准并实施 5 个预注册方案（[docs/RESEARCH.md](docs/RESEARCH.md) 为研究索引）：
- 基础设施（数据管道 + 成本模型 + 波动率目标回测引擎 + 基准）✅；
- 动量家族 3 方案（TSMOM / vol-TSMOM / XSMOM）全部 ❌ 否决；
- Carry 期限结构 🟡 部分通过后经诊断**降级**；
- **动量长多腿诊断（docs/momentum_diag_plan.md）**：最后一个"长多腿为正"的发现也证伪——正贡献 **94~471% 来自贵金属+有色**，剔除后残余 ≈0。
**研究结论（如实，2026-09-21，含样本外修正）**：**窗口结构比策略设计更决定结果**——2018-2026 是金属单边牛市（信号发现 = 金属 beta 幻觉、空头必亏）；**样本外复验（outsample_plan.md）证明该结论是窗口特定的**：2010-2017 危机 regime 中趋势信号跑赢深亏基准（+0.07 vs −0.25）、空头腿 +0.11 正贡献、动量赢家是农产品/黑色（非金属）。风控层：协方差波动率目标已采纳为新默认（2018-26 实现波动 15.3%），回撤减仓 overlay 证伪否决。回测默认窗口已扩至 2010-01-01。
新方案仍须先预注册并经用户批准后实施。

## 2. 环境与运行

- 依赖用 **uv** 管理（pyproject.toml + uv.lock 可复现）：`uv sync` 建 `.venv`；
- 一律 `.venv/bin/python <脚本>` 或 `uv run <tool>` 运行；
- uv 缓存已配置在项目内（uv.toml cache-dir=.uv-cache），不依赖工作区外缓存；
- 数据源 **akshare**（拉取需联网）：先 `scripts/fetch_data.py` 落盘，再跑回测。

## 3. 核心脚本

| 脚本 | 用途 |
|---|---|
| scripts/fetch_data.py | 拉取主力连续日线 → 清洗 → 后复权 → data/futures_main_daily/*.parquet + output/data_summary.csv |
| scripts/backtest.py | 回测 CLI：`--weights vol\|equal\|sma20\|tsmom\|tsmom_vol\|xsmom\|carry [--sma N] [--lookback L] [--out 名]` |
| scripts/run_baseline.py | 基准 A（等权）/B（20 日均线）对比，复用 backtest 引擎 |
| scripts/run_strategy.py | 动量族方案实施（--mode tsmom\|tsmom_vol\|xsmom）：主运行 + 灵敏度 + 子时段 + 成本敏感性 + 多空分解 + 门禁判定（见 docs/*_plan.md） |
| scripts/fetch_termstructure.py | 拉取交易所逐合约日线 → 期限结构斜率因子（SHFE/CZCE/INE，20 品种 × 3 变体，DCE 接口失效暂排除） |
| scripts/run_carry.py | carry 方案实施：主运行 + 定义稳健性 + 子时段 + 成本 + 多空分解 + 门禁（见 docs/carry_plan.md） |
| scripts/run_carry_diag.py | carry 稳健性诊断：有色剥离 + 远月定义一致性 + 板块统计（见 docs/carry_diag_plan.md） |
| scripts/run_momentum_diag.py | 动量长多腿板块诊断：板块分解 + 剔除贵金属有色复算 + 时段（见 docs/momentum_diag_plan.md） |
| scripts/run_rollsim.py | 测量补全：逐合约执行对拍 + TSMOM 复验 + 金属现货/展期分解（见 docs/measurement_plan.md） |
| scripts/run_risk_layer.py | 组合/风控层评估：V/V2(协方差)/V3(回撤减仓) 对比 + 灵敏度 + 空头约束诊断（见 docs/portfolio_risk_plan.md） |
| scripts/run_outsample.py | 样本外扩展复验：W1(2010-17) vs W2(2018-26) 趋势/空头/板块/风控框架（见 docs/outsample_plan.md） |
| scripts/run_cheap_extreme.py | 贱极扫描器（傅海棠框架）：价格低分位+深跌候选池 + 历史事件验证（见 docs/cheap_extreme_plan.md） |
| scripts/example.py | 示例：配置框架用法（冒烟） |

## 4. 冻结参数（改前必须预注册 + 用户批准）

config/default.yaml 中冻结参数（config.py `FROZEN_PARAMS` 标注；覆盖偏离触发醒目警告，不阻止）：

| 参数 | 冻结值 | 说明 |
|---|---|---|
| universe | 30 品种 | 品种池（增删须预注册；由纪律保证，不触发 config 警告） |
| data.adjust_threshold | 0.20 | 后复权换月跳变阈值 |
| cost.slippage_bps | 5 | 滑点 0.05% 单边 |
| cost.commission_bps | 2 | 手续费 0.02% 单边（≈交易所标准×2 近似） |
| cost.margin_ratio | 0.10 | 保证金率 |
| backtest.start_date | 2010-01-01 | 回测起点（2026-09-21 由 2018 扩窗，见 outsample_plan.md） |
| backtest.vol_target | 0.15 | 目标年化波动 |
| backtest.vol_window | 60 | 滚动波动窗口（日） |
| backtest.vol_min | 0.05 | 年化波动下限（防爆仓） |
| backtest.max_pos_ratio | 2.0 | 单品种名义上限（×权益） |
| backtest.vol_estimator | cov | 波动率目标组合波动估计（cov 协方差新默认 / linear 旧线性） |
| strategy.tsmom_lookback | 120 | TSMOM 回看窗口（计划冻结；CLI --lookback 覆盖仅用于灵敏度诊断） |
| strategy.xsmom_lookback | 120 | XSMOM 排序回看窗口（计划冻结；CLI --lookback 覆盖仅用于灵敏度诊断） |
| strategy.xsmom_quantile | 0.25 | XSMOM 分位（短腿 ≤q / 长腿 ≥1−q；计划冻结） |
| params.window / top_ratio / limits.min_n | 21 / 0.2 / 50 | 脚手架占位（未用于策略，勿动） |

## 5. 研究纪律（不可省略）

1. **先预注册、后实验**：任何新变体/参数先写 `docs/{topic}_plan.md`
   （规则定死、判定标准、诚实风险），**用户点头后再实施**；
2. **结果归档**：跑完把结果写进同一 plan 文档 `§0 实施结果归档`（含失败），再提交 commit；
3. **报告要带判定**：对照预注册标准明确"通过/未达标/否决"，诚实写代价与局限；
4. **用数据说话**：不编造数字；关键结论引用输出文件；
5. 同一条件未达标持续 **≥3 轮**才算 blocked；
6. 探索结束必须归档（`archive/experiments/`，git mv 保留历史，不手删）。

## 6. 禁忌清单

- ✅ **每次改动 Python 代码后必须格式化**：`uv run ruff format .` + `uv run ruff check .`
  （0 错误）；提交前 pytest 全过 + 全部模块 import 通过；
- ❌ 不反复重跑实验去"试"：先预注册、后台跑、不重复；
- ❌ 不改冻结参数 / 不绕过配置直接改代码里的常量；
- ❌ 不提交 data/、output/、tmp/（见 .gitignore；长期运营产物例外须显式放行）；
- ❌ **测试/验证代码绝不直接读写真实数据文件**——先在副本/临时目录上测，测完恢复，再碰真实文件；
- ✅ 临时/验证脚本统一放项目内 `tmp/`（不入库），不丢系统 /tmp、不散落仓库根。
