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
- **剪枝编排（已完成）**：`tool-topology optimize analyze/validate/commit/rollback`——analyze 消费慢回归落盘证据产候选提案（只读、Optimization 集隔离）；validate 三关判定（快门拦截跳慢门，REJECT 退出码 1）；commit 唯一写操作且 ACCEPT 记录 + 补丁/声明双指纹是硬门槛；rollback 记录重放（非反向补丁），见 [optimize-pipeline.md](docs/acceptance/optimize-pipeline.md)
- **接入辅助（已完成）**：`@tool` 从类型注解自动推断 consumes/produces（显式优先、类型不建边）；`onboarding/` 顶包——OpenAI specs 批量适配器（`from_openai_specs`）、capabilities LLM 批量提案 + 人工审阅 diff + 审阅门禁应用（提案无落盘路径）、`tool-topology onboard scaffold/propose/apply` 三段式 CLI，见 [onboarding-assist.md](docs/acceptance/onboarding-assist.md)
- **复合节点（已完成）**："最终能力"作为宏节点入图——`composite/` 顶包产出与 ToolNode 完全同构的工厂节点（注册/建边/覆盖/执行/在线零特殊分支），内部是子拓扑 + 有界循环（stop_when + max_iterations + 持久黑板）；内层计量回放外层账单、证据经 `flatten_composite_results` 用同一套 Phase 3/4 机器离线处理；外层保持无环（深度 ≤2 / 自引用构建期拦截），见 [composite-nodes.md](docs/acceptance/composite-nodes.md)
- **资源句柄计量（已完成）**：`resources/` 顶包——工具经 `metered()` / `InMemoryStore` / `LLMResource` 句柄访问资源，token 与读写来源/次数自动计量（三档 `MeteringSource` 诚实标注），零工具侧上报代码；access 维度贯通 trace / route 统计 / 排名 Profile / 在线遥测；计费基准保持声明值，实测值输出漂移信号，见 [resource-metering.md](docs/acceptance/resource-metering.md)
- **Phase 6 在线路由**：`online/` 顶包 + `select` 干跑 CLI —— RouteCatalog（ranking JSON + Active Topology 版本门禁 fail closed，canonical 重建路线结构）、Tier 偏好选路 + 轮转均衡、路线跟随执行（无在线探索）、有界降级（不重复失败路线、降级链可回放）、在线遥测 JSONL 并经 `online_results_to_trials` 无损回流下一轮离线优化——项目主循环至此闭合
- **Phase 5 Route 排名与分级**：`ranking/` 顶包 + `rank` CLI —— Route Profile（success 带 Wilson 区间 / quality / latency / cost 四维向量，缺失维度不臆造）、证据门槛（min_trials，不足只列清单）、四目标 Pareto Frontier（含支配归因与不可比标记）、FAST / BALANCED / QUALITY Tier（多标签、可解释规则）、逐 category 排名、STATISTICAL_TIE 并列、JSON 无损输出供 Phase 6 消费

## Quick start

想直接跑起来，请按 [docs/quickstart.md](docs/quickstart.md) 的线性路径操作
（约 30 分钟，从零走通 Declare → Fast → Slow → Prune → Rank → Route 全循环，
全程离线，每步附实测输出）。以下是框架 API 的最小示例：

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

# Slow Regression（进程内 Demo：sandbox 工具 + 场景级 Fixture，脱网可跑）
python examples/slow_refund/run_demo.py --trials 25 --out-dir artifacts/slow_regression
# CLI 路径要求 JSON 拓扑为工具声明 "implementation": "module:attr" 绑定，
# 未绑定的拓扑会被直接拒绝（exit 2），避免空跑污染统计；
# 沙盒世界的可执行拓扑由导出器生成（以 Python 声明为唯一事实源）：
python examples/slow_refund/export_topology.py
python -m capability_runtime.cli regression slow \
    --topology examples/topology/refund_sandbox.json \
    --scenario examples/scenarios/refund.json --trials 2

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

项目原则见 [Phase 0](docs/acceptance/phase0.md)，各阶段验收规格见 [docs/acceptance/](docs/acceptance/)，快速上手见 [quickstart.md](docs/quickstart.md)，入门教程见 [tutorial.md](docs/tutorial.md)。当前边界：Phase 0–6 全部完成（含[靶场强化里程碑](docs/acceptance/battlefield-hardening.md)），主循环 Declare → Fast → Slow → Prune → Rank → Route → 回流 已闭合；涌现拓扑（方向一）已评估并暂时废弃，保持严格拓扑约束，可继续开发的方向清单见 [AGENTS.md](AGENTS.md) §7（每项启动前需先起草验收文档）。
