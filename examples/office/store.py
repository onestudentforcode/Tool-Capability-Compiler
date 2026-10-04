"""In-memory office corpus state driving the office battlefield tools.

Corpus files under ``corpus/`` are loaded into metered :class:`InMemoryStore`
handles at seed time (administrative, unmetered — fixtures run outside tool
calls); the L0 read tools only ever touch the handles, so every read is
counted onto the invoking tool. The store holds **raw** corpus texts: the
reading tools parse them into domain types (office-battlefield.md §2).

Variants (fixture states; deterministic pure transforms of the corpus):

    clean     the corpus exactly as authored
    messy     table rows are ragged (field count mismatch) -> table_parse
              rejects the table: the "messy corpus -> read failure" path
    sparse    every third data row has its numeric cells blanked -> profile
              null rates and aggregate skips (missing fields, still readable)
    conflict  the document gains a correction section contradicting the
              table figures -> fact/judge tools see inconsistent sources
"""

from __future__ import annotations

import csv
import io
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from capability_runtime.resources import InMemoryStore

VARIANTS = ("clean", "messy", "sparse", "conflict")
DEFAULT_VARIANT = "clean"

_CORPUS_DIR = Path(__file__).resolve().parent / "corpus"


def _load_raw(directory: str, suffix: str) -> dict[str, dict[str, str]]:
    """Load every corpus file as ``{key: {"name": key, "text": raw}}``."""
    records: dict[str, dict[str, str]] = {}
    for path in sorted((_CORPUS_DIR / directory).glob(f"*{suffix}")):
        records[path.stem] = {
            "name": path.stem,
            "text": path.read_text(encoding="utf-8"),
        }
    return records


def _messy_tables(records: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Drop the last field of one data row per table: a ragged row."""
    out: dict[str, dict[str, str]] = {}
    for key, record in records.items():
        lines = record["text"].rstrip("\n").splitlines()
        if len(lines) > 2:
            lines = lines[:2] + [lines[2].rsplit(",", 1)[0]] + lines[3:]
        out[key] = {"name": record["name"], "text": "\n".join(lines) + "\n"}
    return out


def _sparse_tables(records: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Blank the numeric cells of every third data row (missing fields)."""
    out: dict[str, dict[str, str]] = {}
    for key, record in records.items():
        rows = [line.split(",") for line in record["text"].rstrip("\n").splitlines()]
        for index in range(3, len(rows), 3):
            for column in range(2, len(rows[index])):
                rows[index][column] = ""
        text = "\n".join(",".join(row) for row in rows) + "\n"
        out[key] = {"name": record["name"], "text": text}
    return out


def _conflict_docs(records: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Append a correction section that contradicts the table figures."""
    out: dict[str, dict[str, str]] = {}
    for key, record in records.items():
        text = (
            record["text"].rstrip("\n")
            + "\n\n## Correction\nThe finance table contradicts the figures "
            "above: revenue actually declined month over month.\n"
        )
        out[key] = {"name": record["name"], "text": text}
    return out


def _first_key(handle: InMemoryStore) -> str | None:
    snapshot = handle.snapshot()
    return next(iter(sorted(snapshot)), None)


@dataclass
class OfficeStore:
    """The whole mutable corpus state for one demo run.

    ``docs`` / ``tables`` / ``decks`` are metered handles: tools must use
    their async accessors so reads are counted. ``reset`` seeds via the
    unmetered administrative path.
    """

    variant: str = DEFAULT_VARIANT
    docs: InMemoryStore = field(default_factory=lambda: InMemoryStore("office_docs"))
    tables: InMemoryStore = field(default_factory=lambda: InMemoryStore("office_tables"))
    decks: InMemoryStore = field(default_factory=lambda: InMemoryStore("office_decks"))
    rng: random.Random = field(default_factory=random.Random)

    def reset(
        self,
        variant: str = DEFAULT_VARIANT,
        *,
        scenario_id: str = "",
        trial_index: int = 0,
    ) -> None:
        if variant not in VARIANTS:
            raise ValueError(
                f"unknown office corpus variant {variant!r}; expected one of {VARIANTS}"
            )
        self.variant = variant
        # Deterministic randomness: jitter / flaky-failure draws derive from
        # the (scenario_id, trial_index) seed (office-battlefield.md §1).
        self.rng = random.Random(f"{scenario_id}#{trial_index}")
        for handle in (self.docs, self.tables, self.decks):
            handle.clear()
        docs = _load_raw("docs", ".md")
        tables = _load_raw("tables", ".csv")
        decks = _load_raw("decks", ".json")
        if variant == "messy":
            tables = _messy_tables(tables)
        elif variant == "sparse":
            tables = _sparse_tables(tables)
        elif variant == "conflict":
            docs = _conflict_docs(docs)
        self.docs.seed(docs)
        self.tables.seed(tables)
        self.decks.seed(decks)

    # ---- read-only views (tests / fixtures; not the tools' access path) ----

    @property
    def target_doc_id(self) -> str | None:
        return _first_key(self.docs)

    @property
    def target_table_id(self) -> str | None:
        return _first_key(self.tables)

    @property
    def target_deck_id(self) -> str | None:
        return _first_key(self.decks)


def parse_csv(text: str) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Parse raw csv text into (header, data rows); shared by tools/tests.

    Every data row must match the header width — a ragged row (the messy
    fixture) raises ValueError, which the executor classifies as a tool
    failure of the reading tool.
    """
    rows = [tuple(row) for row in csv.reader(io.StringIO(text)) if row]
    if not rows:
        raise ValueError("corpus table has no header row")
    header, data_rows = rows[0], rows[1:]
    for line_no, row in enumerate(data_rows, start=2):
        if len(row) != len(header):
            raise ValueError(
                f"corpus table row {line_no} has {len(row)} fields, "
                f"expected {len(header)}"
            )
    return header, tuple(data_rows)


def parse_deck(text: str) -> tuple[str, tuple[tuple[str, tuple[str, ...]], ...]]:
    """Parse raw deck json into (deck_id, (title, bullets) per slide)."""
    payload = json.loads(text)
    slides = tuple(
        (str(slide["title"]), tuple(str(bullet) for bullet in slide.get("bullets", ())))
        for slide in payload.get("slides", ())
    )
    return str(payload.get("deck_id", "")), slides


# Module-level sandbox; the demo fixture manager (batch D) resets it per trial.
STORE = OfficeStore()
STORE.reset(DEFAULT_VARIANT)
