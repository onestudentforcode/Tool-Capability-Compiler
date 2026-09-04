# 项目教程：Tool-Capability-Compiler 到目前为止做了什么

> 面向读者：刚接触本仓库的开发者 / Agent。目标：让你在 30 分钟内理解这个框架"为什么存在、解决什么问题、已经做完了什么"。

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

## 5. Phase 2 —— Fast Regression（当前的全部实现）

Phase 2 建立了第一套业务能力验证机制，它是整个项目至今 **最核心、实现最完整** 的部分。其本质是：

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

## 6. Phase 2 的后三块（本次会话完成）—— Step 8/9/10

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

## 7. 到现在为止：Phase 0–2 一句话回顾

```text
Phase 0  立宪：Graph 是搜索空间，不是答案；声明拓扑与活跃拓扑分离。
Phase 1  建模：用 layer/provider/worker 声明可搜索、可校验的拓扑（只建模不执行）。
Phase 2  验证：Fast Regression —— 只吃元数据，判 COVERED/UNCERTAIN/UNCOVERED，
               区分能力缺口与拓扑缺口，Gold/Discovery 双模式，接真实 LLM，
               落基线差分，最后全塞进 CLI。
```

实现边界（`docs/acceptance/phase2.md` §68 已全部标记 COMPLETE）：

```text
Step 1  + CapabilityRegistry
Step 2  + Scenario / Suite / Loader
Step 3  Gold Mode Coverage Analyzer
Step 4  Candidate Route Search
Step 5  Coverage Status + Failure Reason
Step 6  Coverage / Category / Capability / Topology Gap Reports
Step 7  CapabilityResolver Protocol + Fake Resolver
Step 8  Ollama LLM Resolver (qwen3:1.7b @ .env)
Step 9  Baseline + Regression Diff
Step 10 Fast Regression CLI
```

测试规模：全量 **107 个测试通过**（Python 3.14），且 **Phase 2 测试不依赖真实 LLM / 网络 / HTTP**（LLM 相关用注入假 HTTP，CLI 用 Gold 模式）。

***

## 8. 如果继续往下做（Phase 3 的前瞻）

Phase 2 只做了 **Fast Regression（只看元数据，不执行）**。AGENTS.md 明确写着的下一步是：

```text
Slow Regression、Trace、Pruning、Ranking —— 必须等对应 Phase 到来后再实现
```

Phase 3 的核心将是 **真正执行 Tool、产生 Trace、做 Evaluation**（此时才能派生出 Active Topology，再谈 Pruning 剪枝与 Ranking 排名）。到那个 Phase，Phase 0 里刻意分离的"Declared vs Active"才会真正汇合。

> 一条纪律提醒（AGENTS.md）：**不要提前实现下一 Phase 的功能。** 现在 Phase 2 已完成，紧接着的正确动作是先起草 Phase 3 验收文档（`docs/acceptance/phase3.md`），再动手实现。

***

## 附录：快速上手指令

```bash
# Python 版本需 >= 3.12（若默认 python 是 3.10，用系统的 3.14）
C:\Python314\python.exe -m pytest -q

# 跑一次 Gold 模式回归
C:\Python314\python.exe -m capability_runtime.cli regression fast \
    --topology examples/topology/refund.json \
    --scenario examples/scenarios/refund.json

# 跑一次 Discovery（接本地 Ollama，需先 ollama pull qwen3:1.7b 且已启动）
C:\Python314\python.exe -m capability_runtime.cli regression fast \
    --topology examples/topology/refund.json \
    --scenario examples/scenarios/refund.json \
    --mode discovery
```

