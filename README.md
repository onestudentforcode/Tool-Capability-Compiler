# Tool Capability Compiler

一个面向智能体工具调用的分层路由与拓扑优化框架。

项目不替开发者手工编排一条固定 Workflow，也不根据输入输出类型编译所谓“最小依赖 DAG”。开发者声明一个允许的工具搜索空间，后续由业务场景回归探索路线、评估效果，并让 Active Topology 逐步收敛。

```text
Declare → Initialize → Explore → Evaluate → Prune → Rank → Route
```

当前 Phase 1–4 已完成（`Declare → Initialize → Explore → Evaluate → Prune`），Phase 5（`Rank`）验收规格已起草：

- **Phase 1 分层拓扑**：有序 Layer 与 Tool Registry、`provider / worker` 双向白名单、相邻层默认全连接、确定性 Declared Topology、Schema 只验证已声明边不建边、RoutePlan 支持同层多 Tool 与拓扑约束校验
- **Phase 2 Fast Regression**：Tool Capability 与 CapabilityRegistry、Scenario / ScenarioSuite 严格加载（Gold 与 Query-only）、COVERED / UNCERTAIN / UNCOVERED 覆盖判定、区分 Capability Gap 与 Topology Gap、有界 CandidateRoute 搜索（多 Provider / Bridge / 禁用约束）、Coverage / Category / Gap 报告、Ollama Capability Resolver（Discovery Mode）、Baseline 与 Regression Diff、`regression fast` CLI
- **Phase 3 Slow Regression**：逐层动态路由（Router 只见当前可达 Tool）、同层多 Tool 并发执行、Trial / ExecutionTrace / ObservedRoute、`available` 与 `selected` 成对观测统计、`free` 与 `basefast`（CandidateRoute seed）两种探索、LLM Router 与 LLM Judge（可 fake 注入）、Fixture 隔离、JSONL / manifest 持久化、`regression slow` CLI
- **Phase 4 拓扑学习与安全剪枝**：Node / Edge Evidence（opportunity vs observed）、Pruning Candidate 与保护（唯一 Provider / Bridge / Sentinel）、Counterfactual Fast Regression、定向 Probe（basefast seed）、Fast / Slow 验证门与 Route 多样性守卫、TopologyVersion（commit / rollback，不物理删除）、`optimize` CLI
- **靶场强化里程碑（已完成）**：计量贯通（工具/路由/评估三段成本与 token）、JSON 拓扑可执行绑定（`implementation` 入口点 + CLI fail-fast）、Sandbox 工具真实化（确定性订单库五变体、失败路径、差异化延迟与成本、Composite 评估器）、场景级 Fixture 隔离（`metadata.fixture` 播种 + 幂等守卫）、场景资产与规模实跑（60 场景 5 类精确 70/15/15 分布 + 250-trial 实跑落盘 + optimize 联动），见 [battlefield-hardening.md](docs/acceptance/battlefield-hardening.md)
- **Phase 6 在线路由**：`online/` 顶包 + `select` 干跑 CLI —— RouteCatalog（ranking JSON + Active Topology 版本门禁 fail closed，canonical 重建路线结构）、Tier 偏好选路 + 轮转均衡、路线跟随执行（无在线探索）、有界降级（不重复失败路线、降级链可回放）、在线遥测 JSONL 并经 `online_results_to_trials` 无损回流下一轮离线优化——项目主循环至此闭合
- **Phase 5 Route 排名与分级**：`ranking/` 顶包 + `rank` CLI —— Route Profile（success 带 Wilson 区间 / quality / latency / cost 四维向量，缺失维度不臆造）、证据门槛（min_trials，不足只列清单）、四目标 Pareto Frontier（含支配归因与不可比标记）、FAST / BALANCED / QUALITY Tier（多标签、可解释规则）、逐 category 排名、STATISTICAL_TIE 并列、JSON 无损输出供 Phase 6 消费

## Quick start

```python
from capability_runtime import (
    LayerRegistry, RoutePlan, ToolRegistry, TopologyBuilder, tool,
)

@tool(
    layer="read",
    workers=["policy_check"],
    capabilities={"order.read", "order.search"},
)
async def database(): ...

@tool(
    layer="analyze",
    providers=["database"],
    workers=["refund"],
)
async def policy_check(): ...

@tool(layer="act", providers=["policy_check"])
async def refund(): ...

layers = LayerRegistry()
layers.register("read", 0)
layers.register("analyze", 1)
layers.register("act", 2)

tools = ToolRegistry()
for node in (database, policy_check, refund):
    tools.register(node)

topology = TopologyBuilder(layers, tools).build()
route = RoutePlan.from_groups(
    topology,
    [{"database"}, {"policy_check"}, {"refund"}],
)
print(route.explain())
```

加载业务场景：

```python
from capability_runtime import CapabilityRegistry, ScenarioLoader

capabilities = CapabilityRegistry.from_tools(tools)
assert capabilities.providers("order.read") == ("database",)

suite = ScenarioLoader().load_file("examples/scenarios/refund.json")
```

建边公式：

```text
Edge(A, B)
= adjacent(layer(A), layer(B))
  AND A.workers allows B
  AND B.providers allows A
```

`consumes / produces` 仍可声明，但只验证已允许边的 Schema 是否明显不匹配。业务意图决定拓扑，Schema 负责诊断。

## CLI

```bash
# Fast Regression（Gold Mode，不依赖 LLM；--fail-on-regression 可用于 CI）
tool-topology regression fast \
    --topology examples/topology/refund.json \
    --scenario examples/scenarios/refund.json

# Slow Regression（free 探索；--basefast seeds.json 以 CandidateRoute 为起点）
tool-topology regression slow \
    --topology examples/topology/refund.json \
    --scenario examples/slow_refund/scenarios.json \
    --trials 5 --out-dir artifacts/slow_regression

# 拓扑优化报告（Evidence → Candidate → 验证 → 版本）
tool-topology optimize \
    --topology examples/topology/refund.json \
    --scenario examples/scenarios/refund.json
```

## Develop

```bash
python -m pytest -q
python -m compileall -q src tests main.py
git diff --check
python -m pip install -e .
python main.py
```

项目原则见 [Phase 0](docs/acceptance/phase0.md)，各阶段验收规格见 [docs/acceptance/](docs/acceptance/)；当前边界：Phase 1–4 已完成，Phase 5（Route 排名与分级）见 [phase5.md](docs/acceptance/phase5.md)。
