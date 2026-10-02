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

## 批次 B 计划：模型驱动发现（占位，开工前细化）

目标：slow + LLMRouter 一次发现跑 → ObservedRoute → 复放验证 →
与批次 A 同一 seeds 格式；FakeRouter 离线测试；真实 Ollama 可选。
待细化决策点：路由 prompt 与合法池的呈现方式；发现跑的 trial
预算；失败模式分类（解析失败/选池外工具）。

## 批次 C 计划：双路径等价（占位，开工前细化）

目标：消除 JSON/Python 两条执行路径的语义分歧（P5）。待细化的
核心决策：loader schema 扩展 carries 类型引用（module:attr 指向
类型对象）vs 过滤器去类型化 vs 其他方案；往返一致性测试的定义。

## 批次 D 计划：声明期诊断（占位，开工前细化）

目标：P6 同层依赖静态诊断、P7 槽名冲突警告、P8 域包规范成文、
P9 timeout 联动检查。待细化：诊断落点（builder 警告 vs 独立
lint 命令）。

## 批次 E 计划：路由证据（占位，开工前细化）

目标：真实 Ollama 规模发现跑；basefast/free/llm 三模式对照报告；
Tier 口径成文（P11）；P12/P13 口径记录。
