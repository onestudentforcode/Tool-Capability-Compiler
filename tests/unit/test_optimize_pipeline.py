"""Optimize pipeline: analyze / validate / commit / rollback (offline).

The end-to-end case runs the executable sandbox refund domain, persists slow
artifacts, then drives the full three-stage flow through the CLI — including
the sentinel-rejection case and record-replay rollback with fingerprint
rejection.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from capability_runtime import (
    ArtifactLoadError,
    CommitGateError,
    TopologyLoader,
    pipeline_commit,
    pipeline_rollback,
)
from capability_runtime.core.tool import NodeSelector, ToolNode, ToolSpec
from capability_runtime.optimization.artifacts import (
    declared_fingerprint,
    export_active_payload,
    patch_fingerprint,
    trial_results_from_dir,
)
from capability_runtime.optimization.pipeline import (
    ValidateConfig,
    analyze as pipeline_analyze,
    patch_from_payload,
    validate as pipeline_validate,
)
from capability_runtime.topology.patch import TopologyPatch

_PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT / "examples" / "slow_refund"))

import refund as sandbox_refund  # noqa: E402
import store  # noqa: E402
from fixtures import SandboxFixtureManager  # noqa: E402


# ---- fixture worlds -------------------------------------------------------------


def _tiny_sync(name: str):
    async def handler() -> dict:
        return {"tool": name}

    return ToolNode(
        spec=ToolSpec(
            name=name,
            layer="only",
            providers=NodeSelector(all_nodes=True),
            workers=NodeSelector(all_nodes=True),
            capabilities=frozenset({f"demo.{name}"}),
            description=f"tiny {name}",
        ),
        handler=handler,
    )


def _payload_run_dir(tmp_path: Path) -> Path:
    """A real sandbox slow run persisted to disk (evidence source)."""
    from capability_runtime import (
        Scenario,
        ScenarioSuite,
        SlowRegressionRunner,
        SlowRegressionWriter,
        build_observation_stats,
        build_slow_regression_report,
    )

    store.STORE.reset("eligible")
    topology, version = sandbox_refund.build_topology(topology_version="v9")
    scenarios = [
        Scenario(id=f"s{index:02d}", query=f"refund {index}", category="refund",
                 metadata={"sentinel": index == 0})
        for index in range(12)
    ]
    suite = ScenarioSuite(
        name="pipeline", version="1.0", description="pipeline", scenarios=tuple(scenarios)
    )
    router = _fixed_router()
    outcome = asyncio.run(
        SlowRegressionRunner(
            topology=topology,
            evaluator=sandbox_refund.build_evaluator(),
            fixture_manager=SandboxFixtureManager(),
            trials_per_scenario=2,
            topology_version=version,
            router_config_id="fake",
            router=router,
        ).run(suite)
    )
    edges = [(edge.source, edge.target) for edge in topology.edges()]
    obs = build_observation_stats(outcome.results, edges=edges)
    report = build_slow_regression_report(
        outcome, obs, suite=suite, topology=topology,
        topology_version=version, router_config_id="fake",
    )
    run_dir = tmp_path / "run_001"
    SlowRegressionWriter(run_dir).write(
        run_id="pipeline-run_001", suite_name=suite.name,
        suite_version=suite.version, topology_version=version,
        router_config_id="fake", evaluator="composite",
        outcome=outcome, report=report, obs=obs,
    )
    return run_dir


def _fixed_router():
    from capability_runtime import FakeRouter

    return FakeRouter(
        layer_selections={
            "read": ["order_db"],
            "analyze": ["policy_check"],
            "action": ["refund_api"],
        }
    )


def _suite_payload() -> dict:
    return {
        "version": "1.0",
        "name": "pipeline",
        "scenarios": [
            {"id": f"s{index:02d}", "query": f"refund {index}",
             "category": "refund", "metadata": {"sentinel": index == 0}}
            for index in range(12)
        ],
    }


# ---- artifacts (Step 1) ------------------------------------------------------------


def test_trial_results_rebuilt_with_version_gate(tmp_path) -> None:
    run_dir = _payload_run_dir(tmp_path)
    results = trial_results_from_dir(run_dir)
    assert len(results) == 24  # 12 scenarios x 2 trials
    assert all(result.route is not None for result in results)
    assert results[0].cost is not None  # metering survived the rebuild

    poisoned = run_dir / "traces.jsonl"
    lines = poisoned.read_text(encoding="utf-8").strip().splitlines()
    row = json.loads(lines[-1])
    row["trial"]["topology_version"] = "v0.9"
    poisoned.write_text("\n".join(lines + [json.dumps(row)]) + "\n", encoding="utf-8")
    with pytest.raises(ArtifactLoadError, match="versions"):
        trial_results_from_dir(run_dir)


def test_fingerprints_are_stable_and_patch_specific() -> None:
    topology, _ = sandbox_refund.build_topology()
    assert declared_fingerprint(topology) == declared_fingerprint(topology)
    other, _ = sandbox_refund.build_topology(topology_version="v-other")
    assert declared_fingerprint(topology) == declared_fingerprint(other)  # version tag无关
    assert patch_fingerprint(("a->b",), ()) != patch_fingerprint((), ("a->b",))


def test_export_active_payload_reproduces_active_space() -> None:
    from capability_runtime import LayerRegistry, ToolRegistry, TopologyBuilder

    topology, _ = sandbox_refund.build_topology()
    payload = json.loads(
        (_PROJECT / "examples" / "topology" / "refund.json").read_text(encoding="utf-8")
    )
    # build a declared payload equivalent to the sandbox topology
    payload = {
        "version": "1.0",
        "layers": [
            {"name": "read", "order": 0},
            {"name": "analyze", "order": 1},
            {"name": "action", "order": 2},
        ],
        "tools": [
            {"name": name, "layer": topology.node(name).spec.layer}
            for name in topology.nodes()
        ],
    }
    patch = TopologyPatch(disabled_nodes=("erp", "web_search"))
    from capability_runtime.topology.patch import apply_patch

    active = apply_patch(topology, patch)
    exported = export_active_payload(payload, active)
    reloaded = TopologyLoader().load_data(exported)
    assert set(reloaded.nodes()) == set(active.nodes())
    assert "erp" not in reloaded.nodes()
    assert set(reloaded.edges()) == set(active.edges())


def test_export_active_payload_keeps_composite_entries_loader_clean() -> None:
    # Composite entries carry kind: composite and the loader's composite
    # schema forbids providers/workers (outer edges default to all).
    # Materializing adjacency into them made the version snapshot
    # unloadable — caught by the closed-loop rollback path after batch F
    # put composites into the office topology (2026-10-04).
    from capability_runtime import (
        LayerRegistry,
        ToolRegistry,
        TopologyBuilder,
    )

    def node(name: str, layer: str) -> ToolNode:
        async def handler() -> dict:
            return {"tool": name}

        return ToolNode(
            spec=ToolSpec(
                name=name,
                layer=layer,
                providers=NodeSelector(all_nodes=True),
                workers=NodeSelector(all_nodes=True),
                capabilities=frozenset({f"demo.{name}"}),
            ),
            handler=handler,
        )

    layers = LayerRegistry()
    layers.register("read", 0)
    layers.register("analyze", 1)
    tools = ToolRegistry()
    tools.register(node("plain_tool", "read"))
    tools.register(node("doc_composed_report", "analyze"))
    topology = TopologyBuilder(layers, tools).build()

    payload = {
        "version": "1.0",
        "layers": [
            {"name": "read", "order": 0},
            {"name": "analyze", "order": 1},
        ],
        "tools": [
            {"name": "plain_tool", "layer": "read"},
            {
                "kind": "composite",
                "name": "doc_composed_report",
                "layer": "analyze",
                "inner": "inner/topo.json",
                "route": [{"layer": "only", "tools": ["worker"]}],
                "stop_when": ["report.final"],
                "max_iterations": 2,
            },
        ],
    }
    exported = export_active_payload(payload, topology)
    composite = next(
        item
        for item in exported["tools"]
        if item.get("kind") == "composite"
    )
    assert composite["name"] == "doc_composed_report"
    assert "providers" not in composite
    assert "workers" not in composite
    # plain tools keep materialized adjacency
    plain = next(
        item for item in exported["tools"] if "kind" not in item
    )
    assert "providers" in plain and "workers" in plain


# ---- analyze (Step 2) ----------------------------------------------------------------


def test_analyze_proposes_candidates_from_real_evidence(tmp_path) -> None:
    run_dir = _payload_run_dir(tmp_path)
    topology, _ = sandbox_refund.build_topology(topology_version="v9")
    from capability_runtime.scenario import ScenarioLoader

    suite = ScenarioLoader().load_data(_suite_payload())
    payload = asyncio.run(pipeline_analyze(topology, suite, run_dir))
    statuses = {item["status"] for item in payload["candidates"]}
    # every sandbox tool is a unique provider -> PROTECTED dominates
    assert "protected" in statuses
    split = payload["split"]
    assert len(split["optimization"]) + len(split["validation"]) + len(
        split["sentinel"]
    ) == 12
    assert split["sentinel"] == ["s00"]
    assert payload["declared_fingerprint"] == declared_fingerprint(topology)
    # round-trip: patch block reloads
    patch = patch_from_payload(payload)
    assert isinstance(patch, TopologyPatch)


def test_analyze_refuses_non_optimization_evidence(tmp_path) -> None:
    run_dir = _payload_run_dir(tmp_path)
    topology, _ = sandbox_refund.build_topology(topology_version="v9")
    tiny = {
        "version": "1.0",
        "name": "tiny",
        "scenarios": [{"id": "zzz", "query": "q"}],  # not in the run
    }
    from capability_runtime.scenario import ScenarioLoader

    suite = ScenarioLoader().load_data(tiny)
    with pytest.raises(ArtifactLoadError, match="optimization split"):
        asyncio.run(pipeline_analyze(topology, suite, run_dir))


# ---- validate (Step 3) ------------------------------------------------------------------


def _executable_world():
    """Two-tool executable topology: probe -> act (implementation-bound)."""
    payload = {
        "version": "1.0",
        "layers": [
            {"name": "read", "order": 0},
            {"name": "act", "order": 1},
        ],
        "tools": [
            {"name": "fetch", "layer": "read",
             "capabilities": ["demo.fetch"],
             "implementation": "tests.unit._binding_tools:fetch"},
            {"name": "analyze", "layer": "act",
             "capabilities": ["demo.analyze"],
             "implementation": "tests.unit._binding_tools:analyze"},
        ],
    }
    suite = {
        "version": "1.0",
        "name": "world",
        "scenarios": [
            {"id": "a", "query": "fetch only", "category": "x",
             "expected_capabilities": ["demo.fetch"]},
            {"id": "b", "query": "fetch and analyze", "category": "x",
             "expected_capabilities": ["demo.fetch", "demo.analyze"],
             "metadata": {"sentinel": True}},
        ],
    }
    return payload, suite


def test_validate_accepts_safe_patch(tmp_path) -> None:
    payload, suite_payload = _executable_world()
    topology = TopologyLoader().load_data(payload)
    from capability_runtime.scenario import ScenarioLoader

    suite = ScenarioLoader().load_data(suite_payload)
    empty = TopologyPatch()
    verdict = asyncio.run(
        pipeline_validate(topology, suite, empty, config=ValidateConfig(trials=1))
    )
    assert verdict["verdict"] == "accept"
    assert verdict["fast"]["passed"] is True
    assert verdict["slow"]["passed"] is True


def test_validate_rejects_sentinel_breaking_patch(tmp_path) -> None:
    payload, suite_payload = _executable_world()
    topology = TopologyLoader().load_data(payload)
    from capability_runtime.scenario import ScenarioLoader

    suite = ScenarioLoader().load_data(suite_payload)
    patch = TopologyPatch(disabled_nodes=("analyze",))
    verdict = asyncio.run(
        pipeline_validate(topology, suite, patch, config=ValidateConfig(trials=1))
    )
    assert verdict["verdict"] == "reject"
    assert verdict["fast"]["passed"] is False
    # fast gate intercepts: slow never ran
    assert verdict["slow"].get("skipped") is True
    assert any(
        f["domain"] in ("global", "category", "sentinel")
        for f in verdict["failures"]
    )
    # fingerprint recorded for the commit gate
    assert verdict["patch_fingerprint"] == patch_fingerprint(
        patch.disabled_edges, patch.disabled_nodes
    )


# ---- commit / rollback (Step 4) -----------------------------------------------------------


def _accepted_verdict(topology, patch) -> dict:
    return {
        "verdict": "accept",
        "declared_fingerprint": declared_fingerprint(topology),
        "patch_fingerprint": patch_fingerprint(
            patch.disabled_edges, patch.disabled_nodes
        ),
    }


def test_commit_gate_and_version_records(tmp_path) -> None:
    payload, _suite = _executable_world()
    topology = TopologyLoader().load_data(payload)
    patch = TopologyPatch(disabled_nodes=("analyze",))
    versions = tmp_path / "versions"

    # gate: no verdict
    with pytest.raises(CommitGateError, match="verdict=accept"):
        pipeline_commit(
            topology, payload, patch, validation={"verdict": "reject"},
            version="v2", versions_dir=versions,
        )
    # gate: fingerprint mismatch
    bad = _accepted_verdict(topology, patch)
    bad["patch_fingerprint"] = "deadbeef"
    with pytest.raises(CommitGateError, match="fingerprint"):
        pipeline_commit(
            topology, payload, patch, validation=bad,
            version="v2", versions_dir=versions,
        )
    # gate: different declared topology
    bad = _accepted_verdict(topology, patch)
    bad["declared_fingerprint"] = "other"
    with pytest.raises(CommitGateError, match="different declared"):
        pipeline_commit(
            topology, payload, patch, validation=bad,
            version="v2", versions_dir=versions,
        )

    record = pipeline_commit(
        topology, payload, patch,
        validation=_accepted_verdict(topology, patch),
        version="v2", versions_dir=versions,
    )
    assert record["version"] == "v2"
    assert (versions / "v2.json").is_file()
    active = TopologyLoader().load_file(versions / "v2.topology.json")
    assert "analyze" not in active.nodes()
    assert json.loads((versions / "current.json").read_text())["version"] == "v2"

    # immutability: same version again refused
    with pytest.raises(CommitGateError, match="immutable"):
        pipeline_commit(
            topology, payload, patch,
            validation=_accepted_verdict(topology, patch),
            version="v2", versions_dir=versions,
        )


def test_rollback_record_replay_and_fingerprint_gate(tmp_path) -> None:
    payload, _suite = _executable_world()
    topology = TopologyLoader().load_data(payload)
    patch = TopologyPatch(disabled_nodes=("analyze",))
    versions = tmp_path / "versions"
    pipeline_commit(
        topology, payload, patch,
        validation=_accepted_verdict(topology, patch),
        version="v2", versions_dir=versions,
    )

    # record replay back to v1 (empty composed patch on the base record? v2's
    # own record replay = v2 again); replay --to v2 works and exports active
    record = pipeline_rollback(
        topology, payload, versions_dir=versions, to="v2"
    )
    assert record["version"] == "v2-restored"
    active = TopologyLoader().load_file(versions / "v2-restored.topology.json")
    assert "analyze" not in active.nodes()

    # default rollback = full restore to declared
    record = pipeline_rollback(topology, payload, versions_dir=versions)
    assert record["version"] == "declared-restored"
    active = TopologyLoader().load_file(
        versions / "declared-restored.topology.json"
    )
    assert "analyze" in active.nodes()

    # fingerprint mismatch: tampered declared topology
    tampered = json.loads(json.dumps(payload))
    tampered["tools"][0]["capabilities"] = ["demo.other"]
    tampered_topology = TopologyLoader().load_data(tampered)
    with pytest.raises(CommitGateError, match="fingerprint mismatch"):
        pipeline_rollback(
            tampered_topology, tampered, versions_dir=versions, to="v2"
        )
    # missing record
    with pytest.raises(CommitGateError, match="no version record"):
        pipeline_rollback(
            topology, payload, versions_dir=versions, to="v99"
        )


# ---- CLI end-to-end (Step 5-6) ---------------------------------------------------------------


import json
from pathlib import Path

from capability_runtime import TopologyLoader


def _argless_world():
    """Two arg-less executable tools across two layers (no typed chaining)."""
    payload = {
        "version": "1.0",
        "layers": [
            {"name": "read", "order": 0},
            {"name": "act", "order": 1},
        ],
        "tools": [
            {"name": "fetch", "layer": "read", "capabilities": ["demo.fetch"],
             "implementation": "tests.unit._optimize_bindings:fetch"},
            {"name": "finalize", "layer": "act",
             "capabilities": ["demo.finalize"],
             "implementation": "tests.unit._optimize_bindings:finalize"},
        ],
    }
    suite = {
        "version": "1.0",
        "name": "argless",
        "scenarios": [
            {"id": "a0", "query": "fetch and finalize", "category": "x",
             "expected_capabilities": ["demo.fetch", "demo.finalize"],
             "metadata": {"sentinel": True}},
            {"id": "a1", "query": "fetch only", "category": "x",
             "expected_capabilities": ["demo.fetch"]},
            {"id": "a2", "query": "fetch again", "category": "x",
             "expected_capabilities": ["demo.fetch"]},
            {"id": "a3", "query": "finalize only", "category": "y",
             "expected_capabilities": ["demo.finalize"]},
            {"id": "a4", "query": "either", "category": "y",
             "expected_capabilities": ["demo.fetch", "demo.finalize"]},
        ],
    }
    return payload, suite


def _argless_run_dir(tmp_path: Path) -> Path:
    """Persist a real slow run for the arg-less world (analyze evidence)."""
    import asyncio

    from capability_runtime import (
        SlowRegressionRunner,
        SlowRegressionWriter,
        build_observation_stats,
        build_slow_regression_report,
    )
    from capability_runtime.evaluation.structured import StructuredEvaluator
    from capability_runtime.scenario import ScenarioLoader

    topology = TopologyLoader().load_data(_argless_world()[0])
    suite = ScenarioLoader().load_data(_argless_world()[1])
    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=StructuredEvaluator({}),
        trials_per_scenario=2,
        topology_version="vA",
        router_config_id="free",
    )
    outcome = asyncio.run(runner.run(suite))
    edges = [(edge.source, edge.target) for edge in topology.edges()]
    obs = build_observation_stats(outcome.results, edges=edges)
    report = build_slow_regression_report(
        outcome, obs, suite=suite, topology=topology,
        topology_version="vA", router_config_id="free",
    )
    run_dir = tmp_path / "argless_run"
    SlowRegressionWriter(run_dir).write(
        run_id="argless-run", suite_name=suite.name, suite_version=suite.version,
        topology_version="vA", router_config_id="free", evaluator="structured",
        outcome=outcome, report=report, obs=obs,
    )
    return run_dir


def test_cli_three_stage_flow_on_argless_world(tmp_path, capsys) -> None:
    import asyncio  # noqa: F401

    from capability_runtime.cli import main

    run_dir = _argless_run_dir(tmp_path)
    payload, suite_payload = _argless_world()
    topology_file = tmp_path / "topology.json"
    topology_file.write_text(json.dumps(payload), encoding="utf-8")
    suite_file = tmp_path / "suite.json"
    suite_file.write_text(json.dumps(suite_payload), encoding="utf-8")
    candidates = tmp_path / "candidates.json"
    verdict_file = tmp_path / "verdict.json"
    versions = tmp_path / "versions"

    code = main([
        "optimize", "analyze",
        "--topology", str(topology_file),
        "--scenario", str(suite_file),
        "--slow-report", str(run_dir),
        "--out", str(candidates),
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "candidates written" in out
    # (P5) analyze header echoes the inputs
    assert "Optimize Analyze" in out
    assert f"slow report: {run_dir}" in out

    code = main([
        "optimize", "validate",
        "--topology", str(topology_file),
        "--scenario", str(suite_file),
        "--patch", str(candidates),
        "--trials", "1",
        "--out", str(verdict_file),
    ])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "VERDICT: ACCEPT" in out
    # output polish (P3): the three gates render before the verdict
    assert "Gates:" in out
    assert "fast (coverage)     PASS" in out
    assert "slow (regression)   PASS" in out
    assert "diversity           PASS" in out
    # (P5) header echoes the inputs
    assert "Optimize Validate" in out
    assert f"patch:    {candidates}" in out
    assert verdict_file.is_file()

    code = main([
        "optimize", "commit",
        "--topology", str(topology_file),
        "--patch", str(candidates),
        "--validation", str(verdict_file),
        "--version", "v2",
        "--versions-dir", str(versions),
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "v2 committed" in out
    committed = TopologyLoader().load_file(versions / "v2.topology.json")
    assert set(committed.nodes()) == {"fetch", "finalize"}

    code = main([
        "optimize", "rollback",
        "--topology", str(topology_file),
        "--versions-dir", str(versions),
        "--to", "v2",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "v2-restored" in out


def test_cli_commit_refuses_without_accept_record(tmp_path, capsys) -> None:
    from capability_runtime.cli import main

    payload, _ = _executable_world()
    topology_file = tmp_path / "t.json"
    topology_file.write_text(json.dumps(payload), encoding="utf-8")
    patch_file = tmp_path / "p.json"
    patch_file.write_text(json.dumps({"patch": {"disabled_nodes": ["analyze"]}}),
                          encoding="utf-8")
    verdict_file = tmp_path / "v.json"
    verdict_file.write_text(json.dumps({"verdict": "reject"}), encoding="utf-8")
    code = main([
        "optimize", "commit",
        "--topology", str(topology_file),
        "--patch", str(patch_file),
        "--validation", str(verdict_file),
        "--version", "v2",
        "--versions-dir", str(tmp_path / "vv"),
    ])
    captured = capsys.readouterr()
    assert code == 2
    assert "verdict=accept" in captured.err




def test_rollback_snapshot_is_self_contained_with_composites(tmp_path) -> None:
    # Version snapshots must load on their own: composite inner paths are
    # relative to the DECLARED file, so the snapshot copies each inner
    # topology next to itself and rewrites the reference. Caught on the
    # office closed loop after batch F put composites in the default
    # topology (2026-10-04).
    from capability_runtime import TopologyLoader
    from capability_runtime.optimization.pipeline import rollback

    inner = {
        "version": "1.0",
        "layers": [{"name": "only", "order": 0}],
        "tools": [
            {
                "name": "fetch",
                "layer": "only",
                "implementation": "tests.unit._binding_tools:fetch",
            }
        ],
    }
    (tmp_path / "inner.json").write_text(
        json.dumps(inner), encoding="utf-8"
    )
    declared = {
        "version": "1.0",
        "layers": [{"name": "act", "order": 0}],
        "tools": [
            {
                "name": "macro",
                "layer": "act",
                "kind": "composite",
                "inner": "inner.json",
                "route": [{"layer": "only", "tools": ["fetch"]}],
                "stop_when": ["note"],
                "max_iterations": 2,
                "capabilities": ["demo.macro"],
            }
        ],
    }
    declared_path = tmp_path / "declared.json"
    declared_path.write_text(json.dumps(declared), encoding="utf-8")
    topology = TopologyLoader().load_file(declared_path)

    record = rollback(
        topology,
        declared,
        versions_dir=tmp_path / "versions",
        source=declared_path,
    )
    assert record["version"] == "declared-restored"
    snapshot = tmp_path / "versions" / "declared-restored.topology.json"
    restored = TopologyLoader().load_file(snapshot)
    assert "macro" in restored.nodes()
    assert (tmp_path / "versions" / "macro_inner.json").is_file()
