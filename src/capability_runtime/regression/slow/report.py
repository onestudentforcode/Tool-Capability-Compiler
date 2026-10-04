"""Aggregation and text rendering of a Slow Regression run.

Step 14 :class:`SlowRegressionReport` condenses a ``SlowRunOutcome`` plus the
derived observation stats into the counters the CLI prints (phase3 §107), and
exposes unused-edge counts so the CLI can report *facts* without ever
recommending pruning (phase3 §108 -- pruning is a Phase 4 decision).
"""

from __future__ import annotations

from dataclasses import dataclass

from ...scenario.models import ScenarioSuite
from ...topology.models import Topology
from ...core.metrics import TokenUsage
from .runner import SlowRunOutcome, sum_known_costs
from .stats import ObservationReport, summarize
from .trial import TrialExecutionStatus

_EXECUTION_FAILED = {
    TrialExecutionStatus.ROUTING_ERROR,
    TrialExecutionStatus.TOOL_ERROR,
    TrialExecutionStatus.LAYER_ERROR,
    TrialExecutionStatus.FIXTURE_ERROR,
}


@dataclass(frozen=True, slots=True)
class SlowRegressionReport:
    suite_name: str
    suite_version: str
    topology_version: str
    router_config_id: str
    scenario_count: int
    trial_count: int
    completed: int
    routing_error: int
    execution_failed: int
    evaluation_error: int
    fixture_error: int
    business_success: int
    business_failure: int
    unique_routes: int
    observed_nodes: int
    total_nodes: int
    observed_edges: int
    total_edges: int
    latency_ms_basics: tuple[float | None, float | None, float | None]
    token_usage: TokenUsage
    unused_edges: int
    failure_by_category: tuple[tuple[str, int], ...] = ()
    # Metering aggregates (output polish P7): summed with the same honesty
    # rule as TrialResult — all-None stays None, absence is never faked as 0.
    cost_tool: float | None = None
    cost_routing: float | None = None
    cost_evaluation: float | None = None
    access_total: int | None = None
    access_resources: int | None = None

    @property
    def coverage_node_rate(self) -> float:
        if self.total_nodes == 0:
            return 0.0
        return self.observed_nodes / self.total_nodes


def build_slow_regression_report(
    outcome: SlowRunOutcome,
    obs: ObservationReport,
    *,
    suite: ScenarioSuite,
    topology: Topology,
    topology_version: str,
    router_config_id: str,
) -> SlowRegressionReport:
    """Aggregate trial outcomes, observation stats and topology facts."""
    completed = 0
    routing_error = 0
    execution_failed = 0
    evaluation_error = 0
    fixture_error = 0
    business_success = 0
    business_failure = 0
    latencies: list[float] = []
    total_input = 0
    total_output = 0

    for result in outcome.results:
        status = result.execution_status
        if status is TrialExecutionStatus.COMPLETED:
            completed += 1
            evaluation = result.evaluation
            if evaluation is not None and evaluation.success:
                business_success += 1
            else:
                business_failure += 1
        elif status is TrialExecutionStatus.ROUTING_ERROR:
            routing_error += 1
        elif status is TrialExecutionStatus.EVALUATION_ERROR:
            evaluation_error += 1
        elif status is TrialExecutionStatus.FIXTURE_ERROR:
            fixture_error += 1
        elif status in _EXECUTION_FAILED:
            execution_failed += 1
        total_input += result.token_usage.input_tokens
        total_output += result.token_usage.output_tokens
        latencies.append(result.latency_ms)

    declared_edges = {(edge.source, edge.target) for edge in topology.edges()}
    used_edges = {
        (stat.source, stat.target)
        for stat in obs.edge_stats.values()
        if stat.observed_count > 0
    }
    unused_edges = len(declared_edges - used_edges)

    access_total = 0
    access_keys: set[str] = set()
    for result in outcome.results:
        if result.access_counts:
            for key, count in result.access_counts.items():
                access_total += count
                access_keys.add(key)

    return SlowRegressionReport(
        suite_name=suite.name,
        suite_version=suite.version,
        topology_version=topology_version,
        router_config_id=router_config_id,
        scenario_count=obs.scenario_count,
        trial_count=len(outcome.results),
        completed=completed,
        routing_error=routing_error,
        execution_failed=execution_failed,
        evaluation_error=evaluation_error,
        fixture_error=fixture_error,
        business_success=business_success,
        business_failure=business_failure,
        unique_routes=obs.unique_route_count,
        observed_nodes=sum(1 for stat in obs.node_stats.values() if stat.selected_count > 0),
        total_nodes=len(topology.nodes()),
        observed_edges=len(used_edges),
        total_edges=len(topology.edges()),
        latency_ms_basics=summarize(latencies),
        token_usage=TokenUsage(input_tokens=total_input, output_tokens=total_output),
        unused_edges=unused_edges,
        failure_by_category=obs.failure_by_category,
        cost_tool=sum_known_costs(
            result.tool_cost for result in outcome.results
        ),
        cost_routing=sum_known_costs(
            result.routing_cost for result in outcome.results
        ),
        cost_evaluation=sum_known_costs(
            result.evaluation_cost for result in outcome.results
        ),
        access_total=access_total if access_keys else None,
        access_resources=len(access_keys) if access_keys else None,
    )


# One-line plain-language glosses for the failure taxonomy, shown inline
# next to the counts (review.md carries the long-form glossary).
FAILURE_CATEGORY_GLOSSES = {
    "tool_selection_error": "router picked an unreachable tool",
    "missing_tool": "router picked a tool that does not exist",
    "wrong_tool_dependency": "all same-layer tools failed (dependency chain)",
    "schema_mismatch": "a tool input could not be resolved from state",
    "tool_execution_error": "a tool raised during execution",
    "reasoning_error": "the LLM judge reasoned incorrectly",
    "answer_error": "business evaluation failed (missing artifacts / quality)",
    "provider_failure": "the LLM / external provider failed",
    "timeout": "a tool exceeded the per-tool timeout",
    "evaluation_error": "the evaluator itself failed",
    "fixture_error": "fixture setup / teardown failed",
    "success": "no failure",
}

_SAMPLE_REASON_LIMIT = 100


def render_slow_report(report: SlowRegressionReport, *, failures=None) -> str:
    """Render the CLI text output (phase3 §107-108).

    ``failures`` (optional trial results) adds a "Failure Samples" section:
    up to two representative trials per category (deterministic by trial
    id) with the evaluation reason. Without it the output is unchanged.
    """
    mean, median, p95 = report.latency_ms_basics
    mean_s = f"{mean:.1f}" if mean is not None else "-"
    median_s = f"{median:.1f}" if median is not None else "-"
    p95_s = f"{p95:.1f}" if p95 is not None else "-"

    lines = [
        "Slow Regression",
        "",
        f"Suite:  {report.suite_name} {report.suite_version}",
        f"Topology: {report.topology_version}",
        f"Router config: {report.router_config_id}",
        "",
        f"Scenarios:       {report.scenario_count}",
        f"Trials:          {report.trial_count}",
        "",
        f"Completed:        {report.completed}",
        f"Routing Failed:   {report.routing_error}",
        f"Execution Failed: {report.execution_failed}",
        f"Evaluation Error: {report.evaluation_error}",
        f"Fixture Error:    {report.fixture_error}",
        "",
        f"Business Success: {report.business_success}",
        f"Business Failed:  {report.business_failure}",
        "",
        f"Unique Routes: {report.unique_routes}",
        "",
        f"Observed Nodes: {report.observed_nodes} / {report.total_nodes}",
        f"Observed Edges: {report.observed_edges} / {report.total_edges}",
        f"Unused Edges:   {report.unused_edges}",
    ]

    # Metering facts (output polish P7): costs render '-' when the whole
    # run is unmetered — absence is never faked as 0 (resource-metering §1).
    cost_tool = f"{report.cost_tool:.4f}" if report.cost_tool is not None else "-"
    cost_routing = (
        f"{report.cost_routing:.4f}" if report.cost_routing is not None else "-"
    )
    cost_evaluation = (
        f"{report.cost_evaluation:.4f}"
        if report.cost_evaluation is not None
        else "-"
    )
    if report.access_total is not None:
        accesses = f"{report.access_total} ({report.access_resources} resources)"
    else:
        accesses = "-"
    lines += [
        "",
        "Metering:",
        f"  Tokens (in / out):   {report.token_usage.input_tokens} / "
        f"{report.token_usage.output_tokens}",
        f"  Tool Cost:           {cost_tool}",
        f"  Routing Cost:        {cost_routing}",
        f"  Evaluation Cost:     {cost_evaluation}",
        f"  Resource Accesses:   {accesses}",
    ]

    # Facts, not recommendations: pruning is a Phase 4 decision (phase3 §108).
    if report.failure_by_category:
        lines.append("")
        lines.append("Failure Categories:")
        for name, count in report.failure_by_category:
            gloss = FAILURE_CATEGORY_GLOSSES.get(name)
            suffix = f"  # {gloss}" if gloss else ""
            lines.append(f"  {name:<26}{count}{suffix}")

    lines.append("")
    if failures:
        samples = _failure_samples(failures)
        if samples:
            lines.append("")
            lines.append("Failure Samples (up to 2 per category):")
            for name, entries in samples:
                lines.append(f"  {name}")
                for trial_id, reason in entries:
                    lines.append(f"    {trial_id} - {reason}")

    lines.append("(Phase 3 observes only; it makes no pruning recommendation.)")
    return "\n".join(lines)
def _failure_samples(failures) -> list[tuple[str, list[tuple[str, str]]]]:
    """Up to two representative (trial id, reason) pairs per failure category."""
    by_category: dict[str, list] = {}
    for result in failures:
        category = result.failure_category
        if category is None:
            continue
        by_category.setdefault(category.value, []).append(result)
    samples = []
    for name in sorted(by_category):
        entries = []
        for result in sorted(
            by_category[name], key=lambda item: item.trial.id
        )[:2]:
            reason = (
                result.evaluation.reason
                if result.evaluation is not None and result.evaluation.reason
                else result.execution_status.value
            )
            if len(reason) > _SAMPLE_REASON_LIMIT:
                reason = reason[:_SAMPLE_REASON_LIMIT] + "..."
            entries.append((result.trial.id, reason))
        samples.append((name, entries))
    return samples
