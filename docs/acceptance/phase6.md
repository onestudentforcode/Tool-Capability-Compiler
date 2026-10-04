# Phase 6 — Online Routing & Serving Runtime

## 0. 阶段定位

Phase 6 建立项目的最后一层：

> **把离线学到的拓扑与路线，变成每次请求可执行的路由决策。**

此前各阶段的产出：

```text
Phase 1  →  Declared Topology（搜索空间）
Phase 2  →  理论能力覆盖
Phase 3  →  真实执行证据
Phase 4  →  Active Topology（收敛且版本化的搜索空间）
Phase 5  →  Route Ranking / Pareto / Tier（每条路线的多维依据）
```

Phase 6 第一次把它们组合成在线服务形态：

```text
请求（query / category / 偏好）
        ↓
RouteCatalog（Active Topology + Route Ranking，版本门禁）
        ↓
RouteSelector（Tier 偏好 → 候选集）
        ↓
LoadBalancer（同级候选间均衡）
        ↓
OnlineRuntime（按选定路线执行，不再探索）
        ↓
失败 → FallbackPolicy（有界降级到保留的替代路线）
        ↓
OnlineResult（可解释：选了谁、为什么、降级链）
        ↓
OnlineTelemetry（回流下一轮离线回归）
```

自此项目主循环闭合：

```text
Declare → Fast → Slow → Prune → Rank → Route(在线)
    ↑________________________________________|
              在线遥测成为下一轮回归的证据
```

---

## 0.1 前置条件

Phase 6 直接消费以下既有产物，全部已就绪：

```text
Active Topology     Phase 4 的 TopologyVersion / Topology（工具可执行）
Route Ranking       Phase 5 的 ranking JSON（profiles / tiers / pareto / 版本绑定）
计量                批次 A 的 cost / token 贯通（在线遥测复用同一口径）
可执行工具          批次 B 的 implementation 绑定或 Python 注册路径
执行引擎            Phase 3 的 LayerExecutor / ExecutionState
```

---

# 1. Phase 6 核心问题

本阶段需要回答：

```text
一次请求应该选哪个 Tier 的哪条 Route？

多条同级 Route 之间如何负载均衡？

执行失败时如何在保留的替代路线间有界降级？

每次选择是否都能解释（策略 / 均衡状态 / 降级链）？

在线使用数据如何无损回流下一轮离线优化？

版本不一致的 Catalog 是否被硬性拒绝？
```

---

# 2. Phase 6 非目标

本阶段明确不实现：

```text
HTTP / WebSocket / MCP 服务器（不绑定 Web 框架与外部服务）
认证 / 限流 / 多租户
在线 LLM 路由 / 在线自由探索（在线执行已学到的路线）
在线修改 Topology / 在线剪枝 / 在线重排名
在线业务评估（LLM Judge 仍属离线回归）
Bandit / 强化学习 / 在线学习
分布式状态 / 集群均衡
Circuit Breaker（有界降级已覆盖 MVP；熔断器留待后续）
SLA 契约与执行机制
流式响应
```

三条最重要的纪律：

> 在线阶段不做探索：Route 由离线证据决定，在线只执行。

> 在线阶段不做学习：遥测只是证据，任何 Topology / 排名变更必须回到离线闭环。

> 在线阶段不评估业务质量：降级由执行失败触发，Judge 属于回归。

---

# 3. 核心原则：执行已学的路线

这是全项目的收敛点，必须写死：

```text
Phase 3 在线 = 自由探索（每层 Agent 选择）
Phase 6 在线 = 路线跟随（执行离线选定的路线）
```

理由：拓扑经过 Phase 4 剪枝、路线经过 Phase 5 排名之后，"选择"本身
已经是离线产物。在线再做自由探索，等于放弃了前四个阶段的工作。
在线的自由度只剩三处，且全部有界：

```text
1. Tier 偏好（请求或配置指定 fast / balanced / quality）
2. 同级均衡（多条同级候选间的轮转）
3. 有界降级（执行失败时切换到下一条保留候选）
```

---

# 4. 与前序阶段的关系

## 4.1 消费（只读）

```text
Topology（含可执行工具）
RouteRankingReport JSON（profiles / tier assignments / pareto / 版本）
```

## 4.2 回流（只写证据）

```text
OnlineTelemetry JSONL → 作为下一轮 Phase 3/4/5 的输入之一
```

在线遥测永远不直接触发任何变更。发现某路线在线劣化 →
输出 `review_candidate` 事实 → 走离线 Optimization Round。

---

# 5. RouteCatalog：版本门禁

核心对象：

```python
@dataclass(frozen=True)
class RouteCatalog:
    topology: Topology                      # Active Topology（可执行）
    topology_version: str
    ranking_version_binding: ...            # ranking 携带的版本
    entries: tuple[RouteEntry, ...]         # 可服务的路线
```

## 5.1 构建与加载

- 从 Phase 5 ranking JSON 解析出 `RouteEntry`：

```text
RouteEntry
    route_id / canonical / segments（由 canonical 解析重建）
    tiers: tuple[RouteTier, ...]            # 命中的 Tier（可为空）
    pareto: bool                            # 是否位于前沿
    categories: tuple[str, ...]
    success_rate / ci / latency_median / cost_mean / quality_mean
```

- 路线结构（逐层工具集合）由 `canonical`（`layer:[a,b]` 格式）确定性
  解析重建，并逐一校验工具存在于 Topology 且所在层一致；
  任一工具缺失 → `RouteCatalogError`（fail closed）。

## 5.2 版本门禁（硬性）

```text
catalog 构建时校验：
    ranking.topology_version == topology.version
    ranking.router_config_id 记录在案（供审计）
不一致 → RouteCatalogError，拒绝服务
```

这是 Phase 5 §16 裁定的执行点：**在线只允许加载与其 Active Topology
版本一致的排名**。

## 5.3 候选集查询

```python
def candidates(self, *, category: str | None, tier: RouteTier | None) -> tuple[RouteEntry, ...]
```

- 指定 category 时：仅返回覆盖该 category 的 ranked 路线；
  空集 → 交给上层报 `RouteSelectionError`（fail closed，可解释），
  不静默回退到全局（`allow_global_fallback` 配置可显式放开，默认关）；
- 指定 tier 时：在该 Tier 的路线内筛选；无该 Tier 候选 → 依
  Tier 优先级链降级到下一 Tier（见 §6）；
- Tier 链全部为空时，ranked 但未贴 Tier 标签（UNASSIGNED）的路线
  作为最后一级兜底组——它们同样携带完整证据，不能让有覆盖的
  category 因标签缺失而不可服务。

---

# 6. SelectionPolicy：Tier 偏好与选路

```python
@dataclass(frozen=True)
class OnlineConfig:
    tier_priority: tuple[str, ...] = ("fast", "balanced", "quality")
    allow_global_fallback: bool = False
    max_fallbacks: int = 1                  # 降级尝试上限（0 = 不降级）
```

## 6.1 请求形态

```python
@dataclass(frozen=True)
class OnlineRequest:
    query: str
    category: str | None = None
    tier: str | None = None                 # 请求级偏好，覆盖默认优先级起点
    request_id: str | None = None           # 缺省自动生成
```

## 6.2 选择逻辑（确定性）

```text
1. 候选集 = catalog.candidates(category, tier)
2. 空集 → RouteSelectionError（带 category / tier / 全集大小的原因）
3. 交给 LoadBalancer 在候选内选一条
4. 记录 selection_reason（请求偏好、命中 Tier、均衡状态）
```

同输入同状态 → 同选择。MVP 不做随机；如未来引入加权随机，必须
显式配置且带种子。

---

# 7. LoadBalancer：同级候选间均衡

MVP 实现轮转（Round-Robin）：

```python
class RoundRobinBalancer:
    def pick(self, candidates) -> RouteEntry   # 按 route_id 稳定排序轮转
```

- 轮转状态按 catalog 生命周期持有，per-catalog 确定性；
- balancer 是策略接口：未来可替换加权 / 最少失败优先，但
  权重只能来自离线排名数据（success_rate 等），不得在线自适应；
- 同一请求的降级链选择不消耗轮转位（见 §9）。

---

# 8. OnlineRuntime：路线跟随执行

复用 Phase 3 执行引擎，语义按在线场景收紧：

```text
按 RouteEntry.segments 逐层执行：
    同层多工具 → LayerExecutor 并发（复用）
    部分失败   → 保留成功者继续（phase3 §41 语义不变）
    整层失败   → 该路线执行失败 → 触发降级（§9）
执行完毕    → 汇总 state / latency / cost / token（批次 A 口径）
```

与 Phase 3 的差异（必须遵守）：

```text
无 Router 介入          路线即决策，不做逐层再选择
无 FixtureManager       在线无回归隔离概念
无 Evaluator            业务评估离线进行
timeout 沿用 ExecutionContext.per_tool_timeout_seconds
```

---

# 9. FallbackPolicy：有界降级

```text
路线执行失败后：
    1. 依 tier_priority 构造剩余候选链（同 Tier 未用过的候选优先，
       其次下一 Tier 的候选）
    2. 已尝试的路线从链中移除
    3. 尝试次数 ≤ max_fallbacks；耗尽 → 返回失败结果（含完整降级链）
    4. 每次降级记录原因与新旧路线
```

约束：

- 降级只由**执行失败**触发（tool error / layer error / timeout）；
  业务质量不在线判定；
- 降级链中的每条候选都必须来自 Catalog（版本门禁天然覆盖）；
- 永不无限重试，永不在同一次请求内重复同一条失败路线。

---

# 10. OnlineResult：可解释输出

```python
@dataclass(frozen=True)
class OnlineResult:
    request_id: str
    selected_route_id: str | None           # 最终实际执行的路线
    selection_reason: str                   # 初始选择依据
    fallback_chain: tuple[FallbackStep, ...]  # 每次降级（from/to/cause）
    status: OnlineStatus                    # SERVED / ROUTE_FAILED / NO_CANDIDATE
    state: ExecutionState | None
    latency_ms: float
    cost: float | None                      # execution 口径（tool + routing 无 → 工具）
    token_usage: TokenUsage
    trace: ExecutionTrace | None            # 复用 Phase 3 trace 结构
```

约束：`status=ROUTE_FAILED` 时必须携带完整降级链与最终失败原因；
`NO_CANDIDATE` 必须携带 SelectionError 的原因文本。

---

# 11. OnlineTelemetry：闭环回流

每次请求产生一条遥测记录（JSONL）：

```text
OnlineRecord
    request_id / timestamp
    category / tier_preference
    selected_route_id / status
    latency / cost / token
    fallback_depth
```

用途与边界：

- 聚合成 `OnlineUsageStats`（per-route usage / success / latency / cost），
  格式与 Phase 3 `RouteObservationStats` 对齐，可直接作为下一轮
  Optimization Round 的 evidence 输入之一；
- **不**触发任何在线变更；
- 提供 `has_capacity_concern` 类的纯事实提示（如某路线失败率异常），
  输出为 `review_candidate` 事实而非动作。

---

# 12. 错误模型（追加到 `core/errors.py`）

```text
OnlineRoutingError                # 根（继承 TopologyFrameworkError）
├── RouteCatalogError             # 版本不一致 / 路线结构无法重建 / 工具缺失
└── RouteSelectionError           # 候选集为空 / 偏好无候选
```

执行层错误继续复用 `ExecutionError` 族，不重复定义。

---

# 13. 核心对象汇总

```text
RouteCatalog / RouteEntry
OnlineConfig / OnlineRequest / OnlineResult / OnlineStatus
SelectionPolicy（tier 偏好解析）
RoundRobinBalancer（LoadBalancer 接口的 MVP 实现）
OnlineRuntime（route-following 执行）
FallbackPolicy / FallbackStep
OnlineTelemetry / OnlineRecord / OnlineUsageStats
OnlineRoutingError / RouteCatalogError / RouteSelectionError
```

---

# 14. 推荐目录结构

新增与 `ranking/` 平级的顶包 `online/`：

```text
src/capability_runtime/
├── online/                    # 新增 —— Phase 6 主体
│   ├── __init__.py
│   ├── catalog.py             # RouteCatalog / RouteEntry / canonical 解析重建
│   ├── selection.py           # OnlineConfig / OnlineRequest / SelectionPolicy
│   ├── balancer.py            # LoadBalancer 协议 + RoundRobinBalancer
│   ├── runtime.py             # OnlineRuntime（route-following）
│   ├── fallback.py            # FallbackPolicy / FallbackStep
│   └── telemetry.py           # OnlineRecord / OnlineTelemetry / OnlineUsageStats
│
└── cli.py                     # 追加 `select` 子命令（Step 8）
```

根包统一导出公共符号。

---

# 15. 模块职责

## RouteCatalog

只负责：版本门禁 + 路线重建 + 候选集查询。

## SelectionPolicy

只负责：把（请求偏好, 配置优先级, 候选集）解析成一次初始选择。

## LoadBalancer

只负责：同级候选间确定性分派。

## OnlineRuntime

只负责：执行选定路线并产出可解释结果（含降级）。

## OnlineTelemetry

只负责：记录事实、聚合成离线可消费的证据。

任何模块不得修改 Topology / Ranking，不得调用 LLM。

---

# 16. CLI 与 Demo

## select（干跑选择，不执行）

```bash
tool-topology select \
    --topology examples/topology/refund.json \
    --ranking artifacts/ranking/run_xxx.json \
    [--category refund] [--tier fast] \
    [--format text|json]
```

输出命中的候选集、按当前策略将选中的路线与原因。不执行任何工具。

## 在线服务 Demo（进程内）

`examples/online_refund/serve_demo.py`：

```text
1. 加载 sandbox 拓扑 + 250-trial scale run 的 ranking 产物（版本门禁）
2. 构造 RouteCatalog
3. 服务 N 个请求：Tier 偏好轮换 + 轮转均衡
4. 注入确定性工具失败（复用 sandbox 变体）演示有界降级
5. 输出 OnlineResult 摘要 + 遥测 JSONL + OnlineUsageStats
6. 展示闭环：遥测统计可回流下一轮 optimize
```

---

# 17. 开发顺序

| Step | 模块 | 验收点 |
| --- | --- | --- |
| 1 | `core/errors.py` + `online/catalog.py` | ranking JSON → RouteEntry；canonical 解析重建；版本门禁 fail closed；工具缺失报错 |
| 2 | `online/selection.py` | 请求/配置形态；Tier 偏好链；空候选 RouteSelectionError 带原因 |
| 3 | `online/balancer.py` | 轮转确定性；同状态同选择 |
| 4 | `online/runtime.py` | 路线跟随执行；部分失败继续；计量贯通 |
| 5 | `online/fallback.py` | 有界降级链；不重复失败路线；耗尽返回完整链 |
| 6 | `online/telemetry.py` | JSONL 记录 + OnlineUsageStats 与 Phase 3 统计口径对齐 |
| 7 | 集成：catalog→select→runtime→fallback→telemetry 全链 | 在真实 scale ranking 上服务 |
| 8 | `cli.py` select + `examples/online_refund` | 干跑选择 + 进程内服务 Demo（含失败注入与降级） |
| 9 | 闭环验证 | 遥测聚合 → 作为 evidence 输入 EvidenceAggregator 跑通一次 |

---

# 18. Unit Tests

至少覆盖：

```text
Catalog
  ranking JSON 解析 → entries 完整
  canonical 解析重建 segments，与拓扑工具/层一致
  版本不一致 → RouteCatalogError
  工具缺失 / 层不符 → RouteCatalogError
  category / tier 候选查询；空集行为（fail closed + 配置放开）

Selection
  请求 tier 覆盖默认优先级
  候选为空 → RouteSelectionError 带完整原因
  确定性：同输入同选择

Balancer
  轮转顺序确定且循环
  空候选拒绝

Runtime
  按路线逐层执行，输出 state / latency / cost / token
  同层部分失败继续；整层失败触发降级
  无 Router 介入（结构断言）

Fallback
  依 tier_priority 构链；同请求不重复路线
  max_fallbacks=0 不降级；耗尽返回完整链与最终原因

Telemetry
  记录字段完整；聚合统计与 RouteObservationStats 口径对齐
  只读回流：断言遥测不触碰 catalog / ranking 对象
```

---

# 19. Integration Test

基于批次 E 的 250-trial scale ranking（或测试内现场生成）：

```text
服务 20+ 请求：多 category、多 Tier 偏好
注入 1 类确定性失败 → 验证降级链与最终成功率
遥测聚合后与 EvidenceAggregator 对接跑通（闭环验收）
```

---

# 20. Definition of Done

## Catalog

* [ ] 消费 Phase 5 ranking JSON
* [ ] canonical 重建路线结构并校验拓扑一致
* [ ] 版本门禁 fail closed
* [ ] 候选查询支持 category / tier

## Selection & Balance

* [ ] Tier 偏好（请求级 + 配置级）
* [ ] 空候选可解释拒绝
* [ ] 轮转均衡确定性

## Execution & Fallback

* [ ] 路线跟随执行（无在线探索）
* [ ] 部分失败继续 / 整层失败降级
* [ ] 有界降级、不重复失败路线
* [ ] 计量（latency / cost / token）贯通到结果

## Explainability

* [ ] 每次结果带选择原因
* [ ] 降级链完整可回放
* [ ] NO_CANDIDATE / ROUTE_FAILED 带原因

## Telemetry & Loop

* [ ] JSONL 遥测落盘
* [ ] 聚合统计对齐 Phase 3 口径
* [ ] 闭环：遥测可被 Phase 4 EvidenceAggregator 消费
* [ ] 在线零变更（Topology / Ranking 全程只读）

## Boundary

* [ ] 无 HTTP / MCP / Web 绑定
* [ ] 无在线 LLM 路由与自由探索
* [ ] 无在线学习 / 在线剪枝 / 在线业务评估
* [ ] 无限重试不存在（一切降级有界）

---

# 21. 最终验收场景

用批次 E 真实数据：

```text
Active Topology: sandbox refund v0.4.0（10 工具，可执行）
Ranking:         250-trial scale run 的 rank 产物（41 路线，18 ranked）
```

服务一批请求后必须能回答：

```text
fast 偏好与 quality 偏好各选中了哪些路线？

同 Tier 的多条路线是否被轮转均衡？

注入 read 层失败后，请求是否降级到保留替代路线并最终成功？

降级链是否完整可回放（从哪条到哪条、什么原因）？

每次请求的 latency / cost / token 是否完整记录？

遥测聚合成 per-route 统计后，能否直接喂给 EvidenceAggregator？

版本被篡改的 ranking 是否被 Catalog 拒绝？
```

---

# 22. 核心验收问题

最终只问六个问题。

### 1.

在线执行的是否只有"离线学到的路线"，而没有在线探索？

### 2.

每次选择是否都能解释（偏好、Tier、均衡状态、降级链）？

### 3.

失败降级是否有界、不重复、可回放？

### 4.

Catalog 是否对版本不一致硬性 fail closed？

### 5.

在线遥测是否无损回流离线闭环，而在线自身零变更？

### 6.

整个过程是否不绑定任何 Web 框架 / MCP / 外部服务？

六个答案全部为 Yes，则 Phase 6 核心假设验证成功。

---

# 23. Phase 6 完成后的系统形态

项目的完整闭环正式落地：

```text
Tool Declaration
        ↓
Declared Dense Topology
        ↓
Fast Regression ──┐
        ↓         │
Slow Regression   │  在线遥测回流
        ↓         │
Execution Evidence┤
        ↓         │
Topology Learning │
        ↓         │
Active Topology   │
        ↓         │
Route Ranking     │
        ↓         │
Online Routing ───┘
```

从"人工声明 Tool Graph"到"业务数据驱动的拓扑学习与在线服务"，
Phase 0 立项时的全部假设得到验证。后续方向（多租户服务化、
在线自适应、熔断器、监控体系）属于工程化扩展，不再是本框架的
核心假设验证范围。
