"""Office composite showcase run (office-battlefield batch F).

Drives the 6-scenario composite suite through the real slow pipeline
(offline fake for tool-side LLM calls), then proves the direction-2
showcase claims:

  1. the composites execute as ordinary tools (seeds route through them),
     converging in a deterministic number of bounded iterations;
  2. metering of every inner execution rolls up onto the outer tool;
  3. flatten_composite_results turns every iteration into an inner
     pseudo-trial, so the standard observation machinery sees the inner
     world.

Artifacts land in artifacts/composite_demo/<run>/ with the console
mirrored to console.txt and a human-readable summary in review.md.

Usage::

    python examples/office/run_composite_demo.py [--trials 3]
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
    CandidateRoute,
    RouteLayer,
    ScenarioLoader,
    SlowRegressionRunner,
    build_observation_stats,
    flatten_composite_results,
)

from examples.office import composite_nodes, office, office_llm  # noqa: E402
from examples.office.fixtures import OfficeFixtureManager  # noqa: E402

SUITE_PATH = _HERE / "scenarios_composite.json"

_CAPABILITY_TOOLS = {
    "doc.parse": "doc_parse",
    "fact.extract": "keyfact_extract",
    "doc.composed_report": "doc_composed_report",
    "ppt.composed_deck": "ppt_composed_deck",
    "quality.judge": "quality_judge",
    "doc.render": "render_doc",
    "ppt.overflow": "slide_overflow_check",
    "ppt.render": "render_pptx",
}


class _Tee:
    """Mirror stdout into the run's console.txt (failure-safe)."""

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


def build_seeds(suite, topology):
    layer_order = {layer.name: layer.order for layer in topology.layers()}
    seeds = {}
    for scenario in suite.scenarios:
        by_order = {}
        for capability in scenario.expected_capabilities:
            tool = _CAPABILITY_TOOLS.get(capability)
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


def composite_iterations(outcome) -> dict[str, list[int]]:
    """Per composite name: the iteration count of every execution."""
    counts: dict[str, list[int]] = {}
    for result in outcome.results:
        for layer in result.trace.layers:
            for execution in layer.tool_executions:
                if execution.composite_detail:
                    counts.setdefault(execution.tool_name, []).append(
                        len(execution.composite_detail)
                    )
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Office composite showcase run")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)

    out_dir = (
        Path(args.out_dir) if args.out_dir
        else _HERE / "artifacts" / "composite_demo" / time.strftime("demo_%Y%m%d%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    console = open(out_dir / "console.txt", "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, console)

    topology, version = office.build_topology()
    suite = ScenarioLoader().load_file(str(SUITE_PATH))
    seeds = build_seeds(suite, topology)
    office_llm.install_offline_fake()

    started = time.perf_counter()
    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=office.build_evaluator(),
        fixture_manager=OfficeFixtureManager(),
        seeds=seeds,
        trials_per_scenario=args.trials,
        topology_version=version,
        router_config_id="composite-showcase",
        per_tool_timeout_seconds=0.5,
    )
    outcome = asyncio.run(runner.run(suite))
    elapsed = round(time.perf_counter() - started, 1)

    evaluated = [r for r in outcome.results if r.evaluation is not None]
    successes = sum(1 for r in evaluated if r.evaluation.success)
    print(f"Composite showcase: {len(suite.scenarios)} scenarios x {args.trials} trials")
    print(f"Business: {successes}/{len(evaluated)} success ({elapsed}s)")

    iterations = composite_iterations(outcome)
    for name, counts in sorted(iterations.items()):
        print(
            f"  {name}: {len(counts)} executions, iterations per execution "
            f"{Counter(counts)}"
        )

    # inner world through the standard machinery
    flattened = flatten_composite_results(outcome.results, composite_nodes.SPECS)
    inner_obs = build_observation_stats(flattened, edges=())
    inner_summary = {
        "pseudo_trials": len(flattened),
        "inner_unique_routes": inner_obs.unique_route_count,
    }
    print(f"Flattened inner world: {inner_summary}")

    # metering rollup: composite ToolExecutions carry the inner access counts
    rollup = {}
    for result in outcome.results:
        for layer in result.trace.layers:
            for execution in layer.tool_executions:
                if execution.tool_name in composite_nodes.SPECS:
                    bucket = rollup.setdefault(
                        execution.tool_name,
                        {"executions": 0, "access_counts": Counter(), "tokens": 0},
                    )
                    bucket["executions"] += 1
                    for key, count in (execution.access_counts or {}).items():
                        bucket["access_counts"][key] += count
                    if execution.token_usage is not None:
                        bucket["tokens"] += execution.token_usage.total
    for name, bucket in sorted(rollup.items()):
        print(
            f"  {name}: metering rollup {dict(bucket['access_counts'])}, "
            f"tokens={bucket['tokens']}"
        )

    report = {
        "run": {
            "scenarios": len(suite.scenarios),
            "trials_per_scenario": args.trials,
            "total_trials": len(outcome.results),
            "business_success": successes,
            "evaluated": len(evaluated),
            "elapsed_seconds": elapsed,
        },
        "composite_iterations": {
            name: dict(Counter(counts)) for name, counts in sorted(iterations.items())
        },
        "inner_world": inner_summary,
        "metering_rollup": {
            name: {
                "executions": bucket["executions"],
                "access_counts": dict(bucket["access_counts"]),
                "tokens": bucket["tokens"],
            }
            for name, bucket in sorted(rollup.items())
        },
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "review.md").write_text(
        "\n".join(
            [
                "# Office 复合节点展台（批次 F）",
                "",
                f"> {time.strftime('%Y-%m-%d %H:%M')}，离线确定性，"
                "`python examples/office/run_composite_demo.py` 可复跑。",
                "",
                "## 外层：复合节点即普通工具",
                "",
                f"- {len(suite.scenarios)} 场景 × {args.trials} 试验，路线经种子直穿"
                " doc_composed_report / ppt_composed_deck；",
                f"- 业务成功 {successes}/{len(evaluated)}；",
                f"- 逐复合节点迭代次数：{report['composite_iterations']}",
                "  （预期全部 2：轮1 失败+修润，轮2 幂等重放+放行）；",
                "",
                "## 内层：同一套机器",
                "",
                f"- flatten 展平出 {inner_summary['pseudo_trials']} 个内层伪 trial，"
                f"内层唯一路线 {inner_summary['inner_unique_routes']} 条；",
                f"- 计量聚合：{json.dumps(report['metering_rollup'], ensure_ascii=False)}",
                "  （门缓存读写计入 access_counts，LLM token 汇入外层执行）。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Artifacts in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
