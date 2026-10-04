"""Deterministic extract/verify/render tools for the office battlefield.

Office battlefield batch B (docs/acceptance/office-battlefield.md §4/§8): the
code-only slice of the domain — L1 extraction/profiling, L3 rule checks, L4
render-to-spec. Sixteen nodes, fully offline and deterministic: same input in,
same artifact out (gold-tested in tests/integration/test_office_det_tools.py).
Simulated latency lives in the tool bodies (asyncio.sleep constants) so route
latency can differentiate — never in core.

    extract  table_profile / table_header_fix_rule / text_segment /
             formula_scan / data_aggregate / chart_prepare
    verify   style_check / doc_length_check / formula_check /
             slide_overflow_check / chart_data_check / grammar_check_rule
    render   render_doc / render_xlsx / render_pptx / render_chart

Render stops at in-memory FileSpec artifacts by design (§1/§10): the spec is
verifiable but nothing is written to disk. Rule checks never throw — they
report violations as ReviewReport issues.
"""

import asyncio
import csv
import io
import json
import re

# NOTE: no `from __future__ import annotations` here — ToolExecutor resolves
# tool arguments by inspecting the real (non-string) type annotations.

from capability_runtime import tool

# Relative imports only: the office modules are always imported as the
# ``examples.office`` package, so plain-name imports can never shadow the
# slow_refund sandbox modules (facts/store) in a shared pytest process.
from .facts import (
    AggregateResult,
    ChartSpec,
    DataTable,
    Draft,
    FileSpec,
    FormulaAudit,
    FormulaSpec,
    ReviewReport,
    SlideCopy,
    SourceDoc,
    StyleSpec,
    TableProfile,
    TextSegments,
)

_REF = re.compile(r"([A-Za-z]+)([0-9]+)")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RANGE = re.compile(r"([0-9]+)\s*:\s*([0-9]+)")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


# ---- shared deterministic helpers -----------------------------------------


def _column_kinds(table: DataTable) -> tuple[str, ...]:
    """Per-column inferred kind: int / float / text / mixed."""
    kinds: list[str] = []
    for index in range(len(table.columns)):
        found: set[str] = set()
        for row in table.rows:
            cell = row[index] if index < len(row) else ""
            if not cell.strip():
                continue
            try:
                int(cell)
                found.add("int")
                continue
            except ValueError:
                pass
            try:
                float(cell)
                found.add("float")
                continue
            except ValueError:
                pass
            found.add("text")
        if found == {"int"}:
            kinds.append("int")
        elif found and found <= {"int", "float"}:
            kinds.append("float")
        elif not found or found == {"text"}:
            kinds.append("text")
        else:
            kinds.append("mixed")
    return tuple(kinds)


def _last_numeric_index(table: DataTable) -> int:
    """Index of the last numeric column; ValueError when there is none."""
    kinds = _column_kinds(table)
    for index in range(len(kinds) - 1, -1, -1):
        if kinds[index] in ("int", "float"):
            return index
    raise ValueError(f"table {table.table_id!r} has no numeric column")


def _numeric_values(table: DataTable, index: int) -> tuple[float, ...]:
    """One float per row for column ``index``; empty cells count as 0.0."""
    return tuple(
        float(row[index]) if index < len(row) and row[index].strip() else 0.0
        for row in table.rows
    )


def _report(issues: list[str]) -> ReviewReport:
    """Uniform verify outcome: pass flag, issue list, judge-style score."""
    score = 1.0 if not issues else max(0.0, 1.0 - 0.5 * len(issues))
    return ReviewReport(passed=not issues, issues=tuple(issues), score=score)


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_BREAK.split(text) if part.strip()]


# ---- extract layer ---------------------------------------------------------


@tool(
    layer="extract",
    consumes=[DataTable],
    produces=[TableProfile],
    cost_per_call=0.002,
    capabilities={"table.profile"},
    description="Profile a table: column types, null rates, anomalies",
)
async def table_profile(data_table: DataTable) -> TableProfile:
    await asyncio.sleep(0.006)
    kinds = _column_kinds(data_table)
    null_rates: list[tuple[str, float]] = []
    for index, name in enumerate(data_table.columns):
        nulls = sum(
            1
            for row in data_table.rows
            if not (row[index] if index < len(row) else "").strip()
        )
        null_rates.append((name, nulls / len(data_table.rows) if data_table.rows else 0.0))
    anomalies = [
        f"column {name!r} has mixed types"
        for name, kind in zip(data_table.columns, kinds)
        if kind == "mixed"
    ]
    duplicates = len(data_table.rows) - len(set(data_table.rows))
    if duplicates:
        anomalies.append(f"{duplicates} duplicate row(s)")
    return TableProfile(
        table_id=data_table.table_id,
        column_types=tuple(zip(data_table.columns, kinds)),
        null_rates=tuple(null_rates),
        anomalies=tuple(anomalies),
    )


@tool(
    layer="extract",
    consumes=[DataTable],
    produces=[DataTable],
    cost_per_call=0.0005,
    capabilities={"table.repair"},
    description="Rule repair: strip headers, fill empty names, drop duplicate columns",
)
async def table_header_fix_rule(data_table: DataTable) -> DataTable:
    await asyncio.sleep(0.003)
    keep: list[int] = []
    columns: list[str] = []
    seen: set[str] = set()
    for index, name in enumerate(data_table.columns):
        cleaned = name.strip() or f"col_{index}"
        if cleaned in seen:
            continue
        seen.add(cleaned)
        keep.append(index)
        columns.append(cleaned)
    rows = tuple(
        tuple(row[index] if index < len(row) else "" for index in keep)
        for row in data_table.rows
    )
    return DataTable(table_id=data_table.table_id, columns=tuple(columns), rows=rows)


@tool(
    layer="extract",
    consumes=[SourceDoc],
    produces=[TextSegments],
    cost_per_call=0.0005,
    capabilities={"text.segment"},
    description="Cut a document into non-empty chunks on blank lines",
)
async def text_segment(source_doc: SourceDoc) -> TextSegments:
    await asyncio.sleep(0.002)
    chunks = tuple(
        chunk
        for chunk in (part.strip() for part in re.split(r"\n\s*\n", source_doc.text))
        if chunk
    )
    return TextSegments(doc_id=source_doc.doc_id, chunks=chunks)


@tool(
    layer="extract",
    consumes=[DataTable],
    produces=[FormulaAudit],
    cost_per_call=0.001,
    capabilities={"formula.audit"},
    description="Audit formula-looking cells: suspicious syntax, out-of-range refs",
)
async def formula_scan(data_table: DataTable) -> FormulaAudit:
    await asyncio.sleep(0.003)
    findings: list[str] = []
    for row_no, row in enumerate(data_table.rows, start=2):
        for col_no, cell in enumerate(row, start=1):
            if not cell.startswith("="):
                continue
            body = cell[1:].strip()
            if not body or body.count("(") != body.count(")"):
                findings.append(f"r{row_no}c{col_no}: suspicious syntax {cell!r}")
                continue
            for letters, digits in _REF.findall(body):
                column = 0
                for char in letters:
                    column = column * 26 + (ord(char) - 64)
                out_of_range = (
                    int(digits) > len(data_table.rows) + 1
                    or column > len(data_table.columns)
                )
                if out_of_range:
                    findings.append(
                        f"r{row_no}c{col_no}: reference {letters}{digits} out of range"
                    )
    return FormulaAudit(table_id=data_table.table_id, findings=tuple(findings))


@tool(
    layer="extract",
    consumes=[DataTable],
    produces=[AggregateResult],
    cost_per_call=0.002,
    capabilities={"data.aggregate"},
    description="Group by the first column and sum the last numeric column",
)
async def data_aggregate(data_table: DataTable) -> AggregateResult:
    await asyncio.sleep(0.005)
    index = _last_numeric_index(data_table)
    totals: dict[str, float] = {}
    for row in data_table.rows:
        cell = row[index] if index < len(row) else ""
        if cell.strip():
            totals[row[0]] = totals.get(row[0], 0.0) + float(cell)
    return AggregateResult(
        table_id=data_table.table_id,
        metric=data_table.columns[index],
        groups=tuple(sorted(totals.items())),
    )


@tool(
    layer="extract",
    consumes=[DataTable],
    produces=[ChartSpec],
    cost_per_call=0.001,
    capabilities={"chart.prepare"},
    description="Prepare a bar chart series from the last numeric column",
)
async def chart_prepare(data_table: DataTable) -> ChartSpec:
    await asyncio.sleep(0.004)
    index = _last_numeric_index(data_table)
    return ChartSpec(
        chart_id=f"{data_table.table_id}_chart",
        kind="bar",
        title=data_table.table_id,
        labels=tuple(row[0] for row in data_table.rows),
        values=_numeric_values(data_table, index),
    )


# ---- verify layer ----------------------------------------------------------


@tool(
    layer="verify",
    consumes=[Draft, StyleSpec],
    produces=[ReviewReport],
    cost_per_call=0.001,
    capabilities={"style.check"},
    description="Rule check: sentence length against the StyleSpec contract",
)
async def style_check(draft: Draft, style_spec: StyleSpec) -> ReviewReport:
    await asyncio.sleep(0.004)
    sentences = _sentences(draft.body)
    issues = [
        f"sentence too long: {len(sentence.split())} words "
        f"(max {style_spec.max_sentence_words})"
        for sentence in sentences
        if len(sentence.split()) > style_spec.max_sentence_words
    ]
    score = 1.0 - len(issues) / len(sentences) if sentences else 1.0
    return ReviewReport(passed=not issues, issues=tuple(issues), score=score)


@tool(
    layer="verify",
    consumes=[Draft],
    produces=[ReviewReport],
    cost_per_call=0.0005,
    capabilities={"doc.length"},
    description="Rule check: body word count and section headings",
)
async def doc_length_check(draft: Draft) -> ReviewReport:
    await asyncio.sleep(0.002)
    issues: list[str] = []
    words = len(draft.body.split())
    if not 10 <= words <= 800:
        issues.append(f"body has {words} words (allowed 10..800)")
    if not any(line.startswith("## ") for line in draft.body.splitlines()):
        issues.append("no sections (missing '## ' headings)")
    return _report(issues)


@tool(
    layer="verify",
    consumes=[FormulaSpec, DataTable],
    produces=[ReviewReport],
    cost_per_call=0.001,
    capabilities={"formula.check"},
    description="Rule check: formula syntax, column references, numeric ranges",
)
async def formula_check(
    formula_spec: FormulaSpec, data_table: DataTable
) -> ReviewReport:
    await asyncio.sleep(0.004)
    issues: list[str] = []
    formula = formula_spec.formula
    if not formula.startswith("="):
        issues.append("formula must start with '='")
    body = formula[1:]
    for match in _IDENT.finditer(body):
        if body[match.end() : match.end() + 1] == "(":
            continue  # function name, not a column reference
        if match.group(0) not in data_table.columns:
            issues.append(f"unknown column reference {match.group(0)!r}")
    for start, end in _RANGE.findall(body):
        if int(end) <= int(start):
            issues.append(f"range {start}:{end} has end <= start")
    return _report(issues)


@tool(
    layer="verify",
    consumes=[SlideCopy],
    produces=[ReviewReport],
    cost_per_call=0.0005,
    capabilities={"ppt.overflow"},
    description="Rule check: per-slide bullet count and bullet length",
)
async def slide_overflow_check(slide_copy: SlideCopy) -> ReviewReport:
    await asyncio.sleep(0.003)
    issues: list[str] = []
    for title, bullets in slide_copy.slides:
        longest = max((len(bullet) for bullet in bullets), default=0)
        if len(bullets) > 5 or longest > 90:
            issues.append(
                f"slide {title!r} overflows "
                f"({len(bullets)} bullets, longest {longest} chars)"
            )
    return _report(issues)


@tool(
    layer="verify",
    consumes=[ChartSpec, DataTable],
    produces=[ReviewReport],
    cost_per_call=0.001,
    capabilities={"chart.check"},
    description="Rule check: chart series matches the source table values",
)
async def chart_data_check(
    chart_spec: ChartSpec, data_table: DataTable
) -> ReviewReport:
    await asyncio.sleep(0.004)
    issues: list[str] = []
    if len(chart_spec.labels) != len(data_table.rows):
        issues.append(
            f"label count {len(chart_spec.labels)} != table rows {len(data_table.rows)}"
        )
    try:
        index = _last_numeric_index(data_table)
    except ValueError:
        issues.append("table has no numeric column to compare against")
        return _report(issues)
    expected = _numeric_values(data_table, index)
    if len(chart_spec.values) != len(expected):
        issues.append(
            f"value count {len(chart_spec.values)} != table values {len(expected)}"
        )
    for position, (actual, want) in enumerate(zip(chart_spec.values, expected), start=1):
        if round(actual, 6) != round(want, 6):
            issues.append(f"value #{position}: chart {actual} != table {want}")
    return _report(issues)


@tool(
    layer="verify",
    consumes=[Draft],
    produces=[ReviewReport],
    cost_per_call=0.0005,
    capabilities={"text.grammar"},
    description="Rule check: double spaces, lowercase sentence starts, TODO markers",
)
async def grammar_check_rule(draft: Draft) -> ReviewReport:
    await asyncio.sleep(0.002)
    issues: list[str] = []
    if "  " in draft.body:
        issues.append("double space found")
    for sentence in _sentences(draft.body):
        first = sentence.lstrip()[:1]
        if first.isalpha() and first.islower():
            issues.append(f"sentence starts lowercase: {sentence.strip()[:30]!r}")
    if "TODO" in draft.body:
        issues.append("TODO marker found")
    return _report(issues)


# ---- render layer (in-memory FileSpec only, never written to disk) ---------


@tool(
    layer="render",
    consumes=[Draft, ReviewReport],
    produces=[FileSpec],
    cost_per_call=0.002,
    capabilities={"doc.render"},
    description="Assemble a markdown doc FileSpec with the review verdict",
)
async def render_doc(draft: Draft, review_report: ReviewReport) -> FileSpec:
    await asyncio.sleep(0.005)
    content = (
        f"# {draft.title}\n\n{draft.body}\n\n---\n"
        f"review_passed={review_report.passed} review_score={review_report.score:.2f}"
    )
    return FileSpec(
        kind="doc", name=f"{draft.title}.md", encoding="markdown", content=content
    )


@tool(
    layer="render",
    consumes=[DataTable, FormulaSpec],
    produces=[FileSpec],
    cost_per_call=0.002,
    capabilities={"table.render"},
    description="Assemble a csv sheet FileSpec with a formula footnote",
)
async def render_xlsx(
    data_table: DataTable, formula_spec: FormulaSpec
) -> FileSpec:
    await asyncio.sleep(0.005)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(data_table.columns)
    writer.writerows(data_table.rows)
    content = buffer.getvalue() + f"# formula: {formula_spec.formula}\n"
    return FileSpec(
        kind="sheet",
        name=f"{data_table.table_id}.csv",
        encoding="csv",
        content=content,
    )


@tool(
    layer="render",
    consumes=[SlideCopy],
    produces=[FileSpec],
    cost_per_call=0.001,
    capabilities={"ppt.render"},
    description="Assemble a json deck FileSpec (in memory, never written)",
)
async def render_pptx(slide_copy: SlideCopy) -> FileSpec:
    await asyncio.sleep(0.004)
    payload = {
        "deck_id": slide_copy.deck_id,
        "slides": [
            {"title": title, "bullets": list(bullets)}
            for title, bullets in slide_copy.slides
        ],
    }
    return FileSpec(
        kind="deck",
        name=f"{slide_copy.deck_id}.json",
        encoding="json",
        content=json.dumps(payload, ensure_ascii=False),
    )


@tool(
    layer="render",
    consumes=[ChartSpec],
    produces=[FileSpec],
    cost_per_call=0.001,
    capabilities={"chart.render"},
    description="Assemble a json chart FileSpec (in memory, never written)",
)
async def render_chart(chart_spec: ChartSpec) -> FileSpec:
    await asyncio.sleep(0.003)
    payload = {
        "kind": chart_spec.kind,
        "title": chart_spec.title,
        "labels": list(chart_spec.labels),
        "values": list(chart_spec.values),
    }
    return FileSpec(
        kind="chart",
        name=f"{chart_spec.chart_id}.json",
        encoding="json",
        content=json.dumps(payload, ensure_ascii=False),
    )


NODES = (
    table_profile,
    table_header_fix_rule,
    text_segment,
    formula_scan,
    data_aggregate,
    chart_prepare,
    style_check,
    doc_length_check,
    formula_check,
    slide_overflow_check,
    chart_data_check,
    grammar_check_rule,
    render_doc,
    render_xlsx,
    render_pptx,
    render_chart,
)
