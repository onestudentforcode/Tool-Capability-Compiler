# Phase 4 — Topology Learning & Safe Pruning

## 0. 阶段定位

Phase 4 建立项目第一套：

> **基于真实业务证据的 Tool Topology 学习与安全剪枝机制。**

Phase 1 得到：

```text
Declared Dense Topology
```

Phase 2 得到：

```text
Theoretical Capability Coverage
```

Phase 3 得到：

```text
Observed Execution Evidence
```

Phase 4 第一次将这些信息组合起来：

```text
Declared Topology
        +
Fast Regression Coverage
        +
Slow Regression Trace
        +
Node / Edge Opportunity
        +
Node / Edge Usage
        +
Route Distribution
        +
Business Evaluation
        ↓
Candidate Detection
        ↓
Counterfactual Validation
        ↓
Probe Regression
        ↓
Candidate Topology
        ↓
Fast Regression Again
        ↓
Slow Regression Again
        ↓
Accept / Reject
        ↓
New Active Topology Version
```

Phase 4 的目标不是：

```text
找到最优 Tool Route
```

而是：

> **在不明显损害业务能力的前提下，把初始化时较稠密的 Tool 搜索空间逐步收敛为更小、更稳定、更有业务证据支持的 Active Topology。**

---

# 1. Phase 4 核心目标

本阶段必须完成：

1. 消费 Phase 2 Fast Regression 数据
2. 消费 Phase 3 Slow Regression Trace
3. 建立 Node / Edge Evidence
4. 区分“没机会使用”和“有机会但没使用”
5. 识别潜在冗余 Edge
6. 识别潜在冗余 Node
7. 识别业务关键 Edge / Node
8. 识别稀有但必要的能力路径
9. 对低证据 Edge 进行定向 Probe
10. 对候选剪枝进行 Counterfactual Fast Regression
11. 生成 Candidate Topology
12. 对 Candidate Topology 重新执行 Fast Regression
13. 对 Candidate Topology 重新执行 Slow Regression
14. 判断是否允许接受本轮剪枝
15. 生成新的 Active Topology Version
16. 保留完整 Pruning Evidence
17. 支持拒绝和回滚

---

# 2. Phase 4 非目标

本阶段明确不实现：

```text
最终 Route Ranking
Route Performance Tier
Route Cost Tier
线上 Route Selection
线上 Load Balancing
Retry
Fallback
Circuit Breaker
动态线上剪枝
Production Traffic Learning
Bandit Routing
强化学习
Loop
Recursive Routing
跨层回跳
自动修改 Tool 业务代码
```

尤其：

> Phase 4 不负责判断 Route A 是否应该优先于 Route B。

这属于 Phase 5。

Phase 4 只回答：

> **某些 Node / Edge 是否已经具备足够证据，可以安全地从 Active Topology 中移除。**

---

# 3. Phase 4 的核心原则

整个阶段遵循：

```text
Evidence
↓
Candidate
↓
Validate
↓
Accept
```

禁止：

```text
Unused
↓
Delete
```

---

# 4. Graph 本身仍然不是答案

项目从 Phase 1 开始的核心原则继续保持：

> **Topology 是 Agent 的搜索空间。**

Phase 4 实际优化的是：

```text
Search Space
```

而不是某一条固定 Pipeline。

因此：

```text
Topology Optimization
≠
Workflow Compilation
```

---

# 5. Declared Topology 与 Active Topology

Phase 4 必须正式区分：

```text
DeclaredTopology
```

和：

```text
ActiveTopology
```

---

# 6. DeclaredTopology

DeclaredTopology 来自开发者声明：

```text
layer
provider
worker
```

以及 Phase 1 初始化规则。

例如：

```text
L1 → L2 默认全连接
```

形成：

```text
DeclaredTopology
```

它表示：

> **开发者认为理论上允许存在的最大 Tool 搜索空间。**

DeclaredTopology 原则上保持稳定。

Phase 4 不直接修改它。

---

# 7. ActiveTopology

ActiveTopology 表示：

> 当前实际向 Agent 开放的 Tool 搜索空间。

初始化：

```text
ActiveTopology v1
≈
DeclaredTopology
```

经过 Phase 4：

```text
DeclaredTopology
        ↓
ActiveTopology v1
        ↓
Optimization Round
        ↓
ActiveTopology v2
        ↓
Optimization Round
        ↓
ActiveTopology v3
```

ActiveTopology 会逐渐收敛。

---

# 8. 不物理删除 Edge

Phase 4 禁止：

```text
delete edge
```

应该：

```text
edge.status = disabled
```

或者通过：

```text
TopologyPatch
```

覆盖。

原因：

```text
可回滚
可解释
可比较
可重新启用
```

---

# 9. 不修改 provider / worker 原始声明

假设：

```text
DB.worker = all
```

Phase 4 发现：

```text
DB → Summarizer
```

长期无价值。

不应该自动改为：

```text
DB.worker = [
    PolicyCheck,
    RiskCheck
]
```

因为那会修改开发者声明的：

```text
Declared Topology
```

正确方式：

```text
Declared Edge:
DB → Summarizer

ActiveTopology:
disabled
```

---

# 10. Phase 4 的拓扑层级

正式形成：

```text
Tool Declaration
      ↓
DeclaredTopology
      ↓
Topology Overlay / Patch
      ↓
ActiveTopology
```

未来线上 Runtime 只消费：

```text
ActiveTopology
```

开发者仍可以重新生成：

```text
DeclaredTopology
```

---

# 11. Phase 4 核心输入

至少消费：

```text
TopologySnapshot
FastRegressionReport
SlowRegressionReport
ScenarioSuite
```

其中 Slow Regression 必须包含：

```text
Node Available Count
Node Selected Count

Edge Opportunity Count
Edge Observed Count

Route Usage

Business Success
Quality

Tool / Route Latency
Tool / Route Cost
```

---

# 12. 最重要的区别：Unused ≠ Useless

假设：

```text
Edge A

observed_count = 0
```

不能直接认为：

```text
Edge A useless
```

需要首先知道：

```text
opportunity_count
```

---

# 13. Case A：没有探索机会

```text
opportunity = 0
observed = 0
```

意味着：

```text
NO EVIDENCE
```

而不是：

```text
LOW VALUE
```

---

# 14. Case B：机会极少

```text
opportunity = 3
observed = 0
```

证据仍然不足。

应该：

```text
DEFER
```

或者进入：

```text
PROBE
```

---

# 15. Case C：大量机会，从未使用

```text
opportunity = 1000
observed = 0
```

这是非常强的：

```text
Pruning Signal
```

但仍然不是直接删除证据。

---

# 16. Case D：经常使用

```text
opportunity = 1000
observed = 700
```

说明：

```text
Edge highly active
```

一般不进入初始剪枝候选。

---

# 17. Edge Selection Rate

Phase 3 已经能够计算：

```text
Edge Usage Rate
=
observed_count
/
opportunity_count
```

Phase 4 正式消费该指标。

例如：

```text
DB → PolicyCheck

opportunity = 900
observed = 720

usage_rate = 80%
```

---

# 18. Node Selection Rate

同理：

```text
Node Selection Rate
=
selected_count
/
available_count
```

例如：

```text
WebSearch

available = 2000
selected = 4

selection_rate = 0.2%
```

这是候选信号。

---

# 19. 但 Selection Rate 不是价值函数

必须明确：

```text
low usage
≠
low business value
```

例如：

```text
FraudRiskCheck
```

只服务：

```text
0.1%
```

的特殊业务。

它可能：

```text
usage_rate = 0.1%
```

但这条能力不能被删除。

所以 Phase 4 必须结合：

```text
Scenario Coverage
Capability Criticality
Route Diversity
Business Success
```

综合判断。

---

# 20. EdgeEvidence

新增：

```python
@dataclass
class EdgeEvidence:
    edge_id: str

    opportunity_count: int
    observed_count: int

    successful_trial_count: int
    failed_trial_count: int

    scenario_count: int
    successful_route_count: int

    usage_rate: float

    fast_coverage_support: int

    protected: bool
```

---

# 21. NodeEvidence

```python
@dataclass
class NodeEvidence:
    tool_name: str

    available_count: int
    selected_count: int

    successful_trial_count: int
    failed_trial_count: int

    scenario_count: int
    capability_count: int

    selection_rate: float

    protected: bool
```

---

# 22. Evidence 不做因果推断

例如：

```text
Edge A
出现在大量失败 Trial
```

不能直接得出：

```text
Edge A 导致失败
```

同样：

```text
Edge B
出现在大量成功 Trial
```

不能证明：

```text
Edge B 导致成功
```

这些只能作为：

```text
Candidate Prioritization Signal
```

真正剪枝必须依赖后面的：

```text
Counterfactual Regression
```

---

# 23. Phase 4 需要引入 Counterfactual

例如怀疑：

```text
DB → Summarizer
```

没有价值。

不是直接禁用。

而是构造：

```text
CandidateTopology
=
ActiveTopology
-
(DB → Summarizer)
```

然后重新测试：

```text
Fast Regression
Slow Regression
```

这才是最重要的验证逻辑。

---

# 24. Candidate 状态

建议：

```python
class CandidateStatus(str, Enum):
    IDENTIFIED = "identified"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROBE_REQUIRED = "probe_required"
    PROTECTED = "protected"
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
```

---

# 25. Candidate Reason

例如：

```text
HIGH_OPPORTUNITY_UNUSED

LOW_SELECTION_RATE

REDUNDANT_CONNECTIVITY

REDUNDANT_PROVIDER_PATH

NO_SUCCESSFUL_ROUTE_SUPPORT

NODE_NEVER_SELECTED
```

这些只是：

```text
why investigate
```

而不是：

```text
why delete
```

---

# 26. Candidate Generation

第一版可以采用规则式系统：

```text
if opportunity >= min_opportunity
and usage_rate <= candidate_usage_threshold:
    candidate
```

例如：

```text
min_opportunity = 100

candidate_usage_threshold = 0.01
```

具体默认值不写死在核心代码。

全部进入：

```text
PruningConfig
```

---

# 27. PruningConfig

推荐：

```python
@dataclass(frozen=True)
class PruningConfig:
    min_edge_opportunities: int = 100
    min_node_availability: int = 100

    edge_usage_threshold: float = 0.01
    node_selection_threshold: float = 0.01

    max_fast_coverage_drop: float = 0.0

    max_success_rate_drop: float = 0.01
    max_quality_drop: float = 0.02

    max_pruning_batch_size: int = 20
```

这些参数只是默认配置。

不能散布在算法中。

---

# 28. 先剪 Edge，再考虑 Node

Phase 4 的默认优化顺序：

```text
Edge Pruning
↓
Topology Re-evaluation
↓
Node Pruning
```

原因是：

```text
Node
```

比：

```text
Edge
```

粒度更粗。

Tool 本身可能仍然在其他 Route 中具有价值。

---

# 29. Edge Pruning 是 Phase 4 主线

系统初始化：

```text
Adjacent Layers Full Connection
```

因此最大冗余通常存在于：

```text
Edge
```

而不是：

```text
Tool
```

所以 Phase 4 首要目标是：

> **把 Dense Inter-layer Connections 收敛成业务真实使用的稀疏连接。**

---

# 30. Node Pruning

Tool 可以进入 Node Pruning Candidate，但要求更严格。

至少：

```text
availability 足够高
AND
selected 极少
AND
不是唯一 Capability Provider
AND
不是必要 Bridge Node
AND
移除后 Fast Coverage 不下降
AND
Slow Regression 不明显下降
```

才可以禁用。

---

# 31. Node 被禁用 ≠ Tool 被删除

例如：

```text
Tool Registry:
WebSearch exists

ActiveTopology:
WebSearch disabled
```

后续：

```text
Topology rollback
```

可以重新启用。

---

# 32. Capability Protection

如果某 Tool 是：

```text
refund.special_case.check
```

唯一 Provider：

```text
Tool A
```

即使：

```text
selected_count = 0
```

也不能自动进入普通剪枝。

应该：

```text
protected = true
```

---

# 33. Unique Capability Provider

定义：

```text
Capability C
```

如果 ActiveTopology 中只有：

```text
Tool A
```

提供 C：

```text
Tool A = unique provider
```

默认保护。

---

# 34. 但 Unique Provider 并不意味着永远保护

如果：

```text
Capability C
```

在全部 Scenario Suite 中完全没有业务需求：

```text
affected_scenarios = 0
```

可以进入：

```text
manual_review
```

但 Phase 4 MVP 不自动禁用。

---

# 35. Sentinel Scenario

Phase 4 必须正式支持：

> **Sentinel Scenario**

用来保护：

```text
低频
但不能丢失
```

的业务能力。

例如：

```text
账户注销
异常退款
高风险转账
特殊投诉
```

这些 Scenario 在线 Query 中可能极少出现。

不能依赖真实频率保护。

---

# 36. Sentinel 示例

```json
{
  "id": "critical_refund_001",
  "query": "处理特殊退款案例",
  "metadata": {
    "sentinel": true,
    "priority": "critical"
  }
}
```

---

# 37. Sentinel Rule

任何 CandidateTopology 如果导致：

```text
Sentinel Scenario:

COVERED
↓
UNCOVERED
```

直接：

```text
REJECT
```

不允许通过整体 Coverage Rate 掩盖。

---

# 38. 为什么不能只看全局 Coverage

例如：

```text
Before:
99.8%

After:
99.7%
```

看起来只下降：

```text
0.1%
```

但丢失的可能恰好是：

```text
Critical Payment Cancellation
```

所以需要：

```text
Global Coverage
Category Coverage
Sentinel Coverage
```

同时验证。

---

# 39. Scenario Priority

建议利用：

```text
metadata.priority
```

例如：

```text
critical
high
normal
low
```

Critical Scenario：

```text
zero tolerance
```

普通 Scenario：

可以按照配置允许极小波动。

---

# 40. Route Diversity Protection

你的项目未来希望支持：

```text
多条 Route
↓
负载均衡
```

因此 Phase 4 不能把一个 Scenario 的所有替代 Route 都剪掉，只留下唯一 Route。

必须保护：

```text
Route Diversity
```

---

# 41. Route Diversity Floor

例如：

```text
Scenario A

Successful Route A
Successful Route B
Successful Route C
```

可以配置：

```text
min_successful_route_families = 2
```

那么 Phase 4 不应该把：

```text
B
C
```

全部剪掉。

---

# 42. 这里不涉及 Route Ranking

Phase 4 不判断：

```text
A 比 B 好
```

只判断：

> 是否仍然保留了足够的替代路径。

---

# 43. Successful Route Support

EdgeEvidence 应统计：

```text
successful_route_count
```

例如：

```text
Edge X
```

只被：

```text
Route C
```

使用。

但 Route C 是一个长期成功的替代 Route。

那么 Edge X 即使：

```text
usage_rate 很低
```

也应该慎重剪枝。

---

# 44. Structural Criticality

Phase 4 可以分析：

```text
某个 Edge 被移除后，
哪些能力 Route 会断开？
```

形成：

```text
StructuralCriticality
```

---

# 45. Fast Counterfactual Test

对于 Edge：

```text
A → B
```

创建：

```text
Topology_without_edge
```

然后运行 Phase 2：

```text
Fast Regression
```

如果：

```text
coverage unchanged
```

则说明：

```text
静态能力层面存在替代 Route
```

如果：

```text
coverage drops
```

Edge 自动：

```text
PROTECTED
```

或：

```text
REJECTED
```

---

# 46. Fast Counterfactual 是第一道安全门

候选 Edge 流程：

```text
Candidate
↓
Disable Virtually
↓
Fast Regression
↓
Coverage Drop?
```

如果：

```text
YES
```

立即：

```text
REJECT
```

无需进入昂贵 Slow Regression。

---

# 47. Topology Gap Regression

必须特别检查：

```text
Capability 仍存在
但 Route 因 Edge 被剪导致 disconnected
```

即：

```text
TOPOLOGY_DISCONNECTED
```

这是 Phase 4 最常见的回归风险。

---

# 48. Probe Regression

有些 Candidate：

```text
opportunity 很低
observed = 0
```

无法判断。

这时不应该：

```text
keep forever
```

也不应该：

```text
prune
```

应该：

```text
PROBE_REQUIRED
```

---

# 49. Probe 的目的

Probe 回答：

> 这条 Edge / Route 是真的没有业务价值，还是 Phase 3 的自由探索从未走到这里？

---

# 50. Directed Exploration

Phase 3 已实现两种执行入口（`SlowRegressionRunner`）：

```text
free       自由探索：每层取"当前可达工具"的稳定子集，受 max_tools_per_layer 约束
basefast   seed 探索：以 CandidateRoute 为起点，逐层构建 ExpansionPlan 轮转变体
```

Phase 4 的探针（Probe）**复用 basefast 机制**，而不是新造一种模式：

```text
Probe = 针对候选 Edge 构造"定向 seed"（把目标 Edge 强制纳入首轮扩展）+ SlowRegressionRunner
```

例如候选：

```text
ERP → PolicyCheck
```

系统能找到：

```text
理论上能够经过该 Edge 的 Scenario
```

然后以"强制包含 `ERP → PolicyCheck` 的 CandidateRoute 作为 seed"运行 Slow Regression：

```text
directed probe trial
```

从而增加该 Edge 被探索的机会——这正是 basefast 的 ExpansionPlan 在"种子轮"能覆盖的变体。

---

# 51. Probe 不等于强制判优

Probe 只增加：

```text
Evidence
```

如果该 Edge 可以形成：

```text
成功 Route
```

那么它可能：

```text
保留
```

或者至少：

```text
延迟剪枝
```

---

# 52. 为什么 Probe 非常重要

LLM Router 本身存在偏好。

例如：

```text
DB description 写得很好
ERP description 很模糊
```

可能造成：

```text
DB selected 1000 times
ERP selected 0 times
```

这不一定说明：

```text
ERP 无价值
```

而可能说明：

```text
Router Bias
```

Probe 可以部分区分这两种情况。

---

# 53. Optimization Dataset 与 Validation Dataset

Phase 4 必须避免：

> 使用同一批 Scenario 生成剪枝规则，再用同一批 Scenario 宣布剪枝成功。

否则很容易：

```text
Topology Overfitting
```

---

# 54. Optimization Set

用于：

```text
统计 Edge Usage
统计 Node Usage
生成 Candidate
```

---

# 55. Validation Set

用于：

```text
验证 CandidateTopology
```

不得参与：

```text
Candidate Generation
```

---

# 56. Sentinel Set

第三类：

```text
Sentinel Set
```

专门保护关键业务。

因此：

```text
Scenario Dataset
├── Optimization Set
├── Validation Set
└── Sentinel Set
```

---

# 57. Dataset Split

MVP 可以：

```text
80% Optimization
20% Validation
```

并独立维护：

```text
Sentinel Scenarios
```

也允许业务自己提供固定划分。

---

# 58. 分割必须稳定

不能每轮随机变化。

建议：

```text
hash(scenario_id)
```

进行 deterministic split。

这样不同 Topology Version 可以公平比较。

---

# 59. Candidate Generation 只能看 Optimization Set

严格禁止：

```text
先看 Validation Set
↓
再决定剪谁
```

否则 Validation 就失去意义。

---

# 60. 单 Edge Counterfactual 与 Batch Pruning

如果：

```text
Edge A
```

单独删除安全，

```text
Edge B
```

单独删除也安全，

不代表：

```text
A + B
```

一起删除安全。

因为它们可能互为替代路径。

---

# 61. 因此最终必须做 Batch Validation

流程：

```text
Individual Candidate Check
        ↓
Candidate Batch
        ↓
Apply All to Shadow Topology
        ↓
Full Validation
```

---

# 62. max_pruning_batch_size

必须限制：

```text
max_pruning_batch_size
```

例如：

```text
20
```

避免一次：

```text
disable 300 edges
```

导致问题难以定位。

---

# 63. Progressive Pruning

推荐：

```text
Round 1
300 edges → 270

Round 2
270 → 240

Round 3
240 → 225
```

而不是：

```text
300 → 80
```

一次性激进收敛。

---

# 64. CandidateTopology

新增：

```python
@dataclass
class CandidateTopology:
    base_version: str
    patch: TopologyPatch
    topology: ToolTopology
```

CandidateTopology 永远基于一个明确：

```text
ActiveTopology Version
```

---

# 65. TopologyPatch

```python
@dataclass
class TopologyPatch:
    disabled_edges: tuple[str, ...]
    disabled_nodes: tuple[str, ...]
```

未来可以增加：

```text
enabled_edges
enabled_nodes
```

用于 rollback / restore。

---

# 66. PruningDecision

每个候选必须保留：

```python
@dataclass
class PruningDecision:
    target_id: str
    target_type: str

    reason: str

    evidence: dict

    status: CandidateStatus

    validation_result: Any | None
```

---

# 67. 禁止黑盒剪枝

不能只输出：

```text
disabled:
- edge_31
- edge_72
```

必须能解释：

```text
edge_31

Opportunity: 1240
Observed: 0
Usage Rate: 0%

Fast Counterfactual Coverage Drop: 0

Successful Route Support: 0

Sentinel Impact: 0

Probe Trials: 20
Probe Success: 0

Decision:
candidate
```

---

# 68. Validation Pipeline

完整 CandidateTopology 验证：

```text
CandidateTopology
        ↓
Fast Regression
        ↓
Sentinel Regression
        ↓
Slow Regression
        ↓
Metrics Comparison
        ↓
Accept / Reject
```

---

# 69. Validation 第一层：Structural Validation

先检查：

```text
Topology valid
No invalid provider / worker edge
No impossible layer connection
No orphan structural corruption
```

失败直接拒绝。

---

# 70. Validation 第二层：Fast Regression

比较：

```text
BaselineTopology
vs
CandidateTopology
```

至少比较：

```text
Global Coverage
Category Coverage
Sentinel Coverage
Capability Gaps
Topology Gaps
```

---

# 71. Fast Acceptance Rule

基本规则：

```text
global_coverage_drop
<=
max_fast_coverage_drop
```

默认推荐：

```text
0
```

Phase 4 MVP 尽量：

> **不接受理论能力覆盖下降。**

---

# 72. Sentinel Acceptance Rule

```text
sentinel_coverage_drop
=
0
```

必须严格为：

```text
0
```

---

# 73. Category Guard

不能只看 Global Coverage。

例如：

```text
refund:
99% → 80%
```

即使全局：

```text
95% → 94.9%
```

也不能接受。

因此配置：

```text
max_category_coverage_drop
```

---

# 74. Validation 第三层：Slow Regression

只有通过 Fast Regression 后，才执行昂贵 Slow Regression。

使用：

```text
Validation Set
```

执行相同：

```text
Router Config
Model
Prompt Version
Tool Metadata Version
Fixture
Trial Count
```

进行公平比较。

---

# 75. Slow Validation 关注什么

至少比较：

```text
Business Success Rate
Execution Error Rate
Quality
Unique Successful Routes
Available Tool Count
Router Token Usage
Latency
Cost
```

---

# 76. Business Success 是第一核心指标

候选拓扑：

```text
edges -20%
```

但：

```text
business success
95% → 87%
```

必须拒绝。

---

# 77. Success Tolerance

配置：

```text
max_success_rate_drop
```

例如：

```text
0.01
```

即最大允许：

```text
1 percentage point
```

下降。

对于 critical category：

```text
0
```

---

# 78. Quality Guard

例如：

```text
success rate unchanged
```

但答案质量：

```text
0.91 → 0.72
```

同样说明剪枝过度。

因此：

```text
max_quality_drop
```

---

# 79. Error Guard

必须检查：

```text
execution_error_rate
```

例如剪掉一些 Edge 后：

```text
Router 频繁找不到可用节点
```

可能导致：

```text
LayerExecutionError
```

即使部分 Scenario 仍然成功，也应该被发现。

---

# 80. Route Diversity Guard

比较：

```text
Successful Route Families
```

例如：

```text
Before:
3

After:
1
```

如果配置要求：

```text
min_route_families = 2
```

则拒绝。

---

# 81. 什么是 Route Family

Phase 4 MVP 可以直接：

```text
route_id
```

作为 Route Family。

未来 Phase 5 可以对结构相似 Route 进行聚类。

当前不做。

---

# 82. Topology Reduction Metrics

Phase 4 需要记录优化收益：

```text
Node Count
Edge Count

Average Available Tools Per Layer Decision

Router Tool Metadata Tokens
```

---

# 83. Edge Reduction Rate

```text
Edge Reduction Rate
=
1 -
candidate_edge_count / baseline_edge_count
```

例如：

```text
300
↓
210

reduction = 30%
```

---

# 84. Search Space Reduction

Edge 数量下降并不是唯一指标。

更重要的是：

```text
平均每次 Router Decision 可以看到多少 Tool。
```

例如：

```text
Before:
12.4 tools

After:
5.7 tools
```

这更直接体现：

```text
Routing Search Space
```

是否收敛。

---

# 85. Router Context Reduction

如果 Tool Description 占据大量 Token，

Topology 剪枝后：

```text
available tools
```

减少，

Router Prompt Token 理论上也下降。

因此可以统计：

```text
average_router_input_tokens
```

---

# 86. Phase 4 不以 Cost 为主要剪枝依据

虽然可以记录：

```text
Cost
Latency
```

但不要因为：

```text
Tool A expensive
```

就直接剪掉 A。

因为：

```text
Route Performance / Cost Optimization
```

属于 Phase 5。

Phase 4 主要依据：

```text
Topology Redundancy
+
Business Preservation
```

---

# 87. Candidate Score

可以为了排序 Candidate 定义：

```text
candidate_score
```

但它只决定：

```text
先检查谁
```

不能直接决定：

```text
剪谁
```

---

# 88. Candidate Score 示例

可以考虑：

```text
high opportunity
+
low usage
+
low successful-route support
+
low capability criticality
```

得到较高候选优先级。

但最终必须经过 Validation Gate。

---

# 89. Candidate Score 不进入 Runtime

这是纯：

```text
offline optimization metric
```

不能影响 Phase 3 Router。

---

# 90. Accept / Reject

CandidateTopology 最终：

```text
ACCEPTED
```

或者：

```text
REJECTED
```

不允许：

```text
partial mysterious mutation
```

---

# 91. Accepted Topology

如果：

```text
CandidateTopology v5
```

通过全部验证：

```text
ActiveTopology v4
↓
ActiveTopology v5
```

---

# 92. Rejected Topology

如果失败：

```text
CandidateTopology
↓
REJECTED
```

当前：

```text
ActiveTopology
```

完全不变。

---

# 93. Validation Failure 必须定位

例如：

```text
Candidate rejected

Reason:
refund category success rate

Before:
97%

After:
91%

Affected edges:
DB → RefundCheck
RAG → RefundCheck
```

这样才能进入下一轮更小批次尝试。

---

# 94. Binary Search / Batch Isolation

如果：

```text
20 edges
```

批量剪枝后失败，

Phase 4 可以把 Candidate Batch：

```text
20
↓
10 + 10
```

分别验证。

用于定位：

```text
harmful pruning subset
```

MVP 可以先手工或简单实现。

---

# 95. Pruning Round

正式定义：

```python
@dataclass
class PruningRound:
    id: str
    base_topology_version: str

    optimization_report_id: str

    candidates: tuple[PruningDecision, ...]

    patch: TopologyPatch

    validation: ValidationReport | None
```

---

# 96. OptimizationRun

一个完整 Phase 4 Run 可以包含多个：

```text
PruningRound
```

例如：

```text
OptimizationRun 001

Round 1
300 → 280

Round 2
280 → 260

Round 3
260 → 248
```

---

# 97. 停止条件

不能无限剪。

可以在以下任意情况停止：

```text
No New Candidates

Coverage Constraint Reached

Success Constraint Reached

Reduction Improvement Too Small

max_rounds reached
```

---

# 98. Conservative Default

Phase 4 MVP 默认应：

```text
Conservative
```

宁可：

```text
多保留一些 Edge
```

也不要：

```text
过度剪枝
```

因为本项目后续仍需要：

```text
Alternative Routes
```

支持 Phase 5 / Phase 6。

---

# 99. Pruning Plateau

例如：

```text
Round 1: -80 edges
Round 2: -30
Round 3: -8
Round 4: -1
```

说明：

```text
Topology approaching stable region
```

可以停止。

---

# 100. Active Topology Version

建议版本记录：

```json
{
  "version": "topology_v4",

  "base": "topology_v3",

  "disabled_edges": [
    "edge_31",
    "edge_72"
  ],

  "disabled_nodes": [],

  "optimization_run": "opt_20260905_001"
}
```

---

# 101. Topology Version 必须不可变

一旦：

```text
topology_v4
```

生成，

不能原地修改。

下一次：

```text
topology_v5
```

必须创建新版本。

---

# 102. Rollback

如果后续发现：

```text
v5
```

有问题，

可以：

```text
Active:
v5
↓
Rollback
↓
v4
```

因为：

```text
DeclaredTopology
```

和旧 Active Version 都保留。

---

# 103. Optimization Report

最终报告至少包括：

```text
Baseline Topology
Candidate Topology
Accepted Topology

Node Count
Edge Count

Coverage Before / After
Success Before / After
Quality Before / After

Route Diversity Before / After

Average Available Tools Before / After

Candidate Decisions

Rejected Candidates
Protected Candidates
Probe Results
```

---

# 104. 一个典型报告

例如：

```text
Topology Optimization

Base:
v3

Candidate:
v4

Edges:
312 → 221
-29.2%

Nodes:
48 → 46

Fast Coverage:
96.8% → 96.8%

Sentinel Coverage:
100% → 100%

Slow Business Success:
93.4% → 93.7%

Quality:
0.891 → 0.895

Average Available Tools:
9.8 → 5.3

Router Input Tokens:
-31%

Unique Successful Routes:
41 → 37

Decision:
ACCEPTED
```

这里：

```text
Route 37 比 Route 41 哪个更好
```

仍然不是 Phase 4 需要回答的问题。

---

# 105. Pruning Candidate 示例

假设：

```text
WebSearch → RefundPolicyCheck
```

Phase 3：

```text
opportunity = 1284
observed = 1
successful_route_support = 0
```

Phase 2 Counterfactual：

```text
coverage drop = 0
```

Sentinel：

```text
impact = 0
```

进入：

```text
PRUNING CANDIDATE
```

---

# 106. 反例：稀有 Edge

```text
RiskDB → FraudCheck
```

数据：

```text
opportunity = 12
observed = 4
```

只服务：

```text
fraud category
```

且：

```text
sentinel scenario
```

依赖它。

结果：

```text
PROTECTED
```

即使全局 usage 极低。

---

# 107. 反例：Router Bias

```text
ERP → PolicyCheck

opportunity = 1000
observed = 0
```

Fast Counterfactual：

```text
coverage unchanged
```

但 ERP 理论上提供 DB 的替代路径。

Phase 4 可以：

```text
PROBE_REQUIRED
```

执行：

```text
20 directed probe trials
```

如果：

```text
18 successful
```

说明：

```text
它实际上是一条可用备用路径
```

不应轻易剪掉。

---

# 108. 这与未来负载均衡直接相关

如果过早把：

```text
ERP Route
```

剪掉，

Phase 6 就失去了：

```text
Load Balancing Candidate
```

所以 Phase 4 的目标不是：

```text
只剩唯一最常用 Route
```

而是：

> **删除明显无价值的搜索空间，同时保存有证据的业务替代路径。**

---

# 109. Node Candidate 示例

```text
GenericSummarizer
```

数据：

```text
available = 6000
selected = 2

unique capabilities = 0

successful routes = 0

bridge role = false
```

Fast Counterfactual：

```text
coverage unchanged
```

Slow Validation：

```text
success unchanged
```

则可以：

```text
disable node
```

---

# 110. Bridge Node Protection

有些节点：

```text
NormalizeOrder
```

本身：

```text
selection rate 不高
```

但它可能连接：

```text
DB
↓
NormalizeOrder
↓
PolicyCheck
```

这种：

```text
Bridge Node
```

不能只根据自身 Capability / Usage 删除。

需要检查：

```text
Structural Reachability
```

---

# 111. Orphan Node

剪枝之后可能出现：

```text
Node
```

既没有：

```text
incoming active edge
```

又没有：

```text
outgoing active edge
```

可以标记：

```text
orphaned
```

但仍然不要立即物理删除。

---

# 112. Phase 4 与 Tool Metadata 的关系

如果一个 Edge：

```text
理论上合理
```

但长期无法被 Agent 正确选择，

问题可能不一定是拓扑。

也可能是：

```text
Tool Description
Tool Name
Capability Metadata
```

有问题。

因此 Phase 4 Report 可以输出：

```text
metadata_review_candidate
```

但不自动修改 Description。

---

# 113. Router Bias Report

例如：

```text
Tool A:
available 2000
selected 0

Probe:
successful 18/20
```

这非常可能说明：

```text
Routing / Metadata Problem
```

而不是：

```text
Tool Capability Problem
```

这类情况应该单独报告。

---

# 114. Phase 4 的关键分类

最终一个 Edge / Node 至少可能属于：

```text
ACTIVE

PRUNING_CANDIDATE

PROBE_REQUIRED

PROTECTED

DISABLED

INSUFFICIENT_EVIDENCE
```

---

# 115. Phase 4 CLI

可以提供：

```bash
tool-topology optimize analyze \
  --topology topology_v3.json \
  --fast-report fast_v3.json \
  --slow-report slow_v3.json
```

生成：

```text
candidate report
```

---

# 116. Candidate Validation

```bash
tool-topology optimize validate \
  --base topology_v3 \
  --patch pruning_patch_004.json \
  --suite customer_service_v4.json
```

执行：

```text
Fast Regression
+
Slow Regression
```

---

# 117. Commit

验证成功后：

```bash
tool-topology optimize commit \
  --candidate candidate_topology_v4.json
```

生成新的：

```text
ActiveTopology v4
```

这里的 commit 只是：

```text
offline topology version commit
```

不是生产自动发布。

---

# 118. Rollback

```bash
tool-topology topology activate topology_v3
```

未来生产管理方式可在后续 Phase 再完善。

---

# 119. 推荐目录结构

Phase 4 新增（注意：实际包名是 `capability_runtime`，Phase 4 的优化逻辑作为其下一个平行的 `optimization/` 顶包，而不是重构现有 `regression/`）：

```text
src/capability_runtime/
    │
    ├── optimization/                 # Phase 4 新增
    │   ├── evidence.py
    │   ├── candidate.py
    │   ├── analyzer.py
    │   ├── probe.py
    │   ├── counterfactual.py
    │   ├── pruning.py
    │   ├── validation.py
    │   └── report.py
    │
    ├── topology/
    │   ├── version.py                # ActiveTopology 版本（TopologyVersion）
    │   ├── patch.py                  # TopologyPatch（disabled_edges / disabled_nodes）
    │   └── snapshot.py
    │
    └── regression/                   # 现有 fast / slow，Phase 4 消费其输出
        ├── fast/...
        └── slow/...
```

---

# 120. 模块职责

## EvidenceAnalyzer

负责：

```text
Trace
↓
NodeEvidence / EdgeEvidence
```

不得修改 Topology。

---

## CandidateDetector

负责：

```text
Evidence
↓
PruningCandidate
```

---

## ProbeRunner

负责：

```text
低证据 Candidate
↓
Directed Slow Regression
↓
Additional Evidence
```

---

## CounterfactualAnalyzer

负责：

```text
Virtual Remove
↓
Fast Regression
↓
Impact
```

---

## TopologyOptimizer

负责：

```text
Candidate Set
↓
TopologyPatch
```

---

## ValidationRunner

负责：

```text
CandidateTopology
↓
Fast + Slow Validation
```

---

## TopologyVersionManager

负责：

```text
ActiveTopology Version
Candidate Version
Commit
Rollback
```

---

# 121. Phase 4 推荐开发顺序

## Step 1

实现：

```text
NodeEvidence
EdgeEvidence
```

从 Phase 3 Trace 聚合。

---

## Step 2

实现：

```text
CandidateDetector
```

只输出候选，不做任何修改。

---

## Step 3

实现：

```text
protected capability
sentinel scenario
```

保护逻辑。

---

## Step 4

实现：

```text
TopologyPatch
CandidateTopology
```

支持虚拟禁用 Edge。

---

## Step 5

实现：

```text
Counterfactual Fast Regression
```

---

## Step 6

实现：

```text
ProbeRunner
```

复用 Phase 3 的 `basefast`（定向 seed + ExpansionPlan）与 `free` 执行入口。

---

## Step 7

实现：

```text
Batch Candidate Builder
```

---

## Step 8

实现：

```text
Fast Validation Gate
```

包括：

```text
global
category
sentinel
```

---

## Step 9

实现：

```text
Slow Validation Gate
```

---

## Step 10

加入：

```text
Route Diversity Guard
```

---

## Step 11

实现：

```text
Optimization Set
Validation Set
Sentinel Set
```

---

## Step 12

实现：

```text
TopologyVersion
Commit
Rollback
```

---

## Step 13

最后实现：

```text
Optimization Report
CLI
```

---

# 122. Unit Test — Evidence

必须覆盖：

```text
opportunity=0 observed=0

high opportunity / zero observed

high opportunity / high observed

low opportunity

successful route support

node availability / selection
```

---

# 123. Unit Test — Candidate Detection

确认：

```text
高 opportunity + 低 usage
→ candidate

低 opportunity
→ insufficient evidence

protected capability
→ protected
```

---

# 124. Unit Test — Counterfactual

删除：

```text
A → B
```

如果仍存在：

```text
A → C → B
```

Fast Coverage：

```text
unchanged
```

Candidate 可以继续验证。

---

# 125. Counterfactual Failure Test

删除 Edge 后：

```text
Scenario X

COVERED
→
UNCOVERED
```

则：

```text
REJECTED
```

---

# 126. Sentinel Test

即使：

```text
Global coverage unchanged
```

只要：

```text
Sentinel Scenario
COVERED → UNCOVERED
```

必须：

```text
REJECTED
```

---

# 127. Batch Interaction Test

Edge A 单删安全。

Edge B 单删安全。

A+B：

```text
coverage drops
```

必须确认：

```text
Batch Validation
```

能够发现。

---

# 128. Probe Test

Candidate：

```text
opportunity low
```

经过 Directed Probe Trials（basefast 定向 seed）后：

```text
evidence updated
```

Candidate 状态从：

```text
PROBE_REQUIRED
```

转为：

```text
PROTECTED
```

或者：

```text
IDENTIFIED
```

---

# 129. Route Diversity Test

Before：

```text
3 successful routes
```

After：

```text
1 successful route
```

配置：

```text
min_successful_route_families = 2
```

Candidate：

```text
REJECTED
```

---

# 130. Version Test

确认：

```text
v1
↓
candidate v2
↓
accepted
↓
active v2
```

同时：

```text
v1
```

仍然可恢复。

---

# 131. Integration Test

建议准备：

```text
30~50 Tools
4 Layers
200~400 Edges

200+ Scenarios
1000+ Slow Trials
```

人为设计：

```text
Useful Edges
Redundant Edges
Rare Critical Edges
Unused But Viable Alternative Edges
Disconnected Candidates
```

Phase 4 必须：

```text
剪掉 Redundant Edge
保护 Critical Edge
识别 Router Bias Edge
保持 Fast Coverage
保持 Slow Success
```

---

# 132. Phase 4 Definition of Done

## Evidence

* [ ] Node Opportunity 可统计
* [ ] Node Usage 可统计
* [ ] Edge Opportunity 可统计
* [ ] Edge Usage 可统计
* [ ] Successful Route Support 可统计
* [ ] Scenario Support 可统计

## Candidate

* [ ] 支持 Edge Candidate
* [ ] 支持 Node Candidate
* [ ] 支持 Insufficient Evidence
* [ ] 支持 Probe Required
* [ ] 支持 Protected
* [ ] Candidate 具有明确 Reason

## Protection

* [ ] Unique Capability Provider 保护
* [ ] Sentinel Scenario 保护
* [ ] Critical Category 保护
* [ ] Bridge Node 保护
* [ ] Route Diversity Guard

## Counterfactual

* [ ] 可以虚拟禁用 Edge
* [ ] 可以虚拟禁用 Node
* [ ] 不修改 DeclaredTopology
* [ ] Counterfactual Fast Regression 可运行
* [ ] 可以检测 Capability Regression
* [ ] 可以检测 Topology Regression

## Probe

* [ ] 可以针对 Candidate 定向探索
* [ ] 可以复用 Phase 3 的 `basefast`（定向 seed + ExpansionPlan）做定向探测
* [ ] Probe 数据进入 Evidence
* [ ] 可以识别潜在 Router Bias

## Dataset

* [ ] Optimization Set
* [ ] Validation Set
* [ ] Sentinel Set
* [ ] Split 稳定
* [ ] Validation 数据不参与 Candidate Generation

## Validation

* [ ] Structural Validation
* [ ] Fast Regression Validation
* [ ] Category Guard
* [ ] Sentinel Guard
* [ ] Slow Regression Validation
* [ ] Success Guard
* [ ] Quality Guard
* [ ] Error Guard
* [ ] Route Diversity Guard

## Versioning

* [ ] DeclaredTopology 与 ActiveTopology 分离
* [ ] TopologyPatch 可序列化
* [ ] CandidateTopology 可生成
* [ ] Accepted Topology 创建新版本
* [ ] Rejected Candidate 不修改 ActiveTopology
* [ ] 支持 Rollback

## Boundary

* [ ] 不做 Route Ranking
* [ ] 不做 Cost Tier
* [ ] 不做 Performance Tier
* [ ] 不做线上负载均衡
* [ ] 不做 Retry
* [ ] 不做 Fallback
* [ ] 不物理删除 Tool
* [ ] 不修改 provider / worker 原始声明

---

# 133. Phase 4 最终验收场景

假设 Phase 1 初始化得到：

```text
50 Tools
4 Layers
420 Edges
```

经过 Phase 2：

```text
Business Coverage:
96.5%
```

经过 Phase 3：

```text
200 Scenarios
2000 Trials

Observed Edges:
137 / 420
```

其中大量 Edge：

```text
High Opportunity
Low / Zero Usage
```

Phase 4 应能够生成：

```text
CandidateTopology
```

例如：

```text
50 Tools
420 Edges
↓
47 Tools
235 Edges
```

并重新执行 Regression。

最终：

```text
Fast Coverage:
96.5% → 96.5%

Sentinel Coverage:
100% → 100%

Business Success:
93.1% → 93.3%

Quality:
No significant regression

Average Available Tools:
11.2 → 6.1

Router Tool Context:
显著下降

Successful Alternative Routes:
仍满足最低保留要求
```

然后生成：

```text
ActiveTopology vNext
```

---

# 134. Phase 4 最核心的验收问题

最终只问七个问题。

### 1.

系统是否能够区分：

```text
没有被使用
```

和：

```text
有大量机会但没有被使用
```

？

### 2.

对于低使用率 Edge，系统是否能够判断它是否承担：

```text
稀有业务
唯一能力
关键连接
备用 Route
```

？

### 3.

在证据不足时，系统是否会：

```text
Probe
```

而不是直接剪枝？

### 4.

每一个剪枝是否都能够回答：

```text
为什么它是 Candidate？
删除后理论能力有没有下降？
删除后真实业务成功率有没有下降？
```

？

### 5.

系统是否能够避免：

```text
为了减少 Edge
把所有替代 Route 都剪成唯一 Route
```

？

### 6.

CandidateTopology 失败后，是否完全不会污染：

```text
Current ActiveTopology
```

？

### 7.

整个过程最终是否得到：

```text
更小的 Agent Search Space
```

同时保持：

```text
Capability Coverage
Business Success
Critical Scenarios
Alternative Route Diversity
```

？

如果七个答案全部为：

```text
Yes
```

则 Phase 4 核心假设验证成功。

---

# 135. Phase 4 完成后的系统形态

完成 Phase 4 后，整个项目第一次形成完整闭环：

```text
Tool Declaration
        ↓
Dense Declared Topology
        ↓
Fast Regression
        ↓
Slow Regression
        ↓
Execution Evidence
        ↓
Topology Learning
        ↓
Safe Pruning
        ↓
Active Topology
        ↓
Regression Again
        └───────────────┐
                        ↓
                    Next Round
```

项目从：

```text
人工声明 Tool Graph
```

真正迈向：

```text
业务数据驱动的 Tool Topology Learning
```

---

# 136. Phase 4 完成后的下一步

Phase 5：

> **Route Evaluation, Ranking & Tiering**

Phase 4 已经回答：

```text
哪些搜索空间可以安全删除？
```

Phase 5 接下来回答：

```text
剩下的多条有效 Route 中：

哪些更快？
哪些更便宜？
哪些质量更高？
哪些更稳定？

同一个业务目标下，
有哪些 Route 是 Fast Tier？
有哪些是 Balanced Tier？
有哪些是 Quality Tier？
```

届时开始正式消费：

```text
Success
Quality
Latency
Token
Cost
```

形成：

```text
Route Profile
Route Ranking
Pareto Frontier
Route Tier
```

并为之后：

```text
Load Balancing
Dynamic Route Selection
```

准备数据。

Phase 4 的使命则始终保持：

> **不是找出唯一最佳路径，而是在业务能力不退化的前提下，让 Tool Topology 从“默认稠密”逐步收敛成“有真实证据支持的有效搜索空间”。**
