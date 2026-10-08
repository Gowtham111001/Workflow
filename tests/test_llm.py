"""Checks the request ClaudeLLM sends and how it handles responses, against a mocked transport."""

import json

import anthropic
import httpx2
import pytest

from jobpilot.apply.answers import DraftAnswer
from jobpilot.llm import ClaudeLLM, LLMError


def client_returning(body: dict, seen: list):
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=body)

    return anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def message(stop_reason="end_turn", text='{"answer": "Hi", "confident": true}', **extra):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}, **extra}


def test_request_shape_and_parse():
    seen = []
    llm = ClaudeLLM("claude-opus-5-5", client_returning(message(), seen))
    out = llm.parse(system="SYS", prompt="Q", schema=DraftAnswer, effort="low")
    assert out == DraftAnswer(answer="Hi", confident=True)

    body = json.loads(seen[0].content)
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "server-side-fallback-2026-07-01" in seen[0].headers["anthropic-beta"]


def test_refusal_raises():
    body = message(stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": None})
    body["content"] = []  # declined before any output
    llm = ClaudeLLM("claude-opus-5-5", client_returning(body, []))
    with pytest.raises(LLMError, match="declined"):
        llm.parse(system="S", prompt="Q", schema=DraftAnswer)


def test_partial_output_raises_llm_error():
    body = message(stop_reason="refusal", text='{"answer": "Hi',
                   stop_details={"type": "refusal", "category": None, "explanation": None})
    llm = ClaudeLLM("claude-opus-5-5", client_returning(body, []))
    with pytest.raises(LLMError):
        llm.parse(system="S", prompt="Q", schema=DraftAnswer)
