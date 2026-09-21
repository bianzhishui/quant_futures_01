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
| 4 | Carry 期限结构 | [carry_plan.md](carry_plan.md) | 🟡 部分通过 | **+0.52 vs 基准 A +0.51（首个跑赢）** | 长多（backwardation）强 +0.60；但 2022 后消失 / 远月定义敏感 / 有色集中 |

## 核心研究结论（跨方案）

1. **多头/backwardation 方向存在正溢价（双来源印证）**：
   - 动量家族 3 方案：长多腿一致为正（+0.20 / +0.07 / 等权亦正）；
   - Carry：backwardation 多头腿 +0.60（几乎全部收益来源）；
   - → "中国商品期货的多头/backwardation 方向存在正风险溢价"是目前最强的跨来源证据。
2. **空头腿系统性无效/亏损**：做空下跌趋势（动量）、做空输家（XSMOM −0.61）、做空 contango（carry ≈0）——在 2018-2026 中国商品上均无正贡献。
3. **含空头的动量（任何形态）净成本后不成立**：时序/截面、等权/波动率目标、分位边界三条轴均验证。
4. **Carry 有边际但不稳健**：G2a（2022 后边际消失）、G2b（远月定义反号）、G2c（有色集中 51%）三扇门未过 → 🟡，不判可并入。
5. **样本警示**：所有方案在同一 2018-2026 样本检验（未做家族级多重检验校正）；"部分通过/否决"是证据倾向，非统计证明。

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
