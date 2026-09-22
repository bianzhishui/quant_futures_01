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

## 核心研究结论（跨方案，2026-09-21 终版）

1. **全部信号发现最终归结为 贵金属+有色 板块 beta（2018-2026 牛市后视）**：
   - 动量长多腿：正贡献 **94%（TSMOM）/ 93%（vol-TSMOM）/ 471%（XSMOM）** 来自贵金属+有色；剔除这两板块后残余 ≈ 原值 10%（≈0，成本后必为负）；
   - Carry 正溢价：仅有色 6 品种（非有色跑输基准）；
   - 同期沪金 ≈4.7 倍、沪银 ≈10.5 倍、有色大涨——本样本期被金属超级牛市主导，**任何偏向金属的多头信号都会"看起来有效"，这是样本运气/后视，非可交易 edge**；
   - 基准对照（D4）：等权买入持有各板块贡献均衡（0.07~0.17）——牛市并非板块专属，是**动量/carry 信号的选择**集中到了金属。
2. **空头腿系统性无效/亏损**：做空下跌趋势（动量）、做空输家（XSMOM −0.61）、做空 contango（carry ≈0）——在 2018-2026 中国商品上均无正贡献。
3. **含空头的动量（任何形态）净成本后不成立**：时序/截面、等权/波动率目标、分位边界三条轴均验证。
4. **样本警示**：所有方案在同一 2018-2026 样本检验（未做家族级多重检验校正）；该样本被金属超级牛市主导，结论不可外推其他时段。
5. **对下一步的含义**：在该样本上继续挖信号的意义很低——建议转向**组合/风控层**（波动率目标补强、回撤控制、多空不对称仓位设计），或接受"简单信号在该样本期无效"的结论。
6. **测量升级（measurement_plan.md）**：金属 beta 结论在逐合约干净执行下**稳健**（TSMOM 重建 −0.16 vs sina −0.30，同号）；金属牛市为**现货驱动**（贵金属展期为负）；sina 主力连续系统性低估 backwardated 品种（如螺纹展期 +52%、菜油 +76% 被漏掉）、高估 contango 品种——未来"吃展期"策略须用逐合约执行。

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
