"""Apply reviewed capabilities: the only write path, gated on human approval.

The approved mapping is the review artifact — proposals have no parameter
path into this module. Every applied capability is grammar-checked, and the
resulting topology is re-validated through :class:`TopologyLoader` before
anything touches disk.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..core.capability import validate_capability_name
from ..core.errors import ApplyError, InvalidCapabilityError


def apply_capabilities(
    topology_payload: Mapping[str, Any],
    approved: Mapping[str, Sequence[str]],
    *,
    out_path: str | Path,
) -> Path:
    """Merge approved capabilities into a topology payload and write it.

    Gate semantics: every key in ``approved`` must name an existing tool
    (unknown tools are rejected — that is the review gate), capabilities are
    grammar-checked and unioned with whatever the tool already declares.
    """
    tools = topology_payload.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ApplyError("topology payload has no tools list")

    validated: dict[str, tuple[str, ...]] = {}
    for tool, capabilities in approved.items():
        clean: list[str] = []
        for capability in capabilities:
            try:
                clean.append(validate_capability_name(str(capability)))
            except InvalidCapabilityError as exc:
                raise ApplyError(
                    f"approved capability for {tool!r} is invalid: {exc}"
                ) from exc
        validated[str(tool)] = tuple(sorted(set(clean)))

    known = {
        str(item.get("name")) for item in tools if isinstance(item, Mapping)
    }
    unknown = sorted(set(validated) - known)
    if unknown:
        raise ApplyError(
            "approved references tools not in the topology: "
            + ", ".join(unknown)
        )

    merged = json.loads(json.dumps(dict(topology_payload)))
    for item in merged["tools"]:
        name = str(item.get("name", ""))
        if name not in validated:
            continue
        existing = list(item.get("capabilities", []))
        item["capabilities"] = sorted(set(existing) | set(validated[name]))

    _validate_roundtrip(merged)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return target


def _validate_roundtrip(payload: Mapping[str, Any]) -> None:
    from ..topology.loader import TopologyLoader

    try:
        TopologyLoader().load_data(payload)
    except Exception as exc:  # noqa: BLE001 - any loader failure blocks apply
        raise ApplyError(f"applied topology failed validation: {exc}") from exc
