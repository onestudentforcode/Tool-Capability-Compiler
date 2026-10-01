"""Export the sandbox refund topology to a Loader-compatible executable JSON.

The Python world in ``refund.py`` is the single source of truth for layers,
whitelists, capabilities and costs; this script serializes it (plus
``implementation`` entry points) into ``examples/topology/refund_sandbox.json``
so the CLI chain (optimize validate/commit, slow regression, online select)
can execute the very same sandbox tools from a JSON topology.

Usage::

    python export_topology.py            # writes ../topology/refund_sandbox.json
    python export_topology.py --out path/to/file.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent / "src"))

import refund  # noqa: E402

_IMPLEMENTATION_MODULE = "examples.slow_refund.refund"


def _selector_names(selector) -> list[str] | None:
    """Explicit whitelist names, or None when the selector means 'all'."""
    if selector.all_nodes:
        return None
    return sorted(selector.names)


def build_payload() -> dict:
    topology, version = refund.build_topology()
    tools = []
    for name in topology.nodes():
        spec = topology.node(name).spec
        item = {
            "name": spec.name,
            "layer": spec.layer,
            "implementation": f"{_IMPLEMENTATION_MODULE}:{spec.name}",
        }
        providers = _selector_names(spec.providers)
        if providers is not None:
            item["providers"] = providers
        workers = _selector_names(spec.workers)
        if workers is not None:
            item["workers"] = workers
        if spec.capabilities:
            item["capabilities"] = sorted(spec.capabilities)
        if spec.description:
            item["description"] = spec.description
        if spec.cost_per_call is not None:
            item["cost_per_call"] = spec.cost_per_call
        tools.append(item)
    layers = [
        {"name": layer.name, "order": layer.order}
        for layer in topology.layers()
    ]
    return {
        "version": version,
        "layers": layers,
        "tools": tools,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(_HERE.parent / "topology" / "refund_sandbox.json"),
    )
    args = parser.parse_args(argv)

    payload = build_payload()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"topology written to {out_path} ({len(payload['tools'])} tools)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
