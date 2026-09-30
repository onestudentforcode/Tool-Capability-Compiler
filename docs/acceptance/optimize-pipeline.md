# Optimize Pipeline —— 剪枝编排里程碑（方向 4）

> 定位：AGENTS.md §7 可开发方向 4 的验收规格（里程碑式命名）。Phase 4
> 已交付全部**组件**（Evidence / Candidate / Protection / Counterfactual /
> Probe / Batch / 快慢双门 / Diversity Guard / DatasetSplit /
> TopologyVersion），但它们只能被测试与演示脚本（run_scale.py）进程内调用。
> 本里程碑补上**可操作的三段式编排**：让"Prune"成为一条有文件、有验证
> 记录、有版本号、可审计的命令行工作流。

---

## 0. 目标形态

```bash
# ① analyze（只读）：证据 → 候选补丁提案
tool-topology optimize analyze \
    --topology t.json --scenario suite.json \
    --slow-report artifacts/slow_regression/run_xxx \
    [--min-opportunity 5] \
    --out candidates.json

# ② validate（不动任何东西）：补丁 → 三关判定
tool-topology optimize validate \
    --topology t.json --scenario suite.json \
    --patch candidates.json \
    [--trials 3 --expected-fact k=v ...] \
    --out verdict.json          # ACCEPT 退出码 0 / REJECT 退出码 1

# ③ commit（唯一写操作；只接受带 ACCEPT 记录的补丁）
tool-topology optimize commit \
    --version v2 --patch candidates.json \
    --validation verdict.json \
    --versions-dir artifacts/versions

# ④ rollback（可选）：回到声明拓扑
tool-topology optimize rollback --versions-dir artifacts/versions --to v1
```

三段之间**零隐式串联**（同 onboarding-assist 的审阅门禁哲学）：
analyze 不写拓扑、validate 不写版本、commit 拒绝没有 ACCEPT 验证记录的
补丁——人工确认落在"审阅 candidates.json + 亲自敲 commit"两处。

旧版 `optimize`（两版本间确定性报告）保留为 `optimize report`，行为不变。

---

## 1. 磁盘适配层（Step 1 的核心工作）

慢回归落盘产物是 JSON，组件入参是内存对象——补两类适配器
（`optimization/artifacts.py`），全部离线可测：

```text
observation_report_from_dir(run_dir)
    node_stats/edge_stats/route_stats.json → ObservationReport
    （selection_events / scenario_route_distribution 不在落盘内，
     重建为空——EvidenceAggregator 不消费这两项，如实标注）

trial_results_from_dir(run_dir)
    traces.jsonl → 轻量重建 TrialResult（Trial / 状态 / route 段 /
    evaluation success / latency / cost / access）
    供 EvidenceAggregator 的逐边 successful_route_count 重聚合；
    重建是有损的（错误对象→字符串），文档明示，不冒充无损
```

`rows_from_run`（Phase 5）的经验直接沿用：manifest 与 traces 版本矛盾
即拒绝。

---

## 2. analyze：证据 → 候选提案（只读）

流水线（全部现成组件的组合）：

```text
DatasetSplit（hash 稳定切分）
    → 只取 Optimization 集（Validation/Sentinel 永不参与候选生成 §59）
ProtectionRegistry（sentinel 场景来自 suite 的 metadata.sentinel）
EvidenceAggregator（磁盘适配产物）
CandidateDetector（PruningConfig 可经 --min-opportunity 调整门槛）
CounterfactualRunner（对候选批做 Fast 预检：覆盖掉 → 标 COVERAGE_DROP）
BatchCandidateBuilder（分组 + max_pruning_batch_size）
    → candidates.json
```

`candidates.json` 内容：TopologyPatch（`disabled_edges/disabled_nodes`）+
逐候选记录（status / reason / evidence 摘要 / counterfactual 结论 / 分组
id）。`PROBE_REQUIRED` 候选如实标注，**不自动补证**（probe 是显式动作，
见 §6 非目标）。

退出码：分析完成恒 0（候选为空也是合法结论，打印 "(no candidates)"）。

---

## 3. validate：三关判定（不动任何东西）

```text
build_candidate(topology, patch)          # 声明拓扑不可变（§4）
FastValidationGate
    base vs candidate 在 Validation+Sentinel 集上跑 Fast Regression
    （global / category / sentinel 三域；sentinel 单场景降级 → REJECT）
SlowValidationGate（仅 Fast 门通过后执行）
    base vs candidate 用相同执行配置在 Validation 集上跑慢回归
    （成功率 / 质量 / 错误率增量）
RouteDiversityGuard（成功路线族不低于配置下限）
    → verdict.json + 文本报告
```

- 执行配置沿用 `regression slow` 的参数面（`--trials` /
  `--expected-fact` / `--max-concurrency`）；**拓扑必须可执行**
  （implementation 绑定，批次 B 语义——不可执行直接拒绝，退出码 2）；
- `verdict.json`：ACCEPT/REJECT、逐关结果、失败原因定位（受影响
  category / 场景 / 边）、base 与 candidate 的对照指标；
- 退出码：ACCEPT → 0；REJECT → 1（CI 友好，同 `--fail-on-regression`
  先例）；参数/输入错误 → 2；
- 复现性：DatasetSplit 确定性 + 慢回归配置全量记录进 verdict。

---

## 4. commit：唯一写操作，验证记录是硬门槛

```text
optimize commit --version v2 --patch candidates.json \
    --validation verdict.json --versions-dir artifacts/versions
```

- **门禁（类型层面）**：commit 只接受 verdict 文件路径并校验其为
  ACCEPT 且 patch 指纹一致（disabled 集合逐项比对）——没有验证记录、
  记录 REJECT、或补丁与记录不匹配 → `OptimizationError`，退出码 2；
- `TopologyVersion.commit_patch`（现成）→ 版本记录文件落盘
  `versions/v2.json`（base / patch / composed patch / 来源验证记录）；
  同时导出该版本的**可执行拓扑 JSON**（补丁已应用）供 Rank/Online 消费；
- 人工确认 = 审阅 candidates/verdict + 亲自执行 commit 命令，与
  phase4-plan §5 的"commit 显式人工确认"一致；
- `rollback`：按 §0 形式回到指定版本（沿用现成 `rollback()`），
  版本历史不可变（§101）。

---

## 5. 错误模型（追加 core/errors.py）

```text
OptimizePipelineError            # 根（继承 TopologyFrameworkError）
├── ArtifactLoadError            # 慢回归产物缺失/版本矛盾/不可解析
└── CommitGateError              # 缺 ACCEPT 记录 / 指纹不匹配 / 版本冲突
```

（复用既有 OptimizationError 家族承载组件级失败，不重复定义。）

---

## 6. 非目标

```text
三段自动串联（analyze 完自动 validate 自动 commit）—— 永不
PROBE_REQUIRED 自动补证 —— probe 保持显式（未来可另立 optimize probe）
多轮自动循环（单次调用 = 单轮；轮次由人驱动）
新剪枝算法 / 新门指标 —— 只编排现有组件
在线任何东西
批量拆分失败的二分定位自动化（BatchCandidateBuilder 已有 bisect，
  编排层不包装）
```

---

## 7. 开发顺序

| Step | 模块 | 验收点 |
| --- | --- | --- |
| 1 | `optimization/artifacts.py` + errors | 两类适配器往返正确；版本矛盾拒绝；离线 |
| 2 | `optimization/pipeline.py::analyze` | Optimization 集隔离；保护/反事实预检进候选记录；candidates.json 可序列化往返 |
| 3 | `pipeline.py::validate` | 三关顺序（Fast 不过不跑 Slow）；verdict 完整可定位；退出码三档 |
| 4 | `pipeline.py::commit/rollback` | 验证记录门禁三类拒绝；版本文件落盘；导出可执行拓扑 JSON |
| 5 | CLI 接线（optimize analyze/validate/commit/rollback + 旧命令转 report） | 三段命令离线可跑；教程/README 更新 |
| 6 | 端到端集成 | run_scale 产物 → analyze → validate → commit v2 → rank 消费 v2 拓扑 |

测试约束：全部离线；validate 的慢回归用 sandbox 风格可执行拓扑
（implementation 绑定 + StructuredEvaluator expected-fact），不触网。

---

## 8. Definition of Done

## 适配

* [ ] 慢回归产物 → ObservationReport / TrialResult 重建，版本门禁
* [ ] 有损重建明示，不冒充无损

## analyze

* [ ] 只读；Validation/Sentinel 不参与候选
* [ ] 保护 / 反事实预检 / 批次分组进候选记录
* [ ] 空候选是合法结论

## validate

* [ ] 三关顺序执行，Fast 拦截则不跑 Slow
* [ ] REJECT 定位到 category / 场景 / 边
* [ ] 退出码三档（0/1/2）；执行配置记录进 verdict

## commit / rollback

* [ ] ACCEPT 记录 + 指纹一致是硬门槛
* [ ] 版本文件 + 可执行拓扑 JSON 导出；历史不可变
* [ ] rollback 可回退

## 边界

* [ ] 零隐式串联；无自动补证；无自动多轮
* [ ] 声明拓扑全程不可变
* [ ] 既有 544 测试零改动

---

## 9. 最终验收场景

用批次 E 的 250-trial 慢回归产物 + sandbox 可执行拓扑：

```text
analyze   → 候选报告（单 Provider 拓扑预期大量 PROTECTED + 若干
            INSUFFICIENT_EVIDENCE，如实在候选文件中呈现）

validate  → 对构造的可安全补丁（禁用一条确无使用的边）跑出 ACCEPT；
            对构造的有害补丁（禁用 sentinel 依赖边）跑出 REJECT 且
            原因定位到 sentinel 场景

commit    → v2 版本文件 + v2 可执行拓扑；无 verdict / REJECT verdict /
            指纹不匹配三类拒绝全部触发

回环      → v2 拓扑可被 rank 消费；rollback 可退回 v1
```

---

## 10. 实现进度

| Step | 状态 | 落点 |
| --- | --- | --- |
| 1 | [ ] | |
| 2 | [ ] | |
| 3 | [ ] | |
| 4 | [ ] | |
| 5 | [ ] | |
| 6 | [ ] | |
