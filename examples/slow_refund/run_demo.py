"""Run the Slow Regression integration demo (phase3 §109-111).

Usage::

    python run_demo.py [--trials 100] [--out-dir artifacts]

Runs the offline refund domain end-to-end: seeds each scenario from
``seeds.json`` (basefast), explores ExpansionPlan variants across trials,
evaluates business success via :class:`RefundEvaluator`, prints the report and
writes traces/stats JSON for offline analysis (§105).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "src"))

from capability_runtime import (  # noqa: E402
    DefaultFixtureManager,
    SlowRegressionRunner,
    SlowRegressionWriter,
    build_observation_stats,
    build_slow_regression_report,
    compute_expansion_deltas,
    render_slow_report,
)
from capability_runtime.scenario import ScenarioLoader  # noqa: E402
from refund import RefundEvaluator, build_topology  # noqa: E402

_TOPOLOGY_VERSION = "v0.3.1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Slow Regression refund demo")
    parser.add_argument("--trials", type=int, default=100, help="Total trials to run")
    parser.add_argument("--out-dir", default=None, help="Artifacts directory")
    args = parser.parse_args(argv)

    scenarios_per = max(1, args.trials // 5)

    topology, version = build_topology(topology_version=_TOPOLOGY_VERSION)
    suite = ScenarioLoader().load_file(str(_HERE / "scenarios.json"))
    seeds = _load_seeds(_HERE / "seeds.json")

    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=RefundEvaluator(),
        fixture_manager=DefaultFixtureManager(),
        seeds=seeds,
        trials_per_scenario=scenarios_per,
        topology_version=version,
        router_config_id="basefast",
    )
    outcome = _sync(runner, suite)
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

    print(render_slow_report(report))
    print(_render_routes_and_deltas(obs, outcome))

    if args.out_dir:
        out = Path(args.out_dir) / f"run_{datetime.now():%Y%m%d%H%M%S}"
        run_id = f"{suite.name}-{out.name}"
        writer = SlowRegressionWriter(out)
        written = writer.write(
            run_id=run_id,
            suite_name=suite.name,
            suite_version=suite.version,
            topology_version=version,
            router_config_id="basefast",
            evaluator="refund:successful-refund-result",
            outcome=outcome,
            report=report,
            obs=obs,
        )
        print(f"\nArtifacts written to {out}")
        for name in sorted(written):
            print(f"  {name}")
    return 0


def _sync(runner: SlowRegressionRunner, suite):
    import asyncio

    return asyncio.run(runner.run(suite))


def _load_seeds(path: Path):
    from capability_runtime.cli import load_seed_routes

    return load_seed_routes(str(path))


def _render_routes_and_deltas(obs, outcome) -> str:
    lines = ["", "Observed Routes (route_id  usage  success  latency)"] 
    for route_id in sorted(obs.route_stats):
        stat = obs.route_stats[route_id]
        mean, _, _ = stat.latency_basics
        latency = f"{mean:.0f}" if mean is not None else "-"
        lines.append(
            f"  {route_id}  {stat.usage_count:>5}  "
            f"{stat.business_success_count:>5}  {latency:>6}ms"
        )

    lines.append("")
    lines.append("Expansion deltas (baseline vs variant, evidence only):")
    deltas = compute_expansion_deltas(outcome.results)
    if not deltas:
        lines.append("  (none)")
    for delta in deltas:
        lines.append(
            f"  {delta.scenario_id} {delta.layer}: "
            f"{','.join(delta.baseline_tools)} -> {','.join(delta.variant_tools)} "
            f"latency {delta.latency_delta_ms:+.0f}ms"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())