"""Parameterized LLM tool factories for the office battlefield (batch C).

Redundant capability variants share one implementation (office-battlefield.md
§2): each ``make_*`` factory closes over a prompt style, a simulated latency
and a declared cost, then returns a distinct ``ToolNode``. Identity comes from
configuration, so variant differences surface in the metering data (declared
cost / measured tokens / latency / output length), never in duplicated code.

The shared prompt-style table and the lenient JSON coercions live here too:
every family parses one uniform JSON shape, and a malformed LLM payload fails
honestly (ValueError -> TOOL_EXECUTION_ERROR).

NOTE: no `from __future__ import annotations` here — ToolExecutor resolves
tool arguments by inspecting the real (non-string) type annotations of the
closures this module builds.
"""

import asyncio

from capability_runtime import ToolNode, tool

from . import office_llm, store
from .facts import (
    Draft,
    FactSheet,
    FormulaSpec,
    Narrative,
    Outline,
    ReviewReport,
    SlideCopy,
    SourceDoc,
)

# Prompt styles: the variant identity an LLM (or the offline fake) can see.
_STYLE_SYSTEMS = {
    "fast": "You draft minimal office content: one short sentence per field.",
    "steady": "You draft careful office content: thorough, detailed, well structured.",
    "verbose": "You draft expansive office content: elaborate every point at length.",
    "concise": "You write terse office email: one short sentence.",
    "detailed": "You write complete office email: detailed and explicit.",
    "conservative": "You polish conservatively: keep the wording, fix only clear issues.",
    "aggressive": "You polish aggressively: rewrite freely for impact.",
}


def style_system(style: str) -> str:
    """The system prompt for a variant style (the variant-visible identity)."""
    try:
        return _STYLE_SYSTEMS[style]
    except KeyError:
        raise ValueError(f"unknown office LLM variant style: {style!r}") from None


# ---- lenient coercions: one uniform JSON shape per family -----------------


def as_str(value: object, default: str) -> str:
    return default if value is None else str(value)


def as_str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def slides_from_json(value: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """``[{"title": str, "points": [str]}]`` -> SlideOutline/SlideCopy slides."""
    if not isinstance(value, (list, tuple)):
        return ()
    slides = []
    for item in value:
        if isinstance(item, dict):
            slides.append((as_str(item.get("title"), ""), as_str_tuple(item.get("points"))))
    return tuple(slides)


def review_from_json(payload: dict) -> ReviewReport:
    """``{"passed": bool, "issues": [str], "score": float}`` -> ReviewReport."""
    return ReviewReport(
        passed=bool(payload.get("passed", False)),
        issues=as_str_tuple(payload.get("issues")),
        score=float(payload.get("score", 0.0)),
    )


# ---- factory plumbing ------------------------------------------------------


def _node(
    handler,
    *,
    name: str,
    layer: str,
    capabilities: frozenset[str] | set[str],
    consumes: tuple[type, ...],
    produces: tuple[type, ...],
    cost: float,
    description: str,
) -> ToolNode:
    """Decorate one closure into a named ToolNode."""
    return tool(
        name=name,
        layer=layer,
        capabilities=capabilities,
        consumes=consumes,
        produces=produces,
        cost_per_call=cost,
        description=description,
    )(handler)


# ---- extract-layer factories -----------------------------------------------


def make_text_summarize(
    *, name: str, latency: float, cost: float, style: str, prompt_hint: str
) -> ToolNode:
    system = style_system(style)

    async def text_summarize(source_doc: SourceDoc) -> Narrative:
        await asyncio.sleep(latency)
        shape = 'Reply only with JSON: {"text": str}.'
        prompt = f"Summarize this document. {shape} {prompt_hint}\n\n{source_doc.text}"
        payload = await office_llm.complete_json(prompt, system=system)
        return Narrative(text=as_str(payload.get("text"), ""))

    return _node(
        text_summarize,
        name=name,
        layer="extract",
        capabilities={"text.summarize"},
        consumes=(SourceDoc,),
        produces=(Narrative,),
        cost=cost,
        description=f"LLM summary of the source document ({style} variant)",
    )


def make_text_translate(
    *,
    name: str,
    latency: float,
    cost: float,
    style: str,
    prompt_hint: str,
    flaky: bool = False,
) -> ToolNode:
    system = style_system(style)

    async def text_translate(source_doc: SourceDoc) -> SourceDoc:
        await asyncio.sleep(latency)
        if flaky and store.STORE.rng.random() < 0.2:
            # Seeded stall: with a per-tool timeout this becomes TIMEOUT
            # deterministically (office-battlefield.md §1 / §6).
            await asyncio.sleep(0.5)
        shape = 'Reply only with JSON: {"title": str, "text": str}.'
        prompt = (
            f"Translate this document to English. {shape} {prompt_hint}\n\n"
            f"Title: {source_doc.title}\n{source_doc.text}"
        )
        payload = await office_llm.complete_json(prompt, system=system)
        return SourceDoc(
            doc_id=source_doc.doc_id,
            title=as_str(payload.get("title"), source_doc.title),
            text=as_str(payload.get("text"), source_doc.text),
        )

    return _node(
        text_translate,
        name=name,
        layer="extract",
        capabilities={"text.translate"},
        consumes=(SourceDoc,),
        produces=(SourceDoc,),
        cost=cost,
        description=f"LLM translation of the source document ({style} variant)",
    )


# ---- compose-layer factories -----------------------------------------------


def make_outline_doc_gen(
    *, name: str, latency: float, cost: float, style: str, prompt_hint: str
) -> ToolNode:
    system = style_system(style)

    async def outline_doc_gen(source_doc: SourceDoc, fact_sheet: FactSheet) -> Outline:
        await asyncio.sleep(latency)
        shape = 'Reply only with JSON: {"title": str, "sections": [str]}.'
        facts_text = "; ".join(fact_sheet.facts)
        prompt = (
            f'Outline a document titled "{source_doc.title}" covering the facts: '
            f"{facts_text}. {shape} {prompt_hint}"
        )
        payload = await office_llm.complete_json(prompt, system=system)
        return Outline(
            title=as_str(payload.get("title"), source_doc.title),
            sections=as_str_tuple(payload.get("sections")),
        )

    return _node(
        outline_doc_gen,
        name=name,
        layer="compose",
        capabilities={"outline.compose"},
        consumes=(SourceDoc, FactSheet),
        produces=(Outline,),
        cost=cost,
        description=f"LLM document outline ({style} variant)",
    )


def make_draft_section(
    *, name: str, latency: float, cost: float, style: str, prompt_hint: str
) -> ToolNode:
    system = style_system(style)

    async def draft_section(source_doc: SourceDoc, fact_sheet: FactSheet) -> Draft:
        await asyncio.sleep(latency)
        shape = 'Reply only with JSON: {"title": str, "body": str}.'
        facts_text = "; ".join(fact_sheet.facts)
        prompt = (
            f'Draft the section "{source_doc.title}" from these facts: '
            f"{facts_text}. {shape} {prompt_hint}"
        )
        payload = await office_llm.complete_json(prompt, system=system)
        return Draft(
            title=as_str(payload.get("title"), source_doc.title),
            body=as_str(payload.get("body"), ""),
        )

    return _node(
        draft_section,
        name=name,
        layer="compose",
        capabilities={"section.draft"},
        consumes=(SourceDoc, FactSheet),
        produces=(Draft,),
        cost=cost,
        description=f"LLM section drafter ({style} variant)",
    )


def make_formula_gen(
    *, name: str, latency: float, cost: float, style: str, prompt_hint: str
) -> ToolNode:
    system = style_system(style)

    async def formula_gen(narrative: Narrative) -> FormulaSpec:
        await asyncio.sleep(latency)
        shape = 'Reply only with JSON: {"column": str, "formula": str, "explanation": str}.'
        prompt = (
            f"Turn this request into one spreadsheet formula: {narrative.text} "
            f"{shape} {prompt_hint}"
        )
        payload = await office_llm.complete_json(prompt, system=system)
        return FormulaSpec(
            column=as_str(payload.get("column"), ""),
            formula=as_str(payload.get("formula"), ""),
            explanation=as_str(payload.get("explanation"), ""),
        )

    return _node(
        formula_gen,
        name=name,
        layer="compose",
        capabilities={"formula.generate"},
        consumes=(Narrative,),
        produces=(FormulaSpec,),
        cost=cost,
        description=f"LLM formula generator ({style} variant)",
    )


def make_slide_copy(
    *, name: str, latency: float, cost: float, style: str, prompt_hint: str
) -> ToolNode:
    system = style_system(style)

    async def slide_copy(source_doc: SourceDoc) -> SlideCopy:
        await asyncio.sleep(latency)
        shape = 'Reply only with JSON: {"slides": [{"title": str, "points": [str]}]}.'
        prompt = (
            f'Write slide copy for a deck based on "{source_doc.title}". '
            f"{shape} {prompt_hint}\n\n{source_doc.text}"
        )
        payload = await office_llm.complete_json(prompt, system=system)
        return SlideCopy(
            deck_id=source_doc.doc_id,
            slides=slides_from_json(payload.get("slides")),
        )

    return _node(
        slide_copy,
        name=name,
        layer="compose",
        capabilities={"slide.copy"},
        consumes=(SourceDoc,),
        produces=(SlideCopy,),
        cost=cost,
        description=f"LLM per-slide copy ({style} variant)",
    )
