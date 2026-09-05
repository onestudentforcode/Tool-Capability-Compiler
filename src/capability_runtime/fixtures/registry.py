from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..core.errors import RegistrationError
from ..execution.state import ExecutionInputs
from ..scenario.models import Scenario
from ..regression.slow.trial import Trial


class FixtureRegistry:
    """Named fixture factories, resolved by scenario's slow_regression.fixture."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], Any]] = {}

    def register(
        self, name: str, factory: Callable[[], Any], *, replace: bool = False
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise RegistrationError("fixture name must be a non-empty string")
        if name in self._factories and not replace:
            raise RegistrationError(f"fixture {name!r} is already registered")
        self._factories[name] = factory

    def get(self, name: str) -> Any:
        if name not in self._factories:
            raise RegistrationError(f"fixture {name!r} is not registered")
        return self._factories[name]()

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))