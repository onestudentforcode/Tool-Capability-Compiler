"""LLM access facade for the office battlefield (batch C).

One replaceable module-level handle (``RESOURCE``). The real handle points at
the local Ollama (base_url defaults from env ``LLM_BASE_URL`` to
localhost:11434) and is never contacted by tests — they swap in a fake via
:func:`set_resource`. Every office LLM tool calls the model through
:func:`complete` / :func:`complete_json`, so token / measured-cost metering
settles inside :class:`LLMResource` at the tool-call boundary and tool bodies
carry zero reporting code (office-battlefield.md §2 ②).

``lenient_json`` is the milestone's declared failure path: LLM output that
does not parse into a JSON object (prose around an object is tolerated)
raises ``ValueError``, which ToolExecutor classifies as
``TrialFailureCategory.TOOL_EXECUTION_ERROR``.

NOTE: no `from __future__ import annotations` here — this module stays
uniform with the office tool modules (real annotations, ToolExecutor
contract).
"""

import json
import re

from capability_runtime.resources import LLMResource, LLMResponse

# Real semantics: local Ollama. Constructing the handle opens no connection,
# but completing through it would touch the network — tests must
# set_resource a fake handle first.
RESOURCE = LLMResource(model="qwen3:1.7b", input_cost_per_1k=0.5, output_cost_per_1k=1.0)


def set_resource(resource: LLMResource) -> None:
    """Swap the module-level LLM handle (the tests' injection point)."""
    global RESOURCE
    RESOURCE = resource


def get_resource() -> LLMResource:
    """The active handle; tools read it per call so test swaps always win."""
    return RESOURCE


async def complete(prompt: str, *, system: str | None = None) -> LLMResponse:
    """One completion through the active handle; metering is automatic."""
    return await get_resource().complete(prompt, system=system)


async def complete_json(prompt: str, *, system: str | None = None) -> dict:
    """One completion parsed leniently into a JSON object."""
    response = await complete(prompt, system=system)
    return lenient_json(response.text)


def lenient_json(text: str) -> dict:
    """Parse LLM output into a JSON object, tolerating surrounding prose.

    ``json.loads`` first; on failure the first balanced ``{...}`` block
    (located by regex, balanced by a string-aware scan) is retried.
    Anything else raises ``ValueError`` — the declared
    malformed-LLM-output failure path.
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{", text)
        if match is None:
            raise ValueError(
                f"no JSON object in LLM output: {text[:120]!r}"
            ) from None
        block = _balanced_object(text, match.start())
        if block is None:
            raise ValueError(
                f"unbalanced JSON object in LLM output: {text[:120]!r}"
            ) from None
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed JSON in LLM output: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"LLM output is not a JSON object: {type(parsed).__name__}")
    return parsed


def _balanced_object(text: str, start: int) -> str | None:
    """The balanced ``{...}`` block starting at ``start`` (string-aware)."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
