"""LLMProvider 接口契约测试。

不访问真实模型；用 MockProvider 验证：
- call_text / call_vision 接口签名与返回类型
- 异常类型继承
- 运行期模型可切换
"""

from __future__ import annotations

import pytest

from src.llm import (
    LLMError,
    LLMResponseError,
    LLMTimeout,
    Message,
    build_provider,
)
from src.llm.mock_provider import MockProvider


class TestMessage:
    def test_message_is_frozen(self) -> None:
        m = Message(role="user", content="hi")
        with pytest.raises(Exception):
            m.role = "system"  # type: ignore[misc]

    def test_role_values(self) -> None:
        for role in ("system", "user", "assistant"):
            m = Message(role=role, content="x")
            assert m.role == role


class TestExceptionHierarchy:
    def test_timeout_is_llm_error(self) -> None:
        assert issubclass(LLMTimeout, LLMError)

    def test_response_error_is_llm_error(self) -> None:
        assert issubclass(LLMResponseError, LLMError)


class TestMockProvider:
    def test_call_text_returns_fixed_response(self) -> None:
        p = MockProvider(text_response="ok")
        out = p.call_text(
            messages=[Message(role="user", content="hello")],
            model="Qwen3-2B",
        )
        assert out == "ok"
        assert len(p.text_calls) == 1
        assert p.text_calls[0]["model"] == "Qwen3-2B"

    def test_call_text_routes_by_model(self) -> None:
        p = MockProvider(
            responses={"Qwen3-2B": "small", "Qwen2.5-72B": "large"},
        )
        assert p.call_text([Message(role="user", content="x")], model="Qwen3-2B") == "small"
        assert (
            p.call_text([Message(role="user", content="x")], model="Qwen2.5-72B") == "large"
        )

    def test_call_text_handler_overrides(self) -> None:
        def handler(msgs: list[Message], model: str) -> str:
            return f"saw {len(msgs)} messages on {model}"

        p = MockProvider(text_handler=handler)
        out = p.call_text(
            [Message(role="system", content="s"), Message(role="user", content="u")],
            model="m",
        )
        assert out == "saw 2 messages on m"

    def test_call_vision_records_call(self, tmp_path) -> None:
        img = tmp_path / "fake.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        p = MockProvider(vision_response='{"ok": true}')
        out = p.call_vision(image_path=img, prompt="describe", model="Qwen3-VL-4B")
        assert out == '{"ok": true}'
        assert len(p.vision_calls) == 1
        assert p.vision_calls[0]["image_path"] == img
        assert p.vision_calls[0]["model"] == "Qwen3-VL-4B"

    def test_health_check(self) -> None:
        assert MockProvider().health_check() is True


class TestBuildProvider:
    def test_build_mock(self) -> None:
        cfg = {
            "llm": {
                "provider": "mock",
                "mock": {"text_response": "hello"},
            }
        }
        p = build_provider(cfg)
        assert isinstance(p, MockProvider)
        assert p.call_text([Message(role="user", content="x")], model="any") == "hello"

    def test_build_api(self) -> None:
        from src.llm.api_provider import OpenAICompatibleProvider

        cfg = {
            "llm": {
                "provider": "api",
                "api": {
                    "base_url": "http://localhost:8000/v1",
                    "api_key": "EMPTY",
                    "timeout": 30.0,
                },
            }
        }
        p = build_provider(cfg)
        assert isinstance(p, OpenAICompatibleProvider)
        assert p.base_url == "http://localhost:8000/v1"

    def test_build_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            build_provider({"llm": {"provider": "telepathy"}})


class TestApiProviderEncoding:
    """API provider 不发实际请求，只验证图片编码。"""

    def test_encode_image_round_trips(self, tmp_path) -> None:
        from src.llm.api_provider import _encode_image

        img = tmp_path / "x.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x01")
        url = _encode_image(img)
        assert url.startswith("data:image/png;base64,")

    def test_encode_image_missing_raises(self, tmp_path) -> None:
        from src.llm.api_provider import _encode_image

        with pytest.raises(FileNotFoundError):
            _encode_image(tmp_path / "no.png")
