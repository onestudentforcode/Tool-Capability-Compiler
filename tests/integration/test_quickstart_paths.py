"""Quickstart paths stay executable: committed JSON topologies load from
the repo root (regression guard for the bare-module type-reference bug
found while surveying CLI output: slow_refund types serialize as
'facts:X' when imported script-style, which crashed every CLI consumer).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from capability_runtime import TopologyLoader, unbound_tool_names  # noqa: E402


@pytest.mark.parametrize(
    "topology_path",
    [
        "examples/topology/refund.json",
        "examples/topology/refund_sandbox.json",
        "examples/topology/office.json",
    ],
)
def test_committed_topologies_load_from_repo_root(topology_path) -> None:
    topology = TopologyLoader().load_file(str(_ROOT / topology_path))
    assert topology.nodes()
    assert unbound_tool_names(topology) == ()
