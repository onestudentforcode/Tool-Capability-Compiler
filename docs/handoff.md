# Handoff — 2026-10-03

> 交接快照：仓库状态、本阶段成果、仍然有效的用户裁定、架构陷阱、
> 代码地图、运行手册、已知边界与候选后续。长期规范以 `AGENTS.md`
> 为准；本文是某一时点的状态固化，接手后随里程碑推进刷新。

---

## 1. 仓库当前状态

- 分支 `dev`，领先 `main` **208 个本地提交**（按纪律默认不 push）；
- 工作区干净；**661 个测试全绿**（`python -m pytest -q`），
  compileall / diff-check 通过；
- 本会话弧线的最近提交（全部已验证门）：

```text
e32cd2d fix: singularize scaffold tool count              (onboard)
3bf4979 feat: carry first tool error in discovery reason  (诊断)
350d810 docs: check output-polish P7/P8 boxes
ffab23d feat: render metering section in slow report      (P7)
862a411 feat: add CLI top-level exception backstop        (P8)
18c9972 docs: spec output-polish P7/P8
0e260c2 docs: add handoff snapshot (2026-10-03)
（更早：输出润色 P2-P6、office battlefield A-F、discovery-routing
批次 A-E 等，见 AGENTS.md §7）
```

## 2. 本阶段成果（四个弧线，全部收官）

| 弧线 | 内容 | 关键证据 |
| --- | --- | --- |
| **Office Battlefield A–E**（办公靶场） | 5 层 51 工具 / 11 冗余 capability / 60 场景 42-9-9 / 四语料变体 / 离线 fake LLM | 首个真实剪枝（6 边候选 → ACCEPT → commit → rollback）；rank 三 Tier + 6 冗余 capability 跨 Tier；两轮收敛 60/60、183→174ms |
| **Discovery & Routing A–E**（发现与路由） | 种子生命周期正位（fast 桥 + 模型发现双路径，复放不过不固化）；JSON/Python 双路径等价；声明期诊断 | qwen3:1.7B 真实发现跑：零池外违例但 6/6 链不完整，复放门全部拒绝——机制就绪、模型完整度是后续 |
| **批次 F**（复合节点展台） | doc_composed_report / ppt_composed_deck 入图（53 节点）；门控首现 + 诚实计费 | 21 次复合执行全部恰好 2 轮确定性收敛；flattener 展平内层伪 trial 同机处理 |
| **输出润色 P2–P6** | slow 失败样本/白话注解、validate 三门行、演示脚本 dict 治理、CLI 输入回显头、rank 层段折行 | 653 测试；控制台无 python 字面量断言；无 failures 时渲染逐字节兼容 |

## 3. 仍然有效的用户裁定（接手者不得违背）

1. **宪法**：严格拓扑（Layer/provider/worker 决定边）；consumes/produces
   只验证不建边；Declared/Active 分离；涌现拓扑/图层环废弃（§2.1）；
2. **工作流**：每个批次单独推进——先更新阶段文档展示计划，**用户验收后
   才开始实现**；完成跑全量门再按职责分批提交；默认本地提交不 push；
3. 办公靶场头号原则：**不在乎工具输出质量**，实现越简单越好；
4. LLM 只接**本地 Ollama**，远端注入设计保留不实现；测试零触网；
5. 种子生命周期：**复放验证是固化硬门槛**；覆盖判定权保留静态 fast；
6. 批次 F 裁定：**门控首现**（不改核心提取语义）、**诚实计费**（外层
   cost = 内层声明和）、**最小演示**（6 场景独立套件）；
7. 输出润色范围裁定：**P1 运行过程可观测性不做**；P7/P8 曾暂缓，
   **2026-10-03 经用户指令"修复暴露的功能毛病"解除暂缓并已落地**；
8. 2026-10-03 裁定：修复暴露的功能毛病；架构部分不轻易改，要改
   必须先详细评估+文档留档。RouteSearch 预算截断已完成评估
   （docs/routesearch-budget-evaluation.md）并于 2026-10-04 起草
   验收文档（docs/acceptance/routesearch-assignment.md），未实现，
   待验收。

## 4. 架构陷阱备忘（都付过学费，详文见 office-battlefield-notes.md）

- **同层工具互不依赖**（并发执行、只读进层前状态）——消费类型的
  生产者必须在更早层；builder 会发 `UNSATISFIABLE_INPUT`；
- **形参名 = 消费类型槽名**（名字优先解析）；违者 `SLOT_NAME_CONFLICT`；
- **域包只做包路径导入**（裸名 facts/store 会跨包遮蔽 sys.modules）；
- **计量句柄只在 ToolExecutor 上下文可用**（测试用 mount_collector
  helper 包裸调用）；
- **JSON 拓扑带类型引用才有完整等价性**（`consumes/produces:
  "module:attr"`；导出器自动派生；不带 = 旧行为）；
- **复合节点**：固定路线 × 跨轮门缓存是收敛习语；输出提取取**首个**
  匹配（终态类型只能由收尾门首现）；邻接边约束对复合同样生效
  （L2 复合只吃 L0 产物在跳过 L1 的路线上不可达）；
- RouteSearch 在宽拓扑受枚举预算截断（可行性由见证回退保证）；
- Tier 参照池只含**成功交付过**的路线（零成功路线进池会让
  FAST/BALANCED 结构性不可达）。

## 5. 代码地图

```text
src/capability_runtime/      核心包（结构见 AGENTS.md §7）
  regression/seed_export.py      批次A：fast 链 → 复放验证 → seeds v2（指纹绑定）
  regression/seed_discovery.py   批次B：路由器驱动的发现 → 复放 → 固化
  topology/loader.py             JSON 拓扑（类型引用 + composite 条目）
  topology/builder.py            声明期诊断（两类警告）
  composite/                     宏节点（spec/runtime/flattener）
examples/office/            办公域包（域包规范见 docs/domain-package-conventions.md）
  tools_l0/det/llm               51 工具（工厂变体在 factories.py）
  composite_inner/nodes          批次F 两个宏节点（53 = 51+2）
  run_scale / run_closed_loop / run_composite_demo / run_routing_comparison
  render_review.py               产物 → review.md（table/fmt 公共原语）
docs/
  acceptance/office-battlefield.md (+notes.md 实施问题记录)
  acceptance/discovery-routing.md (+plan.md 逐批计划)
  acceptance/output-polish.md
  domain-package-conventions.md / ranking-tiers.md
examples/office/artifacts/  本地产物（gitignore；review.md 是审查入口）
```

## 6. 运行手册（常用命令）

```bash
# 导出可执行拓扑（Python 声明为唯一事实源；含复合节点与 inner 文件）
python examples/office/export_topology.py

# 种子：静态桥 / 模型发现（后者需本地 Ollama；离线用 --scripted-router）
PYTHONPATH=src python -m capability_runtime.cli seeds export \
  --topology examples/topology/office.json --scenario examples/office/scenarios.json \
  --fast-report <fast目录> --out seeds.json
PYTHONPATH=src python -m capability_runtime.cli regression fast --topology ... --out-dir <fast目录>

# 演示四入口（全部离线确定性；产物自带 console.txt + review.md）
python examples/office/run_scale.py --trials 5
python examples/office/run_closed_loop.py --trials 15
python examples/office/run_composite_demo.py --trials 3
python examples/office/run_routing_comparison.py --limit 12 --trials 2

# 审查既有产物
python examples/office/render_review.py <产物目录>

# optimize 三段式 / rank / select 见 README 与 docs/acceptance/optimize-pipeline.md
```

## 7. 已知边界与暂缓项

- P1（运行过程进度输出）——用户裁定不做；P7/P8 已于 2026-10-03
  落地（slow Metering 段 + CLI 异常兜底）；
- 精确 RouteSearch 宽拓扑枚举预算截断（语义由见证回退保证）——
  评估留档 docs/routesearch-budget-evaluation.md（实测：246 次截断
  事件/60 场景、5000 万预算 28 分钟未跑完）；改进验收文档已起草：
  docs/acceptance/routesearch-assignment.md（能力指派枚举 + 桥接
  定向补全 + 死路记忆，输出契约不变），**待用户验收后实现**；
- 工具 latency 声明字段后置（P9 以 domain-package-conventions.md §7
  规范落地）；
- 真实模型选路完整度不足（1.7B 系统性缺收尾检查）；
- 复合嵌套深度上限 MAX_COMPOSITE_DEPTH=2；
- render_review.py 只认 closed_loop/scale 产物形状；composite_demo
  与 model_discovery 目录自带各自渲染，不支持经 render_review 重建
  （一致性重构候选，非缺陷）。

## 8. 候选下一步（启动前须用户裁定并起草验收文档）

1. **RouteSearch 能力指派枚举**（验收文档已起草待验收）：
   docs/acceptance/routesearch-assignment.md——批次 A 安全网 /
   B 内芯替换 / C office 实证；背景与实测见
   docs/routesearch-budget-evaluation.md；
2. **路由质量改进**（真实发现跑的自然续集，三选一或组合）：路由
   prompt 注入场景期望能力（RoutingContext 目前只有 query）/ 更强
   本地模型 / 静态桥+模型发现混合校验；
3. Office 靶场深用：复合节点参与剪枝/排名对照、60 主套件接入
   composed capability（会改变 42-9-9 验收基线，需慎重）；
4. phase6.md §23 工程化扩展（多租户/熔断/在线自适应）。

## 9. 接手即检

```bash
git status --short          # 应为空
python -m pytest -q         # 661 passed
python -m compileall -q src tests main.py examples
python examples/office/run_composite_demo.py --trials 1   # ~1s 冒烟
```
