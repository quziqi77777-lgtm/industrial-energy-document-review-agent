"""OpenAI-compatible HTTP 后端。

可对接：vLLM (`vllm serve ...`)、Ollama (`/v1/chat/completions`)、LM Studio、本地代理。
切换模型后端只需改 `config/default.yaml` 的 `llm.api.base_url`，业务代码不变。
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Iterable

import httpx

from .provider import LLMError, LLMProvider, LLMResponseError, LLMTimeout, Message


log = logging.getLogger(__name__)


def _encode_image(image_path: str | Path) -> str:
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"图片不存在: {p}")
    mime, _ = mimetypes.guess_type(p.name)
    mime = mime or "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


class OpenAICompatibleProvider(LLMProvider):
    """对接 OpenAI Chat Completions 协议的本地或远端服务。"""

    def __init__(
        self,
        base_url: str,
        api_key: str = "EMPTY",
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.Client(timeout=timeout)

    def __del__(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def _post(self, url: str, payload: dict, timeout: float | None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=timeout or self.default_timeout,
                )
                if resp.status_code >= 400:
                    raise LLMResponseError(
                        f"LLM 服务返回 {resp.status_code}: {resp.text[:300]}"
                    )
                return resp.json()
            except httpx.TimeoutException as e:
                last_exc = e
                log.warning("LLM 调用超时（attempt %d/%d）", attempt + 1, self.max_retries + 1)
            except httpx.HTTPError as e:
                last_exc = e
                log.warning("LLM HTTP 错误：%s", e)
        if isinstance(last_exc, httpx.TimeoutException):
            raise LLMTimeout(str(last_exc)) from last_exc
        raise LLMError(f"LLM 调用失败: {last_exc}") from last_exc

    def call_text(
        self,
        messages: Iterable[Message],
        model: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float | None = None,
    ) -> str:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = self._post(f"{self.base_url}/chat/completions", payload, timeout)
        return _extract_content(data)

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
        image_data_url = _encode_image(image_path)
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": content})
        payload = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = self._post(f"{self.base_url}/chat/completions", payload, timeout)
        return _extract_content(data)

    def health_check(self) -> bool:
        try:
            resp = self._client.get(f"{self.base_url}/models", timeout=5.0)
            return resp.status_code < 500
        except Exception:
            return False


def _extract_content(resp: dict) -> str:
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LLMResponseError(f"无法解析 LLM 响应: {resp}") from e
