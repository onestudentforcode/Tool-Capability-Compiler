# Phase 2 — Fast Regression & Capability Coverage

## 0. 阶段定位

Phase 2 建立项目的第一套业务能力验证机制：

> **Fast Regression**

Fast Regression 不执行真实 Tool，不访问数据库、不调用业务 API、不产生外部副作用。

它只消费：

```text
Tool Metadata
+
Current Topology
+
Business Scenarios
```

然后判断：

```text
当前工具拓扑
是否存在理论上能够完成该业务场景的 Route
```

因此 Phase 2 本质上是：

> **Capability Coverage Test**

而不是：

> Execution Test

---

# 1. Phase 2 的核心目标

Phase 2 必须实现以下能力：

1. 定义标准 Scenario JSON 格式
2. 批量加载业务场景
3. 将用户 Query 映射为需要的业务能力
4. 在当前 Tool Topology 中搜索候选 Route
5. 支持每一 Layer 选择一个或多个 Tool
6. 判断业务场景是否被当前 Topology 覆盖
7. 区分 `COVERED / UNCERTAIN / UNCOVERED`
8. 输出候选 Tool Route
9. 输出无法覆盖业务的具体原因
10. 聚合生成整体 Coverage Report
11. 识别缺失 Capability
12. 为 Phase 3 Slow Regression 生成候选测试 Route

---

# 2. Phase 2 非目标

本阶段明确不实现：

```text
真实 Tool 调用
数据库访问
HTTP / MCP 执行
Slow Regression
Tool Execution Trace
Latency Measurement
Cost Measurement
Route Ranking
Edge Pruning
Topology Learning
负载均衡
失败重试
降级
熔断
Loop
Recursive Tool Call
复杂权限系统
复杂风险控制
```

尤其禁止：

> 为了验证某个 Tool 是否真的有效，而在 Fast Regression 中实际调用 Tool。

这属于 Phase 3。

---

# 3. Phase 2 的输入

Phase 2 有两个核心输入。

## 3.1 Current Topology

来自 Phase 1：

```text
Layer
ToolNode
ToolEdge
Provider
Worker
Topology
```

例如：

```text
Layer 1 — Read

DB
RAG
WebSearch

        ↓

Layer 2 — Analyze

RefundPolicyCheck
RiskCheck
Summarizer

        ↓

Layer 3 — Action

Refund
SendEmail
UpdateOrder
```

---

## 3.2 Scenario Dataset

通过统一 JSON 格式定义业务场景。

例如：

```json
{
  "version": "v1",
  "scenarios": [
    {
      "id": "refund_001",
      "query": "帮用户判断订单123能不能退款",
      "category": "refund",
      "expected_capabilities": [
        "order.read",
        "refund.policy.check"
      ]
    }
  ]
}
```

Phase 2 的核心任务就是：

```text
Scenario
+
Topology
↓
Coverage Analysis
```

---

# 4. Fast Regression 的核心原则

Fast Regression 必须遵守：

> **只分析“声明的能力”，不验证“实际执行效果”。**

例如某 Tool：

```text
name:
refund_policy_check

description:
判断订单是否符合退款政策
```

Fast Regression 可以认为：

```text
该 Tool 理论上具备
refund.policy.check
```

但是不能证明：

```text
它判断得正确
```

因此：

```text
Fast Regression Success
≠
Business Execution Success
```

---

# 5. Fast Regression 的三个状态

禁止只返回：

```text
true / false
```

必须至少支持：

```text
COVERED
UNCERTAIN
UNCOVERED
```

---

## 5.1 COVERED

代表：

> 当前 Topology 中存在一条或多条合理 Route，从 Tool 声明能力来看，能够完成该 Scenario。

例如：

```text
Query:
查询订单123是否可以退款
```

找到：

```text
DB
↓
RefundPolicyCheck
```

所需能力：

```text
order.read
refund.policy.check
```

均得到满足。

结果：

```text
COVERED
```

---

# 6. UNCERTAIN

表示：

> 系统发现了可能相关的 Tool，但无法高置信度确认能够完整完成 Scenario。

例如：

用户：

```text
查询退款政策并判断订单123能否退款
```

系统只有：

```text
RAG
description:
查询公司内部知识
```

以及：

```text
OrderDB
description:
查询订单数据
```

但是没有显式：

```text
refund.policy.check
```

可能由 Agent 通过：

```text
OrderDB + RAG
```

自己完成判断。

这种情况不能直接：

```text
COVERED
```

也不应该：

```text
UNCOVERED
```

应返回：

```text
UNCERTAIN
```

---

# 7. UNCOVERED

代表：

> 当前 Topology 从 Tool Metadata 看，没有合理能力组合能够满足 Scenario。

例如：

```text
用户：
修改订单配送地址
```

当前只有：

```text
OrderRead
RAG
WebSearch
```

不存在：

```text
order.update
```

则：

```text
UNCOVERED
```

同时输出：

```text
missing_capabilities:
- order.update
```

---

# 8. Scenario

Phase 2 引入核心对象：

```text
Scenario
```

推荐模型：

```python
@dataclass(frozen=True)
class Scenario:
    id: str
    query: str

    category: str | None = None

    expected_capabilities:
        tuple[str, ...] = ()

    metadata:
        dict[str, Any] = field(default_factory=dict)
```

---

# 9. expected_capabilities

`expected_capabilities` 是 Fast Regression 非常重要的字段。

允许两类 Dataset。

---

## 9.1 Gold Scenario

人工已经知道场景需要：

```text
order.read
refund.policy.check
```

例如：

```json
{
  "id": "refund_001",
  "query": "判断订单123能不能退款",
  "expected_capabilities": [
    "order.read",
    "refund.policy.check"
  ]
}
```

这种场景可以用于稳定回归。

---

## 9.2 Query-only Scenario

只提供：

```json
{
  "id": "refund_002",
  "query": "这个订单还能退款吗？"
}
```

此时系统需要自行解析：

```text
Query
↓
Required Capability
```

这种 Scenario 更接近真实业务。

---

# 10. Phase 2 必须同时支持两类 Scenario

原因是：

```text
Gold Dataset
```

适合做：

```text
Regression
Evaluation
```

而：

```text
Query-only Dataset
```

适合：

```text
Discovery
Capability Boundary Exploration
```

两者不能混为一谈。

---

# 11. Scenario JSON Schema

建议第一版：

```json
{
  "version": "1.0",
  "name": "refund-regression",
  "description": "退款业务能力回归",

  "scenarios": [
    {
      "id": "refund_001",
      "query": "查询订单123是否可以退款",
      "category": "refund",
      "expected_capabilities": [
        "order.read",
        "refund.policy.check"
      ],
      "metadata": {
        "priority": "high"
      }
    }
  ]
}
```

---

# 12. Scenario Loader

实现：

```python
class ScenarioLoader:

    def load_file(
        self,
        path: Path,
    ) -> ScenarioSuite:
        ...
```

返回：

```python
@dataclass
class ScenarioSuite:
    name: str
    version: str
    scenarios: tuple[Scenario, ...]
```

---

# 13. JSON Validation

Scenario 文件必须进行严格校验。

至少验证：

```text
version exists
scenario.id unique
query non-empty
expected_capabilities valid
metadata JSON serializable
```

错误必须抛：

```text
ScenarioValidationError
```

---

# 14. Capability 在 Phase 2 正式成为一级概念

Phase 1 Tool 已经拥有：

```text
layer
provider
worker
```

Phase 2 必须增加：

```text
capabilities
```

例如：

```python
@tool(
    layer="read",
    capabilities={
        "order.read",
        "order.search",
    },
)
async def query_order(...):
    ...
```

另一个：

```python
@tool(
    layer="analyze",
    capabilities={
        "refund.policy.check",
    },
)
async def check_refund(...):
    ...
```

---

# 15. Capability 与 Tool 解耦

必须允许：

```text
一个 Tool
→ 多个 Capability
```

例如：

```text
OrderDB

order.read
order.search
order.history.read
```

同时允许：

```text
一个 Capability
→ 多个 Tool
```

例如：

```text
order.read

← MySQLOrderReader
← ERPOrderReader
← CacheOrderReader
```

这为后续：

```text
Route Diversity
Load Balancing
```

打基础。

---

# 16. Capability Naming

Phase 2 推荐采用：

```text
domain.action
```

或：

```text
domain.resource.action
```

例如：

```text
order.read
order.update

refund.policy.read
refund.policy.check
refund.execute

user.profile.read

email.compose
email.send
```

Capability Name 必须：

```text
lowercase
dot-separated
```

禁止：

```text
RefundCheck
CHECK_REFUND
refund check
```

---

# 17. Capability Registry

新增：

```python
class CapabilityRegistry:

    def register(
        self,
        capability: str,
        tool_name: str,
    ) -> None:
        ...

    def providers(
        self,
        capability: str,
    ) -> list[str]:
        ...

    def capabilities_of(
        self,
        tool_name: str,
    ) -> set[str]:
        ...
```

---

# 18. Query → Capability Resolution

对于 Query-only Scenario，需要：

```text
User Query
↓
Capability Resolver
↓
Required Capabilities
```

例如：

```text
"帮我查一下订单123"
```

得到：

```text
order.read
```

---

# 19. Phase 2 可以使用 LLM

这一点与原 Phase 1 不同。

LLM 可以用于：

```text
Query
→
Required Capabilities
```

因为这是语义不确定问题。

但是 LLM 不参与：

```text
Topology Traversal
Edge Validation
Route Search
Coverage Calculation
```

这些必须由确定性代码完成。

---

# 20. Capability Resolver Interface

必须抽象：

```python
class CapabilityResolver(Protocol):

    async def resolve(
        self,
        query: str,
        available_capabilities: set[str],
    ) -> CapabilityResolution:
        ...
```

返回：

```python
@dataclass
class CapabilityResolution:

    required:
        tuple[str, ...]

    optional:
        tuple[str, ...]

    confidence:
        float

    reasoning:
        str | None = None
```

---

# 21. 不允许 Capability Resolver 自由发明 Capability

必须给 Resolver：

```text
available_capabilities
```

要求它优先从现有 Capability 中选择。

但是需要支持发现缺口。

因此 Resolver 可以返回：

```text
missing_capability_hints
```

例如：

```python
CapabilityResolution(
    required=("order.read",),
    missing_capability_hints=(
        "order.delivery_address.update",
    ),
)
```

---

# 22. Resolver Output 必须结构化

禁止依赖：

```text
LLM 返回自然语言
然后 Regex Parse
```

应该使用结构化输出：

```json
{
  "required": [
    "order.read",
    "refund.policy.check"
  ],
  "optional": [],
  "missing_capability_hints": [],
  "confidence": 0.94
}
```

---

# 23. Gold Scenario 不需要 LLM Resolver

如果：

```text
expected_capabilities
```

已经存在：

```text
required_capabilities
=
expected_capabilities
```

直接进入 Topology Coverage。

这样 Gold Regression：

```text
可复现
确定性更强
成本更低
```

---

# 24. Coverage Analyzer

核心组件：

```python
class CoverageAnalyzer:

    def analyze(
        self,
        topology: ToolTopology,
        required_capabilities: set[str],
    ) -> CoverageResult:
        ...
```

它负责回答：

```text
这些 Capability
能否在当前 Topology 中形成合理 Route？
```

---

# 25. 不能只检查 Capability 是否存在

例如当前：

```text
Tool A
capability = order.read

Tool B
capability = refund.policy.check
```

单独来看两个 Capability 都存在。

但 Topology：

```text
A  X  B
```

没有合法路径。

那么：

```text
Capability Exists
```

不等于：

```text
Scenario Covered
```

因此必须检查：

```text
Capability Presence
+
Topology Reachability
```

---

# 26. Fast Regression Route

Phase 2 引入：

```text
CandidateRoute
```

它不是执行计划。

只是：

> 从当前声明 Topology 中找到的一种理论工具组合。

例如：

```python
@dataclass(frozen=True)
class CandidateRoute:

    layers:
        tuple[RouteLayer, ...]

    capabilities:
        frozenset[str]
```

---

# 27. RouteLayer

因为一层允许多个 Tool：

```python
@dataclass(frozen=True)
class RouteLayer:

    layer: str

    tools:
        tuple[str, ...]
```

例如：

```text
Route:

Read:
    DB
    RAG

Analyze:
    RefundPolicyCheck
```

表示：

```python
CandidateRoute(
    layers=(
        RouteLayer(
            layer="read",
            tools=("db", "rag"),
        ),
        RouteLayer(
            layer="analyze",
            tools=("refund_policy_check",),
        ),
    )
)
```

---

# 28. Candidate Route 的基本约束

候选 Route 必须满足：

1. Tool 存在于当前 Active Topology
2. Tool 所在 Layer 顺序合法
3. 跨 Layer 传播必须存在 ToolEdge
4. 每一个 required capability 至少被一个选中 Tool 覆盖
5. 不使用 disabled edge
6. 不使用 disabled tool

---

# 29. Phase 2 不追求唯一 Route

例如：

```text
order.read
```

可能由：

```text
DB
ERP
Cache
```

实现。

那么可以得到：

```text
Route A
DB → PolicyCheck
```

```text
Route B
ERP → PolicyCheck
```

```text
Route C
Cache → PolicyCheck
```

Fast Regression 应保留候选 Route 的多样性。

---

# 30. Route 数量限制

不能无限枚举所有组合。

需要：

```text
max_candidate_routes
```

例如默认：

```text
20
```

避免：

```text
Dense Topology
→
Combinatorial Explosion
```

---

# 31. Phase 2 Route Search 目标

Fast Regression 不追求：

```text
Best Route
```

只需要证明：

```text
至少存在可行 Route
```

并保留一定数量候选。

因此主要目标是：

```text
Feasibility Search
```

而不是：

```text
Optimal Planning
```

---

# 32. Route Search 推荐策略

第一版建议：

```text
Capability-guided Layer Search
```

步骤：

```text
1. 根据 required capabilities 找相关 Tool
2. 按 Layer 分组
3. 根据 Topology Edge 检查 Tool 是否可以组合
4. 生成有限数量 Candidate Routes
5. 删除明显冗余 Route
```

---

# 33. Relevant Tool Pruning

假设有：

```text
100 Tools
```

Scenario 需要：

```text
order.read
refund.policy.check
```

首先根据 Capability Registry 找：

```text
order.read
→
DB
ERP

refund.policy.check
→
RefundPolicyCheck
```

因此首先得到 Relevant Tool Set：

```text
DB
ERP
RefundPolicyCheck
```

再根据 Topology 扩展必要中间节点。

---

# 34. Bridge Tool

某些 Tool 本身不提供 required capability，但可能是连接路径必要节点。

例如：

```text
DB
↓
NormalizeOrder
↓
RefundPolicyCheck
```

其中：

```text
NormalizeOrder
```

没有 Scenario 直接要求的 Capability。

但是它是：

```text
Bridge Tool
```

所以 Route Search 不能只包含 capability-matching Tool。

---

# 35. Bridge Search

允许在：

```text
Relevant Tools
```

之间搜索有限长度路径。

建议配置：

```text
max_bridge_depth
```

Phase 2 默认：

```text
2
```

避免搜索空间爆炸。

---

# 36. Fast Regression Result

推荐：

```python
@dataclass
class FastRegressionResult:

    scenario_id: str

    status:
        CoverageStatus

    required_capabilities:
        tuple[str, ...]

    covered_capabilities:
        tuple[str, ...]

    missing_capabilities:
        tuple[str, ...]

    candidate_routes:
        tuple[CandidateRoute, ...]

    confidence:
        float

    reason:
        str
```

---

# 37. CoverageStatus

```python
class CoverageStatus(str, Enum):

    COVERED = "covered"
    UNCERTAIN = "uncertain"
    UNCOVERED = "uncovered"
```

---

# 38. COVERED 判断

第一版规则：

```text
required capabilities 全部存在
AND
至少存在合法 Candidate Route
AND
Capability Resolver confidence >= threshold
```

则：

```text
COVERED
```

Gold Scenario：

```text
无需 Resolver confidence
```

---

# 39. UNCOVERED 判断

满足任一：

```text
required capability 完全没有 Tool 提供
```

或者：

```text
相关 Tool 存在
但 Topology 中无法形成合法 Route
```

则：

```text
UNCOVERED
```

需要进一步区分：

```text
MISSING_CAPABILITY
```

和：

```text
TOPOLOGY_DISCONNECTED
```

---

# 40. UNCERTAIN 判断

例如：

```text
Resolver confidence 过低
```

或者：

```text
只有语义相近 Capability
```

或者：

```text
需要的能力依赖 Tool 自由推理
但没有显式 Tool Capability 支持
```

返回：

```text
UNCERTAIN
```

---

# 41. Failure Reason

建议枚举：

```text
MISSING_CAPABILITY
TOPOLOGY_DISCONNECTED
LOW_RESOLUTION_CONFIDENCE
AMBIGUOUS_CAPABILITY
NO_VALID_ROUTE
ROUTE_SEARCH_LIMIT_REACHED
INVALID_SCENARIO
```

---

# 42. Missing Capability Report

Phase 2 一个非常重要的产物：

```text
MissingCapabilityReport
```

例如跑 1000 个 Scenario：

```text
order.read
covered 412

refund.policy.check
covered 190

invoice.send
missing 73

user.address.update
missing 51
```

可以快速回答：

> 当前业务数据中，哪些问题 Tool System 根本没有能力覆盖？

这正是 Fast Regression 最重要的价值。

---

# 43. Coverage Report

整个 Scenario Suite 执行完后：

```python
@dataclass
class CoverageReport:

    suite_name: str

    total: int

    covered: int
    uncertain: int
    uncovered: int

    coverage_rate: float

    results:
        tuple[FastRegressionResult, ...]

    missing_capabilities:
        dict[str, int]
```

---

# 44. Coverage Rate

建议：

```text
coverage_rate
=
covered / total
```

UNCERTAIN 不计入 Covered。

例如：

```text
Total      1000
Covered     820
Uncertain   100
Uncovered    80

Coverage Rate = 82%
```

---

# 45. Category Coverage

必须支持按：

```text
category
```

聚合。

例如：

| Category | Total | Covered | Uncertain | Uncovered |
| -------- | ----: | ------: | --------: | --------: |
| refund   |   200 |     190 |         5 |         5 |
| order    |   300 |     294 |         4 |         2 |
| invoice  |   100 |      61 |        20 |        19 |

这样可以定位：

```text
能力盲区在哪个业务领域
```

---

# 46. Topology Gap

需要区分：

```text
Capability Gap
```

和：

```text
Topology Gap
```

---

## Capability Gap

例如：

```text
需要：
invoice.send
```

但系统没有任何 Tool 提供：

```text
invoice.send
```

这是：

```text
Capability Gap
```

解决方式：

```text
新增 Tool
```

---

## Topology Gap

例如：

```text
Tool A:
order.read

Tool B:
refund.policy.check
```

能力都存在。

但是：

```text
A → B
```

由于 provider / worker 限制不存在。

这是：

```text
Topology Gap
```

解决方式可能是：

```text
调整 provider / worker
```

而不是增加 Tool。

这两类问题必须在 Report 中分开。

---

# 47. Fast Regression Runner

推荐：

```python
class FastRegressionRunner:

    async def run(
        self,
        suite: ScenarioSuite,
        topology: ToolTopology,
    ) -> CoverageReport:
        ...
```

执行：

```text
for scenario

    ↓

resolve capability

    ↓

analyze coverage

    ↓

search candidate route

    ↓

build result

    ↓

aggregate
```

---

# 48. Gold Mode

Runner 支持：

```text
mode = gold
```

仅使用：

```text
expected_capabilities
```

完全不调用 LLM。

适合：

```text
CI
PR Regression
Stable Benchmark
```

---

# 49. Discovery Mode

支持：

```text
mode = discovery
```

通过：

```text
Query
→ Capability Resolver
```

进行能力探索。

适合：

```text
新业务数据
线上 Query Dataset
历史真实 Query
```

---

# 50. 推荐 CI 用法

Phase 2 完成后，可以：

```text
PR
↓
Tool / Topology Changed
↓
Run Fast Regression
↓
Coverage Comparison
```

例如：

```text
Before:

coverage = 94.6%

After:

coverage = 87.2%
```

直接：

```text
CI Failed
```

或者至少：

```text
Warning
```

---

# 51. Baseline

Fast Regression 必须支持保存：

```text
Baseline Report
```

例如：

```json
{
  "suite": "customer_service_v1",
  "topology_version": "v12",
  "coverage_rate": 0.946
}
```

下一次 Regression 与 Baseline 比较。

---

# 52. Regression Diff

必须能够输出：

```text
Newly Covered
Newly Uncovered
Still Uncovered
Status Changed
```

例如：

```text
refund_021

Before:
COVERED

After:
UNCOVERED

Reason:
edge db → refund_policy_check disabled
```

这个能力非常重要。

---

# 53. Topology Version

Phase 2 开始正式引入：

```text
topology_version
```

所有 Regression Report 必须绑定：

```text
Topology Version
Scenario Suite Version
```

例如：

```text
topology=v0.2.4
suite=customer_service_v3
```

否则不同回归结果无法可靠比较。

---

# 54. Candidate Route Fingerprint

Phase 2 即使不执行 Route，也应该给 Route 生成稳定标识。

例如：

```text
read:[db,rag]
analyze:[refund_check]
action:[refund]
```

规范化后 hash：

```text
route_id
```

为 Phase 3 / Phase 4 的统计做准备。

---

# 55. Route Normalization

同一 Layer：

```text
DB + RAG
```

与：

```text
RAG + DB
```

如果表示并行工具集合，应视为同一个 Route。

所以生成 fingerprint 时：

```text
Layer 内 Tool Name ASC
```

---

# 56. Fast Regression 不产生 Route Score

Phase 2 禁止根据：

```text
Tool 数量
路径长度
Provider 类型
```

提前判断：

```text
Route A 比 Route B 好
```

因为此时没有：

```text
真实成功率
真实成本
真实延迟
```

只允许记录结构性属性：

```text
tool_count
layer_count
route_depth
```

---

# 57. Fast Regression 的核心输出例子

输入：

```json
{
  "id": "refund_001",
  "query": "查一下订单123能不能退款",
  "expected_capabilities": [
    "order.read",
    "refund.policy.check"
  ]
}
```

Topology：

```text
Read

DB
RAG
WebSearch

Analyze

RefundPolicyCheck
Summarizer
```

得到：

```json
{
  "scenario_id": "refund_001",
  "status": "covered",

  "required_capabilities": [
    "order.read",
    "refund.policy.check"
  ],

  "covered_capabilities": [
    "order.read",
    "refund.policy.check"
  ],

  "missing_capabilities": [],

  "candidate_routes": [
    {
      "layers": [
        {
          "layer": "read",
          "tools": [
            "db"
          ]
        },
        {
          "layer": "analyze",
          "tools": [
            "refund_policy_check"
          ]
        }
      ]
    }
  ]
}
```

---

# 58. Missing Capability 示例

用户：

```text
把订单配送地址修改成上海
```

Resolver：

```text
required:
order.read
order.shipping_address.update
```

Topology 只有：

```text
order.read
```

结果：

```text
UNCOVERED

missing:
order.shipping_address.update
```

---

# 59. Topology Gap 示例

存在：

```text
DB
capability:
order.read
```

和：

```text
RefundCheck
capability:
refund.policy.check
```

但：

```text
DB.worker = Summarizer

RefundCheck.provider = RAG
```

所以：

```text
DB → RefundCheck
```

不存在。

结果：

```text
UNCOVERED

reason:
TOPOLOGY_DISCONNECTED
```

而不是：

```text
MISSING_CAPABILITY
```

---

# 60. Unit Tests — Scenario Loader

至少：

```text
load valid suite
duplicate scenario id
missing query
invalid capabilities
empty suite
metadata parsing
```

---

# 61. Unit Tests — Capability Registry

至少：

```text
register capability
one tool multiple capabilities
one capability multiple tools
find capability providers
unknown capability
```

---

# 62. Unit Tests — Capability Resolver

Gold Mode：

```text
不调用 LLM
```

Discovery Mode：

使用 Fake Resolver。

必须测试：

```text
valid resolution
low confidence
missing capability hint
optional capability
```

Phase 2 单元测试不得依赖真实 LLM API。

---

# 63. Unit Tests — Coverage Analyzer

至少：

### Case 1

Capability 存在且 Route 连通：

```text
COVERED
```

---

### Case 2

Capability 不存在：

```text
UNCOVERED
MISSING_CAPABILITY
```

---

### Case 3

Capability 都存在但 Graph 不连通：

```text
UNCOVERED
TOPOLOGY_DISCONNECTED
```

---

### Case 4

存在多个 Provider：

返回多个：

```text
CandidateRoute
```

---

### Case 5

某 Layer 需要多个 Tool：

```text
DB + RAG
↓
Analyzer
```

能够生成：

```text
multi-tool RouteLayer
```

---

# 64. Unit Tests — Route Search

必须验证：

```text
respect provider
respect worker
respect disabled edge
respect disabled tool
allow multiple nodes per layer
bridge node discovery
route deduplication
max route count
```

---

# 65. Integration Test

准备：

```text
10~20 Tools
```

覆盖：

```text
order
refund
email
user
invoice
```

准备：

```text
50~100 Scenarios
```

其中人为设计：

```text
70% covered
15% uncertain
15% uncovered
```

运行：

```python
report = await runner.run(
    suite,
    topology,
)
```

验证：

```text
Coverage Report
Category Report
Missing Capability
Topology Gap
Candidate Route
```

均正确。

---

# 66. 推荐目录结构

在 Phase 1 基础上新增：

```text
src/
└── tool_topology/
    │
    ├── capability/
    │   ├── models.py
    │   ├── registry.py
    │   └── resolver.py
    │
    ├── scenario/
    │   ├── models.py
    │   ├── loader.py
    │   └── validation.py
    │
    ├── regression/
    │   ├── fast_runner.py
    │   ├── coverage.py
    │   ├── route_search.py
    │   ├── result.py
    │   └── report.py
    │
    └── topology/
        └── ...
```

---

# 67. 模块职责边界

## Capability Resolver

只负责：

```text
Query
→
Required Capabilities
```

不得搜索 Graph。

---

## Coverage Analyzer

负责：

```text
Capability
+
Topology
→
Coverage
```

不得调用 Tool。

---

## Route Search

只负责：

```text
在 Topology 中寻找合法 Candidate Route
```

---

## FastRegressionRunner

只负责：

```text
编排上述三个组件
+
聚合结果
```

---

# 68. Phase 2 开发顺序

当前实现状态：

```text
Step 1  COMPLETE
Step 2  COMPLETE
Step 3  COMPLETE        (Gold Mode Coverage Analyzer: COVERED / UNCOVERED;
                              MISSING_CAPABILITY / TOPOLOGY_DISCONNECTED)
Step 4+ NOT STARTED
```

本状态只表示代码落地进度，不改变后续步骤的验收要求。

建议：

## Step 1

扩展 Tool：

```text
capabilities
```

并实现：

```text
CapabilityRegistry
```

---

## Step 2

实现：

```text
Scenario
ScenarioSuite
ScenarioLoader
```

---

## Step 3

实现 Gold Mode：

```text
expected_capabilities
↓
Coverage Analyzer
```

此时先完全不引入 LLM。

---

## Step 4

实现：

```text
CandidateRoute
RouteLayer
Route Search
```

---

## Step 5

实现：

```text
COVERED
UNCERTAIN
UNCOVERED
```

与完整 Failure Reason。

---

## Step 6

实现：

```text
CoverageReport
Category Report
Missing Capability Report
Topology Gap Report
```

---

## Step 7

增加：

```text
CapabilityResolver Interface
```

以及 Fake Resolver。

---

## Step 8

最后再接真实：

```text
LLM Capability Resolver
```

---

## Step 9

实现：

```text
Baseline
Regression Diff
```

---

## Step 10

建立：

```text
Fast Regression CLI
```

---

# 69. CLI

Phase 2 建议提供：

```bash
tool-topology regression fast \
    --topology topology.json \
    --scenario scenarios/refund.json
```

输出：

```text
Fast Regression

Suite: refund-v1
Topology: v0.2

Total:      100
Covered:     86
Uncertain:    8
Uncovered:    6

Coverage: 86.00%

Missing Capabilities:
1. refund.special_case.check   4
2. order.address.update        2
```

---

# 70. Scenario JSON 是核心资产

从 Phase 2 开始，需要把：

```text
Business Scenario Dataset
```

视为项目的一等资产。

它不只是：

```text
test fixture
```

而是未来：

```text
Fast Regression
Slow Regression
Topology Optimization
Route Ranking
```

共同消费的数据来源。

因此必须具备：

```text
version
id
category
metadata
```

---

# 71. Fast / Slow Regression 必须使用同一 Scenario Schema

这是一个关键架构约束。

Phase 3 不应该重新设计另一套测试输入。

应该：

```text
Scenario Suite
      │
      ├── Fast Regression
      │
      └── Slow Regression
```

区别只在：

```text
Fast:
Metadata Simulation

Slow:
Actual Tool Execution
```

---

# 72. Phase 2 不修改 Topology

非常重要：

> Fast Regression 是 Observer，不是 Optimizer。

它可以发现：

```text
Capability Gap
Topology Gap
```

但不得：

```text
自动增加 Edge
自动删除 Edge
自动修改 provider
自动修改 worker
```

这些只能形成：

```text
Suggestion / Report
```

实际 Topology 优化属于 Phase 4。

---

# 73. Capability Gap 建议

Fast Regression 可以输出：

```json
{
  "type": "capability_gap",
  "capability": "order.address.update",
  "affected_scenarios": 34
}
```

但不能自己创建虚拟 Tool。

---

# 74. Topology Gap 建议

例如：

```text
DB
```

和：

```text
RefundCheck
```

存在能力，但不能连接。

可以输出：

```json
{
  "type": "topology_gap",
  "source": "db",
  "target": "refund_check",
  "affected_scenarios": 12
}
```

后续人工决定是否修改：

```text
provider / worker
```

---

# 75. Phase 2 的性能要求

Fast Regression 未来会用于：

```text
大量业务 Query
```

所以不能一个 Scenario 就进行全图暴力枚举。

Phase 2 MVP 推荐目标：

```text
100 Tools
1000 Scenarios
```

在不执行真实 LLM Resolver 的 Gold Mode 下：

```text
秒级完成
```

具体严格 benchmark 暂不要求。

---

# 76. Cache

Phase 2 可以加入简单 Cache：

```text
required capability set
+
topology version
↓
candidate routes
```

因为很多 Scenario 可能映射为相同能力组合。

例如：

```text
"查一下订单"
"帮我看看这个订单"
"订单123是什么情况"
```

最终都是：

```text
order.read
```

可以复用 Coverage Result。

---

# 77. Phase 2 暂不根据 Regression 自动优化 Graph

即使发现：

```text
某条 Edge 没有 Candidate Route 使用
```

也不能剪枝。

原因：

Fast Regression 只是：

```text
静态能力覆盖
```

真正 Edge 使用率必须来自 Phase 3：

```text
Slow Regression Execution Trace
```

---

# 78. Phase 2 与 Phase 3 的边界

Phase 2 输出：

```text
Candidate Route
```

Phase 3 才输出：

```text
Executed Route
Execution Trace
Evaluation
```

即：

```text
Phase 2:
Could work

Phase 3:
Did work
```

这两个概念必须严格分离。

---

# 79. Phase 2 的核心公式

Fast Regression：

```text
Scenario
↓
Required Capabilities
↓
Relevant Tools
↓
Topology-constrained Route Search
↓
Coverage Result
```

Coverage 可以表达为：

```text
Covered
=
Capability Available
∩
Topology Reachable
∩
Route Constructible
```

但：

```text
Covered
≠
Execution Success
```

---

# 80. Phase 2 Definition of Done

必须满足：

## Scenario

* [ ] 支持 Scenario JSON
* [ ] 支持 Scenario Suite
* [ ] 支持版本
* [ ] 支持分类
* [ ] 支持 expected_capabilities
* [ ] 支持 query-only scenario

## Capability

* [ ] Tool 可以声明 Capability
* [ ] Capability Registry 可正常工作
* [ ] 一个 Tool 可提供多个 Capability
* [ ] 一个 Capability 可存在多个 Tool Provider

## Resolution

* [ ] Gold Mode 不依赖 LLM
* [ ] Discovery Mode 支持 Capability Resolver
* [ ] Resolver 输出结构化
* [ ] 支持 confidence
* [ ] 支持 missing capability hint

## Coverage

* [ ] 支持 COVERED
* [ ] 支持 UNCERTAIN
* [ ] 支持 UNCOVERED
* [ ] 可以区分 Capability Gap
* [ ] 可以区分 Topology Gap

## Route

* [ ] 能生成 Candidate Route
* [ ] Route 遵守 provider
* [ ] Route 遵守 worker
* [ ] Route 遵守 Layer
* [ ] 每层支持多个 Tool
* [ ] 支持 Bridge Tool
* [ ] 支持多候选 Route
* [ ] Candidate Route 有稳定 route_id

## Report

* [ ] 输出 Coverage Rate
* [ ] 输出 Category Coverage
* [ ] 输出 Missing Capability Report
* [ ] 输出 Topology Gap Report
* [ ] 支持 Baseline
* [ ] 支持 Regression Diff

## Boundary

* [ ] Fast Regression 不调用真实 Tool
* [ ] 不修改 Topology
* [ ] 不做 Edge 剪枝
* [ ] 不统计真实 Cost
* [ ] 不统计真实 Latency
* [ ] 不做 Retry / Fallback / Circuit Breaker

---

# 81. Phase 2 最终验收场景

系统拥有：

```text
50 Tools
4 Layers
大量默认连接
部分 provider / worker 白名单
```

输入：

```text
500 个真实业务 Query
```

Fast Regression 应能够输出：

```text
当前 Tool System 理论覆盖率是多少？

哪些业务问题完全没有对应 Tool？

哪些 Tool 已存在，但 Topology 阻止其组成有效 Route？

哪些 Scenario 存在多个 Candidate Route？

修改 Tool 或 Topology 后，
哪些原本能够完成的业务变得无法完成？
```

如果这些问题全部能够稳定回答，则 Phase 2 完成。

---

# 82. Phase 2 最核心的验收问题

最后只需要问五个问题：

### 1.

给当前 Topology 和一个业务 Query，是否能在不执行 Tool 的情况下判断它理论上是否可完成？

### 2.

如果不能完成，是否能够明确区分：

```text
缺 Tool
```

还是：

```text
Tool 有，但是连接关系有问题
```

### 3.

一个业务需求存在多条理论 Route 时，是否能够保留这些 Route，而不是强行选择唯一路径？

### 4.

修改 Tool / provider / worker 后，是否能够通过 Fast Regression 快速发现业务能力回退？

### 5.

Fast Regression 的输出是否可以直接作为下一阶段 Slow Regression 的候选 Route 输入？

如果五个答案全部为：

```text
Yes
```

则 Phase 2 核心假设验证成功。

---

# 83. Phase 2 完成后的下一步

Phase 3：

> **Slow Regression & Execution Exploration**

Phase 3 将第一次真正执行 Tool。

核心流程：

```text
Scenario
↓
Candidate Search Space
↓
Agent freely selects tools per layer
↓
Actual Execution
↓
Trace Graph
↓
Business Evaluation
```

然后开始收集：

```text
Route Usage
Success Rate
Quality
Latency
Cost
Tool Selection
Edge Usage
```

这些真实 Trace 数据才会成为 Phase 4：

```text
Topology Pruning
```

的基础。

因此 Phase 2 的任务始终只有一个：

> **验证当前 Tool Topology 的理论业务覆盖边界，而不是证明执行质量。**
