"""Declarative Topology loader for metadata-only Fast Regression.

Fast Regression never executes tools, so a topology can be described purely by
its layer/tool metadata and loaded without real function implementations. This
loader builds the same :class:`Topology` the registry path produces, using a
placeholder async handler that is never invoked (phase2.md section 2).
"""

from __future__ import annotations

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
}


async def _null_handler(*args: Any, **kwargs: Any) -> None:
    return None


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
            spec = self._tool(item, index, location)
            tools.register(ToolNode(spec=spec, handler=_null_handler))
        return TopologyBuilder(layers, tools).build()

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
        return ToolSpec(
            name=name,
            layer=layer,
            providers=providers,
            workers=workers,
            capabilities=capabilities,
            description=description.strip(),
        )

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