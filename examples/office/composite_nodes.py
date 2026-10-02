"""Office composite nodes (office-battlefield batch F).

Direction-2 showcase: two "final capability" macro nodes on the real office
topology. Externally they are plain ToolNodes (registration, edges, coverage,
execution, ranking, online - zero special branches); internally each runs a
bounded refine loop over :mod:`composite_inner` with metered cross-iteration
gates. Honest billing: ``cost_per_call`` is the exact sum of the inner tools'
declared costs (locked by test).
"""

from capability_runtime.composite import CompositeSpec, build_composite_node

from .composite_inner import (
    build_deck_inner_topology,
    build_doc_inner_topology,
)
from .facts import Draft, FactSheet, SlideCopy, SourceDoc

DOC_INNER_COSTS = (0.001, 0.0005, 0.004, 0.0005, 0.0001)
DECK_INNER_COSTS = (0.001, 0.0005, 0.002, 0.0005, 0.0001)

DOC_SPEC = CompositeSpec(
    name="doc_composed_report",
    layer="compose",
    topology=build_doc_inner_topology(),
    route=(
        ("draft_step",),
        ("check_step",),
        ("polish_step",),
        ("report_release", "report_holdover"),
    ),
    stop_when=("draft",),
    max_iterations=3,
    consumes=(SourceDoc, FactSheet),
    produces=(Draft,),
    capabilities=frozenset({"doc.composed_report"}),
    cost_per_call=sum(DOC_INNER_COSTS),
    description=(
        "Bounded draft->length-check->polish->release loop producing a "
        "self-checked Draft (deterministic two-iteration convergence)"
    ),
)

DECK_SPEC = CompositeSpec(
    name="ppt_composed_deck",
    layer="compose",
    topology=build_deck_inner_topology(),
    route=(
        ("deck_step",),
        ("deck_check_step",),
        ("deck_polish_step",),
        ("deck_release", "deck_holdover"),
    ),
    stop_when=("slide_copy",),
    max_iterations=3,
    consumes=(SourceDoc, FactSheet),
    produces=(SlideCopy,),
    capabilities=frozenset({"ppt.composed_deck"}),
    cost_per_call=sum(DECK_INNER_COSTS),
    description=(
        "Bounded deck->overflow-check->trim->release loop producing a "
        "self-checked SlideCopy (deterministic two-iteration convergence)"
    ),
)

doc_composed_report = build_composite_node(DOC_SPEC)
ppt_composed_deck = build_composite_node(DECK_SPEC)

NODES = (doc_composed_report, ppt_composed_deck)
SPECS = {"doc_composed_report": DOC_SPEC, "ppt_composed_deck": DECK_SPEC}
