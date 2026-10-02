# Domain Package Conventions — 域包规范

> 适用对象：一切新增域包（`examples/<domain>/` 及未来的域）。
> 来源：办公靶场实施中踩过的坑与修正（详见
> `office-battlefield-notes.md`），本文是它们的成文版——新域开工前
> 逐条过一遍，验收时按此审查。

## 1. 导入纪律：包路径 only（裸名禁令）

- 域包内模块一律**相对导入**（`from .facts import Order`）；测试与
  脚本一律 `from examples.<domain> import ...`。
- **禁止裸名导入**（`import facts` / `from facts import ...`）：
  裸名会把模块缓存进 `sys.modules` 的顶层名，多个域包的同名模块
  （facts / store / fixtures 是高危名）在共享进程（如 pytest）里
  互相遮蔽，产生与被改文件无关的诡异 ImportError
  （案例：office-facts 与 refund-facts 冲突，notes §2.1）。
- 导出器与入口脚本只引导 `src/` + 仓库根两个路径，用包路径导入；
  不要把域包目录自身插进 `sys.path`。

## 2. 工具声明：注解与类型签名

- 工具模块**禁止** `from __future__ import annotations`：
  ToolExecutor 与声明期诊断都靠真实（非字符串）注解工作
  （案例：槽名冲突诊断在字符串注解下整体失效）。
- **形参名 = 消费类型的槽名**（`snake(类型名)`，如
  `ReviewReport → review_report`）。实参解析形参名优先、类型兜底；
  形参名撞上**其他**类型的槽名会静默绑错产物——builder 会在声明期
  发 `SLOT_NAME_CONFLICT` 警告，见到即修。
- `consumes` / `produces` 显式声明主干类型；**同层工具互不依赖**
  （同层并发执行、只读进层前状态）——消费类型的生产者必须在
  **更早的层**，builder 会在声明期发 `UNSATISFIABLE_INPUT` 警告。
- 类型主干保持最小集；层间只流动主干类型，格式转换内聚在
  读入工具与渲染工具的世界边界上（见 office-battlefield-notes.md
  万用工具一节）。

## 3. 计量上下文

- `InMemoryStore` 等计量句柄只能在 ToolExecutor 执行的工具内使用
  （收集器由执行器挂载；裸调 handler 会 `MeteringContextError`）。
- 测试直接调 handler 一律走 `call_tool` helper（mount_collector /
  unmount_collector 包裹，参照 `tests/integration/test_office_batch_a.py`），
  顺带可断言 access_counts / measured token。

## 4. 失败语义

- 评估器是业务守门人：trace 里的工具错误**不会**自动成为 trial
  失败——家族评估器必须扫描 trace 并把 error_category 回填到
  EvaluationResult（参照 office 的 `OfficeFamilyEvaluator`）。
- 域内失败路径要真实出现并被归类（tool_execution_error / timeout /
  layer_error / answer_error），slow 工件按类别落账。

## 5. 语料与 Fixture

- 语料文件是简化格式（受控 md / csv / json），世界状态存
  `store.py` 的计量句柄；解析发生在读取工具内，store 保持 raw。
- fixture 变体（clean / messy / sparse / conflict）是**确定性纯
  变换**；fixture manager 在 setup 时按 `(scenario_id, trial_index)`
  重置种子 rng，保证逐试验可复现。

## 6. 可复现与留档

- 一切随机性从 `store.rng` 抽取（按试验播种），禁止直接
  `import random`；
- 复现性验收用**语义哈希**（剥离时间戳与 latency 后对比），不比字节；
- 每次运行留档 `console.txt` + `review.md`（render_review 自动生成）。

## 7. 超时联动（P9 口径，规范版）

- per-tool 超时线必须**高于最慢的正常工具**、**低于注入的故障
  停顿**——两侧任何一侧调整都要重查另一侧（案例：变体延迟放大后
  0.08s 超时线误伤正常流量，notes §4.4）。
- 代码化需要工具声明 latency 字段（schema 扩展）——已记录为后置
  项（discovery-routing.md P9）；当前以本条规范执行。

## 8. LLM 设施（如域内含 LLM 工具）

- 所有模型调用经域内统一门面（参照 office 的 `office_llm.py`），
  测试通过 `set_resource` / `_http` 注入 fake，**零触网**；
- fake 的内容差异就是计量分化的来源：按变体风格产出不同长度/形状，
  按输入长度计分（judge），保证四维分化可被 rank 看见；
- 畸形输出走宽松解析后抛 `ValueError`（→ TOOL_EXECUTION_ERROR），
  偶发停顿用可取消的 `await asyncio.sleep`（同步 sleep 会卡死
  事件循环并破坏超时语义）。
