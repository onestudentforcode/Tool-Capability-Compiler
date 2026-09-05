import asyncio

import pytest

from capability_runtime import (
    DefaultFixtureManager,
    FixtureResetError,
    FixtureSetupError,
    FixtureTeardownError,
    FixtureRegistry,
    IsolationMode,
    RegistrationError,
    Trial,
)


def _trial(trial_id: str) -> Trial:
    return Trial(
        id=trial_id,
        scenario_id="s1",
        trial_index=0,
        topology_version="1.0",
        scenario_suite_version="2.0",
        router_config_id="cfg-fake",
    )


def test_setup_returns_fresh_deepcopy_per_trial() -> None:
    manager = DefaultFixtureManager(template={"order": {"amount": 100}})
    a = asyncio.run(manager.setup(None, _trial("t1")))
    asyncio.run(manager.reset(None, _trial("t1")))
    b = asyncio.run(manager.setup(None, _trial("t2")))
    assert a == {"order": {"amount": 100}}
    assert b == {"order": {"amount": 100}}
    # mutating one trial's inputs must not affect the other (deep isolation)
    a["order"]["amount"] = 999
    assert b["order"]["amount"] == 100


def test_double_setup_raises_fixture_setup_error() -> None:
    manager = DefaultFixtureManager()
    asyncio.run(manager.setup(None, _trial("t1")))
    with pytest.raises(FixtureSetupError):
        asyncio.run(manager.setup(None, _trial("t1")))


def test_reset_without_active_state_raises() -> None:
    manager = DefaultFixtureManager()
    with pytest.raises(FixtureResetError):
        asyncio.run(manager.reset(None, _trial("t1")))


def test_teardown_without_setup_raises() -> None:
    manager = DefaultFixtureManager()
    with pytest.raises(FixtureTeardownError):
        asyncio.run(manager.teardown(None, _trial("t1")))


def test_full_lifecycle_roundtrip() -> None:
    manager = DefaultFixtureManager(template={"x": 1})
    trial = _trial("t1")
    inputs = asyncio.run(manager.setup(None, trial))
    assert inputs == {"x": 1}
    asyncio.run(manager.reset(None, trial))
    asyncio.run(manager.setup(None, trial))  # can reuse after reset
    asyncio.run(manager.teardown(None, trial))


def test_isolation_mode_defaults_to_sequential() -> None:
    assert DefaultFixtureManager().isolation_mode is IsolationMode.SEQUENTIAL


def test_registry_registers_and_resolves_named_fixture() -> None:
    registry = FixtureRegistry()
    registry.register("eligible_order", lambda: DefaultFixtureManager({"a": 1}))
    manager = registry.get("eligible_order")
    assert isinstance(manager, DefaultFixtureManager)
    assert asyncio.run(manager.setup(None, _trial("t1"))) == {"a": 1}
    assert registry.names() == ("eligible_order",)


def test_registry_rejects_duplicate() -> None:
    registry = FixtureRegistry()
    registry.register("f", lambda: DefaultFixtureManager())
    with pytest.raises(RegistrationError):
        registry.register("f", lambda: DefaultFixtureManager())
    registry.register("f", lambda: DefaultFixtureManager(), replace=True)


def test_registry_get_missing_raises() -> None:
    registry = FixtureRegistry()
    with pytest.raises(RegistrationError):
        registry.get("nope")