"""Office battlefield batch C: the LLM tool layer, fully offline.

Acceptance (office-battlefield.md §9 C): every LLM tool runs against an
injected fake resource (zero network); the section.draft variants
differentiate on all four evidence dimensions (declared cost, measured
tokens, configured latency, output length); malformed LLM output raises
ValueError and classifies as TOOL_EXECUTION_ERROR both at tool level and
through a SlowRegressionRunner trial; the seeded store rng makes the
translate stall deterministic and hit/miss observable.
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from capability_runtime import (  # noqa: E402
    EvaluationResult,
    Evaluator,
    LayerRegistry,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    ToolExecutionStatus,
    ToolRegistry,
    TopologyBuilder,
    TrialExecutionStatus,
    TrialFailureCategory,
    tool,
)
from capability_runtime.resources import LLMResource  # noqa: E402
from capability_runtime.resources.metering import (  # noqa: E402
    MeteringSource,
    mount_collector,
    unmount_collector,
)

from examples.office import facts, office_llm, store, tools_llm  # noqa: E402

# ---- offline fake plumbing --------------------------------------------------


def fake_llm(
    content: str,
    *,
    prompt_tokens: int = 10,
    completion_tokens: int = 10,
    calls: list[dict] | None = None,
) -> LLMResource:
    """A fake LLMResource: `_http` answers from a fixed string, OpenAI-shape."""

    def _http(payload: dict) -> dict:
        if calls is not None:
            calls.append(payload)
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        }

    return LLMResource(model="fake", _http=_http)


@pytest.fixture(autouse=True)
def offline_llm():
    """Every test is offline: a default fake handle, the real one restored."""
    original = office_llm.RESOURCE
    office_llm.set_resource(fake_llm("{}"))
    yield
    office_llm.set_resource(original)


async def call_metered(node, *args):
    """Invoke a handler the way ToolExecutor does: metering mounted."""
    token = mount_collector()
    try:
        result = await node.handler(*args)
    finally:
        collector = unmount_collector(token)
    return result, collector


def _source_doc(text: str = "Q3 revenue grew 12 percent to 4.2M in EMEA.") -> facts.SourceDoc:
    return facts.SourceDoc(doc_id="d1", title="Weekly Report", text=text)


# ---- inventory ---------------------------------------------------------------

EXPECTED_NODES = {
    # name: (layer, capability, declared cost_per_call)
    "keyfact_extract": ("extract", "fact.extract", 0.003),
    "text_summarize_fast": ("extract", "text.summarize", 0.001),
    "text_summarize_steady": ("extract", "text.summarize", 0.01),
    "text_translate_fast": ("extract", "text.translate", 0.001),
    "text_translate_steady": ("extract", "text.translate", 0.01),
    "style_profile": ("extract", "style.profile", 0.004),
    "table_header_fix_llm": ("extract", "table.repair", 0.006),
    "outline_doc_gen_fast": ("compose", "outline.compose", 0.001),
    "outline_doc_gen_steady": ("compose", "outline.compose", 0.01),
    "outline_slide_gen": ("compose", "outline.slides", 0.005),
    "draft_section_fast": ("compose", "section.draft", 0.001),
    "draft_section_steady": ("compose", "section.draft", 0.01),
    "draft_section_verbose": ("compose", "section.draft", 0.02),
    "draft_email_concise": ("compose", "mail.draft", 0.002),
    "draft_email_detailed": ("compose", "mail.draft", 0.008),
    "draft_speaker_notes": ("compose", "notes.draft", 0.004),
    "formula_gen_fast": ("compose", "formula.generate", 0.001),
    "formula_gen_steady": ("compose", "formula.generate", 0.01),
    "slide_copy_fast": ("compose", "slide.copy", 0.001),
    "slide_copy_steady": ("compose", "slide.copy", 0.01),
    "text_polish_conservative": ("compose", "text.polish", 0.004),
    "text_polish_aggressive": ("compose", "text.polish", 0.006),
    "text_expand": ("compose", "text.expand", 0.005),
    "title_gen": ("compose", "title.compose", 0.0008),
    "chart_type_pick_rule": ("compose", "chart.select", 0.0005),
    "chart_type_pick_llm": ("compose", "chart.select", 0.004),
    "theme_palette_pick": ("compose", "palette.select", 0.0005),
    "insight_narrate": ("compose", "insight.narrate", 0.003),
    "fact_check_judge": ("verify", "fact.verify", 0.005),
    "quality_judge": ("verify", "quality.judge", 0.004),
    "grammar_check_llm": ("verify", "text.grammar", 0.005),
}

DOMAIN_TYPES = frozenset(
    {
        facts.SourceDoc,
        facts.DataTable,
        facts.SlideDigest,
        facts.AggregateResult,
        facts.FactSheet,
        facts.Outline,
        facts.FormulaSpec,
        facts.SlideOutline,
        facts.StyleSpec,
        facts.ChartSpec,
        facts.Draft,
        facts.SlideCopy,
        facts.Narrative,
        facts.EmailDraft,
        facts.ReviewReport,
        facts.FileSpec,
    }
)


def test_node_inventory_matches_batch_c_spec() -> None:
    assert len(tools_llm.NODES) == 31
    assert len(EXPECTED_NODES) == 31
    for node in tools_llm.NODES:
        layer, capability, cost = EXPECTED_NODES[node.spec.name]
        assert node.spec.layer == layer, node.spec.name
        assert node.spec.capabilities == frozenset({capability}), node.spec.name
        assert node.spec.cost_per_call == pytest.approx(cost), node.spec.name
        assert set(node.spec.consumes) <= DOMAIN_TYPES, node.spec.name
        assert set(node.spec.produces) <= DOMAIN_TYPES, node.spec.name
        # exporter contract: implementation bindings go by attribute name
        assert getattr(tools_llm, node.spec.name) is node, node.spec.name

    counts = Counter(node.spec.layer for node in tools_llm.NODES)
    assert counts == Counter({"compose": 21, "extract": 7, "verify": 3})
    layer_sequence = [node.spec.layer for node in tools_llm.NODES]
    assert layer_sequence == ["extract"] * 7 + ["compose"] * 21 + ["verify"] * 3


# ---- positive paths ----------------------------------------------------------


def test_positive_paths_produce_typed_artifacts() -> None:
    sheet = facts.FactSheet(doc_id="d1", facts=("revenue up 12%", "4.2M total"))
    messy_table = facts.DataTable(
        table_id="sales", columns=("rgion", "month"), rows=(("emea", "jan"), ("apac", "feb"))
    )

    async def scenario():
        # keyfact_extract: valid JSON -> FactSheet anchored to the source doc
        office_llm.set_resource(fake_llm('{"facts": ["revenue up 12%", "4.2M total"]}'))
        extracted, collector = await call_metered(tools_llm.keyfact_extract, _source_doc())
        assert isinstance(extracted, facts.FactSheet)
        assert extracted.doc_id == "d1"
        assert extracted.facts == ("revenue up 12%", "4.2M total")
        assert collector.source is MeteringSource.MEASURED  # tokens metered automatically

        # draft_section (factory variant): source doc + fact sheet -> Draft
        office_llm.set_resource(fake_llm('{"title": "Overview", "body": "Steady prose."}'))
        draft, _ = await call_metered(tools_llm.draft_section_steady, _source_doc(), sheet)
        assert isinstance(draft, facts.Draft)
        assert draft.title == "Overview"
        assert draft.body == "Steady prose."

        # quality_judge: verdict JSON -> ReviewReport
        office_llm.set_resource(fake_llm('{"passed": true, "issues": [], "score": 0.9}'))
        report, _ = await call_metered(tools_llm.quality_judge, draft)
        assert isinstance(report, facts.ReviewReport)
        assert report.passed is True
        assert report.issues == ()
        assert report.score == pytest.approx(0.9)

        # table_header_fix_llm: header repaired, data rows untouched
        office_llm.set_resource(fake_llm('{"columns": ["region", "month"]}'))
        fixed, _ = await call_metered(tools_llm.table_header_fix_llm, messy_table)
        assert isinstance(fixed, facts.DataTable)
        assert fixed.table_id == "sales"
        assert fixed.columns == ("region", "month")
        assert fixed.rows == (("emea", "jan"), ("apac", "feb"))

    asyncio.run(scenario())


def test_lenient_json_tolerates_prose_and_rejects_garbage() -> None:
    assert office_llm.lenient_json('{"a": 1}') == {"a": 1}
    assert office_llm.lenient_json('Sure: {"a": {"b": "]"}}, done.') == {"a": {"b": "]"}}
    with pytest.raises(ValueError):
        office_llm.lenient_json("total garbage not json")
    with pytest.raises(ValueError):
        office_llm.lenient_json("[1, 2, 3]")

    # prose around the object is tolerated end-to-end (lenient positive path)
    office_llm.set_resource(fake_llm('Here you go: {"facts": ["x"]} — hope that helps.'))

    async def scenario():
        extracted, _ = await call_metered(tools_llm.keyfact_extract, _source_doc())
        assert extracted.facts == ("x",)

    asyncio.run(scenario())


# ---- variant differentiation (office-battlefield.md §9 C) --------------------


def test_draft_section_variants_differentiate_on_four_dimensions() -> None:
    long_body = "elaborated point after elaborated point. " * 12

    def style_fake(payload: dict) -> dict:
        # usage tokens scale with the prompt style the variant sent
        system = payload["messages"][0]["content"]
        if "one short sentence" in system:
            style = "fast"
        elif "elaborate" in system:
            style = "verbose"
        else:
            style = "steady"
        bodies = {
            "fast": '{"title": "T", "body": "one line"}',
            "steady": '{"title": "T", "body": "a full careful section with several sentences."}',
            "verbose": '{"title": "T", "body": "' + long_body + '"}',
        }
        usage = {"fast": (8, 4), "steady": (32, 48), "verbose": (40, 220)}
        return {
            "choices": [{"message": {"content": bodies[style]}}],
            "usage": {
                "prompt_tokens": usage[style][0],
                "completion_tokens": usage[style][1],
            },
        }

    office_llm.set_resource(LLMResource(model="fake", _http=style_fake))
    sheet = facts.FactSheet(doc_id="d1", facts=("f1", "f2"))
    variants = (
        tools_llm.draft_section_fast,
        tools_llm.draft_section_steady,
        tools_llm.draft_section_verbose,
    )

    async def scenario():
        return {
            node.spec.name: await call_metered(node, _source_doc(), sheet)
            for node in variants
        }

    outcomes = asyncio.run(scenario())
    assert len(outcomes) == 3

    # (a) declared cost: three distinct values, straight from the tool specs
    costs = [node.spec.cost_per_call for node in variants]
    assert costs == [0.001, 0.01, 0.02]

    # (b) measured tokens: the fake's usage varies with the prompt style, and
    # the pipeline meters it onto each call with no tool-side reporting code
    output_tokens = {
        name: collector.tokens.output_tokens for name, (_, collector) in outcomes.items()
    }
    assert sorted(output_tokens.values()) == [4, 48, 220]
    for _, collector in outcomes.values():
        assert collector.source is MeteringSource.MEASURED

    # (c) configured latency: three distinct values in the variant table
    latencies = [config["latency"] for config in tools_llm.DRAFT_SECTION_VARIANTS]
    assert latencies == [0.006, 0.025, 0.015]

    # (d) output length: the fake content scales with style, drafts differ
    bodies = {name: result.body for name, (result, _) in outcomes.items()}
    lengths = {name: len(body) for name, body in bodies.items()}
    assert (
        lengths["draft_section_fast"]
        < lengths["draft_section_steady"]
        < lengths["draft_section_verbose"]
    )


def test_every_redundant_family_differentiates_its_variants() -> None:
    by_name = {node.spec.name: node for node in tools_llm.NODES}
    families = (
        tools_llm.SUMMARIZE_VARIANTS,
        tools_llm.TRANSLATE_VARIANTS,
        tools_llm.OUTLINE_DOC_VARIANTS,
        tools_llm.DRAFT_SECTION_VARIANTS,
        tools_llm.FORMULA_GEN_VARIANTS,
        tools_llm.SLIDE_COPY_VARIANTS,
    )
    for table in families:
        nodes = [by_name[config["name"]] for config in table]
        assert len({node.spec.cost_per_call for node in nodes}) == len(nodes)
        assert len({config["style"] for config in table}) == len(table)
        assert len({config["latency"] for config in table}) == len(table)


# ---- parse-failure classification --------------------------------------------


def test_malformed_llm_output_raises_value_error() -> None:
    office_llm.set_resource(fake_llm("total garbage not json"))

    async def scenario():
        with pytest.raises(ValueError):
            await call_metered(tools_llm.keyfact_extract, _source_doc())

    asyncio.run(scenario())


class ToolErrorSurfacingEvaluator(Evaluator):
    """AlwaysPass-style gate that surfaces per-tool failure categories.

    The runner only evaluates COMPLETED trials and a whole-layer failure
    carries no trial-level category, so this inline topology keeps one
    succeeding sibling in the failing layer: the trial completes, and the
    evaluator fails it with the failed tool's own error_category
    (TOOL_EXECUTION_ERROR for the malformed LLM payload).
    """

    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        for layer in trace.layers:
            for execution in layer.tool_executions:
                if execution.status is ToolExecutionStatus.ERROR:
                    return EvaluationResult(
                        success=False,
                        quality_score=0.0,
                        reason=f"tool {execution.tool_name} failed: {execution.error}",
                        category=execution.error_category,
                    )
        return EvaluationResult(success=True, quality_score=1.0)


def test_parse_failure_classifies_as_tool_execution_error_in_runner() -> None:
    office_llm.set_resource(fake_llm("total garbage not json"))

    layers = LayerRegistry()
    layers.register("extract", 0)
    layers.register("compose", 1)

    @tool(layer="extract", produces=[facts.SourceDoc], cost_per_call=0.001)
    async def stub_source_doc() -> facts.SourceDoc:
        return facts.SourceDoc(doc_id="d1", title="T", text="body")

    @tool(layer="extract", produces=[facts.FactSheet], cost_per_call=0.001)
    async def stub_facts() -> facts.FactSheet:
        return facts.FactSheet(doc_id="d1", facts=("f1",))

    @tool(layer="compose", produces=[facts.Draft], cost_per_call=0.001)
    async def stub_draft() -> facts.Draft:
        return facts.Draft(title="stub", body="ok")

    registry = ToolRegistry()
    registry.register(stub_source_doc)
    registry.register(stub_facts)
    registry.register(stub_draft)  # succeeds, so the layer (and trial) completes
    registry.register(tools_llm.draft_section_fast)  # fake returns garbage
    topology = TopologyBuilder(layers, registry).build()

    suite = ScenarioSuite(
        name="office-batch-c",
        version="0.1",
        scenarios=(Scenario(id="s-parse-fail", query="draft a section"),),
    )
    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=ToolErrorSurfacingEvaluator(),
        trials_per_scenario=1,
    )
    outcome = asyncio.run(runner.run(suite))

    result = outcome.results[0]
    assert result.execution_status is TrialExecutionStatus.COMPLETED
    assert result.failure_category is TrialFailureCategory.TOOL_EXECUTION_ERROR
    tool_errors = [
        execution.error_category
        for layer in result.trace.layers
        for execution in layer.tool_executions
        if execution.status is ToolExecutionStatus.ERROR
    ]
    assert tool_errors == [TrialFailureCategory.TOOL_EXECUTION_ERROR]


# ---- seeded rng: determinism and the translate stall --------------------------


def test_store_rng_reset_is_deterministic() -> None:
    store.STORE.reset("clean", scenario_id="s", trial_index=1)
    first = [store.STORE.rng.random() for _ in range(6)]
    store.STORE.reset("clean", scenario_id="s", trial_index=1)
    second = [store.STORE.rng.random() for _ in range(6)]
    assert first == second
    assert first

    store.STORE.reset("clean", scenario_id="s", trial_index=2)
    other = [store.STORE.rng.random() for _ in range(6)]
    assert other != first


def _first_seed_where(predicate) -> int:
    for trial_index in range(64):
        store.STORE.reset("clean", scenario_id="s", trial_index=trial_index)
        if predicate(store.STORE.rng.random()):
            return trial_index
    raise AssertionError("no seeded draw satisfies the predicate in 64 trials")


def test_translate_fast_stall_is_seeded_and_rng_driven(monkeypatch) -> None:
    office_llm.set_resource(fake_llm('{"title": "t", "text": "translated text"}'))
    doc = _source_doc()

    async def scenario():
        return await call_metered(tools_llm.text_translate_fast, doc)

    # hit branch: first draw < 0.2 -> the 0.5s stall is injected
    hit_seed = _first_seed_where(lambda draw: draw < 0.2)
    store.STORE.reset("clean", scenario_id="s", trial_index=hit_seed)  # rewind
    sleeps: list[float] = []

    async def record_sleep(delay, *args, **kwargs):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    translated, _ = asyncio.run(scenario())
    assert sleeps == [pytest.approx(0.006), 0.5]
    assert isinstance(translated, facts.SourceDoc)
    assert translated.text == "translated text"

    # miss branch: first draw >= 0.2 -> only the latency sleep happens
    miss_seed = _first_seed_where(lambda draw: draw >= 0.2)
    store.STORE.reset("clean", scenario_id="s", trial_index=miss_seed)  # rewind
    sleeps.clear()
    translated, _ = asyncio.run(scenario())
    assert sleeps == [pytest.approx(0.006)]
    assert translated.text == "translated text"


# ---- judges -------------------------------------------------------------------


def test_judge_tools_parse_multi_issue_verdicts() -> None:
    verdict = (
        '{"passed": false, "issues": ["unsupported claim", "wrong number", '
        '"tone drift"], "score": 0.42}'
    )
    office_llm.set_resource(fake_llm(verdict))
    draft = facts.Draft(title="T", body="B")
    sheet = facts.FactSheet(doc_id="d1", facts=("f",))

    async def scenario():
        reports = {}
        for node in (
            tools_llm.fact_check_judge,
            tools_llm.quality_judge,
            tools_llm.grammar_check_llm,
        ):
            args = (draft, sheet) if node is tools_llm.fact_check_judge else (draft,)
            reports[node.spec.name] = await call_metered(node, *args)
        return reports

    reports = asyncio.run(scenario())
    assert set(reports) == {"fact_check_judge", "quality_judge", "grammar_check_llm"}
    for name, (report, _) in reports.items():
        assert isinstance(report, facts.ReviewReport), name
        assert report.passed is False
        assert report.issues == ("unsupported claim", "wrong number", "tone drift")
        assert report.score == pytest.approx(0.42)
