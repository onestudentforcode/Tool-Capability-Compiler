# 快速上手 Quick Start

目标读者：第一次接触本项目、想在 30 分钟内从零跑通主循环的人。
本指南是一条**线性路径**：每一步都给出命令和实测输出，照抄即可复现，
全程离线（不需要 LLM、不需要网络）。想理解"为什么这样设计"，请读
[tutorial.md](tutorial.md)；本文只回答"怎么跑起来"。

项目主循环（本指南将依次走过每一站）：

```text
Declare → Fast → Slow → Prune → Rank → Route → 回流
```

约定：

- 所有命令都在仓库根目录执行；
- CLI 一律写 `python -m capability_runtime.cli`（`tool-topology` 是安装后的
  等价终端命令）；
- 本地产物统一写到 `artifacts/`（已 gitignore），不会污染工作区。

---

## 1. 环境准备（2 分钟）

要求 Python ≥ 3.12（项目在 3.14 上开发并验证），零第三方依赖。

```bash
python -m pip install -e .     # 注册 capability_runtime 包

python -m pytest -q            # 自检：应输出 558 passed
```

Windows 下若默认 `python` 不可用，用完整路径（如 `C:\Python314\python.exe`）。

---

## 2. 第一条命令：Fast Regression（5 分钟）

Fast Regression 只分析元数据——比对场景声明的 capability 需求与拓扑
实际提供的 capability，**不执行任何工具**，天然适合 CI。

```bash
python -m capability_runtime.cli regression fast \
    --topology examples/topology/refund.json \
    --scenario examples/datasets/customer_service.fast.json
```

实测输出（数据集刻意含 30% 非 COVERED 场景）：

```text
Total:      60
Covered:    42
Uncertain:  9
Uncovered:  9

Coverage: 70.00%

Missing Capabilities:
1. invoice.send                   2
2. user.profile.read              2
...

Topology Gaps:
1. email.send, order.read                   1
...
```

两类缺口含义不同：`Missing Capabilities` 是拓扑里根本没有工具提供该
capability（Capability Gap）；`Topology Gaps` 是工具都在、但分层白名单
不允许把它们连起来（Topology Gap）。加 `--fail-on-regression` 可让回退
场景以退出码 1 失败，用于 CI 门禁。

---

## 3. 真实执行：Slow Regression Demo（5 分钟）

`examples/slow_refund/` 是一个完整靶场：10 个沙盒退款工具、3 层拓扑、
5 种订单变体（含失败路径）、场景级 Fixture 隔离。真的执行工具、真的
产生失败，仍然完全离线。

```bash
python examples/slow_refund/run_demo.py --trials 25 --out-dir artifacts/demo
```

实测输出（节选）：

```text
Business Success: 15
Business Failed:  10

Observed Nodes: 10 / 10
Observed Edges: 21 / 21
Unused Edges:   0

Failure Categories:
  answer_error              10

(Phase 3 observes only; it makes no pruning recommendation.)
```

慢回归只观测不决策——每条路线的成功率、延迟、失败类别都被记录成
证据，但没有任何东西被修改。

---

## 4. 规模实跑：证据落盘（约 25 秒）

优化（Prune）阶段消费的是**落盘证据**。规模实跑一次 50 场景 × 5 试次
的慢回归，把完整产物写进 `examples/slow_refund/artifacts/scale/`：

```bash
python examples/slow_refund/run_scale.py --scenarios 50 --trials 5
```

实测输出（节选）：

```text
Scale run: 50 scenarios x 5 trials
Trials: 250  unique routes: 41
Business: 160 success / 81 failed
Elapsed: 21.9s
Artifacts in examples/slow_refund/artifacts/scale/scale_20261001162805:
  traces.jsonl / scenarios.json / route_stats.json /
  node_stats.json / edge_stats.json / report.json / manifest.json
  ...

Resource access (metered handles):
  sandbox_erp[read]           87
  sandbox_orders[read]        250
  sandbox_refunds[write]      160
```

注意 `scenarios.json`：规模实跑把本次使用的场景集一并落盘。后续
analyze/validate/rank 都要消费它——**场景集与证据必须同源**（同一套
scenario_id），否则 optimize analyze 会拒绝
（`slow report contains no trials for the optimization split`）。

后面各步用变量引用最新一次产物（Git Bash）：

```bash
SCALE=$(ls -d examples/slow_refund/artifacts/scale/scale_* | tail -1)
```

---

## 5. 优化三段式：analyze → validate → commit（10 分钟）

剪枝是显式三段编排：analyze 只读、validate 判定、commit 是唯一写操作。

这一步需要**可执行拓扑**（JSON 中每个工具带 `"implementation": "模块:属性"`
绑定）。沙盒世界的可执行拓扑由导出器生成（以 Python 声明为唯一事实源）：

```bash
python examples/slow_refund/export_topology.py
# topology written to examples\topology\refund_sandbox.json (10 tools)
```

### ① analyze：证据 → 候选提案（只读）

```bash
python -m capability_runtime.cli optimize analyze \
    --topology examples/topology/refund_sandbox.json \
    --scenario "$SCALE/scenarios.json" \
    --slow-report "$SCALE" \
    --out artifacts/candidates.json
```

实测输出：

```text
split: 39 optimization / 9 validation / 2 sentinel
candidates by status: {'protected': 10}
(analyze observes only; validate decides, commit writes)
```

场景集被确定性切成三份：optimization（产生证据）/ validation + sentinel
（验证剪枝，sentinel 场景直接免疫剪枝）。本例全部候选都被保护——
沙盒世界每个工具都是某 capability 的唯一提供者，这是保护机制在起作用。

### ② validate：三关判定（不动任何东西）

```bash
python -m capability_runtime.cli optimize validate \
    --topology examples/topology/refund_sandbox.json \
    --scenario "$SCALE/scenarios.json" \
    --patch artifacts/candidates.json \
    --trials 2 --out artifacts/verdict.json
echo "exit=$?"      # ACCEPT=0 / REJECT=1
```

实测输出：

```text
VERDICT: ACCEPT
```

三关依次是：快门（Counterfactual 元数据回归，拦截就没必要跑慢门）→
慢门（剪枝前后各跑一轮真实执行对比）→ 多样性守卫（成功路线族不得
塌缩）。REJECT 是正常退出码 1，不是报错。

### ③ commit：唯一写操作（硬门槛）

```bash
python -m capability_runtime.cli optimize commit \
    --topology examples/topology/refund_sandbox.json \
    --patch artifacts/candidates.json \
    --validation artifacts/verdict.json \
    --version v2 --versions-dir artifacts/versions
```

实测输出：

```text
version v2 committed:
  record:   artifacts/versions/v2.json
  topology: artifacts/versions/v2.topology.json
```

commit 只接受 verdict=ACCEPT 且补丁指纹、声明指纹与验证记录一致——
任何一项不符都拒绝。声明拓扑文件永远不被修改，剪枝只落在版本记录里。

### ④ rollback：记录重放

```bash
python -m capability_runtime.cli optimize rollback \
    --topology examples/topology/refund_sandbox.json \
    --versions-dir artifacts/versions --to v2
# current active topology -> v2-restored (record replay; history untouched)
```

rollback 不是反向补丁，而是**重放已提交的版本记录**。重放从未提交过的
版本（如 v1）会被拒绝，exit 2——这是刻意的失败路径。

---

## 6. 排名与在线选路（5 分钟）

### rank：证据 → 路线排名

```bash
python -m capability_runtime.cli rank \
    --slow-report "$SCALE" \
    --scenario "$SCALE/scenarios.json" \
    --min-trials 4 --format json --out artifacts/ranking.json
```

`--scenario` 不能省：它提供"场景 → 类目"映射，省掉后在线目录会认为
任何类目都无路线可选。文本报告（`--format text`）长这样：

```text
Tier QUALITY
  read:[erp,order_db,web_search] analyze:[policy_check,summarizer] action:[refund_api,send_email]
      success 100.0% [72.2, 100.0]  quality 1.00  latency 151ms (p95 158ms)
      cost $0.023  trials 10  pareto: yes
```

成功率的方括号是 Wilson 置信区间；试次不足 `--min-trials` 的路线只列
清单不参与排名（证据不足不臆造）。

### select：在线选路干跑

```bash
python -m capability_runtime.cli select \
    --topology examples/topology/refund_sandbox.json \
    --ranking artifacts/ranking.json --category refund --tier fast
```

实测输出（节选）：

```text
Would select: read:[erp,order_db,web_search] analyze:[policy_check,summarizer] action:[refund_api,send_email]
(dry run; no tool is executed)
```

在线目录有版本门禁：ranking 的 topology_version 与拓扑不一致会拒绝
加载（fail closed）。

### 在线服务闭环 Demo

```bash
python examples/online_refund/serve_demo.py --out-dir artifacts/online
```

实测输出（节选）——学习 → 服务（含注入失败与降级链）→ 遥测 → 回流：

```text
  [not_found injected] -> route_failed fallback_chain=2
      718dcdbe3376c87e -> 380038b553ee0530 (route 718dcdbe3376c87e: layer 'read' failed)
      380038b553ee0530 -> 606d2e675e66f892 (route 380038b553ee0530: layer 'analyze' failed)
  [erp_down injected] -> served (tier=fast (config))

Loop closure: 8 online observations -> evidence over 10 nodes / 21 edges (offline round input)
```

在线遥测经 `online_results_to_trials` 回流，成为下一轮离线优化的输入
——主循环至此闭合。

---

## 7. 写你自己的工具（5 分钟）

声明式接入，一个装饰器 + 一个公式：

```python
from capability_runtime import LayerRegistry, ToolRegistry, TopologyBuilder, tool

@tool(layer="read", workers=["policy_check"],          # 我的数据只允许流向 policy_check
      capabilities={"order.read"})
async def order_db() -> Order: ...

@tool(layer="analyze", providers=["order_db"],         # 我只接收来自 order_db 的数据
      workers=["refund_api"])
async def policy_check(order: Order) -> PolicyDecision: ...

@tool(layer="act", providers=["policy_check"])
async def refund_api(order: Order, decision: PolicyDecision) -> RefundResult: ...

layers = LayerRegistry()
layers.register("read", 0); layers.register("analyze", 1); layers.register("act", 2)

tools = ToolRegistry()
for node in (order_db, policy_check, refund_api):     # @tool 返回 ToolNode
    tools.register(node)

topology = TopologyBuilder(layers, tools).build()
```

建边公式（记住这一条就够）：

```text
Edge(A, B)
= adjacent(layer(A), layer(B))      # 只允许相邻层
  AND A.workers allows B            # A 主动声明数据可流向谁
  AND B.providers allows A          # B 主动声明只接收谁的数据
```

`consumes / produces` 类型注解**只验证已允许边的参数传递，绝不建边**。
业务意图（白名单）决定拓扑，类型负责诊断——这是全项目的第一原则。

想让 JSON 拓扑可执行，给工具加 `"implementation": "模块:属性"` 绑定
（可指向 `@tool` 装饰后的 ToolNode，加载器会自动解包出真实函数）。

---

## 8. 从 OpenAI 风格工具迁移

已有 OpenAI tool specs 的三段式接入（详见 [tutorial.md](tutorial.md) §15
的六步实测示例）：

```bash
# ① 骨架：specs + 人工归类 layer → 拓扑骨架（离线）
python -m capability_runtime.cli onboard scaffold \
    --specs examples/onboarding_demo/openai_specs.json \
    --layer read --out artifacts/skeleton.json

# ② 提案：LLM 批量提议 capabilities（需本地 Ollama），
#    人工审 diff 后手写 approved.json —— 提案永不直接落盘

# ③ 应用：审阅门禁，只接受显式 approved 的条目
python -m capability_runtime.cli onboard apply \
    --topology artifacts/skeleton.json \
    --approved examples/onboarding_demo/approved.json \
    --out artifacts/topology.json

# ④ 立即验证覆盖
python -m capability_runtime.cli regression fast \
    --topology artifacts/topology.json \
    --scenario examples/onboarding_demo/scenarios.json
```

---

## 9. 下一步读什么

| 想了解 | 去处 |
| --- | --- |
| 每一步背后的设计取舍（教学文档） | [tutorial.md](tutorial.md) |
| 各阶段验收规格（实现的合同） | [docs/acceptance/](acceptance/) |
| 资源计量 / 复合节点 / 接入辅助 / 剪枝编排 | 对应 milestone 文档，见 acceptance 目录 |
| 项目宪法与开发规则 | [AGENTS.md](../AGENTS.md)、[phase0.md](acceptance/phase0.md) |

## 常见问题

- **`select` 报 `no ranked route covers category=...`**
  rank 时漏了 `--scenario`，排名 JSON 里没有类目映射。
- **`validate` 退出码 2，提示 `requires an executable topology`**
  拓扑 JSON 里有工具没有 `"implementation"` 绑定；先用
  `export_topology.py` 导出可执行拓扑，或在骨架上补绑定。
- **`analyze` 报 `slow report contains no trials for the optimization split`**
  传给 analyze 的场景文件与慢回归产物不是同一套 scenario_id；
  用产物目录里自带的 `scenarios.json`。
- **`rollback` 拒绝某个版本**
  rollback 只重放**已提交**的版本记录；v1 若从未 commit 过就不存在记录。
- **Windows 找不到 python**
  用完整路径执行，如 `C:\Python314\python.exe`。
