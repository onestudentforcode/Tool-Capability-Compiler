# Battlefield Hardening — 靶场强化里程碑

> 定位：位于 Phase 4 与 Phase 5 之间的加固里程碑。不引入新的架构方向，
> 只兑现 phase0/2/3 文档中已承诺、但实现中被"骨架先行"跳过的靶场
> （Battlefield）实质：真实计量、可执行工具、状态化 Fixture、
> 有规模的业务场景资产。完成后 Phase 5 的排名才有真实证据可消费。

---

## 0. 动机：三层缺口（2026-09 代码事实基线)

### 计量层（Phase 4/5 的共同数据契约，当前为空壳）

```text
TrialResult.cost        硬编码 None，无任何填充路径
ToolExecution.cost      字段存在但从不填充
ToolExecution.token_usage  字段存在但从不填充
LLMRouter               不解析也不记录 token / routing cost
EvaluationResult        无成本字段，judge 成本无处安放
quality_score           StructuredEvaluator / demo 评估器均为二值 1.0/0.0
```

phase3.md §78-80 规定了 Token / Cost / 双口径成本的采集义务，
实现只落了字段，没落计量。

### 工具与执行层（"现有工具只是 demo"）

```text
examples/slow_refund    工具返回硬编码常量，与 scenario 无关；
                        无失败模式；无延迟/成本差异
TopologyLoader          JSON 工具绑定 _null_handler；
                        CLI regression slow --topology json 实际执行空操作
```

### 数据与编排层（靶场空心）

```text
场景资产    fast 示例 2 条、slow 示例 5 条，均无 category；
            phase2 §65 承诺的 50-100 场景 / 5 业务域 / 70-15-15 从未交付
Fixture     DefaultFixtureManager 只做 template 深拷贝；
            phase3 §11 承诺的测试订单 / mock 服务 / 数据库状态未落地
规模实跑    phase2 §81 / phase3 §129 / phase4 §133 的验收规模从未实际运行
```

### 后果推演

在此基线上直接实现 Phase 5：success 趋同（工具恒成功）→ 全部
STATISTICAL_TIE；quality 是 success 的复制；latency 是微秒级事件循环
噪声；cost 恒 None。Pareto 全员互不支配，Tier 无信息量——管道技术上
正确、结论为零。因此本里程碑是 Phase 5 的硬前置。

---

## 1. 原则

- **离线优先不变**：不绑定具体 LLM Provider、数据库、Web 框架或外部
  服务（AGENTS §4）；零第三方运行时依赖；
- **sandbox 的"真实"** = 数据依赖 + 失败模式 + 计量差异，不是接生产
  系统。受控变量（模拟延迟、失败注入）全部允许，但必须可复现；
- **可复现优先**：任何随机性由 `(scenario_id, trial_index)` 种子确定，
  同输入两次运行产出完全一致的 Trace；
- **分批交付**：五个批次各自独立验收、独立提交，顺序即依赖顺序；
- **不改变核心架构**：不动 Edge 规则、探索模式、Phase 边界与已验收
  行为；既有测试只增不删。

---

## 2. 批次 A：计量贯通（cost / token / 双口径）

### 交付项

```text
ToolSpec.cost_per_call          @tool(cost_per_call=...) 可选声明，>= 0
                                TopologyLoader JSON 同名字段，严格校验
ToolExecution.cost              按调用尝试计费：成功 / 超时 / 异常均计；
                                参数解析失败（未真正调用）不计
RoutingDecision.token_usage     Router 每次决策附带 token 用量（可空）
RoutingDecision.routing_cost    Router 每次决策附带成本（可空）
RouterConfig                    可选定价：input_cost_per_1k /
                                output_cost_per_1k（可空 = 未定价）
LLMRouter                       解析响应 usage（OpenAI 兼容字段），
                                按定价折算 routing_cost；解析失败不致命
EvaluationResult.cost           judge 审计成本（可空）
TrialResult.tool_cost           Σ ToolExecution.cost（全缺省则 None）
TrialResult.routing_cost        Σ RoutingDecision.routing_cost
TrialResult.evaluation_cost     来自 EvaluationResult.cost
TrialResult.cost                tool_cost + routing_cost（execution 口径）
```

### 口径裁定（延续 phase3 §80）

```text
TrialResult.cost            = execution cost = tool + routing   → 排名消费
TrialResult.evaluation_cost = judge 成本                        → 仅审计
token_usage                 = tool token + router token 合计
```

### 验收

- [ ] 声明 cost_per_call 的工具经执行后 `ToolExecution.cost` 非空
- [ ] 未声明成本的拓扑运行后 `TrialResult.cost` 仍为 None（不臆造 0）
- [ ] Router 决策 token 进入 `TrialResult.token_usage`
- [ ] `RouteObservationStats.costs` 在带成本 demo 上非空（端到端）
- [ ] traces.jsonl / route_stats.json 携带全部新字段
- [ ] 既有 377 个测试全部通过（行为无回退）

---

## 3. 批次 B：可执行绑定（JSON 拓扑 ↔ Python 实现）

### 交付项

```text
TopologyLoader    工具可选 "implementation": "module.path:attr"
                  入口点解析并绑定真实 async handler；
                  缺省仍为 _null_handler（Fast Regression 不受影响）
CLI slow          拓扑含未绑定工具时拒绝执行（fail fast），
                  避免静默产出 null 输出污染统计
```

### 边界

- 入口点解析是**离线靶场装配机制**，不是线上插件加载器；只允许
  已安装模块内的显式 `module:attr`，不做任意路径执行；
- 解析失败抛 `TopologyBuildError`，带工具名与入口点字符串。

### 验收

- [ ] JSON 声明 implementation 的拓扑可被 `regression slow` 真执行
- [ ] 未绑定工具触发明确错误而非空跑
- [ ] Fast Regression 路径行为不变

---

## 4. 批次 C：Sandbox 工具真实化

把 `examples/slow_refund` 从常量返回升级为受控实验台：

### 交付项

```text
内存订单库        fixture 驱动的多订单变体：
                  eligible / ineligible / high-risk / not-found / erp-mismatch
数据依赖分支      工具行为依赖状态中的实际订单字段（非 query 文本）
确定性失败注入    seed = hash(scenario_id, trial_index)；
                  not-found 订单触发 read 层失败、erp 偶发不可用
差异化计量        各工具声明不同 cost_per_call；
                  模拟延迟写在工具实现内（asyncio.sleep），不进 core
评估器升级        RefundEvaluator → CompositeEvaluator：
                  结构化断言（退款是否正确）+ 答案完整性（摘要字段覆盖），
                  quality_score 连续化，不再二值
```

### 验收

- [ ] 不同场景产生真实不同的成功率与质量分布
- [ ] 同 (scenario, trial_index) 两次运行 Trace 一致
- [ ] 至少存在失败路径 trial（read 失败 / 全层失败）并被正确归类
- [ ] quality_score 出现 (0,1) 开区间内的值

---

## 5. 批次 D：Fixture 状态化

### 交付项

```text
具名 sandbox fixture    每场景声明 fixture 变体（订单种子）；
                        setup 注入内存库初始状态，teardown 重置；
                        连续 trial 状态不泄漏
FixtureRegistry 接线    demo 场景通过 fixture 名拿到差异化种子
```

### 验收

- [ ] 同一场景连续 N 个 trial 结果一致（隔离生效，无状态累积）
- [ ] 不同 fixture 变体的场景产生不同业务结论

---

## 6. 批次 E：场景资产 + 规模实跑

### 交付项

```text
场景资产    50-100 条场景，5 个 category（order / refund / email /
            user / invoice），覆盖分布约 70% covered / 15% uncertain /
            15% uncovered；含 sentinel 与 priority 标注（兑现 phase2 §65、
            phase4 §35-36）
规模实跑    200 场景 × 5-10 trials 的 Slow Regression 实际运行并落盘
            artifacts（manifest / traces / *_stats）
回归联动    用真实 evidence 跑一次完整 optimize 流程（回填 phase4 §133
            的缩小版验收）
性能记录    记录实跑耗时与产物体积，暴露聚合/持久化的规模问题
```

### 验收

- [ ] 一次真实规模 artifacts 落盘，Phase 5 有真实数据可消费
- [ ] optimize 全流程在真实 evidence 上产出候选与验证结论
- [ ] 实跑脚本与数据入库（examples / datasets），可重复执行

---

## 7. 本里程碑不做

```text
生产系统 / 真实外部 API 接入
在线 Route Selection / Load Balancing（Phase 6）
Route 排名与 Tier（Phase 5）
修改 Edge 规则 / 探索模式 / 阶段边界
引入第三方运行时依赖
自动修改 Topology
```

---

## 8. 提交划分

```text
docs    里程碑文档 + phase5.md 前置修订 + AGENTS/README 边界更新
feat    批次 A：core 计量模型（ToolSpec / RoutingDecision /
        EvaluationResult / TrialResult 字段与校验）
feat    批次 A：executor / runner / llm_router / loader 记账接线
test    批次 A：正向与失败路径单测 + 端到端贯通断言
feat/test   批次 B-E 逐批同模式推进
```

---

## 9. 完成标准

五个批次全部验收通过后：

```text
Phase 3 的 Trace 里，每条 Route 都有非空的四维证据来源：
    success（真实变异）  quality（连续分布）
    latency（有差异）     cost（双口径可拆）

Phase 4 的 Evidence / Candidate / Gate 在真实规模 evidence 上跑通过。

Phase 5 可以直接消费落盘 artifacts 开始实现。
```

届时更新 `AGENTS.md §7` 与 README，正式进入 Phase 5。

---

## 10. 实现进度

| 批次 | 状态 | 说明 |
| --- | --- | --- |
| A 计量贯通 | [x] | `ToolSpec.cost_per_call`（@tool + TopologyLoader）；`ToolExecution.cost` 按调用尝试计费；`RoutingDecision.token_usage / routing_cost` + `RouterConfig` 定价；LLMRouter 解析 OpenAI 兼容 / Ollama 原生 usage；`EvaluationResult.cost`；`TrialResult.tool_cost / routing_cost / evaluation_cost / cost`（execution 口径，全缺省保持 None）；token 聚合含 router。测试 `tests/unit/test_metering.py`（23 项，端到端贯通 `RouteObservationStats.costs`） |
| B 可执行绑定 | [ ] | |
| C Sandbox 工具 | [ ] | |
| D Fixture 状态化 | [ ] | |
| E 场景资产 + 实跑 | [ ] | |
