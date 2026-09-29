# Phase 5 实施约定 —— 命名与目录结构

> 定位：动手写代码前，把 Phase 5 的对象命名、包路径、模块职责、错误模型、
> CLI 面与 9 个 Step 的实现顺序一次性敲定。实现以本文件 + phase5.md 的语义
> 为准；与 phase5.md 冲突时命名以本文件为准。

---

## 0. 三条总决定（TL;DR）

1. **新增与 `optimization/` 平级的顶包 `ranking/`**，全部新模块落在
   `capability_runtime/ranking/` 之下，不重构 `regression/` / `optimization/`。
2. **只消费、不执行**：输入是 Phase 3 的 `TrialResult` 序列或其落盘
   `traces.jsonl`（+ `manifest.json`），Phase 5 不跑 Trial、不调 LLM、
   不改 Topology。
3. **统一行视图**：两条输入路径（进程内 / 磁盘 artifacts）先归一到轻量
   `TrialRow`，聚合逻辑只写一份。

---

## 1. 命名与数据规则

- 目录/模块 `snake_case`；类 `PascalCase`；枚举省略 `Enum` 后缀。
- 领域数据一律 `@dataclass(frozen=True, slots=True)`；名称集合输出前升序。
- 公共失败用 `core/errors.py` 自定义异常；数值统计复用
  `regression/slow/stats.py::summarize`（mean/median/p95），保证跨阶段口径一致。

---

## 2. 目录结构

```text
src/capability_runtime/
├── ranking/                   # 新增 —— Phase 5 主体
│   ├── __init__.py
│   ├── stats.py               # wilson_interval / statistical_tie（纯函数）
│   ├── profile.py             # TrialRow / RouteProfile / build_profiles
│   │                          # + rows_from_results（进程内适配）
│   ├── eligibility.py         # RankConfig / EligibilityStatus / RankingEligibility
│   ├── pareto.py              # ParetoFrontier / Domination / build_frontier
│   ├── tier.py                # TierConfig / RouteTier / RouteTierAssignment
│   ├── family.py              # RouteFamily（MVP：route_id 一一对应）
│   └── report.py              # RouteRankingReport / build / render / to_json
│                              # + rows_from_run（traces.jsonl + manifest 适配）
│
└── cli.py                     # 追加 `rank` 子命令（Step 9）
```

根包 `capability_runtime/__init__.py` 统一导出公共符号。

---

## 3. 对象 → 模块映射

| phase5.md 对象 | 落点 | 说明 |
| --- | --- | --- |
| `RouteProfile` | `ranking/profile.py` | 四维向量 + Wilson 区间 + 版本绑定 |
| 行视图（phase5 未命名） | `TrialRow` | ranking 所需的最小 trial 投影，两条输入共用 |
| `RankConfig` | `ranking/eligibility.py` | `min_trials=20` / `confidence_level=0.95` |
| `RankingEligibility` | 同名 dataclass + `EligibilityStatus` 枚举 | RANKED / INSUFFICIENT_EVIDENCE |
| `ParetoFrontier` | `ranking/pareto.py` | 附 `Domination` 归因、`partial_comparisons` 标记 |
| `RouteTierAssignment` | `ranking/tier.py` | 指标快照 + matched_rules 文本；多标签 |
| `TierConfig` | 同上 | 阈值全集中于此，默认值即 phase5 §11.1 |
| `RouteFamily` | `ranking/family.py` | MVP：`family_id == route_id` 单例族 |
| `RouteRankingReport` | `ranking/report.py` | 含 `CategoryRanking` 逐类聚合 |
| category 维度 | `report.py::build_category_rankings` | best_* 只在类内取值（phase5 §13） |

---

## 4. 错误模型（追加到 `core/errors.py`）

```text
RankingError                  # 根（继承 TopologyFrameworkError）
├── RouteProfileError         # 版本混杂 / 计数不一致 / 行数据缺失
└── RankingConfigError        # RankConfig / TierConfig 参数非法
```

---

## 5. 口径裁定（实现必须遵守）

- `business_success_rate` 分母 = **有 evaluation 的 trial 数**
  （`evaluated_count`），不是全部 trial；
- `evaluated_count == 0` 时 rate=0.0、Wilson 区间 `(0.0, 1.0)`（最大无信息）；
- 延迟用 trial 实测 `latency_ms`；quality/cost 列表中缺省值直接剔除，
  全缺省 → 该维 `None`（不臆造 0，延续批次 A 语义）；
- Pareto 目标：`success_rate ↑ / quality_mean ↑ / latency_median ↓ / cost_mean ↓`；
  某维任一方为 `None` → 该维退出本次支配判定并累计 `partial_comparisons`；
  无任何可比维 → 双方均留前沿；
- Tier 判定在 **RANKED** 集合内取 `best_*`；QUALITY 门槛要求
  `best_quality` 存在，否则无人可命中 QUALITY；
- STATISTICAL_TIE 以 success 的 Wilson 区间重叠为准，输出为
  tie 对清单；Tier 数值门槛不受 tie 影响（容差语义已覆盖）。

---

## 6. CLI 面（Step 9）

```bash
tool-topology rank \
    --slow-report artifacts/slow_regression/run_xxx \
    [--scenario scenarios/xxx.json]   # 提供 category 维度；缺省则该维标注不可用
    [--min-trials 20] \
    [--format text|json] \
    [--out PATH]
```

- 读取 `manifest.json`（版本绑定 + run id）与 `traces.jsonl`（行数据）；
- 版本混杂（traces 内 topology/router 不一致）→ `RouteProfileError`，
  CLI 转 stderr + exit 2；
- 退出码恒 0（排名不是 CI 门禁）。

---

## 7. Step 顺序与验收点

| Step | 模块 | 验收点 |
| --- | --- | --- |
| 1 | `core/errors.py` + `ranking/stats.py` | RankingError 族；Wilson 数值正确；tie 判定 |
| 2 | `ranking/profile.py` | TrialRow 两条适配；四维聚合；分母口径；版本混杂报错；结构属性 |
| 3 | `ranking/eligibility.py` | min_trials 门槛；差值清单；配置非法 → RankingConfigError |
| 4 | `ranking/pareto.py` | 支配/互不支配；缺维降级 + partial 标记；被支配归因 |
| 5 | `ranking/tier.py` | 三 Tier 命中/未命中/多标签/UNASSIGNED；快照+规则文本 |
| 6 | `ranking/family.py` | 单例族确定性 |
| 7 | `ranking/report.py` | 报告构建 + text 渲染 + JSON 往返 + category 聚合 |
| 8 | `ranking/report.py`（磁盘适配） | rows_from_run 消费真实 traces.jsonl + manifest |
| 9 | `cli.py` + 集成 | `tool-topology rank`；磁盘端到端（写→读→排） |

测试约束：全部离线；不依赖真实 LLM / 网络。

---

## 8. 阶段边界检查表

- [ ] 不执行 Tool / 不调 LLM / 不修改 Topology
- [ ] 不裁决唯一 Best Route；允许并列 / 不可比 / UNASSIGNED
- [ ] 四维向量不降维（任何加权分只作展示顺序）
- [ ] 版本绑定强制；跨版本不混排
- [ ] 输出可被 Phase 6 无损消费（JSON 字段完整）

---

## 9. 实现进度

| Step | 状态 | 落点 |
| --- | --- | --- |
| 1 | [x] | `core/errors.py` 增 RankingError / RouteProfileError / RankingConfigError；`ranking/stats.py`（z_score / wilson_interval / statistical_tie，statistics.NormalDist 零依赖） |
| 2 | [x] | `ranking/profile.py`：TrialRow 行视图（进程内 `rows_from_results` + 磁盘适配共用）；RouteProfile 四维聚合；success 分母=evaluated；全缺省维度保持 None；版本混杂拒绝 |
| 3 | [x] | `ranking/eligibility.py`：RankConfig / EligibilityStatus / RankingEligibility（shortfall 差值） |
| 4 | [x] | `ranking/pareto.py`：四目标支配 + Domination 归因 + partial_comparisons；目标子集可配 |
| 5 | [x] | `ranking/tier.py`：FAST / BALANCED / QUALITY（多标签、UNASSIGNED、指标快照 + matched_rules） |
| 6 | [x] | `ranking/family.py`：单例族 |
| 7 | [x] | `ranking/report.py`：build_ranking_report / render / to_json / CategoryRanking（类内 best_*）/ statistical_ties |
| 8 | [x] | `rows_from_run`：manifest + traces.jsonl 读取，manifest 与 traces 版本矛盾拒绝 |
| 9 | [x] | CLI `tool-topology rank`（--slow-report / --scenario / --min-trials / --format / --out）；真实 250-trial scale 数据实跑验证（18/41 ranked，min-trials=4） |

全量 474 tests / compileall / diff-check 通过；测试 `tests/unit/test_ranking.py`（32 项，含写→读→排磁盘端到端与混合版本拒绝）。
