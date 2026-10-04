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

from capability_runtime import TokenUsage
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


# ---- offline fake (batch D scale runs; zero network) ------------------------

# Variant-visible content sizes: the fake's identity of a variant is the
# length/shape of its reply, so measured tokens and judge scores actually
# differentiate fast/steady/verbose (office-battlefield.md §5).
_FAKE_BODY = {
    "short": "Revenue grew 8%.",
    "medium": (
        "Revenue grew 8% quarter over quarter while churn stayed steady at "
        "2.1%. The auth migration remains the main delivery risk."
    ),
    "long": (
        "Revenue grew 8% quarter over quarter. Churn stayed steady at 2.1%. "
        "The auth migration remains the main delivery risk. "
    )
    * 10,
}


def _fake_size(system: str) -> str:
    """Reply size implied by the variant's system prompt (its identity)."""
    if "minimal" in system or "terse" in system:
        return "short"
    if "elaborate" in system or "complete" in system:
        return "long"
    return "medium"


def _fake_fill(prompt: str, system: str = "") -> str:
    """Deterministic content for one prompt, honoring its JSON shape hint.

    Office prompts all embed ``Reply only with JSON: {shape}.`` — the fake
    fills exactly that shape with plausible office values, sized by the
    variant style, so the lenient parser and every downstream coercion see
    honest data offline. Review shapes score by prompt length: longer input
    (a verbose draft) reads as higher judged quality.
    """
    size = _fake_size(system)
    values = {
        "facts": ["revenue grew 8% quarter over quarter", "churn steady at 2.1%"],
        "text": _FAKE_BODY[size],
        "title": "Office Report",
        "body": _FAKE_BODY[size],
        "tone": "formal",
        "max_sentence_words": 22,
        "columns": ["region", "month", "units", "revenue"],
        "slides": [
            {"title": "Overview", "points": ["Revenue up 8%"]},
            {"title": "Risks", "points": ["Auth migration may slip"]},
            {"title": "Outlook", "points": ["Q4 budget ask", "Two hires"]},
        ][: 1 if size == "short" else 2 if size == "medium" else 3],
        "to": "team@example.com",
        "subject": "Office update",
        "column": "revenue",
        "formula": "=SUM(D2:D13)",
        "explanation": "Sums the revenue column across all data rows.",
        "kind": "bar",
    }
    if "score" in prompt:
        score = 0.5 + 0.45 * min(len(prompt), 1500) / 1500
        values["score"] = round(score, 2)
        values["passed"] = score >= 0.5
        values["issues"] = ["closing paragraph drifts informal"] if score < 0.7 else []
    shape_match = re.search(r"Reply only with JSON: (\{.*\})\.?", prompt)
    shape = shape_match.group(1) if shape_match else "{}"
    filled = {}
    for key in re.findall(r'"([a-z_]+)"\s*:', shape):
        if key in values:
            filled[key] = values[key]
    return json.dumps(filled, ensure_ascii=False)


def install_offline_fake() -> None:
    """Route all office LLM calls through a deterministic offline fake.

    The fake fills each prompt's declared JSON shape and scales its usage
    tokens with the prompt/system length, so measured metering still
    differentiates variants (fast/steady/verbose). A seeded wobble (~4% of
    calls, drawn from the trial-seeded rng) returns total garbage,
    exercising the malformed-output -> TOOL_EXECUTION_ERROR path
    reproducibly. (Timeouts come from the translate factory's cancellable
    stall — a sync sleep here would block the event loop and break
    asyncio.wait_for.)
    """

    def fake_http(payload) -> dict:
        from . import store

        prompt = payload["messages"][-1]["content"]
        system = payload["messages"][0]["content"] if len(payload["messages"]) > 1 else ""
        draw = store.STORE.rng.random()
        content = "total garbage not json" if draw < 0.04 else _fake_fill(prompt, system)
        usage = TokenUsage(
            input_tokens=8 + len(prompt) // 16 + len(system) // 8,
            output_tokens=4 + len(content) // 8,
        )
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": usage.input_tokens,
                "completion_tokens": usage.output_tokens,
            },
        }

    set_resource(
        LLMResource(
            model="office-fake", input_cost_per_1k=0.5, output_cost_per_1k=1.0,
            _http=fake_http,
        )
    )
