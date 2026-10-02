# Ranking Tiers — Tier 语义与口径

> 适用对象：`tool-topology rank`、`ranking/` 顶包的全部消费方。
> 本文是 Phase 5 语义 + 办公靶场修正（discovery-routing 批次 C/D-era，
> 提交 `e0c1e95`）的成文版；规则本体在
> `src/capability_runtime/ranking/tier.py`，本文解释"为什么是这么判的"。

## 1. 数据流与资格

```text
slow 工件 (traces.jsonl + manifest.json)
  → rows_from_run → RouteProfile（每条路线一个四维向量）
  → check_eligibility（trial_count >= RankConfig.min_trials 才 RANKED）
  → Pareto 前沿 + Tier 多标签（只对 RANKED 路线）
```

- **min_trials**：试验数不足的路线是 INSUFFICIENT_EVIDENCE，永不进
  Pareto 与 Tier——几条试验撑不起任何统计判断。
- **参照池**（关键口径，P11）：best 延迟 / 成本 / 质量 / 成功率在
  **至少成功交付过一次**的 RANKED 路线上取
  （`business_success_count > 0`）。零成功路线不是服务选项：它们的
  （往往极小的）延迟成本会把参照值压到任何成功路线都够不到的位置，
  使 FAST/BALANCED 在一切有失败流量的域里结构性不可达
  ——该缺陷由办公靶场首次暴露并修复。
- **success_ok 门**：任何标签都要求路线成功率 ≥ best_success −
  `success_tolerance`（默认 0.02）——便宜但不可靠的路线拿不到标签。

## 2. 三档多标签规则（相对 RANKED 池内最优，阈值集中在 TierConfig）

| Tier | 条件（全部满足） | 默认容差 |
| --- | --- | --- |
| FAST | `latency_median ≤ best_latency × (1 + fast_latency_tolerance)` | 0.20 |
| QUALITY | `quality_mean ≥ best_quality − quality_tolerance`（唯一显式要求质量存在的档） | 0.05 |
| BALANCED | 成功率达标 ∧ 延迟 ≤ best×(1+0.50) ∧ 质量 ≥ best−0.10 ∧ 成本 ≤ best×(1+0.50) | balanced_* 三项 |

- **多标签**：一条例线可同时持有多个 Tier（如 fast+quality）——
  它们在不同的维度上都是好选择。
- **UNASSIGNED**：一条 RANKED 路线可能一个档都不匹配（tiers 为空
  元组）——它仍出现在向量表里，只是无标签；在线 select 会把无标签
  路线作为该类别的最后兜底（ranked-but-unlabelled）。
- **维度缺失**：质量均值不存在时不阻塞 BALANCED（"全局缺失不设障"），
  QUALITY 档则自然不可匹配。

## 3. 确定性与口径注意事项

- 同一 Registry 与同一输入产生确定性输出：路线按 route_id 升序判定，
  参照值取法确定（min/max over 池），无随机数。
- **延迟与耗时是时变量**：跨运行的 Tier 结果可能因毫秒级抖动在容差
  边界翻转。要稳的结论请放大真实差距（如变体延迟对比度），不要调
  容差去迁就噪声（办公靶场教训，office-battlefield-notes.md §4.3）。
- **口径敏感性**：失败率不同的域可能需要不同 TierConfig（例如
  success_tolerance 提高以容忍不稳定的高风险路线）；阈值集中在
  TierConfig，调参不改语义。
- 参照池口径是本文件成文时的一次**语义修复**而非调参：修复前
  "best" 含零成功路线，修复后语义为"在真实可服务的路线里相对最优"。

## 4. 与在线的关系

`build_catalog(topology, ranking, topology_version)` 强制
ranking.topology_version 与所服务拓扑版本一致（版本错配拒绝）——
**每个版本有自己的证据与 Tier**；剪枝后的 Tier 结论来自剪枝后
拓扑自己的 slow 证据（discovery-routing-plan.md 批次 E 流程）。
