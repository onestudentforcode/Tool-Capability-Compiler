import asyncio
import sys
from pathlib import Path

import pytest

from capability_runtime import (
    CandidateRoute,
    ProbeError,
    ProbeRunner,
    ProbeVerdict,
    build_directed_seed,
)
from capability_runtime.scenario import ScenarioLoader

_DEMO = Path(__file__).resolve().parent.parent.parent / "examples" / "slow_refund"


@pytest.fixture(scope="module")
def _demo():
    sys.path.insert(0, str(_DEMO))
    from refund import RefundEvaluator, build_topology

    topology, version = build_topology(topology_version="probe-test-v1")
    suite = ScenarioLoader().load_file(str(_DEMO / "scenarios.json"))
    return topology, suite, RefundEvaluator()


def _mk_layers(topology, read, analyze, action):
    from capability_runtime.route import RouteLayer

    return (
        RouteLayer("read", tuple(read)),
        RouteLayer("analyze", tuple(analyze)),
        RouteLayer("action", tuple(action)),
    )


def _all_suite_seeds(topology, suite, read, analyze, action):
    return {
        scenario.id: CandidateRoute(
            layers=_mk_layers(topology, read, analyze, action),
            capabilities=frozenset(),
        )
        for scenario in suite.scenarios
    }


def test_build_directed_seed_forces_edge(_demo) -> None:
    topology, _, _ = _demo
    base = CandidateRoute(
        layers=_mk_layers(topology, ["order_db"], ["policy_check"], ["refund_api"]),
        capabilities=frozenset(),
    )
    seed = build_directed_seed(base, topology, "order_db", "risk_check")
    by_layer = {layer.layer: set(layer.tools) for layer in seed.layers}
    assert by_layer["read"] == {"order_db"}
    assert by_layer["analyze"] == {"policy_check", "risk_check"}
    assert by_layer["action"] == {"refund_api"}
    assert seed.layer_count == 3


def test_build_directed_seed_rejects_non_edge(_demo) -> None:
    topology, _, _ = _demo
    base = CandidateRoute(
        layers=_mk_layers(topology, ["order_db"], ["policy_check"], ["refund_api"]),
        capabilities=frozenset(),
    )
    with pytest.raises(ProbeError):
        build_directed_seed(base, topology, "order_db", "refund_api")


def test_probe_observed_when_via_edge_executes(_demo) -> None:
    topology, suite, evaluator = _demo
    seeds = _all_suite_seeds(topology, suite, ["order_db"], ["policy_check"], ["refund_api"])
    result = asyncio.run(
        ProbeRunner(topology=topology, evaluator=evaluator, trials_per_scenario=2).run(
            suite, "order_db", "policy_check", seeds=seeds
        )
    )
    assert result.edge == ("order_db", "policy_check")
    assert result.verdict == ProbeVerdict.OBSERVED
    assert result.observed
    assert result.observed_count > 0
    assert result.total_trials > 0


def test_probe_missing_seed_rejected(_demo) -> None:
    topology, suite, evaluator = _demo
    with pytest.raises(ProbeError):
        asyncio.run(
            ProbeRunner(topology=topology, evaluator=evaluator).run(
                suite, "order_db", "policy_check", seeds={}
            )
        )


def test_probe_requires_all_scenario_seeds(_demo) -> None:
    topology, suite, evaluator = _demo
    partial = _all_suite_seeds(topology, suite, ["order_db"], ["policy_check"], ["refund_api"])
    partial = {key: value for key, value in partial.items() if key != suite.scenarios[0].id}
    with pytest.raises(ProbeError):
        asyncio.run(
            ProbeRunner(topology=topology, evaluator=evaluator).run(
                suite, "order_db", "policy_check", seeds=partial
            )
        )


def test_directed_seed_preserves_full_span(_demo) -> None:
    topology, _, _ = _demo
    base = CandidateRoute(
        layers=_mk_layers(topology, ["order_db"], ["policy_check"], ["refund_api"]),
        capabilities=frozenset(),
    )
    seed = build_directed_seed(base, topology, "order_db", "policy_check")
    # Probe must keep earlier/later layers reachable, not collapse to two layers.
    assert seed.layer_count == 3
    assert {layer.layer for layer in seed.layers} == {"read", "analyze", "action"}
    read = {
        tool for layer in seed.layers if layer.layer == "read" for tool in layer.tools
    }
    analyze = {
        tool for layer in seed.layers if layer.layer == "analyze" for tool in layer.tools
    }
    assert read & {"order_db"}
    assert analyze & {"policy_check"}