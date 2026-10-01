"""Office battlefield batch B: deterministic extract/verify/render tools.

Acceptance (docs/acceptance/office-battlefield.md §9 batch B): every
deterministic tool carries gold assertions (same input -> same output) and
the six rule checks produce both pass and fail conclusions. Fully offline.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from capability_runtime.resources.metering import (  # noqa: E402
    mount_collector,
    unmount_collector,
)

from examples.office import facts  # noqa: E402
from examples.office import tools_det  # noqa: E402


async def call_tool(node, *args):
    """Invoke a tool handler the way ToolExecutor does: with a metering
    context mounted, so store reads would be counted, not rejected."""
    token = mount_collector()
    try:
        return await node.handler(*args)
    finally:
        unmount_collector(token)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def sales_table() -> facts.DataTable:
    return facts.DataTable(
        table_id="sales",
        columns=("region", "units", "revenue"),
        rows=(
            ("north", "120", "9600.0"),
            ("south", "90", "7200.0"),
        ),
    )


# ---- structural inventory --------------------------------------------------


def test_sixteen_nodes_declared_in_layer_order() -> None:
    nodes = tools_det.NODES
    assert len(nodes) == 16
    assert [node.spec.layer for node in nodes] == (
        ["extract"] * 6 + ["verify"] * 6 + ["render"] * 4
    )
    assert len({node.spec.name for node in nodes}) == 16
    for node in nodes:
        assert 0.0005 <= node.spec.cost_per_call <= 0.002, node.spec.name
        assert node.spec.description, node.spec.name
        for kind in (*node.spec.consumes, *node.spec.produces):
            assert kind.__module__ == facts.__name__, node.spec.name
        params = [
            parameter
            for parameter in inspect.signature(node.handler).parameters.values()
            if parameter.kind
            in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        assert [param.name for param in params] == [
            _snake(kind.__name__) for kind in node.spec.consumes
        ]
        assert [param.annotation for param in params] == list(node.spec.consumes)


# ---- extract layer ---------------------------------------------------------


def test_table_profile_gold() -> None:
    table = facts.DataTable(
        table_id="t1",
        columns=("name", "qty", "note"),
        rows=(
            ("a", "1", "x"),
            ("a", "1", "x"),
            ("b", "2.5", ""),
            ("c", "oops", "y"),
        ),
    )

    async def scenario() -> None:
        profile = await call_tool(tools_det.table_profile, table)
        assert isinstance(profile, facts.TableProfile)
        assert profile.table_id == "t1"
        assert profile.column_types == (
            ("name", "text"),
            ("qty", "mixed"),
            ("note", "text"),
        )
        assert profile.null_rates == (("name", 0.0), ("qty", 0.0), ("note", 0.25))
        assert profile.anomalies == (
            "column 'qty' has mixed types",
            "1 duplicate row(s)",
        )

    asyncio.run(scenario())


def test_table_header_fix_rule_gold() -> None:
    dirty = facts.DataTable(
        table_id="t2",
        columns=(" Region ", "", "units", "units"),
        rows=(("north", "1", "2", "3"),),
    )

    async def scenario() -> None:
        fixed = await call_tool(tools_det.table_header_fix_rule, dirty)
        assert isinstance(fixed, facts.DataTable)
        assert fixed.table_id == "t2"
        assert fixed.columns == ("Region", "col_1", "units")
        assert fixed.rows == (("north", "1", "2"),)

    asyncio.run(scenario())


def test_text_segment_gold() -> None:
    doc = facts.SourceDoc(
        doc_id="d1",
        title="T",
        text="First para.\n\nSecond para here.\n\n \nThird para.",
    )

    async def scenario() -> None:
        segments = await call_tool(tools_det.text_segment, doc)
        assert segments == facts.TextSegments(
            doc_id="d1",
            chunks=("First para.", "Second para here.", "Third para."),
        )

    asyncio.run(scenario())


def test_formula_scan_gold() -> None:
    dirty = facts.DataTable(
        table_id="t3",
        columns=("a", "b"),
        rows=(
            ("=A1+B1", "5"),
            ("=SUM(A1:A2", "6"),
            ("=", "7"),
            ("=Z9", "8"),
        ),
    )

    async def scenario() -> None:
        audit = await call_tool(tools_det.formula_scan, dirty)
        assert isinstance(audit, facts.FormulaAudit)
        assert audit.table_id == "t3"
        assert len(audit.findings) == 3
        assert audit.findings[0] == "r3c1: suspicious syntax '=SUM(A1:A2'"
        assert audit.findings[1] == "r4c1: suspicious syntax '='"
        assert audit.findings[2] == "r5c1: reference Z9 out of range"

        clean_audit = await call_tool(tools_det.formula_scan, sales_table())
        assert clean_audit.findings == ()

    asyncio.run(scenario())


def test_data_aggregate_gold() -> None:
    table = facts.DataTable(
        table_id="t4",
        columns=("region", "month", "revenue"),
        rows=(
            ("north", "m1", "10.5"),
            ("south", "m1", "2.0"),
            ("north", "m2", "4.5"),
            ("south", "m2", ""),
        ),
    )

    async def scenario() -> None:
        result = await call_tool(tools_det.data_aggregate, table)
        assert isinstance(result, facts.AggregateResult)
        assert result.table_id == "t4"
        assert result.metric == "revenue"
        assert result.groups == (("north", 15.0), ("south", 2.0))

    asyncio.run(scenario())


def test_data_aggregate_raises_on_text_only_table() -> None:
    table = facts.DataTable(table_id="t5", columns=("a", "b"), rows=(("x", "y"),))

    async def scenario() -> None:
        with pytest.raises(ValueError, match="no numeric column"):
            await call_tool(tools_det.data_aggregate, table)

    asyncio.run(scenario())


def test_chart_prepare_gold() -> None:
    async def scenario() -> None:
        spec = await call_tool(tools_det.chart_prepare, sales_table())
        assert isinstance(spec, facts.ChartSpec)
        assert spec == facts.ChartSpec(
            chart_id="sales_chart",
            kind="bar",
            title="sales",
            labels=("north", "south"),
            values=(9600.0, 7200.0),
        )

    asyncio.run(scenario())


# ---- verify layer ----------------------------------------------------------


def test_style_check_pass_and_fail() -> None:
    style = facts.StyleSpec(tone="plain", max_sentence_words=4)

    async def scenario() -> None:
        good = facts.Draft(title="T", body="Tiny one. Two words now. Three fine.")
        report = await call_tool(tools_det.style_check, good, style)
        assert isinstance(report, facts.ReviewReport)
        assert report.passed and report.issues == () and report.score == 1.0

        bad = facts.Draft(title="T", body="One two three four five six. Short ok.")
        report = await call_tool(tools_det.style_check, bad, style)
        assert not report.passed
        assert len(report.issues) == 1
        assert "6 words" in report.issues[0]
        assert report.score == 0.5

    asyncio.run(scenario())


def test_doc_length_check_pass_and_fail() -> None:
    async def scenario() -> None:
        good = facts.Draft(title="T", body="## Overview\n" + ("lorem " * 12).strip())
        report = await call_tool(tools_det.doc_length_check, good)
        assert report.passed and report.issues == () and report.score == 1.0

        bad = facts.Draft(title="T", body="too short")
        report = await call_tool(tools_det.doc_length_check, bad)
        assert not report.passed
        assert len(report.issues) == 2
        assert report.score == 0.0

    asyncio.run(scenario())


def test_formula_check_pass_and_fail() -> None:
    table = facts.DataTable(
        table_id="t", columns=("units", "revenue"), rows=(("1", "2"),)
    )

    async def scenario() -> None:
        good = facts.FormulaSpec(
            column="total", formula="=SUM(units) + revenue", explanation=""
        )
        report = await call_tool(tools_det.formula_check, good, table)
        assert report.passed and report.issues == () and report.score == 1.0

        bad = facts.FormulaSpec(
            column="total", formula="bogus(units) - tax", explanation=""
        )
        report = await call_tool(tools_det.formula_check, bad, table)
        assert not report.passed
        assert len(report.issues) == 2  # missing '=' and unknown column 'tax'
        assert report.score == 0.0

        bad_range = facts.FormulaSpec(
            column="total", formula="=SUM(3:1) + revenue", explanation=""
        )
        report = await call_tool(tools_det.formula_check, bad_range, table)
        assert not report.passed
        assert len(report.issues) == 1
        assert "range 3:1" in report.issues[0]

    asyncio.run(scenario())


def test_slide_overflow_check_pass_and_fail() -> None:
    async def scenario() -> None:
        good = facts.SlideCopy(
            deck_id="d", slides=(("S1", ("bullet one", "bullet two")),)
        )
        report = await call_tool(tools_det.slide_overflow_check, good)
        assert report.passed and report.issues == () and report.score == 1.0

        bad = facts.SlideCopy(
            deck_id="d",
            slides=(
                ("Dense", tuple(f"b{i}" for i in range(6))),
                ("Long", ("y" * 91,)),
            ),
        )
        report = await call_tool(tools_det.slide_overflow_check, bad)
        assert not report.passed
        assert len(report.issues) == 2
        assert report.score == 0.0

    asyncio.run(scenario())


def test_chart_data_check_pass_and_fail() -> None:
    table = facts.DataTable(
        table_id="t", columns=("k", "v"), rows=(("a", "1.5"), ("b", "2.5"))
    )

    async def scenario() -> None:
        good = facts.ChartSpec(
            chart_id="c", kind="bar", title="t", labels=("a", "b"), values=(1.5, 2.5)
        )
        report = await call_tool(tools_det.chart_data_check, good, table)
        assert report.passed and report.issues == () and report.score == 1.0

        # round(…, 6) equality: sub-microscopic drift is not a mismatch.
        near = facts.ChartSpec(
            chart_id="c",
            kind="bar",
            title="t",
            labels=("a", "b"),
            values=(1.5000004, 2.4999999),
        )
        report = await call_tool(tools_det.chart_data_check, near, table)
        assert report.passed and report.issues == () and report.score == 1.0

        bad = facts.ChartSpec(
            chart_id="c", kind="bar", title="t", labels=("a",), values=(1.5, 9.9)
        )
        report = await call_tool(tools_det.chart_data_check, bad, table)
        assert not report.passed
        assert len(report.issues) == 2  # label count + one mismatched value
        assert report.score == 0.0

    asyncio.run(scenario())


def test_grammar_check_pass_and_fail() -> None:
    async def scenario() -> None:
        good = facts.Draft(title="T", body="Clean text. No issues here.")
        report = await call_tool(tools_det.grammar_check_rule, good)
        assert report.passed and report.issues == () and report.score == 1.0

        bad = facts.Draft(title="T", body="Double  space. lower start. TODO fix")
        report = await call_tool(tools_det.grammar_check_rule, bad)
        assert not report.passed
        assert len(report.issues) == 3
        assert report.score == 0.0

    asyncio.run(scenario())


# ---- render layer ----------------------------------------------------------


def test_render_doc_gold() -> None:
    draft = facts.Draft(title="Weekly", body="All systems nominal.")
    report = facts.ReviewReport(passed=True, issues=(), score=0.9)

    async def scenario() -> None:
        spec = await call_tool(tools_det.render_doc, draft, report)
        assert isinstance(spec, facts.FileSpec)
        assert spec.kind == "doc"
        assert spec.encoding == "markdown"
        assert spec.name == "Weekly.md"
        assert spec.content == (
            "# Weekly\n\nAll systems nominal.\n\n---\n"
            "review_passed=True review_score=0.90"
        )

    asyncio.run(scenario())


def test_render_xlsx_gold() -> None:
    table = facts.DataTable(
        table_id="sales",
        columns=("region", "units"),
        rows=(("north", "120"), ("south", "90")),
    )
    formula = facts.FormulaSpec(column="units", formula="=SUM(units)", explanation="")

    async def scenario() -> None:
        spec = await call_tool(tools_det.render_xlsx, table, formula)
        assert spec.kind == "sheet"
        assert spec.encoding == "csv"
        assert spec.name == "sales.csv"
        assert spec.content == (
            "region,units\nnorth,120\nsouth,90\n# formula: =SUM(units)\n"
        )

    asyncio.run(scenario())


def test_render_pptx_gold() -> None:
    copy = facts.SlideCopy(deck_id="q3", slides=(("Title", ("a", "b")), ("Next", ())))

    async def scenario() -> None:
        spec = await call_tool(tools_det.render_pptx, copy)
        assert spec.kind == "deck"
        assert spec.encoding == "json"
        assert spec.name == "q3.json"
        assert json.loads(spec.content) == {
            "deck_id": "q3",
            "slides": [
                {"title": "Title", "bullets": ["a", "b"]},
                {"title": "Next", "bullets": []},
            ],
        }

    asyncio.run(scenario())


def test_render_chart_gold() -> None:
    chart = facts.ChartSpec(
        chart_id="sales_chart",
        kind="bar",
        title="Sales",
        labels=("a", "b"),
        values=(1.0, 2.0),
    )

    async def scenario() -> None:
        spec = await call_tool(tools_det.render_chart, chart)
        assert spec.kind == "chart"
        assert spec.encoding == "json"
        assert spec.name == "sales_chart.json"
        assert json.loads(spec.content) == {
            "kind": "bar",
            "title": "Sales",
            "labels": ["a", "b"],
            "values": [1.0, 2.0],
        }

    asyncio.run(scenario())


# ---- determinism -----------------------------------------------------------


def _determinism_cases():
    table = sales_table()
    dirty = facts.DataTable(
        table_id="t", columns=(" A ", "", "a"), rows=(("1", "2", "3"),)
    )
    doc = facts.SourceDoc(doc_id="d", title="D", text="Para one.\n\nPara two.")
    audit_table = facts.DataTable(
        table_id="t", columns=("a", "b"), rows=(("=SUM(A1:A2)", "x"),)
    )
    grouped = facts.DataTable(
        table_id="t", columns=("g", "v"), rows=(("a", "1"), ("a", "2"))
    )
    draft = facts.Draft(title="T", body="## S\nSome words in a body here.")
    style = facts.StyleSpec(tone="plain", max_sentence_words=10)
    formula = facts.FormulaSpec(column="v", formula="=SUM(v)", explanation="")
    copy = facts.SlideCopy(deck_id="d", slides=(("S", ("b",)),))
    chart = facts.ChartSpec(
        chart_id="c", kind="bar", title="T", labels=("a", "b"), values=(1.0, 2.0)
    )
    report = facts.ReviewReport(passed=True, issues=(), score=0.9)
    return [
        (tools_det.table_profile, (table,)),
        (tools_det.table_header_fix_rule, (dirty,)),
        (tools_det.text_segment, (doc,)),
        (tools_det.formula_scan, (audit_table,)),
        (tools_det.data_aggregate, (grouped,)),
        (tools_det.chart_prepare, (table,)),
        (tools_det.style_check, (draft, style)),
        (tools_det.doc_length_check, (draft,)),
        (tools_det.formula_check, (formula, table)),
        (tools_det.slide_overflow_check, (copy,)),
        (tools_det.chart_data_check, (chart, table)),
        (tools_det.grammar_check_rule, (draft,)),
        (tools_det.render_doc, (draft, report)),
        (tools_det.render_xlsx, (table, formula)),
        (tools_det.render_pptx, (copy,)),
        (tools_det.render_chart, (chart,)),
    ]


def test_same_input_gives_same_output() -> None:
    async def scenario() -> None:
        for node, args in _determinism_cases():
            first = await call_tool(node, *args)
            second = await call_tool(node, *args)
            assert first == second, node.spec.name

    asyncio.run(scenario())
