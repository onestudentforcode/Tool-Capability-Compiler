"""LLM tool layer for the office battlefield (batch C): 31 nodes.

Extract (7 LLM) / compose (21, the LLM heart incl. two rule tools) / verify
(3 LLM judges) — office-battlefield.md §4. Every LLM tool is "prompt template
+ lenient parse + office_llm.complete_json": metering (tokens / measured cost)
settles inside LLMResource at the tool-call boundary, tool bodies carry zero
reporting code, and a malformed LLM payload raises ValueError which the
executor classifies as TOOL_EXECUTION_ERROR.

Redundant variants are factory-built (see ``factories.py``): the variant
tables below are the single source of truth for each family's configuration
(name / latency / cost / style / prompt hint), and the unpacked module
attributes feed ``export_topology.py``, which binds by attribute name —
so every attribute name here must equal its spec.name exactly.

NOTE: no `from __future__ import annotations` here — ToolExecutor resolves
tool arguments by inspecting the real (non-string) type annotations.
"""

import asyncio
from dataclasses import replace

from capability_runtime import tool

from . import office_llm
from .factories import (
    as_str,
    as_str_tuple,
    make_draft_section,
    make_formula_gen,
    make_outline_doc_gen,
    make_slide_copy,
    make_text_summarize,
    make_text_translate,
    review_from_json,
    slides_from_json,
    style_system,
)
from .facts import (
    AggregateResult,
    ChartSpec,
    DataTable,
    Draft,
    EmailDraft,
    FactSheet,
    Narrative,
    ReviewReport,
    SlideOutline,
    SourceDoc,
    StyleSpec,
)

# ---- extract layer (7, all LLM) --------------------------------------------


@tool(
    layer="extract",
    consumes=[SourceDoc],
    produces=[FactSheet],
    cost_per_call=0.003,
    capabilities={"fact.extract"},
    description="LLM key-fact extraction (the fact-verify anchor)",
)
async def keyfact_extract(source_doc: SourceDoc) -> FactSheet:
    await asyncio.sleep(0.008)
    shape = 'Reply only with JSON: {"facts": [str]}.'
    prompt = f"List the key facts stated in this document. {shape}\n\n{source_doc.text}"
    payload = await office_llm.complete_json(prompt)
    return FactSheet(doc_id=source_doc.doc_id, facts=as_str_tuple(payload.get("facts")))


@tool(
    layer="extract",
    consumes=[SourceDoc],
    produces=[StyleSpec],
    cost_per_call=0.004,
    capabilities={"style.profile"},
    description="LLM writing-style profile of the source document",
)
async def style_profile(source_doc: SourceDoc) -> StyleSpec:
    await asyncio.sleep(0.01)
    shape = 'Reply only with JSON: {"tone": str, "max_sentence_words": int}.'
    prompt = f"Profile the writing style of this document. {shape}\n\n{source_doc.text}"
    payload = await office_llm.complete_json(prompt)
    return StyleSpec(
        tone=as_str(payload.get("tone"), "neutral"),
        max_sentence_words=int(payload.get("max_sentence_words", 25)),
    )


@tool(
    layer="extract",
    consumes=[DataTable],
    produces=[DataTable],
    cost_per_call=0.006,
    capabilities={"table.repair"},
    description="LLM header repair (redundant with the batch-B rule variant)",
)
async def table_header_fix_llm(data_table: DataTable) -> DataTable:
    await asyncio.sleep(0.012)
    shape = 'Reply only with JSON: {"columns": [str]}.'
    prompt = (
        "Repair the corrupted header of this table (current columns: "
        f"{list(data_table.columns)}). {shape}"
    )
    payload = await office_llm.complete_json(prompt)
    return DataTable(
        table_id=data_table.table_id,
        columns=as_str_tuple(payload.get("columns")),
        rows=data_table.rows,
    )


# ---- compose layer (21) -----------------------------------------------------


def _email_from(payload: dict) -> EmailDraft:
    return EmailDraft(
        to=as_str(payload.get("to"), "colleague@example.com"),
        subject=as_str(payload.get("subject"), ""),
        body=as_str(payload.get("body"), ""),
    )


async def _polish(narrative: Narrative, style_spec: StyleSpec, system: str) -> Draft:
    shape = 'Reply only with JSON: {"title": str, "body": str}.'
    prompt = (
        f"Turn this summary into polished prose: tone {style_spec.tone}, "
        f"at most {style_spec.max_sentence_words} words per sentence. {shape}\n\n"
        f"{narrative.text}"
    )
    payload = await office_llm.complete_json(prompt, system=system)
    return Draft(
        title=as_str(payload.get("title"), "polished"),
        body=as_str(payload.get("body"), narrative.text),
    )


def _monotonic(values: tuple[float, ...]) -> bool:
    return all(a <= b for a, b in zip(values, values[1:])) or all(
        a >= b for a, b in zip(values, values[1:])
    )


_TONE_PALETTES = {
    "formal": "navy-gray",
    "casual": "warm-amber",
    "technical": "steel-blue",
    "friendly": "sage-green",
}


@tool(
    layer="compose",
    consumes=[SourceDoc],
    produces=[SlideOutline],
    cost_per_call=0.005,
    capabilities={"outline.slides"},
    description="LLM slide outline from the source document",
)
async def outline_slide_gen(source_doc: SourceDoc) -> SlideOutline:
    await asyncio.sleep(0.012)
    shape = 'Reply only with JSON: {"slides": [{"title": str, "points": [str]}]}.'
    prompt = (
        f'Turn this document into a slide deck outline. {shape}\n\n'
        f"{source_doc.title}\n{source_doc.text}"
    )
    payload = await office_llm.complete_json(prompt)
    return SlideOutline(
        deck_id=source_doc.doc_id, slides=slides_from_json(payload.get("slides"))
    )


@tool(
    layer="compose",
    consumes=[Narrative],
    produces=[EmailDraft],
    cost_per_call=0.002,
    capabilities={"mail.draft"},
    description="LLM concise mail draft (never sent — §10)",
)
async def draft_email_concise(narrative: Narrative) -> EmailDraft:
    await asyncio.sleep(0.004)
    shape = 'Reply only with JSON: {"to": str, "subject": str, "body": str}.'
    prompt = f"Draft a concise reply email about: {narrative.text} {shape}"
    payload = await office_llm.complete_json(prompt, system=style_system("concise"))
    return _email_from(payload)


@tool(
    layer="compose",
    consumes=[Narrative],
    produces=[EmailDraft],
    cost_per_call=0.008,
    capabilities={"mail.draft"},
    description="LLM detailed mail draft (never sent — §10)",
)
async def draft_email_detailed(narrative: Narrative) -> EmailDraft:
    await asyncio.sleep(0.02)
    shape = 'Reply only with JSON: {"to": str, "subject": str, "body": str}.'
    prompt = f"Draft a detailed reply email about: {narrative.text} {shape}"
    payload = await office_llm.complete_json(prompt, system=style_system("detailed"))
    return _email_from(payload)


@tool(
    layer="compose",
    consumes=[SourceDoc],
    produces=[Narrative],
    cost_per_call=0.004,
    capabilities={"notes.draft"},
    description="LLM speaker notes for a deck based on the source document",
)
async def draft_speaker_notes(source_doc: SourceDoc) -> Narrative:
    await asyncio.sleep(0.01)
    shape = 'Reply only with JSON: {"text": str}.'
    prompt = f"Write speaker notes presenting this document. {shape}\n\n{source_doc.text}"
    payload = await office_llm.complete_json(prompt)
    return Narrative(text=as_str(payload.get("text"), ""))


@tool(
    layer="compose",
    consumes=[Narrative, StyleSpec],
    produces=[Draft],
    cost_per_call=0.004,
    capabilities={"text.polish"},
    description="LLM conservative polish (keeps wording, fixes clear issues)",
)
async def text_polish_conservative(
    narrative: Narrative, style_spec: StyleSpec
) -> Draft:
    await asyncio.sleep(0.005)
    return await _polish(narrative, style_spec, style_system("conservative"))


@tool(
    layer="compose",
    consumes=[Narrative, StyleSpec],
    produces=[Draft],
    cost_per_call=0.006,
    capabilities={"text.polish"},
    description="LLM aggressive polish (rewrites freely for impact)",
)
async def text_polish_aggressive(narrative: Narrative, style_spec: StyleSpec) -> Draft:
    await asyncio.sleep(0.012)
    return await _polish(narrative, style_spec, style_system("aggressive"))


@tool(
    layer="compose",
    consumes=[FactSheet],
    produces=[Draft],
    cost_per_call=0.005,
    capabilities={"text.expand"},
    description="LLM expansion of key facts into full sections",
)
async def text_expand(fact_sheet: FactSheet) -> Draft:
    await asyncio.sleep(0.012)
    shape = 'Reply only with JSON: {"title": str, "body": str}.'
    prompt = (
        f"Expand these key points into full sections: "
        f"{', '.join(fact_sheet.facts)}. {shape}"
    )
    payload = await office_llm.complete_json(prompt)
    return Draft(
        title=as_str(payload.get("title"), "expanded"),
        body=as_str(payload.get("body"), ""),
    )


@tool(
    layer="compose",
    consumes=[SourceDoc],
    produces=[Narrative],
    cost_per_call=0.0008,
    capabilities={"title.compose"},
    description="Cheap LLM title generator (high-frequency FAST-tier traffic)",
)
async def title_gen(source_doc: SourceDoc) -> Narrative:
    await asyncio.sleep(0.003)
    shape = 'Reply only with JSON: {"text": str}.'
    prompt = f"Propose one title for this document. {shape}\n\n{source_doc.text[:400]}"
    payload = await office_llm.complete_json(prompt)
    return Narrative(text=as_str(payload.get("text"), source_doc.title))


@tool(
    layer="compose",
    consumes=[ChartSpec],
    produces=[ChartSpec],
    cost_per_call=0.0005,
    capabilities={"chart.select"},
    description="Rule-based chart kind pick (redundant with the LLM variant)",
)
async def chart_type_pick_rule(chart_spec: ChartSpec) -> ChartSpec:
    await asyncio.sleep(0.002)
    if len(chart_spec.labels) <= 3:
        kind = "pie"
    elif _monotonic(chart_spec.values):
        kind = "line"
    else:
        kind = "bar"
    return replace(chart_spec, kind=kind)


@tool(
    layer="compose",
    consumes=[ChartSpec],
    produces=[ChartSpec],
    cost_per_call=0.004,
    capabilities={"chart.select"},
    description="LLM chart kind pick (redundant with the rule variant)",
)
async def chart_type_pick_llm(chart_spec: ChartSpec) -> ChartSpec:
    await asyncio.sleep(0.01)
    shape = 'Reply only with JSON: {"kind": str}.'
    prompt = (
        "Pick a chart kind (pie, line or bar) for series "
        f"{list(chart_spec.labels)} with values {list(chart_spec.values)}. {shape}"
    )
    payload = await office_llm.complete_json(prompt)
    return replace(chart_spec, kind=as_str(payload.get("kind"), "bar"))


@tool(
    layer="compose",
    consumes=[StyleSpec],
    produces=[StyleSpec],
    cost_per_call=0.0005,
    capabilities={"palette.select"},
    description="Deterministic tone-to-palette mapping",
)
async def theme_palette_pick(style_spec: StyleSpec) -> StyleSpec:
    await asyncio.sleep(0.002)
    return replace(
        style_spec, palette=_TONE_PALETTES.get(style_spec.tone.lower(), "neutral-gray")
    )


@tool(
    layer="compose",
    consumes=[AggregateResult],
    produces=[Narrative],
    cost_per_call=0.003,
    capabilities={"insight.narrate"},
    description="LLM narration of a grouped aggregate",
)
async def insight_narrate(aggregate_result: AggregateResult) -> Narrative:
    await asyncio.sleep(0.008)
    groups = ", ".join(f"{name}={value:g}" for name, value in aggregate_result.groups)
    shape = 'Reply only with JSON: {"text": str}.'
    prompt = (
        f"Narrate one insight from metric {aggregate_result.metric} over groups: "
        f"{groups}. {shape}"
    )
    payload = await office_llm.complete_json(prompt)
    return Narrative(text=as_str(payload.get("text"), ""))


# ---- verify layer (3, LLM judges) -------------------------------------------


@tool(
    layer="verify",
    consumes=[Draft, FactSheet],
    produces=[ReviewReport],
    cost_per_call=0.005,
    capabilities={"fact.verify"},
    description="LLM judge: draft consistency against the fact sheet",
)
async def fact_check_judge(draft: Draft, fact_sheet: FactSheet) -> ReviewReport:
    await asyncio.sleep(0.012)
    shape = 'Reply only with JSON: {"passed": bool, "issues": [str], "score": float}.'
    prompt = (
        f"Check the draft against the facts and report every violation. {shape}\n\n"
        f"Draft: {draft.title} — {draft.body}\nFacts: {'; '.join(fact_sheet.facts)}"
    )
    return review_from_json(await office_llm.complete_json(prompt))


@tool(
    layer="verify",
    consumes=[Draft],
    produces=[ReviewReport],
    cost_per_call=0.004,
    capabilities={"quality.judge"},
    description="LLM judge: overall draft quality score",
)
async def quality_judge(draft: Draft) -> ReviewReport:
    await asyncio.sleep(0.01)
    shape = 'Reply only with JSON: {"passed": bool, "issues": [str], "score": float}.'
    prompt = f"Judge the overall quality of this draft. {shape}\n\n{draft.title}\n{draft.body}"
    return review_from_json(await office_llm.complete_json(prompt))


@tool(
    layer="verify",
    consumes=[Draft],
    produces=[ReviewReport],
    cost_per_call=0.005,
    capabilities={"text.grammar"},
    description="LLM grammar check (redundant with the batch-B rule variant)",
)
async def grammar_check_llm(draft: Draft) -> ReviewReport:
    await asyncio.sleep(0.01)
    shape = 'Reply only with JSON: {"passed": bool, "issues": [str], "score": float}.'
    prompt = f"Check this draft for grammar issues. {shape}\n\n{draft.title}\n{draft.body}"
    return review_from_json(await office_llm.complete_json(prompt))


# ---- factory-built redundant variants ---------------------------------------
#
# One implementation per family, N configured ToolNodes (office-battlefield.md
# §2); these tables are the single source of truth for each variant's config.

SUMMARIZE_VARIANTS = (
    {
        "name": "text_summarize_fast",
        "latency": 0.006,
        "cost": 0.001,
        "style": "fast",
        "prompt_hint": "Keep it to one sentence.",
    },
    {
        "name": "text_summarize_steady",
        "latency": 0.025,
        "cost": 0.01,
        "style": "steady",
        "prompt_hint": "Cover every section in detail.",
    },
)
text_summarize_fast, text_summarize_steady = (
    make_text_summarize(**config) for config in SUMMARIZE_VARIANTS
)

TRANSLATE_VARIANTS = (
    {
        "name": "text_translate_fast",
        "latency": 0.006,
        "cost": 0.001,
        "style": "fast",
        "prompt_hint": "Translate quickly, one pass.",
        "flaky": True,
    },
    {
        "name": "text_translate_steady",
        "latency": 0.02,
        "cost": 0.01,
        "style": "steady",
        "prompt_hint": "Translate carefully, preserving nuance.",
    },
)
text_translate_fast, text_translate_steady = (
    make_text_translate(**config) for config in TRANSLATE_VARIANTS
)

OUTLINE_DOC_VARIANTS = (
    {
        "name": "outline_doc_gen_fast",
        "latency": 0.006,
        "cost": 0.001,
        "style": "fast",
        "prompt_hint": "Three headings maximum.",
    },
    {
        "name": "outline_doc_gen_steady",
        "latency": 0.025,
        "cost": 0.01,
        "style": "steady",
        "prompt_hint": "Plan every section carefully.",
    },
)
outline_doc_gen_fast, outline_doc_gen_steady = (
    make_outline_doc_gen(**config) for config in OUTLINE_DOC_VARIANTS
)

DRAFT_SECTION_VARIANTS = (
    {
        "name": "draft_section_fast",
        "latency": 0.006,
        "cost": 0.001,
        "style": "fast",
        "prompt_hint": "One short paragraph.",
    },
    {
        "name": "draft_section_steady",
        "latency": 0.025,
        "cost": 0.01,
        "style": "steady",
        "prompt_hint": "A complete, careful section.",
    },
    {
        "name": "draft_section_verbose",
        "latency": 0.015,
        "cost": 0.02,
        "style": "verbose",
        "prompt_hint": "Write as much as possible.",
    },
)
draft_section_fast, draft_section_steady, draft_section_verbose = (
    make_draft_section(**config) for config in DRAFT_SECTION_VARIANTS
)

FORMULA_GEN_VARIANTS = (
    {
        "name": "formula_gen_fast",
        "latency": 0.005,
        "cost": 0.001,
        "style": "fast",
        "prompt_hint": "Best guess immediately.",
    },
    {
        "name": "formula_gen_steady",
        "latency": 0.02,
        "cost": 0.01,
        "style": "steady",
        "prompt_hint": "Explain the formula fully.",
    },
)
formula_gen_fast, formula_gen_steady = (
    make_formula_gen(**config) for config in FORMULA_GEN_VARIANTS
)

SLIDE_COPY_VARIANTS = (
    {
        "name": "slide_copy_fast",
        "latency": 0.006,
        "cost": 0.001,
        "style": "fast",
        "prompt_hint": "Three words per bullet.",
    },
    {
        "name": "slide_copy_steady",
        "latency": 0.02,
        "cost": 0.01,
        "style": "steady",
        "prompt_hint": "Full speaker-ready copy.",
    },
)
slide_copy_fast, slide_copy_steady = (
    make_slide_copy(**config) for config in SLIDE_COPY_VARIANTS
)

# All 31 nodes in layer order: extract (7) -> compose (21) -> verify (3).
NODES = (
    keyfact_extract,
    text_summarize_fast,
    text_summarize_steady,
    text_translate_fast,
    text_translate_steady,
    style_profile,
    table_header_fix_llm,
    outline_doc_gen_fast,
    outline_doc_gen_steady,
    outline_slide_gen,
    draft_section_fast,
    draft_section_steady,
    draft_section_verbose,
    draft_email_concise,
    draft_email_detailed,
    draft_speaker_notes,
    formula_gen_fast,
    formula_gen_steady,
    slide_copy_fast,
    slide_copy_steady,
    text_polish_conservative,
    text_polish_aggressive,
    text_expand,
    title_gen,
    chart_type_pick_rule,
    chart_type_pick_llm,
    theme_palette_pick,
    insight_narrate,
    fact_check_judge,
    quality_judge,
    grammar_check_llm,
)
