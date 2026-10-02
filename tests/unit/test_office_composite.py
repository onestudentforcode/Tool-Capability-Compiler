"""Office composite nodes (office-battlefield batch F).

The two direction-2 showcases: bounded refine loops that converge
deterministically in TWO iterations (fail-and-improve, then
re-emit-and-pass), honest billing (outer cost == sum of inner declared
costs), and gate-reset isolation. All offline via the office fake LLM.
"""

from __future__ import annotations

import asyncio

import pytest

from capability_runtime.resources.metering import (
    mount_collector,
    take_detail,
    unmount_collector,
)

from examples.office import composite_inner, composite_nodes, facts, office_llm
from examples.office.tools_llm import draft_section_fast

_SOURCE = facts.SourceDoc(
    doc_id="weekly_report",
    title="Weekly Report - Platform Team",
    text=(
        "# Weekly Report - Platform Team\n\n## Progress\nShipped the export "
        "pipeline and cut build time by 40 percent.\n\n## Risks\nThe auth "
        "migration may slip one sprint.\n\n## Next\nFinish the auth migration "
        "and draft the Q4 hiring plan.\n"
    ),
)
_FACTS = facts.FactSheet(
    doc_id="weekly_report",
    facts=("revenue grew 8% quarter over quarter", "churn steady at 2.1%"),
)


@pytest.fixture(autouse=True)
def _offline(tmp_path):
    office_llm.install_offline_fake()
    composite_inner.reset_inner_gates()
    yield
    composite_inner.reset_inner_gates()


async def _run_composite(node, *args):
    token = mount_collector()
    try:
        result = await node.handler(*args)
        detail = take_detail()
        return result, detail
    finally:
        unmount_collector(token)


def test_doc_composite_converges_in_two_iterations() -> None:
    result, detail = asyncio.run(
        _run_composite(composite_nodes.doc_composed_report, _SOURCE, _FACTS)
    )
    # iteration 1: short fake draft fails the length rule, polish expands;
    # iteration 2: gate re-emits the expanded draft, check passes, gate
    # releases the terminal Draft (first appearance == success)
    assert len(detail) == 2
    assert isinstance(result, facts.Draft)
    assert len(result.body.split()) >= 10


def test_deck_composite_converges_and_trims() -> None:
    result, detail = asyncio.run(
        _run_composite(composite_nodes.ppt_composed_deck, _SOURCE, _FACTS)
    )
    assert len(detail) == 2
    assert isinstance(result, facts.SlideCopy)
    title, bullets = result.slides[0]
    assert len(bullets) <= composite_inner._MAX_BULLETS
    assert all(len(b) <= composite_inner._MAX_BULLET_CHARS for b in bullets)


def test_honest_billing_outer_cost_equals_inner_sum() -> None:
    for spec in (composite_nodes.DOC_SPEC, composite_nodes.DECK_SPEC):
        inner_costs = sum(
            spec.topology.node(name).spec.cost_per_call or 0.0
            for name in spec.topology.nodes()
        )
        assert spec.cost_per_call == pytest.approx(inner_costs), spec.name


def test_gate_reset_restores_two_iteration_convergence() -> None:
    node = composite_nodes.doc_composed_report
    asyncio.run(_run_composite(node, _SOURCE, _FACTS))
    # without a reset the gate still holds the improved draft: the very
    # first iteration passes the checks (one-iteration convergence)
    _, detail = asyncio.run(_run_composite(node, _SOURCE, _FACTS))
    assert len(detail) == 1
    # the fixture-manager reset restores the fail-then-improve story
    composite_inner.reset_inner_gates()
    _, detail = asyncio.run(_run_composite(node, _SOURCE, _FACTS))
    assert len(detail) == 2


def test_draft_step_reuses_the_real_draft_handler() -> None:
    """The inner drafter routes through the real LLM tool (offline fake)."""
    working = asyncio.run(
        _run_composite(composite_inner.draft_step, _SOURCE, _FACTS)
    )[0]
    reference = asyncio.run(
        _run_composite(draft_section_fast, _SOURCE, _FACTS)
    )[0]
    assert isinstance(working, composite_inner.WorkingDraft)
    assert working.body == reference.body
    assert working.title == reference.title


def test_outer_topology_registers_composites_cleanly() -> None:
    from examples.office import office

    topology, _ = office.build_topology()
    assert {"doc_composed_report", "ppt_composed_deck"} <= set(topology.nodes())
    assert len(topology.nodes()) == 53
    # batch-D diagnostics stay silent for the composites (adjacent-layer
    # producers exist; generated handler params follow the slot convention)
    codes = {w.code for w in topology.warnings()}
    assert "UNSATISFIABLE_INPUT" not in codes
    assert "SLOT_NAME_CONFLICT" not in codes
