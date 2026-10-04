"""Battlefield Hardening Batch D: scenario-seeded sandbox fixtures.

Each scenario declares its sandbox variant via ``metadata.fixture``;
``SandboxFixtureManager`` reseeds the store per trial so mutations (the
refund idempotency guard) never leak between trials of the same scenario
(battlefield-hardening §5).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from capability_runtime import (
    DefaultFixtureManager,
    FixtureSetupError,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    FakeRouter,
)
from capability_runtime.core.errors import RegistrationError

_PROJECT = Path(__file__).resolve().parents[2]
_DEMO = _PROJECT / "examples" / "slow_refund"
sys.path.insert(0, str(_DEMO))

import fixtures as demo_fixtures  # noqa: E402
import refund  # noqa: E402
import store  # noqa: E402

_REFUND_ROUTE = FakeRouter(
    layer_selections={
        "read": ["order_db"],
        "analyze": ["policy_check"],
        "action": ["refund_api"],
    }
)


def _suite(*specs: tuple[str, str | None]) -> ScenarioSuite:
    return ScenarioSuite(
        name="fixtures",
        version="1.0",
        description="fixtures",
        scenarios=tuple(
            Scenario(
                id=sid,
                query=f"refund {sid}",
                metadata={"fixture": fixture} if fixture else {},
            )
            for sid, fixture in specs
        ),
    )


def _run(manager, suite, *, trials: int = 3):
    return asyncio.run(
        SlowRegressionRunner(
            topology=refund.build_topology()[0],
            evaluator=refund.build_evaluator(),
            fixture_manager=manager,
            trials_per_scenario=trials,
            topology_version="v0.3.1",
            router_config_id="fake",
            router=_REFUND_ROUTE,
        ).run(suite)
    )


# ---- registry ----------------------------------------------------------------


def test_registry_covers_all_variants() -> None:
    registry = demo_fixtures.build_registry()
    assert set(registry.names()) == set(store.VARIANTS)
    registry.get("high_risk")
    assert store.STORE.variant == "high_risk"


def test_registry_rejects_duplicate_registration() -> None:
    registry = demo_fixtures.build_registry()
    with pytest.raises(RegistrationError):
        registry.register("eligible", lambda: None)


# ---- manager contract ---------------------------------------------------------


def test_setup_seeds_declared_fixture() -> None:
    manager = demo_fixtures.SandboxFixtureManager()
    scenario = Scenario(
        id="s1", query="q", metadata={"fixture": "not_found"}
    )
    asyncio.run(manager.setup(scenario, _trial("s1", 0)))
    assert store.STORE.variant == "not_found"


def test_setup_defaults_to_eligible_without_declaration() -> None:
    manager = demo_fixtures.SandboxFixtureManager()
    asyncio.run(manager.setup(Scenario(id="s2", query="q"), _trial("s2", 0)))
    assert store.STORE.variant == "eligible"


def test_unknown_fixture_is_a_fixture_error_not_a_crash() -> None:
    manager = demo_fixtures.SandboxFixtureManager()
    scenario = Scenario(id="s3", query="q", metadata={"fixture": "ghost"})
    with pytest.raises(FixtureSetupError, match="ghost"):
        asyncio.run(manager.setup(scenario, _trial("s3", 0)))


def test_trial_with_unknown_fixture_is_marked_fixture_error() -> None:
    outcome = _run(
        demo_fixtures.SandboxFixtureManager(),
        _suite(("s_bad", "ghost")),
        trials=1,
    )
    assert outcome.results[0].execution_status.value == "fixture_error"


def _trial(scenario_id: str, index: int):
    from capability_runtime import Trial

    return Trial(
        id=f"{scenario_id}#{index:03d}",
        scenario_id=scenario_id,
        trial_index=index,
        topology_version="v0.3.1",
        scenario_suite_version="1.0",
        router_config_id="fake",
    )


# ---- isolation: the acceptance core --------------------------------------------


def test_consecutive_trials_are_isolated() -> None:
    # With per-trial reseeding every trial of the same scenario succeeds —
    # the refund idempotency guard would otherwise fail trials 2+.
    outcome = _run(
        demo_fixtures.SandboxFixtureManager(), _suite(("iso", "eligible"))
    )
    assert len(outcome.results) == 3
    for result in outcome.results:
        assert result.execution_status.value == "completed"
        assert result.evaluation.success


def test_without_reseeding_state_leaks_across_trials() -> None:
    # Control: DefaultFixtureManager never touches the store, so the first
    # refund poisons the sandbox and later trials fail with "already
    # refunded" — exactly the leak the sandbox fixture exists to prevent.
    store.STORE.reset("eligible")
    outcome = _run(
        DefaultFixtureManager(), _suite(("leak", "eligible"))
    )
    successes = [r.evaluation.success for r in outcome.results]
    assert successes[0] is True
    assert any(success is False for success in successes[1:])


def test_teardown_restores_default_variant() -> None:
    manager = demo_fixtures.SandboxFixtureManager()
    scenario = Scenario(id="s4", query="q", metadata={"fixture": "erp_down"})
    trial = _trial("s4", 0)
    asyncio.run(manager.setup(scenario, trial))
    assert store.STORE.variant == "erp_down"
    asyncio.run(manager.teardown(scenario, trial))
    assert store.STORE.variant == "eligible"


# ---- scenario assets carry variants --------------------------------------------


def test_demo_scenarios_declare_valid_fixtures() -> None:
    from capability_runtime.scenario import ScenarioLoader

    suite = ScenarioLoader().load_file(str(_DEMO / "scenarios.json"))
    for scenario in suite.scenarios:
        name = demo_fixtures.fixture_name_of(scenario)
        assert name in store.VARIANTS


def test_variants_drive_different_business_conclusions() -> None:
    outcome = _run(
        demo_fixtures.SandboxFixtureManager(),
        _suite(("ok", "eligible"), ("rejected", "high_risk")),
        trials=2,
    )
    by_scenario = {}
    for result in outcome.results:
        by_scenario.setdefault(result.trial.scenario_id, []).append(
            result.evaluation.success if result.evaluation else None
        )
    assert all(by_scenario["ok"])
    assert not any(by_scenario["rejected"])
