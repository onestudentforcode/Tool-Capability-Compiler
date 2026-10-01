"""In-memory office corpus state driving the office battlefield tools.

Corpus files under ``corpus/`` are loaded into metered :class:`InMemoryStore`
handles at seed time (administrative, unmetered — fixtures run outside tool
calls); the L0 read tools only ever touch the handles, so every read is
counted onto the invoking tool. The store holds **raw** corpus texts: the
reading tools parse them into domain types (office-battlefield.md §2).

Variants (corpus states; messy/sparse/conflict land with the scenario batch):

    clean   the corpus exactly as authored
"""

from __future__ import annotations

import csv
import io
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from capability_runtime.resources import InMemoryStore

VARIANTS = ("clean",)
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
        self.docs.seed(_load_raw("docs", ".md"))
        self.tables.seed(_load_raw("tables", ".csv"))
        self.decks.seed(_load_raw("decks", ".json"))

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
    """Parse raw csv text into (header, data rows); shared by tools/tests."""
    rows = [tuple(row) for row in csv.reader(io.StringIO(text)) if row]
    if not rows:
        raise ValueError("corpus table has no header row")
    return rows[0], tuple(rows[1:])


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
