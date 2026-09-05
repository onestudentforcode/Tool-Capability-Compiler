# Phase 4 实施约定 —— 命名与目录结构

> 定位：在动手写任何代码前，把 Phase 4 的对象命名、包路径、模块职责、错误模型、
> CLI 面、以及 13 个 Step 的实现顺序一次性敲定。实现时一律以本文件为准，
> 若与 `docs/acceptance/phase4.md` 冲突，以本文件 + phase4.md 的语义为准（命名以本文件为准）。

---

## 0. 三条总决定（TL;DR）

1. **包名保持 `capability_runtime`，不新建 `tool_topology`。** Phase 4 的全部新模块都落在
   `capability_runtime` 之下，且新增一个与 `regression/` 平级的顶层包 `optimization/`
   （文档 §119 的 `tool_topology/optimization/` → `capability_runtime/optimization/`）。
2. **`Topology` 声明模型保持不变，新增"补丁/覆盖"层 `TopologyPatch`，不物理删改。**
   声明式 `Topology`（provider / worker / edges）始终不可变；Phase 4 只在其上叠加
   `disabled_edges / disabled_nodes`，得到 `CandidateTopology` 供反事实与剪枝验证。
3. **Phase 4 严格消费 Phase 3 产物，不重写执行器。** 反事实与探针都复用现成的
   `FastRegressionRunner` / `SlowRegressionRunner`；新代码只做"证据 → 候选 → 验证"。

---

## 0.1 设计裁定（用户要求对齐）：探针复用 basefast 定向 seed，不新增 guided / replay

> 覆盖 phase4.md §50 / §147 / Step 6 的表述。已对齐：**Phase 3 不存在 `guided / replay` 模式**，
> 只有 `free`（自由探索）与 `basefast`（以 Fast Regression 的 CandidateRoute 为 seed，
> 逐层生成 ExpansionPlan 轮转变体）。

Phase 4 的 `ProbeRunner` **复用 `basefast`**，针对候选 Edge 构造"定向 seed"再跑 Slow
Regression：

```text
Fast Regression（找出候选 Edge）     SlowRegressionRunner + 定向 seed（强制纳入目标 Edge）
      ↓ 每 Scenario 产 CandidateRoute     ↓ 逐层 ExpansionPlan 轮转
   CandidateDetector          ──────►   directed probe trial（增加该 Edge 被探索的机会）
```

只有 `CandidateTopology` 的 Fast Counterfactual 通过（覆盖降为 0 才继续）之后，才进入
`ProbeRunner`；探针结果再喂给 Slow Validation Gate。

---

## 0.2 Phase 4 消费的 Phase 3 现有符号（不重造）

| Phase 4 需要 | 现有落点 |
|---|---|
| Edge 计数 opportunity/observed | `regression/slow/stats.py` → `EdgeObservationStats`（`ObservationReport.edge_stats`） |
| Node 计数 available/selected | `NodeObservationStats`（`ObservationReport.node_stats`） |
| 路线分布 | `RouteObservationStats`（`ObservationReport.route_stats`）+ `ObservedRoute.route_id` |
| 业务成败 | `SlowRegressionReport.business_success / business_failure` |
| topo 覆盖 / unused | `SlowRegressionReport.unused_edges`；Fast 侧 `CoverageAnalyzer` |
| 反事实 / 探针执行 | `FastRegressionRunner` / `SlowRegressionRunner`（注入 topo / evaluator / router） |
| delta | `compute_expansion_deltas`（`regression/slow/stats.py`） |

**注意缺口**：逐边"成功路线支持数（successful_route_count / scenario_count）"在现有
`EdgeObservationStats` 没有现成字段，需在 `EvidenceAggregator`（Step 1）里从 trace 端重新聚合。

---

## 1. 命名规则

- 目录/模块：`snake_case`；类：`PascalCase`；枚举：`PascalCase`（省略 `Enum` 后缀）。
- 每个新模块自带 `__init__.py`，并在外层包 `capability_runtime/__init__.py` 汇总导出。
- 领域数据一律 `@dataclass(frozen=True, slots=True)`（与 0–3 一致）。
- 公共失败一律用 `core/errors.py` 里定义的项目自定义异常，禁止泄露 `KeyError/ValueError`。
- 集合对外输出前按名称升序规范化（延续既有规则）。
- phase4.md 推荐对象名**尽量保留**，只改位置（映射见 §3），避免文档与代码概念错位。

---

## 2. 最终目录结构

```text
src/capability_runtime/
├── core/                       # 已有；本阶段只做增量
│   ├── errors.py               # 追加 Phase 4 异常（见 §4）
│   └── metrics.py              # 已有 TokenUsage，复用
│
├── optimization/               # 新增 —— Phase 4 主体（与 regression 平级、隔离命名）
│   ├── __init__.py
│   ├── analyzer.py             # OptimizationAnalyzer：编排"检测→反事实→探针→验证→提交"
│   │                           #            + DatasetSplit（Optimization/Validation/Sentinel）
│   ├── evidence.py             # EvidenceAggregator / NodeEvidence / EdgeEvidence
│   │                           #            (+ 逐边成功路线支持数，trace 端重聚合)
│   ├── candidate.py            # Candidate / CandidateStatus / CandidateReason
│   │                           #  + CandidateDetector + ProtectionRegistry
│   ├── counterfactual.py       # CounterfactualRunner（Fast Counterfactual on TopologyPatch）
│   ├── probe.py                # ProbeRunner（复用 basefast 定向 seed）
│   ├── batch.py                # BatchCandidateBuilder + 渐进剪枝 + max_pruning_batch_size
│   ├── pruning.py              # PruningDecision / FastValidationGate / SlowValidationGate
│   │                           #  + RouteDiversityGuard
│   │                           # （命名按文档 §121 Step 8/9/10；合并于 pruning.py 便于职责收拢）
│   └── report.py               # OptimizationReport + build + render
│
├── topology/                   # 新增 —— 可回滚的补丁/版本层（声明 Topology 仍不可变）
│   ├── __init__.py
│   ├── patch.py                # TopologyPatch / CandidateTopology（虚拟禁用 Edge/Node）
│   ├── version.py              # TopologyVersion
│   └── snapshot.py             # commit / rollback / ActiveTopology 版本存储
│
├── regression/                 # 已有；只消费不修改
│   ├── fast/...
│   └── slow/...
│
└── cli.py                      # 已有；追加 `optimize` 子命令（Step 13）
```

根包 `capability_runtime/__init__.py` 统一导出以上全部公共符号。

---

## 3. 对象 → 模块 映射表

| phase4.md 对象 | 落点类名 | 所在模块 |
|---|---|---|
| `NodeEvidence` / `EdgeEvidence` | 同名 | `optimization/evidence.py` |
| 逐边成功路线支持数 | `EdgeEvidence.successful_route_count / scenario_count` | `optimization/evidence.py` |
| `CandidateDetector` | 同名 | `optimization/candidate.py` |
| `CandidateStatus` / `CandidateReason` | 同名 | `optimization/candidate.py` |
| protected capability / sentinel / bridge | `ProtectionRegistry` | `optimization/candidate.py` |
| `TopologyPatch` / `CandidateTopology` | 同名 | `topology/patch.py` |
| `Counterfactual Fast Regression` | `CounterfactualRunner` | `optimization/counterfactual.py` |
| `ProbeRunner` | 同名 | `optimization/probe.py` |
| `Batch Candidate Builder` | `BatchCandidateBuilder` | `optimization/batch.py` |
| `Fast Validation Gate` | `FastValidationGate` | `optimization/pruning.py` |
| `Slow Validation Gate` | `SlowValidationGate` | `optimization/pruning.py` |
| `Route Diversity Guard` | `RouteDiversityGuard` | `optimization/pruning.py` |
| Optimization/Validation/Sentinel Set | `DatasetSplit` + 三个 dataclass | `optimization/analyzer.py` |
| `TopologyVersion` / `Commit` / `Rollback` | 同名函数 + `TopologyVersion` | `topology/version.py`、`topology/snapshot.py` |
| `OptimizationReport` | 同名 | `optimization/report.py` |

---

## 4. 错误模型（追加到 `core/errors.py`）

```text
OptimizationError                     # 根（继承 TopologyFrameworkError）
├── EvidenceError                     # trace 聚合失败 / 计数不一致
├── CandidateDetectionError
├── ProtectionViolationError          # 候选触碰 protected capability / sentinel
├── CounterfactualError
├── ProbeError
├── ValidationGateError
├── PruningError                      # 批次超 max_pruning_batch_size 等
└── TopologyVersioningError           # commit / rollback 冲突
```

全部继承已有的 `TopologyFrameworkError`，并在 `core/__init__.py`、`capability_runtime/__init__.py` 导出。

---

## 5. CLI 面（Step 13 落地）

```bash
tool-topology optimize analyze \
    --topology topology.json \
    --scenario scenarios/*.json \
    --evidence artifacts/run_0001        # 指向 Phase 3 slow Regression 的落盘（obs stats）

tool-topology optimize validate \
    --candidate topology.json.patch      # 反事实 + 探针 + 双门 + 批次

tool-topology optimize commit \
    --version 1                           # 写 ActiveTopology 版本（可回滚）
```

CLI 只打印证据与候选状态（IDENTIFIED / INSUFFICIENT_EVIDENCE / PROBE_REQUIRED /
PROTECTED / ACCEPTED / REJECTED）与剪枝建议，**最终 commit 显式需要人工/脚本确认**
（`optimize commit` 单独一步，不与 analyze 合并）。

---

## 6. 13 个 Step 的实现顺序与验收点

> 严格按 phase4.md §121 的 Step 顺序推进，每个 Step 交付后跑全量 `pytest` + `compileall`，
> 按 AGENTS 分批提交。不提前实现下一 Step。

| Step | 模块 | 验收点 |
|---|---|---|
| 1 | `optimization/evidence.py` | 从 Phase 3 `ObservationReport` 聚合 Node/Edge Evidence；**补逐边 successful_route_count / scenario_count**（trace 端重聚合）；`opportunity=0`、`high opp/zero observed`、`high opp/high observed`、`low opp` 全覆盖（覆盖 phase4 §122） |
| 2 | `optimization/candidate.py` | `CandidateDetector` 只输出候选不改任何东西；高 opp+低 usage→IDENTIFIED、低 opp→INSUFFICIENT_EVIDENCE（覆盖 §123） |
| 3 | `optimization/candidate.py`（ProtectionRegistry） | protected capability / sentinel scenario / bridge / 唯一提供者 → PROTECTED（覆盖 §123） |
| 4 | `topology/patch.py` | `TopologyPatch`（disabled_edges/disabled_nodes）+ `CandidateTopology` 虚拟禁用；声明 Topology 不可变 |
| 5 | `optimization/counterfactual.py` | 对 `CandidateTopology` 跑 Fast Regression；删 A→B 仍存在 A→C→B → unchanged，继续验证（§124）；COVERED→UNCOVERED → REJECTED（§125） |
| 6 | `optimization/probe.py` | `ProbeRunner` 复用 basefast 定向 seed / free 执行入口（对齐 §50，不新造模式） |
| 7 | `optimization/batch.py` | `BatchCandidateBuilder`：Edge A/B 单删安全、A+B 覆盖下降 → 回滚该批次（§127）；`max_pruning_batch_size` |
| 8 | `optimization/pruning.py`（FastValidationGate） | global / category / sentinel 三域 Fast 门；sentinel 单独降 → REJECTED（§126） |
| 9 | `optimization/pruning.py`（SlowValidationGate） | 成功率 / 质量 / 错误率 / 多样性守卫 |
| 10 | `optimization/pruning.py`（RouteDiversityGuard） | 保成功备用路线；禁止把搜索空间压成单一路线 |
| 11 | `optimization/analyzer.py`（DatasetSplit） | `hash(scenario_id)` 稳定切 Optimization / Validation / Sentinel；**Validation 不参与候选生成** |
| 12 | `topology/version.py` + `snapshot.py` | `TopologyVersion` 不可变；commit / rollback |
| 13 | `optimization/report.py` + `cli.py` | `OptimizationReport`（候选状态 + 剪枝建议 + commit/rollback）+ `optimize` 三子命令 |

测试约束（phase4 §122-127 及既有习惯）：单元测试全部离线；
`FastValidationGate` / `SlowValidationGate` 用 `FakeRouter` / JSON 可执行占位 Tool，**不依赖真实 LLM / 网络**。

---

## 7. 已解决的命名冲突（文档 → 实现）

| 冲突 | 决定 |
|---|---|
| §119/§123 包名 `tool_topology/` | 改为 `capability_runtime/`，新增平级 `optimization/` |
| §50/§147/Step 6 "Phase 3 guided / replay" | Phase 3 实际只有 `free` / `basefast`；探针复用 basefast 定向 seed |
| Step 8/9/10 的 Gate 命名 | 文档列在 §121 但目录未列 pruning.py；收拢进 `optimization/pruning.py`，类名保持 Gate 原名 |
| 逐边 successful_route_count | `EdgeObservationStats` 无此字段；`EvidenceAggregator` 从 trace 端重聚合补齐 |
| `TopologyPatch` 归属 | 放 `topology/patch.py`（文档目录即如此）；`CandidateTopology` 包装现有 `Topology` + patch |
| Dataset split 时序 | 纯函数 `DatasetSplit` 随 Step 11 交付；Step 8/9 门内如需要，先用 `analyzer.py` 里同一实现拉前（命名不变） |

---

## 8. 阶段边界检查表（实现时对照 phase4.md §128 及方向纪律）

- [ ] 不物理删除 Edge / Node —— 一律 `TopologyPatch` 虚拟禁用，可回滚
- [ ] 不改 DeclaredTopology 的 provider / worker 声明
- [ ] 严格"证据 → 候选 → 反事实验证 → 接受"，禁止 `Unused → Delete`
- [ ] 证据不足 → INSUFFICIENT_EVIDENCE / PROBE_REQUIRED，绝不硬删
- [ ] Validation Set 不参与候选生成（防 overfitting）
- [ ] 探针只复用 basefast 定向 seed / free，不新增执行模式
- [ ] 只输出"Unused Edges 等事实 + 剪枝建议"，剪枝/排名由人工或回归证据最终决定，观测统计不替用户拍板
- [ ] 不做 Route 最终排名 / 性能等级 / Cost 等级（归 Phase 5/6）
- [ ] 不以 Cost 作为主要剪枝依据
- [ ] 不触碰既有的有效测试；新测试全部离线（FakeRouter / JSON 占位工具）

---

## 9. 实现进度

| Step | 状态 | 落点 |
|---|---|---|
| 1 | [x] | `optimization/evidence.py`：`EvidenceAggregator / NodeEvidence / EdgeEvidence / EvidenceReport`；逐边 `successful_route_count / scenario_count` 从 trace 端重聚合；新测试 `tests/unit/test_opt_evidence.py`（§122 全覆盖，7 项） |
| 2 | [x] | `optimization/candidate.py`：`Candidate / CandidateStatus / CandidateReason / PruningConfig / CandidateDetector`（规则式，只输出不改动）；新测试 `tests/unit/test_opt_candidate.py`（§123 三类 + 原因区分，10 项） |
| 3 | [x] | `optimization/candidate.py` 增 `ProtectionRegistry`：唯一 Provider（§33）/ Bridge（§110）/ Sentinel 关键 provider 的 incident edge 锁 + extra 覆盖；Detector 识别受保护节点；新测试 `tests/unit/test_opt_protection.py`（7 项） |
| 4 | [x] | `topology/patch.py`：`TopologyPatch`（disabled_edges/disabled_nodes）+ `CandidateTopology`（base_version + patch）+ `apply_patch`/`build_candidate`；声明 Topology 不可变；新测试 `tests/unit/test_opt_patch.py`（10 项） |
| 5 | [x] | `optimization/counterfactual.py`：`CounterfactualRunner` 复用 `FastRegressionRunner` 对 patched 视图重跑 Fast Regression；`ScenarioCounterfactual` 逐 Scenario 比对 before/after；删冗余边仍保覆盖→PASS（§124），COVERED→UNCOVERED→COVERAGE_DROP 拒绝（§125）；新测试 `tests/unit/test_opt_counterfactual.py`（6 项）+ `CounterfactualError` |
| 6 | [x] | `optimization/probe.py`：`ProbeRunner` 复用 basefast 定向 seed；`build_directed_seed` 把候选边强制纳入完整跨层 seed、保持前/后层可达（对齐 §50/§147，不新造 guided/replay）；`ProbeResult.verdict`（OBSERVED / NOT_OBSERVED / NOT_REACHABLE）；新测试 `tests/unit/test_opt_probe.py`（6 项）+ `ProbeError` |
| 7 | [x] | `optimization/batch.py`：`BatchCandidateBuilder` 把 individually-safe 候选分组为不超过 `max_pruning_batch_size` 的批次（§62）；`CandidateBatch` 自含 `TopologyPatch`，便于回滚（§60/§61）；`bisect` 支持失败时二分定位有害子集（§94）；`validate_edges` 校验边均存在；新测试 `tests/unit/test_opt_batch.py`（11 项）+ `PruningError` |
| 8 | [x] | `optimization/pruning.py`：`FastValidationGate` 三域 Fast 门：global 整体覆盖、category 分类覆盖、sentinel 单场景回归（§126）；`GateVerdict` / `GateFailure` / `FastGateResult`；`summarize_by_category` 辅助函数；`ValidationGateError`；新测试 `tests/unit/test_opt_fast_gate.py`（7 项） |
| 9 | [x] | `optimization/pruning.py` 增 `SlowValidationGate`：成功率（max_success_rate_drop）/ 质量（max_quality_drop，可选）/ 错误率（routing+execution+fixture 增量）三项慢指标校验；`SlowGateResult`；新测试 `tests/unit/test_opt_slow_gate.py`（9 项） |
| 10 | [x] | `optimization/pruning.py` 增 `RouteDiversityGuard`：保留成功备用路线、禁止把搜索空间压成单一路线（§129）；`DiversityGuardResult` 含 before/after/min/lost_families；新测试 `tests/unit/test_opt_diversity_guard.py`（6 项） |
| 11 | [x] | `optimization/analyzer.py`：`DatasetSplit` 纯函数切分（hash(scenario_id) 稳定分 Optimization / Validation / Sentinel）；`split_suite` + `split_ids`；**Validation/Sentinel 不参与候选生成**（§128 overfitting guard）；新测试 `tests/unit/test_opt_dataset_split.py`（11 项） |
| 12 | [x] | `topology/version.py`：`TopologyVersion` 不可变版本快照（declared + active + patch）；`initial_version` / `commit_patch` / `rollback` / `compose_patches`；新增 `TopologyVersioningError`；新测试 `tests/unit/test_topology_version.py`（10 项） |