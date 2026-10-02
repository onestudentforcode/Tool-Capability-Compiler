from __future__ import annotations

import inspect
import re

from ..core.errors import InvalidTopologyReferenceError, TopologyBuildError
from ..core.layer import Layer
from ..core.tool import NodeSelector, ToolNode
from ..registry import LayerRegistry, ToolRegistry
from .models import ToolEdge, Topology, TopologyValidationWarning


class TopologyBuilder:
    """Build adjacent-layer edges from the intersection of two allow-lists."""

    def __init__(self, layers: LayerRegistry, tools: ToolRegistry) -> None:
        self._layer_registry = layers
        self._tool_registry = tools

    def build(self) -> Topology:
        layers = self._layer_registry.all()
        self._validate_layer_order(layers)
        tools = self._tool_registry.all()
        nodes = {node.spec.name: node for node in tools}
        by_layer = {layer.name: self._tool_registry.in_layer(layer.name) for layer in layers}

        for node in tools:
            self._layer_registry.get(node.spec.layer)
        self._validate_references(layers, nodes)

        edges: list[ToolEdge] = []
        warnings: list[TopologyValidationWarning] = []
        for source_layer, target_layer in zip(layers, layers[1:], strict=False):
            for source in by_layer[source_layer.name]:
                for target in by_layer[target_layer.name]:
                    if not self._allows(source, target):
                        continue
                    edges.append(ToolEdge(source.spec.name, target.spec.name))
                    warning = self._schema_warning(source, target)
                    if warning is not None:
                        warnings.append(warning)
        warnings.extend(self._unsatisfiable_input_warnings(layers, nodes))
        warnings.extend(self._slot_name_conflict_warnings(nodes))
        return Topology(layers, nodes, tuple(edges), tuple(warnings))

    @staticmethod
    def _unsatisfiable_input_warnings(
        layers: tuple[Layer, ...], nodes: dict[str, ToolNode]
    ) -> list[TopologyValidationWarning]:
        """Flag consumed types with no producer in an earlier layer.

        Same-layer tools run concurrently and cannot see each other's
        outputs, later layers have not executed yet, and a missing producer
        is unresolvable outright — in all three cases the tool can never
        run successfully (office-battlefield-notes.md §1.1).
        """
        layer_order = {layer.name: layer.order for layer in layers}
        producers: dict[type, set[tuple[str, int]]] = {}
        for name, node in nodes.items():
            for produced in node.spec.produces:
                producers.setdefault(produced, set()).add(
                    (name, layer_order[node.spec.layer])
                )
        warnings: list[TopologyValidationWarning] = []
        for name in sorted(nodes):
            node = nodes[name]
            consumer_order = layer_order[node.spec.layer]
            for consumed in sorted(node.spec.consumes, key=lambda t: t.__name__):
                candidates = producers.get(consumed, set())
                if any(order < consumer_order for _, order in candidates):
                    continue
                if candidates:
                    reason = (
                        "its producers all live in the same or a later layer"
                    )
                else:
                    reason = "no tool in the topology produces this type"
                warnings.append(
                    TopologyValidationWarning(
                        code="UNSATISFIABLE_INPUT",
                        source=name,
                        target=consumed.__name__,
                        message=(
                            f"Tool {name!r} consumes {consumed.__name__} but "
                            f"{reason}: same-layer tools run concurrently and "
                            "cannot see each other's outputs, and later layers "
                            "have not executed yet. Move a producer to an "
                            "earlier layer."
                        ),
                    )
                )
        return warnings

    @staticmethod
    def _slot_name_conflict_warnings(
        nodes: dict[str, ToolNode]
    ) -> list[TopologyValidationWarning]:
        """Flag handler parameters whose name hijacks another type's slot.

        Argument resolution is name-first: a parameter named like a
        different type's slot binds that slot's artifact instead of the
        annotated type (executor.py _lookup_by_name). The declaration is
        only conflict-free when the parameter name equals its own type's
        slot name.
        """
        def snake(name: str) -> str:
            return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

        slot_owner: dict[str, type] = {}
        for node in nodes.values():
            for produced in node.spec.produces:
                slot_owner.setdefault(snake(produced.__name__), produced)
        warnings: list[TopologyValidationWarning] = []
        for name in sorted(nodes):
            handler = nodes[name].handler
            try:
                signature = inspect.signature(handler)
            except (TypeError, ValueError):
                continue
            for param_name, param in signature.parameters.items():
                annotation = param.annotation
                # inspect.Parameter.empty is itself a class — unannotated
                # parameters must be skipped explicitly.
                if annotation is inspect.Parameter.empty:
                    continue
                if not inspect.isclass(annotation):
                    continue
                expected = snake(annotation.__name__)
                if param_name == expected:
                    continue
                owner = slot_owner.get(param_name)
                if owner is None or owner is annotation:
                    continue
                warnings.append(
                    TopologyValidationWarning(
                        code="SLOT_NAME_CONFLICT",
                        source=name,
                        target=param_name,
                        message=(
                            f"Tool {name!r} parameter {param_name!r} is "
                            f"annotated {annotation.__name__} but name-first "
                            f"resolution binds the slot {param_name!r} of "
                            f"{owner.__name__}; rename the parameter to "
                            f"{expected!r}."
                        ),
                    )
                )
        return warnings

    @staticmethod
    def _validate_layer_order(layers: tuple[Layer, ...]) -> None:
        for left, right in zip(layers, layers[1:], strict=False):
            if right.order != left.order + 1:
                raise TopologyBuildError(
                    f"Layer orders must be contiguous: {left.order} -> {right.order}"
                )

    @staticmethod
    def _allows(source: ToolNode, target: ToolNode) -> bool:
        return source.spec.workers.allows(target.spec.name) and target.spec.providers.allows(
            source.spec.name
        )

    def _validate_references(
        self, layers: tuple[Layer, ...], nodes: dict[str, ToolNode]
    ) -> None:
        layer_indexes = {layer.name: index for index, layer in enumerate(layers)}
        for node in nodes.values():
            index = layer_indexes[node.spec.layer]
            self._validate_selector(
                owner=node,
                selector=node.spec.providers,
                expected_layer=layers[index - 1].name if index > 0 else None,
                relation="provider",
                nodes=nodes,
            )
            self._validate_selector(
                owner=node,
                selector=node.spec.workers,
                expected_layer=layers[index + 1].name if index + 1 < len(layers) else None,
                relation="worker",
                nodes=nodes,
            )

    @staticmethod
    def _validate_selector(
        *,
        owner: ToolNode,
        selector: NodeSelector,
        expected_layer: str | None,
        relation: str,
        nodes: dict[str, ToolNode],
    ) -> None:
        if selector.all_nodes:
            return
        if expected_layer is None and selector.names:
            raise InvalidTopologyReferenceError(
                f"Tool {owner.spec.name} cannot declare {relation}s outside the topology"
            )
        for reference in sorted(selector.names):
            referenced = nodes.get(reference)
            if referenced is None:
                raise InvalidTopologyReferenceError(
                    f"Tool {owner.spec.name} references unknown {relation}: {reference}"
                )
            if referenced.spec.layer != expected_layer:
                raise InvalidTopologyReferenceError(
                    f"Tool {owner.spec.name} references {relation} {reference} in "
                    f"non-adjacent layer {referenced.spec.layer}"
                )

    @staticmethod
    def _schema_warning(
        source: ToolNode, target: ToolNode
    ) -> TopologyValidationWarning | None:
        if not source.spec.produces or not target.spec.consumes:
            return None
        if set(source.spec.produces).intersection(target.spec.consumes):
            return None
        source_types = ", ".join(item.__name__ for item in source.spec.produces)
        target_types = ", ".join(item.__name__ for item in target.spec.consumes)
        return TopologyValidationWarning(
            code="SCHEMA_MISMATCH",
            source=source.spec.name,
            target=target.spec.name,
            message=(
                f"Allowed edge {source.spec.name} -> {target.spec.name} has no exact "
                f"schema overlap ({source_types} -> {target_types})"
            ),
        )
