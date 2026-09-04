# Phase 3 — Slow Regression & Execution Exploration

## 0. 阶段定位

Phase 3 建立项目第一套真实 Tool 执行与路径探索机制：

> **Slow Regression**

与 Phase 2 的 Fast Regression 不同，本阶段允许：

* 实际调用 Tool；
* 实际调用 LLM Router；
* 实际访问测试数据库、RAG、HTTP API、MCP 等后端；
* 在 Topology 约束范围内由 Agent 自由选择 Tool；
* 同一 Layer 选择多个 Tool；
* 同一 Scenario 重复运行多个 Trial；
* 记录最终形成的真实 Route；
* 对最终业务结果进行 Evaluation；
* 收集 Node、Edge、Route 的真实使用数据；
* 收集 latency、token、cost 等原始指标。

Phase 3 的核心目标不是寻找“最佳 Route”，而是：

> **让 Agent 在当前 Tool Topology 中真实探索，并将执行过程中产生的路径、结果和性能数据转化为结构化 Trace Dataset。**

核心流程：

```text
Scenario
   ↓
Topology
   ↓
Layer Router
   ↓
Select Tool(s)
   ↓
Actual Tool Execution
   ↓
Execution State
   ↓
Next Layer
   ↓
...
   ↓
Final Result
   ↓
Evaluator
   ↓
Trial Trace
   ↓
Regression Dataset
```

---

# 1. Phase 3 核心问题

本阶段需要回答：

```text
面对真实业务 Scenario，

Agent 实际会选择哪些 Tool？

每一层会选择一个还是多个 Tool？

哪些 Node 经常被选择？

哪些 Edge 经常被经过？

同一个 Scenario 会形成多少种不同 Route？

这些 Route 实际是否完成业务目标？

Tool 真实调用是否存在错误？

Route 实际耗时是多少？

Route 实际 Token / Cost 是多少？
```

这些数据将成为 Phase 4：

```text
Topology Pruning
```

和 Phase 5：

```text
Route Ranking / Tiering
```

的基础。

---

# 2. Phase 3 非目标

本阶段明确不实现：

```text
Edge 自动剪枝
Tool 自动删除
Topology 自动修改
Route 最终排名
Route Tier
线上 Route Selection
负载均衡
Retry
Fallback
Circuit Breaker
复杂 Failure Recovery
Loop
Recursive Tool Call
跨层回跳
长期任务
Distributed Runtime
Human Approval
Production Traffic Routing
```

尤其需要明确：

> Phase 3 负责收集事实，不负责根据这些事实修改 Topology。

即：

```text
Observe
≠
Optimize
```

---

# 3. Phase 2 与 Phase 3 的关系

Phase 2：

```text
Scenario
↓
Tool Metadata
↓
Candidate Route
↓
Coverage
```

回答：

> Could this topology solve it?

Phase 3：

```text
Scenario
↓
Actual Tool Execution
↓
Observed Route
↓
Evaluation
```

回答：

> Did it actually solve it?

因此：

```text
Fast Regression
=
Theoretical Capability Coverage

Slow Regression
=
Empirical Execution Evaluation
```

---

# 4. Phase 3 不执行 Phase 2 的固定 Route

这是一个关键约束。

Phase 2 产生：

```text
CandidateRoute
```

但它不应该强制 Phase 3：

```text
必须执行 CandidateRoute A
```

Slow Regression 的主要价值就在于：

> Agent 可以在当前 Topology 允许的范围内自行选择工具组合。

否则 Slow Regression 只能验证人工生成的 Route，而无法发现：

```text
未知 Route
替代 Route
冗余 Route
组合 Route
```

---

# 5. Phase 2 Candidate Route 的定位

Phase 2 的 Candidate Route 可以作为：

```text
Search Hint
Debug Reference
Regression Seed
```

但不能成为唯一执行路线。

因此 Phase 3 可以支持：

```text
exploration_mode = free
```

或者：

```text
exploration_mode = guided
```

其中：

### free

Agent 只看到当前 Topology 中允许使用的节点。

### guided

Agent 可以看到 Phase 2 Candidate Route 作为参考，但仍允许偏离。

Phase 3 默认：

```text
free
```

---

# 6. Phase 3 的核心对象

本阶段新增核心对象：

```text
Trial
SlowRegressionRunner
LayerRouter
RoutingDecision

ExecutionContext
ExecutionState

LayerExecution
ToolExecution

ObservedRoute
ExecutionTrace

Evaluator
EvaluationResult

TrialResult

SlowRegressionReport
```

---

# 7. Trial

一个：

```text
Scenario
```

不等于一次 Regression。

同一个 Scenario 应允许执行：

```text
N Trials
```

例如：

```text
Scenario:

"帮我判断订单123能不能退款"

Trials:

20
```

可能得到：

```text
Route A    11 次
Route B     6 次
Route C     3 次
```

这才可以观察 Agent 的真实 Route 分布。

---

# 8. Trial 的定义

推荐：

```python
@dataclass(frozen=True)
class Trial:
    id: str

    scenario_id: str

    trial_index: int

    topology_version: str

    scenario_suite_version: str

    router_config_id: str
```

一次 Trial 必须是：

```text
独立
可追踪
可评估
```

的执行单元。

---

# 9. Trial Independence

同一 Scenario 的多个 Trial 必须尽量彼此独立。

例如：

```text
Trial 1:
refund_order("001")
```

如果真的修改了测试数据库，那么 Trial 2 再执行：

```text
refund_order("001")
```

可能得到：

```text
already refunded
```

这会污染 Regression。

因此 Phase 3 必须引入：

> **Fixture Isolation**

---

# 10. Regression Environment

Slow Regression 禁止默认直接针对生产环境运行。

建议定义：

```text
mock
sandbox
staging
```

Phase 3 默认推荐：

```text
sandbox
```

生产环境必须显式禁止：

```text
production regression
```

这是测试框架边界，不是完整 Policy Engine。

---

# 11. Fixture

Scenario 可以指定：

```text
fixture
```

例如：

```json
{
  "id": "refund_001",
  "query": "订单123能退款吗？",

  "slow_regression": {
    "fixture": "eligible_order",
    "trials": 20
  }
}
```

Fixture 可以准备：

```text
测试订单
测试用户
测试文档
Mock 服务
测试数据库状态
```

---

# 12. FixtureManager

定义：

```python
class FixtureManager(Protocol):

    async def setup(
        self,
        scenario: Scenario,
        trial: Trial,
    ) -> ExecutionInputs:
        ...

    async def reset(
        self,
        scenario: Scenario,
        trial: Trial,
    ) -> None:
        ...

    async def teardown(
        self,
        scenario: Scenario,
        trial: Trial,
    ) -> None:
        ...
```

典型 Trial：

```text
setup
 ↓
execute
 ↓
evaluate
 ↓
reset / teardown
```

---

# 13. Scenario Schema 扩展

Phase 3 继续使用 Phase 2 的：

```text
Scenario
ScenarioSuite
```

不得重新创建另一套业务输入格式。

Phase 3 只扩展可选字段：

```json
{
  "id": "refund_001",
  "query": "判断订单123能不能退款",
  "category": "refund",

  "expected_capabilities": [
    "order.read",
    "refund.policy.check"
  ],

  "slow_regression": {
    "trials": 20,
    "fixture": "eligible_order",

    "evaluation": {
      "type": "structured",
      "expected": {
        "refund_allowed": true
      }
    }
  }
}
```

---

# 14. Suite 级配置

允许：

```json
{
  "name": "customer_service",
  "version": "3.0",

  "slow_regression": {
    "default_trials": 10,
    "environment": "sandbox"
  },

  "scenarios": []
}
```

Scenario 级配置覆盖 Suite 默认配置。

---

# 15. SlowRegressionRunner

核心入口：

```python
class SlowRegressionRunner:

    async def run(
        self,
        suite: ScenarioSuite,
        topology: ToolTopology,
    ) -> SlowRegressionReport:
        ...
```

内部：

```text
Scenario
 ↓
N Trials
 ↓
Fixture Setup
 ↓
Runtime Execute
 ↓
Evaluator
 ↓
TrialResult
 ↓
Aggregate
```

---

# 16. Layer-by-Layer Routing

Phase 3 不采用：

```text
先生成整条 DAG
再执行
```

而采用：

> **Layer-by-Layer Dynamic Routing**

例如：

```text
Read Layer
 ↓
执行结果
 ↓
Analyze Layer Router
 ↓
执行结果
 ↓
Action Layer Router
```

Agent 可以根据上一层实际结果改变下一层 Tool 选择。

---

# 17. 为什么不能提前生成完整 Route

例如 Scenario：

```text
检查用户订单问题
```

Read Layer：

```text
DB
```

执行后发现：

```text
order not found
```

此时 Analyze / Action 路径可能和：

```text
order found
```

完全不同。

因此 Route 应该：

```text
逐层形成
```

而不是完全提前确定。

---

# 18. LayerRouter

引入：

```python
class LayerRouter(Protocol):

    async def route(
        self,
        context: RoutingContext,
    ) -> RoutingDecision:
        ...
```

LayerRouter 一般由：

```text
LLM
```

实现。

---

# 19. RoutingContext

推荐：

```python
@dataclass
class RoutingContext:

    query: str

    current_layer: str

    available_tools:
        tuple[ToolSummary, ...]

    state_summary: Any

    previous_layers:
        tuple[LayerExecution, ...]

    topology_version: str
```

---

# 20. Agent 不应该看到全部 Tool

这是本项目的重要目标之一。

在某个 Layer：

```text
Analyze
```

Router 只能看到当前从上一层可到达的：

```text
available_tools
```

而不是整个 Registry。

例如完整系统有：

```text
100 Tools
```

当前层经过 Topology Filtering 后只剩：

```text
6 Tools
```

则 LLM 只获得这 6 个 Tool。

---

# 21. Available Tool 计算

假设上一层执行：

```text
{DB, RAG}
```

下一层 Candidate：

```text
PolicyCheck
RiskCheck
Summarizer
Classifier
```

可用节点必须满足：

```text
Tool enabled
AND
Edge enabled
AND
provider / worker allowed
AND
runtime input satisfiable
```

---

# 22. 多上游节点的 Reachability

上一层：

```text
P = {DB, RAG}
```

下一层节点：

```text
B
```

只要存在：

```text
DB → B
```

或者：

```text
RAG → B
```

则 B 在拓扑上：

```text
reachable
```

即采用：

```text
OR Reachability
```

---

# 23. Schema 仍然负责 Runtime Validation

Topology 负责回答：

```text
允许不允许走这条边？
```

Schema 负责回答：

```text
实际输入够不够？
```

因此：

```text
Topology Reachable
```

并不一定：

```text
Runtime Callable
```

如果 B 需要：

```text
Order
RefundPolicy
```

而 State 只有：

```text
Order
```

则 B 当前不可调用。

---

# 24. RoutingDecision

推荐：

```python
@dataclass(frozen=True)
class RoutingDecision:

    action: RoutingAction

    selected_tools:
        tuple[str, ...]

    reason: str | None = None
```

其中：

```python
class RoutingAction(str, Enum):
    EXECUTE = "execute"
    FINISH = "finish"
```

---

# 25. Router 可以在任意 Layer FINISH

不是所有 Scenario 都必须走完整 Layer。

例如：

```text
用户：
查询订单状态
```

可能：

```text
Read Layer
DB
↓
FINISH
```

无需进入：

```text
Analyze
Action
```

所以 Agent 可以主动返回：

```text
FINISH
```

---

# 26. 禁止跨层回跳

Phase 3 执行顺序必须单调：

```text
L1
↓
L2
↓
L3
↓
...
```

禁止：

```text
L3
↓
L2
```

也禁止：

```text
L2
↺
L2
```

因此本阶段天然无：

```text
Loop
Recursion
```

---

# 27. Layer Skip

默认不允许：

```text
L1
↓
L3
```

除非未来 Topology 明确支持跨层 Edge。

Phase 3 MVP 继续遵守 Phase 1：

> ToolEdge 只存在于相邻层。

Agent 可以：

```text
提前 FINISH
```

但不能跳过一个中间层继续访问更后面的 Layer。

---

# 28. 一个 Layer 可以选择多个 Tool

这是 Phase 3 的关键能力。

例如：

```text
Read Layer

DB
RAG
WebSearch
```

Router 可以选择：

```text
{DB, RAG}
```

而不是只能：

```text
DB
```

这样可以表达：

```text
多来源信息获取
并行查询
信息交叉验证
```

---

# 29. 同层节点执行语义

Phase 3 规定：

> 同一个 Layer 被同时选中的 Tool 是 sibling nodes。

它们之间没有：

```text
intra-layer dependency
```

因此：

```text
DB
RAG
```

可以默认并行执行。

推荐：

```python
await asyncio.gather(...)
```

---

# 30. 同层 Tool 不允许依赖同层 Tool 输出

例如：

```text
A
↓
B
```

如果 A 和 B 都位于同一个 Layer，则 Phase 3 不支持。

应：

```text
拆成两个 Layer
```

或者未来增加：

```text
sub-layer
```

当前不做。

---

# 31. max_tools_per_layer

必须提供限制：

```text
max_tools_per_layer
```

防止 Agent：

```text
把整层 Tool 全调用
```

例如：

```python
max_tools_per_layer = 3
```

超过则：

```text
InvalidRoutingDecisionError
```

---

# 32. Tool Selection 必须属于 Available Tools

Router 返回：

```text
["db", "unknown_tool"]
```

其中：

```text
unknown_tool
```

不在当前可用集合。

Runtime 不允许执行。

抛：

```text
InvalidRoutingDecisionError
```

---

# 33. Router 不直接调用 Tool

必须严格分离：

```text
LayerRouter
→ Selection
```

和：

```text
Runtime
→ Execution
```

禁止 Router 自己：

```text
call_tool()
```

这样才可以完整记录：

```text
Routing Decision
```

和：

```text
Tool Execution
```

---

# 34. ExecutionState

Phase 3 建立跨 Layer 的共享状态：

```python
class ExecutionState:
    ...
```

State 至少包括：

```text
Original Query
Scenario Inputs
Tool Outputs
Intermediate Results
Final Response
```

---

# 35. Blackboard 模式

可以将 State 理解为：

```text
Blackboard
```

例如：

```text
Query

"订单123是否可以退款"

↓

State:

order = ...
refund_policy = ...
analysis = ...
refund_result = ...
```

每一 Layer：

```text
读取 State
↓
执行 Tool
↓
更新 State
```

---

# 36. Tool Output 必须带来源

不能简单：

```python
state["order"] = result
```

建议至少保留：

```text
value
source_tool
layer
timestamp
```

例如：

```python
ArtifactValue(
    value=order,
    source_tool="db",
    layer="read",
)
```

为 Trace 和 Debug 提供依据。

---

# 37. 同名输出冲突

如果：

```text
DB
```

和：

```text
ERP
```

同时产生：

```text
order
```

不能后写覆盖前写。

State 必须允许：

```text
Multi-source Artifact
```

例如：

```text
order:
  - source=db
  - source=erp
```

否则无法支持同层多个 Tool。

---

# 38. ToolExecution

每次真实 Tool 调用必须记录：

```python
@dataclass
class ToolExecution:

    tool_name: str
    layer: str

    started_at: datetime
    ended_at: datetime

    status: ToolExecutionStatus

    input_summary: Any
    output_summary: Any

    latency_ms: float

    token_usage: TokenUsage | None
    cost: float | None

    error: ExecutionError | None
```

---

# 39. ToolExecutionStatus

建议：

```text
SUCCESS
ERROR
```

Phase 3 不需要：

```text
RETRYING
FALLBACK
CIRCUIT_OPEN
```

因为这些机制当前明确不做。

---

# 40. Tool Failure

Tool 调用失败：

```text
ToolExecution.status = ERROR
```

记录错误。

框架：

```text
不自动 retry
不自动 fallback
不自动切换 provider
```

---

# 41. Partial Layer Failure

如果某层选择：

```text
DB
RAG
```

结果：

```text
DB SUCCESS
RAG ERROR
```

允许保留：

```text
DB output
```

继续执行。

即：

```text
一个 sibling Tool 失败
≠
整个 Trial 立即失败
```

是否最终完成业务由后面的：

```text
Evaluator
```

判断。

---

# 42. Entire Layer Failure

如果当前选择：

```text
DB
RAG
```

全部：

```text
ERROR
```

则默认：

```text
Trial Execution Failed
```

不继续进入下一层。

---

# 43. 不做同 Trial 自动替代

例如：

```text
DB failed
```

系统不会自动：

```text
改用 ERP
```

这属于：

```text
fallback
```

当前不做。

但是另外一个 Trial 中 Agent 自己选择：

```text
ERP
```

属于正常 Route Exploration。

这两个概念必须区分。

---

# 44. LayerExecution

一个 Layer 的完整记录：

```python
@dataclass
class LayerExecution:

    layer: str

    available_tools:
        tuple[str, ...]

    selected_tools:
        tuple[str, ...]

    routing_decision:
        RoutingDecision

    tool_executions:
        tuple[ToolExecution, ...]

    started_at: datetime
    ended_at: datetime
```

---

# 45. ExecutionTrace

每一个 Trial 都产生：

```python
@dataclass
class ExecutionTrace:

    trial_id: str
    scenario_id: str

    topology_version: str

    layers:
        tuple[LayerExecution, ...]

    started_at: datetime
    ended_at: datetime
```

ExecutionTrace 是 Phase 3 最重要的产物之一。

---

# 46. ObservedRoute

ExecutionTrace 可以抽取：

```text
ObservedRoute
```

例如：

```text
Read:
    DB
    RAG

Analyze:
    PolicyCheck

Action:
    Refund
```

---

# 47. CandidateRoute 和 ObservedRoute 的区别

Phase 2：

```text
CandidateRoute
```

代表：

> 理论上可能。

Phase 3：

```text
ObservedRoute
```

代表：

> Agent 在一次真实 Trial 中确实走过。

这两个 ID 不得混用。

---

# 48. Observed Route Fingerprint

需要生成稳定：

```text
route_id
```

例如：

```text
read:[db,rag]
analyze:[policy_check]
action:[refund]
```

规范化后：

```text
hash
```

得到：

```text
route_id
```

---

# 49. Layer 内工具集合标准化

如果同层 Tool 是并行 sibling：

```text
{DB, RAG}
```

那么：

```text
DB + RAG
```

和：

```text
RAG + DB
```

应属于同一 Route。

因此 route fingerprint 中：

```text
Layer 内 Tool Name ASC
```

---

# 50. Route 不包含执行结果

以下两个 Trial：

```text
DB → PolicyCheck → Refund
```

即使一个成功，一个失败：

```text
route_id
```

仍然相同。

Route 表示：

```text
结构
```

Evaluation 表示：

```text
结果
```

两者必须分开。

---

# 51. Edge Observation

ExecutionTrace 必须能够推导实际经过的 Edge。

例如：

```text
Read:
{DB, RAG}

Analyze:
{PolicyCheck}
```

如果 Topology 有：

```text
DB → PolicyCheck
RAG → PolicyCheck
```

则这两个 Edge 都被视为本 Trial：

```text
observed
```

---

# 52. Node Usage

每次 Tool 被真实选择：

```text
node_usage += 1
```

Phase 3 收集：

```text
NodeUsageStats
```

例如：

```text
DB             812
RAG            603
WebSearch       31
RefundCheck    214
```

---

# 53. Edge Usage

每条真实传播 Edge：

```text
edge_usage += 1
```

例如：

```text
DB → RefundCheck        189
RAG → RefundCheck       145
WebSearch → RefundCheck   2
```

这些只是：

> Observed Statistics

不是：

> Pruning Decision

---

# 54. Route Usage

每个 route_id：

```text
route_usage += 1
```

例如：

```text
Route A    412
Route B    183
Route C     21
```

Phase 3 允许统计次数。

但不在本阶段宣布：

```text
Route A > Route B
```

---

# 55. 为什么不能根据次数立即剪枝

例如：

```text
Edge X
usage = 0
```

并不能说明：

```text
Edge X useless
```

可能只是：

```text
Scenario 没覆盖
Trial 数量不足
LLM 没探索到
Router 有偏好
```

所以 Phase 3 只记录：

```text
usage = 0
```

Phase 4 才判断：

```text
是否成为 pruning candidate
```

---

# 56. Exploration 是 Phase 3 的核心

如果 Agent 每次：

```text
temperature = 0
```

并且总选择：

```text
同一 Route
```

就无法发现替代 Route。

因此 Slow Regression 必须允许：

```text
Exploration Configuration
```

---

# 57. RouterConfig

推荐：

```python
@dataclass(frozen=True)
class RouterConfig:

    model: str

    temperature: float

    max_tools_per_layer: int

    exploration_mode: str

    prompt_version: str
```

---

# 58. Exploration Mode

Phase 3 MVP 推荐三个模式：

```text
free
guided
replay
```

---

# 59. free

```text
free
```

用于正常 Slow Regression。

Router 看到：

```text
当前可用 Tool
+
State
+
Query
```

自主选择。

这是最重要的模式。

---

# 60. guided

```text
guided
```

向 Router 提供：

```text
Phase 2 Candidate Routes
```

作为：

```text
possible references
```

但允许偏离。

可用于：

```text
验证 Fast Regression 发现的路径是否真实可用
```

---

# 61. replay

```text
replay
```

不进行自由 Router。

直接重复执行一个：

```text
ObservedRoute / CandidateRoute
```

主要用于：

```text
Debug
Reproduction
Route Stability Test
```

不作为 Phase 3 默认路径发现机制。

---

# 62. Router Prompt 的核心约束

Router 应理解：

```text
当前所在 Layer
当前业务目标
当前已有执行结果
当前允许使用的 Tool
```

并做：

```text
选择 1~N Tool
```

或者：

```text
FINISH
```

---

# 63. Router 不负责最终业务评判

Router 不能自己决定：

```text
这次 Trial 成功了
```

它只能决定：

```text
继续选择 Tool
```

或者：

```text
停止
```

最终业务效果由：

```text
Evaluator
```

判断。

---

# 64. Final Result

Router 返回：

```text
FINISH
```

时，Runtime 必须生成：

```text
FinalResult
```

例如：

```python
@dataclass
class FinalResult:

    response: Any

    state_snapshot: Any
```

Evaluator 消费：

```text
FinalResult
+
Scenario
+
必要的 sandbox state
```

---

# 65. Evaluator

定义：

```python
class Evaluator(Protocol):

    async def evaluate(
        self,
        scenario: Scenario,
        result: FinalResult,
        trace: ExecutionTrace,
    ) -> EvaluationResult:
        ...
```

---

# 66. EvaluationResult

推荐：

```python
@dataclass
class EvaluationResult:

    success: bool

    quality_score: float | None

    criteria:
        tuple[CriterionResult, ...]

    reason: str | None
```

其中：

```text
quality_score ∈ [0, 1]
```

---

# 67. Success 和 Quality 必须分开

例如：

```text
Route A
```

成功退款：

```text
success = true
```

但是响应信息非常差：

```text
quality = 0.62
```

另一个：

```text
Route B
```

同样成功：

```text
success = true
quality = 0.94
```

Phase 5 才会利用这些指标进行 Route 分级。

---

# 68. Evaluator 类型

Phase 3 推荐至少支持：

```text
StructuredEvaluator
LLMJudgeEvaluator
CompositeEvaluator
```

---

# 69. StructuredEvaluator

优先使用确定性检查。

例如：

```text
expected:

refund_allowed = true
```

实际：

```text
refund_allowed = true
```

则：

```text
PASS
```

这种 Evaluator 最可靠。

---

# 70. Side-effect Evaluation

对于：

```text
Action Tool
```

不能只检查 Agent 自己说：

```text
"退款成功"
```

应该检查 Sandbox 实际状态。

例如：

```text
database.order.status == refunded
```

这才能确认：

```text
Business Success
```

---

# 71. LLMJudgeEvaluator

适用于：

```text
自然语言答案
总结质量
信息完整性
语义正确性
```

例如：

```text
用户问题是否被完整回答？
```

可以由 Judge Model 给：

```text
success
quality_score
reason
```

---

# 72. Regression 不允许只使用 LLM Judge

对于存在确定性结果的场景：

```text
refund
order update
email sent
record create
```

优先：

```text
Structured Assertion
```

LLM Judge 只用于无法精确断言的部分。

---

# 73. CompositeEvaluator

允许：

```text
Business State
+
Answer Quality
```

组合。

例如：

```text
refund_result == success          70%
answer completeness              30%
```

Phase 3 可以生成：

```text
quality_score
```

但不进行 Route 排名。

---

# 74. Evaluation Error

Evaluator 自己失败：

```text
EvaluationError
```

不能把它误认为：

```text
Business Failure
```

需要区分：

```text
execution failed
evaluation failed
business failed
```

---

# 75. TrialResult

推荐：

```python
@dataclass
class TrialResult:

    trial: Trial

    execution_status:
        TrialExecutionStatus

    route:
        ObservedRoute | None

    trace:
        ExecutionTrace

    evaluation:
        EvaluationResult | None

    latency_ms: float

    token_usage:
        TokenUsage

    cost:
        float | None
```

---

# 76. TrialExecutionStatus

建议：

```text
COMPLETED

ROUTING_ERROR
TOOL_ERROR
LAYER_ERROR
EVALUATION_ERROR
FIXTURE_ERROR
```

其中：

```text
COMPLETED
```

不等于：

```text
Business Success
```

例如：

```text
execution_status = COMPLETED
evaluation.success = false
```

是完全合法的。

---

# 77. Latency Collection

Phase 3 开始收集真实：

```text
Tool Latency
Layer Latency
Trial Latency
```

但只作为：

```text
Raw Metrics
```

不做：

```text
fast / slow tier
```

Phase 5 再做分级。

---

# 78. Token Collection

对于 LLM：

```text
Router
Tool
Evaluator
```

如果可以获取：

```text
input_tokens
output_tokens
```

必须记录。

例如：

```python
@dataclass
class TokenUsage:

    input_tokens: int = 0
    output_tokens: int = 0
```

---

# 79. Cost Collection

Tool / LLM Adapter 如果能提供：

```text
cost
```

Phase 3 应记录真实：

```text
USD / Trial
```

例如：

```text
route cost =

routing cost
+
tool model cost
+
evaluation cost
```

但需要进一步区分：

```text
execution_cost
```

和：

```text
evaluation_cost
```

---

# 80. 为什么 Evaluation Cost 要单独记录

LLM Judge 是：

```text
测试成本
```

不是未来生产 Route 的：

```text
执行成本
```

因此：

```text
production_route_cost
```

不能包含：

```text
regression judge cost
```

建议记录：

```text
routing_cost
tool_cost
evaluation_cost
total_regression_cost
```

---

# 81. SlowRegressionReport

一轮 Slow Regression 最终生成：

```python
@dataclass
class SlowRegressionReport:

    suite_name: str
    suite_version: str

    topology_version: str
    router_config_id: str

    scenario_count: int
    trial_count: int

    results:
        tuple[TrialResult, ...]

    node_stats:
        dict[str, NodeObservationStats]

    edge_stats:
        dict[str, EdgeObservationStats]

    route_stats:
        dict[str, RouteObservationStats]
```

---

# 82. NodeObservationStats

只记录事实：

```text
selected_count
success_trial_count
failed_trial_count
```

暂时不生成：

```text
importance_score
```

---

# 83. EdgeObservationStats

记录：

```text
observed_count
successful_trial_count
failed_trial_count
```

不产生：

```text
prune = true
```

---

# 84. RouteObservationStats

记录：

```text
usage_count

completed_count
business_success_count
business_failure_count

latencies
costs
quality_scores
```

Phase 3 可以计算基础统计：

```text
mean
median
p95
```

但不产生最终 Route 排名。

---

# 85. Scenario-level Route Distribution

对于每个 Scenario：

```text
refund_001

Route A     12
Route B      5
Route C      3
```

必须保留这种分布。

这是未来判断：

```text
Agent Routing Stability
```

的重要数据。

---

# 86. Route Diversity

可以记录：

```text
unique_route_count
```

例如：

```text
20 Trials
6 Unique Routes
```

但 Phase 3 不定义：

```text
越多越好
```

或者：

```text
越少越好
```

只记录。

---

# 87. Route 和 Scenario 必须绑定

同一个：

```text
route_id
```

可能在多个 Scenario 出现。

因此统计至少支持：

```text
Global Route Stats
```

和：

```text
Scenario Route Stats
```

---

# 88. Trace 必须完整保留 Routing Decision

不能只记录：

```text
调用了哪些 Tool
```

还需要记录：

```text
当时有哪些 Tool 可以选
最终选了哪些
```

例如：

```text
Available:

DB
RAG
WebSearch

Selected:

DB
RAG
```

否则 Phase 4 无法判断：

```text
WebSearch 没被使用
```

到底是：

```text
不可达
```

还是：

```text
Agent 主动没选
```

---

# 89. Available / Selected 区分非常关键

Phase 3 必须同时统计：

```text
available_count
```

和：

```text
selected_count
```

例如：

```text
WebSearch

available = 1000
selected = 2
```

和：

```text
WebSearch

available = 2
selected = 2
```

含义完全不同。

这将成为 Phase 4 剪枝的重要依据。

---

# 90. Edge 也需要 Opportunity Count

同理，Edge 应记录：

```text
opportunity_count
observed_count
```

例如：

```text
DB → PolicyCheck

opportunity = 500
observed = 460
```

和：

```text
WebSearch → PolicyCheck

opportunity = 3
observed = 0
```

不能简单都判断：

```text
unused
```

---

# 91. Phase 3 最重要的数据概念

因此 Phase 3 不能只统计：

```text
Used
```

还必须统计：

```text
Could Have Been Used
```

即：

```text
Selection Opportunity
```

未来剪枝真正需要的是：

```text
usage / opportunity
```

而不只是：

```text
usage
```

---

# 92. Selection Event

可以引入：

```python
@dataclass
class SelectionEvent:

    layer: str

    available_tools:
        tuple[str, ...]

    selected_tools:
        tuple[str, ...]
```

这是最原始的探索数据。

---

# 93. Topology Snapshot

每一次 Slow Regression 必须绑定：

```text
Topology Version
```

必要时保存：

```text
Topology Snapshot Hash
```

否则未来无法知道：

```text
某条 Route 当时究竟是在什么搜索空间中产生的。
```

---

# 94. Model Configuration Snapshot

Regression Report 必须记录：

```text
Router Model
Temperature
Prompt Version
Tool Description Version
```

否则：

```text
Route Distribution
```

变化可能并不是 Topology 变化导致的。

---

# 95. Tool Metadata Version

如果 Tool Description 被修改：

```text
"查询数据库"
```

变成：

```text
"根据订单ID查询完整订单详情"
```

LLM Router 的选择概率可能改变。

因此最好记录：

```text
tool_registry_hash
```

或：

```text
tool_metadata_version
```

---

# 96. Determinism 不再是 Phase 3 目标

Phase 2 Gold Regression 追求：

```text
deterministic
```

Phase 3 则允许：

```text
stochastic routing
```

因为我们正是需要观察：

```text
Route Diversity
```

但是：

> 所有随机和模型配置必须被记录。

---

# 97. 可复现性

不能保证完全复现 LLM 输出。

但必须保证我们知道：

```text
Scenario Version
Topology Version
Tool Version
Router Model
Prompt Version
Temperature
Trial ID
```

这样才具有工程可追溯性。

---

# 98. Runner 并发

Scenario 和 Trial 数量未来可能很大。

例如：

```text
500 Scenarios
×
10 Trials
=
5000 Trials
```

Phase 3 Runner 应支持：

```text
trial concurrency
```

但必须配置：

```text
max_concurrency
```

---

# 99. 同一 Trial 内并发

同一个 Layer 中选中：

```text
DB
RAG
WebSearch
```

默认并发。

不同 Layer：

```text
sequential
```

因此：

```text
Layer 1
parallel
↓
Layer 2
parallel
↓
Layer 3
parallel
```

这和项目最初的分层网络抽象保持一致。

---

# 100. Trial 间并发与 Fixture

如果 Trial 使用共享 Sandbox 数据：

```text
并发执行
```

可能发生数据污染。

因此 FixtureManager 必须明确：

```text
isolation mode
```

Phase 3 MVP 可以：

```text
默认 Scenario 内 Trial 串行
Scenario 间允许并发
```

或者由 Fixture 显式声明安全并发。

---

# 101. Error Model

新增异常建议：

```text
SlowRegressionError

FixtureSetupError
FixtureResetError

RoutingError
InvalidRoutingDecisionError

ToolExecutionError
LayerExecutionError

EvaluationError

TraceSerializationError
```

---

# 102. Tool Error 不向 Router 隐藏

如果某个 sibling Tool：

```text
ERROR
```

下一层 Router 应能看到：

```text
tool failed
```

但是不要默认把完整 Stack Trace 塞进 LLM Context。

应提供：

```text
sanitized error summary
```

---

# 103. Sensitive Data

Execution Trace 默认禁止记录：

```text
Credentials
API Key
Authorization Header
Database Password
```

Tool Input / Output 建议提供：

```text
summary / sanitized snapshot
```

而不是无条件序列化全部对象。

---

# 104. Trace Storage

Phase 3 MVP 可以先写：

```text
JSONL
```

例如：

```text
slow_regression_traces.jsonl
```

一行一个：

```text
TrialResult
```

这非常适合后续：

```text
统计
重放
离线分析
```

---

# 105. 推荐输出文件

一次 Regression：

```text
artifacts/
└── slow_regression/
    └── run_20260904_001/
        ├── manifest.json
        ├── report.json
        ├── traces.jsonl
        ├── node_stats.json
        ├── edge_stats.json
        └── route_stats.json
```

---

# 106. manifest.json

记录：

```text
suite version
topology version
tool metadata version
router config
evaluator config
start time
end time
trial count
```

---

# 107. Phase 3 CLI

建议：

```bash
tool-topology regression slow \
    --topology topology.json \
    --scenario scenarios/customer_service.json \
    --trials 10 \
    --environment sandbox
```

输出：

```text
Slow Regression

Suite: customer_service_v3
Topology: v0.3.1

Scenarios: 100
Trials:    1000

Completed:         941
Execution Failed:   37
Evaluation Error:   22

Business Success:
812 / 941

Unique Routes:
47

Observed Nodes:
31 / 42

Observed Edges:
96 / 218
```

---

# 108. CLI 不输出 Pruning 建议

Phase 3 可以输出：

```text
Unused Edges: 122
```

但是不能输出：

```text
Recommend pruning 122 edges
```

因为是否应该剪枝属于 Phase 4。

---

# 109. Integration Demo

建议继续沿用退款业务 Demo，但扩展 Tool。

Topology：

```text
Layer 1 — Read

OrderDB
ERP
RAG
WebSearch


Layer 2 — Analyze

RefundPolicyCheck
RiskCheck
OrderSummarizer


Layer 3 — Action

RefundAPI
SendEmail
CreateTicket
```

---

# 110. Scenario 示例

```text
Scenario A

"订单123满足退款条件的话帮我退款"
```

可能形成：

```text
Route A

OrderDB + RAG
↓
RefundPolicyCheck
↓
RefundAPI
```

另一次 Trial：

```text
Route B

ERP
↓
RefundPolicyCheck + RiskCheck
↓
RefundAPI
```

另一次：

```text
Route C

OrderDB
↓
RefundPolicyCheck
↓
RefundAPI
```

这正是 Slow Regression 要捕获的数据。

---

# 111. Demo 验收

运行：

```text
100 Trials
```

应能够得到：

```text
多个 route_id
每个 route 的 usage
每条 edge 的 opportunity
每条 edge 的 observed usage
每个 node 的 availability
每个 node 的 selection
每个 trial 的业务成功结果
每个 trial 的 latency
每个 trial 的 token / cost
```

---

# 112. Unit Test — LayerRouter Contract

至少测试：

```text
只允许选择 available tool

不能超过 max_tools_per_layer

允许选择多个 tool

允许 FINISH

非法 tool 被拒绝
```

---

# 113. Unit Test — Topology Filtering

至少：

```text
respect enabled node
respect enabled edge
respect provider
respect worker
respect previous selected nodes
respect schema availability
```

---

# 114. Unit Test — Multi-tool Layer

验证：

```text
DB + RAG
```

同时执行。

State 中同时保留：

```text
DB result
RAG result
```

不存在覆盖。

---

# 115. Unit Test — Partial Failure

选择：

```text
A + B
```

其中：

```text
A SUCCESS
B ERROR
```

确认：

```text
A 输出保留
B Error 被 Trace 记录
Trial 可以继续
```

---

# 116. Unit Test — Entire Layer Failure

```text
A ERROR
B ERROR
```

确认：

```text
Trial 停止
execution_status = TOOL_ERROR / LAYER_ERROR
```

---

# 117. Unit Test — No Retry

Tool：

```text
第一次调用失败
第二次调用成功
```

Phase 3 必须确认：

```text
只调用一次
```

---

# 118. Unit Test — Trace

必须正确记录：

```text
available tools
selected tools
tool output
tool error
latency
layer
route
edge
```

---

# 119. Unit Test — Route Fingerprint

以下：

```text
Read:
DB + RAG
```

和：

```text
Read:
RAG + DB
```

得到相同：

```text
route_id
```

---

# 120. Unit Test — Evaluator

至少：

```text
structured success
structured failure
LLM judge fake result
composite result
evaluation error
```

测试不得依赖真实 LLM API。

---

# 121. Unit Test — Fixture Isolation

连续两个 Trial：

```text
Trial 1 修改状态
```

Trial 2 开始前必须恢复：

```text
initial fixture state
```

---

# 122. Integration Test

至少创建：

```text
10~20 Tools
3 Layers
20~50 Scenarios
5 Trials / Scenario
```

总计：

```text
100~250 Trials
```

实际运行 Fake / Sandbox Tools。

验证：

```text
multiple observed routes
node usage
edge usage
opportunity stats
evaluation results
latency stats
route fingerprints
trace persistence
```

---

# 123. 推荐目录结构

Phase 3 在当前项目增加：

```text
src/
└── tool_topology/
    │
    ├── routing/
    │   ├── models.py
    │   ├── router.py
    │   ├── filtering.py
    │   └── prompts.py
    │
    ├── runtime/
    │   ├── context.py
    │   ├── state.py
    │   ├── executor.py
    │   └── tool_executor.py
    │
    ├── regression/
    │   ├── slow_runner.py
    │   ├── trial.py
    │   ├── trace.py
    │   ├── route.py
    │   ├── stats.py
    │   └── report.py
    │
    ├── evaluation/
    │   ├── base.py
    │   ├── structured.py
    │   ├── llm_judge.py
    │   └── composite.py
    │
    └── fixtures/
        ├── base.py
        └── manager.py
```

---

# 124. 模块职责

## LayerRouter

只负责：

```text
当前层选什么 Tool
```

---

## TopologyFilter

只负责：

```text
哪些 Tool 当前允许被选
```

---

## Runtime

只负责：

```text
执行选择
传播 State
推进 Layer
```

---

## Evaluator

只负责：

```text
最终业务效果如何
```

---

## SlowRegressionRunner

负责：

```text
Scenario × Trial 编排
```

---

## Stats Aggregator

只负责：

```text
从 Trace 计算 Observation Statistics
```

不得剪枝。

---

# 125. Phase 3 开发顺序

建议严格按照以下顺序。

## Step 1

实现：

```text
Trial
ExecutionContext
ExecutionState
```

---

## Step 2

实现：

```text
TopologyFilter
```

完成：

```text
previous layer
+
provider / worker
+
state
→
available tools
```

---

## Step 3

实现：

```text
LayerRouter Protocol
FakeRouter
```

先完全不接 LLM。

---

## Step 4

实现：

```text
single-tool layer execution
```

---

## Step 5

升级为：

```text
multi-tool concurrent layer execution
```

---

## Step 6

实现：

```text
LayerExecution
ToolExecution
ExecutionTrace
```

---

## Step 7

实现：

```text
ObservedRoute
Route Fingerprint
```

---

## Step 8

实现：

```text
StructuredEvaluator
```

先跑确定性业务场景。

---

## Step 9

实现：

```text
FixtureManager
```

保证 Trial Isolation。

---

## Step 10

实现：

```text
SlowRegressionRunner
```

支持：

```text
Scenario
×
N Trials
```

---

## Step 11

实现：

```text
Node Observation
Edge Observation
Route Observation
Opportunity Count
```

---

## Step 12

接入：

```text
LLM LayerRouter
```

---

## Step 13

最后增加：

```text
LLMJudgeEvaluator
```

---

## Step 14

完成：

```text
JSONL Trace Persistence
CLI
Integration Demo
```

---

# 126. Phase 3 最核心的架构约束

## Rule 1

Agent 不获得完整 Tool Registry。

只获得：

```text
当前 Layer 可达节点。
```

---

## Rule 2

Topology 决定：

```text
搜索空间
```

Agent 决定：

```text
搜索空间中的实际选择
```

---

## Rule 3

一次 Route 不是提前固定的。

Route 由：

```text
每一层实际 Routing Decision
```

逐步形成。

---

## Rule 4

每层允许：

```text
1~N Tool
```

并允许：

```text
FINISH
```

---

## Rule 5

同层 Tool：

```text
Sibling Nodes
```

默认无依赖，可并行。

---

## Rule 6

执行只能：

```text
向下一层传播
```

禁止：

```text
循环
递归
回跳
```

---

## Rule 7

Tool 调用失败：

```text
记录
```

而不是：

```text
自动 Retry / Fallback
```

---

## Rule 8

Execution Success 与 Business Success 分离。

---

## Rule 9

Slow Regression 必须记录：

```text
available
```

和：

```text
selected
```

而不只是：

```text
selected
```

---

## Rule 10

Phase 3 产生：

```text
Observation
```

但不产生：

```text
Optimization Decision
```

---

# 127. Phase 3 最重要的统计关系

对于 Tool：

```text
Selection Rate
=
selected_count
/
available_count
```

对于 Edge：

```text
Edge Usage Rate
=
observed_count
/
opportunity_count
```

对于 Route：

```text
Route Frequency
=
route_trial_count
/
scenario_trial_count
```

这些指标 Phase 3 可以计算。

但是：

> 不允许仅凭这些指标自动修改 Topology。

---

# 128. Phase 3 Definition of Done

## Runtime

* [ ] 支持逐 Layer 执行
* [ ] Router 只看到当前可达 Tool
* [ ] 支持每层多个 Tool
* [ ] 同层 Tool 可并行
* [ ] 支持提前 FINISH
* [ ] 不支持 Loop
* [ ] 不支持递归
* [ ] 不支持跨层回跳
* [ ] 不自动 Retry
* [ ] 不自动 Fallback

## State

* [ ] Tool Output 可以向后传播
* [ ] 支持多来源 Output
* [ ] Output 保留来源信息
* [ ] Tool Error 可以向后续 Router 暴露摘要

## Trial

* [ ] 一个 Scenario 可以执行多个 Trial
* [ ] 每个 Trial 有唯一 ID
* [ ] Trial 绑定 Topology Version
* [ ] Trial 绑定 Router Config
* [ ] Trial 之间支持 Fixture Isolation

## Routing

* [ ] 支持 free mode
* [ ] 支持 guided mode
* [ ] 支持 replay mode
* [ ] Routing Decision 结构化
* [ ] 非法 Tool Selection 被拒绝
* [ ] 支持 max_tools_per_layer

## Trace

* [ ] 记录 available tools
* [ ] 记录 selected tools
* [ ] 记录 Tool Execution
* [ ] 记录 Layer Execution
* [ ] 记录 Tool Error
* [ ] 记录 latency
* [ ] 记录 token
* [ ] 记录 cost
* [ ] 生成 ObservedRoute
* [ ] 生成稳定 route_id

## Evaluation

* [ ] 支持 StructuredEvaluator
* [ ] 支持 LLMJudgeEvaluator
* [ ] 支持 CompositeEvaluator
* [ ] execution status 与 business success 分离
* [ ] Evaluation Error 单独记录

## Statistics

* [ ] Node available count
* [ ] Node selected count
* [ ] Edge opportunity count
* [ ] Edge observed count
* [ ] Route usage count
* [ ] Scenario route distribution
* [ ] Business success count
* [ ] Quality 原始数据
* [ ] Latency 原始数据
* [ ] Cost 原始数据

## Persistence

* [ ] Slow Regression Report 可序列化
* [ ] Trace 可以保存 JSONL
* [ ] 保存 Topology Version
* [ ] 保存 Scenario Version
* [ ] 保存 Router Model / Prompt Version
* [ ] 保存 Tool Metadata Version

## Boundary

* [ ] 不修改 Topology
* [ ] 不剪枝 Edge
* [ ] 不删除 Tool
* [ ] 不做 Route 最终排名
* [ ] 不做性能等级
* [ ] 不做成本等级
* [ ] 不做线上负载均衡

---

# 129. Phase 3 最终验收场景

假设系统中存在：

```text
4 Layers
50 Tools
约 300 条 Active Edge
```

准备：

```text
200 个 Scenario
```

每个执行：

```text
10 Trials
```

共：

```text
2000 Trials
```

Phase 3 完成后必须能够回答：

```text
Agent 实际调用了哪些 Tool？

每个 Tool 有多少次机会被选择？

最终真正选择了多少次？

每条 Edge 有多少次传播机会？

真正经过了多少次？

一个业务场景产生了多少种不同 Route？

不同 Trial 最终业务是否成功？

每次执行耗时多少？

实际 LLM Token 消耗多少？

实际 Tool / LLM 成本是多少？

哪些 Tool / Edge 从来没有被实际探索到？

哪些 Tool / Edge 经常出现在成功 Route 中？

哪些 Route 对同一业务形成了替代实现？
```

最后三类问题在 Phase 3 只输出数据。

不得自动得出：

```text
应该剪掉谁
```

或：

```text
应该优先使用谁
```

---

# 130. Phase 3 的核心验收问题

最终只问六个问题。

### 1.

给定一个 Scenario，Agent 是否只能在当前 Topology 允许的范围内自由组合 Tool？

### 2.

Agent 是否可以在一个 Layer 中选择多个 Tool，并将结果统一传播到下一层？

### 3.

重复执行同一个 Scenario 后，是否能够观察到不同的真实 Route？

### 4.

每一个 Trial 是否能够被准确还原为：

```text
哪些工具可选
→
选了哪些
→
执行结果是什么
→
下一层选了哪些
→
最终业务是否完成
```

### 5.

是否能够准确统计：

```text
Node Opportunity
Node Usage
Edge Opportunity
Edge Usage
Route Usage
```

### 6.

所有结果是否只是：

```text
Observed Evidence
```

而没有提前修改 Topology？

如果六个答案全部为：

```text
Yes
```

则 Phase 3 核心假设验证成功。

---

# 131. Phase 3 完成后的下一步

Phase 4：

> **Topology Learning & Pruning**

Phase 4 将第一次消费：

```text
Fast Regression Coverage
+
Slow Regression Trace
+
Node Opportunity
+
Edge Opportunity
+
Node Usage
+
Edge Usage
+
Business Success
```

开始回答：

```text
哪些 Edge 是真正低价值的？

哪些 Edge 虽然使用率低，但承担稀有业务能力？

哪些 Edge 可以安全进入 pruning candidate？

剪掉一条 Edge 后，
Fast Regression Coverage 是否下降？

剪枝后的 Topology 在 Slow Regression 中是否仍然稳定？
```

核心循环将正式形成：

```text
Declared Dense Topology
        ↓
Fast Regression
        ↓
Slow Regression
        ↓
Observed Evidence
        ↓
Pruning Candidate
        ↓
Regression Again
        ↓
Active Topology Version
```

Phase 3 的使命只有一个：

> **把“Agent 可能怎么使用工具”转化为“Agent 实际怎么使用工具”的可度量证据。**
