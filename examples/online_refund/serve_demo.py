"""Phase 6 online-serving demo: the closed loop in one process (phase6 §16).

Pipeline: a small in-process scale run -> ranking -> catalog (version gate)
-> serve mixed requests with tier preferences, balancing and bounded
fallback (including injected read-layer failures) -> telemetry -> evidence
feed-back into the Phase 4 aggregator.

Usage::

    python serve_demo.py [--scenarios 12] [--trials 3] [--out-dir artifacts]

Fully offline and deterministic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "src"))
sys.path.insert(0, str(_HERE.parent / "slow_refund"))

from capability_runtime import (  # noqa: E402
    EvidenceAggregator,
    OnlineConfig,
    OnlineRequest,
    OnlineRuntime,
    OnlineTelemetry,
    RankConfig,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    build_observation_stats,
    build_ranking_report,
    build_catalog,
    online_results_to_trials,
    ranking_to_json,
    rows_from_results,
)
import store  # noqa: E402
from fixtures import SandboxFixtureManager  # noqa: E402
from refund import build_evaluator, build_topology  # noqa: E402
from run_scale import build_scale_suite  # noqa: E402

_TOPOLOGY_VERSION = "v0.4.0"


async def _learn_catalog(min_trials: int):
    """Offline half: run the sandbox, rank it, gate the catalog."""
    topology, version = build_topology(topology_version=_TOPOLOGY_VERSION)
    suite, seeds = build_scale_suite(12)
    outcome = await SlowRegressionRunner(
        topology=topology,
        evaluator=build_evaluator(),
        fixture_manager=SandboxFixtureManager(),
        seeds=seeds,
        trials_per_scenario=3,
        topology_version=version,
        router_config_id="basefast-demo",
    ).run(suite)
    report = build_ranking_report(
        rows_from_results(outcome.results),
        category_of={s.id: s.category for s in suite.scenarios if s.category},
        rank_config=RankConfig(min_trials=min_trials),
    )
    catalog = build_catalog(
        topology, ranking_to_json(report), topology_version=_TOPOLOGY_VERSION
    )
    return topology, report, catalog


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Online routing demo")
    parser.add_argument("--min-trials", type=int, default=2)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)

    started = time.perf_counter()
    topology, report, catalog = await _learn_catalog(args.min_trials)
    print(
        f"Offline phase: {len(report.profiles)} routes ranked, "
        f"{len(catalog.entries)} in catalog "
        f"(version gate: {catalog.topology_version})"
    )

    runtime = OnlineRuntime(
        catalog=catalog,
        config=OnlineConfig(max_fallbacks=2),
    )
    telemetry = OnlineTelemetry()

    plan = [
        ("refund the order please", "refund", "fast"),
        ("same request again - balancer rotates", "refund", "fast"),
        ("high quality handling requested", "refund", "quality"),
        ("default preference", "refund", None),
        ("order summary request", "order", None),
        ("order summary, again", "order", None),
    ]
    results = []
    for query, category, tier in plan:
        store.STORE.reset("eligible")
        result = await runtime.serve(
            OnlineRequest(query=query, category=category, tier=tier)
        )
        results.append(result)
        telemetry.append(result)
        depth = f" fallback_depth={result.fallback_depth}" if result.fallback_chain else ""
        print(
            f"  [{category}/{tier or 'default'}] -> {result.status.value:<13}"
            f" {result.selected_route_id}{depth} "
            f"({result.latency_ms:.0f}ms)"
        )

    # injected failure: the order vanishes -> every read-layer attempt fails,
    # the bounded fallback chain exhausts, replayable on the result
    store.STORE.reset("not_found")
    failed = await runtime.serve(
        OnlineRequest(query="refund a missing order", category="refund", tier="fast")
    )
    results.append(failed)
    telemetry.append(failed)
    print(f"  [not_found injected] -> {failed.status.value} "
          f"fallback_chain={len(failed.fallback_chain)}")
    for step in failed.fallback_chain:
        print(f"      {step.from_route_id} -> {step.to_route_id} ({step.cause})")

    # injected partial failure: ERP down degrades read-only-ERP routes, but
    # parallel siblings keep the layer alive (phase3 §41 semantics online)
    store.STORE.reset("erp_down")
    degraded = await runtime.serve(
        OnlineRequest(query="verify then refund", category="refund", tier=None)
    )
    results.append(degraded)
    telemetry.append(degraded)
    print(f"  [erp_down injected] -> {degraded.status.value} "
          f"({degraded.selection_reason.split(',')[0]})")

    store.STORE.reset("eligible")

    # ---- telemetry: facts only ------------------------------------------------
    print("\nOnline usage stats (telemetry):")
    for stat in telemetry.usage_stats():
        print(
            f"  {stat.route_id:<18} usage={stat.usage_count:<3} "
            f"served={stat.served_count:<3} failed={stat.failed_count:<3} "
            f"latency_median={stat.latency_median:.0f}ms"
        )
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = telemetry.write_jsonl(out / "online_telemetry.jsonl")
        print(f"\nTelemetry written to {path}")

    # ---- loop closure: online evidence feeds the Phase 4 aggregator -----------
    trials = online_results_to_trials(results)
    obs = build_observation_stats(
        trials,
        edges=[(edge.source, edge.target) for edge in topology.edges()],
    )
    evidence = EvidenceAggregator().build(
        report=obs,
        results=trials,
        edges=[(edge.source, edge.target) for edge in topology.edges()],
    )
    elapsed = time.perf_counter() - started
    print(
        f"\nLoop closure: {len(trials)} online observations -> "
        f"evidence over {len(evidence.node_evidence)} nodes / "
        f"{len(evidence.edge_evidence)} edges (offline round input)"
    )
    print(f"Demo elapsed: {elapsed:.1f}s")
    print("(Online observes and records; it never modifies topology or ranking.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
