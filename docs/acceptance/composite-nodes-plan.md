# 复合节点里程碑 实施约定 —— 命名与目录结构

> 定位：动手写代码前敲定 composite-nodes.md 的对象命名、包路径、关键
> 机制落点与 8 个 Step 的实现顺序。实现以本文件 + composite-nodes.md
> 语义为准；冲突时命名以本文件为准。

---

## 0. 三条总决定（TL;DR）

1. **新增顶包 `composite/`**（与 ranking/ online/ resources/ 平级），
   core / topology / online 零结构性改动。
2. **复合节点 = 工具工厂**：`build_composite_node(spec) -> ToolNode`，
   handler 是生成的内层执行函数；注册/建边/覆盖/执行/在线零特殊分支。
3. **通用明细通道**：handler 向调用边界附带结构化明细的机制是通用的
  （`attach_detail` / `take_detail` contextvar，挂在 resources/metering.py
   的调用边界模块），executor 只是转发——不是复合专用分支。

---

## 1. 目录结构

```text
src/capability_runtime/
├── composite/
│   ├── __init__.py
│   ├── spec.py        # CompositeSpec / build_composite_node / 深度与自引用检查
│   ├── runtime.py     # CompositeRuntime（有界循环 / 黑板 / 计量汇总 / 明细）
│   └── evidence.py    # flatten_composite_results
├── resources/metering.py   # 增量：attach_detail / take_detail（通用通道）
├── execution/executor.py   # 增量：composite_detail 字段 + take_detail 转发
└── topology/loader.py      # 增量：kind=composite（Step 5）
```

---

## 2. 关键口径裁定

- **深度上限保留 ≤ 2**（用户裁定 2026-09：暂且保留）；实现为
  `MAX_COMPOSITE_DEPTH = 2`，模块级 `composite_depth_by_name` 登记簿
  追踪每个已构建复合的深度；构建新复合时扫描内层拓扑节点名取
  `max(depth)+1`，超限 / 内层含同名节点 → `CompositeSpecError`；
- **循环语义**：同一 `route` 逐层执行 + 黑板（ExecutionState）跨轮
  持久 + `stop_when` 槽位齐备（ALL）即停 + `max_iterations` 硬预算；
  for-else 语义——预算耗尽仍未停 → `CompositeExecutionError`；
- **失败语义**：内层整层失败 → 记录该层后立即上抛（外层 executor 包为
  ToolExecutionError）；明细与计量汇总在 finally 中执行——失败复合
  照常携带明细与内层账单；
- **计量汇总**：内层每 ToolExecution 的 `access_counts / token_usage /
  measured_cost` 回放外层 collector（`record_tokens` 走 MEASURED 档；
  内层声明价不回放——外层只计自己的 `cost_per_call` 一次）；直接调用
  （无外层 collector）时跳过汇总不报错；
- **出参抽取**：`produces` 按 isinstance 从黑板抽取；单类型返回值本身，
  多类型返回 tuple（与 executor 既有传播语义一致）；空 produces 返回
  None（JSON 复合默认末端节点，跨层类型契约仅 Python 路径）；
- **伪 Trial 版本**：flattener 产出的内层伪 Trial `topology_version =
  f"inner:{spec.name}"`，仅供证据聚合，不进入外层排名；
- **detail 通道**：`attach_detail(tuple[tuple[LayerExecution, ...], ...])`
  （每轮一个元组）；失败轮也包含已执行的层记录。

---

## 3. 错误模型（追加 core/errors.py）

```text
CompositeError             # 根
├── CompositeSpecError     # 声明非法 / 深度超限 / 自引用
└── CompositeExecutionError  # stop condition unmet 等运行期失败
```

---

## 4. Step 顺序与验收点

| Step | 模块 | 验收点 |
| --- | --- | --- |
| 1 | errors + spec.py | 全校验分支；工厂产物注册建边；深度/自引用拦截 |
| 2 | runtime.py | 有界循环/黑板持久/预算耗尽失败/入参注入出参抽取 |
| 3 | executor + metering 通道 | composite_detail 持久化往返；计量汇总三档正确 |
| 4 | evidence.py | 伪 Trial 指纹稳定；内层 stats + EvidenceAggregator 跑通 |
| 5 | loader | kind=composite 严格解析；内层必须可执行绑定 |
| 6 | 在线兼容 | canonical 重建 / route-following / 降级链对复合照常 |
| 7 | Demo | 二轮 refine（兄弟哨兵 + 黑板跨轮）+ flattener 闭环 |
| 8 | 集成验收 | 全链 + 515+ tests 全过 |

---

## 5. 阶段边界检查表

- [ ] 图层面无环（深度/自引用构建期拦截）
- [ ] 外层机制零特殊分支（注册/建边/覆盖/执行/排名/在线）
- [ ] 内层边永不进入外层统计
- [ ] 一切循环有界；无内嵌重试
- [ ] 内层剪枝仅经 flattener 离线进行

---

## 6. 实现进度

| Step | 状态 | 落点 |
| --- | --- | --- |
| 1 | [x] | `composite/spec.py`：CompositeSpec 全校验（route 走 RoutePlan / 预算 / 槽位 / 深度≤2 / 自引用）；`build_composite_node` 工厂 + `_typed_handler`（槽位命名参数，Python 3.14 PEP 649 免疫） |
| 2 | [x] | `composite/runtime.py`：有界循环 + 黑板跨轮持久 + stop_when(ALL) + 预算耗尽 CompositeExecutionError；整层失败立即上抛；入参种子注入 / produces 抽取 |
| 3 | [x] | `resources/metering.py` 增通用 `attach_detail/take_detail` 通道；executor 转发 `composite_detail`；内层 access/token/measured_cost finally 回放（失败复合照常携带明细与账单） |
| 4 | [x] | `composite/evidence.py` flatten_composite_results：每轮一个伪 Trial（`inner:{name}` 版本线）；stats + EvidenceAggregator 原样跑通 |
| 5 | [x] | loader `kind=composite`：严格字段集（providers/workers/consumes/produces 拒绝）、内层相对路径解析、内层必须全绑定 |
| 6 | [x] | 在线兼容：catalog canonical 重建（act:[refund_handler]）+ route-following 执行 + 内层计量回放进在线 trace（测试覆盖） |
| 7 | [x] | `examples/composite_refund`：双重确认门（计数器在计量句柄上，iter1 issue_refund 失败/holdover 保层，iter2 放行）+ flattener 闭环输出 |
| 8 | [x] | 集成验收：全链 Demo（3 trials × 2 迭代，外层账单含 composite_confirm[read]:4/[write]:2 内层回放）+ 全量 527 tests / compileall / diff-check |

实现备注：同层工具经共享 state 顺序执行，当层内即可见兄弟输出——跨轮门信号必须放在
黑板/句柄上而非"同层产出"（demo 的计数器门即为此设计）。
