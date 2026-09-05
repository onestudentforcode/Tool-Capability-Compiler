"""Prompt construction for the LLM layer router (Step 12).

Builds the system + user prompts handed to a local Ollama model so it can pick
which available tools to run for the current layer. The prompt only exposes the
current layer's reachable tools, never the whole topology (phase3 §20, §126).

The LLM JSON contract is:

    {"action": "execute", "selected_tools": ["db", "rag"], "reason": "..."}

or:

    {"action": "finish", "reason": "..."}
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .models import ToolSummary


@dataclass(frozen=True, slots=True)
class RouterPrompt:
    system: str
    user: str


def build_router_prompt(
    *,
    query: str,
    layer: str,
    available_tools: Sequence[ToolSummary],
    state_summary: Any = None,
    previous_layers: Sequence[str] = (),
    max_tools_per_layer: int,
    exploration_mode: str = "free",
) -> RouterPrompt:
    """Compose the routing prompt for one layer.

    ``previous_layers`` is a sequence of already-visited layer names (not raw
    LayerExecution objects) to keep this module independent of the slow trace
    types.
    """
    system = (
        "You are the layer router of a layered tool-execution framework. "
        "For the current layer only, decide which of the AVAILABLE tools to run "
        "to make progress on the business goal. Rules: you may only pick tools "
        f"from the AVAILABLE list; pick at most {max_tools_per_layer} tools; "
        "you may pick one or more tools, or stop with action 'finish' when the "
        "goal is satisfied or cannot be furthered. You do not judge the final "
        "business outcome. Respond ONLY with a single JSON object and no prose, "
        "having exactly these keys: action (either 'execute' or 'finish'), "
        "selected_tools (array of tool names; empty when action is 'finish'), "
        "reason (short string)."
    )

    lines: list[str] = []
    lines.append(f"Layer to route: {layer}")
    lines.append(f"Business goal: {query}")
    if previous_layers:
        lines.append("Layers already executed: " + ", ".join(previous_layers))
    if state_summary is not None:
        lines.append(f"Current state: {state_summary}")
    lines.append("AVAILABLE tools for this layer:")
    if available_tools:
        for summary in sorted(available_tools, key=lambda item: item.name):
            desc = f"- {summary.name}"
            if summary.description:
                desc += f": {summary.description}"
            if summary.capabilities:
                desc += " [capabilities: " + ", ".join(sorted(summary.capabilities)) + "]"
            lines.append(desc)
    else:
        lines.append("(none)")

    if exploration_mode == "guided":
        lines.append(
            "Exploration mode: guided — prefer the suggested route hint when it "
            "helps the goal, but you are allowed to deviate."
        )
    elif exploration_mode == "free":
        lines.append(
            "Exploration mode: free — choose the tools that best make progress."
        )

    return RouterPrompt(system=system, user="\n".join(lines))