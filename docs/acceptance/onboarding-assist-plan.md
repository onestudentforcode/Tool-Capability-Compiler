# 接入辅助里程碑 实施约定 —— 命名与目录结构

> 定位：动手写代码前敲定 onboarding-assist.md 的对象命名、包路径、
> 推断规则细节与四个批次的实现顺序。实现以本文件 + onboarding-assist.md
> 语义为准；冲突时命名以本文件为准。

---

## 0. 三条总决定（TL;DR）

1. **新增顶包 `onboarding/`**（adapter / proposals / apply），批次 A
   落在 `decorators.py` 增量，批次 D 落在 `cli.py`。
2. **类型只填契约字段**：推断产物进入 `ToolSpec.consumes / produces`
   （执行与验证契约），建边规则零改动（AGENTS §2 宪法重申）。
3. **审阅门禁落在类型层面**：`apply_capabilities` 只接受
   `Mapping[tool, Sequence[str]]` 的 approved 产物；提案对象
   （`CapabilityProposal`）没有通往落盘的参数路径。

---

## 1. 命名与口径裁定

- **推断规则（批次 A）**：
  - `consumes` 为空且函数存在位置参数 → 逐参数取类型注解；
    注解缺失或为字符串（future-annotations 模块）→ `RegistrationError`，
    错误信息指路"声明 consumes 或修正注解"；
  - `produces` 为空且返回注解为类型 → `(返回注解,)`；返回注解为
    `None`/缺失 → 保持 `()`；
  - 显式声明优先、可与推断混用；既有"参数数 ≠ consumes 数"校验不变；
  - 已知行为变化（文档化）：返回类型化值且未显式声明 produces 的工具，
    推断后将开始传播输出——仓库内无此形态，风险为零；
- **错误模型修正（对规格 §8 的落地裁定）**：装饰器推断失败沿用
  `RegistrationError`（注册期语义不变，规格中"导出层别名不新增"为准），
  故不引入 `SpecInferenceError`；实际新增
  `OnboardingError → ProposalError / ApplyError`；
- **提案返回形态（对规格 §4 草图的命名修正）**：
  `CapabilityProposalSet(proposals, invalid_dropped)`——`proposals` 只含
  通过 `validate_capability_name` 的项，`invalid_dropped` 记录
  `(tool, capability)` 对；vocabulary 复用不单独存字段，由
  `render_capability_diff` 对照词表现场标注；
- **批量适配（批次 B）**：直接构造 `ToolNode`（不经 @tool，同复合
  工厂先例）；dispatch 同步形态经 `asyncio.to_thread` 包装；description
  = 原文 + `Parameters:` + schema JSON 摘要；
- **CLI propose 的离线测试**：`capability_runtime.cli` 内以模块级导入
  `propose_capabilities`，测试用 monkeypatch 替换，不触网。

---

## 2. 目录结构

```text
src/capability_runtime/
├── onboarding/
│   ├── __init__.py
│   ├── openai_adapter.py    # from_openai_specs
│   ├── proposals.py         # propose_capabilities / render_capability_diff
│   │                        #   / CapabilityProposal / CapabilityProposalSet
│   └── apply.py             # apply_capabilities（审阅门禁）
├── decorators.py            # 批次 A：类型推断（增量）
└── cli.py                   # 批次 D：onboard scaffold/propose/apply
```

---

## 3. Step 顺序与验收点

| Step | 模块 | 验收点 |
| --- | --- | --- |
| 1 | `core/errors.py` + `decorators.py` | 推断等价手写；混用/显式优先；缺失与字符串注解指路错误 |
| 2 | `onboarding/openai_adapter.py` | 批量注册零逐工具代码；sync/async dispatch；schema 并入 description |
| 3 | `onboarding/proposals.py` | 假 HTTP 解析 / 非法丢弃 / 词表优先 / 传输失败 ProposalError |
| 4 | `onboarding/apply.py` | 未知工具拒绝；非法能力拒绝；Loader 往返校验落盘 |
| 5 | `cli.py` 三段命令 | scaffold 产物可加载；propose monkeypatch 离线；apply 产物被 regression fast 消费 |

---

## 4. 阶段边界检查表

- [ ] 类型不建边；拓扑模型与建边规则零改动
- [ ] 提案无落盘参数路径；apply 只吃 approved
- [ ] layer 保持人工；cost 提案不做；无脚本层（已冻结）
- [ ] 零第三方依赖；既有 527 测试零改动

---

## 5. 实现进度

| Step | 状态 | 落点 |
| --- | --- | --- |
| 1 | [x] | `core/errors.py` 增 OnboardingError/ProposalError/ApplyError（无 SpecInferenceError，装饰器沿用 RegistrationError）；`decorators.py` 推断：consumes 严格（缺失/字符串注解指路报错）、produces 宽松（字符串注解跳过——兼容 future-import 模块中的既有工具） |
| 2 | [x] | `onboarding/openai_adapter.py`：from_openai_specs（sync/async dispatch、schema 并入 description、重复名校验）+ skeleton_payload（批次 D 复用） |
| 3 | [x] | `onboarding/proposals.py`：CapabilityProposalSet（非法名 dropped 有记录）/ propose_capabilities（批量一次调用、词表注入、假 _http）/ render_capability_diff（vocab/new/dropped 三类标注） |
| 4 | [x] | `onboarding/apply.py`：未知工具拒绝、非法能力拒绝、与既有能力并集、TopologyLoader 往返校验后落盘 |
| 5 | [x] | CLI `onboard scaffold/propose/apply`：三段无隐式串联；propose monkeypatch 离线测试；端到端闭环测试（scaffold→apply→regression fast 覆盖判定通过） |

全量 544 tests / compileall / diff-check 通过；测试 `tests/unit/test_onboarding.py`（17 项）。
