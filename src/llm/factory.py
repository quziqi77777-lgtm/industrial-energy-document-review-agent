"""LLMProvider 工厂：根据配置返回具体后端实例。

新增后端 = 在此添加分支。业务代码全部使用 `build_provider()` 拿 Provider，便于测试替换。
"""

from __future__ import annotations

from typing import Any

from .api_provider import OpenAICompatibleProvider
from .mock_provider import MockProvider
from .provider import LLMProvider


def build_provider(config: dict[str, Any]) -> LLMProvider:
    """根据 `config['llm']` 字段构造 Provider。"""
    llm_cfg = config.get("llm", {})
    provider_kind = llm_cfg.get("provider", "api")

    if provider_kind == "mock":
        mock_cfg = llm_cfg.get("mock", {})
        return MockProvider(
            text_response=mock_cfg.get("text_response", ""),
            vision_response=mock_cfg.get("vision_response", ""),
            responses=mock_cfg.get("responses"),
        )

    if provider_kind == "api":
        api_cfg = llm_cfg.get("api", {})
        return OpenAICompatibleProvider(
            base_url=api_cfg.get("base_url", "http://localhost:8000/v1"),
            api_key=api_cfg.get("api_key", "EMPTY"),
            timeout=api_cfg.get("timeout", 120.0),
            max_retries=api_cfg.get("max_retries", 2),
        )

    raise ValueError(f"未知的 LLM provider 类型: {provider_kind}")
