"""Named office corpus fixtures for the battlefield scale runs.

Each scenario declares its corpus variant via ``metadata.fixture`` (the same
convention the refund sandbox uses for phase 4 sentinels). The manager resets
the office store on setup, so every trial starts from identical corpus state
and the (scenario_id, trial_index) seeded rng is reproducible per trial
(office-battlefield.md §1 / §6).
"""

from __future__ import annotations

from capability_runtime import FixtureRegistry, FixtureSetupError

from . import store

DEFAULT_FIXTURE = "clean"


def build_registry() -> FixtureRegistry:
    """One named fixture per corpus variant; calling it reseeds the store."""
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


class OfficeFixtureManager:
    """Seeds the corpus per trial; reset/teardown restore the default."""

    def __init__(self, registry: FixtureRegistry | None = None) -> None:
        self._registry = registry or build_registry()

    async def setup(self, scenario, trial):
        name = fixture_name_of(scenario)
        if name not in self._registry.names():
            raise FixtureSetupError(
                f"scenario {scenario.id!r} requests unknown fixture "
                f"{name!r} (known: {', '.join(self._registry.names())})"
            )
        # The seeded reset: rng draws (flaky stalls, fake-LLM wobble) derive
        # from (scenario_id, trial_index), so every trial is reproducible.
        store.STORE.reset(name, scenario_id=scenario.id, trial_index=trial.trial_index)
        return {"fixture": name, "scenario_id": scenario.id}

    async def reset(self, scenario, trial) -> None:
        self._registry.get(DEFAULT_FIXTURE)

    async def teardown(self, scenario, trial) -> None:
        self._registry.get(DEFAULT_FIXTURE)
