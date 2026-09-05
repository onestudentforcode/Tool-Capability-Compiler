# 项目教程：Tool-Capability-Compiler 到目前为止做了什么

> 面向读者：刚接触本仓库的开发者 / Agent。目标：让你在 30 分钟内理解这个框架"为什么存在、解决什么问题、已经做完了什么（Phase 0–3）"。

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

配套的还有 `TopologyLoader`（Step 10 引入）：**Fast Regression 不执行 Tool**，所以拓扑可以只用 JSON 描述声明，用一个永远不会被调用的占位 handler 构建，无需真实函数实现——这让"纯元数据的快速回归"跑在配置文件上都能成立。

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
    --topology examples/topology/refund.json \
    --scenario examples/scenarios/refund.json \
    --trials 100 --environment sandbox \
    --basefast seeds.json          # 可选：用 Fast 的 CandidateRoute 做 seed
    --out-dir artifacts/run_0001   # 可选：写上面六个文件
```

配套离线示例 `examples/slow_refund/`：**10 个 Tool / 3 层**（read 4 + analyze 3 + action 3）的退款域，纯内存假实现可脱网跑：

```bash
python examples/slow_refund/run_demo.py --trials 100 --out-dir artifacts
```

一次实测输出：**100 个 Trial → 30 个唯一 route_id；业务成功 74 / 失败 26；10/10 节点、21/21 条边都被观察到；产物全部落盘**。（数字随 run 浮动，属正常——这正是差分要看的。）

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

## 8. 到现在为止：Phase 0–3 一句话回顾

```text
Phase 0  立宪：Graph 是搜索空间，不是答案；声明拓扑与活跃拓扑分离。
Phase 1  建模：用 layer/provider/worker 声明可搜索、可校验的拓扑（只建模不执行）。
Phase 2  验证：Fast Regression —— 只吃元数据，判 COVERED/UNCERTAIN/UNCOVERED，
               区分能力缺口与拓扑缺口，Gold/Discovery 双模式，接真实 LLM，
               落基线差分，全塞进 CLI。
Phase 3  探索：Slow Regression —— 真正执行 Tool、逐层传播状态、产生 Trace，
               抽 ObservedRoute，用 seed 扩展出多条候选路线，评估业务结果，
               沉淀观测统计与 JSONL 落的证据，只给"事实"不做剪枝。
```

实现边界（`phase2.md` §68 全部标记 COMPLETE；`phase3-plan.md` §9 全部勾选 `[x]`）：

```text
Phase 2  Step 1–10  能力覆盖验证（CapabilityRegistry → … → Fast Regression CLI）
Phase 3  Step 1–14  执行探索（ExecutionState → … → 持久化 + slow CLI + 离线 Demo）
```

测试规模：全量 **247 个测试通过**（Python 3.14），且 **Phase 2 / Phase 3 测试都不依赖真实 LLM / 网络 / HTTP**（LLM 相关统一注入假 HTTP；CLI 的 slow 模式用 JSON 描述的可执行占位工具）。

***

## 9. 如果继续往下做（Phase 4 前瞻）

Phase 3 把"真实执行"的**证据**攒齐了：哪条边从没走通、哪个节点的 latency 高、哪条路线业务失败——这些都是 `Unused Edges` / `expansion delta` / 各层 stats 里的事实。Phase 4 该接手的是：

```text
Active Topology 派生 ──► Pruning（剪枝，把从没用的边去掉）──► Ranking（排序）
```

要动手前的纪律提醒（AGENTS.md）：**先按 phase 顺序起草 `phase4.md` 验收文档，再实现**；且 Phase 4 的**剪枝/排名指令仍需来自人工或回归证据，不能由观测统计自行"替用户做决定"**。

***

## 附录：快速上手指令

```bash
# Python 版本需 >= 3.12（若默认 python 是 3.10，用系统的 3.14）
C:\Python314\python.exe -m pytest -q

# 跑一次 Gold 模式快回归（不看执行）
C:\Python314\python.exe -m capability_runtime.cli regression fast \
    --topology examples/topology/refund.json \
    --scenario examples/scenarios/refund.json

# 跑一次 Discovery（接本地 Ollama，需先 ollama pull qwen3:1.7b 且已启动）
C:\Python314\python.exe -m capability_runtime.cli regression fast \
    --topology examples/topology/refund.json \
    --scenario examples/scenarios/refund.json \
    --mode discovery

# 跑一次慢回归（真实执行 100 个 Trial，落盘证据）
C:\Python314\python.exe -m capability_runtime.cli regression slow \
    --topology examples/topology/refund.json \
    --scenario examples/scenarios/refund.json \
    --trials 100 --out-dir artifacts/run_0001

# 跑离线退款 Demo（10 Tool / 3 Layer，脱网可跑）
C:\Python314\python.exe examples\slow_refund\run_demo.py --trials 100 --out-dir artifacts
```

