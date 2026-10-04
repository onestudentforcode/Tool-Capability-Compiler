"""Office battlefield batch D: scale slow-regression run + coverage report.

Runs the office domain at scale over the authored 60-scenario suite (default
5 trials each), with every LLM call routed through the deterministic offline
fake (``office_llm.install_offline_fake``) so the run is fully reproducible.
Writes the full slow artifact set plus ``coverage.json`` (fast-regression
semantics: COVERED / UNCERTAIN / UNCOVERED per scenario) and the exact suite
copy for downstream CLI steps. Observation only — pruning happens in the
optimize pipeline (batch E).

Usage::

    python run_scale.py [--trials 5] [--out-dir artifacts]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path


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

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from capability_runtime import (  # noqa: E402
    CandidateRoute,
    RouteLayer,
    ScenarioLoader,
    SlowRegressionRunner,
    SlowRegressionWriter,
    build_observation_stats,
    build_slow_regression_report,
)
from capability_runtime.core.failure import TrialFailureCategory  # noqa: E402

from examples.office import office, office_llm  # noqa: E402
from examples.office.fixtures import OfficeFixtureManager  # noqa: E402

SUITE_PATH = _HERE / "scenarios.json"

# Canonical (recommended) tool per capability: the basefast seed choice.
# Redundant capabilities pin the steady/recommended variant; text.translate
# pins the fast variant so its seeded stall produces TIMEOUT traffic.
_CAPABILITY_TOOLS = {
    "doc.parse": "doc_parse",
    "fs.read": "fs_read",
    "table.parse": "table_parse",
    "ppt.parse": "deck_parse",
    "table.profile": "table_profile",
    "table.repair": "table_header_fix_rule",
    "text.segment": "text_segment",
    "fact.extract": "keyfact_extract",
    "text.summarize": "text_summarize_steady",
    "text.translate": "text_translate_fast",
    "formula.audit": "formula_scan",
    "data.aggregate": "data_aggregate",
    "chart.prepare": "chart_prepare",
    "style.profile": "style_profile",
    "outline.compose": "outline_doc_gen_steady",
    "outline.slides": "outline_slide_gen",
    "section.draft": "draft_section_steady",
    "mail.draft": "draft_email_detailed",
    "notes.draft": "draft_speaker_notes",
    "formula.generate": "formula_gen_steady",
    "slide.copy": "slide_copy_steady",
    "text.polish": "text_polish_conservative",
    "text.expand": "text_expand",
    "title.compose": "title_gen",
    "chart.select": "chart_type_pick_rule",
    "palette.select": "theme_palette_pick",
    "insight.narrate": "insight_narrate",
    "text.grammar": "grammar_check_rule",
    "style.check": "style_check",
    "fact.verify": "fact_check_judge",
    "doc.length": "doc_length_check",
    "formula.check": "formula_check",
    "ppt.overflow": "slide_overflow_check",
    "chart.check": "chart_data_check",
    "quality.judge": "quality_judge",
    "doc.render": "render_doc",
    "table.render": "render_xlsx",
    "ppt.render": "render_pptx",
    "chart.render": "render_chart",
}


# Redundant capabilities whose canonical seed rotates deterministically per
# scenario (stable id hash): every variant anchors its own routes, so route
# profiles and rank tiers can actually differentiate the variants (§5).
_VARIANT_ROTATION = {
    "section.draft": (
        "draft_section_fast",
        "draft_section_steady",
        "draft_section_verbose",
    ),
    "mail.draft": ("draft_email_concise", "draft_email_detailed"),
}


def _rotated_tool(scenario_id: str, capability: str) -> str | None:
    rotation = _VARIANT_ROTATION.get(capability)
    if rotation is None:
        return None
    digest = hashlib.sha256(scenario_id.encode("utf-8")).hexdigest()
    return rotation[int(digest, 16) % len(rotation)]


def build_seeds(suite, topology) -> dict[str, CandidateRoute]:
    """One feasible seed route per scenario, derived from its expected caps.

    Deferred (uncovered) capabilities have no provider and are honestly
    absent from the route; the run records the shortfall as business failure
    evidence instead of inventing tools. Redundant capabilities rotate their
    canonical variant per scenario id (see ``_VARIANT_ROTATION``).
    """
    layer_order = {layer.name: layer.order for layer in topology.layers()}
    seeds: dict[str, CandidateRoute] = {}
    for scenario in suite.scenarios:
        by_order: dict[int, tuple[str, set[str]]] = {}
        for capability in scenario.expected_capabilities:
            tool = _rotated_tool(scenario.id, capability) or _CAPABILITY_TOOLS.get(
                capability
            )
            if tool is None or tool not in topology.nodes():
                continue
            spec = topology.node(tool).spec
            _, tools = by_order.setdefault(layer_order[spec.layer], (spec.layer, set()))
            tools.add(tool)
        layers = tuple(
            RouteLayer(by_order[order][0], tuple(sorted(by_order[order][1])))
            for order in sorted(by_order)
        )
        seeds[scenario.id] = CandidateRoute(layers=layers, capabilities=frozenset())
    return seeds


def _seed_covers(route: CandidateRoute, required: frozenset[str], topology) -> bool:
    covered: set[str] = set()
    for segment in route.layers:
        for tool in segment.tools:
            covered |= topology.node(tool).spec.capabilities
    return required.issubset(covered)


def coverage_report(suite, topology, seeds: dict[str, CandidateRoute]) -> dict:
    """Fast-regression coverage semantics, derived from seed witness routes.

    Semantics (identical to CoverageAnalyzer): a scenario is UNCOVERED when a
    required capability has no provider, UNCERTAIN when its discovery
    metadata marks ambiguity / low resolution confidence, and COVERED when
    every required capability has a provider and a feasible declared route
    exists. Feasibility is witnessed by the scenario's constructed seed
    route (adjacent layers, real providers, dense declared edges) instead of
    the exact RouteSearch: the exact analyzer enumerates every tool subset
    of the maximal chain, which is exponential and infeasible on a 51-node
    battlefield — the seed witness proves the same fact in linear time.
    """
    providers: dict[str, set[str]] = {}
    for name in topology.nodes():
        for capability in topology.node(name).spec.capabilities:
            providers.setdefault(capability, set()).add(name)

    rows = []
    counts: Counter = Counter()
    for scenario in suite.scenarios:
        required = frozenset(scenario.expected_capabilities)
        metadata = dict(scenario.metadata or {})
        missing = sorted(cap for cap in required if not providers.get(cap))
        if missing:
            status, reason = "uncovered", "missing_capability"
        elif metadata.get("discovery") == "ambiguous":
            status, reason = "uncertain", "ambiguous_capability"
        elif metadata.get("discovery") == "low_confidence":
            status, reason = "uncertain", "low_resolution_confidence"
        elif scenario.id in seeds and _seed_covers(
            seeds[scenario.id], required, topology
        ):
            status, reason = "covered", None
        else:
            status, reason = "uncovered", "topology_disconnected"
        counts[status] += 1
        rows.append(
            {
                "scenario_id": scenario.id,
                "category": scenario.category,
                "status": status,
                "reason": reason,
                "missing_capabilities": missing,
            }
        )
    return {
        "suite": suite.name,
        "suite_version": suite.version,
        "distribution": dict(sorted(counts.items())),
        "scenarios": rows,
    }


def failure_breakdown(results) -> dict:
    statuses: Counter = Counter(result.execution_status.value for result in results)
    categories: Counter = Counter(
        result.failure_category.value
        for result in results
        if result.failure_category is not None
    )
    return {
        "execution_status": dict(sorted(statuses.items())),
        "failure_category": dict(sorted(categories.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Office scale slow-regression run")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument(
        "--topology-version", default=office.DEFAULT_TOPOLOGY_VERSION
    )
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir) if args.out_dir else _HERE / "artifacts" / "scale"
    run_dir = out_dir / f"scale_{time.strftime('%Y%m%d%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    # Console archive: everything printed from here on (success or failure)
    # is mirrored into the run directory.
    console = open(run_dir / "console.txt", "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, console)
    sys.stderr = _Tee(sys.stderr, console)

    topology, version = office.build_topology(topology_version=args.topology_version)
    suite = ScenarioLoader().load_file(str(SUITE_PATH))
    seeds = build_seeds(suite, topology)

    office_llm.install_offline_fake()

    started = time.perf_counter()
    outcome = asyncio.run(
        SlowRegressionRunner(
            topology=topology,
            evaluator=office.build_evaluator(),
            fixture_manager=OfficeFixtureManager(),
            seeds=seeds,
            trials_per_scenario=args.trials,
            topology_version=version,
            router_config_id="basefast-office",
            per_tool_timeout_seconds=0.15,
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
        router_config_id="basefast-office",
    )

    writer = SlowRegressionWriter(run_dir)
    writer.write(
        run_id=f"scale-{run_dir.name}",
        suite_name=suite.name,
        suite_version=suite.version,
        topology_version=version,
        router_config_id="basefast-office",
        evaluator="composite:office_family+review_quality",
        outcome=outcome,
        report=report,
        obs=obs,
    )
    (run_dir / "coverage.json").write_text(
        json.dumps(
            coverage_report(suite, topology, seeds), indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    # Persist the exact suite so downstream CLI steps (optimize analyze /
    # validate) split the very same scenario IDs.
    (run_dir / "scenarios.json").write_text(
        json.dumps(
            {
                "name": suite.name,
                "version": suite.version,
                "description": suite.description,
                "scenarios": [
                    {
                        "id": scenario.id,
                        "query": scenario.query,
                        "category": scenario.category,
                        "expected_capabilities": list(
                            scenario.expected_capabilities
                        ),
                        "metadata": dict(scenario.metadata),
                    }
                    for scenario in suite.scenarios
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    breakdown = failure_breakdown(outcome.results)
    summary = {
        "run": {
            "scenarios": len(suite.scenarios),
            "trials_per_scenario": args.trials,
            "total_trials": len(outcome.results),
            "unique_routes": obs.unique_route_count,
            "business_success": report.business_success,
            "business_failure": report.business_failure,
            "topology_version": version,
            "elapsed_seconds": round(elapsed, 2),
        },
        "coverage": json.loads(
            (run_dir / "coverage.json").read_text(encoding="utf-8")
        )["distribution"],
        "failures": breakdown,
    }
    (run_dir / "scale_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Office scale run: {len(suite.scenarios)} scenarios x {args.trials} trials")
    coverage = summary["coverage"]
    print(
        f"Coverage: covered {coverage.get('covered', 0)} / "
        f"uncertain {coverage.get('uncertain', 0)} / "
        f"uncovered {coverage.get('uncovered', 0)}"
    )
    print(
        f"Business: {report.business_success} success / "
        f"{report.business_failure} failed"
    )
    print("Status: " + " | ".join(
        f"{name} {count}" for name, count in breakdown["execution_status"].items()
    ))
    print("Failures: " + (" | ".join(
        f"{name} {count}" for name, count in breakdown["failure_category"].items()
    ) or "none"))
    print(f"Unique routes: {obs.unique_route_count}  elapsed: {elapsed:.1f}s")
    print(f"Artifacts in {run_dir}:")
    for path in sorted(run_dir.iterdir()):
        print(f"  {path.name:<26}{path.stat().st_size:>10} bytes")
    try:  # the readable mirror must never fail the run itself
        from examples.office import render_review

        render_review.write_review(run_dir)
        print(f"  {'review.md':<26}{'(human-readable mirror)':>10}")
    except Exception as exc:  # noqa: BLE001
        print(f"  review rendering skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
