from __future__ import annotations

import asyncio
import json

import pytest

from capability_runtime import (
    CapabilityResolution,
    CapabilityResolutionError,
    OllamaCapabilityResolver,
)
from capability_runtime.capability.ollama_resolver import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
)


def run(coro):
    return asyncio.run(coro)


AVAILABLE = frozenset(
    {
        "order.read",
        "order.search",
        "refund.policy.check",
        "refund.execute",
        "email.send",
    }
)


def make_resolver(content: str, **kwargs):
    # fake _http mirrors _chat: it returns the inner capability JSON, not the
    # OpenAI chat envelope.
    fake_http = lambda payload: content
    return OllamaCapabilityResolver(_http=fake_http, **kwargs)


def test_resolves_query_to_structure_capabilities() -> None:
    content = json.dumps(
        {
            "required": ["order.read", "refund.policy.check"],
            "optional": [],
            "missing_capability_hints": [],
            "confidence": 0.95,
            "reasoning": "need order + policy check",
        }
    )
    resolver = make_resolver(content)
    result = run(resolver.resolve("订单123能不能退款", AVAILABLE))
    assert result == CapabilityResolution(
        required=("order.read", "refund.policy.check"),
        confidence=0.95,
        reasoning="need order + policy check",
    )


def test_exposes_hint_for_missing_capability() -> None:
    content = json.dumps(
        {
            "required": ["order.read"],
            "optional": [],
            "missing_capability_hints": ["order.update"],
            "confidence": 0.4,
            "reasoning": None,
        }
    )
    resolver = make_resolver(content)
    result = run(resolver.resolve("改配送地址", frozenset({"order.read"})))
    assert result.required == ("order.read",)
    assert result.missing_capability_hints == ("order.update",)
    assert result.confidence == 0.4


def test_payload_marks_json_output_and_lists_available() -> None:
    seen: dict = {}
    empty = json.dumps(
        {"required": [], "optional": [], "missing_capability_hints": [], "confidence": 0.9}
    )

    def fake_http(payload):
        seen["payload"] = payload
        return empty

    resolver = OllamaCapabilityResolver(_http=fake_http)
    run(resolver.resolve("hello", AVAILABLE))
    assert seen["payload"]["model"] == DEFAULT_MODEL
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert "- order.read" in seen["payload"]["messages"][1]["content"]


def test_rejects_malformed_json() -> None:
    resolver = make_resolver("not-json")
    with pytest.raises(CapabilityResolutionError):
        run(resolver.resolve("query", AVAILABLE))


def test_rejects_unavailable_selected_capability() -> None:
    content = json.dumps(
        {
            "required": ["order.update"],
            "optional": [],
            "missing_capability_hints": [],
            "confidence": 0.9,
        }
    )
    resolver = make_resolver(content)
    with pytest.raises(CapabilityResolutionError):
        run(resolver.resolve("改地址", AVAILABLE))


def test_rejects_hint_that_is_already_available() -> None:
    content = json.dumps(
        {
            "required": ["order.read"],
            "optional": [],
            "missing_capability_hints": ["order.read"],
            "confidence": 0.9,
        }
    )
    resolver = make_resolver(content)
    with pytest.raises(CapabilityResolutionError):
        run(resolver.resolve("query", AVAILABLE))


def test_rejects_confidence_out_of_range() -> None:
    content = json.dumps(
        {
            "required": ["order.read"],
            "optional": [],
            "missing_capability_hints": [],
            "confidence": 1.5,
        }
    )
    resolver = make_resolver(content)
    with pytest.raises(CapabilityResolutionError):
        run(resolver.resolve("query", AVAILABLE))


def test_rejects_non_list_field() -> None:
    content = json.dumps(
        {
            "required": "order.read",
            "optional": [],
            "missing_capability_hints": [],
            "confidence": 0.9,
        }
    )
    resolver = make_resolver(content)
    with pytest.raises(CapabilityResolutionError):
        run(resolver.resolve("query", AVAILABLE))


def test_explicit_options_override_defaults() -> None:
    resolver = OllamaCapabilityResolver(
        base_url="http://127.0.0.1:9999",
        model="qwen3:8b",
        dotenv_path=None,
    )
    assert resolver.base_url == "http://127.0.0.1:9999"
    assert resolver.model == "qwen3:8b"


def test_dotenv_loader_populates_environment(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    env_file = tmp_path / "envs"
    env_file.write_text(
        "LLM_BASE_URL=http://ollama.local:11434\nLLM_MODEL=qwen3:1.7b\n",
        encoding="utf-8",
    )
    resolver = OllamaCapabilityResolver(dotenv_path=env_file)
    assert resolver.base_url == "http://ollama.local:11434"
    assert resolver.model == "qwen3:1.7b"


def test_http_failure_is_wrapped_as_domain_error() -> None:
    def fake_http(payload):
        raise CapabilityResolutionError("Ollama HTTP 500")

    resolver = OllamaCapabilityResolver(_http=fake_http)
    with pytest.raises(CapabilityResolutionError):
        run(resolver.resolve("query", AVAILABLE))


def test_explicit_defaults_when_env_absent(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)
    resolver = OllamaCapabilityResolver(dotenv_path=None)
    assert resolver.model == DEFAULT_MODEL
    assert resolver.base_url == DEFAULT_BASE_URL