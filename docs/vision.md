# Architecture Pivot

项目早期尝试过 `Artifact → Backward Planning → Minimal DAG`。该模型回答“为了得到目标类型需要哪些依赖”，但没有回答项目真正关心的问题：“在允许的工具组合空间里，哪些 Route 在真实业务中有效”。

当前架构因此改为：

```text
开发者声明 Layered Search Space
        ↓
Battlefield 探索业务 Route
        ↓
Trace 与多维 Evaluation
        ↓
Declared Topology 派生 Active Topology Versions
```

旧实现中保留的原则包括 Registry、Topology 与执行分离、async Tool、Schema Validation、自定义错误和确定性初始化；Artifact Goal、Producer Resolution、Backward Planner 与 Minimal DAG 不再属于主线。

修改意见见下：
是，而且我认为应该**重写 Phase 0 的核心抽象，并直接废弃当前 Phase 1 的主线设计**，而不是在现有版本上修补。

你现在把项目真正的核心说清楚了：

> **我们不是根据 Tool 的输入输出“编译出一条最小 DAG”，而是先构造一个较宽松的分层 Tool Topology，再通过真实业务场景持续探索、验证、统计和剪枝，让拓扑逐渐收敛。**

这两个方向有本质区别。

---

# 一、之前的方向偏在哪里

之前我们设计的是：

```text
Tool
 ↓
consumes / produces
 ↓
自动推导 Dependency Edge
 ↓
Goal
 ↓
Backward Planning
 ↓
Minimal DAG
 ↓
Execute
```

它解决的是：

> **“给定目标，哪些工具是必要依赖？”**

而你真正想做的是：

```text
Tool Definition
 ↓
Layer / Provider / Worker
 ↓
Initialize Dense Topology
 ↓
Business Scenario Battlefield
 ↓
Agent Explore Tool Combinations
 ↓
Execution Trace
 ↓
Evaluate
 ↓
Statistics
 ↓
Prune / Rank / Tier
 ↓
Optimized Tool Topology
```

它解决的是：

> **“在一个允许组合的 Tool 搜索空间中，哪些工具组合在真实业务中有效？”**

所以项目其实更像：

**Tool Routing + Evaluation + Topology Optimization Framework**

而不是之前定义的：

**Dependency Graph Compiler**。

---

# 二、最重要的核心抽象也要改变

之前核心对象是：

```text
Artifact
Tool
Capability
Goal
DependencyGraph
ExecutionPlan
```

现在我建议改成：

```text
Layer
ToolNode
ToolEdge
Topology

Scenario
Battlefield

RoutePlan
Trial
TraceGraph

Evaluation
RouteStats
TopologyVersion
```

这里最关键的是：

> **Graph 本身不是答案，Graph 是搜索空间。**

这句话应该进入新版 Phase 0。

---

# 三、`layer / provider / worker` 应该成为建图核心

例如：

```python
@tool(
    layer="read",

    providers="all",

    workers=[
        "check_refund",
        "summarize_order",
    ],
)
async def get_order(...):
    ...
```

假设：

```text
Layer 1
────────────────
RAG
DB
WebSearch


Layer 2
────────────────
PolicyCheck
Summarizer
Analyzer


Layer 3
────────────────
Refund
Email
UpdateOrder
```

初始化阶段默认：

```text
Layer N
   ↓
Layer N+1
```

全连接。

例如：

```text
RAG ─────────→ PolicyCheck
    ─────────→ Summarizer
    ─────────→ Analyzer

DB ──────────→ PolicyCheck
   ──────────→ Summarizer
   ──────────→ Analyzer

WebSearch ───→ PolicyCheck
          ───→ Summarizer
          ───→ Analyzer
```

然后使用：

```text
provider
worker
```

限制边。

---

# 四、Provider / Worker 的语义应该现在就定死

我建议规定：

```text
provider
=
这个 Tool 允许接收哪些上一层节点的输出

worker
=
这个 Tool 允许将结果传播给哪些下一层节点
```

默认：

```python
providers="all"
workers="all"
```

那么：

```text
A → B
```

存在的条件是：

```text
layer(B) = layer(A) + 1

AND

B.provider allows A

AND

A.worker allows B
```

这是新版 Framework 最基本的 Edge Rule。

例如：

```text
A.worker = all

B.provider = [A, C]
```

则：

```text
A → B     YES
C → B     YES
D → B     NO
```

如果：

```text
A.worker = [B]

B.provider = [A, C]
```

仍然：

```text
A → B
```

成立。

也就是说两边都是**白名单约束**，最终取交集。

---

# 五、input/output schema 不应该删除

只是地位改变。

之前：

```text
Input / Output
→ 决定 Edge
```

现在：

```text
Layer / Provider / Worker
→ 决定 Edge

Input / Output Schema
→ 验证 Edge 是否可执行
```

例如显式允许：

```text
get_order → refund
```

但是：

```text
get_order output = Order

refund input = RefundRequest
```

那初始化时就应该给出：

```text
TopologyValidationWarning
```

而不是靠类型自动创造别的路径。

换句话说：

> **拓扑由开发者表达业务意图，Schema 负责发现明显错误。**

这更符合你的设计。

---

# 六、“每层多个 Tool”非常重要

这说明所谓 Tool Chain 实际上不应该建模成：

```text
A → B → C
```

而应该允许：

```text
Layer 1

{ DB, RAG }

        ↓

Layer 2

{ PolicyCheck, RiskCheck }

        ↓

Layer 3

{ Refund }
```

因此一个 Route 更适合表示成：

```text
RoutePlan

L1 = {DB, RAG}
L2 = {PolicyCheck, RiskCheck}
L3 = {Refund}
```

形式上：

```python
RoutePlan = [
    {"db", "rag"},
    {"policy_check", "risk_check"},
    {"refund"},
]
```

这点会影响整个 Runtime。

所以我甚至不建议继续使用：

```text
tool chain
```

作为内部严格术语。

更准确的是：

```text
Route
Execution Subgraph
Trace Graph
```

因为一次执行可能有分叉和汇合。

---

# 七、你的“靶场”其实会成为这个项目最有特色的东西

我认为这个概念应该直接进入核心架构，而不是当测试工具。

可以正式定义：

```text
Battlefield
```

它消费：

```json
{
  "scenarios": [
    {
      "id": "refund_001",
      "query": "帮用户退款订单123",
      "success_criteria": [
        "查询订单",
        "确认退款条件",
        "执行退款"
      ]
    }
  ]
}
```

然后产生两套模式：

```text
Fast Regression
Slow Regression
```

---

# 八、Fast Regression 的定位要非常严格

你的定义基本正确：

> 不实际调用 Tool，只验证当前 Tool Topology 是否覆盖业务场景。

例如：

```text
用户：
查一下订单123是否符合退款条件
```

Fast Regression 只让 Planner 看：

```text
Tool Metadata
Layer
Provider
Worker
Description
Schema
```

然后判断：

```text
这个问题是否存在合理 Route？
```

例如输出：

```text
Scenario: refund_001

Status:
COVERED

Candidate Route:

DB
↓
RefundPolicyCheck
```

或者：

```text
Status:
UNCOVERED

Reason:
No tool can determine refund eligibility.
```

但这里有一个很重要的工程边界：

> **Fast Regression 只能证明“从声明的能力来看似乎可完成”，不能证明工具真的可以完成。**

所以最好不要只有：

```text
True / False
```

而是：

```text
COVERED
UNCERTAIN
UNCOVERED
```

Fast Regression 本质是：

**Capability Coverage Test**

而不是：

**Execution Test**。

---

# 九、Slow Regression 才是真正学习拓扑的地方

Slow Regression：

```text
Scenario
↓
Agent
↓
Current Tool Topology
↓
自由选择每层节点
↓
实际执行
↓
Result
↓
Evaluation
↓
Trace
```

例如同一个退款场景跑 100 次。

可能出现：

```text
Route A

DB
↓
PolicyCheck
↓
Refund

55 次
```

```text
Route B

DB + RAG
↓
PolicyCheck
↓
Refund

32 次
```

```text
Route C

WebSearch
↓
Analyzer
↓
Refund

13 次
```

最终业务完成率：

```text
A   96%
B   98%
C   61%
```

平均成本：

```text
A   $0.012
B   $0.031
C   $0.048
```

延迟：

```text
A   800ms
B   1.6s
C   2.4s
```

这时候框架才真正开始获得信息。

---

# 十、这里实际上形成了两个 Graph

这个设计我强烈建议保留。

## Declared Topology

初始化生成：

```text
开发者声明的最大允许搜索空间
```

例如：

```text
100 Nodes
800 Edges
```

它基本不应该被物理修改。

然后有：

## Active Topology

根据 regression 得到：

```text
当前推荐执行拓扑
```

比如：

```text
100 Nodes
230 Edges
```

这样：

```text
Declared Topology
        ↓
Regression
        ↓
Active Topology v1
        ↓
Regression
        ↓
Active Topology v2
```

不要直接：

```text
没有使用
↓
delete edge
```

否则后续很难恢复。

应该：

```text
enabled = false
```

或者创建新的：

```text
TopologyVersion
```

---

# 十一、剪枝逻辑不能只是“0 次使用就删除”

这个地方需要稍微约束一下，否则很容易把系统越训越窄。

假设：

```text
A → B
```

从来没走过。

有两种可能：

```text
确实没用
```

或者：

```text
Agent 从来没有探索到
```

完全不是一回事。

因此剪枝应该至少满足：

```text
足够的 Scenario Coverage
+
足够 Trial 次数
+
路径长期未使用
+
删除后 Fast Regression 不出现明显能力缺口
```

之后：

```text
edge.status = pruning_candidate
```

然后再跑一轮 regression。

通过：

```text
disabled
```

而不是物理删除。

---

# 十二、这其实会自然形成“探索—利用”

你前面说：

> 同一业务可能有不同路线。

这其实特别重要。

例如：

```text
Route A
quality = 0.97
latency = 800ms
cost = $0.03
```

```text
Route B
quality = 0.96
latency = 500ms
cost = $0.01
```

```text
Route C
quality = 0.98
latency = 2s
cost = $0.08
```

没有绝对意义上的：

```text
Best Route
```

它们可能分别是：

```text
Fast
Balanced
High Quality
```

所以不要急着压缩成一个：

```text
score = 0.82
```

最好保留一个向量：

| Route | Success | Quality | Latency | Cost |
| ----- | ------: | ------: | ------: | ---: |
| A     |     98% |    0.96 |   800ms | 0.03 |
| B     |     97% |    0.95 |   500ms | 0.01 |
| C     |     99% |    0.98 |  2100ms | 0.08 |

然后再形成：

```text
performance tier
cost tier
quality tier
```

这样未来才能做你说的：

> **不同工具链负载均衡。**

---

# 十三、这样看，“性能和成本分级”也应该针对 Route

不是：

```text
Tool A 很贵
```

这么简单。

因为真正的成本是：

```text
Route Cost
=
Σ Tool Cost
+
LLM Routing Cost
+
Parallel Overhead
```

性能也是：

```text
Route Latency
```

比如：

```text
DB + RAG
```

如果并行：

```text
latency ≈ max(DB, RAG)
```

而不是简单相加。

所以最终优化对象应该是：

> **Route / Execution Subgraph**

而不是单个 Tool。

---

# 十四、因此之前 Phase 1 的这些内容应该删除

之前我们设计：

```text
ArtifactKey
Goal(produces=...)
Backward Planner
Producer Resolution
Minimal Dependency DAG
Provider Priority
```

这些都不应该成为当前 Phase 1 主线。

尤其：

```text
Goal Artifact
↓
Backward Search
```

和你的框架已经明显冲突。

你的 Planner 应该是：

```text
User Query / Scenario
        ↓
Agent Planner
        ↓
Topology-constrained Tool Selection
        ↓
Layer-by-layer Route
```

不是：

```text
Artifact
↓
Deterministic Backward Resolver
```

---

# 十五、但是之前有一些东西可以保留

不是全部推倒。

这些仍然成立：

```text
Tool Registry

Graph / Topology 和 Runtime 分离

ExecutionPlan 独立存在

Async Tool Runtime

Custom Error Model

Tool Schema Validation

Execution Trace

Deterministic Initialization

Unit / Integration Test
```

只是它们的语义需要改。

---

# 十六、我建议整个 Phase 重新划分

现在更合理的开发路线是：

| Phase       | 目标       | 核心产物                                                                    |
| ----------- | -------- | ----------------------------------------------------------------------- |
| **Phase 0** | 定义框架原则   | Layer、Tool、Provider、Worker、Topology、Battlefield、Route、Trial             |
| **Phase 1** | 构建分层工具拓扑 | Tool Registry、Layer Registry、Topology Builder、provider/worker、初始化全连接    |
| **Phase 2** | 快回归      | Scenario JSON、Metadata Planner、Coverage Report、Missing Capability       |
| **Phase 3** | 慢回归      | Tool Runtime、Agent Routing、Multi-node Layer、Trial、TraceGraph、Evaluation |
| **Phase 4** | 拓扑学习与剪枝  | RouteStats、EdgeStats、Pruning Candidate、Topology Version                 |
| **Phase 5** | 路径排名     | Success/Quality/Latency/Cost、Route Tier、Pareto Routes                   |
| **Phase 6** | 在线路由     | 优化后的 Topology、Route Selection、Load Balancing                            |

这样整个项目逻辑会非常顺。

---

# 十七、Phase 1 应该变成什么

新的 Phase 1 唯一任务应该是：

> **证明我们可以通过 `layer / provider / worker` 声明生成一个可约束、可检查、可供 Agent 搜索的分层 Tool Topology。**

例如：

```text
L0
────────────────
UserContext


L1 READ
────────────────
DB
RAG
WebSearch


L2 ANALYZE
────────────────
PolicyCheck
RiskCheck
Summarizer


L3 ACTION
────────────────
Refund
Email
UpdateOrder
```

默认：

```text
L1 ↔ L2
```

相邻层全连接。

然后 Tool：

```python
Tool(
    name="refund",
    layer="action",
    providers=[
        "policy_check"
    ],
)
```

初始化以后自动：

```text
PolicyCheck → Refund
```

而：

```text
RiskCheck → Refund
Summarizer → Refund
```

不存在。

Phase 1 做到这里就够了。

**不要在 Phase 1 做 Slow Regression。**

---

# 十八、Phase 2 才开始体现这个项目真正的新意

因为：

```text
layer/provider/worker
```

本身其实并不算特别新的概念。

真正有意思的是：

```text
Declared Dense Topology
        ↓
Scenario Battlefield
        ↓
Fast Regression
        ↓
Slow Regression
        ↓
Observed Execution Graph
        ↓
Topology Optimization
```

我认为项目真正值得做的核心卖点应该变成：

> **不是让开发者手工编排一条 Agent Workflow，而是让开发者声明一个“允许的工具搜索空间”，再通过真实业务回归让这个搜索空间逐渐收敛。**

这跟我们之前说的“自动 dependency compiler”相比，我认为**更加接近你最开始那个神经网络 + 剪枝的想法**。

---

# 十九、我会重新定义整个项目

旧定义：

> A typed capability graph runtime for composable AI agents.

已经不够准确。

可以暂时改成：

> **A layered tool-routing and topology optimization framework for AI agents.**

中文：

> **一个面向智能体工具调用的分层路由与拓扑优化框架。**

核心循环变成：

```text
Declare
   ↓
Initialize
   ↓
Explore
   ↓
Evaluate
   ↓
Prune
   ↓
Rank
   ↓
Route
```

这才是现在这个项目真正的“灵魂”。

---

所以结论很明确：

**Phase 0 和 Phase 1 都需要重写。**

其中 Phase 0 主要重写**项目定义、核心抽象和核心公式**；Phase 1 则彻底去掉 `Artifact-driven backward planning`，改成 **Layered Tool Topology MVP**。

而 `fast regression → slow regression → trace statistics → pruning → route ranking` 应该成为后续连续四个 Phase 的主轴，而不是附加测试功能。
