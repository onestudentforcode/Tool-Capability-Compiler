"""Office composite integration (office-battlefield batch F).

End to end over the exported JSON topology: the composite suite runs
through the standard slow pipeline, every composite execution converges
in exactly two iterations, the flattener turns each iteration into an
inner pseudo-trial, and honest billing shows up on the outer execution.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from capability_runtime import (  # noqa: E402
    ScenarioLoader,
    SlowRegressionRunner,
    TopologyLoader,
    flatten_composite_results,
)

from examples.office import composite_nodes, office, office_llm  # noqa: E402
from examples.office.fixtures import OfficeFixtureManager  # noqa: E402
from examples.office.run_composite_demo import build_seeds  # noqa: E402

SUITE_PATH = _ROOT / "examples" / "office" / "scenarios_composite.json"
TOPOLOGY_PATH = _ROOT / "examples" / "topology" / "office.json"


def test_exported_payload_carries_composite_entries() -> None:
    payload = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    composites = [t for t in payload["tools"] if t.get("kind") == "composite"]
    assert {t["name"] for t in composites} == {
        "doc_composed_report",
        "ppt_composed_deck",
    }
    for item in composites:
        assert item["route"] and item["stop_when"] and item["max_iterations"] >= 1
        assert item["consumes"] and item["produces"]
        inner = TOPOLOGY_PATH.parent / item["inner"]
        assert inner.is_file(), inner


def test_composite_suite_through_json_topology() -> None:
    topology = TopologyLoader().load_file(str(TOPOLOGY_PATH))
    assert {"doc_composed_report", "ppt_composed_deck"} <= set(topology.nodes())
    suite = ScenarioLoader().load_file(str(SUITE_PATH))
    seeds = build_seeds(suite, topology)
    office_llm.install_offline_fake()

    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=office.build_evaluator(),
        fixture_manager=OfficeFixtureManager(),
        seeds=seeds,
        trials_per_scenario=1,
        topology_version=office.DEFAULT_TOPOLOGY_VERSION,
        router_config_id="composite-it",
        per_tool_timeout_seconds=0.5,
    )
    outcome = asyncio.run(runner.run(suite))

    assert len(outcome.results) == 6
    evaluated = [r for r in outcome.results if r.evaluation is not None]
    successes = [r for r in evaluated if r.evaluation.success]
    # deterministic under the offline fake: all six scenarios deliver
    # (trial 0 seed routes; the fake's 4% wobble does not fire here)
    assert len(successes) == 6

    composite_executions = [
        execution
        for result in outcome.results
        for layer in result.trace.layers
        for execution in layer.tool_executions
        if execution.tool_name in composite_nodes.SPECS
    ]
    assert len(composite_executions) == 6
    for execution in composite_executions:
        assert len(execution.composite_detail) == 2
        # honest billing: the outer execution carries the declared sum
        spec = composite_nodes.SPECS[execution.tool_name]
        assert execution.cost == spec.cost_per_call
        # metering rolled up: gate reads/writes are visible on the outer tool
        assert execution.access_counts

    flattened = flatten_composite_results(outcome.results, composite_nodes.SPECS)
    assert len(flattened) == 12  # 6 executions x 2 iterations
    inner_ids = {t.trial.id for t in flattened}
    assert any(":doc_composed_report:0" in i for i in inner_ids)
    assert any(":doc_composed_report:1" in i for i in inner_ids)
    assert any(":ppt_composed_deck:1" in i for i in inner_ids)
