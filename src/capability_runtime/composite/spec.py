"""CompositeSpec and the composite-node factory (composite-nodes milestone §4).

A composite node is a *tool factory*: ``build_composite_node`` yields a
standard :class:`ToolNode` whose handler runs a bounded inner route over an
inner topology. Registration, edge building, coverage, execution, ranking and
online routing treat it exactly like any other tool — zero special branches.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from ..core.capability import validate_capability_name
from ..core.errors import CompositeSpecError, RegistrationError
from ..core.tool import NodeSelector, ToolNode, ToolSpec
from ..topology.models import Topology
from ..route.models import RoutePlan

MAX_COMPOSITE_DEPTH = 2  # 保留裁定（2026-09）：嵌套复合深度上限暂为 2

# name -> composite depth, populated by build_composite_node; used to police
# nesting depth and reject self/indirect reference at construction time
composite_depth_by_name: dict[str, int] = {}


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


@dataclass(frozen=True, slots=True)
class CompositeSpec:
    """Declaration of a composite (macro) node.

    ``route`` is per-layer tool sets in inner-layer order; validated as a
    RoutePlan against ``topology``. ``stop_when`` are inner blackboard slots:
    the loop stops when all of them exist (ALL semantics).
    """

    name: str
    layer: str
    topology: Topology
    route: tuple[tuple[str, ...], ...]
    stop_when: tuple[str, ...]
    max_iterations: int
    consumes: tuple[type, ...] = ()
    produces: tuple[type, ...] = ()
    capabilities: frozenset[str] = frozenset()
    cost_per_call: float | None = None
    description: str = ""
    inner_version: str = "declared"
    _depth: int = field(default=0, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise CompositeSpecError("composite name must be a non-empty string")
        if not isinstance(self.layer, str) or not self.layer.strip():
            raise CompositeSpecError(
                f"composite {self.name!r}: layer must be a non-empty string"
            )
        if isinstance(self.max_iterations, bool) or self.max_iterations < 1:
            raise CompositeSpecError(
                f"composite {self.name!r}: max_iterations must be a positive int"
            )
        if not self.stop_when or any(
            not isinstance(slot, str) or not slot.strip()
            for slot in self.stop_when
        ):
            raise CompositeSpecError(
                f"composite {self.name!r}: stop_when needs non-empty slot names"
            )
        if not self.route:
            raise CompositeSpecError(
                f"composite {self.name!r}: route cannot be empty"
            )
        if any(not isinstance(item, type) for item in (*self.consumes, *self.produces)):
            raise CompositeSpecError(
                f"composite {self.name!r}: consumes/produces must contain types"
            )
        if len(set(self.produces)) != len(self.produces):
            raise CompositeSpecError(
                f"composite {self.name!r}: cannot produce the same schema twice"
            )
        if self.cost_per_call is not None and (
            isinstance(self.cost_per_call, bool) or self.cost_per_call < 0
        ):
            raise CompositeSpecError(
                f"composite {self.name!r}: cost_per_call must be non-negative"
            )
        capabilities = frozenset(
            validate_capability_name(item) for item in self.capabilities
        )
        object.__setattr__(self, "capabilities", capabilities)
        self._validate_route()
        self._validate_depth()

    def _validate_route(self) -> None:
        try:
            RoutePlan.from_groups(self.topology, [set(g) for g in self.route])
        except Exception as exc:  # noqa: BLE001 - wrap any plan validation failure
            raise CompositeSpecError(
                f"composite {self.name!r}: route is not a valid plan on the "
                f"inner topology: {exc}"
            ) from exc

    def _validate_depth(self) -> None:
        inner_names = set(self.topology.nodes())
        if self.name in inner_names:
            raise CompositeSpecError(
                f"composite {self.name!r}: self-reference is forbidden"
            )
        inner_depth = max(
            (composite_depth_by_name.get(node, 0) for node in inner_names),
            default=0,
        )
        if inner_depth + 1 > MAX_COMPOSITE_DEPTH:
            raise CompositeSpecError(
                f"composite {self.name!r}: nesting depth would exceed "
                f"MAX_COMPOSITE_DEPTH={MAX_COMPOSITE_DEPTH}"
            )
        object.__setattr__(self, "_depth", inner_depth + 1)


def build_composite_node(spec: CompositeSpec) -> ToolNode:
    """Factory: a standard ToolNode whose handler runs the composite runtime.

    The handler is generated with one typed positional parameter per consumed
    schema so the executor's signature-driven argument resolution (name, then
    type annotation) injects the outer artifacts exactly like for @tool
    functions — the executor keeps zero composite-specific branches.
    """
    from .runtime import CompositeRuntime

    runtime = CompositeRuntime(spec)
    handler = _typed_handler(runtime, spec.consumes)
    tool_spec = ToolSpec(
        name=spec.name,
        layer=spec.layer,
        providers=NodeSelector(all_nodes=True),
        workers=NodeSelector(all_nodes=True),
        capabilities=spec.capabilities,
        consumes=spec.consumes,
        produces=spec.produces,
        description=spec.description.strip(),
        cost_per_call=spec.cost_per_call,
    )
    node = ToolNode(spec=tool_spec, handler=handler)
    composite_depth_by_name[spec.name] = spec._depth
    return node


def _typed_handler(runtime, consumes: tuple[type, ...]):
    """A generated closure whose parameters resolve like any @tool function.

    Parameter names follow the slot convention (snake_case of the consumed
    schema name) so the executor's name-first argument resolution hits
    directly — the same mechanism every sandbox tool relies on. Annotations
    are attached as plain namespace names for documentation; under Python
    3.14's lazy (PEP 649) annotations they may surface as strings, which is
    fine because resolution never depends on them.
    """
    names = [_snake(consumed.__name__) for consumed in consumes]
    namespace: dict[str, object] = {"__runtime__": runtime}
    params: list[str] = []
    for index, consumed in enumerate(consumes):
        type_name = f"__consumed_{index}__"
        namespace[type_name] = consumed
        params.append(f"{names[index]}: {type_name}")
    body = ", ".join(names)
    source = (
        "def _generated_handler(" + ", ".join(params) + "):\n"
        f"    return __runtime__.run({body})\n"
    )
    exec(source, namespace)  # noqa: S102 - generated from validated spec types
    return namespace["_generated_handler"]
