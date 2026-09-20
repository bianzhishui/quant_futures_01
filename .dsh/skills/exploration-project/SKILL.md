---
name: exploration-project
description: 探索型研究项目的启动与纪律——通用探索方法论（预注册、判定标准、防数据挖掘、归档、对账）；Python 工程规范（uv/虚拟环境、配置化、代码风格、测试、临时脚本）；git 仓库初始化；含通用配置框架脚手架（bootstrap.py + templates/），可快速初始化一个新探索项目。
---

# 探索型研究项目：启动 · 纪律 · 工程

**This skill is guidance, not a checklist.** 沉淀自一个长期运营的研究仓库
（quant_trading_01：A 股量化，R5 等权575 + 四账户模拟盘），提炼出**与领域无关**的
探索方法论与 Python 工程规范。落地到具体仓库时，领域专属规则（如 A 股 T+1/涨跌停/
费率）**不属于本 skill**——写进该仓库自己的 AGENTS.md。

---

## 1. 探索方法论（通用）

### 1.1 预注册：先定死，再实验

1. 任何新变体/参数/架构改动，先写预注册方案：**假设、规则（定死）、判定标准（写死）、诚实风险**，用户批准后再实施；
2. 方案文档遵循命名 `docs/{topic}_plan.md`；判定标准写死，**实施后不可事后调参**；
3. 实施后把结果（含失败）写回同一文档 **`§0 实施结果归档`**，再提交 commit。

> 目的：防数据挖掘/过拟合——先定规则再看数据，判定标准不是事后从结果里挑出来的。

### 1.2 判定与诚实

- 对照预注册标准给结论：**通过 / 未达标 / 否决**，诚实写代价与局限；
- 同一条件未达标持续 **≥3 轮**才算 blocked（不因一轮失败就放弃，也不因一轮通过就定论）；
- **用数据说话**：不编造数字；关键结论必须引用输出文件；拿不到就如实写"数据未出/需跑"。

### 1.3 实验纪律（防数据挖掘）

- **不反复重跑"试"**：慢实验（10-20 分钟级）先预注册、后台跑、不重复；
- "默认行为不变"的验证用**快照对拍**：改造前后各跑一次，关键输出逐字节一致；
- **单信号/单模块通过 ≠ 整体增益**：单因子/单组件验证通过后必须过**组合验证门禁**
  （增量门禁：并入后整体指标增量 ≥ 阈值）；正交性、显著性只是**必要不充分条件**；
- **样本外验证**：训练段选参 → 验证段确认（选参时看不到验证段）；**高原判据**：
  参数扫描看**高原**（邻域波动小）而非**尖峰**（端点伪通过、过拟合）；网格内部优于端点。

### 1.4 归档（探索结束必须归档，不手删）

- 已结束探索 → `archive/experiments/{unit}/`：**脚本 + plan + 结论输出**自包含成单元；
- 生产链脚本**留位**主目录，其 plan/结论照常归档；
- 归档一律 `git mv` 保留历史，**禁止 rm/cp 搬移**；归档后须 import/编译校验通过；
- 索引登记结论（状态：✅ 通过并入 / ❌ 否决 / 🟡 部分通过 / ⏸ 搁置 / 🛠 工具）。

### 1.5 运营/长期运行对账

- **不变量检查**（每次变更后抽查）：① 期末净值 = 现金 + 持仓；② 期末 ≈ 期初 − 费用（<1%）；
  ③ 周期涨幅连乘 = 累计；
- 监控指标 + 状态灯（如 μ±2σ 带），只读不操作。

---

## 2. Python 工程规范（硬性限制）

### 2.1 环境：uv + 虚拟环境

- 依赖用 **uv** 管理：`pyproject.toml` + `uv.lock` 可复现；`uv sync` 建 `.venv`；
- `uv.toml` 把缓存放项目内（`cache-dir = ".uv-cache"`，gitignore），**不依赖工作区外缓存**；
- 一律 `.venv/bin/python <脚本>` 或 `uv run <tool>` 运行；不激活 venv、不裸 pip install。

### 2.2 配置化（代码不 hardcode 配置值）

- 所有可配置项集中在 YAML：`config/default.yaml` 为权威基底（缺失 → **报错，无兜底**）；
- 覆盖：`--config` / 环境变量 → **深合并**（同 key 覆盖，缺项继承 default）；指定文件不存在 → 报错；
- **冻结参数校验**：default.yaml 中标注的冻结 key 被覆盖 → 醒目警告（不阻止），提示需预注册；
- 代码读取：函数内 `get_config()` **惰性单例**；**全仓无模块级配置读取**；
  各脚本 `main()` 里 `load_config(args.config)` 建立单例；
- CLI 参数默认值取配置；显式指定时覆盖配置。

### 2.3 代码风格

- **每次改动 Python 代码后必须格式化**：`uv run ruff format .` + `uv run ruff check .`（0 错误）；
- 逻辑放函数、`main()` 只做 argparse 壳；**模块级不读 sys.argv**（被 import 时读错调用方参数）；
- 同仓库脚本间调用用 **import 模块**，不用 subprocess（省进程冷启动）；subprocess 只留给
  跨环境/跨语言、独立一次性工具、需超时强杀场景；
- 退出码语义用函数返回值 + try/except 保留，不靠进程码。

### 2.4 测试

- pytest，`tests/` 目录（pyproject `testpaths` 配置）；
- **测试/验证代码绝不直接读写真实数据文件**——先在副本/临时目录上测，测完恢复，再碰真实文件；
- 改代码后提交前：format + check + **pytest 全过** + 全部模块 import 通过。

### 2.5 临时脚本

- 临时/验证脚本统一放项目内 `tmp/`（gitignore）：数据探查、一次性对照实验、临时验证；
  不丢系统 /tmp、不散落仓库根（避免跨会话丢失/污染工作区/误提交）。

---

## 3. Git 仓库初始化

1. `git init` + 首次提交（脚手架已带 .gitignore）；
2. **.gitignore 策略**：数据/大文件/输出（`*.parquet`、`output/`、`*.csv`、`*.png`）不入库；
   例外：长期运营的每日/每月产物**入库跟踪**（在 .gitignore 里显式放行）；
3. 文档与代码分离：`docs/` 放设计/方案/决策；可执行脚本放 `scripts/`；
4. **AGENTS.md**：写清项目上下文、环境、核心脚本、冻结参数、纪律与禁忌——AI 代理与
   维护者的第一入口（脚手架自带模板，落库后按项目改）。

---

## 4. 快速启动：脚手架 + 通用配置框架

用本 skill 自带模板 + bootstrap 脚本，一分钟初始化一个可探索项目：

```bash
python skills/exploration-project/bootstrap.py ../my-project --name my-project --git
cd ../my-project
uv sync                                  # 建 .venv + uv.lock
.venv/bin/python scripts/example.py      # 冒烟：配置框架可用
uv run pytest                            # 测试全过
```

之后：按项目改 `config/default.yaml` → 改 `AGENTS.md` → 首次 commit。

**脚手架内容**（`skills/exploration-project/templates/`，bootstrap 复制并改名）：

| 文件 | 说明 |
|---|---|
| pyproject.toml | uv 虚拟项目（无 build-system，只装依赖）；deps pyyaml；dev pytest/ruff；ruff 默认风格；pytest `pythonpath=["src"]` |
| uv.toml | cache-dir 项目内（.uv-cache） |
| .gitignore | 数据/输出/缓存/venv/tmp 忽略（含例外注释） |
| config/default.yaml | 全量配置基底（权威；缺失报错无兜底） |
| config/custom.yaml.example | 覆盖示例（同 key 覆盖，缺项继承） |
| src/{pkg}/config.py | **通用配置框架**：YAML 深合并 + 惰性单例 + 属性访问 + 冻结校验（提炼自 quant_trading_01 的 research/config.py） |
| src/{pkg}/__init__.py | 包入口 |
| scripts/example.py | 配置框架用法示例（sys.path 引导 + main() 壳） |
| tests/test_config.py | 配置框架测试（不碰真实数据文件） |
| tmp/.gitkeep | 临时脚本目录约定 |
| docs/README.md | 文档目录约定（plan 命名/归档） |
| AGENTS.md | 项目纪律模板（上下文/环境/脚本/冻结参数/纪律/禁忌） |
| README.md | 项目说明模板 |

**通用配置框架要点**（src/{pkg}/config.py）：

- `load_config([custom_path])`：始终加载 default.yaml → 深合并自定义（`--config` > 环境变量 > 无）→ 冻结校验 → 设置单例；
- `get_config()`：**惰性单例**（首次调用加载 default；`main()` 里 `load_config` 后生效）；被 import 的脚本未走 main() 时用默认配置；
- `Config`：属性访问 `cfg.a.b` + dict 访问 `cfg["a"]["b"]` + 按路径取值 `cfg.get("a","b",default=...)`；缺失 key 报错（无兜底）；
- `paths` 段自动解析为**相对仓库根的绝对路径**（CWD 无关）；
- `FROZEN_PARAMS` 标注冻结 key（覆盖偏离 → 醒目警告；空列表 = 关闭校验）；
- `add_config_arg(parser)`：给 argparse 加 `--config` 参数。

**bootstrap.py 行为**：复制 templates/ → 目标目录（须不存在或为空）→ 包名由项目名派生
（`--pkg` 可显式指定）→ 全文件文本替换（`exploration_project`→包名、`exploration-project`→项目名）
→ `--git` 时 `git init` → 打印下一步指引。

---

## 5. 边界

- 本 skill 只含**可移植方法论 + 通用工程规范**；领域专属规则（A 股 T+1/涨跌停/费率、行业规则…）
  落到各项目 AGENTS.md；
- 落地某仓库时，具体命令/路径/配置系统以该仓库 AGENTS.md 为准。
