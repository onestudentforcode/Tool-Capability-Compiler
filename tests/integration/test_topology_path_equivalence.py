"""Dual-path equivalence (discovery-routing batch C, P5).

The same declaration built via TopologyBuilder (Python world) and
TopologyLoader (JSON world) must behave identically: identical per-node
schemas, identical builder warnings, and — the actual divergence that
motivated this batch — identical TopologyFilter availability for every
layer x state x previous-selection combination. Before batch C the JSON
path carried empty schemas, so its filter silently kept type-infeasible
tools (office-battlefield-notes.md §4.2).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from capability_runtime import (  # noqa: E402
    TopologyFilter,
    TopologyLoader,
)
from capability_runtime.execution.state import (  # noqa: E402
    ArtifactValue,
    ExecutionState,
)
from capability_runtime.optimization.artifacts import (  # noqa: E402
    declared_fingerprint,
)

from examples.office import facts, office  # noqa: E402
from examples.office.export_topology import build_payload  # noqa: E402


def _artifact(slot: str, value) -> tuple[str, ArtifactValue]:
    return slot, ArtifactValue(value=value, source_tool="equivalence", layer="test")


def _state(*artifacts) -> ExecutionState:
    state = ExecutionState(query="equivalence")
    for slot, artifact in artifacts:
        state.add_artifact(slot, artifact)
    return state


def _states():
    """States that span the type-feasibility boundaries of the office domain."""
    source_doc = _artifact("source_doc", facts.SourceDoc(
        doc_id="d1", title="T", text="body"))
    data_table = _artifact("data_table", facts.DataTable(
        table_id="t1", columns=("a",), rows=(("1",),)))
    slide_digest = _artifact("slide_digest", facts.SlideDigest(
        deck_id="d1", slides=(("S", ("b",)),)))
    fact_sheet = _artifact("fact_sheet", facts.FactSheet(
        doc_id="d1", facts=("f",)))
    style_spec = _artifact("style_spec", facts.StyleSpec(
        tone="formal", max_sentence_words=20))
    aggregate = _artifact("aggregate_result", facts.AggregateResult(
        table_id="t1", metric="revenue", groups=(("north", 1.0),)))
    draft = _artifact("draft", facts.Draft(title="T", body="B"))
    narrative = _artifact("narrative", facts.Narrative(text="N"))
    slide_copy = _artifact("slide_copy", facts.SlideCopy(
        deck_id="d1", slides=(("S", ("b",)),)))
    formula = _artifact("formula_spec", facts.FormulaSpec(
        column="revenue", formula="=SUM(A1)", explanation="x"))
    chart = _artifact("chart_spec", facts.ChartSpec(
        chart_id="c1", kind="bar", title="T", labels=("a",), values=(1.0,)))
    email = _artifact("email_draft", facts.EmailDraft(
        to="x@example.com", subject="S", body="B"))
    review = _artifact("review_report", facts.ReviewReport(
        passed=True, issues=(), score=0.8))
    file_spec = _artifact("file_spec", facts.FileSpec(
        kind="doc", name="n", encoding="markdown", content="c"))
    outline = _artifact("outline", facts.Outline(
        title="T", sections=("s",)))
    everything = _state(
        source_doc, data_table, slide_digest, fact_sheet, style_spec,
        aggregate, draft, narrative, slide_copy, formula, chart, email,
        review, file_spec, outline,
    )
    return [
        everything,
        _state(),
        _state(source_doc),
        _state(source_doc, data_table),
        _state(source_doc, data_table, slide_digest),
        _state(source_doc, fact_sheet, style_spec),
        _state(aggregate, formula, data_table),
        _state(draft, narrative, review, file_spec),
        _state(slide_copy, review, file_spec),
        _state(aggregate, draft),
        everything,
    ]


def _previous_selections(topology):
    """Realistic per-layer selections, plus a couple of cross-layer ones."""
    names = list(topology.nodes())
    by_layer = {
        layer.name: tuple(sorted(
            n for n in names if topology.node(n).spec.layer == layer.name
        ))
        for layer in topology.layers()
    }
    selections = [()]
    for tools in by_layer.values():
        if tools:
            selections.append(tools[:2])
    selections.append(("doc_parse", "table_parse"))
    selections.append(("keyfact_extract", "data_aggregate"))
    selections.append(("draft_section_steady", "quality_judge"))
    return selections


def test_dual_path_schemas_match_per_node() -> None:
    python_topology, _ = office.build_topology()
    json_topology = TopologyLoader().load_data(build_payload())

    assert set(python_topology.nodes()) == set(json_topology.nodes())
    for name in sorted(python_topology.nodes()):
        py_spec = python_topology.node(name).spec
        json_spec = json_topology.node(name).spec
        assert py_spec.consumes == json_spec.consumes, name
        assert py_spec.produces == json_spec.produces, name


def test_dual_path_filter_availability_identical() -> None:
    python_topology, _ = office.build_topology()
    json_topology = TopologyLoader().load_data(build_payload())
    py_filter = TopologyFilter(python_topology)
    json_filter = TopologyFilter(json_topology)

    states = _states()
    for layer in office.LAYERS:
        for previous in _previous_selections(python_topology):
            for state in states:
                py_available = py_filter.available_tools(
                    layer, tuple(previous), state
                )
                json_available = json_filter.available_tools(
                    layer, tuple(previous), state
                )
                assert py_available == json_available, (
                    layer, previous, sorted(state.names())
                )


def test_dual_path_warnings_and_fingerprint_identical() -> None:
    python_topology, _ = office.build_topology()
    json_topology = TopologyLoader().load_data(build_payload())

    py_warnings = sorted(
        (w.source, w.target, w.code) for w in python_topology.warnings()
    )
    json_warnings = sorted(
        (w.source, w.target, w.code) for w in json_topology.warnings()
    )
    assert py_warnings == json_warnings
    # types are deliberately excluded from the fingerprint (batch C §C.3)
    assert declared_fingerprint(python_topology) == declared_fingerprint(json_topology)
