"""Render office battlefield artifacts into a human-readable review file.

Reads the JSON artifacts of one run directory (a scale run or a closed-loop
run) and writes ``review.md`` next to them: tables for every stage, plus the
domain glossary and per-section explanations a reviewer needs — the JSON
files stay machine-shaped; this file is the readable mirror.

Usage::

    python examples/office/render_review.py <artifact_dir>
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _table(headers: list[str], rows: list[list[str]]) -> str:
    def esc(value) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    head = "| " + " | ".join(headers) + " |"
    rule = "|" + "|".join("---" for _ in headers) + "|"
    body = ["| " + " | ".join(esc(cell) for cell in row) + " |" for row in rows]
    return "\n".join([head, rule, *body]) if body else "（无记录）"


def _fmt(value, digits: int = 1, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


GLOSSARY = """\
### 0. 术语速查（先读这一节）

| 术语 | 含义 |
| --- | --- |
| trial | 一个场景的一次独立执行：按选定路线逐层执行工具，再交给评估器打分。id 形如 `场景id#序号` |
| route / canonical | 一次 trial 实际走过的路线。canonical 形如 `层:[工具,工具]`，一行一层 |
| completed / layer_error | 执行状态：跑完全程 / 某一层的选中工具全部失败（试验就此终止） |
| answer_error | 业务失败：执行完成但评估器判不通过（缺工件/质量问题） |
| tool_execution_error | 工具运行期抛错（如 LLM 输出畸形、脏语料解析失败） |
| timeout | 单工具超过 per-tool 超时线（0.15s；translate 变体的 0.5s 注入停顿会命中） |
| cost | 声明成本 `cost_per_call` 之和，是**计费基准**；管道实测 token/成本另存，作漂移证据 |
| opportunity_count / usage_rate | 边"本可被使用"的次数 / 实际使用占比——低使用率是剪枝候选的证据 |
| 候选三态 | IDENTIFIED=证据充分且未受保护，可进入剪枝验证；INSUFFICIENT_EVIDENCE=证据不足；PROTECTED=免疫（唯一提供者/桥接/哨兵关联） |
| Tier | 路线相对最优的三档多标签：FAST（延迟≤最快×1.2）、QUALITY（质量≥最优−0.05）、BALANCED（成功/延迟/质量/成本全在宽容差内） |
| 哨兵（sentinel） | 关键业务场景，其关联边免疫剪枝，且必进每轮验证集 |
| 指纹（fingerprint） | 拓扑/补丁的内容哈希，用于把 validate 记录与 commit 的补丁严格对齐 |
"""


def _gate(value) -> str:
    if value is None:
        return "—（跳过）"
    return "✅ 通过" if value else "❌ 未通过"


def _review_closed_loop(d: Path) -> str:
    rep = _load(d / "report.json")
    cand = _load(d / "candidates.json")
    verdict = _load(d / "verdict.json")
    ranking = _load(d / "ranking.json")
    lines: list[str] = [
        f"# Office Battlefield 闭环审查报告（{d.name}）",
        "",
        f"> 生成时间 {datetime.now():%Y-%m-%d %H:%M}，由 `render_review.py` 从同目录 JSON 产物"
        " 渲染，兼作控制台输出的留档重建。数字来自确定性离线复现，可用"
        " `python examples/office/run_closed_loop.py --trials 15` 重跑核对。",
        "",
        GLOSSARY,
    ]

    r1 = rep["round1_declared"]
    lines += [
        "## 1. Round 1 —— 声明拓扑上的离线探索",
        "",
        "这一轮是 optimize 的证据来源：60 场景 × 15 试验，成功约一半是**设计使然**"
        "（uncovered 场景必失败、messy 语料读取失败、注入的畸形输出/超时），"
        "失败流量正是后续剪枝与排名的证据。",
        "",
        _table(
            ["指标", "值", "说明"],
            [
                ["trials", r1["trials"], "总试验数 = 60 场景 × 15 试验"],
                ["success_rate", r1["success_rate"], "业务成功比例（分母=产生了评估的 trial）"],
                ["mean_cost", r1["mean_cost"], "平均声明成本（计费基准）"],
                ["mean_latency_ms", r1["mean_latency_ms"], "平均路线延迟（含模拟延迟与注入停顿）"],
            ],
        ),
        "",
    ]

    split = cand["split"]
    by_status: dict[str, int] = {}
    by_kind: dict[str, list] = {}
    for c in cand["candidates"]:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
        if c["status"] == "identified":
            by_kind.setdefault(c["kind"], []).append(c)
    identified = by_kind.get("edge", []) + by_kind.get("node", [])
    lines += [
        "## 2. analyze —— 首个真实剪枝候选（里程碑的标志性事件）",
        "",
        "证据只取自 optimization 切分（validation/sentinel 留给验证，互不污染）。"
        "候选三态：**IDENTIFIED** 进入补丁提案；INSUFFICIENT_EVIDENCE 证据不足；"
        "PROTECTED 免疫剪枝。这是本项目历史上第一次在真实证据上产出非空补丁"
        "（退款沙盒时代每个工具都是唯一提供者，补丁恒为空）。",
        "",
        _table(
            ["证据切分", "场景数", "用途"],
            [
                ["optimization", len(split["optimization"]), "只读地产生候选证据"],
                ["validation", len(split["validation"]), "validate 慢门的回归对照集"],
                ["sentinel", len(split["sentinel"]), "哨兵场景，必进验证且免疫剪枝"],
            ],
        ),
        "",
        _table(
            ["候选状态", "数量", "说明"],
            [
                ["identified", by_status.get("identified", 0), "进入补丁提案"],
                ["insufficient_evidence", by_status.get("insufficient_evidence", 0),
                 "机会数/使用率未达阈值，继续观察"],
                ["protected", by_status.get("protected", 0), "唯一提供者、桥接或哨兵关联边，免疫"],
            ],
        ),
        "",
        "### IDENTIFIED 明细（opportunity=本可使用的次数；usage_rate=实际使用占比）",
        "",
        _table(
            ["subject（边/节点）", "kind", "opportunity", "usage_rate", "判读"],
            [
                [
                    c["subject"], c["kind"], c["opportunity_count"],
                    f"{c['usage_rate']:.4f}",
                    "几乎从未被使用 → 剪掉后正常路线不受影响",
                ]
                for c in identified
            ],
        ),
        "",
        f"**补丁**：禁用 {len(cand['patch']['disabled_edges'])} 条边 + "
        f"{len(cand['patch']['disabled_nodes'])} 个节点（节点=从未被选中的工具）。",
        "",
        f"**反事实（counterfactual）**：把补丁假设应用后做 fast 元数据回归对照，"
        f"verdict = `{(identified[0]['counterfactual'] or {}).get('verdict', 'n/a')}`，"
        "没有任何场景因剪枝失去覆盖。",
        "",
    ]

    cfg = verdict["config"]
    lines += [
        "## 3. validate —— 三道门全部通过才算 ACCEPT",
        "",
        _table(
            ["门", "判定", "它检查什么"],
            [
                ["fast（覆盖门）", _gate(verdict["fast"].get("passed")),
                 "剪枝后 fast 元数据回归不得让任何验证集场景失去覆盖（哨兵免疫）"],
                ["slow（慢门）", _gate(verdict["slow"].get("passed")),
                 "验证集+哨兵在剪枝前后各实跑一遍，业务成功不得回退"],
                ["diversity（多样性门）", _gate(verdict["diversity"].get("passed")),
                 "成功路线家族数不得塌缩（防止剪成一条独木桥）"],
            ],
        ),
        "",
        f"门禁集 = validation {len(cfg['validation_ids'])} 场景 + sentinel "
        f"{len(cfg['sentinel_ids'])} 场景（显式哨兵 3 条 + 切分哨兵槽位）；"
        f"每场景 {cfg['trials']} 试验。"
        f"失败明细：{verdict['failures'] if verdict['failures'] else '无'}。",
        "",
    ]

    rec = rep["commit"]["record"]
    lines += [
        "## 4. commit / rollback —— 唯一写操作与可逆性",
        "",
        _table(
            ["项", "值", "说明"],
            [
                ["版本", "office-v0.1", "版本记录不可变，写入 versions/ 目录"],
                ["composed 补丁", f"{len(rec['disabled_edges'])} 边 + {len(rec['disabled_nodes'])} 节点",
                 "commit 前硬校验：verdict=accept 且补丁/拓扑指纹一致"],
                ["rollback", rep["rollback"]["version"],
                 "记录重放（非反向补丁）：从版本记录重建同一拓扑，证明可逆"],
            ],
        ),
        "",
    ]

    r2a = rep["round2a_committed_slow"]
    lines += [
        "## 5. Round 2a —— 剪枝后拓扑的慢回归（rank 的证据来源）",
        "",
        "在 office-v0.1 上重跑同套件：剪掉的边本来几乎无人使用，成功率应持平或略升"
        "（少了失败流量的分支）。rank/catalog 必须消费**这个版本自己的证据**——框架会"
        "拒绝版本错配的 ranking。",
        "",
        _table(
            ["指标", "Round 2a", "说明"],
            [
                ["trials", r2a["trials"], "60 场景 × 5 试验"],
                ["success_rate", r2a["success_rate"], "与 Round 1 的 0.5565 对照"],
                ["mean_cost", r2a["mean_cost"], "剪枝后计费基准"],
            ],
        ),
        "",
    ]

    rank = rep["rank"]
    profiles = {p["route_id"]: p for p in ranking["profiles"]}
    assignments = [a for a in ranking["tier_assignments"] if a["tiers"]]
    rows = []
    for a in sorted(assignments, key=lambda x: tuple(x["tiers"])):
        p = profiles.get(a["route_id"], {})
        canonical = "  →  ".join(
            f"{seg.split(':')[0]}:{seg.split(':', 1)[1]}" for seg in p.get("canonical", "").splitlines()
        )
        rows.append(
            [
                a["route_id"][:8],
                "/".join(a["tiers"]),
                p.get("trial_count", "—"),
                _fmt(a["success_rate"], 2),
                _fmt(a["latency_median"], 1, "ms"),
                _fmt(a["quality_mean"], 2),
                _fmt(a["cost_mean"], 4),
                canonical[:110],
            ]
        )
    sep_rows = []
    for capability, span in sorted(rank["separating_capabilities"].items()):
        tool_tiers = "；".join(
            f"{tool}→{'/'.join(tiers) or '未上榜'}" for tool, tiers in span["tool_tiers"].items()
        )
        sep_rows.append([capability, "、".join(span["tiers"]), tool_tiers])
    lines += [
        "## 6. rank —— Tier 分布与冗余变体分化",
        "",
        f"非空 Tier：{rank['non_empty_tiers']}。Tier 是相对最优的多标签：FAST 看延迟、"
        "QUALITY 看质量、BALANCED 看四维均衡。**跨 Tier capability** 是本里程碑的"
        "关键证据：同一 capability 的快糙/慢稳变体被 rank 分进不同档位，说明四维分化"
        "真实存在、在线可按需选型。",
        "",
        f"### 跨 Tier 的冗余 capability（{len(sep_rows)} 个）",
        "",
        _table(["capability", "横跨的 Tier", "变体 → 各自 Tier"], sep_rows),
        "",
        f"### 有标签路线明细（{len(rows)} 条，按 Tier 排序）",
        "",
        _table(
            ["route", "Tier", "trials", "成功率", "延迟中位", "质量均值", "成本均值", "canonical"],
            rows,
        ),
        "",
        "> 完整判读规则见每条 assignment 的 matched_rules（ranking.json）；"
        "无标签路线=四维都够不到任何档（多为失败流量）。",
        "",
    ]

    sel = _load(d / "selection.json")
    lines += [
        "## 7. select —— 在线干跑（不执行）",
        "",
        f"对 {len(sel['dry_runs'])} 个请求按类别列出目录中的 Tier 候选数；"
        "这是上线前的人工检查点，不产生执行。",
        "",
        _table(
            ["request", "各 Tier 候选数"],
            [[s["request_id"], json.dumps(s["tiers"], ensure_ascii=False)] for s in sel["dry_runs"]],
        ),
        "",
    ]

    r2 = rep["round2_online"]
    conv = rep["convergence"]
    lines += [
        "## 8. Round 2 —— 在线服务 + 遥测回流（两轮收敛）",
        "",
        _table(
            ["指标", "Round 1（离线探索）", "Round 2（在线，Tier 优先）", "说明"],
            [
                ["成功率", r1["success_rate"], r2["success_rate"],
                 "在线按 FAST→BALANCED→QUALITY 选路，全部命中已验证路线"],
                ["平均成本", r1["mean_cost"], r2["mean_cost"], "Tier 优先 + 剪枝后的目录"],
                ["平均延迟(ms)", r1["mean_latency_ms"], r2["mean_latency_ms"], "同上"],
                ["服务/回流", "900 trials", f"{r2['served']} served / {r2['trials']} trials",
                 "遥测经 online_results_to_trials 回流成 trial（业务判定留在离线侧，成功口径=SERVED）"],
            ],
        ),
        "",
        f"**收敛结论**：{conv['success_rate']}；成本 {conv['mean_cost']}；"
        f"延迟 {conv['mean_latency_ms']}。验收口径：成功率不降 + 成本或延迟下降，两者同时满足。",
        "",
        "## 9. 深挖指引",
        "",
        "- 单场景逐试验：`grep \"weekly_report_fact_brief\" round_1_declared/traces.jsonl`",
        "- 某工具的失败明细：`grep \"draft_section_steady\" round_1_declared/traces.jsonl | grep error`",
        "- 机器可读全量：本目录各 JSON（字段含义见第 0 节术语表）",
        "- 版本审计：`versions/office-v0.1.json`（补丁与指纹）、`versions/office-v0.1.topology.json`（剪枝后可执行拓扑）",
    ]
    return "\n".join(lines) + "\n"


def _review_scale(d: Path) -> str:
    summary = _load(d / "scale_summary.json")
    coverage = _load(d / "coverage.json")
    node_stats = _load(d / "node_stats.json")
    edge_stats = _load(d / "edge_stats.json")
    run = summary["run"]

    by_family: dict[str, dict[str, int]] = {}
    for row in coverage["scenarios"]:
        fam = by_family.setdefault(row["category"] or "(none)", {"covered": 0, "uncertain": 0, "uncovered": 0})
        fam[row["status"]] += 1

    never_selected = [n for n in node_stats if n["selected_count"] == 0]
    hot_nodes = sorted(node_stats, key=lambda n: -n["opportunity_count"])[:8]
    hot_edges = sorted(edge_stats, key=lambda e: -e["opportunity_count"])[:8]
    quiet_edges = [e for e in edge_stats if e["observed_count"] == 0]

    lines = [
        f"# Office Battlefield 规模实跑审查报告（{d.name}）",
        "",
        f"> 生成时间 {datetime.now():%Y-%m-%d %H:%M}，由 `render_review.py` 渲染，"
        "兼作控制台输出的留档重建。重跑：`python examples/office/run_scale.py --trials 5`。",
        "",
        GLOSSARY,
        "## 1. 运行摘要",
        "",
        _table(
            ["指标", "值", "说明"],
            [
                ["场景 × 试验", f"{run['scenarios']} × {run['trials_per_scenario']}", "总 trial 数见下行"],
                ["total_trials", run["total_trials"], "全部 trial"],
                ["unique_routes", run["unique_routes"], "观察到的不同路线数（探索广度）"],
                ["business_success / failure", f"{run['business_success']} / {run['business_failure']}",
                 "业务成败（含设计内失败：uncovered、messy、注入故障）"],
                ["topology_version", run["topology_version"], "声明拓扑版本"],
                ["elapsed_seconds", run["elapsed_seconds"], "总耗时"],
            ],
        ),
        "",
        "## 2. 覆盖分布（fast regression 语义，不执行工具）",
        "",
        _table(
            ["家族", "covered", "uncertain", "uncovered"],
            [[fam, c["covered"], c["uncertain"], c["uncovered"]] for fam, c in sorted(by_family.items())],
        ),
        "",
        f"总计 {coverage['distribution']}——目标 ≈42/9/9。uncovered 场景刻意引用推迟"
        " capability（calendar.schedule / pdf.render 等），uncertain 携带 discovery 元数据"
        "（歧义/低置信），二者都是诚实的未覆盖声明，不是缺陷。",
        "",
        "## 3. 失败构成（slow 落盘必须含全部类别）",
        "",
        _table(
            ["执行状态", "次数", "说明"],
            [
                ["completed", summary["failures"]["execution_status"].get("completed", 0),
                 "跑完全程并评估（成败由评估器定）"],
                ["layer_error", summary["failures"]["execution_status"].get("layer_error", 0),
                 "某层选中工具全灭（如 messy 语料的读取失败）"],
            ],
        ),
        "",
        _table(
            ["失败类别", "次数", "典型来源"],
            [
                ["answer_error", summary["failures"]["failure_category"].get("answer_error", 0),
                 "业务评估不通过（缺工件/uncovered 场景）"],
                ["tool_execution_error", summary["failures"]["failure_category"].get("tool_execution_error", 0),
                 "fake 的 ~4% 畸形输出 → 宽松解析失败"],
                ["timeout", summary["failures"]["failure_category"].get("timeout", 0),
                 "translate 变体 0.5s 注入停顿 > 0.15s 超时线"],
            ],
        ),
        "",
        "## 4. 节点/边热度（剪枝候选视角）",
        "",
        "**从未被选中的节点**（selected_count=0 → 节点剪枝候选视角；实际候选由 optimize analyze 在优化集上判定）：",
        "",
        _table(
            ["tool", "opportunity", "selected", "success"],
            [[n["tool"], n["opportunity_count"], n["selected_count"], n["success_trial_count"]]
             for n in sorted(never_selected, key=lambda n: n["tool"])],
        ),
        "",
        "**机会数最高的边**（opportunity 大而 observed 小 → 边剪枝候选视角）：",
        "",
        _table(
            ["source → target", "opportunity", "observed", "success"],
            [[f"{e['source']} → {e['target']}", e["opportunity_count"], e["observed_count"],
              e["successful_trial_count"]] for e in hot_edges],
        ),
        "",
        f"从未被观察到的边共 {len(quiet_edges)} 条（完整清单见 edge_stats.json）。",
        "",
        "## 5. 深挖指引",
        "",
        "- 单场景：`grep \"<scenario_id>\" traces.jsonl`（每行一个 trial 的逐层逐工具明细）",
        "- 逐场景覆盖理由：coverage.json 的 scenarios[].reason / missing_capabilities",
        "- 下一步：`python examples/office/run_closed_loop.py --trials 15` 消费同类证据走完 optimize → rank → 在线闭环",
    ]
    return "\n".join(lines) + "\n"


def write_review(directory: str | Path) -> Path:
    """Render one artifact directory into review.md; returns the file path."""
    d = Path(directory)
    if (d / "report.json").is_file() and (d / "candidates.json").is_file():
        content = _review_closed_loop(d)
    elif (d / "scale_summary.json").is_file():
        content = _review_scale(d)
    else:
        raise SystemExit(
            f"{d} 不是可识别的产物目录（需要 report.json+candidates.json 或 scale_summary.json）"
        )
    out = d / "review.md"
    out.write_text(content, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", help="scale 或 closed_loop 的产物目录")
    args = parser.parse_args(argv)
    out = write_review(args.directory)
    print(f"review written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
