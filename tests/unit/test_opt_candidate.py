import pytest

from capability_runtime import (
    Candidate,
    CandidateDetector,
    CandidateReason,
    CandidateStatus,
    EdgeEvidence,
    EvidenceReport,
    NodeEvidence,
    PruningConfig,
)


def _report(edges, nodes=None) -> EvidenceReport:
    return EvidenceReport(
        scenario_count=2,
        trial_count=50,
        node_evidence={n.tool: n for n in (nodes or [])},
        edge_evidence={f"{e.source}->{e.target}": e for e in edges},
    )


def _edge(source, target, opp, observed, protected=False, success=0, scenarios=0):
    return EdgeEvidence(
        source=source, target=target,
        opportunity_count=opp, observed_count=observed,
        successful_trial_count=success, failed_trial_count=observed - success,
        successful_route_count=success, scenario_count=scenarios,
        protected=protected,
    )


def _node(tool, available, selected):
    return NodeEvidence(
        tool=tool, available_count=available, selected_count=selected,
        success_trial_count=0, failed_trial_count=0,
    )


CFG = PruningConfig(min_edge_opportunities=100, min_node_availability=100)


def test_high_opportunity_low_usage_is_candidate() -> None:
    evidence = _report([_edge("a", "b", opp=200, observed=0)])
    out = CandidateDetector(CFG).detect(evidence)
    assert len(out) == 1
    c = out[0]
    assert c.kind == "edge" and c.subject == "a->b"
    assert c.status is CandidateStatus.IDENTIFIED
    assert c.reason is CandidateReason.HIGH_OPPORTUNITY_UNUSED
    assert c.opportunity_count == 200 and c.usage_rate == 0.0


def test_low_opportunity_is_insufficient_evidence() -> None:
    evidence = _report([_edge("a", "b", opp=5, observed=0)])
    out = CandidateDetector(CFG).detect(evidence)
    assert len(out) == 1
    assert out[0].status is CandidateStatus.INSUFFICIENT_EVIDENCE
    assert out[0].reason is None


def test_protected_edge_is_protected_not_candidate() -> None:
    # protected edges are never prunable even at zero usage
    evidence = _report([_edge("p", "q", opp=300, observed=0, protected=True)])
    out = CandidateDetector(CFG).detect(evidence)
    assert len(out) == 1
    assert out[0].status is CandidateStatus.PROTECTED
    assert out[0].protected is True


def test_low_selection_rate_reason_for_partial_usage() -> None:
    # some observations but still below threshold -> LOW_SELECTION_RATE, not UNUSED
    evidence = _report([_edge("a", "b", opp=200, observed=1, success=1)])
    edge = evidence.edge_evidence["a->b"]
    assert edge.usage_rate == pytest.approx(0.005)
    out = CandidateDetector(CFG).detect(evidence)
    assert len(out) == 1
    assert out[0].status is CandidateStatus.IDENTIFIED
    assert out[0].reason is CandidateReason.LOW_SELECTION_RATE


def test_well_used_edge_is_not_candidate() -> None:
    evidence = _report([_edge("a", "b", opp=200, observed=150)])
    assert CandidateDetector(CFG).detect(evidence) == ()


def test_protected_is_immune_to_evidence() -> None:
    # high usage + protected must still never be touched
    evidence = _report([_edge("p", "q", opp=200, observed=190, protected=True)])
    out = CandidateDetector(CFG).detect(evidence)
    assert out[0].status is CandidateStatus.PROTECTED


def test_node_never_selected_is_candidate() -> None:
    evidence = _report(
        [],
        nodes=[_node("idle", available=120, selected=0),
               _node("busy", available=120, selected=90)],
    )
    out = CandidateDetector(CFG).detect(evidence)
    subjects = {c.subject for c in out}
    assert subjects == {"idle"}
    c = next(c for c in out if c.subject == "idle")
    assert c.status is CandidateStatus.IDENTIFIED
    assert c.reason is CandidateReason.NODE_NEVER_SELECTED


def test_low_opportunity_node_not_candidate() -> None:
    evidence = _report([], nodes=[_node("sparse", available=3, selected=0)])
    assert CandidateDetector(CFG).detect(evidence) == ()


def test_detection_order_is_deterministic() -> None:
    evidence = _report(
        [_edge(d, e, 200, 0) for d, e in (("b", "y"), ("a", "x"), ("c", "z"))]
    )
    subjects = [c.subject for c in CandidateDetector(CFG).detect(evidence)]
    assert subjects == sorted(subjects)


def test_invalid_config_rejected() -> None:
    with pytest.raises(ValueError):
        PruningConfig(min_edge_opportunities=-1)
    with pytest.raises(ValueError):
        PruningConfig(edge_usage_threshold=1.5)
    with pytest.raises(ValueError):
        PruningConfig(max_pruning_batch_size=0)