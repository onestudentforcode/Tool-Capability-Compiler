"""SlowRegressionRunner <-> LayerRouter integration (phase3 §120/§126)."""

from __future__ import annotations

import pytest

from capability_runtime import (
    CandidateRoute,
    FakeRouter,
    RouteLayer,
    RoutingAction,
    RoutingDecision,
    TrialExecutionStatus,
)

from _slow_helpers import build_topology, make_suite, sync_run


def test_router_selection_drives_layers() -> None:
    topology = build_topology()
    suite = make_suite()
    router = FakeRouter(
        layer_selections={
            "read": ("db",),
            "analyze": ("policy_check",),
        }
    )
    outcome = sync_run(
        topology, suite, trials=1, router=router, router_config_id="fake"
    )
    result = outcome.results[0]
    assert result.execution_status is TrialExecutionStatus.COMPLETED
    assert result.trial.router_config_id == "fake"
    layers = {layer.layer: layer for layer in result.trace.layers}
    assert layers["read"].selected_tools == ("db",)
    assert layers["analyze"].selected_tools == ("policy_check",)


def test_router_finish_stops_execution_cleanly() -> None:
    topology = build_topology()
    suite = make_suite()
    router = FakeRouter(layer_selections={"read": ("db",), "analyze": ()})
    outcome = sync_run(topology, suite, trials=1, router=router)
    result = outcome.results[0]
    assert result.execution_status is TrialExecutionStatus.COMPLETED
    # FINISH at analyze records an empty layer then stops before the action layer.
    assert [layer.layer for layer in result.trace.layers] == ["read", "analyze"]
    assert result.trace.layers[-1].selected_tools == ()


def test_invalid_router_selection_becomes_routing_error() -> None:
    topology = build_topology()
    suite = make_suite()

    class BadRouter:
        async def route(self, context):
            return RoutingDecision(
                action=RoutingAction.EXECUTE, selected_tools=("ghost_tool",)
            )

    outcome = sync_run(topology, suite, trials=1, router=BadRouter())
    result = outcome.results[0]
    assert result.execution_status is TrialExecutionStatus.ROUTING_ERROR
    assert result.route is None
    assert result.evaluation is None


def test_router_ignores_seed_when_provided() -> None:
    topology = build_topology()
    suite = make_suite()
    seed = CandidateRoute(
        layers=(
            RouteLayer("read", ("db",)),
            RouteLayer("analyze", ("summarizer",)),
        ),
        capabilities=frozenset(),
    )
    router = FakeRouter(layer_selections={"analyze": ("policy_check",)})
    outcome = sync_run(
        topology,
        suite,
        seeds={"s1": seed},
        trials=2,
        router=router,
    )
    for result in outcome.results:
        layers = {layer.layer: layer for layer in result.trace.layers}
        # router wins over the seed's expansion plan
        assert layers["analyze"].selected_tools == ("policy_check",)
    assert outcome.seed_missing_scenarios == ()