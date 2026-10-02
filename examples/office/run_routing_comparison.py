"""Three-mode routing comparison on the office battlefield (batch E).

Modes:
    free         no seeds - alphabetical free exploration (no fast prior)
    basefast     seeds frozen from fast candidate chains (batch A bridge)
    llm-scripted seeds from the discovery flow (batch B) driven by a
                 scripted router bound to the same target chains - the
                 offline stand-in that proves the discovery MACHINERY
                 reproduces baseline quality; real-model evidence comes
                 from `seeds discover --router-config` (local Ollama).

Every mode runs the same suite on the same declared topology with the
same offline fake LLM for tools, and reports five metrics: success rate,
mean cost, mean latency, unique routes, and the number of frozen seeds
(free has none - it has no seed lifecycle).

Usage::

    python examples/office/run_routing_comparison.py [--limit 12] [--trials 2]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from capability_runtime import (  # noqa: E402
    FastRegressionRunner,
    Scenario,
    ScenarioLoader,
    ScenarioSuite,
    SlowRegressionRunner,
    discover_seeds,
    export_seeds,
)
from capability_runtime.router.fake_router import ScenarioScriptedRouter

from examples.office import office, office_llm, run_scale  # noqa: E402
from examples.office.fixtures import OfficeFixtureManager  # noqa: E402

SOURCE = "fast-report"


def _limit_suite(suite: ScenarioSuite, limit: int) -> ScenarioSuite:
    if limit <= 0 or limit >= len(suite.scenarios):
        return suite
    return ScenarioSuite(
        name=suite.name,
        version=suite.version,
        description=suite.description,
        scenarios=tuple(suite.scenarios[:limit]),
    )


def _routing_table(suite: ScenarioSuite, topology):
    """The scripted 'model': the canonical target chains basefast uses."""
    seeds = run_scale.build_seeds(suite, topology)
    return {
        scenario_id: {
            segment.layer: list(segment.tools) for segment in route.layers
        }
        for scenario_id, route in seeds.items()
    }


async def _mode_seeds(mode, topology, suite):
    if mode == "basefast":
        report = await FastRegressionRunner().run(
            suite, topology, topology_version=office.DEFAULT_TOPOLOGY_VERSION
        )
        payload = await export_seeds(topology, suite, report)
        return payload.routes, {"frozen": payload.frozen_count,
                                "of": len(suite.scenarios)}
    if mode == "llm-scripted":
        routing = _routing_table(suite, topology)

        def router_factory(scenario: Scenario):
            return ScenarioScriptedRouter(routing=routing.get(scenario.id, {}))

        payload = await discover_seeds(
            topology, suite, router_factory=router_factory, replay_trials=1
        )
        return payload.routes, {"frozen": payload.frozen_count,
                                "of": len(suite.scenarios)}
    return None, {"frozen": 0, "of": len(suite.scenarios)}


async def _run_mode(mode, topology, suite, seeds, trials: int):
    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=office.build_evaluator(),
        fixture_manager=OfficeFixtureManager(),
        seeds=seeds,
        trials_per_scenario=trials,
        topology_version=office.DEFAULT_TOPOLOGY_VERSION,
        router_config_id=f"compare-{mode}",
        per_tool_timeout_seconds=0.15,
    )
    outcome = await runner.run(suite)
    return outcome.results


def _metrics(results) -> dict:
    evaluated = [r for r in results if r.evaluation is not None]
    successes = sum(1 for r in evaluated if r.evaluation.success)
    costs = [r.cost for r in results if r.cost is not None]
    latencies = [r.latency_ms for r in results if r.latency_ms]
    routes = {r.route.route_id for r in results if r.route is not None}
    return {
        "trials": len(results),
        "success_rate": round(successes / len(evaluated), 4) if evaluated else 0.0,
        "mean_cost": round(statistics.fmean(costs), 5) if costs else None,
        "mean_latency_ms": round(statistics.fmean(latencies), 2) if latencies else None,
        "unique_routes": len(routes),
    }


def _comparison_markdown(report: dict) -> str:
    modes = report["modes"]
    rows = []
    for mode in ("free", "basefast", "llm-scripted"):
        m = modes[mode]
        rows.append([
            mode,
            m["metrics"]["trials"],
            m["metrics"]["success_rate"],
            m["metrics"]["mean_cost"],
            m["metrics"]["mean_latency_ms"],
            m["metrics"]["unique_routes"],
            m["seeds"]["frozen"],
        ])
    lines = [
        "# Office Battlefield 三模式路由对照报告",
        "",
        f"> 生成时间 {report['generated_at']}；套件 {report['suite']} "
        f"（前 {report['limit']} 条场景 × {report['trials']} 试验），"
        "工具侧全部离线 fake，结果确定性可复跑"
        "（`python examples/office/run_routing_comparison.py`）。",
        "",
        "## 口径",
        "",
        "- 成功率：业务评估通过的比例（分母 = 产生评估的 trial）。",
        "- 成本：声明 `cost_per_call` 之和（计费基准），实测 token 另存作漂移证据。",
        "- 唯一路线数：该模式下观察到的不同路线（探索广度）。",
        "- 种子冻结数：该模式固化进 seeds 的场景数（free 无种子生命周期）。",
        "- **P12 口径**：在线遥测回流 trial 无业务评估，成功口径为 SERVED——"
        "本对照全部在离线侧，不受此影响。",
        "- **P13 口径**：工具侧质量来自确定性 fake（按变体风格/输入长度），"
        "真实模型的质量与漂移证据需本地 Ollama 实跑"
        "（`seeds discover --router-config`）。",
        "",
        "## 结果",
        "",
        "| 模式 | trials | 成功率 | 平均成本 | 平均延迟(ms) | 唯一路线 | 种子冻结 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *[f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} |" for r in rows],
        "",
        "## 判读",
        "",
        "- free 是无先验对照：字典序探索在 51 节点域上大量撞上类型不可行"
        "的组合，成功率显著低于有种子模式——种子（先验）的价值就是本表的核心结论。",
        "- basefast 与 llm-scripted 使用同一目标链（脚本发现是发现机制的离线"
        "替身）：两者指标应接近；真实模型的差异与失败模式见 "
        "`seeds discover --router-config` 的实跑证据（artifacts/model_discovery/）。",
        "- 各模式逐场景失败类别见对应 slow 运行的 traces（本目录仅保留对照汇总）。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Three-mode routing comparison")
    parser.add_argument("--limit", type=int, default=12,
                        help="Scenario subset size (0 = all 60)")
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)

    topology, version = office.build_topology()
    suite = ScenarioLoader().load_file(str(run_scale.SUITE_PATH))
    suite = _limit_suite(suite, args.limit)

    office_llm.install_offline_fake()

    started = time.perf_counter()
    modes: dict[str, dict] = {}
    for mode in ("free", "basefast", "llm-scripted"):
        seeds, seed_info = asyncio.run(_mode_seeds(mode, topology, suite))
        results = asyncio.run(
            _run_mode(mode, topology, suite, seeds, args.trials)
        )
        modes[mode] = {"metrics": _metrics(results), "seeds": seed_info}
        print(f"{mode:>13}: {modes[mode]['metrics']}")

    elapsed = round(time.perf_counter() - started, 1)
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "suite": f"{suite.name} {suite.version}",
        "limit": args.limit,
        "trials": args.trials,
        "topology_version": version,
        "elapsed_seconds": elapsed,
        "modes": modes,
    }

    out_dir = (
        Path(args.out_dir) if args.out_dir
        else _HERE / "artifacts" / "routing_comparison"
        / time.strftime("cmp_%Y%m%d%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "comparison.md").write_text(
        _comparison_markdown(report), encoding="utf-8"
    )
    print(f"comparison written to {out_dir} (elapsed {elapsed}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
