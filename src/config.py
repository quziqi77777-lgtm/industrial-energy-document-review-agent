"""统一配置加载。

YAML 文件先于环境变量加载，运行期可由 CLI / pytest 通过 `load_config(path)` 切换。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default.yaml"


def _expand(value: Any, base_dir: Path) -> Any:
    """递归展开路径：以 './' 开头的字符串视为相对仓库根的路径。"""
    if isinstance(value, str) and value.startswith("./"):
        return str((base_dir / value[2:]).resolve())
    if isinstance(value, dict):
        return {k: _expand(v, base_dir) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, base_dir) for v in value]
    return value


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """加载 YAML 配置；不缓存便于测试切换。"""
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    repo_root = config_path.parent.parent
    return _expand(raw, repo_root)


@lru_cache(maxsize=1)
def get_default_config() -> dict[str, Any]:
    """缓存默认配置；测试时用 `load_config()` 直接调，避开缓存。"""
    override = os.environ.get("INDUSTRY_AGENT_CONFIG")
    return load_config(override) if override else load_config()
