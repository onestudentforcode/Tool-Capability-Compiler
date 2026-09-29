# Phase 6 实施约定 —— 命名与目录结构

> 定位：动手写代码前敲定 Phase 6 的对象命名、包路径、模块职责、错误模型、
> CLI 面与 9 个 Step 的实现顺序。实现以本文件 + phase6.md 语义为准；
> 冲突时命名以本文件为准。

---

## 0. 三条总决定（TL;DR）

1. **新增与 `ranking/` 平级的顶包 `online/`**，不重构既有包。
2. **在线只执行，不探索、不学习**：路线来自 Catalog（Phase 5 ranked
   产物），执行复用 Phase 3 引擎，遥测回流复用 Phase 3/4 统计口径。
3. **闭环适配器是核心交付之一**：`OnlineResult → TrialResult` 的转换
   让在线观测天然成为下一轮回归的输入（Step 9 验收）。

---

## 1. 命名与数据规则

- 领域数据 `@dataclass(frozen=True, slots=True)`；枚举省略 `Enum` 后缀；
  名称集合输出前升序；公共失败用 `core/errors.py` 自定义异常。
- 在线执行层错误复用 `ExecutionError` 族，不重复定义。

---

## 2. 目录结构

```text
src/capability_runtime/
├── online/                    # 新增 —— Phase 6 主体
│   ├── __init__.py
│   ├── catalog.py             # RouteCatalog / RouteEntry / build_catalog
│   │                          # + canonical 解析重建（parse_canonical）
│   ├── selection.py           # OnlineConfig / OnlineRequest / OnlineStatus
│   │                          # + resolve_tier_order / candidate_groups
│   ├── balancer.py            # LoadBalancer 协议 + RoundRobinBalancer
│   ├── fallback.py            # FallbackStep / build_fallback_chain
│   ├── runtime.py             # OnlineRuntime / OnlineResult / OnlineStatus
│   └── telemetry.py           # OnlineRecord / OnlineTelemetry
│                              # + online_results_to_trials（闭环适配）
│
└── cli.py                     # 追加 `select` 子命令（Step 8）
```

根包统一导出公共符号。

---

## 3. 对象 → 模块映射

| phase6.md 对象 | 落点 | 说明 |
| --- | --- | --- |
| `RouteCatalog` / `RouteEntry` | `online/catalog.py` | 消费 ranking JSON（dict）；只收 RANKED 路线 |
| canonical 解析 | `parse_canonical` | `layer:[a,b]` 逐行 → segments；对照拓扑校验 |
| `OnlineConfig` / `OnlineRequest` | `online/selection.py` | tier_priority 默认 `(fast, balanced, quality)` |
| 候选组解析 | `candidate_groups` | 返回按 Tier 排序的 `tuple[tuple[RouteEntry,...],...]` |
| `RoundRobinBalancer` | `online/balancer.py` | LoadBalancer 协议的 MVP；per-catalog 确定性 |
| `OnlineRuntime` / `OnlineResult` / `OnlineStatus` | `online/runtime.py` | route-following；整层失败触发降级 |
| `FallbackStep` / 降级链 | `online/fallback.py` | 有界、去重、不重复失败路线 |
| `OnlineRecord` / `OnlineTelemetry` | `online/telemetry.py` | JSONL 落盘 + 聚合 |
| 闭环适配 | `online_results_to_trials` | OnlineResult → TrialResult（status 映射见 §4） |
| `select` 干跑 | `cli.py::run_select` | 只选路不执行 |

注：`OnlineStatus`（SERVED / ROUTE_FAILED / NO_CANDIDATE）定义在
`runtime.py`（Result 的组成部分），selection 只产异常与原因文本。

---

## 4. 关键口径裁定

- **Catalog 版本门禁**：`build_catalog(topology, ranking, topology_version=...)`
  显式传入 Active Topology 版本，与 ranking JSON 的 `topology_version`
  不一致 → `RouteCatalogError`；CLI `select` 的 `--topology-version`
  缺省取 ranking 自身值（自洽），生产用法必须显式传入；
- **canonical 重建**：逐行 `layer:[tools]`，逗号分隔；空段、未知层、
  工具不在该层 → `RouteCatalogError`；
- **候选组**：指定 category 无覆盖 → 空组，由 selection 汇总判定
  `RouteSelectionError`（fail closed）；`allow_global_fallback=True` 时
  追加无 category 过滤的组；
- **降级链**：`初始候选所在组剩余 + 后续组全部`，跨组按 route_id 去重，
  长度截断到 `max_fallbacks`；初始选择消耗轮转位，降级不消耗；
- **计量**：失败尝试同样计费（批次 A 语义）——cost/token 汇总覆盖
  全部尝试；`latency_ms` 为请求总墙钟；
- **状态映射（闭环）**：`SERVED → COMPLETED`、
  `ROUTE_FAILED → LAYER_ERROR`、`NO_CANDIDATE → ROUTING_ERROR`；
  evaluation=None（在线无业务评估）；
- **执行环境**：`OnlineRuntime` 默认 `ExecutionEnvironment.SANDBOX`，
  可注入；不新增枚举值。

---

## 5. 错误模型（追加到 `core/errors.py`）

```text
OnlineRoutingError                # 根（继承 TopologyFrameworkError）
├── RouteCatalogError             # 版本不一致 / canonical 不可解析 / 工具缺失
└── RouteSelectionError           # 候选集为空 / tier 偏好无候选
```

---

## 6. CLI 面（Step 8）

```bash
tool-topology select \
    --topology topology.json \
    --ranking ranking.json \
    [--topology-version v]      # 缺省取 ranking 自身（自洽模式）
    [--category refund] [--tier fast] \
    [--format text|json]
```

干跑：列出各 Tier 候选组 + 按当前策略将选中的路线与原因；不执行工具。

---

## 7. Step 顺序与验收点

| Step | 模块 | 验收点 |
| --- | --- | --- |
| 1 | `core/errors.py` + `online/catalog.py` | ranking dict → entries；canonical 重建 + 拓扑校验；版本门禁；category/tier 查询 |
| 2 | `online/selection.py` | 请求/配置校验；tier 序解析（请求覆盖起点）；空候选带原因 |
| 3 | `online/balancer.py` | 轮转确定性；空候选拒绝 |
| 4 | `online/runtime.py` | 路线跟随执行；部分失败继续；计量贯通全部尝试 |
| 5 | `online/fallback.py` | 链构造（组内剩余→后续组、去重、截断）；不重复失败路线 |
| 6 | `online/telemetry.py` | 记录 + JSONL + 聚合 + `online_results_to_trials` |
| 7 | 集成链 | catalog → select → runtime(+fallback) → telemetry 全链 |
| 8 | `cli.py` select + `examples/online_refund/serve_demo.py` | 干跑 + 进程内服务（含 not_found 全链耗尽 / erp_down 部分失败继续） |
| 9 | 闭环 | `online_results_to_trials` → `build_observation_stats` → `EvidenceAggregator` 跑通 |

测试约束：全部离线。

---

## 8. 阶段边界检查表

- [ ] 在线只执行已排名路线；无 Router / LLM / 探索
- [ ] 在线零变更：Topology 与 Ranking 对象全程只读
- [ ] 降级有界、去重、可回放
- [ ] 版本门禁 fail closed
- [ ] 不绑 HTTP / MCP / Web 框架
- [ ] 遥测回流只作为离线证据

---

## 9. 实现进度

| Step | 状态 | 落点 |
| --- | --- | --- |
| 1 | [x] | `online/catalog.py`：build_catalog（版本门禁）/ RouteEntry / parse_canonical（严格逐段重建 + 拓扑校验）；只收 RANKED |
| 2 | [x] | `online/selection.py`：OnlineConfig / OnlineRequest / resolve_tier_order（请求偏好领先）/ candidate_groups；Tier 链末尾追加 UNASSIGNED 兜底组（补充裁定，已同步 phase6.md §5.3） |
| 3 | [x] | `online/balancer.py`：RoundRobinBalancer（确定性轮转，空候选拒绝） |
| 4 | [x] | `online/runtime.py`：OnlineRuntime 路线跟随执行；部分失败继续、整层失败降级；全部尝试计费（含失败尝试） |
| 5 | [x] | `online/fallback.py`：组内剩余→后续组、route_id 去重、max_fallbacks 截断 |
| 6 | [x] | `online/telemetry.py`：OnlineRecord / OnlineTelemetry（JSONL + usage_stats）/ online_results_to_trials（状态映射按 §4） |
| 7 | [x] | 集成链于 Demo 与测试中贯通；顺带修复 Phase 5 `to_json` 对 `str, Enum` 的序列化顺序缺陷（Enum 分支提前，否则 tier 标签在进程内 dict 中残留枚举对象） |
| 8 | [x] | CLI `tool-topology select`（干跑，版本门禁 exit 2）；`examples/online_refund/serve_demo.py`（12×3 学习 → 服务 → not_found 全链耗尽 / erp_down 部分失败继续 → 遥测 → EvidenceAggregator 闭环） |
| 9 | [x] | 闭环测试：online_results_to_trials → build_observation_stats → EvidenceAggregator 跑通（node/edge evidence 非空） |

全量 497 tests / compileall / diff-check 通过；测试 `tests/unit/test_online.py`（23 项）。
