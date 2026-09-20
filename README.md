# quant_futures_01

期货量化研究项目：**全品种商品期货**的策略探索、回测与数据研究。
基础设施方案见 [docs/infrastructure_plan.md](docs/infrastructure_plan.md)（2026-09-20 批准）。

## 环境

- 依赖用 uv 管理：`uv sync` 建 `.venv`；`.venv/bin/python <脚本>` 或 `uv run <tool>` 运行。
- 数据源 akshare（拉取需联网）。

## 快速开始

```bash
uv sync                                        # 建 .venv + uv.lock
.venv/bin/python scripts/fetch_data.py         # 拉取 30 品种主连日线 → 后复权 parquet
.venv/bin/python scripts/run_baseline.py       # 基准 A/B 对比（output/baseline/compare.csv）
.venv/bin/python scripts/backtest.py --weights vol   # 波动率目标回测
uv run pytest                                 # 测试全过
```

## 目录

| 路径 | 说明 |
|---|---|
| config/ | 全量配置（default.yaml 权威基底；custom.yaml 覆盖） |
| src/quant_futures_01/ | 框架包（config.py 配置加载 / data.py 数据管道 / cost.py 成本模型 / portfolio.py 回测引擎） |
| scripts/ | 可执行脚本（fetch_data / backtest / run_baseline / example） |
| data/ | 行情数据 parquet（gitignore，不入库） |
| tests/ | pytest 测试（不读写真实数据文件） |
| tmp/ | 临时脚本（gitignore） |
| docs/ | 设计/方案/决策文档 |
| archive/ | 探索归档（experiments/ 自包含单元） |

## 探索纪律

见 AGENTS.md：预注册 → 判定 → 归档；用数据说话。
