"""Smoke test for the offline refund integration demo (phase3 §109-111).

Loads the demo domain, runs a compact Slow Regression, and asserts the demo
acceptance signals: multiple observed routes, per-edge opportunity/usage, per-
node availability/selection, per-trial business outcome, latency and persistence.
"""

from __future__ import annotations

import sys
from pathlib import Path

from capability_runtime import (
    DefaultFixtureManager,
    SlowRegressionRunner,
    build_observation_stats,
    build_slow_regression_report,
)
from capability_runtime.scenario import ScenarioLoader

_PROJECT = Path(__file__).resolve().parents[2]  # repo root
_DEMO = _PROJECT / "examples" / "slow_refund"


def _load_demo():
    sys.path.insert(0, str(_DEMO))
    import fixtures  # noqa: PLC0415
    import refund  # noqa: PLC0415
    import store  # noqa: PLC0415

    store.STORE.reset("eligible")
    return refund, store, fixtures


def test_demo_produces_multiple_routes_and_stats(tmp_path) -> None:
    refund, store, fixtures = _load_demo()
    topology, version = refund.build_topology(topology_version="v0.3.1")
    assert len(topology.nodes()) == 10
    assert len(topology.layers()) == 3

    from capability_runtime.cli import load_seed_routes

    suite = ScenarioLoader().load_file(str(_DEMO / "scenarios.json"))
    seeds = load_seed_routes(str(_DEMO / "seeds.json"))

    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=refund.build_evaluator(),
        fixture_manager=fixtures.SandboxFixtureManager(),
        seeds=seeds,
        trials_per_scenario=4,  # 5 scenarios x 4 = 20 trials
        topology_version=version,
        router_config_id="basefast",
    )
    import asyncio

    outcome = asyncio.run(runner.run(suite))
    assert len(outcome.results) == 20
    assert outcome.seed_missing_scenarios == ()

    edges = [(edge.source, edge.target) for edge in topology.edges()]
    obs = build_observation_stats(outcome.results, edges=edges)
    report = build_slow_regression_report(
        outcome,
        obs,
        suite=suite,
        topology=topology,
        topology_version=version,
        router_config_id="basefast",
    )

    # §111: multiple route_ids with usage, per-edge opportunity, per-node choices
    assert obs.unique_route_count > 1
    assert all(stat.opportunity_count > 0 for stat in obs.edge_stats.values())
    assert any(stat.observed_count > 0 for stat in obs.edge_stats.values())
    assert all(stat.opportunity_count >= stat.selected_count for stat in obs.node_stats.values())
    # each completed trial carries a business outcome and a latency
    for result in outcome.results:
        assert result.execution_status.name == "COMPLETED"
        assert result.evaluation is not None
        assert result.latency_ms >= 0
    # the action layer produced refund results -> a meaningful business mix
    assert report.business_success + report.business_failure == 20
    assert 0 < report.business_success < 20

    # per-scenario fixtures drive per-scenario conclusions (batch D): the
    # high-risk and ineligible variants can never end in a business success,
    # while the eligible scenarios do succeed.
    by_scenario: dict[str, list[bool]] = {}
    for result in outcome.results:
        evaluation = result.evaluation
        assert evaluation is not None
        by_scenario.setdefault(result.trial.scenario_id, []).append(
            evaluation.success
        )
    assert not any(by_scenario["refund_risk"])
    assert not any(by_scenario["refund_summary"])
    assert any(by_scenario["refund_basic"])

    # persistence hook matches the offline demo contract
    assert report.total_nodes == 10
    assert report.total_edges == len(edges)