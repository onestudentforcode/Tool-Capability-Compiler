from __future__ import annotations

from copy import deepcopy
from enum import Enum
from typing import Any, Mapping

from ..core.errors import FixtureSetupError, FixtureResetError, FixtureTeardownError
from ..execution.state import ExecutionInputs
from ..scenario.models import Scenario
from ..regression.slow.trial import Trial


class IsolationMode(Enum):
    """How trials may share fixture state ($100).

    Phase 3 MVP defaults to scenario-internal sequential execution: each trial
    gets its own state and consecutive trials never share in-flight state."""

    SEQUENTIAL = "sequential"
    PARALLEL_SAFE = "parallel_safe"


class DefaultFixtureManager:
    """Stateless-against-backend fixture manager for the MVP.

    Provides the setup -> execute -> evaluate -> reset/teardown lifecycle and
    guarantees consecutive-trial state isolation: every setup returns a fresh
    deep copy of the template, so a trial mutating its inputs never leaks into
    the next trial or the template itself.
    """

    def __init__(
        self,
        template: Mapping[str, Any] | None = None,
        isolation_mode: IsolationMode = IsolationMode.SEQUENTIAL,
    ) -> None:
        self._template: dict[str, Any] = dict(template) if template else {}
        self.isolation_mode = isolation_mode
        self._active: set[str] = set()

    async def setup(self, scenario: Scenario, trial: Trial) -> ExecutionInputs:
        if trial.id in self._active:
            raise FixtureSetupError(
                f"trial {trial.id!r} is already set up; "
                "reset/teardown is required before re-setup"
            )
        self._active.add(trial.id)
        return deepcopy(self._template)

    async def reset(self, scenario: Scenario, trial: Trial) -> None:
        if trial.id not in self._active:
            raise FixtureResetError(
                f"trial {trial.id!r} has no active fixture state to reset"
            )
        self._active.discard(trial.id)

    async def teardown(self, scenario: Scenario, trial: Trial) -> None:
        if trial.id not in self._active:
            raise FixtureTeardownError(f"trial {trial.id!r} was not set up")
        self._active.discard(trial.id)