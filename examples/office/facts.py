"""Domain type spine shared by the office battlefield tools and evaluators.

Office battlefield batch A (docs/acceptance/office-battlefield.md §3). The
cross-layer backbone — exactly the 15 types the milestone spec names; every
tool consumes/produces a subset of these and nothing else. Value types only:
no logic, no I/O, frozen so artifacts can be shared across the blackboard.

    SourceDoc / DataTable / SlideDigest                 (L0 reads)
      -> FactSheet / Outline / FormulaSpec / SlideOutline /
         StyleSpec / ChartSpec                         (extract / compose)
        -> Draft / SlideCopy / Narrative / EmailDraft  (compose)
          -> ReviewReport                              (verify)
            -> FileSpec                                (render, in-memory only)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDoc:
    """A corpus document: restricted markdown, raw or parsed."""

    doc_id: str
    title: str
    text: str


@dataclass(frozen=True)
class DataTable:
    """A corpus table parsed from csv: string cells, header included."""

    table_id: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class SlideDigest:
    """A corpus deck parsed from json: (title, bullets) per slide."""

    deck_id: str
    slides: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class FactSheet:
    """Extracted facts anchored to a source document (fact-verify reference)."""

    doc_id: str
    facts: tuple[str, ...]


@dataclass(frozen=True)
class Outline:
    """Document skeleton: a title and ordered section headings."""

    title: str
    sections: tuple[str, ...]


@dataclass(frozen=True)
class FormulaSpec:
    """One spreadsheet formula request: target column, formula, intent."""

    column: str
    formula: str
    explanation: str


@dataclass(frozen=True)
class SlideOutline:
    """Deck skeleton: (slide title, key points) per slide."""

    deck_id: str
    slides: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class StyleSpec:
    """Writing-style contract extracted from a reference document."""

    tone: str
    max_sentence_words: int
    palette: str = ""


@dataclass(frozen=True)
class TableProfile:
    """Column-level profile of a corpus table: types, null rates, anomalies."""

    table_id: str
    column_types: tuple[tuple[str, str], ...]
    null_rates: tuple[tuple[str, float], ...]
    anomalies: tuple[str, ...]


@dataclass(frozen=True)
class TextSegments:
    """A source document cut into sequential chunks for downstream LLM tools."""

    doc_id: str
    chunks: tuple[str, ...]


@dataclass(frozen=True)
class FormulaAudit:
    """Audit findings for formula-looking cells already present in a table."""

    table_id: str
    findings: tuple[str, ...]


@dataclass(frozen=True)
class AggregateResult:
    """Grouped aggregation of one numeric metric over a corpus table."""

    table_id: str
    metric: str
    groups: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ChartSpec:
    """A chart request: kind, title, and the ready-to-plot series."""

    chart_id: str
    kind: str
    title: str
    labels: tuple[str, ...]
    values: tuple[float, ...]


@dataclass(frozen=True)
class Draft:
    """A drafted document: title plus markdown-ish body text."""

    title: str
    body: str


@dataclass(frozen=True)
class SlideCopy:
    """Final per-slide copy: (title, bullets) per slide."""

    deck_id: str
    slides: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class Narrative:
    """A short narrated takeaway (e.g. data insight as a sentence)."""

    text: str


@dataclass(frozen=True)
class EmailDraft:
    """A mail draft — never sent (non-idempotent tools are deferred, §10)."""

    to: str
    subject: str
    body: str


@dataclass(frozen=True)
class ReviewReport:
    """Structured verify-stage outcome: pass flag, issues, judge-style score."""

    passed: bool
    issues: tuple[str, ...]
    score: float


@dataclass(frozen=True)
class FileSpec:
    """Render terminal artifact: a serializable in-memory file spec.

    Render stops here by design (§1/§10): the spec is verifiable but nothing
    is written to disk and no OOXML is produced.
    """

    kind: str
    name: str
    encoding: str
    content: str
