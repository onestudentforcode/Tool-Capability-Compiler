"""Inner worlds of the office composites (office-battlefield batch F).

Two bounded refine loops, deterministic under the offline fake, following
the composite-refund idiom: a fixed per-iteration route plus metered
cross-iteration gates that make iteration 2 differ from iteration 1.

  doc_composed_report   c_draft draft_step (gate-cached re-emission of the
                        current best; first call drafts via the real
                        draft_section handler) -> c_check length rule ->
                        c_polish deterministic expansion (updates the gate)
                        -> c_release report_release (emits the terminal
                        Draft ONLY when checks pass; holdover keeps the
                        layer alive on failure)
  ppt_composed_deck     c_draft deck_step (rough deck: headers + sentences
                        crammed onto ONE slide -> overflows) -> c_check
                        overflow rule -> c_polish trim to <=5 bullets ->
                        c_release deck_release (terminal SlideCopy only on
                        pass; holdover sibling)

Convergence is deterministic: iteration 1 fails the check and improves the
gate's current best; iteration 2 re-emits the improved draft/deck
idempotently, passes the check, and the release gate produces the terminal
type (first appearance == success — see the runtime's first-match output
extraction). Gates are InMemoryStores so every gate read/write is metered
onto the invoking tool; the office fixture manager resets them per trial
(reset_inner_gates).

NOTE: no `from __future__ import annotations` here — the executor and the
declaration diagnostics inspect real type annotations.
"""

import asyncio
from dataclasses import dataclass

from capability_runtime import (
    LayerRegistry,
    ToolRegistry,
    TopologyBuilder,
    tool,
)
from capability_runtime.resources import InMemoryStore

from . import tools_llm
from .facts import Draft, FactSheet, SlideCopy, SourceDoc


# ---- private inner types (never on the domain spine) ------------------------


@dataclass(frozen=True)
class WorkingDraft:
    title: str
    body: str


@dataclass(frozen=True)
class WorkingDeck:
    deck_id: str
    slides: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class InnerReview:
    passed: bool
    issues: tuple[str, ...]


@dataclass(frozen=True)
class KeepNote:
    note: str


# ---- cross-iteration gates (metered; fixture resets per trial) --------------

DRAFT_GATE = InMemoryStore("office_composite_draft")
DECK_GATE = InMemoryStore("office_composite_deck")

_MIN_WORDS = 10
_MAX_BULLETS = 5
_MAX_BULLET_CHARS = 90
_EXPANSION = (
    " The report elaborates these findings with supporting context, "
    "risks, and next steps for the team."
)


def reset_inner_gates() -> None:
    DRAFT_GATE.clear()
    DECK_GATE.clear()


# ---- doc inner tools ---------------------------------------------------------


@tool(
    layer="c_draft",
    consumes=[SourceDoc, FactSheet],
    produces=[WorkingDraft],
    cost_per_call=0.001,
    capabilities={"doc.draft.inner"},
    description="Gate-cached drafter: first call drafts (fast variant), later iterations re-emit the current best",
)
async def draft_step(source_doc: SourceDoc, fact_sheet: FactSheet) -> WorkingDraft:
    await asyncio.sleep(0.006)
    cached = await DRAFT_GATE.get("current")
    if cached is not None:
        return cached
    draft = await tools_llm.draft_section_fast.handler(source_doc, fact_sheet)
    working = WorkingDraft(title=draft.title, body=draft.body)
    await DRAFT_GATE.put("current", working)
    return working


@tool(
    layer="c_check",
    consumes=[WorkingDraft],
    produces=[InnerReview],
    cost_per_call=0.0005,
    capabilities={"doc.length.inner"},
    description="Inner length rule: 10..800 words",
)
async def check_step(working_draft: WorkingDraft) -> InnerReview:
    await asyncio.sleep(0.002)
    words = len(working_draft.body.split())
    passed = _MIN_WORDS <= words <= 800
    issues = () if passed else (f"body has {words} words, expected 10..800",)
    return InnerReview(passed=passed, issues=issues)


@tool(
    layer="c_polish",
    consumes=[WorkingDraft, InnerReview],
    produces=[WorkingDraft],
    cost_per_call=0.004,
    capabilities={"doc.polish.inner"},
    description="Deterministic expansion when the length rule failed; updates the gate's current best",
)
async def polish_step(working_draft: WorkingDraft, inner_review: InnerReview) -> WorkingDraft:
    await asyncio.sleep(0.005)
    if inner_review.passed:
        return working_draft
    improved = WorkingDraft(
        title=working_draft.title, body=working_draft.body + _EXPANSION
    )
    await DRAFT_GATE.put("current", improved)
    return improved


@tool(
    layer="c_release",
    consumes=[WorkingDraft, InnerReview],
    produces=[Draft],
    cost_per_call=0.0005,
    capabilities={"doc.release.inner"},
    description="Terminal gate: emits the Draft only when the inner review passed",
)
async def report_release(working_draft: WorkingDraft, inner_review: InnerReview) -> Draft:
    await asyncio.sleep(0.001)
    if not inner_review.passed:
        raise RuntimeError(
            "composed report failed inner checks: "
            + "; ".join(inner_review.issues)
        )
    return Draft(title=working_draft.title, body=working_draft.body)


@tool(
    layer="c_release",
    consumes=[WorkingDraft],
    produces=[KeepNote],
    cost_per_call=0.0001,
    capabilities={"doc.hold.inner"},
    description="Keeps the release layer alive while the gate refuses",
)
async def report_holdover(working_draft: WorkingDraft) -> KeepNote:
    await asyncio.sleep(0.0005)
    return KeepNote(note="awaiting inner checks")


# ---- deck inner tools --------------------------------------------------------


@tool(
    layer="c_draft",
    consumes=[SourceDoc, FactSheet],
    produces=[WorkingDeck],
    cost_per_call=0.001,
    capabilities={"ppt.deck.inner"},
    description="Rough deck: headers, sentences and key facts crammed onto one slide (overflow fuel); gate-cached afterwards",
)
async def deck_step(source_doc: SourceDoc, fact_sheet: FactSheet) -> WorkingDeck:
    await asyncio.sleep(0.006)
    cached = await DECK_GATE.get("current")
    if cached is not None:
        return cached
    lines = [line.strip() for line in source_doc.text.splitlines()]
    headers = tuple(
        dict.fromkeys(
            line.lstrip("#").strip() for line in lines if line.startswith("#")
        )
    )
    sentences = tuple(
        dict.fromkeys(
            line.rstrip(".") for line in lines if line and not line.startswith("#")
        )
    )
    bullets = (
        tuple(dict.fromkeys(headers + sentences + tuple(fact_sheet.facts)))
        or ("Overview",)
    )
    deck = WorkingDeck(
        deck_id=source_doc.doc_id,
        slides=((source_doc.title or "Deck", bullets),),
    )
    await DECK_GATE.put("current", deck)
    return deck


@tool(
    layer="c_check",
    consumes=[WorkingDeck],
    produces=[InnerReview],
    cost_per_call=0.0005,
    capabilities={"ppt.overflow.inner"},
    description="Inner overflow rule: <=5 bullets per slide, <=90 chars per bullet",
)
async def deck_check_step(working_deck: WorkingDeck) -> InnerReview:
    await asyncio.sleep(0.002)
    issues = []
    for title, bullets in working_deck.slides:
        if len(bullets) > _MAX_BULLETS:
            issues.append(
                f"slide {title!r} has {len(bullets)} bullets (max {_MAX_BULLETS})"
            )
        for bullet in bullets:
            if len(bullet) > _MAX_BULLET_CHARS:
                issues.append(f"bullet too long ({len(bullet)} chars)")
    return InnerReview(passed=not issues, issues=tuple(issues))


@tool(
    layer="c_polish",
    consumes=[WorkingDeck, InnerReview],
    produces=[WorkingDeck],
    cost_per_call=0.002,
    capabilities={"ppt.trim.inner"},
    description="Deterministic trim to <=5 bullets and <=90 chars; updates the gate's current best",
)
async def deck_polish_step(working_deck: WorkingDeck, inner_review: InnerReview) -> WorkingDeck:
    await asyncio.sleep(0.003)
    if inner_review.passed:
        return working_deck
    trimmed = tuple(
        (
            title,
            tuple(bullet for bullet in bullets if len(bullet) <= _MAX_BULLET_CHARS)[
                :_MAX_BULLETS
            ],
        )
        for title, bullets in working_deck.slides
    )
    improved = WorkingDeck(deck_id=working_deck.deck_id, slides=trimmed)
    await DECK_GATE.put("current", improved)
    return improved


@tool(
    layer="c_release",
    consumes=[WorkingDeck, InnerReview],
    produces=[SlideCopy],
    cost_per_call=0.0005,
    capabilities={"ppt.release.inner"},
    description="Terminal gate: emits the SlideCopy only when the inner overflow review passed",
)
async def deck_release(working_deck: WorkingDeck, inner_review: InnerReview) -> SlideCopy:
    await asyncio.sleep(0.001)
    if not inner_review.passed:
        raise RuntimeError(
            "composed deck failed inner checks: " + "; ".join(inner_review.issues)
        )
    return SlideCopy(deck_id=working_deck.deck_id, slides=working_deck.slides)


@tool(
    layer="c_release",
    consumes=[WorkingDeck],
    produces=[KeepNote],
    cost_per_call=0.0001,
    capabilities={"ppt.hold.inner"},
    description="Keeps the release layer alive while the gate refuses",
)
async def deck_holdover(working_deck: WorkingDeck) -> KeepNote:
    await asyncio.sleep(0.0005)
    return KeepNote(note="awaiting inner overflow checks")


# ---- inner topologies --------------------------------------------------------


_DOC_INNER_LAYERS = ("c_draft", "c_check", "c_polish", "c_release")


def build_doc_inner_topology():
    layers = LayerRegistry()
    for order, name in enumerate(_DOC_INNER_LAYERS):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in (draft_step, check_step, polish_step, report_release, report_holdover):
        tools.register(node)
    return TopologyBuilder(layers, tools).build()


def build_deck_inner_topology():
    layers = LayerRegistry()
    for order, name in enumerate(_DOC_INNER_LAYERS):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in (
        deck_step,
        deck_check_step,
        deck_polish_step,
        deck_release,
        deck_holdover,
    ):
        tools.register(node)
    return TopologyBuilder(layers, tools).build()
