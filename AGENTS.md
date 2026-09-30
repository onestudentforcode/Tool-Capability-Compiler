# AGENTS.md

本文件适用于整个仓库。所有开发者与自动化 Agent 在修改项目前，应先阅读并遵守本文件。

## 1. Source of Truth

项目开发以以下文档为准，优先级从高到低：

1. 用户当前明确要求；
2. `docs/acceptance/phase0.md` 中的项目总纲领；
3. 当前 Phase 的验收文档；
4. `README.md` 和代码中的现有公共 API。

如果代码与验收文档冲突，应优先修改代码。若确定架构原则需要改变，应先更新 Phase 0 和当前 Phase 文档，再修改实现。

## 2. Project Direction

项目定位为：

> A layered tool-routing and topology optimization framework for AI agents.

核心原则：

- Graph 是允许 Agent 搜索的空间，不是预先计算好的唯一答案；
- Layer、provider 和 worker 决定 ToolEdge；
- `consumes / produces` Schema 只负责验证已声明 Edge，不负责自动建边；
- Route 是允许同层多个 Tool 的执行子图，不是严格 Tool Chain；
- Declared Topology 必须与未来由回归结果派生的 Active Topology 分离；
- Fast Regression 只分析 Metadata，不执行真实 Tool，也不修改 Topology；
- Slow Regression、Trace、Pruning、Ranking 必须在对应 Phase 到来后再实现。

禁止恢复已经废弃的主线：

```text
ArtifactKey
Goal(produces=...)
Producer Resolution
Backward Planner
Minimal Dependency DAG
Provider Priority Planner
```

### 2.1 已评估并暂时废弃的方向（2026-09 裁定）

以下方向经过评估，明确**暂不开发**，现有限制保持不变：

```text
方向一：涌现拓扑 / 薄声明
  取消严格 layer / provider / worker 声明，让拓扑结构在调用过程中自动产生
  （含数据流诱导层序、类型提议连接等一切变体）。

方向二原始形态：图层递归 / 真环
  在拓扑图层面允许 cycle 以表达递归或"最终能力"。
```

裁定与理由：

- 现阶段**保持严格拓扑约束**：Layer 有序 + provider/worker 双向白名单
  决定 ToolEdge；`consumes / produces` 只验证已声明边，绝不建边
  （Phase 0 §6–7 不变）；
- 涌现拓扑会同时拆掉三面承重墙：Declared/Active 分离（回滚与剪枝安全
  的来源）、opportunity 统计语义（剪枝证据的根基）、TopologyFilter
  （在线约束的执行点）；其"降低接入负担"的目标中，clerical 部分可用
  与拓扑模型解耦的手段达成（见 §7 可开发方向 3），无需改拓扑模型；
- 图层真环会摧毁 route_id / opportunity / Counterfactual / 在线有界执行
  的语义；递归需求以"复合节点 + 有界展开"实现（见 §7 可开发方向 2）；
- 重启条件：任何一项要重启，必须先起草新的 Phase 验收文档，论证如何
  保住上述语义后再评估。

## 3. Phase / Step 推进流程

每次实现 Phase 或 Step 时，按以下顺序推进：

1. 完整阅读 `phase0.md`、当前 Phase 文档及本文件；
2. 明确本次 Step 的必须实现项、非目标和验收测试；
3. 检查工作区状态，识别并保留用户已有修改；
4. 制定只覆盖当前 Step 的实现计划；
5. 先修改核心领域模型，再修改 Registry、服务层和公开 API；
6. 增加对应单元测试和最小集成测试；
7. 更新 README、示例和当前 Phase 的实现状态；
8. 运行全量验证；
9. 检查 diff，确认没有混入下一 Step 或无关重构；
10. 按“分批提交策略”创建本地提交。

严格遵守阶段边界：

- 不因为“后续可能需要”而提前实现下一 Step；
- 不为尚不存在的第二个实现提前设计抽象工厂或插件系统；
- 新增模型或模块必须能对应当前 Step 的明确验收项；
- 如果需求只要求 Step 1/2，不得顺带实现 Step 3 的 Coverage Analyzer；
- Phase 2 的测试不得依赖真实 LLM、数据库、HTTP、MCP 或 Tool 执行。

## 4. Implementation Rules

- Python 版本遵循 `pyproject.toml`；
- 所有公共核心代码必须有类型标注；
- 领域数据优先使用 `dataclass`；
- 公共失败使用项目自定义异常，不泄漏 `KeyError`、`ValueError` 等作为主要业务错误；
- 相同 Registry 和输入必须产生确定性顺序；
- 名称集合对外输出前按名称升序规范化；
- Capability 必须使用 lowercase dot-separated 格式；
- Tool、Layer、Scenario ID 等唯一性必须在边界处校验；
- JSON Loader 必须严格校验，并提供包含位置或对象标识的错误信息；
- 核心模块不得绑定具体 LLM Provider、MCP、Web Framework 或外部服务。

## 5. Validation Gate

每批功能完成后至少运行：

```bash
python -m pytest -q
python -m compileall -q src tests main.py
git diff --check
```

如果修改了示例，还需要实际运行对应示例。如果修改了 JSON 资产，还需要使用 Loader 或 JSON 工具验证文件。

交付前必须确认：

- 全量测试通过；
- 新功能有正向与失败路径测试；
- 示例与当前公共 API 一致；
- 没有陈旧模块或文档继续宣传已废弃架构；
- `git status --short` 中只包含本次任务相关变更。

不得通过删除、跳过或弱化既有有效测试来制造通过结果。架构换轨时，可以删除只验证已明确废弃行为的测试，但必须用新架构的对应验收测试替代。

## 6. 分批提交策略

实现完成并验证后，应按职责创建小而完整的本地提交。推荐顺序：

1. **Docs / Spec**：架构原则、Phase 文档、README 定位；
2. **Core Models**：领域模型、异常、decorator；
3. **Registry / Engine**：Registry、Topology、Analyzer、Runner 等实现；
4. **Tests / Examples**：单元测试、集成测试、Scenario Dataset、示例；
5. **Cleanup**：仅在确有必要时单独提交迁移清理或删除陈旧代码。

一次提交可以合并相邻类别，但必须满足：

- 提交具有单一、清晰的目的；
- 不混入无关格式化或用户修改；
- 暂存前检查 `git status --short`；
- 提交前检查 `git diff --cached --stat` 和必要的 staged diff；
- 提交后重新运行相关测试；
- 最后确认工作区是否干净。

提交信息采用 Conventional Commits 风格，例如：

```text
docs: define phase 2 fast regression scope
feat: add capability registry
feat: add scenario suite loader
test: cover phase 2 scenario validation
refactor: remove deprecated artifact planner
```

提交操作规则：

- 默认只创建本地提交，不主动 push；
- 未经用户要求，不 amend、rebase、squash 或改写已有历史；
- 未经用户要求，不创建 Tag、Release 或 Pull Request；
- 如果工作区包含用户修改，必须避开或明确分离，不能擅自纳入提交；
- 如果一个批次验证失败，不得提交该批次；
- 用户明确要求“提交更改”时，优先按上述职责拆分，而不是创建一个巨型提交。

## 7. Current Boundary

当前实现进度以各 Phase 验收文档（含其 `phaseN-plan.md` 的实现进度表）为准。

已实现：

```text
Phase 1  Layered Declared Topology（全部完成）
Phase 2  Fast Regression（Step 1-10 全部完成：
             CapabilityRegistry / Scenario / Coverage / Route Search /
             Reports / Resolver / Ollama Resolver / Baseline Diff / CLI）
Phase 3  Slow Regression（Step 1-14 全部完成：
             Execution / Router（free + basefast seed）/ Trace / ObservedRoute /
             Evaluation（Structured / LLM Judge / Composite）/ Fixtures /
             Observation Stats / LLMRouter / 持久化 / CLI / 离线 Demo）
Phase 4  拓扑学习与安全剪枝（Step 1-13 全部完成：
             Evidence / Candidate / Protection / TopologyPatch /
             Counterfactual / Probe（basefast 定向 seed）/ Batch /
             Fast+Slow Validation Gate / Route Diversity Guard /
             DatasetSplit / TopologyVersion（commit / rollback）/ optimize CLI）
Phase 5  Route 排名与分级（Step 1-9 全部完成：
             ranking/ 顶包：stats / profile / eligibility / pareto / tier /
             family / report；`tool-topology rank` CLI 消费 slow artifacts）
Phase 6  在线路由（Step 1-9 全部完成：
             online/ 顶包：catalog / selection / balancer / fallback /
             runtime / telemetry；`tool-topology select` 干跑 CLI；
             在线遥测经 online_results_to_trials 回流离线闭环）
```

Phase 0-6 全部完成，项目主循环（Declare → Fast → Slow → Prune → Rank →
Route → 回流）闭合。方向一（涌现拓扑）已评估并暂时废弃（见 §2.1），
严格拓扑约束保持不变。当前可继续开发的方向（每项启动前必须先起草
新的验收文档；优先级由用户裁定）：

```text
核心内方向（不动严格拓扑约束）：

1. 资源 facade 计量（管道拦截）
     验收规格已起草：docs/acceptance/resource-metering.md，待审核。
     框架提供资源句柄（memory / llm / metered 通用包装），工具从管道走则
     访问计数与 token 自动精确计量（三档 MeteringSource 诚实标注）；
     计费基准保持声明值不变，实测值作证据与漂移信号。
     解决"监控不依赖工具上报"，与拓扑模型解耦。

2. 复合节点（方向二的采纳形态）
     "最终能力"作为宏节点入图：对外暴露 consumes/produces/capabilities/
     cost 接口，内部是更小的子拓扑（同一套 declare→prune→rank 机制递归
     适用）；外层保持无环，递归以自相似 + 有界展开实现，不引入图层面
     cycle。需评估对 route_id / 统计 / 剪枝的分层影响。

3. 接入辅助（降低接入负担中与拓扑解耦的部分）
     装饰器从类型注解自动推断 consumes/produces（类型不建边，仅免除
     重复声明）；capabilities 由 LLM 批量提案、人工审阅 diff 确认。
     目标：框架边际接入成本趋近 OpenAI tool spec 基线。

4. optimize 三段式编排补全
     analyze / validate / commit 端到端 CLI 编排（Phase 4 组件已齐，
     目前仅 run_scale.py 演示联动；commit 保持显式人工确认）。

工程化扩展（不属于核心假设验证范围，见 phase6.md §23）：

多租户服务化 / 在线自适应 / 熔断器 / 监控体系 / LLMRouter 真实规模实跑
校验等。
```

后续任务必须从用户裁定的方向起草验收文档开始；未经用户明确调整，不得启动已废弃方向（§2.1）。
