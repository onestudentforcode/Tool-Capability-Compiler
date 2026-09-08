from __future__ import annotations

from enum import Enum


class TrialFailureCategory(str, Enum):
    """A single, normalized classification of why a Trial failed.

    Unifies the coarse ``TrialExecutionStatus`` buckets, the typed exception
    tree, and the evaluator's business verdict into one taxonomy that can be
    aggregated across a run.
    """

    # No failure -- the trial completed and the business verdict succeeded.
    SUCCESS = "success"
    # The router picked a tool that exists but is unreachable in this layer.
    TOOL_SELECTION_ERROR = "tool_selection_error"
    # Execution picked a tool that does not exist anywhere in the topology.
    MISSING_TOOL = "missing_tool"
    # Same-layer tools all failed, suggesting a dependency/provider chain error.
    WRONG_TOOL_DEPENDENCY = "wrong_tool_dependency"
    # Tool argument resolution failed (missing / type-mismatched input).
    SCHEMA_MISMATCH = "schema_mismatch"
    # The tool itself raised during invocation.
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    # The model reasoned incorrectly (LLM judge judgement).
    REASONING_ERROR = "reasoning_error"
    # The produced answer / final result did not satisfy the business goal.
    ANSWER_ERROR = "answer_error"
    # The LLM / external provider failed (network, HTTP, auth).
    PROVIDER_FAILURE = "provider_failure"
    # Execution exceeded the per-tool timeout.
    TIMEOUT = "timeout"
    # The evaluator itself failed (distinct from a business failure).
    EVALUATION_ERROR = "evaluation_error"
    # Fixture setup / teardown failed.
    FIXTURE_ERROR = "fixture_error"