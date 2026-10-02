# Discovery & Routing — 发现与路由里程碑

> 定位：办公靶场之后的下一阶段。主线是把**种子生命周期捋回正位**
> （fast 可行链 / 模型发现 → 复放验证 → 固化 seeds.json → slow 定向
> 探索 → 在线复用），并完成 **LLM 首次坐上路由座位** 的实测（证据
> 空洞 #3）。同时清偿第二轮真实域暴露的架构裂缝（双执行路径分歧、
> 声明期诊断缺口）。不改变任何架构原则（严格拓扑 / 类型不建边 /
> Declared-Active 分离全部不动）。
>
> **用户裁定（2026-10 会话确认，优先级最高）**：
> 1. 种子的产生方式回归设想：先跳出既有枚举路径，用最简单的方式——
>    **让模型在合法池内自行选工具实现场景，期间真实调用的工具链经
>    复放验证后固化到 seeds.json**；
> 2. **覆盖判定权保留在静态 fast**（模型发现失败 ≠ 未覆盖）；模型
>    发现只产出实证种子，两者互补不互替。

---

## 0. 动机：一条被倒置的生命周期 + 一次从未发生的实测

```text
设计意图（phase 2/3）   场景 → fast（RouteSearch 候选链）→ 固化 seeds
                        → slow 围绕种子定向探索 → rank → 在线复用
办公靶场现状            场景（手写期望能力）→ build_seeds 手写映射表
                        → 种子 → 覆盖由种子见证反推（因果倒置）
原因                    RouteSearch 在 51 节点稠密拓扑上指数爆炸
                        （修复前单场景 >120s），被迫绕行
变化                    e0c1e95 修复后 RouteSearch ≈2s/场景，
                        正流程重新可行；且用户裁定引入模型驱动发现
```

同时，办公靶场作为第一个"宽拓扑 + 真实证据"的域，暴露了一批
框架级裂缝：同一段声明存在**两条行为不一致的执行路径**；一批
本可在声明期拦住的域作者踩坑面没有任何诊断；LLMRouter 至今
只有 fake 注入记录。这些问题彼此独立，但共同指向同一主题：
**发现（discovery）与路由（routing）的机制补全**。

---

## 1. 问题全景（本轮全部暴露项）

### 1.1 已修复留档（不在本里程碑范围内，证据见 notes）

| # | 问题 | 修复 |
| --- | --- | --- |
| F1 | RouteSearch 预算只约束递归，子集扫描指数失控（单场景 >2h） | e0c1e95，预算约束每个被考察子集 + has_edge O(1) 索引 |
| F2 | pipeline.analyze 节点/边候选混淆 + CounterfactualResult 字段误用 | e0c1e95 |
| F3 | Tier 参照池含零成功路线，FAST/BALANCED 结构性不可达 | e0c1e95 |
| F4 | 域作者踩坑面：同层依赖返工、sys.modules 裸名遮蔽、计量上下文等 | 过程留档 office-battlefield-notes.md §1/§2 |

### 1.2 开放问题（本里程碑的对象）

**P1 种子生命周期倒置：fast → seeds 的固化桥缺失**（批次 A）

- 现状：seeds.json 历史上全部手写——退款靶场手写、办公靶场用
  "capability→工具"映射表 + 哈希轮换；`load_seed_routes`（cli.py:370）
  只读不写，代码库里从未存在"从 fast 报告的
  `candidate_routes`（phase2.md §1964）固化种子"的路径。
- 影响：种子与 fast 脱钩，覆盖语义靠"种子见证反推"支撑
  （office-battlefield-notes.md §3.3）；"发现可行链 → 成为该业务
  种子"的生命周期不存在。

**P2 模型驱动发现缺失：LLM 从未坐上路由座位**（证据空洞 #3，批次 B/E）

- 现状：LLMRouter（router/llm_router.py）仅 fake 注入；所有路由
  都是 basefast 扩展/字典序 free。路由 prompt 的鲁棒性、合法池内
  的选路质量、token 成本、失败模式全部未知。
- 机会：slow 已原生支持 router 模式，ObservedRoute 已记录
  "期间真实调用的工具链"——模型发现只差最后一厘米（固化写出器）。
  且模型链是**执行见证**的（静态可行链从未执行过），种子质量
  升级为"实证可行"。

**P3 种子无复放验证与固化流程**（批次 A/B）

- 现状：无论静态链还是未来的模型链，写入 seeds.json 前都没有
  "1 次确定性复放仍成功"的验证、无 canonical 去重、无最短链偏好。
  模型链一次成功可能是侥幸（冗余兄弟恰好没炸）。

**P4 seeds 无版本/拓扑绑定**（批次 A）

- 现状：seeds.json 只按 scenario_id 索引（cli.py:370-383），换拓扑后
  陈旧种子静默降级（baseline ∩ available 后残缺），无告警、无版本
  指纹。种子应当像拓扑版本一样可审计。

**P5 JSON 拓扑丢类型 → 双执行路径语义分歧**（批次 C，架构级）

- 现状：JSON 拓扑按设计不携带 consumes/produces（loader 建出的
  ToolSpec schema 为空），TopologyFilter 的类型感知可用性过滤在
  JSON 路径上**静默退化**——同一份声明的 Python 路径与 JSON 路径
  行为不一致（office 实测：同场景同 trial 走不同路线、成功率
  0.2 vs 0.5565，office-battlefield-notes.md §4.2）。办公靶场用
  "Round 2 一律 apply_patch 内存拓扑"绕过，但裂缝对一切 CLI/JSON
  用户敞开："可执行 JSON" ≠ "与 Python 声明等价"。

**P6 同层依赖无声明期诊断**（批次 D）

- 现状："同层工具互不依赖"（LayerExecutor 并发语义）只写在执行器
  docstring 里；声明期对"某工具的 consumes 只能由同层产出"完全
  无警告（SCHEMA_MISMATCH 只查已声明边）。办公域 10 个工具签名
  因此返工。

**P7 形参名优先解析的槽名冲突无警告**（批次 D）

- 现状：实参解析形参名优先、类型兜底（executor.py:174-191）；
  形参名撞上其他槽名会静默取错产物。办公域靠"形参名=槽名"的
  约定自保，框架不校验。

**P8 域包范式不统一**（批次 D）

- 现状：slow_refund 用裸名导入 + sys.path 引导（单域时代安全），
  office 用包路径 only（多域共存必须）。规范只存在于
  office-battlefield-notes.md §2.1 与 checklist，无脚手架/校验。

**P9 per-tool timeout 与变体延迟/注入停顿无联动保障**（批次 D）

- 现状：超时线必须落在"最慢正常变体"与"注入故障"之间；办公域
  放大变体延迟后 0.08s 超时线立刻误伤（office-battlefield-notes.md
  §4.4）。无声明、无校验、纯靠人记。

**P10 覆盖报告双机制的等价性靠论证**（批次 A 内验证）

- 现状：办公域 coverage 由种子见证推导（线性），与精确枚举语义
  等价是**论证**出来的；精确枚举的候选列表在宽拓扑仍受预算截断
  （可行性有保证，候选不全）。需要一次小拓扑上的双机制对照测试
  把等价性变成可执行断言。

**P11 Tier 口径未成文**（批次 E）

- 现状：参照池口径（business_success_count > 0）与容差默认值是
  在办公域上调出来的，随修复提交落了代码，但语义决策未写进
  ranking 文档；失败率不同的域可能需要不同口径。

**P12 在线回流 trial 无业务评估**（批次 E 观察项）

- 现状：`online_results_to_trials` 按设计置 evaluation=None
  （业务判定留在离线侧），在线侧只有 SERVED 口径。空洞 #3 的
  实测需要"路由质量"的在线度量设计（路由命中率/回退率/首选
  Tier 分布），本轮先记录口径，不扩遥测 schema。

**P13 fake 质量维度是合成的**（批次 E 前提）

- 现状：办公域全部质量证据来自按 prompt 长度打分的确定性 fake；
  真实 Ollama 的质量/成本/漂移证据为空。批次 E 的实跑前提。

---

## 2. 原则

- **宪法不动**：Layer/provider/worker 决定 ToolEdge；consumes /
  produces 只验证已声明边，绝不建边；Declared/Active 分离；同层
  并发不可依赖；§2.1 废弃方向（涌现拓扑/图层环）不碰；
- **覆盖判定权归静态 fast**：模型发现失败 ≠ 未覆盖；模型产物只有
  经过复放验证才可固化（用户裁定 #2）；
- **零远程依赖不变**：LLM 只接本地 Ollama；离线测试一律 fake
  （FakeRouter 脚本化选路 / fake capability resolver）；真实 Ollama
  是可选实跑路径，不是测试依赖；
- **既有测试只增不删**；核心改动（批次 C）必须带往返一致性测试；
- **可复现**：发现跑允许不可复现，固化产物（seeds.json）必须可
  确定性复放。

---

## 3. 批次划分

```text
批次 A  固化桥（静态）    fast candidate_routes → 复放验证 → seeds.json；
                          seeds 版本/拓扑绑定；P10 双机制对照测试
批次 B  模型驱动发现      slow+LLMRouter 一次发现跑 → ObservedRoute →
                          复放验证 → 同一 seeds 格式；FakeRouter 离线
                          测试；真实 Ollama 可选路径
批次 C  双路径等价        JSON 拓扑类型保真（loader 扩展或等价方案），
                          TopologyFilter 行为统一 + 往返一致性测试
批次 D  声明期诊断        同层依赖静态诊断；槽名冲突警告；域包规范
                          成文/脚手架；timeout 联动检查
批次 E  路由证据          真实 Ollama 规模发现跑；basefast/free/llm
                          三模式对照；路由质量与成本度量；报告落盘；
                          Tier 口径成文
```

依赖关系：A 独立可先行；B 依赖 A 的固化与验证设施；C 独立但建议
在 B 之前（模型发现的成果要能经 JSON 路径复放才算落地）；D 独立；
E 依赖 A+B。

> **工作流（用户裁定）**：每批次单独推进；开工前在配套的
> `discovery-routing-plan.md` 细化该批次计划并展示，验收通过后
> 才开始实现。批次 A 计划已展示，待验收。

## 4. 验收标准（草案）

- [ ] A：任一覆盖场景可由 `fast 报告 → seeds.json` 一条命令固化；
      固化前的复放验证拒绝"复放不成功"的链；seeds 携带拓扑指纹，
      错配时显式报错；双机制覆盖判定在小拓扑上断言一致
- [ ] B：FakeRouter 驱动的发现跑离线全测；真实 Ollama 发现跑为
      可选路径；模型链经复放验证后与静态链写入同一 seeds 格式；
      解析失败/选非法池外工具被归类并留痕
- [ ] C：同一声明经 Python 与 JSON 两条路径构建后，TopologyFilter
      对任意状态的可用性输出一致（往返一致性测试）；office 全套
      回归不变绿转红
- [ ] D：同层依赖构造样例在声明期得到明确诊断；槽名冲突产生警告；
      域包规范成文（含裸名禁令、计量上下文、槽名约定 checklist）
- [ ] E：三模式对照报告落盘（覆盖率/成功率/成本/延迟/路由决策
      质量各一表）；LLMRouter 的失败模式分类进 slow 工件；
      Tier 口径写入 ranking 文档
- [ ] 全量测试 + compileall + diff-check 通过；AGENTS.md §7 与
      README 更新

## 5. 本里程碑不做

```text
远程 LLM API                仅本地 Ollama（办公室先例不变）
在线自适应 / 熔断 / 多租户   phase6.md §23 工程化扩展，不属本阶段
MCP 接入                    平行方向 mcp-import
遥测 schema 扩展            P12 只记录口径
涌现拓扑 / 图层环            §2.1 废弃裁定不变
复合节点批次 F               仍在 office-battlefield.md 后置位
```

## 6. 提交划分

```text
docs    本里程碑文档
feat    批次 A：固化桥 + seeds 绑定 + 对照测试
feat/test   批次 B：发现跑 + FakeRouter 离线测试
feat    批次 C：类型保真 + 往返一致性测试（动核心，独立评审）
feat/test   批次 D：诊断 + 规范成文
feat    批次 E：三模式对照 + 证据报告
```

## 7. 实现进度

| 批次 | 状态 | 说明 |
| --- | --- | --- |
| A 固化桥（静态） | [x] | 已实现：fast --out-dir 落盘、seed_export 复放验证固化、seeds v2 指纹绑定、P10 对照测试 |
| B 模型驱动发现 | [x] | 已实现：discover_seeds（发现跑 → 复放验证 → 固化，source=model-discovery）、ScenarioScriptedRouter、CLI seeds discover（--router-config 真实 Ollama / --scripted-router 离线）|
| C 双路径等价 | [ ] | |
| D 声明期诊断 | [ ] | |
| E 路由证据 | [ ] | |
