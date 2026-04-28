"""LLMProvider 抽象基类与共用数据结构。

所有 agent / pipeline 代码都只依赖本模块定义的接口，不直接 import 后端。
新增模型后端 = 新增一个 Provider 实现 + 在 factory 注册。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal


Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    """OpenAI 风格的对话消息。"""

    role: Role
    content: str


class LLMError(Exception):
    """LLM 调用基类异常。"""


class LLMTimeout(LLMError):
    """超时。调用方应根据上下文决定降级或重试。"""


class LLMResponseError(LLMError):
    """API 返回错误（非 2xx 或格式错误）。"""


class LLMProvider(ABC):
    """所有 LLM 后端的统一接口。

    设计要点：
    - `call_text` 与 `call_vision` 都返回 **字符串**；上层负责 JSON 解析与 schema 校验。
    - 入参的 `model` 显式声明便于运行期切换；调用方读 config 后传入。
    - 任何后端错误统一抛 `LLMError` 子类，调用方据此决策。
    """

    @abstractmethod
    def call_text(
        self,
        messages: Iterable[Message],
        model: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float | None = None,
    ) -> str:
        """文本对话。返回 assistant 消息的纯文本内容。"""

    @abstractmethod
    def call_vision(
        self,
        image_path: str | Path,
        prompt: str,
        model: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float | None = None,
        system: str | None = None,
    ) -> str:
        """单图 + prompt 的多模态调用。返回模型生成的纯文本（可能是 JSON 字符串）。"""

    def health_check(self) -> bool:
        """轻量健康检查；默认返回 True，子类可覆盖。"""
        return True
