"""In-memory metered store (stdlib) — the battlefield sandbox's resource."""

from __future__ import annotations

from typing import Any

from .metering import current_collector


class InMemoryStore:
    """A key-value store whose async accessors are metered.

    ``get`` records a read, ``put`` / ``delete`` record writes — onto the
    collector of the tool invocation that touched them. ``seed`` / ``clear``
    are administrative (fixture setup / teardown) and deliberately unmetered.
    """

    def __init__(self, name: str) -> None:
        if not name.strip():
            from ..core.errors import ResourceHandleError

            raise ResourceHandleError("InMemoryStore needs a non-empty name")
        self._name = name
        self._data: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return self._name

    async def get(self, key: str) -> Any | None:
        current_collector().record_access(self._name, "read")
        return self._data.get(key)

    async def put(self, key: str, value: Any) -> None:
        current_collector().record_access(self._name, "write")
        self._data[key] = value

    async def delete(self, key: str) -> None:
        current_collector().record_access(self._name, "write")
        self._data.pop(key, None)

    # ---- administrative (unmetered: used by fixtures outside tool calls) ----

    def seed(self, mapping: dict[str, Any]) -> None:
        self._data.update(mapping)

    def clear(self) -> None:
        self._data.clear()

    def snapshot(self) -> dict[str, Any]:
        return dict(self._data)
