"""Model-driven seed discovery (discovery-routing batch B).

Every discovered chain must survive a deterministic replay before it is
frozen; discovery failures are recorded as evidence, never raised past the
payload. The LLMRouter case runs fully offline via _http injection — the
real router parses the same decision JSON it would get from a local Ollama.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest

from capability_runtime import Scenario, ScenarioSuite
from capability_runtime.evaluation.base import Evaluator, EvaluationResult
from capability_runtime.regression.seed_discovery import (
    ENTRY_DISCOVERY_FAILED,
    ENTRY_FROZEN,
    ENTRY_REPLAY_FAILED,
    discover_seeds,
)
from capability_runtime.regression.seed_export import (
    ENTRY_REPLAY_FAILED as EXPORT_REPLAY_FAILED,
)
from capability_runtime.router.fake_router import ScenarioScriptedRouter
from capability_runtime.router.llm_router import LLMRouter
from capability_runtime.router.models import RouterConfig

from tests.unit import _seed_bridge_tools as bridge


def _suite() -> ScenarioSuite:
    return ScenarioSuite(
        name="seed_discovery",
        version="0.1",
        scenarios=(
            Scenario(id="S1", query="decide",
                     expected_capabilities=("data.read", "task.decide")),
        ),
    )


class _Fail(Evaluator):
    async def evaluate(self, scenario, result, trace):
        return EvaluationResult(success=False, quality_score=0.0,
                                reason="forced")


def _scripted(routing):
    return _export_discovery(
        lambda scenario: ScenarioScriptedRouter(routing=routing.get(scenario.id, {}))
    )


def _export_discovery(router_factory, suite=None, **kwargs):
    topology = bridge.build_topology()
    return topology, asyncio.run(
        discover_seeds(
            topology,
            suite or _suite(),
            router_factory=router_factory,
            **kwargs,
        )
    )


def test_good_chain_frozen_with_model_discovery_source() -> None:
    routing = {"S1": {"read": ["source_good"], "analyze": ["decide"]}}
    topology, payload = _scripted(routing)

    entries = {entry.scenario_id: entry for entry in payload.entries}
    assert entries["S1"].status == ENTRY_FROZEN
    assert payload.payload["source"] == "model-discovery"
    layers = {
        segment.layer: segment.tools
        for segment in payload.routes["S1"].layers
    }
    assert layers["read"] == ("source_good",)
    assert layers["analyze"] == ("decide",)
    assert payload.topology_fingerprint


def test_failing_tool_yields_discovery_failed() -> None:
    routing = {"S1": {"read": ["source_bad"], "analyze": ["decide"]}}
    topology, payload = _scripted(routing)

    entries = {entry.scenario_id: entry for entry in payload.entries}
    assert entries["S1"].status == ENTRY_DISCOVERY_FAILED
    assert "layer_error" in entries["S1"].reason
    assert "S1" not in payload.routes


def test_pool_illegal_selection_yields_discovery_failed() -> None:
    routing = {"S1": {"read": ["no_such_tool"], "analyze": ["decide"]}}
    topology, payload = _scripted(routing)

    entries = {entry.scenario_id: entry for entry in payload.entries}
    assert entries["S1"].status == ENTRY_DISCOVERY_FAILED
    assert "S1" not in payload.routes


def test_unlisted_layers_finish_without_running_anything() -> None:
    # a script that never lists the read layer finishes at entry: no route,
    # discovery fails honestly (the model proposed to do nothing).
    routing = {"S1": {"analyze": ["decide"]}}
    topology, payload = _scripted(routing)

    entries = {entry.scenario_id: entry for entry in payload.entries}
    assert entries["S1"].status == ENTRY_DISCOVERY_FAILED
    assert "no observed route" in entries["S1"].reason


def test_nondeterministic_chain_must_not_freeze() -> None:
    """A chain that succeeds during discovery but fails its deterministic
    replay is recorded as replay-failed — freezing requires reproducibility."""
    calls = {"count": 0}

    from capability_runtime import tool

    @tool(layer="read", produces=[bridge.Order], capabilities={"data.read"})
    async def once_tool() -> bridge.Order:
        calls["count"] += 1
        if calls["count"] > 1:
            raise RuntimeError("only the first call works")
        return bridge.Order(order_id="once")

    from capability_runtime import LayerRegistry, ToolRegistry, TopologyBuilder

    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "act")):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in (once_tool, bridge.decide):
        tools.register(node)
    topology = TopologyBuilder(layers, tools).build()

    def factory(scenario):
        return ScenarioScriptedRouter(
            routing={"read": ["once_tool"], "analyze": ["decide"]}
        )

    payload = asyncio.run(
        discover_seeds(topology, _suite(), router_factory=factory)
    )
    entries = {entry.scenario_id: entry for entry in payload.entries}
    assert entries["S1"].status == EXPORT_REPLAY_FAILED
    assert "S1" not in payload.routes
    assert calls["count"] == 2  # discovery + replay


def test_llm_router_drives_discovery_offline() -> None:
    """The real LLMRouter parses real decision JSON; a scripted _http body
    proves the model-in-the-seat path works end to end, offline."""
    decisions = iter([
        json.dumps({"action": "execute", "selected_tools": ["source_good"],
                    "reason": "read the source"}),
        json.dumps({"action": "execute", "selected_tools": ["decide"],
                    "reason": "then decide"}),
        json.dumps({"action": "finish", "reason": "done"}),
    ])

    def fake_http(payload):
        return {"choices": [{"message": {"content": next(decisions)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    router_config = RouterConfig(model="fake-router", temperature=0.0,
                                 max_tools_per_layer=3)
    topology, payload = _export_discovery(
        lambda scenario: LLMRouter(router_config=router_config, _http=fake_http)
    )

    entries = {entry.scenario_id: entry for entry in payload.entries}
    assert entries["S1"].status == ENTRY_FROZEN
    layers = {
        segment.layer: segment.tools
        for segment in payload.routes["S1"].layers
    }
    assert layers["read"] == ("source_good",)
    assert layers["analyze"] == ("decide",)


def test_evaluator_gates_discovery() -> None:
    routing = {"S1": {"read": ["source_good"], "analyze": ["decide"]}}
    topology, payload = _export_discovery(
        lambda scenario: ScenarioScriptedRouter(routing=routing.get(scenario.id, {})),
        evaluator=_Fail(),
    )
    entries = {entry.scenario_id: entry for entry in payload.entries}
    assert entries["S1"].status == ENTRY_DISCOVERY_FAILED
    assert "evaluation failed" in entries["S1"].reason


def test_invalid_arguments_rejected() -> None:
    topology = bridge.build_topology()
    with pytest.raises(Exception):
        asyncio.run(
            discover_seeds(
                topology,
                _suite(),
                router_factory=lambda s: ScenarioScriptedRouter(),
                discovery_trials=0,
            )
        )
