# Discovery & Routing 实施计划

> 配套 `discovery-routing.md`（验收文档）。工作流约定（用户裁定）：
> 每个批次单独推进；**开工前在本文件细化该批次计划并展示，验收
> 通过后才开始实现**；实现完成跑全量验证门后再提交。本文件随
> 批次推进滚动更新。

---

## 批次 A 计划：fast → seeds 固化桥（静态）

状态：**已实现并验收**（612 测试全绿） ｜ 对应问题：P1 / P3 / P4 / P10

> 实现中发现的关键事实（已纳入测试设计）：RouteSearch 的候选表带
> **子集支配剪枝**——工具集包含已找到更小链的新候选会被剔除，因此
> 候选全是"子集极小链"；"补一个生产者"式的超集修复链不会出现，
> 备选修复必须是**使用不同工具**的平行链（批次 A 的旗舰测试即按此
> 构造：冗余 data.read 双提供者，坏实现先序、好实现兜底）。另据
> 执行语义，跨层种子若缺下层工具会在首层空选 FINISH（空路线），
> 复放判据因此包含"observed route 非空且覆盖期望能力"的守卫。

### A.0 前置事实（已核实）

- `regression fast` 的完整报告（含逐场景 `candidate_routes`）**只在
  内存中**：CLI 输出渲染文本，可选落盘的 baseline 不含链
  （cli.py:349-366）。固化桥需要先把完整报告持久化。
- `RouteSearch` 产出的候选链已按 `(tool_count, fingerprint)` 升序
  排序（route_search.py:83-86），"最短优先"策略可直接取序。
- 拓扑指纹可复用 `declared_fingerprint(topology)`（与 optimize
  三段式同一口径）。
- `load_seed_routes`（cli.py:370）只认 `{scenario_id: {layers,
  capabilities}}` 旧格式——新格式必须向后兼容。

### A.1 交付物

1. **fast 报告落盘**：`regression fast --out-dir DIR` 新增可选参数，
   写 `report.json`（FastRegressionReport 完整序列化，含逐场景
   candidate_routes 与覆盖判定）。缺省行为不变（只打印）。
2. **核心模块 `regression/seed_export.py`**：
   - `export_seeds(topology, suite, fast_report, *, replay_trials=1,
     max_verify=3, require_eval=False) -> SeedsPayload`
   - 策略：每场景取候选序列前 `max_verify` 条，**逐条复放验证，
     首条通过即固化**；全败则该场景无种子并记录原因（留档）。
   - 复放验证：`SlowRegressionRunner` 单场景 1 trial、seeds 仅含
     该链；成功判据默认 = `execution_status COMPLETED` 且
     observed route 覆盖场景期望能力（核心桥不绑业务评估器）；
     `require_eval=True` 时升级为评估通过（域可传入评估器）。
   - 仅处理 covered 场景；uncovered 无链可固化（诚实缺席，记录）。
3. **seeds.json v2 格式**：头部
   `{format_version: 2, topology_fingerprint, suite: {name, version},
   source: "fast-report", generated_at}` + 原 scenarios 映射；
   `load_seed_routes` 向后兼容旧格式；`regression slow --basefast`
   遇到"文件含指纹且与当前拓扑不符"默认报错
   （`--allow-seed-mismatch` 显式豁免）——P4 的绑定语义。
4. **CLI**：新增 `tool-topology seeds export --topology --scenario
   --fast-report --out [--replay-trials] [--max-verify] [--require-eval]`
   子命令组。
5. **P10 双机制对照测试**：3 层 6 工具小拓扑，逐场景断言
   CoverageAnalyzer（精确枚举）与"固化桥产物 + 种子见证判定"
   的 covered 结论完全一致——把办公域靠论证的等价性变成可执行断言。

### A.2 测试与验证门

- 单测：报告序列化往返、候选选择策略（最短优先/全败留档）、
  复放判据（COMPLETED vs require_eval）、指纹写入与 mismatch 报错、
  旧格式向后兼容；
- 集成：3 层确定性小拓扑（无 LLM）端到端 `fast --out-dir →
  seeds export → slow --basefast`；
- P10 对照测试；office 全套回归不红；全量 pytest + compileall +
  diff-check。

### A.3 明确不做

不替换 office `build_seeds`（留作对照，替换属批次 B/E 的消费侧
决策）；不动 RouteSearch 算法本体；不涉及模型发现（批次 B）；
不加远程 LLM。

### A.4 工作量预估

核心模块 ~150 行 + fast 落盘 ~40 行 + CLI ~50 行 + 测试 ~250 行；
单一 `feat` 提交（含测试）。

---

## 批次 B 计划：模型驱动发现

状态：**已实现**（621 测试全绿） ｜ 对应问题：P2（LLM 首次上路由座位的
机制与离线实测路径）、P3（复放验证，与批次 A 共用）

### B.0 前置事实（已核实）

- slow 原生支持 router 模式：逐层把 RoutingContext（query、当前层、
  可用池、状态摘要、已走层）交给路由器，决策经
  `validate_decision` 对合法池校验；路由失败（含选池外工具、解析
  失败）在 runner 中归类为 ROUTING_ERROR trial。
- LLMRouter（router/llm_router.py）决策格式为
  `{"action": "execute"|"finish", "selected_tools": [...], "reason": ...}`，
  支持 `_http` 注入 → **可全离线测试**；重复工具名被容忍（去重）。
- FakeRouter（按层全局映射）不分场景；发现按场景逐个跑，因此需要
  按场景的脚本路由器。
- 批次 A 的 `_replay_verified` 判据与 SeedsPayload 组装可直接复用。

### B.1 交付物

1. **seed_export.py 小重构**：`_replay_verified` → 公共
   `replay_verified`；抽出 `build_seed_payload(...)` 供两条路径
   （fast-report / model-discovery）共用 v2 组装。
2. **新模块 `regression/seed_discovery.py`**：
   `async def discover_seeds(topology, suite, *, router_factory,
   replay_trials=1, evaluator=None) -> SeedsPayload`
   - 逐场景独立跑一次**发现 slow**（单场景套件、discovery_trials=1、
     router=router_factory(scenario) 产出、router_config_id=
     "model-discovery"）→ 取 ObservedRoute → **确定性复放验证**
     （与批次 A 同判据）→ 通过才固化；
   - payload `source = "model-discovery"`，指纹绑定同 v2；
   - entries 状态扩展：`discovery-failed`（发现跑未完成/路由失败/
     评估失败）与 `replay-failed`（发现成功但复放不过——模型的
     链不可确定性复现时不许固化）。
3. **`ScenarioScriptedRouter`**（router/fake_router.py，与 FakeRouter
   同处）：按 `{layer: [tools]}` 逐层查表返回决策，未列层 FINISH；
   供离线确定性发现与测试。
4. **CLI**：`tool-topology seeds discover --topology --scenario --out
   [--router-config JSON | --scripted-router JSON] [--discovery-trials]
   [--replay-trials] [--require-eval / --expected-fact]`
   - `--router-config`：真实 Ollama（字段同 slow：base_url / model /
     temperature / max_tools_per_layer）→ LLMRouter；
   - `--scripted-router`：离线确定性发现脚本
     `{scenario_id: {layer: [tools]}}`，标注"测试与可复现实验用"。
5. **测试（tests/unit/test_seed_discovery.py + CLI 集成）**：
   - 脚本路由给出好链 → frozen（source=model-discovery）；
   - 脚本选中坏工具（source_bad）→ 发现跑 LAYER_ERROR →
     discovery-failed；
   - 脚本选池外工具 → ROUTING_ERROR → discovery-failed；
   - **非确定性防护**：计数器工具（发现第 1 次调用成功、复放第
     2 次抛错）→ entry replay-failed——固化必须过确定性复放；
   - LLMRouter + fake `_http`（返回合法决策 JSON）→ 真路由器驱动
     发现的离线证明（P2 机制级）；
   - 评估门：evaluator 失败 → 不固化；
   - CLI：`seeds discover --scripted-router` → seeds.json →
     `slow --basefast` 复放 → 指纹绑定同批次 A。

### B.2 明确不做

真实 Ollama 规模实跑与三模式对照（批次 E）；不改 LLMRouter 的
prompt/解析本体（其真实鲁棒性正是批次 E 的验证对象）；不改
basefast 扩展语义；发现失败不做自动重试（记录为批次 E 备选）。

### B.3 决策点（请验收时确认）

1. 发现跑固定 discovery_trials=1（成本考虑），不做多温度重试；
2. `--scripted-router` 进 CLI——让"无 LLM 的确定性发现"成为
   一等公民（同时服务测试与可复现实验）；
3. router_factory 以场景为键注入（RoutingContext 无场景标识，
   逐场景独立发现跑是唯一干净解）。

## 批次 C 计划：双路径等价

状态：**计划已展示，待验收** ｜ 对应问题：P5（JSON 拓扑丢类型 →
TopologyFilter 行为分歧，office-battlefield-notes.md §4.2）

### C.0 前置事实（已核实）

- 分歧的确切位置：TopologyFilter._schema_satisfiable 读
  `tool.spec.consumes`；JSON 路径的 ToolSpec schema 为空（类型按设计
  不在 JSON 里）→ 恒可满足 → 类型不可行的工具混进可用池。
- 执行本身不受影响（ToolExecutor 按 implementation 处理器的真实
  注解解析实参）——分歧只在**过滤器**（与 builder 的 SCHEMA_MISMATCH
  警告）。
- loader 是严格 schema（loader.py `_TOOL_FIELDS` 白名单，未知字段
  拒绝）；`implementation: module:attr` 已有 importlib 解析先例；
  `declared_fingerprint` 已含 nodes/capabilities/edges，不含类型
  （指纹口径保持不变，见决策点 3）。
- ExecutionState 可测试构造：`add_artifact(槽名, ArtifactValue(...))`。

### C.1 交付物

1. **loader 扩展（核心）**：`tools[]` 允许可选字段
   `consumes` / `produces`（"module:attr" 字符串数组）→ importlib
   解析为**真实类型对象**进入 ToolSpec（与 implementation 同一信任
   模型）；解析失败/非类对象 → 带位置信息的构建错误；字段缺省 →
   空元组（全部既有 JSON 文件零破坏）。composite 条目不变。
2. **导出器同步**：office 与 slow_refund 的 export_topology 输出
   consumes/produces——类型引用由 `type.__module__` + `__qualname__`
   **自动派生**（如 `examples.office.facts:SourceDoc`），无手工映射
   表；重新生成 examples/topology/office.json 与 refund_sandbox.json。
3. **往返一致性测试**（tests/integration/test_topology_path_equivalence.py，
   用 office 真域）：同一声明经 TopologyBuilder（Python）与
   TopologyLoader（JSON）构建后——
   - 逐节点断言 spec.consumes/produces 相同；
   - 对 context/extract/compose/verify/render × 多组状态（空、仅
     SourceDoc、+DataTable、+FactSheet+StyleSpec、全工件）× 若干
     previous_selected 组合，断言 TopologyFilter.available_tools
     输出完全一致；
   - builder 的 SCHEMA_MISMATCH 警告集合一致。
4. **loader 单测**（并入 test_topology_loader.py）：类型解析成功/
   坏引用报错/字段缺省向后兼容。
5. **office 全套回归**：621 测试不变绿转红（尤其种子桥与闭环）。

### C.2 明确不做

不改 TopologyFilter 的类型过滤语义（去类型化是错误方向）；不动
`declared_fingerprint` 口径（类型变更不影响指纹——剪枝判据的
连续性优先）；office 闭环暂不改回 JSON 路径（等价后属可选优化）；
不加新工具。

### C.3 决策点（请验收时确认）

1. 类型引用格式 = `"module:attr"`（importlib，与 implementation
   同一信任模型），由导出器自动派生；
2. 旧 JSON 文件无类型字段仍合法（= 今日行为）；**等价性保证只覆盖
   带类型字段的导出拓扑**——"可执行"与"等价"从此可区分；
3. `declared_fingerprint` 不纳入类型（种子/版本链在类型注解微调时
   保持有效）。

## 批次 D 计划：声明期诊断（占位，开工前细化）

目标：P6 同层依赖静态诊断、P7 槽名冲突警告、P8 域包规范成文、
P9 timeout 联动检查。待细化：诊断落点（builder 警告 vs 独立
lint 命令）。

## 批次 E 计划：路由证据（占位，开工前细化）

目标：真实 Ollama 规模发现跑；basefast/free/llm 三模式对照报告；
Tier 口径成文（P11）；P12/P13 口径记录。
