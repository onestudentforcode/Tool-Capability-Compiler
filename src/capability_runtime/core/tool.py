from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from .errors import RegistrationError

SelectorInput: TypeAlias = Literal["all"] | Iterable[str]


@dataclass(frozen=True, slots=True)
class NodeSelector:
    """A deterministic allow-list; `all_nodes=True` means unrestricted."""

    all_nodes: bool
    names: frozenset[str] = frozenset()

    @classmethod
    def parse(cls, value: SelectorInput) -> NodeSelector:
        if value == "all":
            return cls(all_nodes=True)
        if isinstance(value, str):
            raise RegistrationError("Selector must be 'all' or an iterable of tool names")
        names = frozenset(value)
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise RegistrationError("Selector tool names must be non-empty strings")
        return cls(all_nodes=False, names=names)

    def allows(self, tool_name: str) -> bool:
        return self.all_nodes or tool_name in self.names


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    layer: str
    providers: NodeSelector
    workers: NodeSelector
    capabilities: frozenset[str] = frozenset()
    consumes: tuple[type, ...] = ()
    produces: tuple[type, ...] = ()
    description: str = ""
    cost_per_call: float | None = None

    def __post_init__(self) -> None:
        if self.cost_per_call is None:
            return
        if isinstance(self.cost_per_call, bool) or self.cost_per_call < 0:
            raise RegistrationError(
                "ToolSpec cost_per_call must be a non-negative number"
            )


@dataclass(frozen=True, slots=True)
class ToolNode:
    """A topology node with metadata and an async implementation for later phases."""

    spec: ToolSpec
    handler: Callable[..., Awaitable[Any]]

    @property
    def __name__(self) -> str:
        return self.spec.name

    async def invoke(self, *args: Any) -> Any:
        return await self.handler(*args)
