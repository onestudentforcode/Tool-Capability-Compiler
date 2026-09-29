"""Fast and Slow Regression command-line interface.

Usage::

    tool-topology regression fast \\
        --topology topology.json \\
        --scenario scenarios/refund.json \\
        [--mode gold|discovery] [--topology-version v1] \\
        [--baseline baseline.json] [--save-baseline out.json] \\
        [--base-url URL] [--model MODEL] [--fail-on-regression]

    tool-topology regression slow \\
        --topology topology.json \\
        --scenario scenarios/refund.json \\
        [--trials 10] [--environment sandbox] \\
        [--max-concurrency 4] [--topology-version v1] \\
        [--basefast seeds.json] [--out-dir artifacts/slow_regression] \\
        [--router-config router.json] [--base-url URL --model MODEL] \\
        [--expected-fact refund.executed=true]

The ``--basefast`` file maps ``scenario_id`` to a CandidateRoute to seed each
trial's layer expansion::

    {"s1": {"layers": [{"layer": "read", "tools": ["OrderDB", "RAG"]},
                       {"layer": "analyze", "tools": ["RefundPolicyCheck"]}],
            "capabilities": ["order.read"]}}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .capability import OllamaCapabilityResolver
from .execution.context import ExecutionEnvironment
from .evaluation.structured import StructuredEvaluator
from .fixtures.manager import DefaultFixtureManager
from .regression.baseline import Baseline, BaselineStore, compute_diff
from .regression.candidate_route import CandidateRoute
from .regression.coverage import CoverageStatus
from .regression.report import CoverageReport, FastRegressionRunner
from .regression.slow.persistence import SlowRegressionWriter
from .regression.slow.report import (
    build_slow_regression_report,
    render_slow_report,
)
from .regression.slow.runner import SlowRegressionRunner
from .regression.slow.stats import build_observation_stats
from .route.models import RouteLayer
from .router.llm_router import LLMRouter
from .scenario import ScenarioLoader, ScenarioSuite
from .topology.loader import TopologyLoader, unbound_tool_names
from .optimization.report import OptimizationReport, build_report
from .core.errors import RouteCatalogError, RouteProfileError, RouteSelectionError
from .online import (
    OnlineConfig,
    OnlineRequest,
    RoundRobinBalancer,
    build_catalog,
    candidate_groups,
)
from .ranking import (
    RankConfig,
    build_ranking_report,
    render as render_ranking,
    rows_from_run,
    to_json as ranking_to_json,
)
from .topology.version import initial_version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tool-topology",
        description="Layered tool-routing and topology optimization framework",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    regression = subparsers.add_parser("regression", help="Run regressions")
    fast = regression.add_subparsers(dest="subcommand", required=True)

    fast_parser = fast.add_parser("fast", help="Run fast (metadata) regression")
    fast_parser.add_argument("--topology", required=True, help="Topology JSON file")
    fast_parser.add_argument("--scenario", required=True, help="Scenario suite JSON file")
    fast_parser.add_argument(
        "--mode",
        choices=("gold", "discovery"),
        default="gold",
        help="gold uses expected_capabilities (no LLM); discovery resolves queries via LLM",
    )
    fast_parser.add_argument("--topology-version", default="declared")
    fast_parser.add_argument("--baseline", help="Save or compare diff against a baseline")
    fast_parser.add_argument(
        "--save-baseline", help="Write the new coverage snapshot to this path"
    )
    fast_parser.add_argument("--base-url", help="Ollama base URL (discovery mode)")
    fast_parser.add_argument("--model", help="Ollama model name (discovery mode)")
    fast_parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit non-zero when any scenario regresses to not-covered",
    )

    slow_parser = fast.add_parser("slow", help="Run slow (real execution) regression")
    slow_parser.add_argument("--topology", required=True, help="Topology JSON file")
    slow_parser.add_argument("--scenario", required=True, help="Scenario suite JSON file")
    slow_parser.add_argument("--trials", type=int, default=5, help="Trials per scenario")
    slow_parser.add_argument(
        "--environment",
        choices=("mock", "sandbox", "staging"),
        default="sandbox",
        help="Execution environment for tool calls",
    )
    slow_parser.add_argument("--max-concurrency", type=int, default=1)
    slow_parser.add_argument("--topology-version", default="declared")
    slow_parser.add_argument(
        "--basefast",
        metavar="PATH",
        help="JSON mapping scenario_id -> CandidateRoute used to seed each trial",
    )
    slow_parser.add_argument(
        "--out-dir",
        help="Write traces.jsonl / manifest.json / report.json / *_stats.json here",
    )
    slow_parser.add_argument(
        "--router-config",
        help="JSON with {'base_url': ..., 'model': ...} to route layers via an LLM",
    )
    slow_parser.add_argument("--base-url", help="Ollama base URL (LLM routing)")
    slow_parser.add_argument("--model", help="Ollama model name (LLM routing)")
    slow_parser.add_argument(
        "--expected-fact",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="A fact the deterministic evaluator expects in the final state (repeatable)",
    )

    optimize = subparsers.add_parser(
        "optimize",
        help="Optimize topology by pruning underused edges/nodes",
    )
    optimize.add_argument("--topology", required=True, help="Topology JSON file")
    optimize.add_argument(
        "--scenario",
        required=True,
        help="Scenario suite JSON file (used for coverage validation)",
    )
    optimize.add_argument(
        "--start-version",
        default="v1",
        help="Starting version tag for the active topology (default: v1)",
    )
    optimize.add_argument(
        "--end-version",
        default="v2",
        help="Version tag for the optimized topology (default: v2)",
    )
    optimize.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    optimize.add_argument(
        "--out",
        help="Write the optimization report to this file instead of stdout",
    )
    rank = subparsers.add_parser(
        "rank",
        help="Rank observed routes from a slow-regression run (phase 5)",
    )
    rank.add_argument(
        "--slow-report",
        required=True,
        help="Slow regression artifact directory (manifest.json + traces.jsonl)",
    )
    rank.add_argument(
        "--scenario",
        help="Scenario suite JSON providing the category dimension (optional)",
    )
    rank.add_argument(
        "--min-trials",
        type=int,
        default=20,
        help="Minimum trials for a route to enter ranking (default: 20)",
    )
    rank.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text)",
    )
    rank.add_argument(
        "--out",
        help="Write the ranking report to this file instead of stdout",
    )
    select = subparsers.add_parser(
        "select",
        help="Dry-run online route selection from a ranking (phase 6)",
    )
    select.add_argument("--topology", required=True, help="Topology JSON file")
    select.add_argument(
        "--ranking", required=True, help="Route ranking JSON (phase 5 output)"
    )
    select.add_argument(
        "--topology-version",
        help="Active topology version gate; default takes the ranking's own",
    )
    select.add_argument("--category", help="Filter candidates by category")
    select.add_argument(
        "--tier", choices=("fast", "balanced", "quality"), help="Tier preference"
    )
    select.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "regression":
        if args.subcommand == "fast":
            return run_fast(args)
        if args.subcommand == "slow":
            return run_slow(args)
        build_parser().error(f"Unsupported subcommand: {args.subcommand}")
    if args.command == "optimize":
        return run_optimize(args)
    if args.command == "rank":
        return run_rank(args)
    if args.command == "select":
        return run_select(args)
    build_parser().error(f"Unsupported command: {args.command}")


def run_fast(args: argparse.Namespace) -> int:
    topology = TopologyLoader().load_file(args.topology)
    suite = ScenarioLoader().load_file(args.scenario)

    resolver = None
    if args.mode == "discovery":
        resolver = OllamaCapabilityResolver(
            base_url=args.base_url, model=args.model
        )

    runner = FastRegressionRunner(resolver=resolver)
    report = asyncio.run(
        runner.run(suite, topology, topology_version=args.topology_version)
    )

    baseline = None
    if args.baseline:
        baseline = BaselineStore().load(args.baseline)
    if args.save_baseline:
        BaselineStore().save(Baseline.from_report(report), args.save_baseline)

    print(render_report(report, suite, baseline))
    if args.fail_on_regression and _has_regression(report, baseline):
        return 1
    return 0


def load_seed_routes(path: str) -> dict[str, CandidateRoute]:
    """Load the ``--basefast`` seed file mapping scenario_id -> CandidateRoute."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    seeds: dict[str, CandidateRoute] = {}
    for scenario_id, route in dict(raw).items():
        layers = tuple(
            RouteLayer(str(segment["layer"]), tuple(segment["tools"]))
            for segment in route["layers"]
        )
        seeds[scenario_id] = CandidateRoute(
            layers=layers,
            capabilities=frozenset(route.get("capabilities", ())),
        )
    return seeds


def run_slow(args: argparse.Namespace) -> int:
    topology = TopologyLoader().load_file(args.topology)
    unbound = unbound_tool_names(topology)
    if unbound:
        # Executing the null placeholder would silently produce None outputs
        # and poison the run's statistics (battlefield-hardening batch B).
        print(
            "refusing to run slow regression: tools without an executable "
            f"implementation: {', '.join(unbound)}; declare "
            '"implementation": "module:attr" for them',
            file=sys.stderr,
        )
        return 2
    suite = ScenarioLoader().load_file(args.scenario)

    seeds = load_seed_routes(args.basefast) if args.basefast else None
    router = None
    router_config_id = "basefast" if seeds else "free"
    if args.router_config:
        config = json.loads(Path(args.router_config).read_text(encoding="utf-8"))
        router = LLMRouter(
            base_url=config.get("base_url", args.base_url),
            model=config.get("model", args.model),
        )
        router_config_id = config.get("config_id", "llm")
    elif args.base_url and args.model:
        router = LLMRouter(base_url=args.base_url, model=args.model)
        router_config_id = "llm"

    expected: dict[str, str] = {}
    for pair in args.expected_fact:
        name, _, value = pair.partition("=")
        expected[name] = value

    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=StructuredEvaluator(expected),
        fixture_manager=DefaultFixtureManager(),
        seeds=seeds,
        trials_per_scenario=args.trials,
        max_concurrency=args.max_concurrency,
        topology_version=args.topology_version,
        environment=ExecutionEnvironment(args.environment),
        router_config_id=router_config_id,
        router=router,
    )
    outcome = asyncio.run(runner.run(suite))
    obs = build_observation_stats(
        outcome.results,
        edges=[(edge.source, edge.target) for edge in topology.edges()],
    )
    report = build_slow_regression_report(
        outcome,
        obs,
        suite=suite,
        topology=topology,
        topology_version=args.topology_version,
        router_config_id=router_config_id,
    )

    if args.out_dir:
        writer = SlowRegressionWriter(args.out_dir)
        writer.write(
            run_id=f"{suite.name}-{datetime.now():%Y%m%d%H%M%S}",
            suite_name=suite.name,
            suite_version=suite.version,
            topology_version=report.topology_version,
            router_config_id=router_config_id,
            evaluator=f"structured:{sorted(expected)}",
            outcome=outcome,
            report=report,
            obs=obs,
        )

    print(render_slow_report(report))
    return 0


def run_optimize(args: argparse.Namespace) -> int:
    """Run a topology optimization pass.

    In Phase 4, the optimize CLI produces a deterministic report based on
    the declared topology (no real pruning is executed yet — the full
    pruning pipeline will be wired up in later phases).
    """
    topology = TopologyLoader().load_file(args.topology)
    suite = ScenarioLoader().load_file(args.scenario)

    start = initial_version(topology, version=args.start_version)
    end = initial_version(topology, version=args.end_version)
    report = build_report(start=start, end=end)

    if args.format == "json":
        import json as _json
        output = _json.dumps(report.to_json(), indent=2, ensure_ascii=False)
    else:
        output = report.summary_text()

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


def run_rank(args: argparse.Namespace) -> int:
    """Rank routes from persisted slow-regression evidence (phase 5).

    Reads only — ranking never executes tools and never modifies topology.
    """
    try:
        rows, meta = rows_from_run(args.slow_report)
    except RouteProfileError as exc:
        print(f"cannot rank run: {exc}", file=sys.stderr)
        return 2

    category_of = None
    if args.scenario:
        suite = ScenarioLoader().load_file(args.scenario)
        category_of = {
            scenario.id: scenario.category
            for scenario in suite.scenarios
            if scenario.category
        }

    report = build_ranking_report(
        rows,
        category_of=category_of,
        rank_config=RankConfig(min_trials=args.min_trials),
        suite_name=meta.suite_name or None,
        suite_version=meta.suite_version or None,
        source_run=meta.run_id or None,
    )
    output = (
        json.dumps(ranking_to_json(report), indent=2, ensure_ascii=False)
        if args.format == "json"
        else render_ranking(report)
    )
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


def run_select(args: argparse.Namespace) -> int:
    """Dry-run online selection: show candidates and the would-be pick.

    Reads only — no tool is executed (phase6 §16).
    """
    topology = TopologyLoader().load_file(args.topology)
    try:
        ranking = json.loads(Path(args.ranking).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read ranking: {exc}", file=sys.stderr)
        return 2
    version = args.topology_version or str(ranking.get("topology_version", ""))
    try:
        catalog = build_catalog(topology, ranking, topology_version=version)
        request = OnlineRequest(
            query="select", category=args.category, tier=args.tier
        )
        groups = candidate_groups(catalog, request, OnlineConfig())
        pick = RoundRobinBalancer().pick(groups[0][1])
    except (RouteCatalogError, RouteSelectionError) as exc:
        print(f"selection failed: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(
            json.dumps(
                {
                    "topology_version": catalog.topology_version,
                    "groups": [
                        {
                            "tier": tier,
                            "candidates": [entry.canonical for entry in entries],
                        }
                        for tier, entries in groups
                    ],
                    "would_select": {
                        "route_id": pick.route_id,
                        "canonical": pick.canonical,
                        "tiers": list(pick.tiers),
                    },
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    lines = [
        "Route Selection (dry run)",
        "",
        f"Topology: {catalog.topology_version}   "
        f"Ranked routes: {len(catalog.entries)}",
        "",
    ]
    for tier, entries in groups:
        lines.append(f"Tier {tier} ({len(entries)} candidates)")
        for entry in entries:
            lines.append(f"  {entry.canonical}")
        lines.append("")
    lines.append(f"Would select: {pick.canonical}")
    lines.append("(dry run; no tool is executed)")
    print("\n".join(lines))
    return 0


def _has_regression(report: CoverageReport, baseline: Baseline | None) -> bool:
    if baseline is not None:
        diff = compute_diff(baseline, report)
        if diff.newly_uncovered:
            return True
    return any(r.status != CoverageStatus.COVERED for r in report.results)


def render_report(
    report: CoverageReport, suite: ScenarioSuite, baseline: Baseline | None = None
) -> str:
    lines: list[str] = ["Fast Regression", "", f"Suite: {suite.name} {suite.version}"]
    lines.append(f"Topology: {report.topology_version}")
    lines.append("")
    lines.append(f"Total:      {report.total}")
    lines.append(f"Covered:    {report.covered}")
    lines.append(f"Uncertain:  {report.uncertain}")
    lines.append(f"Uncovered:  {report.uncovered}")
    lines.append("")
    lines.append(f"Coverage: {report.coverage_rate * 100:.2f}%")
    lines.append("")
    lines.append("Missing Capabilities:")
    if report.missing_capabilities:
        for index, entry in enumerate(
            sorted(
                report.missing_capabilities,
                key=lambda item: (-item.count, item.capability),
            ),
            start=1,
        ):
            lines.append(f"{index}. {entry.capability:<30} {entry.count}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("Topology Gaps:")
    if report.topology_gaps:
        for index, entry in enumerate(
            sorted(
                report.topology_gaps,
                key=lambda item: (-item.count, item.required_capabilities),
            ),
            start=1,
        ):
            caps = ", ".join(entry.required_capabilities)
            lines.append(f"{index}. {caps:<40} {entry.count}")
    else:
        lines.append("(none)")

    if baseline is not None:
        diff = compute_diff(baseline, report)
        lines.append("")
        lines.append(
            f"Regression vs baseline {baseline.topology_version} "
            f"(current {diff.current_topology_version}):"
        )
        lines.append(f"Newly covered:   {len(diff.newly_covered)}")
        lines.append(f"Newly uncovered: {len(diff.newly_uncovered)}")
        lines.append(f"Still uncovered: {len(diff.still_uncovered)}")
        lines.append(f"Status changed:  {len(diff.status_changed)}")
        if diff.newly_uncovered:
            lines.append("Regressed scenarios:")
            for scenario_id in diff.newly_uncovered:
                lines.append(f"  - {scenario_id}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())