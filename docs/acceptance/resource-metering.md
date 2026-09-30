# Resource Metering —— 资源句柄计量里程碑

> 定位：AGENTS.md §7 可开发方向 1 的验收规格。目标：**工具的 token 消耗、
> 读写来源与次数等计量不再依赖工具主动上报**——框架发放资源句柄（管道
> 拦截），计量发生在句柄内部与调用边界，工具函数体零计量代码。
> 与拓扑模型完全解耦：不改 layer / provider / worker / 建边规则。

---

## 0. 动机：现状三层盘点（2026-09 代码事实）

```text
时间 / 次数 / 成败     已外部化：ToolExecutor 掐墙钟、按调用尝试计费（批次 A）
Router token           已管道化：LLMRouter 读响应 usage（框架作为调用方）
工具内部 LLM token      ToolExecution.token_usage 字段存在但从不填充（无来源）
读写来源与次数          完全没有捕获；ToolSpec 无任何资源声明字段
```

本里程碑补齐后两项：不是让工具"返回参数"，而是让工具"从框架管道访问资源"，
框架天然看得见。

---

## 1. 原则

1. **零侵入**：工具函数体没有任何计量代码；改变的只是"从哪拿资源入口"；
2. **边界归集**：计量与现有的墙钟 / cost / trace 在同一调用边界收账，
   时间对齐（executor 执行前设置收集器，执行后合并）；
3. **三档诚实**（延续批次 A 的 `None != 0` 纪律）：

```text
MeteringSource
    DECLARED    只走了静态声明（现状语义，不变）
    MEASURED    走了句柄管道，token / 访问为实测精确值
    ESTIMATED   未走管道，用估算器显式标注（不冒充实测）
```

4. **薄句柄面**：句柄方法一对一映射资源操作 + 计量，到此为止。不做连接池、
   重试、熔断（那是工程化扩展）；不做插件平台；
5. **零第三方依赖**：框架只提供计量核心 + 内存句柄（stdlib）+ LLM 句柄
   （复用 LLMRouter 的 urllib 基建）+ 通用包装器；sql / redis 等由应用侧
   注入客户端，经通用包装器获得计量；
6. **只记元数据不记内容**：资源名、访问模式、次数、耗时、token 数——
   不记 query 文本与返回数据（phase3 §103 脱敏原则）。

---

## 2. 机制：contextvars 归属

```text
ToolExecutor.execute(tool)
  ├─ contextvar: collector = 本次工具调用的计量收集器
  ├─ await tool.invoke()
  │     └─ await handle.get(...)              ← 工具只写业务代码
  │          └─ 句柄内部：真实资源调用 + collector.record(...)
  └─ collector 合并进 ToolExecution
```

- `contextvars` 按 async task 隔离：同层并发工具各记各账，不串账；
- 未走管道的调用不产生任何记录（约定而非强制，见 §9 边界）。

---

## 3. 批次 A：计量核心

### 交付项

```text
resources/metering.py
    MeteringRecord(resource, access, count, latency_ms)
    MeteringCollector（contextvar 挂载 / record / 合并导出）
    estimate_tokens(text) -> int          # 字符数估算（ESTIMATED 档用，显式调用）

ToolExecution 增量字段
    access_counts: Mapping[str, int]      # "mysql.orders[read]" -> 3
    token_usage: TokenUsage | None        # 已有字段，本批次起有填充路径
    measured_cost: float | None           # 句柄管道实测成本（LLM 句柄写入）
    metering_source: MeteringSource       # 默认 DECLARED

TrialResult / 聚合
    access_counts: Mapping[str, int]      # 本 trial 各工具访问合计
    （billed cost 口径不变：仍按声明 cost_per_call 计费，见 §8）
```

### 验收

- [ ] 无句柄的工具行为与现在完全一致（metering_source=DECLARED，access 空）
- [ ] 并发工具计量互不串账（contextvar 隔离测试）
- [ ] 既有 497 测试全部通过

---

## 4. 批次 B：内存句柄 + 靶场迁移

### 交付项

```text
resources/memory.py
    InMemoryStore(name, access="read")   # get / put / delete，stdlib 实现
resources/handle.py
    metered(name, access, invoke)        # 通用包装：任意 async callable + 资源名
examples/slow_refund/store.py 句柄化
    SandboxStore 的数据访问改为经 InMemoryStore；变体 / 失败注入 /
    幂等守卫等业务语义一行不动
```

### 验收

- [ ] demo 跑通后每个 ToolExecution 带 access_counts（如
      `"sandbox_orders[read]": 1`），metering_source=MEASURED
- [ ] 业务行为与迁移前完全一致（既有 sandbox / fixture / online 测试不改断言）

---

## 5. 批次 C：LLM 句柄（精确 token）

### 交付项

```text
resources/llm.py
    LLMResource(base_url, model, input_cost_per_1k, output_cost_per_1k)
    async complete(prompt) -> LLMResponse(text, usage: TokenUsage, cost: float)
    # Ollama 兼容端点，usage 解析复用 LLMRouter 模式（OpenAI 兼容 +
    # Ollama 原生字段）；构造器可注入假 _http，测试不触网
```

### 验收

- [ ] 经句柄调用后 `ToolExecution.token_usage` 为实测精确值、
      `measured_cost` 按定价折算、metering_source=MEASURED
- [ ] usage 缺失时优雅降级（token None，不失败）
- [ ] 假 HTTP 注入覆盖正反路径（镜像 test_llm_router 模式）

---

## 6. 批次 D：通用外部句柄（零依赖纪律的落点）

### 交付项

```python
# 应用侧示例：任何既有 async 客户端 + 一行包装即获得计量
from capability_runtime.resources import metered

orders = metered(
    "mysql.orders", access="read",
    invoke=my_existing_async_get,        # 应用注入真实客户端调用
)
# 工具内 await orders(key) —— 计数自动归属当前工具调用
```

- sql / redis / http 等品类不进核心包；文档提供配方，客户端由应用注入；
- `ESTIMATED` 档：黑盒 LLM 调用可经 `metered(..., estimate=estimate_tokens)`
  显式包装，token 为估算值并如实标注。

### 验收

- [ ] 通用包装器计数 / 归属正确；估算档标注正确
- [ ] 核心包零第三方依赖不变（pyproject dependencies 仍为空）

---

## 7. 批次 E：聚合、证据与漂移信号

### 交付项

```text
统计聚合
    RouteObservationStats.access_counts（per-route 资源访问合计）
    TrialResult.access_counts 透传
排名扩展（Phase 5）
    RouteProfile.access_counts            # 该路线碰过哪些源、读写分布
漂移信号（事实输出，不做任何自动修改）
    对比工具的 measured_cost 与声明 cost_per_call：
    |measured - declared| / declared 超阈值 → 输出 metadata_review_candidate
    （提醒"声明价已失真"，修正仍由人工改声明）
Demo / CLI
    run_scale.py 与 serve_demo.py 输出 access 统计；
    slow report 增加 per-route 数据访问摘要
```

### 验收

- [ ] 250-trial 量级实跑的 route_stats / rank 产物携带 access 维度
- [ ] 漂移信号在构造的失真样例上触发，且只输出事实
- [ ] 在线遥测 OnlineRecord 可携带 access_counts（回流闭环同口径）

---

## 8. 口径裁定（实现必须遵守）

1. **计费基准不变**：`ToolExecution.cost` 仍按声明 `cost_per_call` 计费
   （回归的确定性与可复现依赖声明值）；实测值入 `token_usage` /
   `measured_cost`，只作为**证据与漂移信号**驱动人工修正声明——
   不自动覆盖计费；
2. `metering_source` 是工具调用级的诚实标注，永不缺席（无管道 = DECLARED）；
3. 句柄计量随工具调用的**每次尝试**发生（与计费同口径：超时/异常的
   尝试同样已产生访问）；
4. 不记录访问内容；资源名由句柄构造处一次性声明，工具装饰器**不新增
   任何参数**。

---

## 9. 本里程碑不做

```text
连接池 / 重试 / 熔断 / 限流（工程化扩展）
拦截绕过句柄的直接 import（做不到，靠约定 + 估算兜底）
记录 query / 返回内容
按计量自动修改 cost_per_call 或 Topology
新的 Layer / 白名单 / 建边语义（拓扑模型零改动）
监控告警 / Prometheus / UI
```

---

## 10. 错误模型（追加到 core/errors.py）

```text
MeteringError                       # 根（继承 TopologyFrameworkError）
├── ResourceHandleError             # 句柄误用（未注册资源 / 重复挂载 collector）
└── MeteringContextError            # 管道在无 collector 上下文中记账（归属缺失）
```

---

## 11. 推荐目录结构

```text
src/capability_runtime/
├── resources/                  # 新增 —— 与 ranking/ online/ 平级
│   ├── __init__.py
│   ├── metering.py             # 收集器 / contextvar / 估算器 / MeteringSource
│   ├── handle.py               # metered() 通用包装 + ResourceHandle 协议
│   ├── memory.py               # InMemoryStore（靶场与测试用）
│   └── llm.py                  # LLMResource（Ollama 兼容，假 HTTP 可注入）
│
├── execution/executor.py       # 批次 A：调用边界挂载 / 收账（增量）
├── regression/slow/stats.py    # 批次 E：route 级 access 聚合（增量）
└── ranking/profile.py          # 批次 E：RouteProfile.access_counts（增量）
```

根包统一导出公共符号。

---

## 12. Definition of Done

## 计量核心

* [ ] contextvar 归属正确，并发隔离
* [ ] 三档 MeteringSource 全覆盖且默认 DECLARED
* [ ] 无管道行为与现状逐字节一致

## 句柄

* [ ] memory / llm / metered() 三类句柄可用
* [ ] LLM token / cost 精确入账，假 HTTP 测试离线
* [ ] 核心包零第三方依赖
* [ ] 工具装饰器零新增参数

## 聚合与证据

* [ ] trial / route / profile 三级 access 维度
* [ ] 漂移信号只输出事实
* [ ] 计费基准（声明值）不变

## 迁移

* [ ] 靶场 sandbox 迁移后既有测试断言零改动
* [ ] run_scale / serve_demo 输出 access 统计

## 边界

* [ ] 不记访问内容；不做中间件全家桶；拓扑模型零改动

---

## 13. 最终验收场景

对迁移后的退款靶场跑 250-trial 量级实跑，必须能回答：

```text
每个工具调用碰了哪些数据源、读几次写几次？

哪条路线是"只读路线"，哪条包含写操作？

工具内部 LLM 调用的精确 token 与实测成本是多少？
（与声明 cost_per_call 的漂移有多大？）

计量结果是否从 ToolExecution → TrialResult → route_stats →
RouteProfile → 在线遥测 全链贯通且口径一致？

未走句柄的工具是否如实标注 DECLARED 而非伪称实测？
```

---

## 14. 实现进度

| 批次 | 状态 | 落点 |
| --- | --- | --- |
| A 计量核心 | [ ] | |
| B 内存句柄 + 靶场迁移 | [ ] | |
| C LLM 句柄 | [ ] | |
| D 通用外部句柄 | [ ] | |
| E 聚合 / 证据 / 漂移 | [ ] | |
