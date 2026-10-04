# Phase 5 — Route Evaluation, Ranking & Tiering

## 0. 阶段定位

Phase 5 建立项目的第一套：

> **基于真实执行证据的多维 Route 排名与分级机制。**

此前各阶段的产出：

```text
Phase 1  →  Declared Topology（搜索空间）
Phase 2  →  Theoretical Capability Coverage（理论覆盖）
Phase 3  →  Observed Execution Evidence（执行证据）
Phase 4  →  Safe Pruning（搜索空间收敛为 Active Topology）
```

Phase 4 保留下来的是"有证据支持的搜索空间"，其中同一业务往往仍存在多条并存的
有效 Route。Phase 5 第一次回答：

```text
剩下的这些 Route 中——

哪些更快？
哪些更便宜？
哪些质量更高？
哪些更稳定？
```

并把答案组织为 Phase 6（在线路由 / 负载均衡）可以直接消费的数据：

```text
Route Profile
Route Ranking
Pareto Frontier
Route Tier（Fast / Balanced / Quality）
```

核心流程：

```text
Active Topology（Phase 4 输出）
        ↓
Slow Regression Trace Dataset（Phase 3 产物，只读）
        ↓
Route Profile（success / quality / latency / cost 向量）
        ↓
证据门槛 + 统计可信度
        ↓
Pareto Frontier（多目标支配）
        ↓
Tier Assignment（Fast / Balanced / Quality）
        ↓
RouteRankingReport
        ↓
Phase 6 Route Selection / Load Balancing
```

## 0.1 前置条件：Battlefield Hardening

Phase 5 消费的数据契约由靶场强化里程碑
（[battlefield-hardening.md](battlefield-hardening.md)）交付，完成后
本阶段方可开始实现：

```text
计量      TrialResult.cost（execution 口径）/ token_usage 真实填充；
          quality_score 连续化（不再二值）
工具      sandbox 工具有数据依赖、失败模式与计量差异
场景      ≥ 50 条带 category 标注的业务场景资产
证据      一次真实规模 Slow Regression 的落盘 artifacts
```

在契约未填时实现 Phase 5，只能得到"算法正确但结论为零"的退化排名
（success 趋同 → 全部 TIE；quality 复制 success；latency 为噪声；
cost 为 None）。

---

# 1. Phase 5 核心问题

本阶段需要回答：

```text
每条 Route 的真实成功率是多少？置信区间多宽？

每条 Route 的答案质量（quality_score）分布如何？

每条 Route 的延迟 mean / median / p95 是多少？

每条 Route 的生产成本（routing + tool）是多少？

哪些 Route 在 (success, quality, latency, cost) 四维上互不支配？

同一业务下，哪些 Route 是 Fast 候选、Balanced 候选、Quality 候选？

哪些 Route 样本不足，暂时没有资格参与排名？

不同业务类别（category）下，Route 优劣是否不同？
```

---

# 2. Phase 5 非目标

本阶段明确不实现：

```text
在线 Route Selection
在线 Load Balancing
Bandit / 强化学习 / 在线学习
自动修改 Topology（Phase 4 职责）
自动改写 Tool / Prompt / Description
新建执行引擎或新的 Trace 格式（复用 Phase 3）
时间序列预测与漂移检测
Prometheus / 监控 / 告警
生产流量接入
```

尤其需要明确的两条纪律：

> Phase 5 是 Ranker，不是 Optimizer：只读证据、只出排名，不修改任何 Topology。

> Phase 5 不裁决唯一 Best Route：输出的是向量、前沿与分级，不是冠军。

如果排名显示某条 Route 长期劣于替代 Route，Phase 5 只能输出
`review_candidate` 事实；是否进入剪枝必须回到 Phase 4 的完整流程
（新的 Optimization Round）。

---

# 3. 核心原则：多维向量不降维

这是 Phase 0 §11 就定下、Phase 5 正式兑现的原则：

```text
评估向量 =
    success（业务成功率）
    quality（答案质量）
    latency（端到端延迟）
    cost（生产成本）
```

必须遵守：

1. 排名对象是 **Route（route_id）**，不是单个 Tool；
2. 四维向量完整保留，**禁止过早压缩成一个综合分数**；
3. 任何加权总分只允许作为"报告展示顺序"的辅助，不得作为 Tier 判定依据，
   不得作为对外结论；
4. 允许并列（STATISTICAL_TIE）、允许不可比（维度缺失）、允许无 Tier
   （UNASSIGNED）——不强行给每条 Route 一个名次。

这与 Phase 4 的 Route Diversity Guard 一脉相承：搜索空间保留多 Route 的目的，
就是让不同 Tier 各自成立，而不是决出一个全局第一。

---

# 4. 与 Phase 4 的边界

```text
Phase 4 回答：哪些搜索空间可以安全删除？
Phase 5 回答：剩下的搜索空间里，各条 Route 表现如何？
```

边界约束：

- Phase 5 消费 Phase 3 的 Trace 与 Phase 4 的 Active Topology，均为只读；
- Phase 5 不产生 TopologyPatch、不生成 Candidate、不触发 Validation Gate；
- "某 Route 应该被淘汰" 属于 Phase 4 判断；Phase 5 只提供支撑该判断的
  指标事实；
- 反向衔接：Phase 5 发现的 INSUFFICIENT_EVIDENCE Route，可以通过 Phase 4
  的 ProbeRunner（basefast 定向 seed）补证据，Phase 5 自己不执行 Trial。

---

# 5. 核心输入

Phase 5 只消费既有产物，不重造数据源：

```text
TrialResult 序列（或其 JSONL 反序列化形态）
ObservationReport.route_stats（RouteObservationStats）
SlowRegressionReport（版本绑定信息）
Scenario suite（category 维度归组）
```

## 5.1 版本一致性（可比性前提）

一次排名内的全部 Trial 必须来自同一：

```text
topology_version
router_config_id
scenario suite name + version
```

混入不同版本的 Trace 会得出不可比结论，必须抛 `RankingError`
（见 §21），而不是静默合并。

## 5.2 RouteRankingReport 的版本绑定

报告必须携带：

```text
topology_version
router_config_id
suite_name + suite_version
数据来源 run id（Slow Regression manifest）
RankConfig / TierConfig 摘要
```

不同版本之间不做自动比较；跨版本对比属于人工分析，不在本阶段自动化。

---

# 6. 成本与延迟口径

## 6.1 成本双口径

延续 Phase 3 §80 的裁定：

```text
execution_cost   = routing_cost + tool_cost      → 排名使用
evaluation_cost  = judge / evaluator 成本        → 只审计，不进排名
```

成本双口径的计量由靶场强化里程碑批次 A 交付
（`TrialResult.tool_cost / routing_cost / evaluation_cost / cost`）；
在此之前的实现中 `TrialResult.cost` 尚未被填充。Phase 5 的
RouteProfile 必须基于 `execution_cost`；`evaluation_cost` 可以随
profile 附带，但绝不参与 Pareto 与 Tier 计算。原因：LLM Judge 是
回归测试成本，不是未来生产 Route 的执行成本。

## 6.2 延迟取实测值

```text
Route Latency = Trial 实测 latency_ms
```

直接取 Phase 3 的 Trial 级实测值。它天然包含了同层并行（≈ max）与
逐层串行的真实效果。**禁止**用 `Σ tool latency` 理论合成——那会高估
并行层的耗时。

## 6.3 Token 用量

Router + Tool 的 token 汇总作为辅助指标记录（Phase 6 在线路由的
容量规划会用到），默认不参与 Pareto 目标（成本维度已隐含）。

---

# 7. RouteProfile

Phase 5 的核心数据对象：

```python
@dataclass(frozen=True)
class RouteProfile:
    route_id: str

    # 样本
    trial_count: int
    completed_count: int
    scenario_count: int
    categories: tuple[str, ...]            # 出现过的业务类别，升序

    # success 维度
    business_success_count: int
    business_success_rate: float
    success_confidence_interval: tuple[float, float]   # Wilson

    # quality 维度（无评估时为 None）
    quality_mean: float | None
    quality_median: float | None

    # latency 维度（ms）
    latency_mean: float | None
    latency_median: float | None
    latency_p95: float | None

    # cost 维度（execution 口径）
    cost_mean: float | None
    cost_median: float | None

    # 辅助指标
    token_usage: TokenUsage

    # 结构属性（Phase 2 已允许记录的仅有的结构性事实）
    tool_count: int
    layer_count: int

    # 版本绑定
    topology_version: str
    router_config_id: str
```

构建规则：

- 从 `TrialResult` 序列（或落盘 stats）按 `route_id` 聚合；
- `business_success_rate` 的分母是 **有 evaluation 结果** 的 Trial，
  执行失败的 Trial 计入 `completed_count` 差值但不稀释成功率语义；
- `quality_*` 只有在至少一个 Trial 产出 `quality_score` 时才有值，
  否则为 `None` 并在报告中标记 `quality_missing`；
- `latency / cost / quality` 的描述统计复用 Phase 3 的
  `summarize()`（mean / median / p95）口径，保证跨阶段一致。

---

# 8. 证据门槛（RankingEligibility）

排名必须有最低证据量，否则"看起来更快"只是噪声。

## 8.1 RankConfig

```python
@dataclass(frozen=True)
class RankConfig:
    min_trials: int = 20            # 进入排名的最低 Trial 数
    confidence_level: float = 0.95  # Wilson 区间置信水平
```

默认值只是配置，不写死在算法里。

## 8.2 资格状态

```text
RANKED                 trial_count >= min_trials，参与 Pareto 与 Tier
INSUFFICIENT_EVIDENCE  trial_count <  min_trials，不参与，只列清单
```

INSUFFICIENT_EVIDENCE 的 Route 必须出现在报告中，并注明：

```text
trials 7 / 20（还差 13）
```

这延续了 Phase 4 "证据不足不裁决" 的纪律：样本不足的 Route 既不奖励
（排进前列）也不惩罚（判为劣质），只是"还没资格说话"。

## 8.3 补证据路径

INSUFFICIENT_EVIDENCE → 运行更多 Phase 3 Slow Regression（free /
basefast），或交给 Phase 4 ProbeRunner 定向探索。Phase 5 自身不执行。

---

# 9. 统计可信度

## 9.1 成功率必须带区间

`business_success_rate` 必须附带 Wilson 置信区间：

```text
success = 96.4%  [91.2%, 98.6%]  (n = 212)
```

样本量越小，区间越宽——报告不得只展示点估计。

## 9.2 STATISTICAL_TIE

两条 Route 的成功率置信区间重叠时，二者在该维度为：

```text
STATISTICAL_TIE
```

处理规则：

- 报告中并列展示，不强行按点估计排序出先后；
- Tier 判定中的 success 比较遇到 TIE 时视为"不可区分"，交由该 Tier 的
  其他维度规则决定。

## 9.3 描述统计的纪律

quality / latency / cost 使用 mean / median / p95 描述性统计。MVP 不做
假设检验，但必须同时展示样本量，任何只给均值不给 n 的展示都是违规。

---

# 10. Pareto Frontier

## 10.1 目标向量

默认四个目标：

```text
success_rate   ↑  maximize
quality_mean   ↑  maximize（缺失则退出支配判定）
latency_median ↓  minimize
cost_mean      ↓  minimize（缺失则退出支配判定）
```

延迟用 median（对离群鲁棒），p95 仍展示；成本用 mean。

## 10.2 支配定义

```text
Route A 支配 Route B
IFF
在所有可比维度上 A >= B（方向按目标）
AND
至少一个维度上 A > B
```

不可比的维度（一方缺失）不参与该次支配判定，并在结果上标注
`partial_comparison`。

## 10.3 输出

```text
ParetoFrontier
    frontier:   互不支配的 route_id 集合
    dominated:  每个被支配 route_id → 支配它的 route_id 列表
```

被支配者必须保留"被谁支配"的归因，这是报告可解释性的最低要求：

```text
read:[rag] analyze:[refund_policy_check]
    dominated by read:[orderdb] analyze:[refund_policy_check]
```

## 10.4 作用域

Pareto 分两个作用域计算：

```text
global     全部 RANKED Route
category   每个 category 内的 RANKED Route（Route 只在其出现过的
           category 中参与）
```

一条 Route 可以在 global 被支配、在某个 category 内位于前沿——这正是
"稀有业务路径"在排名维度的表达。

---

# 11. Route Tier

## 11.1 TierConfig

```python
@dataclass(frozen=True)
class TierConfig:
    # success 底线（绝对值，超出即无资格进任何 Tier）
    success_tolerance: float = 0.02

    # FAST：延迟优先
    fast_latency_tolerance: float = 0.20      # 相对最优 latency 的容忍倍数

    # QUALITY：质量优先
    quality_tolerance: float = 0.05           # 相对最优 quality 的绝对差

    # BALANCED：无明显短板
    balanced_latency_tolerance: float = 0.50
    balanced_quality_tolerance: float = 0.10
    balanced_cost_tolerance: float = 0.50
```

全部阈值集中在 TierConfig，禁止散布在算法中（与 Phase 4 PruningConfig
同一纪律）。

## 11.2 判定规则

设参与排名（RANKED）Route 中的各维最优值：

```text
best_success   = max(success_rate)
best_latency   = min(latency_median)
best_quality   = max(quality_mean)   # 无任何 quality 时 QUALITY 空缺
best_cost      = min(cost_mean)
```

### FAST

```text
latency_median <= best_latency × (1 + fast_latency_tolerance)
AND
success_rate   >= best_success − success_tolerance
```

### QUALITY

```text
quality_mean   >= best_quality − quality_tolerance
AND
success_rate   >= best_success − success_tolerance
```

### BALANCED

```text
latency_median <= best_latency × (1 + balanced_latency_tolerance)
AND
quality_mean   >= best_quality − balanced_quality_tolerance
AND
cost_mean      <= best_cost × (1 + balanced_cost_tolerance)
AND
success_rate   >= best_success − success_tolerance
```

### UNASSIGNED

不满足任何 Tier 条件的 Route 保持 UNASSIGNED——它仍然出现在报告的
向量表中，只是没有分级标签。不硬塞、不四舍五入。

## 11.3 多标签允许

Tier 判定相互独立。一条全面占优的 Route 可以同时命中：

```text
FAST + QUALITY + BALANCED
```

这是特性不是缺陷：它说明该 Route 在多个维度同时接近最优。报告按
Tier 分组展示时，多标签 Route 在每个命中的分组各出现一次，并标注
`also: quality, balanced`。

## 11.4 可解释性

每个 Tier 分配必须携带：

```text
RouteTierAssignment
    route_id
    tiers: tuple[str, ...]          # 命中的全部 Tier
    metrics_snapshot                # 判定时的关键指标值
    matched_rules: tuple[str, ...]  # 命中的规则文本（含阈值）
```

禁止只输出 `tier=fast` 而不给依据。

---

# 12. Route Family

Phase 4 §81 预告的结构相似 Route 聚类，在本阶段以最小形态落地：

```text
MVP       route_id 即 family（与 Phase 4 Route Diversity Guard 同口径）
可选增强   基于逐层工具集合结构相似度（Jaccard）的归并，
          仅用于多样性展示与 Tier 表内去重提示
```

约束：

- 聚类是展示辅助，不改变排名、Pareto 与 Tier 的判定单位（仍是 route_id）；
- 不做语义聚类（不调 LLM）；
- 相似度阈值进配置，不写死。

---

# 13. Category / Scenario 维度

Route 的优劣依赖业务类型：

```text
全局排名 + 逐 category 排名
```

规则：

- Route 只在其**实际出现过**的 category 中参与该 category 的 Pareto /
  Tier；
- 报告必须展示每条 Route 的业务覆盖：

```text
read:[orderdb] analyze:[refund_policy_check] act:[refund]
    scenarios 41   categories: refund, order
```

- category 维度的 best_* 只在该 category 的 RANKED Route 内取值，
  不与全局混算；
- 允许输出"该 category 内证据不足"清单。

---

# 14. 报告示例

```text
Route Ranking

Topology: v5        Router: llm        Suite: customer_service 3.0
Source run: run_20260904_001          Min trials: 20

Ranked Routes: 9 / 14

Tier QUALITY
  read:[orderdb] analyze:[refund_policy_check] act:[refund]
      success 97.6% [95.1, 99.1]  quality 0.94  latency 812ms (p95 1.2s)
      cost $0.014  trials 212  pareto: yes  also: balanced

  read:[orderdb,rag] analyze:[refund_policy_check] act:[refund]
      success 98.1% [95.0, 99.4]  quality 0.95  latency 1.6s (p95 2.4s)
      cost $0.031  trials 198  pareto: yes  also: balanced

Tier FAST
  read:[cache] analyze:[refund_policy_check] act:[refund]
      success 96.9% [93.4, 98.7]  quality 0.91  latency 420ms (p95 0.8s)
      cost $0.009  trials 175  pareto: yes

Tier BALANCED
  read:[orderdb] analyze:[risk_check,refund_policy_check] act:[refund]
      success 97.2% [94.0, 98.8]  quality 0.93  latency 980ms (p95 1.5s)
      cost $0.017  trials 160  pareto: no
      dominated by: read:[orderdb] analyze:[refund_policy_check] act:[refund]

UNASSIGNED
  read:[websearch] analyze:[refund_policy_check] act:[refund]
      success 88.4% [82.1, 92.9]  quality 0.86  latency 2.4s (p95 3.8s)
      cost $0.048  trials 141  pareto: no

Pareto Frontier: 4 members / 5 dominated (4 objectives)

Insufficient Evidence (5)
  read:[erp] analyze:[refund_policy_check] act:[refund]
      trials 7 / 20

(Phase 5 ranks only; route selection belongs to Phase 6.)
```

`RouteRankingReport` 结构化字段至少包括：版本绑定信息、
`profiles: tuple[RouteProfile, ...]`、`eligibility`、`pareto`、
`tier_assignments`、`insufficient`、`category_breakdown`，并提供
JSON 序列化与 text 渲染两种输出。

---

# 15. CLI

```bash
tool-topology rank \
    --slow-report artifacts/slow_regression/run_20260904_001 \
    [--category refund] \
    [--min-trials 20] \
    [--format text|json] \
    [--out artifacts/ranking/run_20260904_001.json]
```

说明：

- `--slow-report` 指向 Phase 3 落盘目录（读取 report.json +
  route_stats / traces），而不是重新执行；
- `--category` 只输出该类别的排名；
- 退出码恒为 0（排名不是 CI 门禁）；证据不足清单只是信息，不是失败。

---

# 16. 与 Phase 6 的接口

Phase 5 的产出是 Phase 6 在线路由的直接输入：

```text
Tier 表          →  按 SLA 选 Route 的候选集
Pareto Frontier  →  无退化替换（swap）的安全边界
Route Profile    →  容量规划 / 成本预算 / 降级顺序的依据
```

因此所有排名产物必须可无损序列化（JSON），且版本绑定字段完整——
Phase 6 只允许加载与其 Active Topology 版本一致的排名。

---

# 17. 核心对象汇总

```text
RouteProfile
RankConfig
RankingEligibility（RANKED / INSUFFICIENT_EVIDENCE）
ParetoFrontier
RouteTierAssignment（FAST / BALANCED / QUALITY / UNASSIGNED，可多标签）
TierConfig
RouteFamily（MVP: route_id）
RouteRankingReport
RankingError / RouteProfileError / RankingConfigError
```

---

# 18. 推荐目录结构

Phase 5 新增与 `optimization/` 平级的顶包 `ranking/`：

```text
src/capability_runtime/
├── ranking/                  # 新增 —— Phase 5 主体
│   ├── __init__.py
│   ├── profile.py            # RouteProfile + build（成本双口径）
│   ├── eligibility.py        # RankConfig / RankingEligibility / 版本一致性
│   ├── stats.py              # Wilson 区间 / tie 判定
│   ├── pareto.py             # 支配判定 / ParetoFrontier
│   ├── tier.py               # TierConfig / RouteTierAssignment
│   ├── family.py             # RouteFamily（MVP: route_id）
│   └── report.py             # RouteRankingReport + build + render + JSON
│
└── cli.py                    # 追加 `rank` 子命令
```

根包 `capability_runtime/__init__.py` 统一导出以上公共符号。

---

# 19. 模块职责

## ProfileBuilder

只负责：

```text
TrialResult / 落盘 stats
→
RouteProfile 向量
```

## EligibilityChecker

只负责：

```text
Profile + RankConfig + 版本一致性
→
RANKED / INSUFFICIENT_EVIDENCE / RankingError
```

## ParetoAnalyzer

只负责：

```text
RANKED profiles
→
frontier + dominated（含归因）
```

## TierAssigner

只负责：

```text
RANKED profiles + TierConfig
→
可解释的 Tier 分配
```

## RankingReportBuilder

只负责：

```text
以上全部
→
RouteRankingReport（text / json）
```

任何模块都不得修改 Topology、不得执行 Tool、不得调用 LLM。

---

# 20. 错误模型（追加到 `core/errors.py`）

```text
RankingError                     # 根（继承 TopologyFrameworkError）
├── RouteProfileError            # 聚合失败 / 计数不一致 / 数据缺失
└── RankingConfigError           # RankConfig / TierConfig 参数非法
```

版本混杂（§5.1）抛 `RouteProfileError` 并指明冲突的版本对；
`min_trials <= 0`、tolerance 越界等抛 `RankingConfigError`。

---

# 21. 开发顺序

严格按 Step 推进，每步交付后跑全量 `pytest` + `compileall`，按 AGENTS
分批提交；不提前实现下一 Step。

| Step | 模块 | 验收点 |
| --- | --- | --- |
| 1 | `ranking/profile.py` | RouteProfile 聚合：计数 / 成功率 / 三维统计 / token / 结构属性；成本双口径（evaluation cost 不进 execution cost） |
| 2 | `ranking/eligibility.py` | min_trials 门槛；版本一致性校验（混杂 → `RouteProfileError`）；INSUFFICIENT_EVIDENCE 清单含差额 |
| 3 | `ranking/stats.py` | Wilson 区间；区间重叠 → STATISTICAL_TIE |
| 4 | `ranking/pareto.py` | 四维支配 / 互不支配；quality 缺失维度退出判定并标注；被支配者带归因 |
| 5 | `ranking/tier.py` | FAST / BALANCED / QUALITY / UNASSIGNED；多标签；每个 assignment 带指标快照 + 命中规则；阈值全在 TierConfig |
| 6 | `ranking/family.py` | MVP route_id 即 family；可选结构相似归并（仅展示） |
| 7 | `ranking/report.py`（category） | 逐 category 的 profile / pareto / tier；Route 业务覆盖展示 |
| 8 | `ranking/report.py` | RouteRankingReport 构建 + text 渲染 + JSON 序列化（供 Phase 6 无损消费） |
| 9 | `cli.py` + Demo | `tool-topology rank` 子命令；离线集成 Demo（复用 `examples/slow_refund` 或合成 trace） |

测试约束：单元测试全部离线，不依赖真实 LLM / 网络 / 真实 Tool 执行。

---

# 22. Unit Tests

至少覆盖：

```text
Profile
  聚合计数正确（trials / completed / success / failure）
  成本口径：evaluation cost 不进入 execution cost
  quality 缺失 → None + quality_missing 标记
  latency 直接取 Trial 实测（含并行层样本）

Eligibility
  trial_count 达标 / 不达标
  版本混杂（topology / router / suite 任一不同）→ RankingError
  min_trials <= 0 → RankingConfigError

Stats
  Wilson 区间数值正确
  区间重叠 → STATISTICAL_TIE；不重叠 → 可区分

Pareto
  支配与互不支配
  quality 缺失时降维判定 + partial_comparison 标注
  被支配者归因正确

Tier
  FAST / QUALITY / BALANCED 各自命中与未命中
  多标签（同时命中多 Tier）
  UNASSIGNED 保留在报告中
  assignment 携带指标快照与规则文本
  tolerance 非法 → RankingConfigError

Family
  MVP：route_id 一一对应
  结构相似归并（若实现）确定性、阈值可配

Report / CLI
  text 渲染包含全部区块
  JSON 序列化往返无损
  rank 子命令离线运行
```

---

# 23. Integration Test

基于 `examples/slow_refund` 数据或确定性合成 Trace：

```text
≥ 3 个 Route family
每条 RANKED Route ≥ 20 Trials
至少 1 条 INSUFFICIENT_EVIDENCE Route
至少 2 个 category
```

验证：

```text
RouteRankingReport 完整（Tier / Pareto / 不足清单 / category）
JSON 落盘可往返
同一输入两次运行输出完全一致（确定性）
```

---

# 24. Definition of Done

## Profile

* [ ] 四维向量完整（success / quality / latency / cost）
* [ ] 成本双口径，排名只消费 execution cost
* [ ] 延迟取 Trial 实测，不理论合成
* [ ] token 用量记录为辅助指标

## Evidence

* [ ] min_trials 门槛生效
* [ ] INSUFFICIENT_EVIDENCE 不进 Pareto / Tier，只列清单
* [ ] 版本一致性强制（混杂报错）
* [ ] 报告绑定 topology / router / suite 版本与来源 run

## Statistics

* [ ] 成功率附带 Wilson 区间与样本量
* [ ] 区间重叠 → STATISTICAL_TIE 并列

## Pareto

* [ ] 四目标支配判定
* [ ] 缺失维度退出判定并标注
* [ ] frontier + dominated（含归因）
* [ ] global 与 category 两个作用域

## Tier

* [ ] FAST / BALANCED / QUALITY / UNASSIGNED
* [ ] 多标签允许
* [ ] 每个分配可解释（快照 + 规则）
* [ ] 阈值全部集中在 TierConfig

## Boundary

* [ ] 不执行 Tool / 不调 LLM / 不修改 Topology
* [ ] 不裁决唯一 Best Route
* [ ] 不做在线选择与负载均衡（Phase 6）
* [ ] 输出可被 Phase 6 无损消费

---

# 25. 最终验收场景

在 Phase 4 产出的 Active Topology 上执行：

```text
200 Scenarios × 10 Trials = 2000 Trials
≥ 10 条 RANKED Route
≥ 2 个 category
```

Phase 5 完成后必须能够回答：

```text
每条 Route 的四维 profile（含区间与样本量）是什么？

哪些 Route 位于 Pareto 前沿？谁被谁支配？

FAST / BALANCED / QUALITY 三个候选集各是哪些 Route？

哪些 Route 样本不足？差多少？通过什么机制补证据？

refund 与 order 两个 category 的排名是否不同？

排名结果绑定哪个 Topology / Router / Suite 版本？
```

---

# 26. 核心验收问题

最终只问六个问题。

### 1.

排名是否始终保持四维向量，而没有压缩成单一综合分数？

### 2.

证据不足的 Route 是否被明确隔离，而不是凭少量样本占据名次？

### 3.

统计上不可区分的 Route 是否并列，而不是被强行排出先后？

### 4.

每一个 Tier 分配是否都能回答"凭什么"（指标快照 + 命中规则）？

### 5.

排名是否严格绑定 Topology / Router / Suite 版本，跨版本不可混？

### 6.

输出（Tier 表 + Pareto 集 + 完整 Profile）是否可被 Phase 6 直接、
无损地消费？

六个答案全部为 Yes，则 Phase 5 核心假设验证成功。

---

# 27. Phase 5 完成后的下一步

Phase 6：

> **Online Routing**

开始消费：

```text
Active Topology（Phase 4）
Route Ranking（Phase 5）
```

回答：

```text
一次线上请求应该选哪个 Tier 的哪条 Route？
多条同级 Route 之间如何负载均衡？
失败时如何在保留的替代 Route 间降级？
```

Phase 5 的使命始终保持：

> **不替系统做选择，而是让每一次选择都有完整、可信、可解释的依据。**
