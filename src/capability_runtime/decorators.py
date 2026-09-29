from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from .core.capability import validate_capability_name
from .core.errors import RegistrationError
from .core.tool import NodeSelector, SelectorInput, ToolNode, ToolSpec


def tool(
    *,
    layer: str,
    providers: SelectorInput = "all",
    workers: SelectorInput = "all",
    capabilities: Iterable[str] = (),
    consumes: Iterable[type] = (),
    produces: Iterable[type] = (),
    name: str | None = None,
    description: str = "",
    cost_per_call: float | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], ToolNode]:
    """Declare a node in the layered tool-routing search space."""

    consume_types = tuple(consumes)
    produce_types = tuple(produces)
    capability_names = frozenset(
        validate_capability_name(item) for item in capabilities
    )
    if not layer.strip():
        raise RegistrationError("Tool layer cannot be empty")
    if any(not isinstance(item, type) for item in (*consume_types, *produce_types)):
        raise RegistrationError("Tool schemas must contain Python types")
    if len(set(consume_types)) != len(consume_types):
        raise RegistrationError("A tool cannot consume the same schema twice")
    if len(set(produce_types)) != len(produce_types):
        raise RegistrationError("A tool cannot produce the same schema twice")
    if cost_per_call is not None and (
        isinstance(cost_per_call, bool) or cost_per_call < 0
    ):
        raise RegistrationError("Tool cost_per_call must be a non-negative number")
    provider_selector = NodeSelector.parse(providers)
    worker_selector = NodeSelector.parse(workers)

    def decorate(function: Callable[..., Awaitable[Any]]) -> ToolNode:
        if not inspect.iscoroutinefunction(function):
            raise RegistrationError(f"Tool function must be async: {function.__name__}")
        tool_name = name or function.__name__
        if not tool_name.strip():
            raise RegistrationError("Tool name cannot be empty")
        signature = inspect.signature(function)
        positional = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) != len(consume_types):
            raise RegistrationError(
                f"Tool {tool_name} declares {len(consume_types)} schema inputs but "
                f"has {len(positional)} positional parameters"
            )
        return ToolNode(
            spec=ToolSpec(
                name=tool_name,
                layer=layer,
                providers=provider_selector,
                workers=worker_selector,
                capabilities=capability_names,
                consumes=consume_types,
                produces=produce_types,
                description=description.strip(),
                cost_per_call=cost_per_call,
            ),
            handler=function,
        )

    return decorate
