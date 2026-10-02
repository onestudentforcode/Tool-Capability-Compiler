"""Office battlefield assembly: layers, tool registration, topology, smoke gate.

The Python declarations here are the single source of truth for the office
topology; ``export_topology.py`` serializes them into executable JSON for the
CLI chain. Tool modules expose their nodes via ``NODES`` so this module stays
a plain concatenation as batches B (deterministic) and C (LLM) land.

Batch A wires the five declared layers and the four L0 readers; the upper
layers stay empty until their batches deliver tools.
"""

# NOTE: no `from __future__ import annotations` here — this module re-exports
# tool nodes and the smoke evaluator inspects real annotation-driven slots.

from capability_runtime import (
    CompositeEvaluator,
    EvaluationResult,
    Evaluator,
    ExecutionState,
    FinalResult,
    LayerRegistry,
    ToolExecutionStatus,
    ToolRegistry,
    TopologyBuilder,
)

from . import composite_nodes, tools_det, tools_l0, tools_llm

LAYERS = ("context", "extract", "compose", "verify", "render")
DEFAULT_TOPOLOGY_VERSION = "office-v0"


def build_topology(*, topology_version: str = DEFAULT_TOPOLOGY_VERSION):
    layers = LayerRegistry()
    for order, name in enumerate(LAYERS):
        layers.register(name, order)

    tools = ToolRegistry()
    for node in (
        *tools_l0.NODES,
        *tools_det.NODES,
        *tools_llm.NODES,
        *composite_nodes.NODES,
    ):
        tools.register(node)

    topology = TopologyBuilder(layers, tools).build()
    return topology, topology_version


class ArtifactPresenceEvaluator(Evaluator):
    """Batch A smoke gate: typed L0 artifacts reached the blackboard.

    Success requires at least one typed artifact in the final state; quality
    is the fraction of the known L0 slots present, so a single-reader route
    lands strictly between 0 and 1. The per-family CompositeEvaluator set
    (office-battlefield.md §7) arrives with the scenario batch.
    """

    _SLOTS = ("source_doc", "data_table", "slide_digest")

    async def evaluate(
        self, scenario, result: FinalResult, trace
    ) -> EvaluationResult:
        state = result.state_snapshot
        names = state.names() if isinstance(state, ExecutionState) else ()
        present = sum(1 for slot in self._SLOTS if slot in names)
        return EvaluationResult(
            success=present > 0,
            quality_score=present / len(self._SLOTS),
            reason=None if present else "no typed artifact reached the blackboard",
        )


class OfficeFamilyEvaluator(Evaluator):
    """Business gate: no tool failure, and the family's artifacts exist.

    Two structured assertions from the run (office-battlefield.md §7): the
    trace must be free of tool errors — a malformed LLM payload, a stalled
    translate or a failed corpus read is a failed run, surfaced with the
    tool's own error category — and the family's terminal artifacts (compose
    output, a verify ReviewReport, and for render-ending families a
    FileSpec) must have reached the blackboard.
    """

    _FAMILY_SLOTS = {
        "doc_report": ("draft", "review_report", "file_spec"),
        "slide_deck": ("slide_copy", "review_report", "file_spec"),
        "sheet_analysis": ("formula_spec", "review_report", "file_spec"),
        "mail_comms": ("email_draft", "review_report"),
    }
    _DEFAULT_SLOTS = ("draft",)

    async def evaluate(
        self, scenario, result: FinalResult, trace
    ) -> EvaluationResult:
        for layer in trace.layers:
            for execution in layer.tool_executions:
                if execution.status is ToolExecutionStatus.ERROR:
                    return EvaluationResult(
                        success=False,
                        quality_score=0.0,
                        reason=f"tool {execution.tool_name} failed: {execution.error}",
                        category=execution.error_category,
                    )
        state = result.state_snapshot
        names = state.names() if isinstance(state, ExecutionState) else ()
        required = self._FAMILY_SLOTS.get(
            getattr(scenario, "category", "") or "", self._DEFAULT_SLOTS
        )
        missing = [slot for slot in required if slot not in names]
        return EvaluationResult(
            success=not missing,
            quality_score=1.0 if not missing else 0.0,
            reason=None if not missing else f"missing artifacts: {', '.join(missing)}",
        )


class OfficeQualityEvaluator(Evaluator):
    """Continuous quality from the verify layer's ReviewReport score.

    Quality-only dimension: always succeeds, the score carries the signal.
    Judge scores land inside the open (0, 1) interval; rule checks report
    1.0 when clean. Missing report (route without a checker) scores 0.
    """

    async def evaluate(
        self, scenario, result: FinalResult, trace
    ) -> EvaluationResult:
        state = result.state_snapshot
        latest = state.latest("review_report") if isinstance(state, ExecutionState) else None
        score = float(getattr(latest.value, "score", 0.0)) if latest else 0.0
        return EvaluationResult(success=True, quality_score=min(max(score, 0.0), 1.0))


def build_evaluator() -> CompositeEvaluator:
    """Family business outcome at 60% + verify-layer quality at 40% (§7)."""
    return CompositeEvaluator(
        {
            "office_family": (OfficeFamilyEvaluator(), 0.6),
            "review_quality": (OfficeQualityEvaluator(), 0.4),
        }
    )
