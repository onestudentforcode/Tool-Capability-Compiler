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

## 7. Phase 2 Current Boundary

当前 Phase 2 实现进度以 `docs/acceptance/phase2.md` 为准。

已实现：

```text
Step 1: Tool capabilities + CapabilityRegistry
Step 2: Scenario + ScenarioSuite + ScenarioLoader
Step 3: Gold Mode Coverage Analyzer (COVERED / UNCOVERED)
```

尚未进入：

```text
Step 4: Candidate Route Search
Step 5+: Coverage Status, Report, Resolver, Baseline, CLI
```

后续任务必须从当前最早未完成 Step 开始，除非用户明确调整优先级或 Phase 文档。
