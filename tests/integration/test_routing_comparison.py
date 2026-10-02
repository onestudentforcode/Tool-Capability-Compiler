"""Three-mode routing comparison smoke (discovery-routing batch E).

Runs the comparison on a small office scenario subset; asserts all three
modes ran, the seeded modes hold the line against free exploration, and
both report files landed. Fully offline (fake tool LLM + scripted router).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from examples.office.run_routing_comparison import main  # noqa: E402


def test_three_mode_comparison_smoke(tmp_path) -> None:
    out_dir = tmp_path / "cmp"
    code = main([
        "--limit", "6", "--trials", "1", "--out-dir", str(out_dir),
    ])
    assert code == 0

    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert set(report["modes"]) == {"free", "basefast", "llm-scripted"}
    for mode in report["modes"]:
        metrics = report["modes"][mode]["metrics"]
        assert metrics["trials"] == 6
        assert 0.0 <= metrics["success_rate"] <= 1.0
    # seeded exploration holds the line against free exploration
    assert (
        report["modes"]["basefast"]["metrics"]["success_rate"]
        >= report["modes"]["free"]["metrics"]["success_rate"]
    )
    assert report["modes"]["basefast"]["seeds"]["frozen"] >= 1
    assert report["modes"]["llm-scripted"]["seeds"]["frozen"] >= 1

    comparison = (out_dir / "comparison.md").read_text(encoding="utf-8")
    assert "三模式路由对照报告" in comparison
    assert "P12" in comparison and "P13" in comparison
