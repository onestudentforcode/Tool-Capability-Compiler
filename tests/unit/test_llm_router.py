from __future__ import annotations

import asyncio
import json

import pytest

from capability_runtime import (
    InvalidRoutingDecisionError,
    InvalidToolSelectionError,
    LLMRouter,
    RoutingAction,
    RouterConfig,
    RoutingContext,
    RoutingDecision,
    RoutingError,
    ToolSummary,
)
from capability_runtime.router.llm_router import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
)


def run(coro):
    return asyncio.run(coro)


AVAILABLE = (ToolSummary("db", "analyze"), ToolSummary("rag", "analyze"))


def summary(name: str, layer: str = "analyze") -> ToolSummary:
    return ToolSummary(name=name, layer=layer)


def make_router(content: str, *, max_tools: int = 2, **cfg_kwargs):
    cfg = RouterConfig(
        model="qwen3:1.7b",
        temperature=0.2,
        max_tools_per_layer=max_tools,
        **cfg_kwargs,
    )
    fake_http = lambda payload: content  # mirrors _chat: returns inner JSON
    return LLMRouter(router_config=cfg, _http=fake_http)


def context(
    available,
    *,
    layer: str = "analyze",
    query: str = "can order 001 be refunded",
    **kw,
) -> RoutingContext:
    return RoutingContext(
        query=query,
        current_layer=layer,
        available_tools=available,
        topology_version="1.0",
        **kw,
    )


def execute_response(*tools: str, reason: str = "proceed") -> str:
    return json.dumps({"action": "execute", "selected_tools": list(tools), "reason": reason})


def test_selects_single_available_tool() -> None:
    router = make_router(execute_response("db"))
    decision = run(router.route(context(AVAILABLE)))
    assert decision.action is RoutingAction.EXECUTE
    assert decision.selected_tools == ("db",)


def test_allows_multiple_tools_in_one_layer() -> None:
    router = make_router(execute_response("db", "rag"))
    decision = run(router.route(context(AVAILABLE)))
    assert decision.selected_tools == ("db", "rag")


def test_finishes() -> None:
    router = make_router(json.dumps({"action": "finish", "reason": "goal met"}))
    decision = run(router.route(context(AVAILABLE)))
    assert decision.action is RoutingAction.FINISH
    assert decision.selected_tools == ()


def test_rejects_unavailable_tool() -> None:
    router = make_router(execute_response("ghost"))
    with pytest.raises(InvalidToolSelectionError):
        run(router.route(context(AVAILABLE)))


def test_rejects_over_limit() -> None:
    router = make_router(execute_response("db", "rag"), max_tools=1)
    with pytest.raises(InvalidRoutingDecisionError):
        run(router.route(context(AVAILABLE)))


def test_tolerates_duplicate_selection() -> None:
    router = make_router(execute_response("db", "db"))
    decision = run(router.route(context(AVAILABLE)))
    assert decision.selected_tools == ("db",)


def test_rejects_malformed_json() -> None:
    router = make_router("not-json")
    with pytest.raises(RoutingError):
        run(router.route(context(AVAILABLE)))


def test_rejects_invalid_action() -> None:
    router = make_router(json.dumps({"action": "skip", "selected_tools": ["db"]}))
    with pytest.raises(RoutingError):
        run(router.route(context(AVAILABLE)))


def test_rejects_non_list_selected_tools() -> None:
    router = make_router(json.dumps({"action": "execute", "selected_tools": "db"}))
    with pytest.raises(RoutingError):
        run(router.route(context(AVAILABLE)))


def test_finishes_when_no_tools_available() -> None:
    router = make_router(execute_response("db"))
    decision = run(router.route(context((), layer="read")))
    assert decision.action is RoutingAction.FINISH
    assert decision.selected_tools == ()


def test_payload_lists_tools_and_records_config() -> None:
    seen: dict = {}

    def fake_http(payload):
        seen["payload"] = payload
        return execute_response("db")

    cfg = RouterConfig(model="qwen3:1.7b", temperature=0.5, max_tools_per_layer=2)
    router = LLMRouter(router_config=cfg, _http=fake_http)
    run(router.route(context(AVAILABLE)))

    assert seen["payload"]["model"] == "qwen3:1.7b"
    assert seen["payload"]["temperature"] == 0.5
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    user = seen["payload"]["messages"][1]["content"]
    assert "Layer to route: analyze" in user
    assert "- db" in user and "- rag" in user
    assert "AVAILABLE tools for this layer:" in user
    system = seen["payload"]["messages"][0]["content"]
    assert "action" in system and "selected_tools" in system

    # record config so a Trial can bind model/prompt/exploration
    assert router.model == "qwen3:1.7b"
    assert router.prompt_version == "1"
    assert router.exploration_mode == "free"
    assert router.base_url == DEFAULT_BASE_URL
    assert router.router_config is cfg


def test_guided_mode_prompts_hint() -> None:
    seen: dict = {}

    def fake_http(payload):
        seen["payload"] = payload
        return execute_response("db")

    router = make_router(None, max_tools=2, exploration_mode="guided")
    router._http = fake_http  # reuse; make_router default lambda is replaced
    run(router.route(context(AVAILABLE)))
    assert "guided" in seen["payload"]["messages"][1]["content"]


def test_default_timeout_constant_exists() -> None:
    assert DEFAULT_TIMEOUT_SECONDS == 60.0
    assert isinstance(DEFAULT_BASE_URL, str)