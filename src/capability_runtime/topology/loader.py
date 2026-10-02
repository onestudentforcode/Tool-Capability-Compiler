"""Declarative Topology loader for metadata-only Fast Regression.

Fast Regression never executes tools, so a topology can be described purely by
its layer/tool metadata and loaded without real function implementations. Tools
default to a placeholder async handler that is never invoked (phase2.md
section 2).

For Slow Regression a tool may instead declare
``"implementation": "module.path:attr"`` to bind a real async handler from an
importable module (battlefield-hardening batch B). This is an offline
battlefield assembly mechanism, not an online plugin loader: it only resolves
an explicit ``module:attr`` entry point inside an already-importable module.
"""

from __future__ import annotations

import importlib
import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..core.capability import validate_capability_name
from ..core.errors import (
    InvalidCapabilityError,
    TopologyBuildError,
)
from ..core.tool import NodeSelector, ToolNode, ToolSpec
from ..registry.layer_registry import LayerRegistry
from ..registry.tool_registry import ToolRegistry
from .builder import TopologyBuilder
from .models import Topology

_SUITE_FIELDS = {"version", "layers", "tools"}
_LAYER_FIELDS = {"name", "order"}
_TOOL_FIELDS = {
    "name",
    "layer",
    "providers",
    "workers",
    "capabilities",
    "description",
    "cost_per_call",
    "implementation",
    "consumes",
    "produces",
}
_COMPOSITE_FIELDS = {
    "name",
    "layer",
    "kind",
    "inner",
    "route",
    "stop_when",
    "max_iterations",
    "capabilities",
    "description",
    "cost_per_call",
}


async def _null_handler(*args: Any, **kwargs: Any) -> None:
    return None


def unbound_tool_names(topology: Topology) -> tuple[str, ...]:
    """Names of tools still carrying the loader's null placeholder handler.

    A slow regression must refuse these: executing the placeholder silently
    produces None outputs that poison the statistics.
    """
    return tuple(
        name
        for name in topology.nodes()
        if topology.node(name).handler is _null_handler
    )


def stop_while_ok(value) -> bool:
    return all(isinstance(slot, str) and slot.strip() for slot in value)


class TopologyLoader:
    """Strict JSON loader for a declarative tool topology."""

    def load_file(self, path: str | Path) -> Topology:
        source = Path(path)
        try:
            raw = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise TopologyBuildError(f"Cannot read topology file: {source}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TopologyBuildError(
                f"Invalid topology JSON at line {exc.lineno}, column {exc.colno}"
            ) from exc
        return self.load_data(data, source)

    def load_data(self, data: object, source: Path | None = None) -> Topology:
        location = f"Topology{(' file ' + str(source)) if source else ''}"
        root = self._require_mapping(data, location)
        self._reject_unknown_fields(root, _SUITE_FIELDS, location)
        self._require_fields(root, {"layers", "tools"}, location)

        layers = LayerRegistry()
        for index, item in enumerate(root["layers"]):
            layer = self._layer(item, index, location)
            layers.register(layer[0], layer[1])

        tools = ToolRegistry()
        for index, item in enumerate(root["tools"]):
            item_location = f"{location} tools[{index}]"
            if isinstance(item, Mapping) and item.get("kind") == "composite":
                tools.register(self._composite_node(item, item_location, source))
                continue
            if isinstance(item, Mapping) and "kind" in item:
                raise TopologyBuildError(
                    f"{item_location} 'kind' must be 'composite' if present"
                )
            spec = self._tool(item, index, location)
            handler = self._resolve_handler(
                item.get("implementation"), spec.name, item_location
            )
            tools.register(ToolNode(spec=spec, handler=handler))
        return TopologyBuilder(layers, tools).build()

    def _composite_node(self, item, location: str, source: Path | None):
        from ..composite.spec import CompositeSpec, build_composite_node

        self._reject_unknown_fields(item, _COMPOSITE_FIELDS, location)
        self._require_fields(
            item,
            {"name", "layer", "inner", "route", "stop_when", "max_iterations"},
            location,
        )
        for unsupported in ("providers", "workers", "consumes", "produces"):
            if unsupported in item:
                raise TopologyBuildError(
                    f"{location} composite entries do not support "
                    f"{unsupported!r} (outer edges default to all; typed "
                    "contracts are Python-path only)"
                )
        inner_raw = Path(str(item["inner"]))
        if not inner_raw.is_absolute() and source is not None:
            inner_raw = source.parent / inner_raw
        try:
            inner_topology = self.load_file(inner_raw)
        except TopologyBuildError as exc:
            raise TopologyBuildError(
                f"{location} cannot load inner topology: {exc}"
            ) from exc
        unbound = unbound_tool_names(inner_topology)
        if unbound:
            raise TopologyBuildError(
                f"{location} inner topology has tools without an executable "
                f"implementation: {', '.join(unbound)}"
            )
        route_raw = item["route"]
        if not isinstance(route_raw, list) or not route_raw:
            raise TopologyBuildError(
                f"{location} 'route' must be a non-empty list of "
                "{layer, tools} segments"
            )
        route: list[tuple[str, ...]] = []
        for seg_index, segment in enumerate(route_raw):
            if (
                not isinstance(segment, dict)
                or set(segment) != {"layer", "tools"}
                or not isinstance(segment["tools"], list)
                or not segment["tools"]
            ):
                raise TopologyBuildError(
                    f"{location} route[{seg_index}] must be "
                    "{{'layer': ..., 'tools': [...]}}"
                )
            route.append(tuple(str(tool) for tool in segment["tools"]))
        stop_when = item["stop_when"]
        if not isinstance(stop_when, list) or not stop_while_ok(stop_when):
            raise TopologyBuildError(
                f"{location} 'stop_when' must be a non-empty list of slots"
            )
        max_iterations = item["max_iterations"]
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 1:
            raise TopologyBuildError(
                f"{location} 'max_iterations' must be a positive integer"
            )
        try:
            spec = CompositeSpec(
                name=str(item["name"]),
                layer=str(item["layer"]),
                topology=inner_topology,
                route=tuple(route),
                stop_when=tuple(str(slot) for slot in stop_when),
                max_iterations=max_iterations,
                capabilities=frozenset(str(cap) for cap in item.get("capabilities", [])),
                cost_per_call=item.get("cost_per_call"),
                description=str(item.get("description", "")),
            )
        except Exception as exc:  # noqa: BLE001 - wrap spec validation uniformly
            raise TopologyBuildError(f"{location} invalid composite: {exc}") from exc
        return build_composite_node(spec)

    @staticmethod
    def _resolve_handler(
        reference: object, tool_name: str, location: str
    ) -> Any:
        if reference is None:
            return _null_handler
        if not isinstance(reference, str) or reference.count(":") != 1:
            raise TopologyBuildError(
                f"{location} 'implementation' for tool {tool_name!r} must be "
                "a 'module:attr' entry point"
            )
        module_name, _, attr = reference.partition(":")
        module_name = module_name.strip()
        attr = attr.strip()
        if not module_name or not attr.isidentifier():
            raise TopologyBuildError(
                f"{location} 'implementation' {reference!r} for tool "
                f"{tool_name!r} must be a 'module:attr' entry point"
            )
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise TopologyBuildError(
                f"{location} cannot import module {module_name!r} for tool "
                f"{tool_name!r}: {exc}"
            ) from exc
        handler = getattr(module, attr, None)
        if isinstance(handler, ToolNode):
            # a ``@tool``-decorated attribute exposes its raw async function
            handler = handler.handler
        if handler is None:
            raise TopologyBuildError(
                f"{location} module {module_name!r} has no attribute "
                f"{attr!r} for tool {tool_name!r}"
            )
        if not callable(handler):
            raise TopologyBuildError(
                f"{location} implementation {reference!r} for tool "
                f"{tool_name!r} is not callable"
            )
        if not inspect.iscoroutinefunction(handler):
            raise TopologyBuildError(
                f"{location} implementation {reference!r} for tool "
                f"{tool_name!r} must be an async function"
            )
        return handler

    @classmethod
    def _layer(cls, data: object, index: int, parent: str) -> tuple[str, int]:
        location = f"{parent} layers[{index}]"
        item = cls._require_mapping(data, location)
        cls._reject_unknown_fields(item, _LAYER_FIELDS, location)
        cls._require_fields(item, {"name", "order"}, location)
        name = item["name"]
        order = item["order"]
        if not isinstance(name, str) or not name.strip():
            raise TopologyBuildError(f"{location} 'name' must be a non-empty string")
        if isinstance(order, bool) or not isinstance(order, int):
            raise TopologyBuildError(f"{location} 'order' must be an integer")
        return name, order

    @classmethod
    def _tool(cls, data: object, index: int, parent: str) -> ToolSpec:
        location = f"{parent} tools[{index}]"
        item = cls._require_mapping(data, location)
        cls._reject_unknown_fields(item, _TOOL_FIELDS, location)
        cls._require_fields(item, {"name", "layer"}, location)
        name = item["name"]
        layer = item["layer"]
        if not isinstance(name, str) or not name.strip():
            raise TopologyBuildError(f"{location} 'name' must be a non-empty string")
        if not isinstance(layer, str) or not layer.strip():
            raise TopologyBuildError(f"{location} 'layer' must be a non-empty string")
        providers = cls._selector(item.get("providers", "all"), location, "providers")
        workers = cls._selector(item.get("workers", "all"), location, "workers")
        capabilities = cls._capabilities(item.get("capabilities", []), location)
        description = item.get("description", "")
        if not isinstance(description, str):
            raise TopologyBuildError(f"{location} 'description' must be a string")
        cost_per_call = cls._cost_per_call(item.get("cost_per_call"), location)
        consumes = cls._type_references(item.get("consumes"), location, "consumes")
        produces = cls._type_references(item.get("produces"), location, "produces")
        return ToolSpec(
            name=name,
            layer=layer,
            providers=providers,
            workers=workers,
            capabilities=capabilities,
            consumes=consumes,
            produces=produces,
            description=description.strip(),
            cost_per_call=cost_per_call,
        )

    @staticmethod
    def _type_references(
        value: object, location: str, field: str
    ) -> tuple[type, ...]:
        """Resolve optional ``"module:attr"`` type references to classes.

        Restores the schema (consumes/produces) of a JSON-declared tool so
        the Python and JSON construction paths behave identically — the
        same trust model as ``implementation``: only explicit, importable
        entry points are resolved. Absent field -> empty tuple.
        """
        if value is None:
            return ()
        if not isinstance(value, list):
            raise TopologyBuildError(
                f"{location} '{field}' must be a list of 'module:attr' type references"
            )
        resolved: list[type] = []
        for index, reference in enumerate(value):
            item_location = f"{location} '{field}'[{index}]"
            if not isinstance(reference, str) or reference.count(":") != 1:
                raise TopologyBuildError(
                    f"{item_location} must be a 'module:attr' type reference"
                )
            module_name, _, attr = reference.partition(":")
            module_name = module_name.strip()
            attr = attr.strip()
            try:
                module = importlib.import_module(module_name)
                resolved_type = getattr(module, attr)
            except (ImportError, AttributeError) as exc:
                raise TopologyBuildError(
                    f"{item_location} cannot resolve type reference "
                    f"{reference!r}: {exc}"
                ) from exc
            if not inspect.isclass(resolved_type):
                raise TopologyBuildError(
                    f"{item_location} {reference!r} is not a class"
                )
            resolved.append(resolved_type)
        return tuple(resolved)

    @staticmethod
    def _cost_per_call(value: object, location: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise TopologyBuildError(
                f"{location} 'cost_per_call' must be a non-negative number"
            )
        return float(value)

    @staticmethod
    def _selector(value: object, location: str, field: str) -> NodeSelector:
        if value == "all":
            return NodeSelector(all_nodes=True)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise TopologyBuildError(
                f"{location} '{field}' must be 'all' or a list of tool names"
            )
        if len(set(value)) != len(value):
            raise TopologyBuildError(f"{location} '{field}' contains duplicates")
        return NodeSelector(all_nodes=False, names=frozenset(value))

    @staticmethod
    def _capabilities(value: object, location: str) -> frozenset[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise TopologyBuildError(
                f"{location} 'capabilities' must be a list of strings"
            )
        try:
            return frozenset(validate_capability_name(item) for item in value)
        except InvalidCapabilityError as exc:
            raise TopologyBuildError(
                f"{location} 'capabilities' contains an invalid capability"
            ) from exc

    @staticmethod
    def _require_mapping(value: object, location: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping) or any(
            not isinstance(key, str) for key in value
        ):
            raise TopologyBuildError(f"{location} must be a JSON object")
        return value

    @staticmethod
    def _require_fields(value: Mapping[str, Any], required: set[str], location: str) -> None:
        missing = sorted(required - set(value))
        if missing:
            raise TopologyBuildError(
                f"{location} is missing required fields: {', '.join(missing)}"
            )

    @staticmethod
    def _reject_unknown_fields(
        value: Mapping[str, Any], allowed: set[str], location: str
    ) -> None:
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise TopologyBuildError(
                f"{location} has unknown fields: {', '.join(unknown)}"
            )