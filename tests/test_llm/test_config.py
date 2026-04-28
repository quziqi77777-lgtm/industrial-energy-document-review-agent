"""配置加载测试。"""

from __future__ import annotations

import pytest

from src.config import load_config


def test_load_default_config() -> None:
    cfg = load_config()
    assert "llm" in cfg
    assert "provider" in cfg["llm"]
    assert cfg["llm"]["text_model"]


def test_load_nonexistent_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_paths_expanded(tmp_path) -> None:
    yaml_path = tmp_path / "test.yaml"
    yaml_path.write_text(
        """
llm:
  provider: mock
paths:
  data_dir: ./data
""",
        encoding="utf-8",
    )
    cfg = load_config(yaml_path)
    expected = str((tmp_path.parent / "data").resolve())
    assert cfg["paths"]["data_dir"] == expected
