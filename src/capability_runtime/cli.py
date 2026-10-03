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
import time
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
from .regression.report import (
    CoverageReport,
    FastRegressionRunner,
    fast_report_from_json,
    fast_report_to_json,
)
from .regression.seed_export import export_seeds, read_seeds
from .regression.seed_discovery import discover_seeds
from .regression.slow.persistence import SlowRegressionWriter
from .regression.slow.report import (
    build_slow_regression_report,
    render_slow_report,
)
from .regression.slow.runner import SlowRegressionRunner
from .regression.slow.stats import build_observation_stats
from .router.fake_router import ScenarioScriptedRouter
from .router.llm_router import LLMRouter
from .router.models import RouterConfig, ToolSummary
from .scenario import ScenarioLoader, ScenarioSuite
from .topology.loader import TopologyLoader, unbound_tool_names
from .optimization.report import OptimizationReport, build_report
from .core.errors import (
    ArtifactLoadError,
    CommitGateError,
    OnboardingError,
    OptimizePipelineError,
    ProposalError,
    RouteCatalogError,
    RouteProfileError,
    RouteSelectionError,
)
from .optimization.pipeline import (
    ValidateConfig,
    analyze as pipeline_analyze,
    commit as pipeline_commit,
    load_original_payload,
    patch_from_payload,
    rollback as pipeline_rollback,
    validate as pipeline_validate,
)
from .optimization.artifacts import declared_fingerprint
from .onboarding import (
    apply_capabilities,
    propose_capabilities,
    render_capability_diff,
)
from .onboarding.openai_adapter import skeleton_payload
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
    fast_parser.add_argument(
        "--out-dir",
        help="Write the full report.json (candidate routes included) here",
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
        "--allow-seed-mismatch",
        action="store_true",
        help="Run even when the seeds file was generated against a different "
        "declared topology (v2 seeds carry a fingerprint)",
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

    seeds_parser = subparsers.add_parser(
        "seeds", help="Seed lifecycle: freeze verified chains into seeds.json"
    )
    seeds_sub = seeds_parser.add_subparsers(dest="seeds_command", required=True)
    seeds_export = seeds_sub.add_parser(
        "export",
        help="Freeze fast-report candidate chains into replay-verified seeds",
    )
    seeds_export.add_argument("--topology", required=True)
    seeds_export.add_argument("--scenario", required=True)
    seeds_export.add_argument(
        "--fast-report",
        required=True,
        help="fast --out-dir directory (report.json inside) or the report JSON path",
    )
    seeds_export.add_argument("--out", required=True)
    seeds_export.add_argument("--replay-trials", type=int, default=1)
    seeds_export.add_argument("--max-verify", type=int, default=3)
    seeds_export.add_argument(
        "--require-eval",
        action="store_true",
        help="Also require the evaluator (built from --expected-fact) to pass",
    )
    seeds_export.add_argument(
        "--expected-fact",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="A fact the deterministic evaluator expects in the final state (repeatable)",
    )

    seeds_discover = seeds_sub.add_parser(
        "discover",
        help="Discover seeds by letting a router pick tools; chains are "
        "replay-verified before freezing",
    )
    seeds_discover.add_argument("--topology", required=True)
    seeds_discover.add_argument("--scenario", required=True)
    seeds_discover.add_argument("--out", required=True)
    discover_router = seeds_discover.add_mutually_exclusive_group(required=True)
    discover_router.add_argument(
        "--router-config",
        help="JSON {base_url?, model, temperature?, max_tools_per_layer?, "
        "input_cost_per_1k?, output_cost_per_1k?} -> LLMRouter (local Ollama)",
    )
    discover_router.add_argument(
        "--scripted-router",
        help="JSON {scenario_id: {layer: [tools]}} -> offline deterministic "
        "discovery (testing / reproducible experiments)",
    )
    seeds_discover.add_argument("--discovery-trials", type=int, default=1)
    seeds_discover.add_argument("--replay-trials", type=int, default=1)
    seeds_discover.add_argument(
        "--require-eval",
        action="store_true",
        help="Also require the evaluator (built from --expected-fact) to pass",
    )
    seeds_discover.add_argument(
        "--expected-fact",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="A fact the deterministic evaluator expects in the final state (repeatable)",
    )

    optimize = subparsers.add_parser(
        "optimize",
        help="Optimize: report / analyze / validate / commit / rollback",
    )
    optimize_sub = optimize.add_subparsers(dest="optimize_command")

    optimize_report = optimize_sub.add_parser(
        "report", help="Deterministic two-version report (legacy behavior)"
    )
    optimize_report.add_argument("--topology", required=True)
    optimize_report.add_argument("--scenario", required=True)
    optimize_report.add_argument("--start-version", default="v1")
    optimize_report.add_argument("--end-version", default="v2")
    optimize_report.add_argument("--format", choices=("text", "json"), default="text")
    optimize_report.add_argument("--out")

    optimize_analyze = optimize_sub.add_parser(
        "analyze", help="Evidence -> candidate proposal (read-only)"
    )
    optimize_analyze.add_argument("--topology", required=True)
    optimize_analyze.add_argument("--scenario", required=True)
    optimize_analyze.add_argument("--slow-report", required=True)
    optimize_analyze.add_argument("--min-opportunity", type=int, default=None)
    optimize_analyze.add_argument("--out", required=True)

    optimize_validate = optimize_sub.add_parser(
        "validate", help="Candidate patch -> three-gate verdict"
    )
    optimize_validate.add_argument("--topology", required=True)
    optimize_validate.add_argument("--scenario", required=True)
    optimize_validate.add_argument("--patch", required=True, help="candidates.json")
    optimize_validate.add_argument("--trials", type=int, default=3)
    optimize_validate.add_argument(
        "--expected-fact", action="append", default=[], metavar="NAME=VALUE"
    )
    optimize_validate.add_argument("--max-concurrency", type=int, default=1)
    optimize_validate.add_argument("--out", required=True)

    optimize_commit = optimize_sub.add_parser(
        "commit", help="Commit a validated patch as a new topology version"
    )
    optimize_commit.add_argument("--topology", required=True)
    optimize_commit.add_argument("--patch", required=True)
    optimize_commit.add_argument("--validation", required=True)
    optimize_commit.add_argument("--version", required=True)
    optimize_commit.add_argument("--base-version", default="v1")
    optimize_commit.add_argument("--versions-dir", required=True)

    optimize_rollback = optimize_sub.add_parser(
        "rollback", help="Record replay to a version (default: declared)"
    )
    optimize_rollback.add_argument("--topology", required=True)
    optimize_rollback.add_argument("--versions-dir", required=True)
    optimize_rollback.add_argument("--to", default=None, help="Version tag")
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
        "--width",
        type=int,
        default=88,
        help="Wrap route lines at layer boundaries (0 disables wrapping)",
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
    onboard = subparsers.add_parser(
        "onboard",
        help="Onboarding assistance: scaffold / propose / apply",
    )
    onboard_sub = onboard.add_subparsers(dest="onboard_command", required=True)

    scaffold = onboard_sub.add_parser(
        "scaffold", help="OpenAI specs -> skeleton topology JSON"
    )
    scaffold.add_argument("--specs", required=True, help="OpenAI tools JSON")
    scaffold.add_argument("--layer", required=True, help="Layer for all tools")
    scaffold.add_argument(
        "--dispatch-module",
        help="Stamp implementation entry points '<module>:<tool>' (async fns)",
    )
    scaffold.add_argument("--out", required=True, help="Skeleton output path")

    propose = onboard_sub.add_parser(
        "propose", help="Batch capability proposals for human review"
    )
    propose.add_argument("--topology", required=True, help="Topology JSON")
    propose.add_argument(
        "--vocabulary-from",
        help="Existing topology JSON whose capabilities seed the vocabulary",
    )
    propose.add_argument("--base-url", help="Ollama base URL")
    propose.add_argument("--model", help="Ollama model name")
    propose.add_argument("--out", required=True, help="Proposals output path")

    apply_cmd = onboard_sub.add_parser(
        "apply", help="Apply reviewed capabilities into the topology"
    )
    apply_cmd.add_argument("--topology", required=True, help="Topology JSON")
    apply_cmd.add_argument(
        "--approved", required=True,
        help='Approved JSON: {"tool": ["cap", ...]}',
    )
    apply_cmd.add_argument("--out", required=True, help="Final output path")
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
        if args.optimize_command == "report":
            return run_optimize(args)
        if args.optimize_command == "analyze":
            return run_optimize_analyze(args)
        if args.optimize_command == "validate":
            return run_optimize_validate(args)
        if args.optimize_command == "commit":
            return run_optimize_commit(args)
        if args.optimize_command == "rollback":
            return run_optimize_rollback(args)
        build_parser().error(
            f"Unsupported optimize command: {args.optimize_command}"
        )
    if args.command == "rank":
        return run_rank(args)
    if args.command == "select":
        return run_select(args)
    if args.command == "seeds":
        if args.seeds_command == "discover":
            return run_seeds_discover(args)
        return run_seeds_export(args)
    if args.command == "onboard":
        return run_onboard(args)
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
    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / "report.json"
        report_path.write_text(
            json.dumps(fast_report_to_json(report), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        print(f"fast report written to {report_path}")
    if args.fail_on_regression and _has_regression(report, baseline):
        return 1
    return 0


def load_seed_routes(path: str) -> dict[str, CandidateRoute]:
    """Load the ``--basefast`` seed file mapping scenario_id -> CandidateRoute.

    Accepts both the legacy plain ``{scenario_id: route}`` map and the v2
    format with a header (format_version / topology_fingerprint / seeds).
    """
    routes, _ = read_seeds(path)
    return routes


def run_seeds_export(args: argparse.Namespace) -> int:
    topology = TopologyLoader().load_file(args.topology)
    suite = ScenarioLoader().load_file(args.scenario)
    report_path = Path(args.fast_report)
    if report_path.is_dir():
        report_path = report_path / "report.json"
    report = fast_report_from_json(
        json.loads(report_path.read_text(encoding="utf-8"))
    )

    expected: dict[str, str] = {}
    for pair in args.expected_fact:
        name, _, value = pair.partition("=")
        expected[name] = value
    evaluator = StructuredEvaluator(expected) if args.require_eval else None

    payload = asyncio.run(
        export_seeds(
            topology,
            suite,
            report,
            replay_trials=args.replay_trials,
            max_verify=args.max_verify,
            evaluator=evaluator,
        )
    )
    out_path = payload.write(args.out)
    print(
        f"seeds written to {out_path}: {payload.frozen_count}/"
        f"{len(suite.scenarios)} scenarios frozen "
        f"(topology {payload.topology_fingerprint})"
    )
    for entry in payload.entries:
        if entry.status != "frozen":
            print(f"  {entry.scenario_id}: {entry.status} ({entry.reason})")
    return 0


def run_seeds_discover(args: argparse.Namespace) -> int:
    topology = TopologyLoader().load_file(args.topology)
    suite = ScenarioLoader().load_file(args.scenario)

    expected: dict[str, str] = {}
    for pair in args.expected_fact:
        name, _, value = pair.partition("=")
        expected[name] = value
    evaluator = StructuredEvaluator(expected) if args.require_eval else None

    if args.router_config:
        config = json.loads(Path(args.router_config).read_text(encoding="utf-8"))
        router_config = RouterConfig(
            model=config.get("model", "local"),
            temperature=config.get("temperature", 0.0),
            max_tools_per_layer=config.get("max_tools_per_layer", 3),
            input_cost_per_1k=config.get("input_cost_per_1k"),
            output_cost_per_1k=config.get("output_cost_per_1k"),
        )
        base_url = config.get("base_url")

        def router_factory(scenario):
            return LLMRouter(router_config=router_config, base_url=base_url)
    else:
        routing = json.loads(
            Path(args.scripted_router).read_text(encoding="utf-8")
        )

        def router_factory(scenario):
            return ScenarioScriptedRouter(routing=routing.get(scenario.id, {}))

    payload = asyncio.run(
        discover_seeds(
            topology,
            suite,
            router_factory=router_factory,
            discovery_trials=args.discovery_trials,
            replay_trials=args.replay_trials,
            evaluator=evaluator,
        )
    )
    out_path = payload.write(args.out)
    print(
        f"seeds written to {out_path}: {payload.frozen_count}/"
        f"{len(suite.scenarios)} scenarios frozen "
        f"(topology {payload.topology_fingerprint}, source={payload.payload['source']})"
    )
    for entry in payload.entries:
        if entry.status != "frozen":
            print(f"  {entry.scenario_id}: {entry.status} ({entry.reason})")
    return 0


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

    seeds = None
    if args.basefast:
        seeds, seed_fingerprint = read_seeds(args.basefast)
        active_fingerprint = declared_fingerprint(topology)
        if (
            seed_fingerprint is not None
            and seed_fingerprint != active_fingerprint
            and not args.allow_seed_mismatch
        ):
            # Stale seeds silently degrade (baseline ∩ available shrinks);
            # a fingerprint mismatch means the world moved under them.
            print(
                "refusing to run slow regression: seeds file was generated "
                f"against topology {seed_fingerprint} but the declared "
                f"topology is {active_fingerprint}; regenerate the seeds or "
                "pass --allow-seed-mismatch",
                file=sys.stderr,
            )
            return 2
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

    print(render_slow_report(report, failures=outcome.results))
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
        else render_ranking(
            report, width=None if args.width == 0 else args.width
        )
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


def run_onboard(args: argparse.Namespace) -> int:
    """onboard scaffold / propose / apply (onboarding-assist milestone §5).

    Three explicit stages with no implicit chaining: propose never writes a
    topology, apply only consumes the human-approved mapping.
    """
    if args.onboard_command == "scaffold":
        try:
            specs = json.loads(Path(args.specs).read_text(encoding="utf-8"))
            payload = skeleton_payload(
                specs, layer=args.layer, dispatch_module=args.dispatch_module
            )
        except (OSError, json.JSONDecodeError, OnboardingError) as exc:
            print(f"scaffold failed: {exc}", file=sys.stderr)
            return 2
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"skeleton written to {args.out} ({len(payload['tools'])} tools)")
        return 0

    if args.onboard_command == "propose":
        topology = TopologyLoader().load_file(args.topology)
        vocabulary: set[str] = set()
        if args.vocabulary_from:
            other = TopologyLoader().load_file(args.vocabulary_from)
            for name in other.nodes():
                vocabulary |= set(other.node(name).spec.capabilities)
        summaries = [
            ToolSummary.from_tool_node(topology.node(name))
            for name in topology.nodes()
        ]
        try:
            proposal_set = asyncio.run(
                propose_capabilities(
                    summaries,
                    vocabulary=vocabulary,
                    base_url=args.base_url,
                    model=args.model,
                )
            )
        except ProposalError as exc:
            print(f"propose failed: {exc}", file=sys.stderr)
            return 2
        payload = {
            "proposals": [
                {
                    "tool": item.tool,
                    "capabilities": list(item.capabilities),
                    "rationale": item.rationale,
                    "confidence": item.confidence,
                }
                for item in proposal_set.proposals
            ],
            "invalid_dropped": [
                {"tool": item.tool, "capability": item.capability}
                for item in proposal_set.invalid_dropped
            ],
        }
        proposals_path = Path(args.out)
        proposals_path.parent.mkdir(parents=True, exist_ok=True)
        proposals_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"proposals written to {args.out}")
        print()
        print(render_capability_diff(proposal_set, vocabulary=vocabulary))
        return 0

    if args.onboard_command == "apply":
        try:
            payload = json.loads(
                Path(args.topology).read_text(encoding="utf-8")
            )
            approved = json.loads(
                Path(args.approved).read_text(encoding="utf-8")
            )
            written = apply_capabilities(payload, approved, out_path=args.out)
        except (OSError, json.JSONDecodeError, OnboardingError) as exc:
            print(f"apply failed: {exc}", file=sys.stderr)
            return 2
        print(f"topology written to {written}")
        return 0

    build_parser().error(f"Unsupported onboard command: {args.onboard_command}")
    return 2


def run_optimize_analyze(args: argparse.Namespace) -> int:
    print("Optimize Analyze")
    print(f"  topology:    {args.topology}")
    print(f"  scenario:    {args.scenario}")
    print(f"  slow report: {args.slow_report}")
    """optimize analyze: read-only evidence -> candidates.json (spec 2)."""
    try:
        topology = TopologyLoader().load_file(args.topology)
        suite = ScenarioLoader().load_file(args.scenario)
        payload = asyncio.run(
            pipeline_analyze(
                topology,
                suite,
                args.slow_report,
                min_edge_opportunities=args.min_opportunity,
            )
        )
    except Exception as exc:  # noqa: BLE001 - hard error, never silent empty
        print(f"analyze failed: {exc}", file=sys.stderr)
        return 2
    Path(args.out).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    from collections import Counter

    by_status = Counter(item["status"] for item in payload["candidates"])
    print(f"candidates written to {args.out}")
    print(
        f"split: {len(payload['split']['optimization'])} optimization / "
        f"{len(payload['split']['validation'])} validation / "
        f"{len(payload['split']['sentinel'])} sentinel"
    )
    print(f"candidates by status: {dict(sorted(by_status.items())) or '{}'}")
    print("(analyze observes only; validate decides, commit writes)")
    return 0


def run_optimize_validate(args: argparse.Namespace) -> int:
    """optimize validate: three gates; REJECT exits 1 (spec 3)."""
    started = time.perf_counter()
    print("Optimize Validate")
    print(f"  topology: {args.topology}")
    print(f"  scenario: {args.scenario}")
    print(f"  patch:    {args.patch}")
    topology = TopologyLoader().load_file(args.topology)
    unbound = unbound_tool_names(topology)
    if unbound:
        print(
            "validate requires an executable topology; tools without "
            f"implementation: {', '.join(unbound)}",
            file=sys.stderr,
        )
        return 2
    try:
        suite = ScenarioLoader().load_file(args.scenario)
        patch_payload = json.loads(Path(args.patch).read_text(encoding="utf-8"))
        patch = patch_from_payload(patch_payload)
        expected: dict[str, str] = {}
        for pair in args.expected_fact:
            name, _, value = pair.partition("=")
            expected[name] = value
        verdict = asyncio.run(
            pipeline_validate(
                topology,
                suite,
                patch,
                config=ValidateConfig(
                    trials=args.trials,
                    expected_facts=tuple(sorted(expected.items())),
                    max_concurrency=args.max_concurrency,
                ),
            )
        )
    except (ArtifactLoadError, OptimizePipelineError) as exc:
        print(f"validate failed: {exc}", file=sys.stderr)
        return 2
    Path(args.out).write_text(
        json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"verdict written to {args.out}")
    print("Gates:")
    for label, gate in (
        ("fast (coverage)", verdict["fast"]),
        ("slow (regression)", verdict["slow"]),
        ("diversity", verdict["diversity"]),
    ):
        if gate.get("skipped"):
            mark = "SKIP"
        elif gate.get("passed"):
            mark = "PASS"
        else:
            mark = "FAIL"
        print(f"  {label:<20}{mark}")
    for failure in verdict["failures"]:
        print(f"  [{failure['domain']}/{failure['key']}] {failure['reason']}")
    elapsed = time.perf_counter() - started
    print(f"VERDICT: {verdict['verdict'].upper()}  ({elapsed:.1f}s)")
    return 0 if verdict["verdict"] == "accept" else 1


def run_optimize_commit(args: argparse.Namespace) -> int:
    """optimize commit: the only write; ACCEPT record is a hard gate (spec 4)."""
    try:
        topology = TopologyLoader().load_file(args.topology)
        payload = load_original_payload(args.topology)
        patch = patch_from_payload(
            json.loads(Path(args.patch).read_text(encoding="utf-8"))
        )
        validation = json.loads(Path(args.validation).read_text(encoding="utf-8"))
        record = pipeline_commit(
            topology,
            payload,
            patch,
            validation=validation,
            version=args.version,
            versions_dir=args.versions_dir,
            base_version=args.base_version,
        )
    except CommitGateError as exc:
        print(f"commit refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, OptimizePipelineError) as exc:
        print(f"commit failed: {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.versions_dir)
    print(f"version {record['version']} committed:")
    print(f"  record:   {out_dir / (record['version'] + '.json')}")
    print(f"  topology: {out_dir / (record['version'] + '.topology.json')}")
    print("rollback: optimize rollback --to " + record["version"])
    return 0


def run_optimize_rollback(args: argparse.Namespace) -> int:
    """optimize rollback: record replay, never an inverse patch (spec 4)."""
    try:
        topology = TopologyLoader().load_file(args.topology)
        payload = load_original_payload(args.topology)
        record = pipeline_rollback(
            topology, payload, versions_dir=args.versions_dir, to=args.to
        )
    except CommitGateError as exc:
        print(f"rollback refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, OptimizePipelineError) as exc:
        print(f"rollback failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"current active topology -> {record['version']} "
        "(record replay; history untouched)"
    )
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