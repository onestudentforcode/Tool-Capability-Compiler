"""Office battlefield batch A: type spine, corpus reads, topology export.

Acceptance (docs/acceptance/office-battlefield.md §9): the type spine flows
end to end (L0 corpus reads land typed artifacts on the blackboard) and the
exported topology is executable JSON that ``regression slow`` can run.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from capability_runtime import (  # noqa: E402
    LayerRegistry,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    ToolRegistry,
    TopologyBuilder,
    TopologyLoader,
    unbound_tool_names,
)
from capability_runtime.cli import main as cli_main  # noqa: E402
from capability_runtime.regression.slow.trial import (  # noqa: E402
    TrialExecutionStatus,
)
from capability_runtime.resources.metering import (  # noqa: E402
    mount_collector,
    unmount_collector,
)

from examples.office import facts, office, store, tools_l0  # noqa: E402
from examples.office.export_topology import build_payload  # noqa: E402

OFFICE_DIR = _ROOT / "examples" / "office"


async def call_tool(node):
    """Invoke a tool handler the way ToolExecutor does: with a metering
    context mounted, so InMemoryStore reads are counted, not rejected."""
    token = mount_collector()
    try:
        return await node.handler()
    finally:
        unmount_collector(token)

TYPE_SPINE = (
    facts.SourceDoc,
    facts.DataTable,
    facts.SlideDigest,
    facts.FactSheet,
    facts.Outline,
    facts.FormulaSpec,
    facts.SlideOutline,
    facts.StyleSpec,
    facts.ChartSpec,
    facts.Draft,
    facts.SlideCopy,
    facts.Narrative,
    facts.EmailDraft,
    facts.ReviewReport,
    facts.FileSpec,
)


def test_type_spine_is_frozen_dataclasses() -> None:
    assert len(TYPE_SPINE) == 15
    for value_type in TYPE_SPINE:
        assert dataclasses.is_dataclass(value_type), value_type.__name__
        assert value_type.__dataclass_params__.frozen, value_type.__name__


def test_l0_tools_read_corpus_into_typed_artifacts() -> None:
    store.STORE.reset()

    async def scenario() -> None:
        doc = await call_tool(tools_l0.fs_read)
        assert isinstance(doc, facts.SourceDoc)
        assert doc.doc_id == "weekly_report"
        assert doc.title == ""

        parsed = await call_tool(tools_l0.doc_parse)
        assert isinstance(parsed, facts.SourceDoc)
        assert parsed.title == "Weekly Report - Platform Team"
        assert "auth migration" in parsed.text

        table = await call_tool(tools_l0.table_parse)
        assert isinstance(table, facts.DataTable)
        assert table.table_id == "sales"
        assert table.columns == ("region", "month", "units", "revenue")
        assert len(table.rows) == 12

        deck = await call_tool(tools_l0.deck_parse)
        assert isinstance(deck, facts.SlideDigest)
        assert deck.deck_id == "q3_review"
        assert deck.slides[0][0] == "Q3 Review"

    asyncio.run(scenario())


def test_l0_reads_fail_when_corpus_is_missing() -> None:
    store.STORE.reset()
    store.STORE.docs.clear()
    store.STORE.tables.clear()
    store.STORE.decks.clear()

    async def scenario() -> None:
        for reader in (tools_l0.fs_read, tools_l0.doc_parse):
            try:
                await call_tool(reader)
            except LookupError:
                pass
            else:
                raise AssertionError(f"{reader.spec.name} should fail on empty docs")
        for reader in (tools_l0.table_parse, tools_l0.deck_parse):
            try:
                await call_tool(reader)
            except LookupError:
                pass
            else:
                raise AssertionError(f"{reader.spec.name} should fail on empty input")

    asyncio.run(scenario())
    store.STORE.reset()


def test_topology_declares_five_layers_with_l0_nodes() -> None:
    topology, version = office.build_topology()
    assert version == office.DEFAULT_TOPOLOGY_VERSION
    assert [layer.name for layer in topology.layers()] == list(office.LAYERS)
    assert [layer.order for layer in topology.layers()] == [0, 1, 2, 3, 4]
    # The four batch-A readers live in the context layer; later batches fill
    # the upper layers (51 nodes in total once B/C have landed).
    for name in ("fs_read", "doc_parse", "table_parse", "deck_parse"):
        assert topology.node(name).spec.layer == "context"


def test_l0_artifacts_reach_the_blackboard() -> None:
    store.STORE.reset()
    # Batch-A scope: an L0-only registry and an inline four-scenario suite, so
    # the smoke run stays offline and deterministic even though the authored
    # scenarios.json now carries the full 60-scenario battlefield suite.
    layers = LayerRegistry()
    for order, name in enumerate(office.LAYERS):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in tools_l0.NODES:
        tools.register(node)
    topology = TopologyBuilder(layers, tools).build()

    suite = ScenarioSuite(
        name="office_batch_a_smoke",
        version="0.1.0",
        scenarios=(
            Scenario(id="s_doc", query="read the report", expected_capabilities=("doc.parse",)),
            Scenario(id="s_fs", query="raw read the report", expected_capabilities=("fs.read",)),
            Scenario(id="s_table", query="load the table", expected_capabilities=("table.parse",)),
            Scenario(id="s_deck", query="read the deck", expected_capabilities=("ppt.parse",)),
        ),
    )

    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=office.ArtifactPresenceEvaluator(),
        trials_per_scenario=1,
    )
    outcome = asyncio.run(runner.run(suite))

    assert len(outcome.results) == 4
    for result in outcome.results:
        assert result.execution_status == TrialExecutionStatus.COMPLETED
        assert result.evaluation is not None
        assert result.evaluation.success
        assert 0.0 < result.evaluation.quality_score <= 1.0


def test_exported_topology_is_executable_json(tmp_path) -> None:
    payload = build_payload()
    assert payload["version"] == office.DEFAULT_TOPOLOGY_VERSION
    assert len(payload["layers"]) == 5
    assert len(payload["tools"]) == 53

    topology_path = tmp_path / "office.json"
    topology_path.write_text(json.dumps(payload), encoding="utf-8")
    from examples.office.export_topology import write_inner_payloads

    write_inner_payloads(tmp_path)
    loaded = TopologyLoader().load_file(str(topology_path))
    assert unbound_tool_names(loaded) == ()

    # A minimal two-scenario suite with context-layer seeds proves the JSON
    # executes end to end without touching any LLM tool (offline, fast).
    scenarios = {
        "name": "office_export_smoke",
        "version": "0.1.0",
        "scenarios": [
            {
                "id": "export_doc",
                "query": "read the report",
                "expected_capabilities": ["doc.parse"],
            },
            {
                "id": "export_table",
                "query": "load the table",
                "expected_capabilities": ["table.parse"],
            },
        ],
    }
    scen_path = tmp_path / "scenarios.json"
    scen_path.write_text(json.dumps(scenarios), encoding="utf-8")
    seeds = {
        "export_doc": {
            "layers": [{"layer": "context", "tools": ["doc_parse"]}],
            "capabilities": ["doc.parse"],
        },
        "export_table": {
            "layers": [{"layer": "context", "tools": ["table_parse"]}],
            "capabilities": ["table.parse"],
        },
    }
    seeds_path = tmp_path / "seeds.json"
    seeds_path.write_text(json.dumps(seeds), encoding="utf-8")

    code = cli_main(
        [
            "regression", "slow",
            "--topology", str(topology_path),
            "--scenario", str(scen_path),
            "--basefast", str(seeds_path),
            "--trials", "1",
        ]
    )
    assert code == 0
