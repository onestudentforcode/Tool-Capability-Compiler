# Office Battlefield — 办公能力真实靶场里程碑

> 定位：域级靶场里程碑。用**办公能力域**（文档撰写 / PPT 制作 / 表格处理 /
> 邮件草稿）替换纯合成的退款沙盒，作为框架主循环的第一个真实领域验证场。
> 不改变任何架构原则（严格拓扑 / 类型不建边 / Declared-Active 分离全部
> 不动），只交付一个新域：语料、工具、场景、证据、闭环演示。
>
> **本里程碑的头号设计原则（用户裁定，优先级最高）：不在乎工具的输出
> 质量。在保证靶场可行的前提下，工具实现越简单越好。** 工具是给框架
> 产生证据的（计量 / 冗余 / 失败模式 / 路线分化），不是给办公用户用的
> 产品。验收检查结构与计量的正确性，不检查文采、排版与内容质量。

---

## 0. 动机：三个证据空洞（2026-10 代码事实基线）

```text
1. 从未发生真实剪枝    退款世界每个工具都是唯一提供者 → 全部 PROTECTED，
                       optimize analyze 历次产出空补丁；validate 对比两个
                       相同拓扑；commit 提交空补丁。剪枝路径零实证。
2. 只有单一合成域      全部证据来自手写退款沙盒；框架声明是通用路由框架，
                       但从未在第二个域上跑过主循环。
3. LLM 从未参与路由    LLMRouter 仅 fake 注入；在线是路线跟随。
                       "面向 AI agents" 的核心命题未被实测。
```

本里程碑直接解决 1 与 2，并为 3（LLM 路由校验，独立后续里程碑）备好
有东西可比的真实域。办公域的选择依据：

- 天然分层数据流（资料 → 结构化 → 起草 → 校验 → 渲染），且比退款
  （3 层）更深，能压相邻层假设；
- LLM 参与与确定性代码的边界天然清晰（起草靠 LLM，解析/校验规则/
  渲染靠代码），正好各占一层；
- 冗余提供者可以**真实**存在（同一 capability 的快糙版 vs 慢稳版），
  这是让剪枝、Pareto、Tier 有信息量的燃料。

---

## 1. 原则

- **简单至上（用户裁定）**：工具能一行实现就不写两行；prompt 最短可用；
  输出为受限 JSON 或短文本；确定性工具用最直白实现；语料最小。凡不
  影响证据产出的复杂度一律砍掉（见 §10 推迟清单）；
- **离线优先不变**：零第三方运行时依赖；全部测试离线（LLM 一律 fake
  注入）；真实 Ollama 实跑是可选的 Demo 路径，不是测试依赖；
- **无远程 API（用户裁定）**：LLM 只接本地 Ollama（`LLMResource` 现状
  即终态，base_url 指向本地）；远端注入的**设计**保留，不实现；
- **非幂等推迟（用户裁定）**：首期不包含任何有外部副作用的工具
  （发送 / 落盘 / 改写源文件）。所有 render 产出停在**内存中可序列化
  的 FileSpec**，终态可验证但不落盘；
- **MCP 不阻塞**：L0 读取工具首期用本地实现占位；mcp-import 是平行
  方向，其桥落地后仅替换 implementation 字符串，拓扑不动；
- **可复现**：随机性（模拟延迟抖动 / 偶发失败注入）由
  `(scenario_id, trial_index)` 种子确定；
- **不改变核心架构**：Edge 规则、探索模式、阶段边界、已验收行为全部
  不动；既有测试只增不删。

---

## 2. 工具导入机制

框架只认一种东西：**本地 Python 模块里的 async 函数，经
`"implementation": "module:attr"` 绑定进 JSON 拓扑**（可直接指向 `@tool`
装饰后的 ToolNode，加载器自动解包）。不存在"外部工具包导入"路径，
四类来源全部收拢为本地模块 + 绑定字符串：

```text
① 确定性代码   本地手写，标准库优先。docx/xlsx/pptx 不做真实格式解析：
               语料本身是简化格式文件（文档=受限 markdown、表格=csv、
               幻灯=json），读取工具解析为自有类型；渲染层产出 FileSpec
               （结构化文件规格，内存对象），不写 OOXML（见 §10）
② LLM 工具     本地实现：async 函数 = prompt 模板 + 宽松解析 + 经
               LLMResource 句柄调本地 Ollama。token/成本计量由管道拦截
               自动完成，工具侧零上报代码
③ MCP          本里程碑不接。L0 用 ① 占位；mcp-import 落地后换绑定串
④ 复合节点     现有 composite/ 工厂，本里程碑后置（§8 批次 F，可选）
```

**冗余变体不重复写代码**：一个工具工厂参数化（模型名 / prompt 风格 /
温度 / 模拟延迟 / cost_per_call），产出 N 个 ToolNode 分别注册。例如
`section.draft ×3` 是同一实现的三个配置，差异天然体现在计量数据里。
本地只有一个 Ollama 模型时，变体退化为 prompt/温度/延迟差异——身份
来自配置，不来自实现。

部署形态沿用既有范式：域包 `examples/office/`（tools / scenarios /
fixtures / export_topology.py），导出器以 Python 声明为唯一事实源生成
可执行 JSON，CLI 全链路消费。

---

## 3. 域模型：5 层与类型主干

```text
L0 context    读语料（文档/表格/幻灯源文件）            全确定性
L1 extract    提取与结构化（profiling / 事实 / 摘要）     代码为主，少量 LLM
L2 compose    起草与生成（大纲 / 成文 / 公式 / 文案）     LLM 为主 ★
L3 verify     校验与评审（规则检查 + LLM judge）          混合
L4 render     渲染（FileSpec 组装，不落盘）               全确定性
```

跨层类型主干（consumes/produces 只验证已声明边，不建边——宪法不变）：

```text
SourceDoc / DataTable / SlideDigest
  → FactSheet / Outline / FormulaSpec / SlideOutline / StyleSpec / ChartSpec
    → Draft / SlideCopy / Narrative / EmailDraft
      → ReviewReport（结构化检查结果 + judge 分数）
        → FileSpec(doc / sheet / deck / chart)
```

---

## 4. 首期工具清单（51 节点 / 42 实现 / 5 层）

实现数 < 节点数：差值全部来自工厂参数化变体。标注 ×N 的即冗余变体。

### L0 context（4）

| 工具 | capability | LLM | 消费 → 产出 |
| --- | --- | --- | --- |
| fs.read | `fs.read` | 否 | → SourceDoc |
| doc.read | `doc.parse` | 否 | 受限 md 语料 → SourceDoc |
| table.read | `table.parse` | 否 | csv 语料 → DataTable |
| deck.read | `ppt.parse` | 否 | json 语料 → SlideDigest |

### L1 extract（13）

| 工具 | capability | LLM | 备注 |
| --- | --- | --- | --- |
| table.profile | `table.profile` | 否 | 列类型 / 空值率 / 脏数据画像 |
| table.header_fix | `table.repair` ×2 | 规则 / LLM | 冗余 |
| text.segment | `text.segment` | 否 | 长文切段 |
| text.keyfact_extract | `fact.extract` | 是 | → FactSheet（事实核查锚点） |
| text.summarize | `text.summarize` ×2 | 快糙 / 慢稳 | 冗余 |
| text.translate | `text.translate` ×2 | 快糙 / 慢稳 | 冗余 |
| formula.scan | `formula.audit` | 否 | 审计源表已有公式 |
| data.aggregate | `data.aggregate` | 否 | 分组聚合 |
| chart.prepare | `chart.prepare` | 否 | → 图表就绪序列 |
| style.profile | `style.profile` | 是 | 范文 → StyleSpec |

### L2 compose（21）★ LLM 心脏层

| 工具 | capability | LLM | 备注 |
| --- | --- | --- | --- |
| outline.doc_gen | `outline.compose` ×2 | 快糙 / 慢稳 | topic+FactSheet → Outline |
| outline.slide_gen | `outline.slides` | 是 | 文档 → SlideOutline |
| draft.section | `section.draft` ×3 | 快糙 / 慢稳 / 啰嗦 | Pareto 主战场 |
| draft.email | `mail.draft` ×2 | 简洁 / 详尽 | 仅草稿，无发送（§10） |
| draft.speaker_notes | `notes.draft` | 是 | |
| formula.gen | `formula.generate` ×2 | 快糙 / 慢稳 | 自然语言 → FormulaSpec，下游确定性校验接住 |
| slide.copy_gen | `slide.copy` ×2 | 快糙 / 慢稳 | 大纲 → 每页文案 |
| text.polish | `text.polish` ×2 | 保守 / 激进 | 冗余 |
| text.expand | `text.expand` | 是 | 要点 → 成段 |
| title.gen | `title.compose` | 是 | 便宜高频，FAST Tier 流量源 |
| chart.type_pick | `chart.select` ×2 | 规则 / LLM | 冗余 |
| theme.palette_pick | `palette.select` | 否 | |
| data.insight_narrate | `insight.narrate` | 是 | 统计 → 叙述句 |

### L3 verify（9）

| 工具 | capability | LLM | 备注 |
| --- | --- | --- | --- |
| check.grammar | `text.grammar` ×2 | 规则 / LLM | 冗余 |
| check.style_rules | `style.check` | 否 | 对照 StyleSpec 的确定性检查 |
| check.fact_consistency | `fact.verify` | judge | Draft vs FactSheet |
| check.length | `doc.length` | 否 | 字数 / 章节约束 |
| check.formula | `formula.check` | 否 | 公式语法 / 引用范围 |
| check.slide_overflow | `ppt.overflow` | 否 | 文本框溢出 / 密度 |
| check.chart_data | `chart.check` | 否 | 图表数据 = 源表数据 |
| judge.quality | `quality.judge` | judge | 总体质量 → CompositeEvaluator |

### L4 render（4，全部产出 FileSpec）

| 工具 | capability | LLM | 备注 |
| --- | --- | --- | --- |
| render.doc | `doc.render` | 否 | Draft+Review → doc FileSpec |
| render.xlsx | `table.render` | 否 | DataTable+Formula → sheet FileSpec |
| render.pptx | `ppt.render` | 否 | SlideCopy → deck FileSpec |
| render.chart | `chart.render` | 否 | ChartSpec → chart FileSpec |

---

## 5. 冗余、保护与哨兵（证据结构设计）

**冗余提供者地图（11 个 capability 多提供者——剪枝燃料）**：

```text
×3   section.draft
×2   table.repair / text.summarize / text.translate / outline.compose /
     mail.draft / formula.generate / slide.copy / text.polish /
     chart.select / text.grammar
```

变体配置刻意制造四维分化：快糙（低延迟低 token 低 quality）、慢稳
（高延迟高 token 高 quality）、啰嗦（高成本中质量）——Pareto 前沿与
FAST/QUALITY Tier 因此有真实张力，剪枝有真实候选。

**保护展品（换非幂等展品后的首期方案）**：唯一提供者自动 PROTECTED
（`fact.extract` / `quality.judge` / `render.pptx` 等大量单提供者
capability 天然形成保护面）；哨兵场景 = 关键业务文档类场景
（`metadata.sentinel = true`），其涉及边直接免疫剪枝。

---

## 6. 场景集（约 60 条 / 4 家族 / 70-15-15）

| 家族 | 数量 | 典型 query | 评估构成（CompositeEvaluator） |
| --- | --- | --- | --- |
| doc_report | 20 | 依据语料写调研报告 / 周报 | fact 覆盖(结构化) + length + judge |
| slide_deck | 15 | 把报告转成汇报 PPT | overflow + 密度 + judge |
| sheet_analysis | 15 | 表格分析 + 公式生成 | formula.check + chart.check + judge |
| mail_comms | 10 | 起草回复 / 通知邮件 | 语气规则(结构化) + judge |

分布对齐靶场强化先例：约 42 covered / 9 uncertain / 9 uncovered。
**uncovered 场景刻意引用推迟清单中的 capability**（`calendar.schedule`
/ `doc.merge` / `pdf.render` 等）——fast regression 保持诚实，且这些
场景是后续里程碑天然的升级回归集。

Fixture 变体（语料状态播种，全确定性）：`clean` / `messy`（表头损坏、
脏数据）/ `sparse`（缺字段）/ `conflict`（来源互相矛盾）。

失败路径（必须真实出现并被归类）：LLM 输出畸形 → 宽松解析失败 →
`TOOL_EXECUTION_ERROR`；公式非法 → `formula.check` 失败 → 业务失败；
溢出 → verify 失败 → polish 循环；`messy` 语料 → read/repair 失败；
注入的偶发超时 → `TIMEOUT`。

---

## 7. 计量与评估

```text
LLM 工具      经 LLMResource：实测 token/成本自动入账（管道拦截）；
              声明值 cost_per_call 按变体配置（快糙 0.001 / 慢稳 0.01），
              实测 vs 声明差异走 drift_findings 输出
确定性工具    语料读取经句柄计数（访问次数入 access_counts）；
              模拟延迟写在工具/工厂配置内（asyncio.sleep），不进 core
评估          CompositeEvaluator 每家族一配：结构化断言（来自 L3 输出）
              + judge quality（连续分）；quality_score 必须出现 (0,1)
              开区间值（靶场强化 C 批验收同款）
```

---

## 8. 批次划分

```text
批次 A  域模型与语料    类型主干 + 最小语料 + L0 读取工具 + export_topology
批次 B  确定性工具层    L1 确定性部分 + L3 规则检查 + L4 render-to-spec
批次 C  LLM 工具层      L2 全部 + LLM-judge 类 L3；LLMResource 接线；
                        工厂变体参数化；fake 注入离线测试
批次 D  场景集与实跑    60 场景 + fixture 变体 + slow 落盘 + 覆盖报告 +
                        规模实跑（≈50 场景 × 5 trials）
批次 E  闭环证据        analyze 非空补丁 → validate → commit → rollback →
                        rank → select → 在线遥测回流；两轮收敛演示报告
批次 F  （可选，后置）  2 个复合节点（doc.composed_report /
                        ppt.composed_deck）作为方向 2 的真实展台
```

批次 E 是本里程碑的意义所在：A–D 只是管道。

---

## 9. 验收标准

**批次级**（各批完成时勾选）：

- [x] A：类型主干贯通（L0 读语料 → 类型化产出进黑板，下游可消费）；
      export_topology 产出可执行 JSON 且 `regression slow` 可跑
- [x] B：确定性工具有 gold 断言测试（同输入同输出）；规则检查能给出
      通过 / 失败两类结论
- [x] C：LLM 工具经 fake 注入全离线测试；变体四维分化（延迟 / token /
      成本 / quality 至少三维不同）；解析失败正确归类
- [x] D：覆盖分布达标（≈70/15/15）；slow 落盘含全部失败类别；
      `(scenario, trial)` 重跑一致
- [x] E：见下方总验收

**总验收（里程碑完成标准）**：

- [x] **首个真实剪枝事件**：optimize analyze 在真实证据上产出 ≥1 个
      IDENTIFIED 候选（非 PROTECTED），非空补丁经 validate ACCEPT、
      commit 落版本、rollback 可重放——三段式首次在非空补丁上走通
      （实测：6 个 IDENTIFIED 边候选、反事实 pass、三门 ACCEPT、
      office-v0.1 落版本并成功重放）
- [x] rank 产出 ≥2 个非空 Tier，且至少一个冗余 capability 的变体被
      分进不同 Tier（四维张力的直接证据）
      （实测：fast 3 / balanced 2 / quality 12；section.draft、
      text.polish、text.summarize、formula.generate、slide.copy、
      text.grammar 六个 capability 的变体跨 Tier）
- [x] 两轮收敛演示：Round 2（含在线遥测回流证据）平均成本或延迟下降、
      成功率不降，报告落盘可复跑
      （实测：在线服务 60/60，成功率 0.5565 → 1.0，平均延迟
      183 → 174ms，平均成本 0.02435 → 0.02428；`run_closed_loop.py`
      一键复跑，report.json 落盘）
- [x] 全部测试离线；全量测试 + compileall + diff-check 通过
- [x] AGENTS.md §7 与 README 更新

---

## 10. 本里程碑不做（推迟清单）

```text
非幂等工具（用户裁定推迟）   mail.send / fs.write / xlsx.write_formula；
                             render 产物停在内存 FileSpec
真实格式兼容                不解析也不输出 OOXML/PDF；语料为简化格式
远程 LLM API（用户裁定）     仅本地 Ollama；远端注入设计保留不实现
MCP 接入                    平行方向 mcp-import，不阻塞本里程碑
web / pdf / ocr / 图片检索   外部依赖或 fixture 复杂度，无拓扑增量
doc.merge / draft.full_doc  与既有 spine 重叠，无新结构
数据可视化进阶              contrast / citation / image.find 等检查后置
复合节点                    批次 F 可选后置
涌现拓扑 / 图层环            §2.1 废弃裁定不变
```

---

## 11. 提交划分

```text
docs    本里程碑文档
feat    批次 A：域类型 + 语料 + L0 + 导出器
feat/test   批次 B–D 逐批同模式推进（实现 + gold 测试 + 离线 LLM fake）
feat    批次 E：闭环演示脚本 + 证据资产
```

---

## 12. 实现进度

> 实施过程中的问题排查与解决记录（同层依赖约束、sys.modules 遮蔽、
> 四个核心缺陷、Tier 参照池口径、JSON 拓扑类型缺失的语义分歧等）
> 见 `office-battlefield-notes.md`。

| 批次 | 状态 | 说明 |
| --- | --- | --- |
| A 域模型与语料 | [x] | facts.py 15 类型主干 + corpus（受限 md / csv / json deck）+ 4 个 L0 读取工具 + export_topology；`regression slow` 可跑（tests/integration/test_office_batch_a.py） |
| B 确定性工具层 | [x] | tools_det.py 16 节点：L1 确定性 6 + L3 规则检查 6 + L4 render-to-FileSpec 4；gold 断言 + 通过/失败双路径测试（test_office_det_tools.py） |
| C LLM 工具层 | [x] | office_llm.py（可替换 LLMResource 句柄 + 宽松解析）+ factories.py（6 个参数化工厂）+ tools_llm.py 31 节点；fake 注入全离线、四维分化断言、解析失败归类 TOOL_EXECUTION_ERROR（test_office_llm_tools.py）。L2 消费只取 L0/L1 产物（同层互不依赖） |
| D 场景集与实跑 | [x] | scenarios.json 60 条 / 4 家族 / 42-9-9 分布 / 3 哨兵（test_office_scenarios.py）；fixtures.py 四变体（messy/sparse/conflict 确定性变换）；run_scale.py 规模实跑（60×5，全部失败类别落盘，(scenario, trial) 重跑语义一致）。覆盖报告由种子见证路线推导（与精确分析器语义一致、线性成本——精确 RouteSearch 在 51 节点稠密拓扑上指数爆炸，见 AGENTS.md §7 已知边界） |
| E 闭环证据 | [x] | run_closed_loop.py 一键复跑：analyze 首批真实剪枝候选（6 边）→ validate 三门 ACCEPT → commit office-v0.1 → rollback 重放 → rank 三 Tier + 6 个冗余 capability 变体跨 Tier → select 干跑 → 在线服务 60/60 + 遥测回流；两轮收敛（成功率 0.5565→1.0，延迟 183→174ms，成本微降）落盘 report.json |
| F 复合节点（可选） | [ ] | 后置 | |
