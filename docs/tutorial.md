# 项目教程：Tool-Capability-Compiler 到目前为止做了什么

> 面向读者：刚接触本仓库的开发者 / Agent。目标：让你在 45 分钟内理解这个框架"为什么存在、解决什么问题、已经做完了什么（Phase 0–6 全部完成，含靶场强化里程碑，主循环已闭合）"。

***

## 0. 一句话先说它是什么

> **Graph 不是答案，Graph 是允许 Agent 搜索的空间。**

这个项目是做给 **AI Agent 的工具调用** 的分层路由与拓扑优化框架。它不帮你"写死一条工作流"，而是让你用"业务场景"持续对工具调用能力做 **验证 → 评估 → 差分 → 收敛**，最终把一个声明好的拓扑（Declared Topology）优化成真正好用、可回归检测的拓扑。

名字里的 `Tool-Capability-Compiler` 透露了核心动机：**把"工具"和"业务能力"分开，用能力作为中间语言**，让 Agent、Layer、拓扑都能在统一的能力维度上对齐。

***

## 1. 它解决的痛点

假设你现在要用 Agent 处理"订单退款"：

- 你有数据库工具 `db`、策略文档工具 `rag`、退款执行工具 `refund`…（**Tools**）

- 你要让 Agent 处理"查订单 → 查退款策略 → 判断可退 → 执行退款"（**一个流程**）

多数框架的做法是：把流程硬编码成一个 DAG 或一条 tool chain。缺点：

1. **答案被预先算死** —— 新增一个工具就改一次逻辑；
2. **能力不可验证** —— 你无法回答"我到底有没有能力处理这类请求？缺什么？"；
3. **回归不可感知** —— 今天能办的，拓扑改一次之后可能悄悄办不了了；
4. **LLM 参与太多** —— 把昂贵的、不确定的 LLM 用来干确定性的事情。

本项目的应对是分三层的"中间抽象"：

```
Tool（具体实现）  ←→  Capability(能力)  ←→  Scenario(业务场景)
```

并给出两个关键分离，贯穿始终。

***

## 2. 两条贯穿全部实现的核心原则

### 2.1 必须区分两种拓扑

| <br /> | Declared Topology（声明的）              | Active Topology（活跃的） |
| ------ | ----------------------------------- | -------------------- |
| 是什么    | 由 `layer / provider / worker` 声明出来的 | 由真实回归结果统计出来的         |
| 来源     | 人工/配置                               | 执行与评估的沉淀             |
| 立场     | 可能性空间                               | 已被证明有效的              |
| 时机     | 现在就建                                | 未来才派生的东西             |

**项目文档反复强调：这两者必须分开，绝不能混在同一个对象里。** 因为"我号称连接了 db 和 refund"和"db 真的喂得了 refund"是两回事。

### 2.2 必须把「LLM」和「确定性流程」分开

LLM 在 Phase 2 只允许干一件事：

```text
User Query  →  业务能力(Capability)    ← LLM 的"翻译工作"
```

而下面的所有流程 **必须是确定性代码，禁止 LLM 参与**：

```text
Topology Traversal     边遍历
Edge Validation        边校验
Route Search           候选路由搜索
Coverage Calculation   覆盖率计算
```

原因：可复现、省成本、可测试。能 100% 确定的逻辑，为什么要交给随机模型？

***

## 3. Phase 0 —— 先定"宪法"

`docs/acceptance/phase0.md` 是全局纲领，定义了地基对象，并且立了三道"边界"：

**（1）一条边是怎么来的？**

> Edge 由 `Layer + Provider + Worker` 决定。

不是靠 `consumes / produces` Schema 自动建边。Schema（`consumes / produces`）**只负责验证"已经声明存在的边"是否合法**，不负责自动连边。

**（2）Route 是什么？**

> Route 是允许同层多个 Tool 的执行子图，不是严格的一条 chain。

强调的是"同层可选性"，而不是单链。

**（3）哪些旧主线被废弃了？**
Phase 0 明确禁止恢复下面这套旧架构（初版误走的路）：

```text
ArtifactKey / Goal(produces=...) / Producer Resolution
Backward Planner / Minimal Dependency DAG / Provider Priority Planner
```

这套旧主线的错误在于：把"预先算出一个最小的任务 DAG"当目标。方向相反——**Graph 要保留搜索空间，不是压缩成一个答案**。

***

## 4. Phase 1 —— 模型层（Declared Topology 的骨架）

目标：用 `layer / provider / worker` 声明出一个 **可约束、可检查、可供搜索** 的拓扑。只建模、**不执行**。

核心概念对应到代码（`src/capability_runtime/`）：

| 领域对象                             | 含义                                          | 关键文件                  |
| -------------------------------- | ------------------------------------------- | --------------------- |
| `Layer`                          | 执行分层（read / analyze / act）                  | `core/layer.py`       |
| `ToolNode` / `ToolSpec`          | 工具节点，含 layer、providers、workers、capabilities | `core/tool.py`        |
| `ToolEdge`                       | 一条有向允许边                                     | `topology/models.py`  |
| `ToolRegistry` / `LayerRegistry` | 唯一性校验的注册表                                   | `registry/`           |
| `TopologyBuilder`                | 按分层与白名单建边                                   | `topology/builder.py` |

**Phase 1 最体现设计的地方：双向白名单取交集建边。**

以集成测试 `tests/integration/test_refund_topology.py` 的 7 个工具为例：

```text
read  层:  db ──(worker=[policy_check])──► policy_check
            rag ──(worker=[policy_check])──► policy_check
analyze 层: policy_check ──(worker=[refund])──► refund
            summoner...（不许再出边 workers=[]）
```

兴趣点（白名单的三种玩法）：

```python
# 只允许流向 policy_check
@tool(layer="read", workers=["policy_check"])
async def db(): ...

# 只接收来自 db/rag 的数据，且只能流向 refund
@tool(layer="analyze", providers=["db","rag"], workers=["refund"])
async def policy_check(): ...

# 用空集合表达"不可能"
@tool(layer="analyze", workers=[])           # 禁止出边
@tool(layer="act", providers=[])            # 禁止入边
```

校验点包括：**跨层引用被拒绝**（`db` 不能直接连 `act` 层的工具）、Schema 告警、多节点 Route 等。

***

## 5. Phase 2 —— Fast Regression（元数据验证，不执行）

Phase 2 建立了第一套业务能力验证机制，其本质是：

> **Capability Coverage Test（能力覆盖测试）。它不是执行测试，是"元数据侧"的覆盖测试。**

它只吃什么？—— `Tool Metadata + 当前拓扑 + 业务场景`。它 **不真实执行任何 Tool**（这也解释了为什么叫"Fast"）。

### 5.1 三个状态（Step 3–5）

对每个业务场景，判定它的覆盖状态：

```text
COVERED     能力齐 + 拓扑连得上
UNCERTAIN   能力齐但不确定（例如能力可信度低，或纯 query 未解析）
UNCOVERED   缺能力(Capability Gap) 或 有工具但连不上(Topology Gap)
```

关键是在 UNCOVERED 里再细分两类缺口：

| 缺口类型               | 含义          | 例子                            |
| ------------------ | ----------- | ----------------------------- |
| **Capability Gap** | 系统里根本没有这个能力 | 场景要 `invoice.send`，但没有工具声明它   |
| **Topology Gap**   | 能力存在，但边连不上  | 有 `refund.execute`，但没有任何边能到达它 |

### 5.2 Gold Mode vs Discovery Mode（Step 6–7）

场景分为两类，决定要不要花钱调用 LLM：

```text
带 expected_capabilities 的人工标注场景  ──►  Gold Mode   （确定性，零成本，走 CI）
只有一句自然语言 query 的场景          ──►  Discovery（Query → LLM → Capability）
```

你永远想**优先用 Gold Mode**：可复现、快、免费。只在面对真实未标注的 query 时才动用 LLM。

### 5.3 具体实现的 7 个 Step

| Step | 内容                                                                  |
| ---- | ------------------------------------------------------------------- |
| 1    | Tool capability + `CapabilityRegistry`（能力为 lowercase dot-separated） |
| 2    | `Scenario` + `ScenarioSuite` + `ScenarioLoader`                     |
| 3    | Gold Mode Coverage Analyzer（COVERED / UNCERTAIN / UNCOVERED）        |
| 4    | Candidate Route Search（保留多样性的候选子图，不选唯一最优解）                          |
| 5    | 完整的 Failure Reason（为什么未覆盖）                                          |
| 6    | Coverage Report + Category / Capability / Topology Gap 报表           |
| 7    | `CapabilityResolver` Protocol + Fake Resolver（先抽象接口）                |

***

## 6. Phase 2 的后三块 —— Step 8/9/10

### 6.1 Step 8：给 Discovery 接上真实 LLM（`OllamaCapabilityResolver`）

Step 7 只搭了抽象接口和假实现。Step 8 把假实现换成真的，用 **本地 Ollama +** **`qwen3:1.7b`**，配置从 `.env` 读取：

```bash
# .env（已在 gitignore，不含密钥）
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=qwen3:1.7b
LLM_TIMEOUT_SECONDS=60
```

实现要点（`src/capability_runtime/capability/ollama_resolver.py`）：

- **零依赖**：标准库 `urllib` + 自带的轻量 `.env` 加载器；

- 走 Ollama 的 OpenAI 兼容端点 `/v1/chat/completions`，强制 `response_format: json_object` **结构化输出**（禁止"自然语言 + 正则解析"那条老路）；

- 只做 Query→Capability；`required/optional` **被约束在** **`available_capabilities`** **内**，缺口只能进 `missing_capability_hints`（禁止模型自由发明能力）；

- 构造器可显式覆盖，也可注入假 `_http` 让测试不触网。

实测（连真 Ollama）：

| Query             | required                           | hints          | confidence |
| ----------------- | ---------------------------------- | -------------- | ---------- |
| 查一下订单123能不能退款     | `order.read`,`refund.policy.check` | —              | 1.0        |
| 帮我把订单123的配送地址改成上海 | `order.read`                       | `order.update` | 0.3        |

第二个 query 的 `order.update` 系统里没有 → 正确进入 hints → 判 UNCOVERED/MISSING\_CAPABILITY。

### 6.2 Step 9：Baseline + Regression Diff（`regression/baseline.py`）

**Baseline（基线快照）**：把某次 `CoverageReport` 的关键信息存成版本化 JSON——每场景的状态 + 计数器，加上 `topology_version`。

**RegressionDiff（回归差分）**：把"新一次的报表"和 Baseline 按场景 id 逐条对比，输出：

```text
Newly covered    覆盖率回升的场景
Newly uncovered  本次"出事"的场景（重点盯这个）
Still uncovered  老问题还在
Status changed   任何状态变化（含变化前后的 Failure Reason）
```

用途一句话：**改了一次拓扑，立刻知道哪些业务能力悄悄退化了**。这补上了"回归不可感知"的短板。

### 6.3 Step 10：CLI（`tool-topology regression fast`）

把前面所有能力串成一条命令（终端脚本已在 `pyproject.toml [project.scripts]` 注册）：

```bash
tool-topology regression fast \
    --topology examples/topology/refund.json \
    --scenario examples/scenarios/refund.json \
    --mode gold | discovery \
    --baseline baseline.json      # 与历史基线差分
    --save-baseline out.json      # 存快照
    --fail-on-regression          # 有回退就退出码 1（CI 友好）
```

配套的还有 `TopologyLoader`（Step 10 引入）：**Fast Regression 不执行 Tool**，所以拓扑可以只用 JSON 描述声明，无需真实函数实现——这让"纯元数据的快速回归"跑在配置文件上都能成立。

> 后续演进（靶场强化批次 B）：JSON 工具可选声明 `"implementation": "module:attr"`
> 入口点绑定真实 async 实现；CLI slow 对未绑定工具**直接拒绝执行**（exit 2），
> 杜绝"空操作执行污染统计"——见 §10。

实测输出（本地 Ollama）：

```text
Gold 模式：       refund_001 covered，refund_002(纯 query) uncertain   → 50%
Discovery 模式：  refund_002 被 qwen3 解析后 covered                    → 100%
```

***

## 7. Phase 3 —— Slow Regression（执行探索）

> 一句话：Phase 2 回答"我**有没有**能力？"，Phase 3 回答"我**真的能做成功**吗？"。

### 7.1 为什么有了 Fast 还要 Slow

Phase 2 只证明"元数据上能力齐、拓扑连通"（COVERED）。但**连得上 ≠ 真能跑通**。Slow Regression **真正执行 Tool**、走一遍、评估业务结果，于是 Phase 0 里刻意分离的那两样东西开始汇合：

```text
Declared Topology（声明的，现在就有）  ──真实回归结果──►  Active Topology（活跃的，慢慢沉淀）
```

纪律不变：Phase 3 **只观测、统计、记录证据**，不剪枝、不排名——剪枝与排名归 Phase 4。

### 7.2 一次 Trial 的完整生命周期

```text
Fixture setup →（逐层执行）→ Evaluation → Fixture teardown
```

- **状态传播**：`ExecutionState` 按 artifact 名字顺序在层间传递；同一层内的多个 Tool 并发执行，结果汇入同一状态（Step 4/5）。
- **Trace**：记录每一层实际选了哪些工具、每个工具的调用与输出摘要（Step 6）。
- **ObservedRoute**：从 Trace 抽出结构路径 `read:[db] → analyze:[policy_check] → …`，用 ASC 稳定序算出一个 `route_id`——同一路线标签永远稳定（Step 7，§51）。

### 7.3 探索方式：free 与 basefast

- **free（自由探索）**：每层取"当前可达工具"的稳定子集，受 `max_tools_per_layer` 约束，没 seed 时兜底。
- **basefast（带 seed 扩展）**：把 Fast Regression 输出的 **CandidateRoute** 作为每个 Trial 的起点（`--basefast seeds.json`），逐层构建 **ExpansionPlan**——baseline（seed 的那组工具）+ 同层 sibling variants，按 Trial 轮转让同一条路线的多个变体都跑一遍（§0.1）。

```json
// seeds.json —— scenario_id → CandidateRoute，逐层给出"第一轮该用哪些工具"
{"s1": {"layers": [{"layer": "read", "tools": ["order_db"]},
                   {"layer": "analyze", "tools": ["policy_check"]}],
        "capabilities": ["order.read", "refund.policy.check"]}}
```

没配 seed 的场景回退到 free，并标记为 `seed_missing`（方便你发现"哪些场景连个候选起点都没有"）。

### 7.4 执行中怎么"选工具"：LayerRouter

Fast 阶段由人/配置给定候选路线；**执行中**则可能动态选路线。Phase 3 抽象出一个 `LayerRouter`（Step 3 协议）：

- 拿到"本层可达工具"→ 让 LLM 按 query + 当前状态挑一组（`LLMRouter`，走 Ollama），直到 `FINISH`；
- **只暴露当前层可达的工具**（绑定 TopologyFilter，§126），决策里的非法工具 → `ROUTING_ERROR` 状态；
- 测试时可注入 `FakeRouter` / patch 假 HTTP，**不触网**（Step 12，配 `router/prompts.py` 约束输出为 JSON）。

### 7.5 评估业务结果：Evaluator 三件套

| Evaluator | 干什么 | 什么时候用 |
| --- | --- | --- |
| `StructuredEvaluator` | 确定性事实断言（`state.字段 == 期望值`） | 结果可结构化比较时 |
| `LLMJudgeEvaluator` | 用 LLM 判**非确定性**结果（自然语言答案质量） | 客观断言判不了时 |
| `CompositeEvaluator` | 多路评估按权重合并成唯一分 | 结构化断言 + LLM 判官一起上 |

每个 Trial 得到 `EvaluationResult(success, quality_score, reason)`——这是"业务到底成没成"的唯一裁决（§50）。

### 7.6 观测统计：证据，不是结论

把 N 个 Trial 的结果沉淀成（Step 11，喂给 Phase 4 的原料）：

```text
node:  available（可用次数） / selected（被选次数）
edge:  opportunity（曾可达） / observed（确实走通）
route: usage_count / business_success_count
expansion delta: baseline vs variant 的 latency / token 差分（compute_expansion_deltas）
```

报告里出现的是 **"Unused Edges（从没走通的边）"这样的事实**，而**绝不出现**"我建议你剪掉 xxx"——剪枝判断是 Phase 4 的事（§108）。

### 7.7 落盘：JSONL 持久化（Step 14）

每个 run 写出六个文件：

```text
manifest.json       run 元数据（suite/拓扑/router config/trial 计数）
report.json         聚合报表
traces.jsonl        一个 Trial 一行（含业务结果 / latency / 状态）
node_stats.json / edge_stats.json / route_stats.json
```

离线可回放、可差分、可做报表。

### 7.8 CLI + 离线 Demo（Step 14）

```bash
tool-topology regression slow \
    --topology topology.json \      # 工具需带 "implementation": "module:attr" 绑定
    --scenario scenarios/refund.json \
    --trials 100 --environment sandbox \
    --basefast seeds.json          # 可选：用 Fast 的 CandidateRoute 做 seed
    --out-dir artifacts/run_0001   # 可选：写六个产物文件
```

> 批次 B 之后，拓扑 JSON 必须给工具绑定可执行实现，否则 CLI 拒绝执行。
> 最快上手用进程内 Demo（§10 批次 C/D 的 sandbox 工具与场景级 Fixture）：

配套离线示例 `examples/slow_refund/`：**10 个 Tool / 3 层**（read 4 + analyze 3 + action 3）的退款域。注意：这批工具在"靶场强化里程碑"（§10）里已经从"返回常量"升级为读取确定性内存订单库——行为随场景数据变化、有真实失败路径、带差异化成本与延迟，所以下面的数字会随场景变体组合浮动（这正是差分要看的）：

```bash
python examples/slow_refund/run_demo.py --trials 25 --out-dir artifacts
```

一次实测输出：**25 个 Trial → 15 个唯一 route_id；业务成功 19 / 失败 6；10/10 节点、21/21 条边都被观察到；延迟在 64–113ms 间真实分化；产物全部落盘**。

### 7.9 Phase 3 的 14 个 Step

```text
Step 1   ExecutionState / ExecutionContext / Trial
Step 2   TopologyFilter（prev + provider/worker + state，OR Reachability）
Step 3   Router models / protocol / FakeRouter 契约
Step 4   单 Tool 逐层执行，State 顺序传播
Step 5   同层 multi-tool 并发
Step 6   Trace 记录
Step 7   ObservedRoute / 稳定 route_id
Step 8   Structured / EvaluationResult 确定性评估
Step 9   Fixtures setup→execute→evaluate→teardown 状态隔离
Step 10  SlowRegressionRunner（CandidateRoute seed + ExpansionPlan + seed_missing 回退 free）
Step 11  Observation Stats（node/edge/route + expansion delta，不剪枝）
Step 12  LLMRouter + prompts（fake 注入）
Step 13  LLMJudgeEvaluator + CompositeEvaluator（fake 注入）
Step 14  持久化（JSONL/manifest/stats）+ `regression slow` CLI + 离线 Demo examples/slow_refund
```

***

## 8. 到现在为止：Phase 0–6 一句话回顾

```text
Phase 0  立宪：Graph 是搜索空间，不是答案；声明拓扑与活跃拓扑分离。
Phase 1  建模：用 layer/provider/worker 声明可搜索、可校验的拓扑（只建模不执行）。
Phase 2  验证：Fast Regression —— 只吃元数据，判 COVERED/UNCERTAIN/UNCOVERED，
               区分能力缺口与拓扑缺口，Gold/Discovery 双模式，接真实 LLM，
               落基线差分，全塞进 CLI。
Phase 3  探索：Slow Regression —— 真正执行 Tool、逐层传播状态、产生 Trace，
               抽 ObservedRoute，用 seed 扩展出多条候选路线，评估业务结果，
               沉淀观测统计与 JSONL 落的证据，只给"事实"不做剪枝。
Phase 4  优化：Topology Optimization —— 消费 Phase 3 证据，规则式识别剪枝候选、
               虚拟补丁反事实验证、探针补证、快/慢双门 + 多样性命门校验，
               版本化提交/回滚 + 优化报告 + optimize CLI，把活跃拓扑逐步收敛。
靶场强化   Battlefield Hardening —— 补齐被"骨架先行"跳过的靶场实质：计量贯通、
               可执行绑定、Sandbox 工具真实化、场景级 Fixture、场景资产与规模实跑。
Phase 5  排名：Route Ranking —— 四维证据向量（success/quality/latency/cost）、
               Wilson 区间、Pareto 前沿、Fast/Balanced/Quality Tier、rank CLI。
Phase 6  在线：Online Routing —— 版本门禁的 RouteCatalog、Tier 偏好选路、
               轮转均衡、路线跟随执行、有界降级、遥测回流离线闭环。
靶场强化   Battlefield Hardening —— 补齐被"骨架先行"跳过的靶场实质：计量贯通、
               可执行绑定、Sandbox 工具真实化、场景级 Fixture、场景资产与规模实跑。
（方向 1） 资源句柄计量 —— 监控不依赖工具上报：句柄管道 + 三档诚实标注 +
               计量向上汇总（见 §13）。
（方向 2） 复合节点 —— 最终能力作为宏节点入图：工具工厂 + 有界内层循环 +
               证据 flattener 递归复用整套机器（见 §14）。
（方向 3） 接入辅助 —— 类型推断 + OpenAI 批量适配 + capabilities 提案审阅，
               边际接入成本趋近 OpenAI tool spec 基线（见 §15）。
（方向 4） 剪枝编排 —— optimize analyze/validate/commit/rollback 三段式，
               Prune 成为可审计的命令行工作流（见 §16）。
```

实现边界（各 `phaseN-plan.md` 进度表全部勾选；里程碑批次 A–E 全部落地）：

```text
Phase 2  Step 1–10  能力覆盖验证（CapabilityRegistry → … → Fast Regression CLI）
Phase 3  Step 1–14  执行探索（ExecutionState → … → 持久化 + slow CLI + 离线 Demo）
Phase 4  Step 1–13  拓扑优化（Evidence → Candidate → Patch → Counterfactual → Probe
                     → Batch → Fast/Slow Gate → Diversity Guard → Dataset Split
                     → Versioning → Report）+ optimize CLI
里程碑    批次 A–E   计量 / 可执行绑定 / Sandbox 工具 / Fixture / 场景资产+实跑
方向 1   批次 A–E   resources/ 顶包：metering / memory / llm / 通用包装
方向 2   Step 1–8   composite/ 顶包 + JSON kind=composite + Demo
方向 3   批次 A–D   decorators 推断 + onboarding/ 顶包 + onboard CLI
方向 4   Step 1–6   optimization/artifacts + pipeline + 五子命令 CLI
```

测试规模：全量 **558 个测试通过**（Python 3.14），且所有测试都不依赖真实
LLM / 网络 / HTTP（LLM 相关统一注入假 HTTP；执行用确定性 sandbox 工具；
优化/排名/在线/剪枝编排全部离线可复现）。

***

## 9. Phase 4 —— Topology Optimization（把探索证据变成更收敛的活跃拓扑）

> 一句话：Phase 3 已经把"真实执行"的证据攒齐了，Phase 4 该做的事是**在确保业务能力不回退的前提下，把从来没被验证过的边从活跃拓扑里安全摘除**。

开头那幅图里，`Declared Topology → Active Topology` 的路正是在 Phase 4 补上第一个环节。但纪律极严：Phase 4 **只做"证据 → 候选 → 验证 → 版本化提交"**，剪枝的判断和最终 commit **必须来自人工或回归证据**，不是观测统计自说自话替你决定（AGENTS.md 三令五申）。

### 9.1 总体流程：从观测到收敛

Phase 4 把 Phase 3 的观测统计走一条完整流水线：

```text
Observation(Phase 3)  →  Evidence（逐节点/逐边证据，含成功路线支持数）
     →  Candidate（规则式剪枝候选，只输出不改任何东西）
     →  Patch（TopologyPatch 虚拟禁用，声明拓扑不可变）
     →  Counterfactual（对补丁视图重跑 Fast Regression，看覆盖是否掉）
     →  Probe（对"证据不足"的边用定向 seed 补证）
     →  Batch（把单独安全的多候选分组，整批验证，失败二分定位有害子集）
     →  Fast Gate / Slow Gate / Diversity Guard（三关校验）
     →  Version（TopologyVersion 版本化提交与回滚）
     →  Report + CLI
```

### 9.2 13 个 Step 拆开看

| Step | 模块 | 一句话 |
| ---- | ---- | ---- |
| 1 | `optimization/evidence.py` | 从 Phase 3 观测聚合 `NodeEvidence` / `EdgeEvidence`，补齐逐边"成功路线支持数" |
| 2 | `optimization/candidate.py` | `CandidateDetector`：高机会+低用量→IDENTIFIED，低机会→INSUFFICIENT_EVIDENCE |
| 3 | `optimization/candidate.py` | `ProtectionRegistry`：唯一 Provider / bridge / sentinel → PROTECTED（绝不触碰） |
| 4 | `topology/patch.py` | `TopologyPatch`（disabled_edges/nodes）+ `CandidateTopology`，只虚拟禁用不改声明 |
| 5 | `optimization/counterfactual.py` | `CounterfactualRunner`：删 A→B 还有 A→C→B 就继续验证；覆盖掉→REJECTED |
| 6 | `optimization/probe.py` | `ProbeRunner`：复用 basefast 定向 seed 补证，不新造 guided/replay 模式 |
| 7 | `optimization/batch.py` | `BatchCandidateBuilder`：多候选整批验证，超 `max_pruning_batch_size` 回滚，失败二分定位 |
| 8 | `optimization/pruning.py` | `FastValidationGate`：global / category / sentinel 三域 Fast 门 |
| 9 | `optimization/pruning.py` | `SlowValidationGate`：成功率 / 质量 / 错误率增量校验 |
| 10 | `optimization/pruning.py` | `RouteDiversityGuard`：禁止把搜索空间压成单一路线 |
| 11 | `optimization/analyzer.py` | `DatasetSplit`：hash 稳定切 Optimization / Validation / Sentinel，Validation 不参与候选生成（防过拟合） |
| 12 | `topology/version.py` | `TopologyVersion` 不可变版本快照 + `commit_patch` / `rollback` |
| 13 | `optimization/report.py` + `cli.py` | `OptimizationReport`（JSON/text）+ `optimize` CLI 子命令 |

### 9.3 两个贯穿 Phase 4 的设计要点

**（1）补丁层而非改声明。** `Topology` 声明模型在 Phase 4 始终保持不可变。剪枝只在上面叠一层 `TopologyPatch`（`disabled_edges` / `disabled_nodes`），得到可回滚的候选视图。这样"宣称连接了 db 和 refund"与"db 真的喂得了 refund"（§2.1 那两个分离）永远不混在一个对象里，试错了随时滚回去。

**（2）Validation/Sentinel 隔离。** `DatasetSplit` 把场景切成 Optimization / Validation / Sentinel 三份，其中 **Validation 与 Sentinel 永不参与候选生成**——防止你用"已知答案"去逆向优化而过度拟合（overfitting guard）。Sentinel 还是 Fast Gate 单场景回归的哨兵。

### 9.4 Phase 4 的 CLI

```bash
# 生成一份优化报告（当前 Phase 4 的 optimize 子命令会打印确定性报告）
tool-topology optimize \
    --topology topology.json \
    --scenario scenarios/refund.json \
    --start-version v1 --end-version v2 \
    --format text | json \
    --out report.json
```

> 注意：Phase 4 把"证据 → 候选 → 验证 → 版本化"的**组件**全部落地；但完整的 `analyze / validate / commit` 三段式端到端编排（`optimize analyze|validate|commit`）属后续 Step，最终 commit 始终显式需人工/脚本确认。真实证据的消费示例见 §10 批次 E 的 `run_scale.py`（ProtectionRegistry → EvidenceAggregator → CandidateDetector 联动）。

***

## 10. 靶场强化里程碑 —— 把"骨架"补成"靶场"

> 一句话：Phase 3/4 的管道在纸面上完备，但喂给它们的数据是空心的——这个里程碑在进入 Phase 5 之前把靶场（Battlefield）做实。

### 10.1 为什么要停一下

2026-09 的一次代码盘点发现三层缺口：

```text
计量层   TrialResult.cost 硬编码 None；ToolExecution 的 cost/token 字段从不填充；
         LLMRouter 不记账；quality_score 只有二值 1.0/0.0
工具层   demo 工具返回硬编码常量（与场景无关）；JSON 拓扑绑 _null_handler，
         CLI slow 实际执行的是空操作
数据层   场景资产 2+5 条且无 category；Fixture 只做深拷贝不准备任何后端状态；
         验收规模的端到端实跑从未发生
```

在这样的地基上直接做 Phase 5 排名，四维里有二维是死的（cost=None、
quality=success 的复制），另外二维没有区分度（success 趋同、latency 是
微秒噪声）——Pareto 全员互不支配，报告技术上正确、信息量为零。
详见 `docs/acceptance/battlefield-hardening.md` §0 的完整推演。

### 10.2 五个批次各补了什么

| 批次 | 交付 | 关键语义 |
| ---- | ---- | ---- |
| A 计量贯通 | `cost_per_call` 声明、`RoutingDecision.token_usage/routing_cost`、`TrialResult.tool_cost/routing_cost/evaluation_cost` | **按调用尝试计费**（超时/异常也算钱）；全缺省保持 `None` 不臆造 0；execution 与 evaluation 成本分离 |
| B 可执行绑定 | JSON 工具 `"implementation": "module:attr"` 入口点 + `unbound_tool_names` | CLI slow 对未绑定拓扑 fail-fast（exit 2），拒绝静默空跑 |
| C Sandbox 工具 | `store.py` 五变体（eligible/ineligible/high_risk/not_found/erp_down）+ Composite 评估器 | 工具行为随数据分支、有失败路径、延迟 3–50ms 成本 $0.001–0.01 真实分化；quality 进入 (0,1) 连续区间 |
| D Fixture 状态化 | `SandboxFixtureManager`（`metadata.fixture` 播种）+ `refund_api` 幂等守卫 | 每 trial 重置沙箱；守卫让"隔离失效会泄漏"变得**可观测** |
| E 资产+实跑 | 60 场景 5 类精确 70/15/15 分布 + `run_scale.py` | 50 场景×5 trials=250 trials：41 路线、业务 160/81、25.9s、artifacts 全落盘 |

批次 E 还顺手暴露了两个**真实拓扑边界**：`summarizer` 是死路（workers=[]）、
`send_email` 不可达（providers=[]）——设计里"应该 covered"的场景实测
TOPOLOGY_DISCONNECTED，这正是 Fast Regression 该揭示的东西。

***

## 11. Phase 5 —— Route Ranking（把证据变成多维排名）

> 一句话：Phase 4 回答"哪些搜索空间可以删"，Phase 5 回答"剩下的路线里，哪些快、哪些便宜、哪些质量高"。

### 11.1 核心纪律：四维向量不降维

Phase 0 §11 立下的原则在这里兑现——排名保持
`success / quality / latency / cost` 四维，**禁止压缩成单一综合分数**：

- success 附带 **Wilson 置信区间**（n=10 全成功 → [72.2%, 100%]，样本少区间就宽）；
- 两个区间重叠 → **STATISTICAL_TIE**，并列展示，不强行排出先后；
- 样本不足 `min_trials`（默认 20）→ **INSUFFICIENT_EVIDENCE**，只列清单注明差多少，不进排名；
- 某维数据全缺省 → 保持 `None`，退出支配判定并标记，绝不臆造 0。

### 11.2 Pareto 与 Tier

- **Pareto 前沿**：四目标支配判定（success↑ quality↑ latency↓ cost↓），
  被支配路线带"被谁支配"的归因；
- **Tier**：FAST / BALANCED / QUALITY 独立判定（可多标签、可 UNASSIGNED），
  规则全部是"相对最优 + 容差"的不等式，阈值集中在 `TierConfig`，
  每个分配带指标快照与命中规则文本——**禁止黑盒分级**；
- **category 维度**：逐类别排名，best 只在类内取值（同一路线全局被支配、
  类内可能在前沿）。

### 11.3 CLI 与真实数据

```bash
tool-topology rank --slow-report artifacts/slow_regression/run_xxx \
    [--scenario suite.json] [--min-trials 20] [--format text|json]
```

只读落盘 artifacts（manifest + traces.jsonl），版本混杂直接拒绝。对批次 E
的 250-trial 实跑（min-trials=4）：**18/41 条路线入选**，Wilson 区间真实
收窄、成本 $0.001–0.023 分化、QUALITY tier 命中带完整摘要链的路线、
失败的 partial 路线全部 UNASSIGNED 留在向量表中。

***

## 12. Phase 6 —— Online Routing（执行学到的路线，闭环完成）

> 一句话：在线阶段不再探索——路线由离线证据决定，在线只做"选路、均衡、有界降级、记账回流"。

### 12.1 全项目的收敛点

```text
Phase 3 在线 = 自由探索（每层 Agent 选择）
Phase 6 在线 = 路线跟随（执行离线选定的路线）
```

在线的全部自由度只有三处且全部有界：Tier 偏好（请求级覆盖配置起点）、
同级轮转均衡（RoundRobin，确定性）、有界降级（`max_fallbacks`，同一请求
永不重复已失败路线）。在线不做业务评估（Judge 属离线），降级只由
**执行失败**触发。

### 12.2 关键机制

| 机制 | 落点 | 一句话 |
| ---- | ---- | ---- |
| **版本门禁** | `RouteCatalog` | ranking 的 topology_version 必须与 Active Topology 一致，否则拒绝服务（fail closed）；路线结构由 canonical 确定性重建并逐一对照拓扑 |
| **选路** | `candidate_groups` | Tier 优先链（默认 fast>balanced>quality）；ranked 未贴标签的路线作链末兜底，有覆盖的 category 不因缺标签不可服务 |
| **执行** | `OnlineRuntime` | 复用 Phase 3 LayerExecutor；同层部分失败继续、整层失败降级；失败尝试同样计费 |
| **可解释** | `OnlineResult` | 每次结果带选择原因 + 完整降级链（从哪条到哪条、什么原因），可回放 |
| **闭环** | `online_results_to_trials` | 在线观测转回 TrialResult，直接作为下一轮 Phase 3/4/5 的 evidence 输入——在线零变更，遥测只回流 |

### 12.3 Demo 与干跑 CLI

```bash
# 干跑：只选路不执行（列各 Tier 候选 + 将选中谁 + 原因）
tool-topology select --topology t.json --ranking r.json \
    [--category refund] [--tier fast] [--format json]

# 进程内服务 Demo：学习 → 服务（含注入失败）→ 遥测 → 闭环，全程离线
python examples/online_refund/serve_demo.py
```

Demo 实测：fast 偏好命中 62ms 路线、quality 偏好命中 155ms 路线、同级轮转
可见；注入 `not_found` 后降级链完整回放（read 失败 → analyze 失败 → 有界
耗尽 ROUTE_FAILED）；注入 `erp_down` 后部分失败继续服务；8 条在线观测转成
TrialResult 喂进 Phase 4 的 EvidenceAggregator（8 nodes / 21 edges）——
**项目主循环正式闭合**。

***

## 13. 资源句柄计量 —— 监控不依赖工具上报（方向 1）

> 一句话：工具的 token 消耗、读写来源与次数，不必依赖工具"返回参数"——框架发放资源句柄，计量发生在句柄内部与调用边界，工具函数体零计量代码。

### 13.1 三档诚实标注

```text
MeteringSource
    DECLARED    未走管道：计费按声明的 cost_per_call（现状语义，不变）
    MEASURED    走了句柄管道：token / 访问 / 实测成本为精确值
    ESTIMATED   显式估算（estimate_tokens），绝不冒充实测
```

最弱声明原则：一次调用里同时出现估算与实测时，如实标注为 ESTIMATED 一档——
混合数据永不冒充全实测（延续批次 A 的 `None != 0` 纪律）。

### 13.2 机制：调用边界收账

```text
ToolExecutor.execute(tool)
  ├─ contextvar 挂载本次调用的 MeteringCollector
  ├─ await tool.invoke()
  │     └─ await handle.get(...)          ← 工具只写业务代码
  │          └─ 句柄内部：真实资源调用 + collector.record(...)
  └─ 收账进 ToolExecution：access_counts / token_usage /
     measured_cost / metering_source（按尝试计费：超时/异常同样已产生访问）
```

contextvars 按 async task 隔离——同层并发工具各记各账，互不串。

### 13.3 三类句柄

```python
from capability_runtime.resources import InMemoryStore, LLMResource, metered

# ① 内存句柄（靶场/测试）：get/put/delete 计量，seed/clear 免计量
orders = InMemoryStore("orders")
await orders.get("ORD-1")            # -> orders[read] +1

# ② LLM 句柄：精确 token + 实测成本（复用 LLMRouter 的 usage 解析）
llm = LLMResource(model="qwen3:1.7b", input_cost_per_1k=0.5, output_cost_per_1k=1.0)
resp = await llm.complete("总结这个订单")   # token/cost 自动入账

# ③ 通用包装：任何既有 async 客户端 + 一行包装即获得计量（应用注入客户端，核心零依赖）
ext = metered("mysql.orders", access="read", invoke=my_existing_async_get)
await ext("ORD-1")
```

### 13.4 计量维度贯通与漂移信号

access_counts 从 `ToolExecution → TrialResult → route_stats →
RouteProfile → 在线遥测` 全链贯通（磁盘往返保真）。计费基准**保持声明值**
（回归可复现性优先）；实测值走 `drift_findings` 输出
`metadata_review_candidate` 事实——提醒"声明价已失真"，修正仍由人工改声明。

实测（250-trial 规模跑）：`sandbox_orders[read]:60 / sandbox_refunds[write]:38 /
sandbox_erp[read]:22`。

***

## 14. 复合节点 —— 最终能力入图（方向 2）

> 一句话："处理退款全流程"作为一个节点参与外层拓扑：对外是与 ToolNode 完全同构的工具工厂，对内是更小的子拓扑 + 有界循环；图层面永不成环。

```python
from capability_runtime import CompositeSpec, build_composite_node

spec = CompositeSpec(
    name="refund_handler", layer="act",
    topology=inner_topology,               # 内层子拓扑（自相似的世界）
    route=(("verify",), ("holdover", "issue_refund")),
    stop_when=("refund_result",),           # 槽位齐备即停（ALL 语义）
    max_iterations=3,                       # 硬预算，无默认魔法数
    consumes=(Order, PolicyDecision), produces=(RefundResult,),
    capabilities={"refund.handle"}, cost_per_call=0.02,
)
refund_handler = build_composite_node(spec)   # -> 标准 ToolNode，注册即用
```

三个关键裁定：

1. **工具工厂，不是新节点类型**——注册/建边/覆盖/执行/排名/在线全部零特殊分支；
2. **循环 = 同一路线 + 持久黑板 + stop_when + 预算**——无隐藏控制流；整层失败立即
   失败（不内嵌重试，重试归外层 Phase 6 降级）；预算耗尽可解释失败
   （`stop condition unmet`）；
3. **剪枝 containment**——内层边永不进入外层统计（明细存 `composite_detail`）；
   `flatten_composite_results` 把每轮执行摊平成内层伪 Trial，同一套
   Evidence/Candidate/Gate 机器在内层拓扑上原样运转。

嵌套深度 ≤2、自引用构建期拦死（类型层面的环也不许）。Demo
（`examples/composite_refund`）用"双重确认计数器"（门信号存在**计量句柄**上，
跨轮生效）实现稳定的二轮 refine：第 1 轮 `issue_refund` 因确认数不足失败、
`holdover` 兄弟保层；第 2 轮计数达标放行——外层账单完整包含内层回放
（`composite_confirm[read]:4 / [write]:2 / sandbox_refunds[write]:1`）。

***

## 15. 接入辅助 —— 边际成本趋近 OpenAI 基线（方向 3）

> 一句话：只消除与拓扑模型解耦的 clerical 负担（类型推断、批量适配），把 semantic 负担从"逐工具编写"压成"一次性批量审阅"（capabilities 提案 → 人审 diff）。

| 项目 | 之前 | 现在 |
| --- | --- | --- |
| async 适配 | 3 行/工具 | 0（`from_openai_specs` 批量） |
| consumes/produces | 2–4 行/工具 | 0（类型注解自动推断） |
| capabilities | 逐工具编写 | **审阅 diff**（LLM 批量提案 → 人工确认） |
| layer 归类 | 人工 | 仍人工（方向一废弃的残留） |

宪法红线保持：**类型只填执行契约字段，绝不建边**；提案永不直接落盘，
`apply_capabilities` 只接受人工确认的 approved 映射。

### 15.1 接入示例：OpenAI 风格工具完整走一遍

以下流程全部离线可复现（资产在 `examples/onboarding_demo/`，
`propose` 一步需要本地 Ollama，可跳过直接手写 approved）。

**第 ① 步：准备 OpenAI specs**（`openai_specs.json`，裸数组）：

```json
[
  {"type": "function", "function": {
    "name": "get_order",
    "description": "Fetch one order by id, returns amount, status and channel",
    "parameters": {"type": "object",
      "properties": {"order_id": {"type": "string"}},
      "required": ["order_id"]}}}
  , ...check_refund_policy / issue_refund 同形态...
]
```

**第 ② 步：scaffold 生成骨架拓扑**（人工触点 1：layer 归类）：

```bash
python -m capability_runtime.cli onboard scaffold \
    --specs examples/onboarding_demo/openai_specs.json \
    --layer read \
    --out artifacts/skeleton.json
# 实测输出：skeleton written to artifacts/skeleton.json (3 tools)
```

骨架是合法拓扑 JSON（schema 要点已并入 description，capabilities 留空）。
单层世界一条命令即可；多层拓扑按层各跑一次 scaffold 再手工合并，或直接
编辑骨架的 layer 字段。

**第 ③ 步：propose 批量提案**（需要本地 Ollama；离线可跳过手写 approved）：

```bash
python -m capability_runtime.cli onboard propose \
    --topology artifacts/skeleton.json \
    --out artifacts/proposals.json
```

输出是人审 diff——`[vocab]` 标注复用了既有能力词表、`[new]` 是新提案、
非法名被丢弃并如实列出。**审阅这一份 diff 就是第 2 个人工触点**。

**第 ④ 步：手写 approved（审阅产物，不是提案的复制）**：

```json
{"get_order": ["order.read"],
 "check_refund_policy": ["refund.policy.check"],
 "issue_refund": ["refund.execute"]}
```

**第 ⑤ 步：apply 落盘**（审阅门禁：approved 里出现拓扑没有的工具直接拒绝）：

```bash
python -m capability_runtime.cli onboard apply \
    --topology artifacts/skeleton.json \
    --approved examples/onboarding_demo/approved.json \
    --out artifacts/topology.json
# 实测输出：topology written to artifacts/topology.json
```

**第 ⑥ 步：立即可跑 Fast Regression**：

```bash
python -m capability_runtime.cli regression fast \
    --topology artifacts/topology.json \
    --scenario examples/onboarding_demo/scenarios.json
```

实测输出（正确区分覆盖与缺口）：

```text
Total:      2        Covered:    1        Uncovered:  1
Coverage: 50.00%
Missing Capabilities:
1. invoice.send                   1     ← 系统里没有开票工具，如实暴露
```

**边界说明**：到此为止是"元数据接入"——Fast Regression 不执行工具。要进
Slow Regression / 在线路由，还需给工具绑 `"implementation": "module:attr"`
入口点（或走 Python 注册路径）。**跨层类型链是 Python 路径**：声明轻量
dataclass 作契约，让 `@tool` 的注解推断自动填 consumes/produces（约 5 行/
工具），批量适配产出的入口工具无类型契约（末端/入口节点语义）。

***

## 16. 剪枝编排 —— Prune 成为命令行工作流（方向 4）

> 一句话：Phase 4 的全部组件（Evidence/Candidate/双门/守卫/版本）本来只能在测试里调用，现在装上了方向盘：三段显式、文件为媒介、可审计。

```bash
# ① analyze（只读）：慢回归落盘证据 -> 候选提案
tool-topology optimize analyze \
    --topology t.json --scenario suite.json \
    --slow-report artifacts/slow_regression/run_xxx \
    --out artifacts/candidates.json

# ② validate（不动任何东西）：三关判定；REJECT 退出码 1
tool-topology optimize validate \
    --topology t.json --scenario suite.json \
    --patch artifacts/candidates.json --trials 3 \
    --out artifacts/verdict.json

# ③ commit（唯一写操作）：ACCEPT 记录 + 补丁/声明双指纹是硬门槛
tool-topology optimize commit \
    --topology t.json --patch artifacts/candidates.json \
    --validation artifacts/verdict.json \
    --version v2 --versions-dir artifacts/versions

# ④ rollback：记录重放（非反向补丁；声明指纹不一致拒绝）
tool-topology optimize rollback \
    --topology t.json --versions-dir artifacts/versions [--to v2]
```

| 阶段 | 语义 | 关键裁定 |
| --- | --- | --- |
| analyze | 只读提案 | 只看 Optimization 集（防过拟合）；反事实 Fast 预检进候选记录 |
| validate | 三关判定 | Fast 拦截则跳 Slow（省最贵一步）；sentinel 门 = hash 桶 ∪ metadata；多样性下限自适应（稀疏世界零误杀） |
| commit | 唯一写 | 缺 ACCEPT / REJECT / 指纹不匹配三类硬拒；版本不可变 |
| rollback | 记录重放 | 补丁只减不增 → 反向补丁不存在；重放锚点是声明拓扑，指纹校验 |

退出码：validate ACCEPT→0 / REJECT→1（CI 友好）；参数与门禁错误→2。

***
## 17. 八个阶段一卷串起来看这份"中间语言"

把 Project 定位那句再读一遍：

> A layered tool-routing and topology optimization framework for AI agents.

主循环六阶段 + 四个加固方向，各干各的、又首尾相接：

```text
Phase 1   建空间：   layer/provider/worker 声明出 Graph（搜索空间）
Phase 2   验覆盖：   元数据侧回答"我有能力吗"（COVERED / UNCERTAIN / UNCOVERED）
Phase 3   跑真执行： 执行侧回答"我做成功了吗"（证据：成功路线 / unused edges）
靶场强化   夯地基：   计量、可执行、数据真实化——让证据有信息量
Phase 4   收敛空间：  用证据把从未被验证的边安全摘除（候选→验证→版本化）
Phase 5   排名路线：  四维向量 + Pareto + Tier，不裁决唯一冠军
Phase 6   在线服务：  执行学到的路线，遥测回流下一轮——闭环
方向 1    计量加固：  句柄管道让监控零侵入，实测回流为漂移证据
方向 2    结构扩展：  复合节点让"最终能力"入图，机制自相似递归适用
方向 3    接入降本：  边际成本趋近 OpenAI 基线，语义负担 authoring→review
方向 4    运维成型：  Prune 三段式编排，拓扑进化可审计、可回滚
```

一处更底层的东西贯穿始终：**Capability 是中间语言**。Tool 用 capability
声明能力，Scenario 用 expected capability 描述需求，Fast Regression 用
capability 判覆盖，拓扑缺口（Topology Gap）与能力缺口（Capability Gap）
都用 capability 对齐，Phase 4 的保护规则（唯一 Provider / sentinel 锁）也
建立在 capability 之上——这就是 `Tool-Capability-Compiler` 这个名字的由来。

***

## 附录：快速上手指令

> 想要一条带实测输出的线性入门路径，见 [quickstart.md](quickstart.md)；
> 本附录是全命令速查表。

先安装（或给下面所有 `python -m` 命令加 `PYTHONPATH=src` 前缀）：

```bash
python -m pip install -e .        # 注册 tool-topology 终端命令
```

```bash
# 全量验证（Python >= 3.12；Windows 下若无默认 python 可用 C:\Python314\python.exe）
python -m pytest -q
python -m compileall -q src tests main.py
git diff --check

# ---- Phase 2：Fast Regression ----
# Gold 模式（确定性、零 LLM）。数据集刻意含 15% 未覆盖场景，
# CI 用法可加 --fail-on-regression（出现回退则退出码 1）
python -m capability_runtime.cli regression fast \
    --topology examples/topology/refund.json \
    --scenario examples/datasets/customer_service.fast.json

# Discovery 模式（接本地 Ollama，需先 ollama pull qwen3:1.7b 并启动）
python -m capability_runtime.cli regression fast \
    --topology examples/topology/refund.json \
    --scenario examples/scenarios/refund.json \
    --mode discovery

# ---- Phase 3：Slow Regression ----
# 离线退款 Demo（10 工具 / 3 层 / 场景级 Fixture，脱网可跑）
python examples/slow_refund/run_demo.py --trials 25 --out-dir artifacts

# ---- 靶场强化：规模实跑 + optimize 联动 ----
# 50 场景 × 5 trials：250 trials 实跑落盘，真实证据喂给
# ProtectionRegistry → EvidenceAggregator → CandidateDetector
python examples/slow_refund/run_scale.py --scenarios 50 --trials 5

# ---- Phase 4：优化报告 ----
python -m capability_runtime.cli optimize \
    --topology examples/topology/refund.json \
    --scenario examples/datasets/customer_service.fast.json \
    --start-version v1 --end-version v2 --format text

# ---- Phase 5：Route 排名（消费 slow artifacts）----
# 最新规模实跑产物目录（run_scale.py 同时落盘本次场景集 scenarios.json）
SCALE=$(ls -d examples/slow_refund/artifacts/scale/scale_* | tail -1)
python -m capability_runtime.cli rank \
    --slow-report "$SCALE" --scenario "$SCALE/scenarios.json" \
    --min-trials 4 --format text
# 存成 JSON 供 Phase 6 的 Catalog 消费（--scenario 提供类目映射，勿省）：
python -m capability_runtime.cli rank \
    --slow-report "$SCALE" --scenario "$SCALE/scenarios.json" \
    --min-trials 4 --format json --out artifacts/ranking.json

# ---- 接入辅助：OpenAI specs -> 拓扑资产（全程离线，propose 除外）----
# ① 骨架（人工触点 1：layer 归类）
python -m capability_runtime.cli onboard scaffold     --specs examples/onboarding_demo/openai_specs.json     --layer read --out artifacts/skeleton.json
# ② 提案（需本地 Ollama；人工触点 2：审 diff 后手写 approved.json）
python -m capability_runtime.cli onboard propose     --topology artifacts/skeleton.json --out artifacts/proposals.json
# ③ 应用（审阅门禁：只接受显式 approved）
python -m capability_runtime.cli onboard apply     --topology artifacts/skeleton.json     --approved examples/onboarding_demo/approved.json     --out artifacts/topology.json
# ④ 立即验证覆盖
python -m capability_runtime.cli regression fast     --topology artifacts/topology.json     --scenario examples/onboarding_demo/scenarios.json

# ---- 剪枝编排：三段式（对慢回归落盘产物；拓扑需可执行绑定）----
# 可执行拓扑由导出器生成（以 Python 声明为唯一事实源）；场景集必须与
# 证据同源——用产物目录里自带的 scenarios.json
python examples/slow_refund/export_topology.py
python -m capability_runtime.cli optimize analyze     --topology examples/topology/refund_sandbox.json     --scenario "$SCALE/scenarios.json"     --slow-report "$SCALE"     --out artifacts/candidates.json
python -m capability_runtime.cli optimize validate     --topology examples/topology/refund_sandbox.json     --scenario "$SCALE/scenarios.json"     --patch artifacts/candidates.json --trials 2 --out artifacts/verdict.json
echo "exit=$?"    # ACCEPT=0 / REJECT=1
python -m capability_runtime.cli optimize commit     --topology examples/topology/refund_sandbox.json --patch artifacts/candidates.json     --validation artifacts/verdict.json     --version v2 --versions-dir artifacts/versions
python -m capability_runtime.cli optimize rollback     --topology examples/topology/refund_sandbox.json --versions-dir artifacts/versions --to v2

# ---- Phase 6：在线路由 ----
# 干跑选路（只选不执行；ranking 的 topology_version 须与拓扑一致，fail closed）
python -m capability_runtime.cli select \
    --topology examples/topology/refund_sandbox.json \
    --ranking artifacts/ranking.json --category refund --tier fast

# 进程内服务 Demo（学习 → 服务 → 注入失败降级 → 遥测 → 闭环）
python examples/online_refund/serve_demo.py --out-dir artifacts
```

