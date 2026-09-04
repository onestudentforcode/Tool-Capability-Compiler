# Phase 3 实施约定 —— 命名与目录结构

> 定位：在动手写任何代码前，把 Phase 3 的对象命名、包路径、模块职责、错误模型、
> CLI 面、以及 14 个 Step 的实现顺序一次性敲定。实现时一律以本文件为准，
> 若与 `docs/acceptance/phase3.md` 冲突，以本文件 + phase3.md 的语义为准（命名以本文件为准）。

---

## 0. 三条总决定（TL;DR）

1. **包名保持 `capability_runtime`**，不新建 `tool_topology`。Phase 0–2 全部，以及 Phase
   3 的全部新模块，都活在同一个包下。
2. **Phase 3 文档里的 `runtime/`（执行引擎）改名为 `execution/`**。因为 `capability_runtime/runtime/`
   已存在，且是 Phase 0 废弃主线的残留目录（仅 `.pyc`、无源码被 git 跟踪）。沿用 `runtime/`
   会诱发 import 到陈旧代码，也不利于区分"旧的 Artifact runtime"与"新的执行引擎"。
3. **`Topology` 保持不变，不新增 `ToolTopology` 类型。** Phase 3 就是**对着同一个声明式
   `Topology` 执行**（它就是 §2 说的"搜索空间"）。`TopologyFilter` 从它读取当前可达节点，
   不需要第二个顶层类型。

---

## 0.1 设计裁定（用户纠正）：Fast Regression 的 CandidateRoute 是 Slow Regression 的起点

> 覆盖 phase3.md §5/§60/§58 的定位。phase3.md 把 CandidateRoute 当成"可选的 reference / search
> hint"，并把默认探索模式定为 `free`。**本裁定推翻该默认**。

### 裁定内容

Slow Regression **不空手探索**，而是以 Fast Regression 为每个 Scenario 发现出的
`CandidateRoute` 作为 **每个 Trial 的起点（seed）**，再从它逐层向外扩展。

### 机制

```text
Fast Regression
   ↓ 每个 Scenario 产出 CandidateRoute
Slow Regression 的一个 Trial：
   ① 先按该 CandidateRoute 的逐层选择执行一遍            ── baseline branch
   ② 在每一层，额外叠加该层"可达但与候选选择不同的兄弟 Tool"── 生成扩展变体 variant
   ③ baseline 与每个 variant 分别计时 / 计 token/cost，
      并跑同一个 Evaluator 得到产物质量
   ④ 计算 delta = variant − baseline（时间、成本、质量）
   ⑤ 汇总出"候选路线是否需要修正"的观测证据
```

- 扩展变体的范围：`候选层选择 ∪ 额外可达兄弟`，**数量上限 = max_tools_per_layer**。
- 一个 layer 可以产生多个变体（例如分别追加 B、追加 C、追加 {B,C}），也可维持原候选不扩展。
- delta 观测维度：`latency_delta`、`cost_delta`、`quality_delta`、以及"产物捕获到的
  字段数/信息完整度"差异。

### 与 Phase 4 的边界

本裁定只让 Slow Regression **产出"哪些扩展值得修正候选路线"的证据**（比如"恒加 B 但质量不
升反而更慢"就是修正信号），**真正的路线修正 / 剪枝 / 排名仍属于 Phase 4**。

### 触发方式：CLI 旗标 `--basefast`

是否以 Fast Regression 结果作为起点，**由 CLI 旗标控制**，不是默认常开。

```bash
tool-topology regression slow \
    --topology topology.json \
    --scenario scenarios/customer_service.json \
    --trials 10 \
    --environment sandbox \
    [--basefast fast_out.json]   # 传入路径：以该 fast 结果为激活/初始值
```

- **传 `--basefast <path>`**：读取指定 Fast Regression 输出，每个 Trial 以对应 Scenario 的
  `CandidateRoute` 为起点（seed）逐层扩展。不传路径值仅传布尔时不合法（必须给路径）。
- **不传 `--basefast`**：走标准 `free` 探索（全层可达即可用），与纯探索模式一致。

```text
--basefast 传入  seed-anchored   （以 CandidateRoute 为起点逐层扩展）
--basefast 缺省  free            （无 seed：全层可达即可用）
可选项            replay          （严格只重复某条 ObservedRoute，供复现）
```

回退规则：**已传 `--basefast` 但某 Scenario 在 Fast Regression 中没有产出 CandidateRoute**
（例如 COVERAGE 为空或 resolve 失败），该 Scenario 回退到 `free` 探索，并在报告里标记
`seed_missing`。

---

## 0.2 新增概念：ExpansionPlan / RouteDelta（承接 0.1）

`router` 层新增"扩展规划"职责（随 Step 10 落地）：

| 对象 | 落点 |
|---|---|
| `ExpansionPlan`（某 layer 是否扩展、追加哪些兄弟） | `router/models.py` |
| `ExpansionDelta`（baseline 与 variant 的时间/成本/质量差） | `regression/slow/stats.py` |
| `SeedMissing`（scenario 无 CandidateRoute 的标记） | `regression/slow/report.py` |

---

## 1. 命名规则

- 目录/模块：`snake_case`；类：`PascalCase`；枚举：`PascalCase(suffix Enum 省略)`。
- 每个新模块自带 `__init__.py`，并在外层包 `__init__.py` 汇总导出。
- 领域数据一律 `@dataclass(frozen=True, slots=True)`（与 0–2 一致）。
- 公共失败一律用 `core/errors.py` 里定义的项目自定义异常，禁止泄露 `KeyError/ValueError`。
- 集合对外输出前按名称升序规范化（延续既有规则）。
- phase3.md 推荐对象名**尽量保留**，只改位置（映射见 §3），避免文档与代码概念错位。

---

## 2. 最终目录结构

```text
src/capability_runtime/
├── core/                       # 已有；本阶段只做增量
│   ├── errors.py               # 追加 Phase 3 异常（见 §4）
│   └── metrics.py              # 新增：TokenUsage（共用度量）
│
├── execution/                  # 新增 —— 执行引擎（替代文档的 runtime/）
│   ├── __init__.py
│   ├── state.py                # ExecutionState / ArtifactValue(多来源) / ExecutionInputs
│   ├── context.py              # ExecutionContext
│   └── executor.py             # Runtime：逐 Layer 顺序推进、同层并发、State 传播
│
├── router/                     # 新增 —— 分层路由（决策层）
│   ├── __init__.py
│   ├── models.py               # RoutingContext / RoutingDecision / RoutingAction / RouterConfig
│   ├── protocol.py             # LayerRouter Protocol
│   ├── fake_router.py          # FakeRouter（Step 3，先不接 LLM）
│   ├── filtering.py            # TopologyFilter（当前 Layer 可达工具，OR Reachability）
│   └── prompts.py              # Step 12 才引入
│   └── llm_router.py           # Step 12 才引入
│
├── regression/                 # 已有；本阶段在其下追加 slow 子包
│   └── slow/                   # 新增 —— Slow Regression（与 fast 平级、隔离命名）
│       ├── __init__.py
│       ├── trial.py            # Trial / TrialResult / TrialExecutionStatus
│       ├── trace.py            # ExecutionTrace / LayerExecution / ToolExecution
│       │                       #              / SelectionEvent / ToolExecutionStatus
│       ├── route.py            # ObservedRoute + route fingerprint
│       ├── stats.py            # Node/Edge/RouteObservationStats + SelectionEvent 聚合
│       ├── report.py           # SlowRegressionReport
│       ├── runner.py           # SlowRegressionRunner
│       └── spec.py             # 解析 scenario 的 slow_regression 配置（suite 默认值合并）
│
├── evaluation/                 # 新增 —— 业务效果评判
│   ├── __init__.py
│   ├── base.py                 # Evaluator Protocol / EvaluationResult / CriterionResult
│   ├── structured.py           # StructuredEvaluator
│   ├── llm_judge.py            # LLMJudgeEvaluator
│   └── composite.py            # CompositeEvaluator
│
├── fixtures/                   # 新增 —— Trial 隔离
│   ├── __init__.py
│   ├── base.py                 # FixtureManager Protocol
│   ├── manager.py              # DefaultFixtureManager（isolation mode）
│   └── registry.py             # 具名 fixture 注册表
│
└── cli.py                      # 已有；追加 `regression slow` 子命令（Step 14）
```

根包 `capability_runtime/__init__.py` 统一导出以上全部公共符号。

---

## 3. 对象 → 模块 映射表

| phase3.md 对象 | 落点类名 | 所在模块 |
|---|---|---|
| `Trial` | `Trial` | `regression/slow/trial.py` |
| `TrialResult` | `TrialResult` | `regression/slow/trial.py` |
| `TrialExecutionStatus` | `TrialExecutionStatus` | `regression/slow/trial.py` |
| `SlowRegressionRunner` | `SlowRegressionRunner` | `regression/slow/runner.py` |
| `ExecutionTrace` | `ExecutionTrace` | `regression/slow/trace.py` |
| `LayerExecution` | `LayerExecution` | `regression/slow/trace.py` |
| `ToolExecution` | `ToolExecution` | `regression/slow/trace.py` |
| `ToolExecutionStatus` | `ToolExecutionStatus` | `regression/slow/trace.py` |
| `ObservedRoute` | `ObservedRoute` | `regression/slow/route.py` |
| `SelectionEvent` | `SelectionEvent` | `regression/slow/trace.py` |
| `SlowRegressionReport` | `SlowRegressionReport` | `regression/slow/report.py` |
| Node/Edge/Route Observation Stats | `*ObservationStats` | `regression/slow/stats.py` |
| `FixtureManager` | `FixtureManager`(Protocol) | `fixtures/base.py` |
| `ExecutionContext` | `ExecutionContext` | `execution/context.py` |
| `ExecutionState` | `ExecutionState` | `execution/state.py` |
| `ExecutionInputs` | `ExecutionInputs` | `execution/state.py`（与 `fixtures/base.py` 复用） |
| `ArtifactValue`（多来源） | `ArtifactValue` | `execution/state.py` |
| `TokenUsage` | `TokenUsage` | `core/metrics.py` |
| `LayerRouter` | `LayerRouter`(Protocol) | `router/protocol.py` |
| `RoutingContext` | `RoutingContext` | `router/models.py` |
| `RoutingDecision` | `RoutingDecision` | `router/models.py` |
| `RoutingAction` | `RoutingAction` | `router/models.py` |
| `RouterConfig` | `RouterConfig` | `router/models.py` |
| `TopologyFilter` | `TopologyFilter` | `router/filtering.py` |
| `Evaluator` | `Evaluator`(Protocol) | `evaluation/base.py` |
| `EvaluationResult` / `CriterionResult` | 同名 | `evaluation/base.py` |
| `StructuredEvaluator` | 同名 | `evaluation/structured.py` |
| `LLMJudgeEvaluator` | 同名 | `evaluation/llm_judge.py` |
| `CompositeEvaluator` | 同名 | `evaluation/composite.py` |

---

## 4. 错误模型（追加到 `core/errors.py`）

```text
SlowRegressionError                # 根
├── FixtureError
│   ├── FixtureSetupError
│   ├── FixtureResetError
│   └── FixtureTeardownError
├── RoutingError
│   ├── InvalidRoutingDecisionError
│   └── InvalidToolSelectionError   # 选中非 available / 超 max_tools_per_layer
├── ExecutionError
│   ├── ToolExecutionError
│   └── LayerExecutionError
├── EvaluationError
└── TraceSerializationError
```

全部继承已有的 `TopologyFrameworkError`（与 0–2 同根），并在 `core/__init__.py`、
`capability_runtime/__init__.py` 导出。

---

## 5. CLI 面（Step 14 落地）

```bash
tool-topology regression slow \
    --topology topology.json \
    --scenario scenarios/customer_service.json \
    --trials 10 \
    --environment sandbox \
    [--max-concurrency 4] \
    [--router-config router.json] \
    [--basefast fast_out.json] \
    [--out-dir artifacts/slow_regression/run_001]
```

`--basefast <path>`：以指定 Fast Regression 输出中的 CandidateRoute 作为每个 Trial 的起点
逐层扩展（详见 §0.1）；必须给出路径值。

输出 manifest.json / report.json / traces.jsonl / node_stats.json / edge_stats.json /
route_stats.json（phase3.md §105）。CLI 只打印观测数据，**不输出剪枝建议**（§108 边界）。

---

## 6. 14 个 Step 的实现顺序与验收点

> 严格顺序推进，每个 Step 交付后跑全量 `pytest` + `compileall`，按 AGENTS 分批提交。
> 不再重蹈"提前实现下一 Step"。

| Step | 模块 | 验收点 |
|---|---|---|
| 1 | `execution/state.py`、`execution/context.py`、`slow/trial.py` | ExecutionState 支持多来源 Artifact 不覆盖；Trial 字段齐全 |
| 2 | `router/filtering.py` | prev layer + provider/worker + state → available tools；OR Reachability |
| 3 | `router/models.py`、`router/protocol.py`、`router/fake_router.py` | FakeRouter 契约：选 available / 拒非法 / FINISH / max_tools |
| 4 | `execution/executor.py` | single-tool 逐层执行，State 顺序传播 |
| 5 | `execution/executor.py` | multi-tool 同层 `asyncio.gather` 并发；无同层依赖 |
| 6 | `slow/trace.py` | 记录 available/selected/tool result/error/latency/layer |
| 7 | `slow/route.py` | ObservedRoute；fingerprint 层内 ASC 规范化、稳定 route_id |
| 8 | `evaluation/structured.py` + `base.py` | 确定性场景 PASS/FAIL；execution vs business success 分离 |
| 9 | `fixtures/` | setup→execute→evaluate→teardown；连续两 Trial 状态隔离 |
| 10 | `slow/runner.py` + `router/models.py` | Scenario × N Trials 编排；**以 CandidateRoute 为 seed**；生成 ExpansionPlan；max_concurrency；`seed_missing` 回退 `free` |
| 11 | `slow/stats.py` | Node/Edge opportunity + observed；Route usage；**baseline vs variant 的 latency/cost/quality delta**；不剪枝 |
| 12 | `router/llm_router.py` + `prompts.py` | 接 Ollama（复用 Step 8 基建）；record config |
| 13 | `evaluation/llm_judge.py`、`composite.py` | LLM Judge 支持 fake 注入 + Composite 组合 |
| 14 | 持久化 + `cli.py` + demo | JSONL/manifest 落盘；`regression slow` 命令；集成 Demo（10-20 Tool / 3 Layer） |

测试约束（phase3.md §112-122）：单元测试不依赖真实 LLM/网络（LLM 一律注入 fake）。

---

## 7. 已解决的命名冲突（文档 → 现实）

| 冲突 | 决定 |
|---|---|
| §123 包名 `tool_topology` | 改为 `capability_runtime` |
| §6/§123 `Runtime` / `runtime/` | 改为 `execution/`；类名可用 `Runtime` 但实现入口建议 `Executor`，以远离废弃 `runtime/` |
| §15 `ToolTopology` | 去掉，统一用现有 `Topology` |
| §6 `SlowRegressionRunner` 等放 `tool_topology/regression/` | 放进 `regression/slow/`（与现有 `regression/report.py`、`regression/route_search.py` 平级但子包隔离，避免 `route.py`/`report.py` 撞名） |
| Scenario 新增 `slow_regression` 可选字段 | Scenario 模型**保持 Phase 2 不变**；由 `spec.py` 单独解析 raw scenario 中的 `slow_regression`，suite 默认值由 spec 合并 |
| `ExecutionInputs` 归属 | 定义在 `execution/state.py`，`fixtures/base.py` 复用，两处不重复实现 |

---

## 8. 阶段边界检查表（实现时对照 phase3.md §128）

- [ ] 不修改 Topology / 不剪枝 / 不删除 Tool
- [ ] 不做 Route 最终排名 / 性能等级 / 成本等级
- [ ] 不自动 Retry / Fallback / 同 Trial 替代
- [ ] 无 Loop / 递归 / 跨层回跳 / Layer Skip
- [ ] 只记录观察事实；`available` 与 `selected` 成对统计
- [ ] 每次 Trial 绑定 Topology version + Router model + Prompt version
- [ ] 以 Fast Regression CandidateRoute 为起点仅在 CLI 传 `--basefast` 时启用；未传则 `free`
- [ ] 每层扩展受 `max_tools_per_layer` 约束，记录 baseline 与各 variant 的时间/成本/质量 delta
- [ ] 只产出"候选路线修正证据"，不出具正式剪枝/排名结论（归 Phase 4）