"""LLM 抽象层：业务代码只依赖 LLMProvider 接口，不与具体后端耦合。"""

from .provider import (
    LLMProvider,
    Message,
    LLMError,
    LLMTimeout,
    LLMResponseError,
)
from .factory import build_provider

__all__ = [
    "LLMProvider",
    "Message",
    "LLMError",
    "LLMTimeout",
    "LLMResponseError",
    "build_provider",
]
