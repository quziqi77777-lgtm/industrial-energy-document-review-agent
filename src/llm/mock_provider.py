"""测试 / 离线开发用的 Mock Provider。

支持两种模式：
1. 固定响应：构造时传入 `text_response` / `vision_response`
2. 路由响应：构造时传入 `responses` dict，按 model 名称返回不同内容
3. 回调响应：传入 `text_handler` / `vision_handler`，让测试动态控制
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from .provider import LLMProvider, Message


class MockProvider(LLMProvider):
    """可控、不联网的 Provider；记录所有调用便于断言。"""

    def __init__(
        self,
        text_response: str = "",
        vision_response: str = "",
        responses: dict[str, str] | None = None,
        text_handler: Callable[[list[Message], str], str] | None = None,
        vision_handler: Callable[[Path, str, str], str] | None = None,
    ) -> None:
        self.text_response = text_response
        self.vision_response = vision_response
        self.responses = responses or {}
        self.text_handler = text_handler
        self.vision_handler = vision_handler

        self.text_calls: list[dict] = []
        self.vision_calls: list[dict] = []

    def call_text(
        self,
        messages: Iterable[Message],
        model: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float | None = None,
    ) -> str:
        msgs = list(messages)
        self.text_calls.append(
            {
                "model": model,
                "messages": msgs,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
            }
        )
        if self.text_handler is not None:
            return self.text_handler(msgs, model)
        if model in self.responses:
            return self.responses[model]
        return self.text_response

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
        path = Path(image_path)
        self.vision_calls.append(
            {
                "image_path": path,
                "prompt": prompt,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "system": system,
            }
        )
        if self.vision_handler is not None:
            return self.vision_handler(path, prompt, model)
        if model in self.responses:
            return self.responses[model]
        return self.vision_response

    def health_check(self) -> bool:
        return True
