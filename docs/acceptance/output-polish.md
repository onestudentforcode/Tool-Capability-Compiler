# Output Polish — 输出润色里程碑

> 定位：小里程碑，纯渲染/输出层，零语义变更。范围：P2-P6（用户
> 裁定 2026-10：P1 运行过程可观测性不做；P7/P8 暂缓）。来源：
> 日志/输出全面评估（见会话记录；顺带修复 slow_refund 类型引用
> 裸名导致 quickstart 断裂的回归，b814030）。

## 原则

- 不改任何业务/统计语义；渲染函数签名只做向后兼容扩展；
- CLI 保持英文 ASCII（终端安全）；演示脚本/review 保持中文风格；
- 既有输出断言测试的调整必须逐条对应到本计划条目，不得顺手弱化。

## 条目与实现要点

### P2 slow 渲染：失败明细与类别白话注解

- `render_slow_report(report, failures=None)` 新增可选参数（默认
  None 保持现行为）：CLI 与演示脚本传入 trial 结果；
- Failure Categories 计数行旁内联一行白话注解（共享常量表：
  answer_error / tool_execution_error / timeout / layer_error /
  routing_error / fixture_error / evaluation_error 等）；
- 追加 "Failure Samples" 段：每类别最多 2 个代表 trial（按 trial id
  确定性选取），格式 `scenario#trial — reason`，reason 截断 ~100 字符。

### P3 optimize validate：三门明细行

- VERDICT 之前打印三行门禁结果（PASS / SKIP / FAIL 对齐）：
  fast (coverage) / slow (regression) / diversity；
- REJECT 时 failures 行归属到对应门下展示。

### P4 演示脚本 dict 直印治理

- `render_review.py` 的 table/fmt helper 公开为公共 API
  （`table()` / `fmt()`），四个演示脚本统一 import；
- 治理点：run_scale 的 Coverage / Status breakdown / Failure
  categories 行；run_closed_loop 的 Round 指标行与状态分布；
  run_routing_comparison 的 per-mode 行；run_composite_demo 的
  iterations Counter；
- 目标形态示例：`free | trials 24 | success 0.2500 | cost 0.0123 |
  latency 45.6ms | routes 21 | seeds 0/12`。

### P5 optimize CLI 统一 run header

- analyze / validate 增加头部：命令标题 + topology/scenario/patch
  输入路径回显（与 fast/slow 的 Suite 块风格对齐）；
- validate 增加 elapsed footer；commit/rollback 已有 footer 风格，
  保持并统一缩进。

### P6 rank 渲染：路线宽度治理

- `render(report, width=88)`：canonical 超宽时按层段折行，
  续行悬挂缩进；不省略任何工具（wrap 不截断）；
- CLI `--width` 可调（0 = 不折行）。

## 测试与验收

- [x] P2：render_slow_report(failures=...) 含样本行与注解行；无
      failures 时输出与现状逐字节一致（向后兼容断言）
- [x] P3：validate ACCEPT 路断言三门行（REJECT 路经 failures 归属展示；
      SKIP/FAIL 标记覆盖）
- [x] P4：四个演示脚本输出不再含 `{'` 字面量（routing comparison capsys 断言 + composite demo 目检）
- [x] P5：analyze/validate stdout 含输入回显头（validate 另含耗时）
- [x] P6：render 宽度断言（段不拆、工具零省略、悬挂缩进、None=旧行为）；
      rank CLI --width（0 关闭）
- [x] 全量测试（653）+ compileall + diff-check；README 无需变更

## 提交划分

docs 本文档 → feat P2+P3（CLI 渲染）→ feat P4+P5（脚本与头）→
feat P6（rank 折行）→ 各批自带测试。
