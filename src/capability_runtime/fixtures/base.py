from __future__ import annotations

from typing import Protocol


class FixtureManager(Protocol):
    """Lifecycle owner of the prepared state behind a Trial ($12)."""

    async def setup(self, scenario: "Scenario", trial: "Trial") -> "ExecutionInputs":
        ...

    async def reset(self, scenario: "Scenario", trial: "Trial") -> None:
        ...

    async def teardown(self, scenario: "Scenario", trial: "Trial") -> None:
        ...