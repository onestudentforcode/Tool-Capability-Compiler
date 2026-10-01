"""L0 context tools: read the office corpus into typed artifacts.

Office battlefield batch A (docs/acceptance/office-battlefield.md §4). Four
deterministic readers; parsing happens here so downstream layers only ever
see domain types. Simulated latency lives in the tool bodies (asyncio.sleep)
so route latency can differentiate — never in core.

    fs_read      raw file read          -> SourceDoc (capability fs.read)
    doc_parse    restricted md parse    -> SourceDoc (capability doc.parse)
    table_parse  csv parse              -> DataTable  (capability table.parse)
    deck_parse   json parse             -> SlideDigest (capability ppt.parse)

fs_read and doc_parse are two real providers of SourceDoc at L0 — the first
redundant capability of the office domain.
"""

import asyncio

# NOTE: no `from __future__ import annotations` here — ToolExecutor resolves
# tool arguments by inspecting the real (non-string) type annotations.

from capability_runtime import tool

# Relative imports only: the office modules are always imported as the
# ``examples.office`` package, so plain-name imports can never shadow the
# slow_refund sandbox modules (facts/store) in a shared pytest process.
from . import store
from .facts import DataTable, SlideDigest, SourceDoc


def _md_title(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


@tool(
    layer="context",
    produces=[SourceDoc],
    cost_per_call=0.001,
    capabilities={"fs.read"},
    description="Raw-read the target corpus document as plain text",
)
async def fs_read() -> SourceDoc:
    await asyncio.sleep(0.002)
    record = await store.STORE.docs.get(store.STORE.target_doc_id)
    if record is None:
        raise LookupError(f"no corpus document seeded (variant={store.STORE.variant})")
    return SourceDoc(doc_id=record["name"], title="", text=record["text"])


@tool(
    layer="context",
    produces=[SourceDoc],
    cost_per_call=0.001,
    capabilities={"doc.parse"},
    description="Parse the restricted-markdown corpus document",
)
async def doc_parse() -> SourceDoc:
    await asyncio.sleep(0.003)
    record = await store.STORE.docs.get(store.STORE.target_doc_id)
    if record is None:
        raise LookupError(f"no corpus document seeded (variant={store.STORE.variant})")
    return SourceDoc(
        doc_id=record["name"], title=_md_title(record["text"]), text=record["text"]
    )


@tool(
    layer="context",
    produces=[DataTable],
    cost_per_call=0.002,
    capabilities={"table.parse"},
    description="Parse the csv corpus table into a DataTable",
)
async def table_parse() -> DataTable:
    await asyncio.sleep(0.004)
    record = await store.STORE.tables.get(store.STORE.target_table_id)
    if record is None:
        raise LookupError(f"no corpus table seeded (variant={store.STORE.variant})")
    columns, rows = store.parse_csv(record["text"])
    return DataTable(table_id=record["name"], columns=columns, rows=rows)


@tool(
    layer="context",
    produces=[SlideDigest],
    cost_per_call=0.002,
    capabilities={"ppt.parse"},
    description="Parse the json corpus deck into a SlideDigest",
)
async def deck_parse() -> SlideDigest:
    await asyncio.sleep(0.004)
    record = await store.STORE.decks.get(store.STORE.target_deck_id)
    if record is None:
        raise LookupError(f"no corpus deck seeded (variant={store.STORE.variant})")
    deck_id, slides = store.parse_deck(record["text"])
    return SlideDigest(deck_id=deck_id or record["name"], slides=slides)


NODES = (fs_read, doc_parse, table_parse, deck_parse)
