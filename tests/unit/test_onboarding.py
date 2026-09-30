"""Onboarding assist: inference, batch adapter, proposals, apply gate, CLI.

All offline — the LLM proposal path always injects a fake transport
(mirroring test_llm_router). The review gate is structural: proposals have
no parameter path to disk; apply only consumes an explicit approved mapping.
"""

# NOTE: no `from __future__ import annotations` — the inference tests need
# real (non-string) annotations on the tools declared below.

import asyncio
import inspect
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from capability_runtime import (
    ApplyError,
    CapabilityProposalSet,
    OnboardingError,
    ProposalError,
    RegistrationError,
    ToolExecutionStatus,
    ToolExecutor,
    ExecutionContext,
    ExecutionEnvironment,
    ExecutionState,
    ToolSummary,
    apply_capabilities,
    from_openai_specs,
    propose_capabilities,
    render_capability_diff,
    tool,
)


# ---- batch A: annotation inference --------------------------------------------


@dataclass(frozen=True)
class Order:
    id: str


@dataclass(frozen=True)
class Decision:
    ok: bool


def test_inference_matches_explicit_declaration() -> None:
    @tool(layer="read")
    async def inferred() -> Order:
        return Order("1")

    @tool(layer="read", consumes=(), produces=(Order,))
    async def explicit() -> Order:
        return Order("1")

    assert inferred.spec.consumes == explicit.spec.consumes == ()
    assert inferred.spec.produces == explicit.spec.produces == (Order,)

    @tool(layer="analyze", capabilities={"a.b"})
    async def inferred_check(order: Order) -> Decision:
        return Decision(True)

    @tool(layer="analyze", capabilities={"a.b"}, consumes=(Order,),
          produces=(Decision,))
    async def explicit_check(order: Order) -> Decision:
        return Decision(True)

    assert inferred_check.spec.consumes == explicit_check.spec.consumes
    assert inferred_check.spec.produces == explicit_check.spec.produces


def test_explicit_declaration_wins_over_inference() -> None:
    @tool(layer="analyze", produces=(Decision,), consumes=(Order,))
    async def check(order: Order) -> Decision:
        return Decision(True)

    assert check.spec.consumes == (Order,)
    assert check.spec.produces == (Decision,)


def test_missing_annotation_error_points_the_way() -> None:
    with pytest.raises(RegistrationError, match="annotate the parameter"):
        @tool(layer="analyze")
        async def broken(order) -> Decision:  # noqa: ANN001
            return Decision(True)


def test_string_annotation_error_mentions_future_import() -> None:
    async def typed(order) -> Decision:  # noqa: ANN001
        return Decision(True)

    typed.__annotations__["order"] = "Order"  # simulate future-annotations
    with pytest.raises(RegistrationError, match="future"):
        tool(layer="analyze")(typed)


def test_string_return_annotation_skips_inference_silently() -> None:
    async def entry() -> object:
        return {"x": 1}

    entry.__annotations__["return"] = "dict"
    node = tool(layer="read")(entry)
    assert node.spec.produces == ()


def test_none_return_annotation_stays_empty() -> None:
    @tool(layer="act")
    async def fire_and_forget() -> None:
        return None

    assert fire_and_forget.spec.produces == ()


# ---- batch B: OpenAI batch adapter --------------------------------------------


SPECS = [
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Fetch an order by id",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_orders",
            "description": "Search orders",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _dispatch(name: str, args: dict) -> dict:
    return {"tool": name, "args": args}


def test_batch_adapter_registers_all_specs_zero_per_tool_code() -> None:
    nodes = from_openai_specs(SPECS, _dispatch, layer="read")
    assert [node.spec.name for node in nodes] == ["get_order", "search_orders"]
    assert all(node.spec.layer == "read" for node in nodes)
    assert all(node.spec.capabilities == frozenset() for node in nodes)
    assert "order_id" in nodes[0].spec.description  # schema merged in


def test_batch_adapter_executes_sync_and_async_dispatch() -> None:
    async def async_dispatch(name: str, args: dict) -> dict:
        return {"tool": name, "async": True}

    sync_nodes = from_openai_specs(SPECS[:1], _dispatch, layer="read")
    async_nodes = from_openai_specs(SPECS[:1], async_dispatch, layer="read")
    executor = ToolExecutor(
        ExecutionContext(environment=ExecutionEnvironment.SANDBOX)
    )
    for node in (*sync_nodes, *async_nodes):
        execution = asyncio.run(
            executor.execute(node, ExecutionState(query="q"))
        )
        assert execution.status is ToolExecutionStatus.SUCCESS
        assert execution.output_summary["tool"] == "get_order"


def test_batch_adapter_rejects_bad_input() -> None:
    with pytest.raises(OnboardingError, match="name"):
        from_openai_specs([{"function": {}}], _dispatch, layer="read")
    with pytest.raises(OnboardingError, match="duplicates"):
        from_openai_specs(SPECS + SPECS[:1], _dispatch, layer="read")
    with pytest.raises(OnboardingError, match="dispatch"):
        from_openai_specs(SPECS, "nope", layer="read")


# ---- batch C: proposals + apply -----------------------------------------------


SUMMARIES = (
    ToolSummary(name="get_order", layer="read",
                description="Fetch an order by id"),
    ToolSummary(name="issue_refund", layer="act",
                description="Execute a refund for an order"),
)


def _proposal_body(payload) -> dict:
    import json as _json

    content = _json.dumps(
        {
            "proposals": [
                {"tool": "get_order", "capabilities": ["order.read"],
                 "rationale": "reads order data", "confidence": 0.9},
                {"tool": "issue_refund",
                 "capabilities": ["refund.execute", "Bad Name"],
                 "confidence": 0.8},
            ]
        }
    )
    assert "get_order" in payload["messages"][1]["content"]
    return {"choices": [{"message": {"content": content}}]}


def test_propose_parses_drops_invalid_and_prefers_vocabulary() -> None:
    proposal_set = asyncio.run(
        propose_capabilities(
            SUMMARIES,
            vocabulary={"order.read", "order.search"},
            _http=_proposal_body,
        )
    )
    by_tool = {item.tool: item for item in proposal_set.proposals}
    assert by_tool["get_order"].capabilities == ("order.read",)
    assert by_tool["issue_refund"].capabilities == ("refund.execute",)
    assert proposal_set.invalid_dropped[0].capability == "Bad Name"

    diff = render_capability_diff(
        proposal_set, vocabulary={"order.read", "order.search"}
    )
    assert "order.read [vocab]" in diff
    assert "refund.execute [new]" in diff
    assert "Bad Name" in diff
    assert "Nothing is written" in diff


def test_propose_rejects_empty_and_bad_transport() -> None:
    with pytest.raises(ProposalError, match="at least one tool"):
        asyncio.run(propose_capabilities((), _http=_proposal_body))
    with pytest.raises(ProposalError, match="missing message content"):
        asyncio.run(
            propose_capabilities(SUMMARIES, _http=lambda payload: {})
        )


def _skeleton() -> dict:
    return {
        "version": "1.0",
        "layers": [{"name": "read", "order": 0}],
        "tools": [
            {"name": "get_order", "layer": "read",
             "capabilities": ["order.read"]},
            {"name": "issue_refund", "layer": "read"},
        ],
    }


def test_apply_merges_capabilities_and_validates_roundtrip(tmp_path) -> None:
    out = tmp_path / "topology.json"
    written = apply_capabilities(
        _skeleton(),
        {"issue_refund": ["refund.execute"]},
        out_path=out,
    )
    payload = json.loads(written.read_text(encoding="utf-8"))
    by_name = {item["name"]: item for item in payload["tools"]}
    assert by_name["issue_refund"]["capabilities"] == ["refund.execute"]
    # existing capabilities are unioned, not replaced
    assert by_name["get_order"]["capabilities"] == ["order.read"]

    from capability_runtime import TopologyLoader

    TopologyLoader().load_file(written)  # roundtrip validation inside apply


def test_apply_gate_rejects_unknown_tools_and_bad_capabilities(tmp_path) -> None:
    with pytest.raises(ApplyError, match="not in the topology"):
        apply_capabilities(
            _skeleton(), {"ghost": ["a.b"]}, out_path=tmp_path / "a.json"
        )
    with pytest.raises(ApplyError, match="invalid"):
        apply_capabilities(
            _skeleton(),
            {"issue_refund": ["Not Valid"]},
            out_path=tmp_path / "b.json",
        )
    with pytest.raises(ApplyError, match="no tools"):
        apply_capabilities(
            {"tools": []}, {}, out_path=tmp_path / "c.json"
        )


# ---- batch D: CLI three stages --------------------------------------------------


def test_cli_scaffold_produce_loadable_skeleton(tmp_path, capsys) -> None:
    from capability_runtime.cli import main
    from capability_runtime import TopologyLoader

    specs = tmp_path / "specs.json"
    specs.write_text(json.dumps(SPECS), encoding="utf-8")
    out = tmp_path / "skeleton.json"
    code = main(
        [
            "onboard", "scaffold",
            "--specs", str(specs), "--layer", "read",
            "--out", str(out),
        ]
    )
    assert code == 0
    assert "2 tools" in capsys.readouterr().out
    topology = TopologyLoader().load_file(out)
    entry = topology.node("get_order")
    assert entry.spec.layer == "read"
    assert entry.spec.capabilities == frozenset()
    assert "order_id" in entry.spec.description
    # without a dispatch module the skeleton is metadata-only (unbound),
    # exactly like any fast-regression topology
    from capability_runtime import unbound_tool_names

    assert set(unbound_tool_names(topology)) == {"get_order", "search_orders"}


def test_cli_propose_writes_proposals_and_diff(tmp_path, capsys, monkeypatch):
    from capability_runtime import cli

    skeleton = tmp_path / "skeleton.json"
    skeleton.write_text(json.dumps(_skeleton()), encoding="utf-8")
    vocabulary = tmp_path / "vocab.json"
    skeleton2 = dict(_skeleton())
    skeleton2["tools"][0]["capabilities"] = ["order.read"]
    vocabulary.write_text(json.dumps(skeleton2), encoding="utf-8")
    out = tmp_path / "proposals.json"

    async def fake_propose(summaries, *, vocabulary=None, **kwargs):
        return CapabilityProposalSet(
            proposals=(
                __import__("capability_runtime").CapabilityProposal(
                    tool="get_order", capabilities=("order.read",)
                ),
            )
        )

    monkeypatch.setattr(cli, "propose_capabilities", fake_propose)
    code = cli.main(
        [
            "onboard", "propose",
            "--topology", str(skeleton),
            "--vocabulary-from", str(vocabulary),
            "--out", str(out),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "order.read [vocab]" in captured.out
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["proposals"][0]["tool"] == "get_order"


def test_cli_full_flow_feeds_fast_regression(tmp_path, capsys) -> None:
    """apply 产物可被 regression fast 直接消费（端到端闭环）。"""
    from capability_runtime.cli import main

    specs = tmp_path / "specs.json"
    specs.write_text(json.dumps(SPECS[:1]), encoding="utf-8")
    skeleton = tmp_path / "skeleton.json"
    final = tmp_path / "topology.json"
    approved = tmp_path / "approved.json"
    scenarios = tmp_path / "scenarios.json"

    assert main(
        ["onboard", "scaffold", "--specs", str(specs), "--layer", "read",
         "--out", str(skeleton)]
    ) == 0
    approved.write_text(
        json.dumps({"get_order": ["order.read"]}), encoding="utf-8"
    )
    assert main(
        ["onboard", "apply", "--topology", str(skeleton),
         "--approved", str(approved), "--out", str(final)]
    ) == 0
    scenarios.write_text(
        json.dumps(
            {
                "version": "1.0",
                "name": "onboard",
                "scenarios": [
                    {
                        "id": "s1",
                        "query": "fetch my order",
                        "category": "order",
                        "expected_capabilities": ["order.read"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    capsys.readouterr()
    code = main(
        [
            "regression", "fast",
            "--topology", str(final), "--scenario", str(scenarios),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Covered:    1" in out


def test_cli_apply_failure_path_exits_nonzero(tmp_path, capsys) -> None:
    from capability_runtime.cli import main

    skeleton = tmp_path / "skeleton.json"
    skeleton.write_text(json.dumps(_skeleton()), encoding="utf-8")
    approved = tmp_path / "approved.json"
    approved.write_text(json.dumps({"ghost": ["a.b"]}), encoding="utf-8")
    code = main(
        ["onboard", "apply", "--topology", str(skeleton),
         "--approved", str(approved), "--out", str(tmp_path / "f.json")]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "not in the topology" in captured.err
