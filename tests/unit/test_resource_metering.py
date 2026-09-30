"""Resource metering milestone: collectors, handles, and the access dimension.

Covers resource-metering.md batches A-E offline: contextvar ownership and
concurrent isolation, the three honesty tiers, memory / llm / metered()
handles, access aggregation through TrialResult -> route stats -> profile ->
online telemetry, and drift findings (facts only, never modifications).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from capability_runtime import (
    ExecutionEnvironment,
    ExecutionContext,
    ExecutionState,
    LayerExecutor,
    MeteringSource,
    TokenUsage,
    ToolExecutionStatus,
    ToolExecutor,
    drift_findings,
    estimate_tokens,
    metered,
)
from capability_runtime.core.errors import (
    MeteringContextError,
    ResourceHandleError,
)
from capability_runtime.resources import (
    InMemoryStore,
    LLMResource,
    mount_collector,
    unmount_collector,
)

_PROJECT = Path(__file__).resolve().parents[2]
_DEMO = _PROJECT / "examples" / "slow_refund"


# ---- batch A: collector core -------------------------------------------------


def test_collector_starts_declared_and_counts_access() -> None:
    from capability_runtime.resources import MeteringCollector

    collector = MeteringCollector()
    assert collector.source is MeteringSource.DECLARED
    assert collector.access_counts == {}
    assert collector.tokens is None
    assert collector.measured_cost is None

    collector.record_access("sandbox_orders", "read")
    collector.record_access("sandbox_orders", "read")
    collector.record_access("sandbox_refunds", "write")
    assert collector.access_counts == {
        "sandbox_orders[read]": 2,
        "sandbox_refunds[write]": 1,
    }
    assert collector.source is MeteringSource.MEASURED


def test_collector_estimated_tier_is_flagged() -> None:
    from capability_runtime.resources import MeteringCollector

    collector = MeteringCollector()
    collector.record_estimated_tokens(TokenUsage(input_tokens=12))
    assert collector.tokens == TokenUsage(input_tokens=12)
    assert collector.source is MeteringSource.ESTIMATED
    collector.record_tokens(TokenUsage(input_tokens=3, output_tokens=4))
    assert collector.tokens == TokenUsage(input_tokens=15, output_tokens=4)
    # mixed estimated + measured tokens stay at the weakest honest claim
    assert collector.source is MeteringSource.ESTIMATED


def test_current_collector_outside_context_is_an_error() -> None:
    from capability_runtime.resources import current_collector

    with pytest.raises(MeteringContextError, match="outside a tool-call context"):
        current_collector()


def test_estimate_tokens_is_rough_but_positive() -> None:
    assert estimate_tokens("x" * 400) == 100
    assert estimate_tokens("") == 1


def test_drift_findings_report_facts_only() -> None:
    findings = drift_findings(
        {"tool_a": (0.03, 10), "tool_b": (0.02, 10), "tool_c": (5.0, 2)},
        {"tool_a": 0.01, "tool_b": 0.01},
        threshold=0.5,
    )
    by_tool = {f.tool: f for f in findings}
    assert set(by_tool) == {"tool_a", "tool_b"}  # tool_c has no declaration
    assert by_tool["tool_a"].drift_ratio == pytest.approx(2.0)
    assert "metadata_review_candidate" in by_tool["tool_a"].summary
    assert "never" not in by_tool["tool_a"].summary  # a fact, not an action


# ---- batch A: executor settlement ------------------------------------------------


def _run_tool(tool_node, state=None, timeout=None):
    executor = ToolExecutor(
        ExecutionContext(
            environment=ExecutionEnvironment.SANDBOX,
            max_concurrency=1,
            per_tool_timeout_seconds=timeout,
        )
    )
    return asyncio.run(executor.execute(tool_node, state or ExecutionState(query="q")))


def test_execution_without_handles_stays_declared() -> None:
    from tests.unit._slow_helpers import db

    execution = _run_tool(db)
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.metering_source is MeteringSource.DECLARED
    assert execution.access_counts is None
    assert execution.measured_cost is None


def test_metered_wrapper_counts_onto_the_execution() -> None:
    from capability_runtime import tool

    async def get_order(key: str) -> dict:
        return {"amount": 10.0}

    ext = metered("mysql.orders", access="read", invoke=get_order)

    @tool(layer="read")
    async def fetcher() -> dict:
        return await ext("ORD-1")

    execution = _run_tool(fetcher)
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.metering_source is MeteringSource.MEASURED
    assert execution.access_counts == {"mysql.orders[read]": 1}


def test_metered_bills_the_attempt_even_on_error() -> None:
    from capability_runtime import tool

    async def boom(_key: str) -> None:
        raise RuntimeError("backend down")

    ext = metered("mysql.orders", access="read", invoke=boom)

    @tool(layer="read")
    async def fetcher() -> None:
        return await ext("ORD-1")

    execution = _run_tool(fetcher)
    assert execution.status is ToolExecutionStatus.ERROR
    assert execution.access_counts == {"mysql.orders[read]": 1}


def test_metered_rejects_non_callable() -> None:
    with pytest.raises(ResourceHandleError):
        metered("x", access="read", invoke="not-callable")


def test_metered_estimate_records_estimated_tokens() -> None:
    from capability_runtime import tool

    async def black_box(prompt: str) -> str:
        return "answer"

    ext = metered(
        "llm.blackbox", access="read", invoke=black_box, estimate=estimate_tokens
    )

    @tool(layer="analyze")
    async def asker() -> str:
        return await ext("a" * 200)

    execution = _run_tool(asker)
    assert execution.metering_source is MeteringSource.ESTIMATED
    assert execution.token_usage == TokenUsage(input_tokens=50)


def test_concurrent_tools_do_not_cross_book() -> None:
    """Same-layer parallel tools each settle onto their own execution."""
    from capability_runtime import tool
    from capability_runtime.core.tool import ToolNode

    store_a = InMemoryStore("res_a")
    store_b = InMemoryStore("res_b")
    store_a.seed({"k": 1})
    store_b.seed({"k": 2})

    @tool(layer="read")
    async def touch_a() -> int:
        return await store_a.get("k")

    @tool(layer="read")
    async def touch_b() -> int:
        return await store_b.get("k")

    async def run() -> tuple:
        executor = LayerExecutor(
            context=ExecutionContext(environment=ExecutionEnvironment.SANDBOX)
        )
        return await executor.run([touch_a, touch_b], ExecutionState(query="q"))

    first, second = asyncio.run(run())
    by_name = {first.tool_name: first, second.tool_name: second}
    assert by_name["touch_a"].access_counts == {"res_a[read]": 1}
    assert by_name["touch_b"].access_counts == {"res_b[read]": 1}
    assert "res_b" not in by_name["touch_a"].access_counts


# ---- batch B: memory handle + sandbox migration ---------------------------------------


def test_memory_store_admin_paths_are_unmetered() -> None:
    store = InMemoryStore("s")
    store.seed({"a": 1})          # no collector context needed
    store.clear()
    assert store.snapshot() == {}
    with pytest.raises(MeteringContextError):
        asyncio.run(store.get("a"))  # metered access requires a context


def test_sandbox_run_carries_access_counts_end_to_end() -> None:
    sys.path.insert(0, str(_DEMO))
    import refund
    import store
    from capability_runtime import (
        FakeRouter,
        Scenario,
        ScenarioSuite,
        SlowRegressionRunner,
        build_observation_stats,
    )
    from capability_runtime.ranking import build_profiles, rows_from_results

    store.STORE.reset("eligible")
    topology, version = refund.build_topology(topology_version="v0.4.0")
    router = FakeRouter(
        layer_selections={
            "read": ["order_db"],
            "analyze": ["policy_check"],
            "action": ["refund_api"],
        }
    )
    suite = ScenarioSuite(
        name="metered",
        version="1.0",
        description="metered",
        scenarios=(Scenario(id="s1", query="refund"),),
    )
    outcome = asyncio.run(
        SlowRegressionRunner(
            topology=topology,
            evaluator=refund.build_evaluator(),
            fixture_manager=None,
            trials_per_scenario=1,
            topology_version=version,
            router_config_id="fake",
            router=router,
        ).run(suite)
    )
    result = outcome.results[0]
    # trial level: one read of orders, one write of refunds (rag/web/policy stay declared)
    assert result.access_counts == {
        "sandbox_orders[read]": 1,
        "sandbox_refunds[write]": 1,
    }
    edges = [(e.source, e.target) for e in topology.edges()]
    obs = build_observation_stats(outcome.results, edges=edges)
    (stat,) = obs.route_stats.values()
    assert stat.access_counts == result.access_counts

    profile = build_profiles(rows_from_results(outcome.results))[0]
    assert profile.access_counts == result.access_counts


# ---- batch C: llm handle ----------------------------------------------------------------


def _llm_body(content: str, usage: dict) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": usage,
    }


def test_llm_resource_meters_precise_tokens_and_cost() -> None:
    resource = LLMResource(
        model="qwen3:1.7b",
        input_cost_per_1k=0.5,
        output_cost_per_1k=1.0,
        _http=lambda payload: _llm_body("ok", {"prompt_tokens": 200, "completion_tokens": 100}),
    )

    from capability_runtime import tool

    @tool(layer="analyze")
    async def asker() -> str:
        response = await resource.complete("summarize")
        return response.text

    execution = _run_tool(asker)
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.metering_source is MeteringSource.MEASURED
    assert execution.token_usage == TokenUsage(input_tokens=200, output_tokens=100)
    assert execution.measured_cost == pytest.approx(0.1 + 0.1)
    # billing basis unchanged: still the declared cost (None here — undeclared)
    assert execution.cost is None


def test_llm_resource_degrades_without_usage() -> None:
    resource = LLMResource(
        model="m", _http=lambda payload: {"choices": [{"message": {"content": "hi"}}]}
    )

    async def call() -> None:
        await resource.complete("q")

    token = mount_collector()
    try:
        asyncio.run(call())
    finally:
        collector = unmount_collector(token)
    assert collector.tokens is None
    assert collector.measured_cost is None


def test_llm_resource_string_fake_and_validation() -> None:
    resource = LLMResource(model="m", _http=lambda payload: "plain-text")
    token = mount_collector()
    try:
        response = asyncio.run(resource.complete("q"))
    finally:
        unmount_collector(token)
    assert response.text == "plain-text"
    assert response.usage is None

    with pytest.raises(ResourceHandleError):
        LLMResource(model=" ")
    with pytest.raises(ResourceHandleError):
        LLMResource(model="m", input_cost_per_1k=-1)


def test_llm_resource_requires_metering_context() -> None:
    resource = LLMResource(model="m", _http=lambda payload: "x")
    with pytest.raises(MeteringContextError):
        asyncio.run(resource.complete("q"))


# ---- batch E: serialization round-trips ---------------------------------------------------


def test_access_counts_survive_disk_roundtrip(tmp_path) -> None:
    """traces.jsonl -> rows_from_run keeps the access dimension (phase5 path)."""
    from capability_runtime import rows_from_run
    from capability_runtime.ranking import rows_from_results

    row = {
        "trial": {
            "id": "t1",
            "scenario_id": "s1",
            "topology_version": "v1",
            "router_config_id": "fake",
        },
        "execution_status": "completed",
        "route": {"route_id": "r1", "canonical": "read:[db]", "segments": [{"layer": "read", "tools": ["db"]}]},
        "evaluation": {"success": True, "quality_score": 0.9},
        "latency_ms": 5.0,
        "token_usage": {"input_tokens": 0, "output_tokens": 0},
        "access_counts": {"sandbox_orders[read]": 2},
    }
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "r",
                "suite_name": "s",
                "suite_version": "1",
                "topology_version": "v1",
                "router_config_id": "fake",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "traces.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    disk_rows, _ = rows_from_run(run_dir)
    assert disk_rows[0].access_counts == {"sandbox_orders[read]": 2}
