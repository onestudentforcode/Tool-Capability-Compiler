# Onboarding Assist —— 接入辅助里程碑

> 定位：AGENTS.md §7 可开发方向 3 的验收规格（里程碑式命名，同
> resource-metering / composite-nodes 先例）。目标：**把接入本框架的
> 边际成本压到 OpenAI tool spec 基线**——只消除与拓扑模型解耦的
> clerical 负担，并把 semantic 负担从"逐工具编写"压缩为"一次性批量
> 审阅"。方向一（涌现拓扑）已废弃（AGENTS §2.1），本里程碑不触碰
> 拓扑模型。

---

## 0. 动机与量化口径

2026-09 方向一评估确立的三层成本框架（见 battlefield-hardening §0 的
同类盘点方法）：

```text
基线成本    callable + description + 参数说明 —— 任何工具体系都要付
clerical    consumes/produces 重复声明、逐工具 async 适配、注册样板
semantic    capabilities 命名、layer 归类
```

本里程碑交付后的目标（50 工具口径）：

| 项目 | 现状 | 交付后 |
| --- | --- | --- |
| async 适配 | 3 行/工具 | 0（批量适配器均摊） |
| consumes/produces | 2–4 行/工具 | 0（类型推断） |
| capabilities | 逐工具编写 | **审阅 diff**（提案→确认） |
| layer 归类 | 人工 | **仍人工**（方向一废弃的残留，见 §7） |
| 骨架拓扑 JSON | 手写 | CLI 生成 |

编写负担目标降幅 **≥ 80%**（以既有 OpenAI specs 为输入的接入演练
度量）；semantic 负担从 authoring 变 review。

---

## 1. 原则

1. **类型不建边（宪法级重申）**：推断只填充 `ToolSpec.consumes /
   produces`（执行与验证契约），ToolEdge 仍只由 layer + 白名单产生
   （AGENTS §2）；
2. **提案不落盘，审阅产物才落盘**：LLM 提案永远不直接写入任何资产；
   落盘的是人工确认后的 approved 集合，且 apply 拒绝未确认的工具；
3. **显式优先**：任何显式声明覆盖推断；推断失败给指路错误信息，
   不静默降级；
4. **零第三方依赖**：LLM 走 Ollama 兼容端点（复用 resolver 的
   urllib 基建），测试注入假 `_http`；
5. **向后兼容**：既有 `@tool` 声明、既有 JSON 资产、既有 527 测试
   断言零改动。

---

## 2. 批次 A：类型推断（零 LLM，纯 clerical 消除）

### 交付项

`decorators.py` 增量（无破坏性）：

```python
@tool(layer="read", capabilities={"order.read"})     # 不写 consumes/produces
async def order_db() -> Order:                       # 返回注解 → produces=(Order,)
    ...

@tool(layer="analyze", capabilities={"refund.policy.check"})
async def policy_check(order: Order) -> PolicyDecision:
    ...   # 参数注解 → consumes=(Order,)；返回注解 → produces=(PolicyDecision,)
```

规则：

- `consumes` 缺省 → 逐位置参数取类型注解；`produces` 缺省 → 取返回
  注解（须为类型；`None`/缺失 → `()`）；
- 显式声明与推断可混用（显式 consumes + 推断 produces 合法）；
- 注解缺失 / 字符串化（future-annotations 模块）→ `RegistrationError`，
  错误信息指明"声明 consumes/produces 或修正注解"；
- 推断结果与现有校验完全同轨（数量一致、去重、类型性检查）。

### 验收

- [ ] 推断声明与手写声明构建的 ToolSpec 逐字段等价
- [ ] 混用/显式覆盖/失败路径错误信息三类测试
- [ ] 既有装饰器测试零改动通过

---

## 3. 批次 B：OpenAI 风格批量适配器

### 交付项

`onboarding/openai_adapter.py`：

```python
from_openai_specs(
    specs: Sequence[Mapping],          # OpenAI function specs（name/description/parameters）
    dispatch: Callable[[str, dict], Awaitable[Any] | Any],
    *, layer: str,
    cost_per_call: float | None = None,
) -> tuple[ToolNode, ...]
```

- 每个适配工具：description = 原 description + parameters schema 要点
  （并入文本，供 LLM Router 与提案消费）；handler 包装 dispatch（同步
  dispatch 自动包 `to_thread`）；
- **边界（诚实声明）**：批量适配产出的入口工具无类型契约
  （consumes/produces 空），即**末端/入口节点语义**——跨层类型链仍走
  Python 路径（批次 A 的推断 + dataclass 契约，配方写入模块 docstring）；
- 适配器不猜 capabilities / layer / cost（这三样留给批次 C 与人工）。

### 验收

- [ ] 50 个 specs 一次注册为可用 ToolNode，零逐工具代码
- [ ] dispatch 同步/异步两种形态可执行
- [ ] description 携带 schema 要点（Router 可见性测试）

---

## 4. 批次 C：capabilities 提案 → 审阅 → 应用

### 交付项

```python
# onboarding/proposals.py
CapabilityProposal(tool, proposed: tuple[str, ...], rationale: str | None,
                   confidence: float)
async propose_capabilities(
    tool_summaries: Sequence[ToolSummary],
    *, vocabulary: AbstractSet[str] = frozenset(),   # 既有能力词表（可选，促进一致性）
    base_url=None, model=None, _http=None,           # 假 HTTP 可注入
) -> tuple[CapabilityProposal, ...]

render_capability_diff(proposals, approved_state) -> str   # 人审的文本 diff

# onboarding/apply.py
apply_capabilities(
    topology_payload: Mapping, approved: Mapping[str, Sequence[str]],
    *, out_path: Path,
) -> Path        # 拒绝 approved 未覆盖的提案工具（审阅门禁）
```

约束：

- 提案批量一次 LLM 调用（全部 name+description+schema 进一个
  结构化输出请求），输出经 `validate_capability_name` 逐条校验，
  非法项丢弃并记录；
- vocabulary 提供时优先复用既有能力名（一致性），不阻止新词；
- **审阅门禁是硬边界**：`apply_capabilities` 只接受显式 approved
  映射；提案对象本身没有写入路径（类型层面就不传给 apply）；
- 应用产物经 `TopologyLoader` 往返校验后落盘。

### 验收

- [ ] 提案：结构化输出解析 / 非法能力名丢弃 / 词表优先，全部离线测试
- [ ] apply：未审阅工具被拒绝（门禁测试）；落盘 JSON 经 Loader 复验
- [ ] diff 渲染包含 proposed / vocabulary-reused / invalid-dropped 三类标注

---

## 5. 批次 D：onboard 三段式 CLI

```bash
# ① 骨架：OpenAI specs → 拓扑 JSON（layer 人工指定；capabilities 留空；
#    implementation 由 --dispatch-module 标注待补）
tool-topology onboard scaffold --specs tools.json --layer read --out skeleton.json

# ② 提案：读取骨架 → LLM 批量提案 → proposals.json + 人审 diff 打印
tool-topology onboard propose --topology skeleton.json [--vocabulary-from existing.json] \
    [--base-url URL --model M] --out proposals.json

# ③ 应用：人审后的 approved 子集 → 最终拓扑 JSON（Loader 校验落盘）
tool-topology onboard apply --topology skeleton.json --approved approved.json --out topology.json
```

- 三段之间没有隐式串联：propose 不落盘到拓扑、apply 必须显式吃
  approved——审阅发生在文件与人工之间，可审计；
- 退出码：scaffold/apply 失败非零；propose 传输失败非零并保留空输出
  不写半成品。

### 验收

- [ ] 三段命令离线可跑（propose 用假 HTTP 测试；真实 Ollama 可选实测）
- [ ] 半成品不落盘；apply 后产物可被 `regression fast` 直接消费

---

## 6. 最终验收场景（端到端演练）

以一份 **50 个 OpenAI 风格 specs** 的 JSON 为输入：

```text
① scaffold          → skeleton.json（50 工具骨架，人工只填 layer 归类）
② propose(本地 LLM) → proposals.json + diff（人审一次，分钟级）
③ apply             → topology.json（Loader 校验通过）
④ regression fast   → 用现有场景资产跑通覆盖判定
⑤ 逐工具框架侧代码行数 = 0；dataclass 契约工具按配方（约 5 行/工具，
   仅跨层链工具需要）
```

量化核对：编写行数相对手写路径（≥ 6 行/工具）降幅 ≥ 80%；
人工触点 = layer 归类 + capabilities diff 审阅两处。

---

## 7. 本里程碑不做（残余清单，诚实声明）

```text
layer 自动归类        方向一废弃的残留，保持人工
拓扑/建边模型改动     严格拓扑约束不变（AGENTS §2.1）
跨层类型的自动推断    dataclass 契约仍人工设计（Python 路径配方）
提案自动应用          永不（审阅门禁是里程碑的存在理由）
cost_per_call 提案    留待后续（可与计量里程碑的 drift 数据联动另立）
接入向导式 TUI/Web    不做
```

---

## 8. 错误模型（追加 core/errors.py）

```text
OnboardingError               # 根（继承 TopologyFrameworkError）
├── SpecInferenceError        # 类型推断失败（注解缺失/字符串化）
├── ProposalError             # 提案传输/解析失败
└── ApplyError                # 审阅门禁拒绝 / 落盘校验失败
```

装饰器推断失败沿用 `RegistrationError`（注册期语义不变），导出层
别名不新增。

---

## 9. 推荐目录结构

```text
src/capability_runtime/
├── onboarding/                # 新增 —— 与 composite/ resources/ 平级
│   ├── __init__.py
│   ├── openai_adapter.py      # from_openai_specs 批量适配
│   ├── proposals.py           # propose_capabilities / render_capability_diff
│   └── apply.py               # apply_capabilities（审阅门禁）
│
├── decorators.py              # 批次 A：类型推断（增量）
└── cli.py                     # 批次 D：onboard scaffold/propose/apply
```

根包统一导出公共符号。

---

## 10. 测试要求

```text
全部离线；propose 一律假 _http 注入（镜像 test_llm_router 模式）

推断      等价性 / 混用 / 显式覆盖 / 失败路径错误信息
适配器    批量注册 / 同步异步 dispatch / schema 要点并入
提案      结构化解析 / 非法名丢弃 / 词表优先 / 传输失败不写半成品
应用      门禁拒绝 / Loader 往返校验 / diff 三类标注
CLI       三段命令成功与失败路径 / apply 产物可被 regression fast 消费
兼容      既有 527 测试零改动
```

---

## 11. Definition of Done

## 推断与适配

* [ ] consumes/produces 类型推断，显式优先，失败指路
* [ ] OpenAI specs 批量适配零逐工具代码
* [ ] 跨层契约边界在文档与 docstring 明示

## 提案与审阅

* [ ] 批量提案 + 词表优先 + 非法丢弃
* [ ] 审阅门禁：提案无写入路径，apply 只吃 approved
* [ ] diff 渲染可审计

## CLI

* [ ] scaffold / propose / apply 三段，无隐式串联
* [ ] 半成品不落盘；产物可被 fast regression 直接消费

## 边界

* [ ] 类型不建边；拓扑模型零改动
* [ ] layer 保持人工；cost 提案不做
* [ ] 零第三方依赖；既有测试零改动

---

## 12. 实现进度

| 批次 | 状态 | 落点 |
| --- | --- | --- |
| A 类型推断 | [ ] | |
| B OpenAI 批量适配 | [ ] | |
| C 提案→审阅→应用 | [ ] | |
| D onboard CLI | [ ] | |
