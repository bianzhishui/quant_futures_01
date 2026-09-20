# exploration-project

探索型研究项目（脚手架模板初始化）。

## 环境

- 依赖用 uv 管理：`uv sync` 建 `.venv`；`.venv/bin/python <脚本>` 或 `uv run <tool>` 运行。

## 快速开始

```bash
uv sync                                # 建 .venv + uv.lock
.venv/bin/python scripts/example.py    # 冒烟：配置框架可用
uv run pytest                          # 测试全过
```

## 目录

| 路径 | 说明 |
|---|---|
| config/ | 全量配置（default.yaml 权威基底；custom.yaml 覆盖） |
| src/exploration_project/ | 通用框架包（config.py 配置加载器） |
| scripts/ | 可执行脚本（main() 壳 + 函数逻辑） |
| tests/ | pytest 测试（不读写真实数据文件） |
| tmp/ | 临时脚本（gitignore） |
| docs/ | 设计/方案/决策文档 |
| archive/ | 探索归档（experiments/ 自包含单元） |

## 探索纪律

见 AGENTS.md：预注册 → 判定 → 归档；用数据说话。
