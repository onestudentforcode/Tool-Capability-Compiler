"""Office battlefield batch E: the first real pruning event + convergence.

Drives the full main loop over the office domain, fully offline (deterministic
fake LLM), in one reproducible run:

    Round 1      scale slow run on the declared topology (60 x trials)
    analyze      evidence -> candidates -> NON-EMPTY patch (first real pruning
                 candidates; the refund sandbox only ever produced empty ones)
    validate     fast + slow + diversity gates on the validation/sentinel split
    commit       the only write: topology version + active payload + record
    rollback     record replay back to the committed version (reversibility)
    rank         route profiles -> Pareto -> FAST/BALANCED/QUALITY tiers, with
                 redundant variants expected to separate across tiers (§5)
    select       online dry run over the committed topology catalog
    Round 2      online serving of the suite (tier-priority selection) with
                 telemetry looped back through online_results_to_trials;
                 convergence = mean cost or latency down, success rate not down

Usage::

    python run_closed_loop.py [--trials 15] [--validate-trials 3]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from capability_runtime import (  # noqa: E402
    OnlineConfig,
    OnlineRequest,
    OnlineRuntime,
    RankConfig,
    RoundRobinBalancer,
    ScenarioLoader,
    SlowRegressionRunner,
    SlowRegressionWriter,
    TopologyLoader,
    apply_patch,
    build_catalog,
    build_observation_stats,
    build_ranking_report,
    build_slow_regression_report,
    candidate_groups,
    online_results_to_trials,
    parse_canonical,
    rows_from_run,
)
from capability_runtime.optimization.pipeline import (  # noqa: E402
    ValidateConfig,
    analyze,
    commit,
    patch_from_payload,
    rollback,
    validate,
)
from capability_runtime.ranking.report import render, to_json  # noqa: E402

from examples.office import office, office_llm, run_scale  # noqa: E402
from examples.office.fixtures import OfficeFixtureManager  # noqa: E402
from examples.office.store import STORE  # noqa: E402


class _Tee:
    """Mirror stdout/stderr into the run's console.txt (逐行刷盘, failure-safe)."""

    def __init__(self, stream, file) -> None:
        self._stream = stream
        self._file = file

    def write(self, text: str) -> int:
        self._stream.write(text)
        self._file.write(text)
        self._file.flush()
        return len(text)

    def flush(self) -> None:
        self._stream.flush()
        self._file.flush()


def check(condition: bool, message: str) -> None:
    """Milestone assertions: a closed-loop run that cannot prove itself fails."""
    if not condition:
        raise SystemExit(f"CLOSED-LOOP CHECK FAILED: {message}")


async def run_round(topology, suite, seeds, trials: int, topology_version: str,
                    out_dir: Path, tag: str):
    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=office.build_evaluator(),
        fixture_manager=OfficeFixtureManager(),
        seeds=seeds,
        trials_per_scenario=trials,
        topology_version=topology_version,
        router_config_id="basefast-office",
        per_tool_timeout_seconds=0.15,
    )
    outcome = await runner.run(suite)
    edges = [(edge.source, edge.target) for edge in topology.edges()]
    obs = build_observation_stats(outcome.results, edges=edges)
    report = build_slow_regression_report(
        outcome, obs, suite=suite, topology=topology,
        topology_version=topology_version, router_config_id="basefast-office",
    )
    writer = SlowRegressionWriter(out_dir / f"round_{tag}")
    writer.write(
        run_id=f"office-{tag}",
        suite_name=suite.name, suite_version=suite.version,
        topology_version=topology_version, router_config_id="basefast-office",
        evaluator="composite:office_family+review_quality",
        outcome=outcome, report=report, obs=obs,
    )
    return outcome, obs, report


def _metrics_line(metrics: dict) -> str:
    """One aligned line for a trial-metrics dict (no python literals)."""
    parts = [f"trials {metrics['trials']}", f"success {metrics['success_rate']}"]
    if metrics.get("mean_cost") is not None:
        parts.append(f"cost {metrics['mean_cost']}")
    if metrics.get("mean_latency_ms") is not None:
        parts.append(f"latency {metrics['mean_latency_ms']}ms")
    return " | ".join(parts)


def trial_metrics(results) -> dict:
    evaluated = [r for r in results if r.evaluation is not None]
    if evaluated:
        success_rate = statistics.fmean(
            1.0 if r.evaluation.success else 0.0 for r in evaluated
        )
    else:
        # telemetry loop-back trials carry no evaluation (business judgement
        # stays offline): SERVED == the route executed end to end
        success_rate = (
            statistics.fmean(
                1.0 if r.execution_status.value == "completed" else 0.0
                for r in results
            )
            if results
            else 0.0
        )
    costs = [r.cost for r in results if r.cost is not None]
    latencies = [r.latency_ms for r in results if r.latency_ms]
    return {
        "trials": len(results),
        "success_rate": round(success_rate, 4),
        "mean_cost": round(statistics.fmean(costs), 5) if costs else None,
        "mean_latency_ms": round(statistics.fmean(latencies), 2) if latencies else None,
    }


def tier_evidence(report, topology) -> dict:
    """Non-empty tiers + redundant capabilities whose variants span tiers."""
    tiers_by_tool: dict[str, set[str]] = {}
    non_empty: Counter = Counter()
    profile_of = {profile.route_id: profile for profile in report.profiles}
    for assignment in report.tier_assignments:
        if not assignment.tiers:
            continue
        for tier in assignment.tiers:
            non_empty[tier.value] += 1
        profile = profile_of.get(assignment.route_id)
        if profile is None:
            continue
        for segment in parse_canonical(profile.canonical):
            for tool in segment.tools:
                tiers_by_tool.setdefault(tool, set()).update(
                    tier.value for tier in assignment.tiers
                )
    capability_tiers = {}
    for name in topology.nodes():
        for capability in topology.node(name).spec.capabilities:
            providers = [
                n for n in topology.nodes()
                if capability in topology.node(n).spec.capabilities
            ]
            if len(providers) < 2:
                continue
            spanned = set()
            for provider in providers:
                spanned |= tiers_by_tool.get(provider, set())
            capability_tiers[capability] = {
                "tiers": sorted(spanned),
                "tools": sorted(providers),
                "tool_tiers": {
                    provider: sorted(tiers_by_tool.get(provider, ()))
                    for provider in providers
                },
            }
    return {
        "non_empty_tiers": dict(sorted(non_empty.items())),
        "capability_tier_spans": capability_tiers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Office closed-loop evidence run")
    parser.add_argument("--trials", type=int, default=15)
    parser.add_argument("--validate-trials", type=int, default=3)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)

    suite = ScenarioLoader().load_file(str(run_scale.SUITE_PATH))
    topology, version = office.build_topology()
    original_payload = json.loads(
        (_ROOT / "examples" / "topology" / "office.json").read_text(encoding="utf-8")
    )
    office_llm.install_offline_fake()
    seeds = run_scale.build_seeds(suite, topology)

    out_dir = (
        Path(args.out_dir) if args.out_dir
        else _HERE / "artifacts" / "closed_loop" / time.strftime("loop_%Y%m%d%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    # Console archive: everything printed from here on (success or failure)
    # is mirrored into the run directory.
    console = open(out_dir / "console.txt", "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, console)
    sys.stderr = _Tee(sys.stderr, console)
    started = time.perf_counter()

    # ---- Round 1: offline exploration on the declared topology --------------
    outcome, obs, report = asyncio.run(
        run_round(topology, suite, seeds, args.trials, version, out_dir, "1_declared")
    )
    round1 = trial_metrics(outcome.results)
    print(f"Round 1 (declared): {_metrics_line(round1)}")

    # ---- analyze: first real pruning candidates -----------------------------
    candidates = asyncio.run(
        analyze(topology, suite, out_dir / "round_1_declared",
                min_edge_opportunities=50)
    )
    (out_dir / "candidates.json").write_text(
        json.dumps(candidates, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    patch = patch_from_payload(candidates)
    identified = [
        c for c in candidates["candidates"] if c["status"] == "identified"
    ]
    check(len(identified) >= 1, "no IDENTIFIED candidate on real evidence")
    check(
        patch.disabled_edges or patch.disabled_nodes,
        "analyze produced an empty patch",
    )
    print(
        f"analyze: {len(identified)} IDENTIFIED "
        f"({sum(1 for c in identified if c['kind'] == 'edge')} edges / "
        f"{sum(1 for c in identified if c['kind'] == 'node')} nodes)"
    )
    counterfactual = next(
        (c["counterfactual"] for c in identified if c["counterfactual"]), None
    )
    if counterfactual:
        print(f"  counterfactual: {counterfactual['verdict']}"
              + (f", regressed {len(counterfactual['regressed_scenarios'])}"
                 if counterfactual.get("regressed_scenarios") else ", no regression"))

    # ---- validate: fast + slow + diversity on the gate split ----------------
    verdict = asyncio.run(
        validate(topology, suite, patch,
                 config=ValidateConfig(trials=args.validate_trials))
    )
    (out_dir / "verdict.json").write_text(
        json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    check(verdict["verdict"] == "accept",
          f"validate rejected the patch: {verdict['failures']}")
    def _mark(gate: dict) -> str:
        if gate.get("skipped"):
            return "SKIP"
        return "PASS" if gate.get("passed") else "FAIL"

    print(f"validate: {verdict['verdict'].upper()} "
          f"(fast {_mark(verdict['fast'])} / slow {_mark(verdict['slow'])} / "
          f"diversity {_mark(verdict['diversity'])})")

    # ---- commit: the only write ---------------------------------------------
    versions_dir = out_dir / "versions"
    record = commit(
        topology, original_payload, patch,
        validation=verdict, version="office-v0.1",
        versions_dir=versions_dir, base_version="office-v0",
    )
    print(f"commit: office-v0.1 recorded "
          f"({len(record['composed']['disabled_edges'])} edges + "
          f"{len(record['composed']['disabled_nodes'])} nodes composed)")

    # ---- rollback: record replay proves reversibility -----------------------
    rolled = rollback(topology, original_payload,
                      versions_dir=versions_dir, to="office-v0.1")
    check(rolled["version"] == "office-v0.1-restored", "rollback replay failed")
    restored = TopologyLoader().load_file(
        str(versions_dir / "office-v0.1-restored.topology.json")
    )
    check(len(restored.nodes()) == len(topology.nodes()) - len(patch.disabled_nodes),
          "restored topology node count mismatch")
    print(f"rollback: {rolled['version']} replayed "
          f"({len(rolled['composed']['disabled_edges'])} edges + "
          f"{len(rolled['composed']['disabled_nodes'])} nodes)")

    # ---- Round 2: post-prune slow run on the committed topology -------------
    # Execution uses apply_patch on the declared topology: the loader-built
    # twin carries no consumes/produces schemas (JSON is type-free by design),
    # which would degrade the TopologyFilter's type-aware availability and
    # skew the comparison. The committed payload stays as the audit artifact.
    active_topology = apply_patch(topology, patch)
    committed_payload = TopologyLoader().load_file(
        str(versions_dir / "office-v0.1.topology.json")
    )
    check(len(committed_payload.nodes()) == len(active_topology.nodes()),
          "committed payload node count does not match the patched topology")
    seeds2 = run_scale.build_seeds(suite, active_topology)
    outcome2, obs2, report2 = asyncio.run(
        run_round(active_topology, suite, seeds2, 5, "office-v0.1",
                  out_dir, "2_committed")
    )
    round2_slow = trial_metrics(outcome2.results)
    print(f"Round 2a (committed, slow): {_metrics_line(round2_slow)}")

    # ---- rank: tiers over the committed topology's own evidence -------------
    # (the catalog binds ranking.topology_version to the served topology, so
    # the online half of Round 2 ranks the post-prune evidence)
    rows, meta = rows_from_run(out_dir / "round_2_committed")
    ranking = build_ranking_report(
        rows,
        category_of={s.id: (s.category or "") for s in suite.scenarios},
        rank_config=RankConfig(min_trials=3),
        suite_name=suite.name, suite_version=suite.version,
        source_run=str(out_dir / "round_2_committed"),
    )
    (out_dir / "ranking.json").write_text(
        json.dumps(to_json(ranking), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "ranking.txt").write_text(render(ranking), encoding="utf-8")
    evidence = tier_evidence(ranking, active_topology)
    check(len(evidence["non_empty_tiers"]) >= 2,
          f"fewer than 2 non-empty tiers: {evidence['non_empty_tiers']}")
    separating = {
        capability: span
        for capability, span in evidence["capability_tier_spans"].items()
        if len(span["tiers"]) >= 2
    }
    check(len(separating) >= 1,
          "no redundant capability has variants in different tiers")
    tiers_line = " / ".join(
        f"{name} {count}" for name, count in evidence["non_empty_tiers"].items()
    )
    print(f"rank: tiers {tiers_line or '-'}")
    print(f"  separating capabilities: {', '.join(sorted(separating)) or '-'}")

    # ---- select: online dry run over the committed catalog -----------------
    catalog = build_catalog(
        active_topology, to_json(ranking), topology_version="office-v0.1"
    )
    selections = []
    for scenario in suite.scenarios[:12]:
        groups = candidate_groups(
            catalog,
            OnlineRequest(query=scenario.query, category=scenario.category,
                          request_id=scenario.id),
            OnlineConfig(),
        )
        selections.append({
            "request_id": scenario.id,
            "tiers": {tier: len(entries) for tier, entries in groups if entries},
        })
    (out_dir / "selection.json").write_text(
        json.dumps({"topology_version": "office-v0.1", "dry_runs": selections},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"select: {len(selections)} dry runs over the committed catalog")

    # ---- Round 2: online serving + telemetry loop-back ----------------------
    runtime = OnlineRuntime(
        catalog=catalog, balancer=RoundRobinBalancer(),
        per_tool_timeout_seconds=0.15,
    )

    async def serve_round2():
        telemetry_records = []
        for scenario in suite.scenarios:
            STORE.reset("clean", scenario_id=scenario.id, trial_index=0)
            result = await runtime.serve(
                OnlineRequest(query=scenario.query, category=scenario.category,
                              request_id=scenario.id)
            )
            telemetry_records.append(result)
        return telemetry_records

    online_results = asyncio.run(serve_round2())
    served = [r for r in online_results if r.status.value == "served"]
    loopback = online_results_to_trials(
        tuple(online_results), scenario_suite_version=suite.version,
        router_config_id="online-loopback",
    )
    round2 = trial_metrics(loopback)
    print(
        f"Round 2 (online, committed topology): {_metrics_line(round2)}"
        f" | served {len(served)}/{len(online_results)}"
    )
    status_line = " | ".join(
        f"{name} {count}"
        for name, count in sorted(
            Counter(r.status.value for r in online_results).items()
        )
    )
    print(f"  status: {status_line}")

    check(round2["success_rate"] >= round1["success_rate"],
          f"success rate dropped: {round1['success_rate']} -> {round2['success_rate']}")
    improved_cost = (
        round2["mean_cost"] is not None and round1["mean_cost"] is not None
        and round2["mean_cost"] <= round1["mean_cost"]
    )
    improved_latency = (
        round2["mean_latency_ms"] is not None
        and round1["mean_latency_ms"] is not None
        and round2["mean_latency_ms"] <= round1["mean_latency_ms"]
    )
    check(improved_cost or improved_latency,
          f"neither cost nor latency improved: {round1} -> {round2}")

    # ---- report --------------------------------------------------------------
    elapsed = round(time.perf_counter() - started, 1)
    report_payload = {
        "round1_declared": round1,
        "analyze": {
            "identified": len(identified),
            "edges": sorted(c["subject"] for c in identified if c["kind"] == "edge"),
            "nodes": sorted(c["subject"] for c in identified if c["kind"] == "node"),
            "counterfactual": next(
                (c["counterfactual"] for c in identified if c["counterfactual"]), None
            ),
        },
        "validate": {
            "verdict": verdict["verdict"],
            "fast": verdict["fast"], "slow": verdict["slow"],
            "diversity": verdict["diversity"],
        },
        "commit": {"version": "office-v0.1", "record": record["composed"]},
        "rollback": {"version": rolled["version"], "replayed": True},
        "round2a_committed_slow": round2_slow,
        "rank": {
            "min_trials": 3,
            "non_empty_tiers": evidence["non_empty_tiers"],
            "separating_capabilities": separating,
        },
        "round2_online": round2,
        "convergence": {
            "success_rate": f"{round1['success_rate']} -> {round2['success_rate']} (not down)",
            "mean_cost": f"{round1['mean_cost']} -> {round2['mean_cost']}",
            "mean_latency_ms": f"{round1['mean_latency_ms']} -> {round2['mean_latency_ms']}",
            "telemetry_loopback_trials": len(loopback),
        },
        "elapsed_seconds": elapsed,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    try:  # the readable mirror must never fail the run itself
        from examples.office import render_review

        render_review.write_review(out_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"review rendering skipped: {exc}")
    print(f"\nCLOSED LOOP COMPLETE in {elapsed}s — artifacts in {out_dir}")
    for name in ("console.txt", "candidates.json", "verdict.json", "ranking.json",
                 "selection.json", "report.json", "review.md", "versions"):
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
