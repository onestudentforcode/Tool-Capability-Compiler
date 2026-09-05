"""Shared fixtures for the Slow Regression (phase3) unit tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from capability_runtime import (
    EvaluationResult,
    Evaluator,
    LayerRegistry,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    ToolRegistry,
    Topology,
    TopologyBuilder,
    tool,
)


# ---- domain facts (real type annotations are required by ToolExecutor) ----


@dataclass(frozen=True)
class Order:
    order_id: str


@dataclass(frozen=True)
class PolicyDecision:
    decision: str


@dataclass(frozen=True)
class Digest:
    text: str


@tool(layer="read", produces=[Order])
async def db() -> Order:
    return Order(order_id="123")


@tool(layer="analyze", consumes=[Order], produces=[PolicyDecision])
async def policy_check(order: Order) -> PolicyDecision:
    return PolicyDecision(decision="approve")


@tool(layer="analyze", consumes=[Order], produces=[Digest])
async def summarizer(order: Order) -> Digest:
    return Digest(text="digest:" + order.order_id)


class AlwaysPassEvaluator(Evaluator):
    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        return EvaluationResult(success=True, quality_score=1.0, reason="ok")


class AlwaysFailEvaluator(Evaluator):
    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        return EvaluationResult(
            success=False, quality_score=0.0, reason="business failed"
        )


def build_topology() -> Topology:
    layers = LayerRegistry()
    layers.register("read", 0)
    layers.register("analyze", 1)
    tools = ToolRegistry()
    for node in (db, policy_check, summarizer):
        tools.register(node)
    return TopologyBuilder(layers, tools).build()


def make_suite(*ids: str) -> ScenarioSuite:
    ids = ids or ("s1",)
    return ScenarioSuite(
        name="demo",
        version="1.0",
        description="demo suite",
        scenarios=tuple(Scenario(id=sid, query="do the thing") for sid in ids),
    )


def sync_run(
    topology: Topology,
    suite: ScenarioSuite,
    *,
    seeds=None,
    trials: int = 3,
    evaluator=None,
    router=None,
    max_tools: int = 2,
    router_config_id: str = "basefast",
):
    return asyncio.run(
        SlowRegressionRunner(
            topology=topology,
            evaluator=evaluator or AlwaysPassEvaluator(),
            seeds=seeds,
            trials_per_scenario=trials,
            max_tools_per_layer=max_tools,
            topology_version="1.0",
            router_config_id=router_config_id,
            router=router,
        ).run(suite)
    )