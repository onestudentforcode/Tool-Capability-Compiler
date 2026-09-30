"""Batch adapter: register an OpenAI-style function spec list in one call.

This is the *quick adaptation* path of the onboarding-assist milestone: every
spec becomes an entry-layer ToolNode with zero per-tool code. The boundary is
deliberate — batch-adapted tools carry no typed contract (consumes/produces
stay empty, terminal/entry semantics). Cross-layer typed chaining remains the
Python path: declare small dataclasses and let @tool's annotation inference
fill consumes/produces (see the recipe below).

Native-adaptation recipe (per cross-layer tool, ~5 lines)::

    @dataclass(frozen=True)
    class Order: ...

    @tool(layer="read", capabilities={"order.read"})
    async def get_order() -> Order:            # return annotation -> produces
        return _to_order(dispatch("get_order", {}))
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from ..core.errors import OnboardingError
from ..core.tool import NodeSelector, ToolNode, ToolSpec


def _function_part(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    if "function" in spec and isinstance(spec["function"], Mapping):
        return spec["function"]
    return spec


def from_openai_specs(
    specs: Sequence[Mapping[str, Any]],
    dispatch: Callable[[str, dict], Any],
    *,
    layer: str,
    cost_per_call: float | None = None,
) -> tuple[ToolNode, ...]:
    """Turn OpenAI function specs into entry-layer ToolNodes in one call.

    ``dispatch(tool_name, arguments)`` may be sync or async; sync dispatches
    run off the event loop via ``asyncio.to_thread``. The parameters schema
    is appended to each tool's description so LLM routers and capability
    proposals can see it.
    """
    if not isinstance(layer, str) or not layer.strip():
        raise OnboardingError("from_openai_specs needs a non-empty layer")
    if not callable(dispatch):
        raise OnboardingError("from_openai_specs needs a callable dispatch")

    nodes: list[ToolNode] = []
    seen: set[str] = set()
    for index, spec in enumerate(specs):
        function = _function_part(spec)
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise OnboardingError(f"specs[{index}] has no function name")
        if name in seen:
            raise OnboardingError(f"specs[{index}] duplicates tool {name!r}")
        seen.add(name)
        description = str(function.get("description", "")).strip()
        parameters = function.get("parameters")
        if parameters is not None:
            try:
                schema_text = json.dumps(parameters, ensure_ascii=False)
            except (TypeError, ValueError):
                schema_text = str(parameters)
            description = (
                f"{description}\nParameters: {schema_text}".strip()
            )

        handler = _make_handler(name, dispatch)
        nodes.append(
            ToolNode(
                spec=ToolSpec(
                    name=name,
                    layer=layer,
                    providers=NodeSelector(all_nodes=True),
                    workers=NodeSelector(all_nodes=True),
                    capabilities=frozenset(),
                    consumes=(),
                    produces=(),
                    description=description,
                    cost_per_call=cost_per_call,
                ),
                handler=handler,
            )
        )
    return tuple(nodes)


def skeleton_payload(
    specs: Sequence[Mapping[str, Any]],
    *,
    layer: str,
    dispatch_module: str | None = None,
) -> dict[str, Any]:
    """Scaffold a single-layer topology payload from OpenAI specs.

    Batch D helper: capabilities stay empty (filled only by reviewed
    proposals via ``apply_capabilities``); ``dispatch_module`` optionally
    stamps ``implementation: "<module>:<tool>"`` entry points (must be async
    functions, per the loader's binding rules).
    """
    tools: list[dict[str, Any]] = []
    for spec in specs:
        function = _function_part(spec)
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise OnboardingError(f"spec has no function name: {spec!r}")
        description = str(function.get("description", "")).strip()
        parameters = function.get("parameters")
        if parameters is not None:
            try:
                schema_text = json.dumps(parameters, ensure_ascii=False)
            except (TypeError, ValueError):
                schema_text = str(parameters)
            description = f"{description}\nParameters: {schema_text}".strip()
        entry: dict[str, Any] = {
            "name": name,
            "layer": layer,
            "description": description,
        }
        if dispatch_module:
            entry["implementation"] = f"{dispatch_module}:{name}"
        tools.append(entry)
    if not tools:
        raise OnboardingError("scaffold needs at least one spec")
    return {
        "version": "1.0",
        "layers": [{"name": layer, "order": 0}],
        "tools": tools,
    }


def _make_handler(
    name: str, dispatch: Callable[[str, dict], Any]
) -> Callable[..., Awaitable[Any]]:
    import inspect

    if inspect.iscoroutinefunction(dispatch):

        async def handler() -> Any:
            return await dispatch(name, {})

    else:

        async def handler() -> Any:
            return await asyncio.to_thread(dispatch, name, {})

    return handler
