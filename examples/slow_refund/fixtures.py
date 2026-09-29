"""Named sandbox fixtures for the refund demo (battlefield-hardening batch D).

Each scenario declares its fixture variant via ``metadata.fixture`` (the same
convention phase 4 uses for sentinels). ``SandboxFixtureManager`` seeds the
store on setup and resets it on reset/teardown, so every trial starts from
identical sandbox state and refund mutations never leak across trials.
"""

from __future__ import annotations

import store
from capability_runtime import FixtureRegistry, FixtureSetupError
from capability_runtime.core.errors import RegistrationError

DEFAULT_FIXTURE = "eligible"


def build_registry() -> FixtureRegistry:
    """One named fixture per sandbox variant; calling it seeds the store."""
    registry = FixtureRegistry()
    for variant in store.VARIANTS:
        registry.register(variant, lambda v=variant: store.STORE.reset(v))
    return registry


def fixture_name_of(scenario) -> str:
    metadata = dict(getattr(scenario, "metadata", None) or {})
    name = metadata.get("fixture", DEFAULT_FIXTURE)
    if not isinstance(name, str) or not name.strip():
        raise FixtureSetupError(
            f"scenario {scenario.id!r} declares a non-string fixture {name!r}"
        )
    return name


class SandboxFixtureManager:
    """Seeds the sandbox store per trial; teardown restores the default."""

    def __init__(self, registry: FixtureRegistry | None = None) -> None:
        self._registry = registry or build_registry()

    async def setup(self, scenario, trial):
        name = fixture_name_of(scenario)
        try:
            self._registry.get(name)
        except RegistrationError as exc:
            raise FixtureSetupError(
                f"scenario {scenario.id!r} requests unknown fixture "
                f"{name!r} (known: {', '.join(self._registry.names())})"
            ) from exc
        return {"fixture": name, "scenario_id": scenario.id}

    async def reset(self, scenario, trial) -> None:
        self._registry.get(DEFAULT_FIXTURE)

    async def teardown(self, scenario, trial) -> None:
        self._registry.get(DEFAULT_FIXTURE)
