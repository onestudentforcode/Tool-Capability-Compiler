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
    EvaluationResult,
    Evaluator,
    ExecutionState,
    FinalResult,
    LayerRegistry,
    ToolRegistry,
    TopologyBuilder,
)

from . import tools_det, tools_l0, tools_llm

LAYERS = ("context", "extract", "compose", "verify", "render")
DEFAULT_TOPOLOGY_VERSION = "office-v0"


def build_topology(*, topology_version: str = DEFAULT_TOPOLOGY_VERSION):
    layers = LayerRegistry()
    for order, name in enumerate(LAYERS):
        layers.register(name, order)

    tools = ToolRegistry()
    for node in (*tools_l0.NODES, *tools_det.NODES, *tools_llm.NODES):
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
