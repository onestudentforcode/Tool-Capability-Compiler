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
    import refund  # noqa: PLC0415

    return refund


def test_demo_produces_multiple_routes_and_stats(tmp_path) -> None:
    refund = _load_demo()
    topology, version = refund.build_topology(topology_version="v0.3.1")
    assert len(topology.nodes()) == 10
    assert len(topology.layers()) == 3

    from capability_runtime.cli import load_seed_routes

    suite = ScenarioLoader().load_file(str(_DEMO / "scenarios.json"))
    seeds = load_seed_routes(str(_DEMO / "seeds.json"))

    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=refund.RefundEvaluator(),
        fixture_manager=DefaultFixtureManager(),
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

    # persistence hook matches the offline demo contract
    assert report.total_nodes == 10
    assert report.total_edges == len(edges)