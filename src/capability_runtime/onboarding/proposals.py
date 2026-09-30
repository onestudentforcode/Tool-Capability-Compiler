"""Capability proposals: one batched LLM call, human review, never auto-write.

Constitution of this module: a proposal has no parameter path to disk. Only
the explicitly reviewed ``approved`` mapping reaches
:func:`onboarding.apply.apply_capabilities`. Everything here produces things
for humans to read.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet, Any

from ..core.capability import validate_capability_name
from ..core.env import env_float, env_string
from ..core.errors import InvalidCapabilityError, ProposalError
from ..router.models import ToolSummary

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MODEL = "qwen3:1.7b"


@dataclass(frozen=True, slots=True)
class CapabilityProposal:
    tool: str
    capabilities: tuple[str, ...]
    rationale: str | None = None
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class DroppedProposal:
    tool: str
    capability: str


@dataclass(frozen=True, slots=True)
class CapabilityProposalSet:
    proposals: tuple[CapabilityProposal, ...]
    invalid_dropped: tuple[DroppedProposal, ...] = ()

    def for_tool(self, tool: str) -> CapabilityProposal | None:
        return next(
            (item for item in self.proposals if item.tool == tool), None
        )


def _system_prompt(vocabulary: AbstractSet[str]) -> str:
    lines = [
        "You propose business capabilities for tools in a tool-routing "
        "framework.",
        "Capability names are lowercase dot-separated, e.g. 'order.read', "
        "'refund.policy.check'.",
        "Prefer reusing terms from the existing vocabulary when they fit; "
        "propose new names only when nothing fits.",
        "Respond with JSON only: "
        '{"proposals": [{"tool": "<name>", "capabilities": ["a.b"], '
        '"rationale": "<short reason>", "confidence": 0.0}]}',
    ]
    if vocabulary:
        lines.append(
            "Existing vocabulary: " + ", ".join(sorted(vocabulary))
        )
    return "\n".join(lines)


def _user_prompt(summaries: Sequence[ToolSummary]) -> str:
    parts = []
    for summary in summaries:
        caps = (
            f" (already has: {', '.join(sorted(summary.capabilities))})"
            if summary.capabilities
            else ""
        )
        parts.append(
            f"tool: {summary.name}\nlayer: {summary.layer}{caps}\n"
            f"description: {summary.description or '(none)'}"
        )
    return "\n\n".join(parts)


async def propose_capabilities(
    tool_summaries: Sequence[ToolSummary],
    *,
    vocabulary: AbstractSet[str] = frozenset(),
    base_url: str | None = None,
    model: str | None = None,
    timeout_seconds: float | None = None,
    dotenv_path: str | Path | None = None,
    _http: Any = None,
) -> CapabilityProposalSet:
    """One batched LLM call proposing capabilities for every tool.

    Invalid capability names (per the framework grammar) are dropped and
    reported in ``invalid_dropped`` — never silently kept, never written
    anywhere.
    """
    if not tool_summaries:
        raise ProposalError("propose_capabilities needs at least one tool")
    endpoint = (
        env_string("LLM_BASE_URL", DEFAULT_BASE_URL, dotenv_path=dotenv_path)
        if base_url is None
        else base_url
    ).rstrip("/")
    selected_model = model or DEFAULT_MODEL
    timeout = (
        env_float(
            "LLM_TIMEOUT_SECONDS",
            DEFAULT_TIMEOUT_SECONDS,
            dotenv_path=dotenv_path,
        )
        if timeout_seconds is None
        else timeout_seconds
    )

    payload = {
        "model": selected_model,
        "stream": False,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _system_prompt(vocabulary)},
            {"role": "user", "content": _user_prompt(tool_summaries)},
        ],
    }
    body = await asyncio.to_thread(
        _request, payload, endpoint, timeout, _http
    )
    return _parse(body, tool_summaries)


def _request(payload, endpoint: str, timeout: float, http) -> Any:
    if http is not None:
        return http(payload)
    request = urllib.request.Request(
        f"{endpoint}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ProposalError(f"proposal HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProposalError(f"proposal request failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ProposalError(f"proposal endpoint non-JSON: {exc}") from exc


def _parse(
    body: Any, summaries: Sequence[ToolSummary]
) -> CapabilityProposalSet:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProposalError("proposal response missing message content") from exc
    if not isinstance(content, str):
        raise ProposalError("proposal message content is not a string")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ProposalError(f"proposal returned non-JSON: {exc}") from exc
    if not isinstance(parsed, Mapping) or not isinstance(
        parsed.get("proposals"), list
    ):
        raise ProposalError(
            "proposal JSON must be {'proposals': [...]}"
        )

    known = {summary.name for summary in summaries}
    proposals: list[CapabilityProposal] = []
    dropped: list[DroppedProposal] = []
    for item in parsed["proposals"]:
        if not isinstance(item, Mapping):
            continue
        tool = str(item.get("tool", ""))
        if tool not in known:
            dropped.append(DroppedProposal(tool=tool, capability="<whole entry>"))
            continue
        raw_caps = item.get("capabilities", [])
        if not isinstance(raw_caps, list):
            raw_caps = []
        valid: list[str] = []
        for capability in raw_caps:
            try:
                valid.append(validate_capability_name(str(capability)))
            except InvalidCapabilityError:
                dropped.append(
                    DroppedProposal(tool=tool, capability=str(capability))
                )
        rationale = item.get("rationale")
        confidence = item.get("confidence", 1.0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            confidence = 1.0
        proposals.append(
            CapabilityProposal(
                tool=tool,
                capabilities=tuple(sorted(set(valid))),
                rationale=(
                    str(rationale).strip()
                    if isinstance(rationale, str) and rationale.strip()
                    else None
                ),
                confidence=min(1.0, max(0.0, float(confidence))),
            )
        )
    ordered = tuple(
        sorted(proposals, key=lambda item: item.tool)
    )
    return CapabilityProposalSet(
        proposals=ordered, invalid_dropped=tuple(dropped)
    )


def render_capability_diff(
    proposal_set: CapabilityProposalSet,
    *,
    vocabulary: AbstractSet[str] = frozenset(),
) -> str:
    """The human-review artifact: proposed / reused / dropped, per tool."""
    lines = ["Capability proposal diff (review before applying)", ""]
    if not proposal_set.proposals:
        lines.append("(no proposals)")
    for proposal in proposal_set.proposals:
        if not proposal.capabilities:
            lines.append(f"{proposal.tool}: (no capabilities proposed)")
            continue
        rendered = []
        for capability in proposal.capabilities:
            mark = " [vocab]" if capability in vocabulary else " [new]"
            rendered.append(f"{capability}{mark}")
        rationale = (
            f"  — {proposal.rationale}" if proposal.rationale else ""
        )
        lines.append(f"{proposal.tool}: {', '.join(rendered)}{rationale}")
    if proposal_set.invalid_dropped:
        lines.append("")
        lines.append("Invalid (dropped, will not be applied):")
        for item in proposal_set.invalid_dropped:
            lines.append(f"  {item.tool}: {item.capability}")
    lines.append("")
    lines.append(
        "Nothing is written until `onboard apply` receives an explicit "
        "approved mapping."
    )
    return "\n".join(lines)
