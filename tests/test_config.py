"""配置框架测试：不读写真实数据文件（默认配置在 config/default.yaml，覆盖用 tmp_path）。"""

from pathlib import Path

import pytest

from quant_futures_01 import config as cfgmod


@pytest.fixture()
def tmp_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 DEFAULT_CONFIG 指到临时 default.yaml（含冻结参数），隔离真实文件。"""
    d = tmp_path / "default.yaml"
    d.write_text(
        """
paths:
  data: "data"
  output: "output"
params:
  window: 21
  top_ratio: 0.2
limits:
  min_n: 50
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG", d)
    return d


def _write_custom(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "custom.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_deep_merge() -> None:
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    override = {"a": {"c": 9}, "e": 4}
    merged = cfgmod._deep_merge(base, override)
    assert merged == {"a": {"b": 1, "c": 9}, "d": 3, "e": 4}


def test_load_default(tmp_default: Path) -> None:
    cfg = cfgmod.load_config()
    assert cfg.params.window == 21
    assert cfg["params"]["top_ratio"] == 0.2
    # paths 段解析为相对仓库根的绝对路径
    assert cfg.paths.output == str(Path(cfgmod.ROOT) / "output")


def test_custom_override(tmp_default: Path, tmp_path: Path) -> None:
    custom = _write_custom(tmp_path, "params:\n  window: 42\n")
    cfg = cfgmod.load_config(str(custom))
    assert cfg.params.window == 42  # 覆盖生效
    assert cfg.limits.min_n == 50  # 缺项继承 default


def test_env_override(
    tmp_default: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = _write_custom(tmp_path, "params:\n  window: 7\n")
    monkeypatch.setenv(cfgmod.ENV_VAR, str(custom))
    cfg = cfgmod.load_config()
    assert cfg.params.window == 7


def test_missing_custom_file(tmp_default: Path, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cfgmod.load_config(str(tmp_path / "nope.yaml"))


def test_missing_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG", tmp_path / "nope.yaml")
    with pytest.raises(FileNotFoundError):
        cfgmod.load_config()


def test_frozen_warning(
    tmp_default: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    custom = _write_custom(tmp_path, "params:\n  window: 42\n")
    cfgmod.load_config(str(custom))
    out = capsys.readouterr().out
    assert "配置偏离冻结参数" in out
    assert "窗口" in out


def test_attr_missing(tmp_default: Path) -> None:
    cfg = cfgmod.load_config()
    with pytest.raises(AttributeError):
        _ = cfg.nope


def test_get_with_default(tmp_default: Path) -> None:
    cfg = cfgmod.load_config()
    assert cfg.get("params", "window") == 21
    assert cfg.get("nope", "x") is None
    assert cfg.get("nope", "x", default=7) == 7


def test_singleton() -> None:
    # 真实 config/default.yaml 存在（脚手架自带）→ 惰性单例同一对象
    cfgmod._SINGLETON = None
    first = cfgmod.get_config()
    second = cfgmod.get_config()
    assert first is second
    assert first.params.window == 21
