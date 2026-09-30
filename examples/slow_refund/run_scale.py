"""Battlefield-hardening batch E: scale slow-regression run + evidence linkage.

Runs the sandbox refund domain at scale (default 50 scenarios x 5 trials),
persists the full artifact set, then feeds the real evidence into the Phase 4
machinery — ProtectionRegistry -> EvidenceAggregator -> CandidateDetector —
and writes an ``optimization_summary.json``. Observation only: nothing is
pruned, candidates are investigation inputs, never decisions.

Usage::

    python run_scale.py [--scenarios 50] [--trials 5] [--out-dir artifacts]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "src"))

from capability_runtime import (  # noqa: E402
    CandidateRoute,
    EvidenceAggregator,
    PruningConfig,
    CandidateDetector,
    ProtectionRegistry,
    RouteLayer,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    SlowRegressionWriter,
    build_observation_stats,
    build_slow_regression_report,
    drift_findings,
)
from fixtures import SandboxFixtureManager  # noqa: E402
from refund import build_evaluator, build_topology  # noqa: E402


@dataclass(frozen=True)
class Template:
    name: str
    category: str
    variant: str
    seed_layers: tuple[tuple[str, tuple[str, ...]], ...]


_TEMPLATES = (
    Template("basic", "refund", "eligible",
             (("read", ("order_db",)), ("analyze", ("policy_check",)), ("action", ("refund_api",)))),
    Template("verify", "refund", "eligible",
             (("read", ("order_db", "erp")), ("analyze", ("policy_check",)), ("action", ("refund_api",)))),
    Template("policy", "refund", "eligible",
             (("read", ("order_db", "rag")), ("analyze", ("policy_check",)), ("action", ("refund_api",)))),
    Template("risk", "refund", "high_risk",
             (("read", ("order_db",)), ("analyze", ("risk_check", "policy_check")), ("action", ("refund_api",)))),
    Template("summary", "order", "ineligible",
             (("read", ("order_db",)), ("analyze", ("summarizer", "policy_check")), ("action", ("refund_api",)))),
    Template("missing", "refund", "not_found",
             (("read", ("order_db",)), ("analyze", ("policy_check",)), ("action", ("refund_api",)))),
    Template("erpdown", "refund", "erp_down",
             (("read", ("order_db", "erp")), ("analyze", ("policy_check",)), ("action", ("refund_api",)))),
    Template("notify", "refund", "eligible",
             (("read", ("order_db",)), ("analyze", ("policy_check",)), ("action", ("refund_api", "send_email")))),
    Template("ticket", "order", "eligible",
             (("read", ("order_db",)), ("analyze", ("policy_check",)), ("action", ("create_ticket",)))),
    Template("multi", "refund", "eligible",
             (("read", ("order_db", "rag")), ("analyze", ("policy_check", "risk_check")), ("action", ("refund_api",)))),
)


def build_scale_suite(count: int) -> tuple[ScenarioSuite, dict[str, CandidateRoute]]:
    """Deterministic scenario/seed pairs cycling the ten templates."""
    scenarios: list[Scenario] = []
    seeds: dict[str, CandidateRoute] = {}
    for index in range(count):
        template = _TEMPLATES[index % len(_TEMPLATES)]
        scenario_id = f"{template.name}_{index:03d}"
        metadata = {"fixture": template.variant}
        if index % 25 == 0:
            metadata["sentinel"] = True
            metadata["priority"] = "critical"
        scenarios.append(
            Scenario(
                id=scenario_id,
                query=f"{template.name} request #{index}",
                category=template.category,
                metadata=metadata,
            )
        )
        # alternate between the template seed and a sibling-extended variant
        # so the same scenario explores more than one route across trials
        layers = [
            RouteLayer(layer, tools)
            for layer, tools in template.seed_layers
        ]
        if index // len(_TEMPLATES) % 2 == 1 and len(layers[0].tools) == 1:
            extended = tuple(sorted(set(layers[0].tools) | {"rag"}))
            layers[0] = RouteLayer(layers[0].layer, extended)
        seeds[scenario_id] = CandidateRoute(
            layers=tuple(layers), capabilities=frozenset()
        )
    suite = ScenarioSuite(
        name="scale_refund",
        version="1.0",
        description="battlefield-hardening batch E scale suite",
        scenarios=tuple(scenarios),
    )
    return suite, seeds


def optimize_linkage(topology, suite, outcome, obs) -> dict:
    """Feed real evidence into the Phase 4 machinery; report, never prune."""
    edges = [(edge.source, edge.target) for edge in topology.edges()]
    sentinels = [
        scenario for scenario in suite.scenarios
        if scenario.metadata.get("sentinel") is True
    ]
    protection = ProtectionRegistry(topology, sentinel_scenarios=sentinels)
    evidence = EvidenceAggregator().build(
        report=obs,
        results=outcome.results,
        edges=edges,
        protected_edges=protection.protected_edges(),
    )
    detector = CandidateDetector(
        config=PruningConfig(min_edge_opportunities=5, min_node_availability=5),
        protected_nodes=protection.protected_nodes(),
    )
    candidates = detector.detect(evidence)
    by_status: dict[str, int] = {}
    for candidate in candidates:
        by_status[candidate.status.value] = by_status.get(candidate.status.value, 0) + 1
    return {
        "evidence": {
            "nodes": len(evidence.node_evidence),
            "edges": len(evidence.edge_evidence),
            "protected_nodes": sorted(protection.protected_nodes()),
            "protected_edges": [
                f"{source}->{target}"
                for source, target in sorted(protection.protected_edges())
            ],
        },
        "candidates_by_status": dict(sorted(by_status.items())),
        "candidates": [
            {
                "kind": candidate.kind,
                "subject": candidate.subject,
                "status": candidate.status.value,
                "reason": candidate.reason.value if candidate.reason else None,
            }
            for candidate in candidates
        ],
    }


def metering_linkage(topology, results) -> dict:
    """Aggregate resource access + declared-vs-measured drift (facts only)."""
    access: dict[str, int] = {}
    measured: dict[str, tuple[float, int]] = {}
    for result in results:
        for key, count in (result.access_counts or {}).items():
            access[key] = access.get(key, 0) + count
        for layer in result.trace.layers:
            for execution in layer.tool_executions:
                if execution.measured_cost is not None:
                    total, n = measured.get(execution.tool_name, (0.0, 0))
                    measured[execution.tool_name] = (total + execution.measured_cost, n + 1)
    measured_mean = {tool: (total / n, n) for tool, (total, n) in measured.items()}
    declared = {
        name: topology.node(name).spec.cost_per_call
        for name in topology.nodes()
        if topology.node(name).spec.cost_per_call is not None
    }
    findings = drift_findings(measured_mean, declared)
    return {
        "access_counts": dict(sorted(access.items())),
        "drift": [finding.summary for finding in findings],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scale slow-regression run")
    parser.add_argument("--scenarios", type=int, default=50)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)

    topology, version = build_topology(topology_version="v0.4.0")
    suite, seeds = build_scale_suite(args.scenarios)

    started = time.perf_counter()
    outcome = asyncio.run(
        SlowRegressionRunner(
            topology=topology,
            evaluator=build_evaluator(),
            fixture_manager=SandboxFixtureManager(),
            seeds=seeds,
            trials_per_scenario=args.trials,
            topology_version=version,
            router_config_id="basefast-scale",
        ).run(suite)
    )
    elapsed = time.perf_counter() - started

    edges = [(edge.source, edge.target) for edge in topology.edges()]
    obs = build_observation_stats(outcome.results, edges=edges)
    report = build_slow_regression_report(
        outcome,
        obs,
        suite=suite,
        topology=topology,
        topology_version=version,
        router_config_id="basefast-scale",
    )

    summary = optimize_linkage(topology, suite, outcome, obs)
    summary["metering"] = metering_linkage(topology, outcome.results)
    summary["run"] = {
        "scenarios": args.scenarios,
        "trials_per_scenario": args.trials,
        "total_trials": len(outcome.results),
        "unique_routes": obs.unique_route_count,
        "business_success": report.business_success,
        "business_failure": report.business_failure,
        "elapsed_seconds": round(elapsed, 2),
    }

    out_dir = Path(args.out_dir) if args.out_dir else _HERE / "artifacts" / "scale"
    run_dir = out_dir / f"scale_{time.strftime('%Y%m%d%H%M%S')}"
    writer = SlowRegressionWriter(run_dir)
    written = writer.write(
        run_id=f"scale-{run_dir.name}",
        suite_name=suite.name,
        suite_version=suite.version,
        topology_version=version,
        router_config_id="basefast-scale",
        evaluator="composite:refund+completeness",
        outcome=outcome,
        report=report,
        obs=obs,
    )
    summary_path = run_dir / "optimization_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    sizes = {path.name: path.stat().st_size for path in sorted(run_dir.iterdir())}
    print(f"Scale run: {args.scenarios} scenarios x {args.trials} trials")
    print(f"Trials: {len(outcome.results)}  unique routes: {obs.unique_route_count}")
    print(
        f"Business: {report.business_success} success / "
        f"{report.business_failure} failed"
    )
    print(f"Elapsed: {elapsed:.1f}s")
    print(f"Artifacts in {run_dir}:")
    for name, size in sizes.items():
        print(f"  {name:<26}{size:>10} bytes")
    print("Optimization linkage (observation only):")
    print(f"  candidates by status: {summary['candidates_by_status']}")
    access = summary["metering"]["access_counts"]
    if access:
        print("Resource access (metered handles):")
        for key, count in access.items():
            print(f"  {key:<28}{count}")
    for line in summary["metering"]["drift"]:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
