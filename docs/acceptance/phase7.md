# Phase 7 — Composite Nodes（复合节点：最终能力入图）

## 0. 阶段定位

Phase 7 建立框架的第一个结构性扩展：

> **"最终能力"作为复合节点（宏）进入拓扑：对外是一个普通工具节点，
> 对内是一个更小的子拓扑，以自相似的方式复用整套框架机制。**

这是 AGENTS §7 方向二的采纳形态。原始形态（图层递归 / 真环）已废弃
（AGENTS §2.1）——真环会摧毁 route_id / opportunity / Counterfactual /
在线有界执行的语义。复合节点以**自相似 + 有界展开**实现递归需求，
**外层拓扑保持无环**：

```text
外层（无环，规则不变）：
  read:[order_db, rag] → analyze:[policy_check] → act:[refund_handler*]
                                                        │
refund_handler 是一个复合节点：                          ▼
  对外：layer / providers / workers / capabilities /
        consumes / produces / cost_per_call / description —— 与 ToolNode 完全同构
  对内：子拓扑 + 内层路线 + stop_when + max_iterations（有界循环）
        内层证据经 flattener 用同一套 Phase 3/4/5 机器离线处理
```

前置条件（全部已就绪）：Phase 1–6、资源句柄计量里程碑
（计量汇总依赖批次 A 的 collector 机制）。

---

# 1. Phase 7 核心问题

```text
一个端到端能力（如"处理退款全流程"）能否作为一个节点参与外层拓扑？

复合节点内部的多轮执行（refine until done）如何有界、可观测、可计量？

内层的边与路线如何获得与外层同构的证据（usage / success / cost），
使剪枝与排名可以递归适用，而不会污染外层语义？

外层在线路由能否像对待普通工具一样路由到复合节点（含降级）？
```

---

# 2. 非目标

```text
图层面允许 cycle（AGENTS §2.1 已废弃，永不复活）
内层在线 LLM 路由 / 内层自由探索（内层执行 = 路线跟随，Phase 6 语义）
无界循环（max_iterations 强制声明，缺省无默认魔法数）
复合节点内部的重试语义（整层失败 = 复合节点失败；重试归外层 Phase 6 降级）
外层运行期间自动剪内层（内层剪枝只发生在离线闭环）
内层覆盖率自动并入外层覆盖（内层是独立拓扑、独立回归资产）
深度 > 2 的嵌套与自引用（MVP 边界，见 §5.6）
```

---

# 3. 核心原则

1. **接口完全同构**：复合节点产出的就是一个 `ToolNode`——注册、建边、
   Fast 覆盖、Slow 执行、在线路由对它零特殊分支。这是本 Phase 最重要的
   工程裁定：**复合节点是工具工厂，不是新节点类型**；
2. **外层无环不变**：邻接建边、RoutePlan 校验、route_id 指纹、
   opportunity 统计、在线有界执行的全部语义原样保留；
3. **递归靠自相似**：内层是一个完整的（更小的）Declared+Active 拓扑，
   框架自身的 Declare → Fast → Slow → Prune → Rank 循环在内层原样适用；
4. **一切循环有界**：循环 = 同一路线重复执行 + 状态黑板跨轮持久 +
   声明式停止条件 + 硬预算。"循环两次"在内层证据里是两份可区分的
   执行记录（更长、更贵、统计上诚实）；
5. **计量向上汇总**：内层每次访问 / token / 实测成本回放到外层
   collector——外层 ToolExecution 的账单包含内层全部开销。

---

# 4. 声明模型

```python
CompositeSpec(
    name="refund_handler",
    layer="act",                          # 外层层级，与普通工具一致
    topology=inner_topology,              # 子拓扑（可由 TopologyLoader 加载）
    route=[{"order_db", "rag"},
           {"policy_check"},
           {"refund_api"}],               # 内层路线（RoutePlan 语义校验）
    stop_when=("refund_result",),         # 槽位齐备（ALL）即停止
    max_iterations=3,                     # 硬预算，必须 >= 1
    consumes=[Order, PolicyDecision],     # 外层入参契约（类型）
    produces=[RefundResult],              # 外层出参契约（类型）
    capabilities={"refund.handle"},       # 端到端能力（外层覆盖判定用）
    cost_per_call=0.02,
    description="Handle a refund end-to-end, refine until issued",
)

refund_handler = build_composite_node(spec)    # -> ToolNode，注册即用
```

校验（`CompositeSpecError`）：

- `route` 必须是内层拓扑上的合法 RoutePlan（逐层相邻、每组同层）；
- `stop_when` 槽位名非空；`max_iterations` 为正整数；
- `consumes/produces` 数量与语义检查与 @tool 同源；
- 外层 `layer` 合法、名称唯一（复用 ToolRegistry 校验）；
- 嵌套深度 ≤ 2 且**禁止自引用**（沿内层拓扑逐级检查复合节点名路径，
  出现环即拒绝——类型层面的环在构建期拦死，图层面永不成环）。

---

# 5. 执行语义

## 5.1 一次复合调用

```text
外层 ToolExecutor.execute(refund_handler)
  ├─ 正常解析入参（按名/类型，同普通工具）
  ├─ handler = CompositeRuntime(spec)
  │    ① 构造内层 ExecutionState，注入 consumes 对应的 artifact
  │    ② for iteration in range(max_iterations):
  │         按 route 逐层执行（复用 LayerExecutor；黑板跨轮持久）
  │         任一整层失败 → 复合节点立即失败（§2：不内嵌重试）
  │         stop_when 槽位齐备 → 停止
  │    ③ 预算耗尽仍未满足 → 复合节点失败（stop condition unmet）
  │    ④ 从内层黑板按 produces 类型抽取 artifact 作为返回值
  │    ⑤ 内层各 ToolExecution 的 access/tokens/measured_cost
  │       回放到外层 collector（计量汇总，§6）
  └─ 外层照常传播 outputs、记账、记录 trace
```

## 5.2 循环即 refine

黑板跨轮持久使后续轮次拥有更多 artifact——这是"循环"的全部语义：
没有隐藏控制流，没有递归栈，只有**同一有界路线在增长的状态上重放**。

## 5.3 失败语义

- 内层单工具失败：兄弟节点结果保留，层继续（phase3 §41 原样）；
- 内层整层失败：复合节点失败（`ToolExecutionStatus.ERROR`），
  已产生的访问照常计费；
- stop_when 未满足且预算耗尽：复合节点失败，原因
  `stop condition unmet after N iterations`；
- 外层处置：Slow Regression 记录失败 trial；Online 走既有降级链。

## 5.4 与在线路由的兼容

复合节点是普通 ToolNode → Catalog 的 canonical（如
`act:[refund_handler]`）照常重建，route-following 执行照常调用 handler，
外层降级链照常工作。**零新增在线代码**（Step 6 仅验证 + 测试）。

## 5.5 与 Fast Regression 的关系

外层覆盖判定消费复合节点的 `capabilities`（端到端能力）。内层拓扑是
独立资产：用内层场景文件对内层跑 `regression fast`，机器原样复用。

## 5.6 MVP 边界

```text
嵌套深度 <= 2（复合内的复合），自引用拒绝
内层路线唯一（MVP 不做内层多路线在线选择；内层"排名→选路"
  属后续扩展，依赖本 Phase 的 flattener 证据）
stop_when 只支持槽位存在性（ALL 语义）
```

---

# 6. 计量与观测

- **计量汇总**：CompositeRuntime 在外层 collector 上下文内运行
  （contextvar 继承），把内层每次 ToolExecution 的
  `access_counts / token_usage / measured_cost` 逐项回放——
  外层账单 = 外层声明价 + 内层全部实测开销的完整视图；
- **三档语义**：内层有句柄流量则外层 `metering_source=MEASURED`
  （最弱声明原则照旧）；
- **嵌套明细**：`ToolExecution` 增可选字段 `composite_detail`
  （每轮的内层 LayerExecution 元组），持久化走既有 dataclass 序列化
  （lossy 原则不变）。

---

# 7. 证据递归：flattener

内层的边与路线如何进入离线闭环？——**把外层 trial 中复合节点的执行
明细摊平成内层伪 TrialResult**：

```python
flatten_composite_results(
    outer_results: Sequence[TrialResult],
    spec_registry: Mapping[str, CompositeSpec],
) -> tuple[TrialResult, ...]
```

- 每个复合执行（每轮）→ 一个内层伪 Trial：
  `route` 从该轮 LayerExecution 抽取（ObservedRoute 指纹原样），
  `latency / cost / access / token` 来自内层明细；
- 伪 Trial 流入 `build_observation_stats(inner_edges)` →
  `EvidenceAggregator / CandidateDetector / build_ranking_report`
  **全部原样复用**——这就是"框架自身递归适用"的落点；
- 严格边界：flattener 只在离线分析中调用；外层运行期不做任何内层剪枝。

---

# 8. 剪枝影响与边界（回应"对剪枝造成极大影响"的担忧）

```text
外层剪枝：复合节点 = 原子。它的边是外层边，保或不保整体判断；
          复合内部的边不是外层边，永不进入外层 Evidence。
内层剪枝：经 flattener 在内层拓扑上独立进行，同一套
          Evidence → Candidate → Gate → Version 机器。
互不污染：内外两套版本线独立；内层剪枝产生新的内层 Active 拓扑，
          复合节点声明随之更新（人工确认），外层重新回归验证。
```

不存在"剪一条边毁掉整个环"的不可判定局面——因为图层面没有环。

---

# 9. 声明式加载（JSON）

`TopologyLoader` 增量支持（严格校验，未知字段拒绝）：

```json
{"name": "refund_handler", "layer": "act",
 "kind": "composite",
 "inner": "examples/composite_refund/inner_topology.json",
 "route": [{"layer": "read", "tools": ["order_db"]},
           {"layer": "analyze", "tools": ["policy_check"]},
           {"layer": "action", "tools": ["refund_api"]}],
 "stop_when": ["refund_result"],
 "max_iterations": 3,
 "capabilities": ["refund.handle"],
 "cost_per_call": 0.02,
 "description": "..."}
```

内层 JSON 必须带 implementation 绑定（批次 B 语义：可执行）。

---

# 10. 错误模型（追加到 core/errors.py）

```text
CompositeError                  # 根（继承 TopologyFrameworkError）
├── CompositeSpecError          # 声明非法（路线/预算/深度/自引用/槽位）
└── CompositeExecutionError     # stop_when 未满足、内层结构损坏等运行期失败
```

---

# 11. 推荐目录结构

```text
src/capability_runtime/
├── composite/                  # 新增 —— 与 ranking/ online/ 平级
│   ├── __init__.py
│   ├── spec.py                 # CompositeSpec / build_composite_node（工厂）
│   ├── runtime.py              # CompositeRuntime（有界循环 + 计量汇总）
│   └── evidence.py             # flatten_composite_results
│
├── execution/executor.py       # composite_detail 字段（增量）
└── topology/loader.py          # kind=composite 加载（Step 5）
```

根包统一导出公共符号。

---

# 12. 模块职责

## CompositeSpec / build_composite_node

只负责：声明校验与 ToolNode 工厂（含 RoutePlan 校验、深度/自引用检查）。

## CompositeRuntime

只负责：有界内层执行、黑板种子与抽取、计量回放、明细记录。

## flatten_composite_results

只负责：外层 trial → 内层伪 Trial 的纯转换。

任何模块不得修改内外层拓扑、不得调用 LLM。

---

# 13. 开发顺序

| Step | 模块 | 验收点 |
| --- | --- | --- |
| 1 | `core/errors.py` + `composite/spec.py` | 声明校验全分支（路线/预算/槽位/深度/自引用）；工厂产物可通过 ToolRegistry 注册并参与建边 |
| 2 | `composite/runtime.py` | 有界循环 + stop_when + 黑板持久；整层失败立即失败；预算耗尽失败；入参注入/出参抽取正确 |
| 3 | 计量汇总 + `composite_detail` | 内层 access/token/measured 回放外层；三档语义正确；明细持久化往返 |
| 4 | `composite/evidence.py` | flattener 产出伪 Trial；`build_observation_stats` + `EvidenceAggregator` 在内层拓扑上原样跑通 |
| 5 | `topology/loader.py` | `kind=composite` JSON 加载（内层可执行绑定）；严格校验 |
| 6 | 在线兼容 | Catalog canonical 重建 + route-following + 降级链对复合节点照常工作（仅测试） |
| 7 | Demo `examples/composite_refund` | 二轮 refine 循环（首轮 pending、次轮 refund_result）+ 在线服务 + flattener 闭环演示 |
| 8 | 集成验收 | 外层 fast/slow + 内层 fast + flattener 剪枝证据 + 在线降级 全链 |

---

# 14. Unit Tests

至少覆盖：

```text
Spec
  合法声明产出可注册 ToolNode；route 非法 / max_iterations<=0 /
  stop_when 为空 → CompositeSpecError
  深度 3 拒绝；自引用拒绝；名称重复拒绝（复用注册校验）

Runtime
  首轮满足 stop_when（单轮完成）
  首轮不满足 → 次轮满足（黑板持久生效）
  预算耗尽 → CompositeExecutionError（stop condition unmet）
  内层整层失败 → 复合失败；已产生访问照常计费
  consumes 注入 / produces 抽取 类型正确

计量
  内层句柄访问汇总进外层 access_counts；token/measured_cost 回放
  无句柄内层 → 外层 DECLARED 不变

Evidence
  flattener：每轮一个伪 Trial；route 指纹稳定；
  伪 Trial 进入 build_observation_stats / EvidenceAggregator 无异常

Loader
  kind=composite 全字段解析；未知字段/坏内层路径拒绝
  内层未绑定 implementation → 拒绝

Online
  canonical 含复合名照常重建；执行与降级链照常
```

---

# 15. Integration Test

`examples/composite_refund`：

```text
外层：read:[order_db] → analyze:[policy_check] → act:[refund_handler]
内层：verify(首轮产出 pending) → refund(次轮产出 refund_result)
     stop_when=("refund_result",)，max_iterations=3
场景：复合场景 × N trials（含失败注入变体）
验证：二轮循环稳定触发；外层 trace 含 composite_detail；
     flattener 产出内层证据并喂 EvidenceAggregator；
     在线请求路由到复合节点并成功；注入失败走外层降级
```

---

# 16. Definition of Done

## 同构

* [ ] 复合节点经工厂产出标准 ToolNode，注册/建边/覆盖/执行/在线零特殊分支
* [ ] 外层拓扑语义（无环/指纹/opportunity）不变

## 有界

* [ ] max_iterations 强制；stop_when 声明式；黑板跨轮持久
* [ ] 无内嵌重试；整层失败立即失败；预算耗尽可解释失败

## 观测与计量

* [ ] 内层计量完整汇总外层；composite_detail 持久化
* [ ] 三档 MeteringSource 语义正确

## 递归证据

* [ ] flattener → 内层 ObservationStats → EvidenceAggregator 跑通
* [ ] 内层剪枝与外层剪枝互不污染（独立版本线）

## 声明与在线

* [ ] JSON kind=composable 加载（严格校验）
* [ ] 在线路由与降级对复合节点照常

## 边界

* [ ] 图层面无环（深度/自引用构建期拦截）
* [ ] 无内层在线选择；无运行期内层剪枝

---

# 17. 最终验收场景

```text
外层拓扑（含 refund_handler 复合节点）跑 Fast：refund.handle 覆盖判定正确

外层 Slow：复合场景多 trial —— 首轮 pending / 次轮成功的二轮循环稳定；
           失败注入变体下复合失败且账单含内层开销

flattener：内层证据喂 EvidenceAggregator，产出内层候选/保护结论

内层 Fast：内层场景独立回归通过（机器零改动复用）

在线：select 干跑列出复合路线；服务请求路由到复合节点；
      注入内层失败后外层降级链接管
```

---

# 18. 核心验收问题

最终只问六个问题。

### 1.

复合节点是否在**所有**外层机制里与普通工具不可区分（注册、建边、
覆盖、执行、排名、在线、降级）？

### 2.

内层循环是否有界、声明式、无隐藏控制流（同一路线 + 持久黑板 +
stop_when + 预算）？

### 3.

外层账单是否包含内层全部计量（访问 / token / 实测成本）？

### 4.

内层证据能否经 flattenter 用**同一套** Phase 3/4/5 机器处理，
而外层语义零污染？

### 5.

图层面是否始终无环（深度与自引用在构建期拦死）？

### 6.

内层剪枝是否独立版本化，绝不与外层剪枝互相掺杂？

六个答案全部为 Yes，则 Phase 7 核心假设验证成功。

---

# 19. Phase 7 完成后的系统形态

```text
工具          原子工具 + 复合节点（最终能力）同图混排
拓扑          外层无环不变；内层是自相似的子世界
证据          外层证据 + 摊平的内层证据，同一套机器两处适用
在线          路线跟随照常；复合内部即 Phase 6 语义的递归实例
```

"递归"以自相似落地：框架的每一个概念在内层都有一个一模一样的实例。
后续可选扩展（需另立验收文档）：内层多路线排名选路、深度放开、
stop_when 谓词语言扩展。

---

# 20. 实现进度

| Step | 状态 | 落点 |
| --- | --- | --- |
| 1 | [ ] | |
| 2 | [ ] | |
| 3 | [ ] | |
| 4 | [ ] | |
| 5 | [ ] | |
| 6 | [ ] | |
| 7 | [ ] | |
| 8 | [ ] | |
