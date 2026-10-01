# Office Battlefield — 实现问题与解决方案记录

> 定位：`office-battlefield.md`（批次 A-E）实施过程中的问题排查记录。
> 每个问题按 **现象 → 根因 → 解决 → 教训** 归档，供后续域级靶场
> （以及任何在"宽拓扑 + 真实证据"规模上使用本框架的人）直接引用。
> 核心修复的代码已随提交 `e0c1e95` 落地；靶场工程决策随 `c1a2759`。

---

## 1. 架构认知类：三条"跑起来才看得见"的框架语义

这三个问题都不是 bug，而是框架的既定语义与直觉相悖。它们决定了
域工具的类型签名怎么写——写错只能返工。

### 1.1 同层工具互不依赖（最大的一次返工）

- **现象**：51 节点全拓扑冒烟跑 `regression slow`，4/4 场景
  Execution Failed；而 4 节点 L0-only 冒烟全绿。
- **根因**：`LayerExecutor`（execution/executor.py:217）对同层选中
  工具**并发执行**（asyncio.gather + 信号量），全部只读**进层前**
  的状态——"Same-layer tools never depend on each other" 是写在
  docstring 里的不变量。而工具清单初版让 L2 工具消费同层产物：
  draft_section 消费 outline_doc_gen 的 Outline、slide_copy 消费
  outline_slide_gen 的 SlideOutline、text_polish 消费同层 Draft、
  title_gen/text_expand 同理。这些消费关系在声明期完全合法
  （SCHEMA_MISMATCH 只是 warning），**在执行期永远不可满足**。
- **解决**：L2 消费一律重映射到 L0/L1 产物——draft_section
  (source_doc, fact_sheet)、slide_copy (source_doc)、text_polish
  (narrative, style_spec)、text_expand (fact_sheet)、title_gen
  (source_doc)、outline_slide_gen (source_doc)、draft_speaker_notes
  (source_doc)；formula_gen 由 (narrative) 改为 (aggregate_result)，
  使表格分析链自洽。Outline/SlideOutline 成为 L2 的"只产出"终端
  工件（架构允许，评估器经 state 快照消费）。
- **教训**：给域工具排类型签名前，先问"这个类型的生产者在哪一层？"
  同层=并发=互相看不见。**这个约束必须在给实现代理（子代理）的
  简报里显式写出**——初版简报漏了它，导致 10 个节点返工。

### 1.2 参数解析是状态匹配，不是边匹配

- **现象/根因**：ToolExecutor 按形参名（snake_case 槽名）优先、
  类型注解兜底从**全部已积累状态**解析实参；边只决定拓扑连通性，
  不决定实参来源。这带来两个推论：(a) 形参名必须等于消费类型的
  槽名（source_doc / fact_sheet / ...），否则同名槽会截走错误值；
  (b) 消费类型可以来自**上溯任意层**（如 render_doc 消费 L2 的
  Draft + L3 的 ReviewReport），不必相邻。
- **解决**：域内约定所有 handler 形参名 = 消费类型槽名，写进两个
  子代理简报的硬性规则；测试里加断言（形参名 == 槽名且注解 ==
  consumes）。
- **教训**：槽名约定要作为域包的硬规范，而不是靠执行期运气。

### 1.3 工具错误不会自动变成 trial 失败——评估器必须自己上升

- **现象**：规模实跑里 fake 的 4% 垃圾输出实际发生了（工具级
  TOOL_EXECUTION_ERROR），但 trial 层成功率纹丝不动，slow 工件里
  failure_category 只有 answer_error。
- **根因**：runner 只对 COMPLETED trial 做评估；整层全失败记
  LAYER_ERROR（trial 级 failure_category=None），层内**部分**工具
  失败时 trial 照常 COMPLETED，业务成功与否完全由评估器说了算。
  OfficeFamilyEvaluator 最初只查工件存在性，对 trace 里的工具错误
  视而不见。
- **解决**：OfficeFamilyEvaluator 先扫描 trace，任何工具 ERROR 即
  判失败并携带该工具的 error_category（timeout / tool_execution_error
  由此进入 trial 级统计）；再查家族工件。修后 slow 落盘含全部四类
  失败（answer_error / tool_execution_error / timeout / layer_error）。
- **教训**：评估器是"业务语义的守门人"，框架不会替你把工具级异常
  翻译成业务失败；这是设计（失败不等于不可用），域侧必须接住。

---

## 2. 模块与导入工程：裸名导入的 sys.modules 污染

### 2.1 facts/store 裸名遮蔽（批次 A 全量测试爆炸）

- **现象**：office 批次 A 落地后，`tests/unit/test_online.py` 等 5 个
  既有测试收集失败：`ImportError: cannot import name 'Digest' from
  'facts' (examples\office\facts.py)`—— Digest 是 slow_refund 的类型。
- **根因**：office 导出器沿袭 slow_refund 范式：`sys.path.insert(0,
  examples/office)` + 裸名 `import office` → office/tools_l0 的
  except 分支裸名 `import store` / `from facts import ...` → Python
  把 **office 的 facts/store 缓存为顶层名** `sys.modules['facts']`。
  pytest 随后收集 unit 测试时，slow_refund 插入自己的目录并
  `import refund` → 其 except 分支 `from facts import Digest` 命中
  **已缓存的 office facts** → 爆炸。两个域包的同名裸模块在共享
  pytest 进程里天然互斥。
- **解决**：office 包**只做包内相对导入**（`from . import store`），
  删除全部 try/except 双导入；导出器只引导 `src` + 仓库根，
  以 `from examples.office import office` 包路径导入，并在
  `_implementation_map` 用包限定名。测试一律 `from examples.office
  import ...`。
- **教训**：多域包共存时，"script 式裸导入"范式（slow_refund 单域
  时代的安全惯例）必须升级为"包路径 only"。新增域包时先全局检查
  裸名碰撞面（facts / store / fixtures 这类通用名是高危名）。

### 2.2 计量句柄必须在 ToolExecutor 上下文里用

- **现象**：测试直接 `await tools_l0.fs_read.handler()` 报
  `MeteringContextError: metering recorded outside a tool-call context`。
- **根因**：InMemoryStore 的 get/put 在无计量收集器时**拒绝服务**
  （计量诚实性设计：访问必须记账到某次工具调用，否则宁可失败）。
  收集器由 ToolExecutor.execute 挂载。
- **解决**：测试提供 `call_tool(node)` helper——mount_collector /
  unmount_collector 包住裸 handler 调用，模拟执行器语义。
- **教训**：绕过执行器直接调 handler 的测试都必须走这个 helper；
  这也顺带让测试能断言 access_counts / measured token。

---

## 3. 核心缺陷：首次被真实证据踩中的四个雷

退款沙盒每个工具都是唯一提供者 → 历史上的 optimize 补丁永远是空的，
以下代码路径**从未被执行过**。office 证据一进来，接连引爆（修复见
提交 `e0c1e95`）。这本身就是里程碑"证据空洞 #1"的实证。

### 3.1 pipeline.analyze 把节点候选当边候选拼接

- **现象**：`analyze()` 在产出首批 IDENTIFIED 候选时 TypeError:
  can only concatenate str (not "NoneType")。
- **根因**：`identified_edges = [c.source + "->" + c.target for c in
  candidates if c.status is IDENTIFIED and c.source]`——节点候选的
  `source=tool, target=None`，`c.source` 真值过滤拦不住它；同时
  disabled_nodes 的过滤写的是 `not c.source`，对节点候选永远为假
  → 节点剪枝永远进不了补丁。两处都应以 `c.kind == "edge"|"node"` 区分。
- **解决**：按 kind 分流。修后 analyze 正常产出 6 个 IDENTIFIED 边
  候选 + 非空补丁。
- **教训**：凡是"历史上恒为空的集合"，其下游代码等于零覆盖——
  新域首个非空结果要当作 fuzz 输入对待。

### 3.2 CounterfactualResult 字段误用

- **现象**：analyze 继续推进后 AttributeError: 'CounterfactualResult'
  object has no attribute 'scenarios'。
- **根因**：pipeline 读 `counterfactual.scenarios` + `getattr(item,
  "regressed")`；实际字段是 `checks` 元组 + `regressed_scenarios`
  property。
- **解决**：改用 `counterfactual.regressed_scenarios`（scenario_id
  列表语义不变）。

### 3.3 RouteSearch 预算不约束子集扫描（指数爆炸）

- **现象**：CoverageAnalyzer 单场景覆盖分析 >120s；analyze 全程
  >500s 超时（60 场景 × 反事实两次 fast 回归，完全不可用）。
- **根因**：`_enumerate` 逐层枚举极大链上**所有工具子集**；稠密
  "all" 白名单下极大链=全部 51 工具，compose 层单次调用要扫 2^21
  个子集，每个子集做 O(|left|×|right|) 互联检查。而预算
  `expansions` 只在**递归进入下一层**时 +1——被互联检查筛掉的
  子集是"免费"的，预算永远打不满，扫描照跑。
- **解决**（两步）：
  1. 预算改为**每个被考察的子集** +1，并在子集循环内检查预算
     （`route_search.py`）。小拓扑（退款 10 节点）枚举总量远低于
     预算，行为不变；宽拓扑按既有"可行性不依赖枚举预算"的见证
     回退语义截断。24.5s → 1.9s/场景。
  2. `Topology.has_edge` 原来在 550 条边的 tuple 上**线性扫描**且
     每次构造 ToolEdge 对象——route search 每场景探测数百万次。
     加 `frozenset` O(1) 边索引（纯缓存，语义不变）。
- **教训**：性能预算必须覆盖"被拒绝的尝试"，否则只是心理安慰；
  热路径上的成员查询永远先看数据结构是不是 O(1)。

### 3.4 Tier 参照池包含零成功路线

- **现象**：rank 落地后 23 个有标签路线**全部只有 QUALITY**，FAST/
  BALANCED 空——里程碑验收"≥2 非空 Tier"不可能达成。
- **根因**：`assign_tiers` 的 best_latency / best_cost 在**全部
  RANKED 路线**上取最优，而探索流量天然产生"又快又便宜但从不
  成功"的失败路线（如 14.9ms、成功 0.0）——它们把参照值压到
  任何成功路线都够不到的位置。success_ok 门只挡标签，挡不住参照值。
  退款沙盒从未做过真实 rank 演示，此语义从未被压力测试。
- **解决**：参照值只在 `business_success_count > 0` 的路线（至少
  成功交付过一次的"服务选项"）上取。第一次尝试用 evaluated_count > 0
  不够——业务失败的路线也有评估记录（quality 0.0、延迟短），
  必须以"成功交付"为口径。修后 fast 3 / balanced 2 / quality 12。
- **教训**："相对最优"类规则必须先问：参与比较的池子里，有没有
  根本不构成选项的成员？601 个既有测试全绿，说明这不是语义变更
  而是缺陷修复——但论证过程（两个口径的取舍）值得留档。

---

## 4. 靶场工程：规模带来的鲁棒性问题

### 4.1 全拓扑冒烟触网 + 批次 A 测试被后续批次击穿

- **现象**：批次 B/C 接线后，批次 A 测试从 1.5s 变 8 分 19 秒，
  且 2 个失败；本地 CLI 冒烟显示 Execution Failed 4/4。
- **根因**：两个独立问题叠加。(a) 批次 A 测试用 `office.build_topology()`
  ——51 节点后自由路由会探到 LLM 工具，`office_llm.RESOURCE` 指向
  本地 Ollama，未启动时每个调用吃满 60s 级连接超时；(b) 其中一个
  断言还写死了节点集合。
- **解决**：测试按批次收窄——黑板冒烟测试自建 L0-only 注册表 +
  内联 4 场景套件；"导出 JSON 可执行"测试改用内联 2 场景 +
  **basefast 种子把路线钉死在 context 层**，全程不触 LLM。
- **教训**：全拓扑冒烟与批次级测试要分层持有各自的拓扑；凡测试
  里会出现 LLM 工具的路线，要么注入 fake，要么用种子把路线锁在
  确定性层。

### 4.2 JSON 拓扑类型缺失 → TopologyFilter 行为分歧（Round 2 成功率跳水）

- **现象**：闭环 Round 2a（commit 后的 office-v0.1 拓扑，经
  TopologyLoader 从 JSON 加载）成功率 0.2，而 Round 1 同场景 0.5565；
  失败全是 schema_mismatch（chart_prepare 无 data_table 等）。
- **根因**：JSON 拓扑**按设计不携带 consumes/produces**（类型活在
  Python 声明里），loader 建出的 ToolSpec schema 为空。TopologyFilter
  的 `available_tools(layer, previous_selected, state)` 依赖 schema
  做"当前状态下类型可满足"的可用性过滤——Python 拓扑会排除类型
  不匹配的兄弟工具，JSON 拓扑不会 → 扩展计划 picks 到 chart_prepare
  这类"上一步根本产不出输入"的工具 → 整层全灭。同一 (scenario,
  trial) 在两轮走的是不同路线。
- **解决**：Round 2 执行用 `apply_patch(topology, patch)` 得到的
  **内存拓扑**（类型完备，与 Round 1 同语义）；committed JSON
  payload 保留为审计工件，只做节点数一致性 sanity check，不参与
  执行。`build_catalog`/在线服务同样消费内存拓扑。
- **教训**：`regression slow 可跑 ≠ 与 Python 声明等价`。JSON 拓扑
  是类型自由的世界，任何依赖 schema 的运行时行为（目前是
  TopologyFilter）在两条路径上不等价——要么给 JSON 加类型字段
  （核心 schema 变更，未做），要么执行路径统一走内存拓扑。这是
  本里程碑最重要的架构发现，已写入里程碑文档。

### 4.3 变体对比度不足 → Tier 分配在噪声里翻转

- **现象**：两次全量闭环（代码除一处与 rank 无关外相同）分别报出
  separating=[text.polish, text.summarize] 与 separating=[]——Tier
  结果不可复现的假象。
- **根因**：变体间模拟延迟差（快糙 6ms vs 慢稳 25ms）相对路线总
  延迟（~100-190ms）太小，单次运行的毫秒级抖动就能让路线的
  latency_median 跨过 best×1.2 的 FAST 门槛。Round 1 数据本身
  是确定的（三次运行的 route_stats 语义哈希一致），翻转发生在
  Tier 判定的临界值上。
- **解决**：按规格 §5 的本意**放大对比度**——慢稳系变体延迟
  0.025→0.1 等（tools_llm.py 变体表是单一事实源）；judge 分数改为
  随输入长度缩放（0.5 + 0.45×min(len,1500)/1500，verbose 长文→
  高分），fake 按变体 system 提示产出不同长度的内容；种子按稳定
  哈希在 section.draft / mail.draft 变体间轮换，让每个变体锚定
  自己的基线路线。修后分离 6 个 capability，且不依赖毫秒运气。
- **教训**：要让"分化"可被 rank 看见，分化量必须显著大于运行噪声；
  用确定性 fake 做靶场时，fake 的**内容差异就是计量差异的来源**，
  等长回文式 fake 会把四维分化压成一个维度。

### 4.4 超时参数与变体延迟的联动

- **现象**：延迟对比度放大后，steady 变体（0.1s）会撞上原有的
  0.08s per-tool 超时，正常流量会大面积 TIMEOUT。
- **解决**：per_tool_timeout 提到 0.15s（高于全部正常工具，低于
  translate 工厂 0.5s 的注入停顿）——TIMEOUT 流量仍然只来自
  偶发停顿。**教训**：超时线必须画在"最慢的正常变体"与"注入的
  故障"之间，且改动任何一侧都要检查另一侧。

### 4.5 catalog 的版本绑定：ranking 必须与所服务拓扑同版本

- **现象**：`build_catalog(office-v0.1 拓扑, office-v0 的 ranking)`
  抛 RouteCatalogError: ranking/topology version mismatch。
- **根因/解决**：这是框架的**正确**防线（版本错配即拒绝）。正确
  流程是 commit 之后在 office-v0.1 上重跑 Round 2a 慢回归、用它的
  工件做 rank（版本随之绑定为 office-v0.1），再喂 catalog。原先
  "用 Round 1 的 ranking 服务 v0.1"的想法本身违反版本链。
- **教训**：闭环脚本必须尊重"每个版本有自己的证据"—— prune 后
  的 Tier 结论来自剪枝后的实跑，而不是剪枝前的。

### 4.6 回流 trial 没有评估——成功口径必须换挡

- **现象**：Round 2 在线服务 60/60 SERVED，但回流 trial 的
  success_rate 算出 0.0。
- **根因**：`online_results_to_trials` 按设计置 `evaluation=None`
  （业务判定留在离线侧），旧 trial_metrics 按"有评估的 trial"取
  分母 → 空集 → 0。
- **解决**：trial_metrics 分挡——有评估用 evaluation.success；
  无评估（回流）用 execution_status == COMPLETED 作成功口径。
  两轮收敛的比较口径在报告里显式标注。

---

## 5. 小问题清单（一句话档）

- `unbound_tool_names` 返回 tuple 不是 set，断言写 `== ()`。
- `TokenUsage` 不在 `capability_runtime.resources` 顶包导出，从
  `capability_runtime` 顶层导入。
- Windows 下 `--out-dir /tmp/...` 在 Python 与 Git Bash 间路径映射
  不一致，对比脚本 glob 落空——临时产物放仓库内相对目录。
- 后台运行 `python - <<EOF | tail` 会丢全部输出——长任务一律重定向
  到文件再读。
- Windows 的 git CRLF 警告贯穿全程，属行尾换行提示，不影响校验门。
- 场景子代理发现规格矛盾：messy≥8 + sparse≥6 的表格链需求（14）
  超过表格链场景总数（12，含 2 条 uncertain-F）。裁定：把 table.parse
  打头的 deferred 场景计入"表格链"池，裁定过程写入测试 docstring
  （test_office_scenarios.py 的 is_table_chain）。
- 工具决定：`data_aggregate` / `chart_prepare` 对空单元格跳过或记 0
  （稀疏语料可跑）、`table_profile` 把空白记为空值率，`data_aggregate`
  对 "N/A" 类非空脏值抛错（脏语料真实失败）；`formula_scan` 干净表
  返回空 findings 而非报错。

---

## 6. 给下一个域的清单（Checklist）

1. 工具类型签名定稿前，逐条核对：消费类型的生产者在**更早的层**吗？
   （同层=并发=不可见）
2. handler 形参名 == 消费类型槽名；显式 consumes/produces；域包只做
   相对导入，杜绝裸名。
3. 测试直接调 handler 一律经计量上下文 helper；有 LLM 工具的路线
   要么注入 fake，要么用种子锁层。
4. 评估器负责把 trace 里的工具错误上升为业务失败，并回填
   failure_category。
5. 闭环/实跑前确认：per-tool 超时 > 最慢正常变体；fake 的内容差异
   足以撑起计量分化；rank 的参照池口径适合本域的失败率。
6. prune 后的执行用 `apply_patch`（内存），JSON payload 只做审计；
   rank/catalog/在线严格按版本链走。
7. 复现性验收用"语义哈希"（剥离时间戳与 latency 后对比），不要比
   字节。
