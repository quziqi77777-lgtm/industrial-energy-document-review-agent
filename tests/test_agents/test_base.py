"""Agent 基础设施测试。"""

from __future__ import annotations

import json

import pytest

from src.agents.base import AgentResult, Finding, parse_json_response


class TestParseJsonResponse:
    def test_plain_json(self) -> None:
        assert parse_json_response('{"a": 1}') == {"a": 1}

    def test_fenced_json(self) -> None:
        text = "前言。\n```json\n{\"verdict\": \"pass\"}\n```\n结尾。"
        assert parse_json_response(text) == {"verdict": "pass"}

    def test_json_in_text(self) -> None:
        text = "结果是 {\"score\": 92, \"items\": [1, 2]} 而已。"
        assert parse_json_response(text) == {"score": 92, "items": [1, 2]}

    def test_nested_braces(self) -> None:
        text = '{"outer": {"inner": "v"}}'
        assert parse_json_response(text) == {"outer": {"inner": "v"}}

    def test_empty_returns_empty_dict(self) -> None:
        assert parse_json_response("") == {}

    def test_strict_raises_on_empty(self) -> None:
        with pytest.raises(ValueError):
            parse_json_response("", strict=True)

    def test_strict_raises_on_garbage(self) -> None:
        with pytest.raises(ValueError):
            parse_json_response("hello world", strict=True)


class TestAgentResult:
    def test_default_uncertain(self) -> None:
        r = AgentResult(dimension="E1_staffing")
        assert r.verdict == "uncertain"
        assert r.findings == []

    def test_to_dict(self) -> None:
        r = AgentResult(
            dimension="E1_staffing",
            verdict="partial",
            score=14,
            confidence=92,
            findings=[Finding(severity="high", description="missing eng")],
            extra={"k": "v"},
        )
        d = r.to_dict()
        # round-trip JSON
        assert json.dumps(d, ensure_ascii=False)
        assert d["findings"][0]["severity"] == "high"
        assert d["extra"]["k"] == "v"
