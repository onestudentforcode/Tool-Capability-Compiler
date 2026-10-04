"""Export the office battlefield topology to a Loader-compatible JSON.

The Python world in the office modules is the single source of truth for
layers, whitelists, capabilities and costs; this script serializes it (plus
``implementation`` entry points) into ``examples/topology/office.json`` so
the CLI chain (regression slow, optimize, rank, select) executes the very
same tools from a JSON topology.

Usage::

    python export_topology.py            # writes ../topology/office.json
    python export_topology.py --out path/to/file.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for _path in (str(_ROOT / "src"), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Package import only (never a plain ``import office``): plain-name imports
# would cache examples/office modules under shared names and shadow the
# slow_refund sandbox modules in any process that also imports them.
from examples.office import office  # noqa: E402
from capability_runtime import ToolNode  # noqa: E402

_IMPLEMENTATION_PACKAGE = "examples.office"
# Tool modules carrying ``implementation`` bindings (L0 readers, deterministic
# layer, LLM layer).
_MODULES = ("tools_l0", "tools_det", "tools_llm")


def _implementation_map() -> dict[str, str]:
    """Map every exported ToolNode to its ``module:attr`` binding string."""
    mapping: dict[str, str] = {}
    for module_name in _MODULES:
        module = importlib.import_module(f"{_IMPLEMENTATION_PACKAGE}.{module_name}")
        for attr in sorted(dir(module)):
            value = getattr(module, attr)
            if isinstance(value, ToolNode):
                if value.spec.name in mapping:
                    raise RuntimeError(f"duplicate tool node name: {value.spec.name}")
                mapping[value.spec.name] = (
                    f"{_IMPLEMENTATION_PACKAGE}.{module_name}:{attr}"
                )
    return mapping


def _selector_names(selector) -> list[str] | None:
    """Explicit whitelist names, or None when the selector means 'all'."""
    if selector.all_nodes:
        return None
    return sorted(selector.names)


def _type_ref(value_type) -> str:
    """Auto-derive the ``module:attr`` reference for a domain type."""
    return f"{value_type.__module__}:{value_type.__qualname__}"


def _inner_topology_payload(spec) -> dict:
    """Serialize one composite's inner topology (tools with implementations)."""
    inner = spec.topology
    tools = []
    for name in inner.nodes():
        node_spec = inner.node(name).spec
        item = {
            "name": node_spec.name,
            "layer": node_spec.layer,
            "implementation": f"{_IMPLEMENTATION_PACKAGE}.composite_inner:{name}",
        }
        if node_spec.consumes:
            item["consumes"] = [_type_ref(t) for t in node_spec.consumes]
        if node_spec.produces:
            item["produces"] = [_type_ref(t) for t in node_spec.produces]
        if node_spec.capabilities:
            item["capabilities"] = sorted(node_spec.capabilities)
        if node_spec.description:
            item["description"] = node_spec.description
        if node_spec.cost_per_call is not None:
            item["cost_per_call"] = node_spec.cost_per_call
        tools.append(item)
    return {
        "version": f"inner-{spec.name}",
        "layers": [
            {"name": layer.name, "order": layer.order} for layer in inner.layers()
        ],
        "tools": tools,
    }


def _composite_item(spec) -> dict:
    """The outer composite entry (kind=composite) referencing the inner file."""
    route = []
    for group in spec.route:
        first = spec.topology.node(next(iter(group)))
        route.append({"layer": first.spec.layer, "tools": list(group)})
    item = {
        "name": spec.name,
        "layer": spec.layer,
        "kind": "composite",
        "inner": f"{spec.name}_inner.json",
        "route": route,
        "stop_when": list(spec.stop_when),
        "max_iterations": spec.max_iterations,
    }
    if spec.consumes:
        item["consumes"] = [_type_ref(t) for t in spec.consumes]
    if spec.produces:
        item["produces"] = [_type_ref(t) for t in spec.produces]
    if spec.capabilities:
        item["capabilities"] = sorted(spec.capabilities)
    if spec.description:
        item["description"] = spec.description
    if spec.cost_per_call is not None:
        item["cost_per_call"] = spec.cost_per_call
    return item


def build_payload() -> dict:
    from examples.office import composite_nodes

    topology, version = office.build_topology()
    implementations = _implementation_map()
    composites = dict(composite_nodes.SPECS)
    missing = sorted(set(topology.nodes()) - set(composites) - set(implementations))
    if missing:
        raise RuntimeError(
            "no implementation binding for tools: " + ", ".join(missing)
        )
    tools = []
    for name in topology.nodes():
        spec = topology.node(name).spec
        if name in composites:
            tools.append(_composite_item(composites[name]))
            continue
        item = {
            "name": spec.name,
            "layer": spec.layer,
            "implementation": implementations[name],
        }
        if spec.consumes:
            item["consumes"] = [_type_ref(t) for t in spec.consumes]
        if spec.produces:
            item["produces"] = [_type_ref(t) for t in spec.produces]
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


def write_inner_payloads(out_dir: Path) -> list[Path]:
    """Write each composite's inner topology JSON next to the main payload."""
    from examples.office import composite_nodes

    written = []
    for name, spec in sorted(composite_nodes.SPECS.items()):
        path = out_dir / f"{name}_inner.json"
        path.write_text(
            json.dumps(_inner_topology_payload(spec), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(_HERE.parent / "topology" / "office.json"),
    )
    args = parser.parse_args(argv)

    payload = build_payload()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    inner_paths = write_inner_payloads(out_path.parent)
    print(
        f"topology written to {out_path} ({len(payload['tools'])} tools, "
        f"{len(inner_paths)} composite inner files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
