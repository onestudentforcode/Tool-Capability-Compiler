"""Fast Regression command-line interface.

Usage::

    tool-topology regression fast \\
        --topology topology.json \\
        --scenario scenarios/refund.json \\
        [--mode gold|discovery] [--topology-version v1] \\
        [--baseline baseline.json] [--save-baseline out.json] \\
        [--base-url URL] [--model MODEL] [--fail-on-regression]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from .capability import OllamaCapabilityResolver
from .regression.baseline import Baseline, BaselineStore, compute_diff
from .regression.coverage import CoverageStatus
from .regression.report import CoverageReport, FastRegressionRunner
from .scenario import ScenarioLoader, ScenarioSuite
from .topology.loader import TopologyLoader


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "regression" or args.subcommand != "fast":
        build_parser().error(f"Unsupported command: {args.command} {args.subcommand}")
    return run_fast(args)


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