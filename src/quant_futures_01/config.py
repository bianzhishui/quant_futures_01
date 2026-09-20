"""配置加载器 —— 全量配置在 YAML 文件，代码不 hardcode 配置值（通用框架版）。

加载规则：
  1. 始终加载 config/default.yaml（基底，权威；缺失 → 报错，无兜底）
  2. 若指定 --config / APP_CONFIG 配置文件 → 深合并覆盖 default.yaml（同 key 覆盖）
  3. 指定文件不存在 → 报错退出
  4. 冻结参数偏离校验：default.yaml 中 FROZEN_PARAMS 标注的 key 被覆盖 → 醒目警告（不阻止）

用法：
  from quant_futures_01 import config
  cfg = config.get_config()                        # 惰性单例（默认 default.yaml）
  cfg = config.load_config("config/custom.yaml")   # 指定文件覆盖（main() 里建立）
  cfg.params.window                                # 属性访问（类型化）
  cfg["params"]["window"]                          # dict 访问亦可
  cfg.get("params", "window", default=None)        # 按路径取值

脚本规范（配合本框架）：
  - main() 里 parser 加 config.add_config_arg(parser)，args.config 传给 load_config
  - 函数内 get_config() 惰性读取；禁止模块级读取配置
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

# 仓库根（本文件在 src/<pkg>/ 下，向上三级）
ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG = ROOT / "config" / "default.yaml"
# 覆盖配置文件的环境变量（如 APP_CONFIG=config/custom.yaml）
ENV_VAR = "APP_CONFIG"

# 冻结参数路径（default.yaml 中这些 key 的值为冻结基准，覆盖偏离 → 警告；空列表 = 关闭校验）
# 格式: (*路径键, 中文名) —— 支持多级路径(如 strategy.r5.seasoning)
FROZEN_PARAMS = [
    ("params", "window", "窗口"),
    ("params", "top_ratio", "头部比例"),
    ("limits", "min_n", "最小样本数"),
    ("data", "adjust_threshold", "复权跳变阈值"),
    ("cost", "slippage_bps", "滑点(bp)"),
    ("cost", "commission_bps", "手续费(bp)"),
    ("cost", "margin_ratio", "保证金率"),
    ("backtest", "start_date", "回测起点"),
    ("backtest", "vol_target", "目标年化波动"),
    ("backtest", "vol_window", "波动窗口"),
    ("backtest", "vol_min", "波动下限"),
    ("backtest", "max_pos_ratio", "单品种名义上限"),
]


def _deep_merge(base: dict, override: dict) -> dict:
    """深合并：override 同 key 覆盖 base，嵌套 dict 递归合并。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            out[k] = _deep_merge(base[k], v)
        else:
            out[k] = v
    return out


def _resolve_paths(cfg: dict) -> dict:
    """把配置 paths 段中相对仓库根的路径统一解析为绝对路径（CWD 无关）。"""
    paths = cfg.get("paths", {})
    return {k: str(ROOT / v) if isinstance(v, str) else v for k, v in paths.items()}


class Config:
    """配置对象：支持属性访问（cfg.a.b）与 dict 访问（cfg["a"]["b"]）。"""

    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, name: str):
        try:
            v = self._data[name]
            return Config(v) if isinstance(v, dict) else v
        except KeyError as e:
            raise AttributeError(
                f"配置项缺失: {name}（请检查 default.yaml 或指定配置文件）"
            ) from e

    def __getitem__(self, key):
        return self._data[key]

    def get(self, *path, default=None):
        """按路径取值：cfg.get("costs", "slip_default")；缺路径返回 default。"""
        node = self._data
        for p in path:
            if not isinstance(node, dict) or p not in node:
                return default
            node = node[p]
        return node

    def to_dict(self) -> dict:
        return self._data

    def __repr__(self) -> str:
        return f"Config({self._data!r})"


def _deep_get(node: dict, keys: list[str]):
    """按路径取嵌套 dict 值；缺路径返回 None。"""
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    return node


def _check_frozen(cfg: Config, default_data: dict, custom_path: str | None) -> None:
    """冻结参数偏离校验：覆盖后值 ≠ default.yaml 冻结基准 → 打印醒目警告。"""
    warns = []
    for *keys, cname in FROZEN_PARAMS:
        base_val = _deep_get(default_data, keys)
        cur_val = _deep_get(cfg.to_dict(), keys)
        if cur_val != base_val:
            warns.append(f"    {cname}({'.'.join(keys)}): {base_val} → {cur_val}")
    if warns:
        src = custom_path or "default.yaml"
        print("\n⚠️⚠️⚠️  配置偏离冻结参数（default.yaml 为冻结基准） ⚠️⚠️⚠️", flush=True)
        print(f"  当前配置来源: {src}", flush=True)
        print("  以下参数与冻结值不同，改动需预注册 + 用户批准:", flush=True)
        for w in warns:
            print(w, flush=True)
        print("⚠️⚠️⚠️  请确认这是有意的实验配置 ⚠️⚠️⚠️\n", flush=True)


_SINGLETON: Config | None = None


def get_config() -> Config:
    """当前进程配置单例（惰性加载）。

    - 首次调用 → 加载 default.yaml
    - main() 里 load_config(args.config) 会重置单例 → 之后 get_config() 返回自定义
    - 被 import 的脚本未走 main() → 首次 get_config() 用默认 default.yaml
    """
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = load_config(None)
    return _SINGLETON


def load_config(custom_path: str | None = None) -> Config:
    """加载配置。

    custom_path: 覆盖配置文件路径（None → 只用 default.yaml；否则环境变量兜底）。
    """
    # 1. default.yaml 必须存在（无兜底）
    if not DEFAULT_CONFIG.exists():
        raise FileNotFoundError(
            f"配置文件缺失: {DEFAULT_CONFIG}（配置全部在 YAML，缺失将无法运行，请恢复）"
        )
    with open(DEFAULT_CONFIG, encoding="utf-8") as f:
        default_data = yaml.safe_load(f)

    # 2. 确定覆盖文件：CLI 参数 > 环境变量 > 无
    override_path = custom_path
    if override_path is None:
        env_path = os.environ.get(ENV_VAR)
        if env_path:
            override_path = env_path

    # 3. 指定文件不存在 → 报错（无兜底）
    data = default_data
    if override_path:
        p = Path(override_path)
        if not p.is_absolute():
            p = ROOT / p
        if not p.exists():
            raise FileNotFoundError(
                f"指定配置文件不存在: {override_path}（请检查路径；若想用默认配置请勿指定 --config）"
            )
        with open(p, encoding="utf-8") as f:
            override_data = yaml.safe_load(f)
        data = _deep_merge(default_data, override_data)

    # 4. 解析 paths 相对路径 → 绝对路径
    data["paths"] = _resolve_paths(data)

    cfg = Config(data)
    # 5. 冻结参数偏离校验（警告不阻止）
    _check_frozen(cfg, default_data, override_path)
    global _SINGLETON
    _SINGLETON = cfg
    return cfg


def add_config_arg(parser) -> None:
    """给 argparse 加 --config 参数。"""
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "配置文件路径（覆盖 config/default.yaml，缺项用 default；"
            f"也可用 {ENV_VAR} 环境变量）"
        ),
    )


if __name__ == "__main__":
    # 快速自检: python -m quant_futures_01.config [custom.yaml]
    c = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    print("配置加载成功:")
    print(f"  params.window = {c.params.window}")
    print(f"  paths.output = {c.paths.output}")
