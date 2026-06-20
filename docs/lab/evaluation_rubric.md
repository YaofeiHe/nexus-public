# Nexus Lab 结构化评测 Rubric

## 目的

本 rubric 定义当前独立 evaluator 的本地评测口径。它面向审批 agent 或 Verix 式 evaluator：读取执行 harness 产出的 `execution.json` 或 case directory，再检查目标项目和 `<NEXUS_ARTIFACT_PATH>` artifacts，输出可审计的 verdict JSON。

重要边界：当前脚本是文件/内容启发式检查，不等于完整智能评审。`pass` 只表示必需证据面存在并通过基础内容判断；它不能证明 Nexus 已经完全理解用户意图，也不能替代 skill replay、人工或 LLM 对业务质量、端到端可用性和用户体验的判断。

## 输入契约

Evaluator 接受以下输入形态：

- `execution.json` 文件。
- 包含 `execution.json` 的 case directory。
- CLI override：`--nexus-root`、`--project-path`、`--run-id`、`--run-dir`、`--case-id`、`--raw-intent`。

`execution.json` 应尽量提供这些字段或等价嵌套字段：

- `case_id`
- `run_id`
- `project_path`
- `run_dir`
- `instruction` 或 `raw_user_request`
- `expected_behavior`

如果字段缺失，脚本会做保守推断；无法定位项目或 run artifact 时，对相关维度判为 `blocked`，而不是把缺证据包装成通过。

## 输出契约

Verdict JSON 至少包含：

- `case_id`
- `run_id`
- `verdict`: `pass` / `fail` / `blocked`
- `failed_surface`
- `expected`
- `observed`
- `evidence_refs`
- `generalization_check`
- `problem_matrix`
- `problem_axis_results`
- `required_nexus_change`

每个 `evidence_refs` 条目包含路径、存在性、说明，以及可用时的行号和短摘录。证据引用必须足够让审批 agent 回到本地文件复核，而不是只相信 evaluator 的摘要。

`problem_axis_results` 固定来自 `scripts/lab/nexus_problem_matrix.json`。每个 case 都应输出 16 条问题轴结果；它们不是可选项，也不是“某 case 只负责某些问题”的局部分工。

## 总体判定

- `pass`：所有适用维度通过；未适用维度会在 `observed` 中说明原因。
- `fail`：目标产物或 run artifact 可检查，但缺少应有证据、内容过于占位、或出现样例泄漏/硬编码迹象。
- `blocked`：执行证据不足，或 Nexus 自身停在需要认证、权限、外部配置、人工审批等阻断状态；此时 evaluator 不能给出完整质量结论。

如果同一 case 同时有 `fail` 和 `blocked`，总体判定为 `fail`，因为已有可确认失败面。

总体判定同时考虑 11 个技术维度和 16 个问题轴。只要问题轴中出现 `fail`，即使底层维度暂时没有直接失败，也不能把 case 视为通过。

## 评测维度

### intent_capture

检查 `docs/intent/original-requirement.md` 和 `.nexus/project-intent.json`。原始输入必须保留用户原话和关键约束，不能只写项目摘要。

失败通常指向 `init_project` 或 `write_project_docs` 的原始意图保存链路。

### intent_understanding

检查 `docs/intent/normalized-requirement.md`、`docs/requirement-trace.md` 或等价 understanding/trace 文件。规范化需求必须说明目标、用户动作、数据对象、workflow、验收标准、边界和未确认点。

失败通常说明 Nexus 只保存了输入，没有把输入转成可执行项目理解。

### reference_reading_record

当 prompt 要求读取本地历史、参考文件、fixture、source material 或检索材料时，检查 source/material index、reference materials、source status 或 search result artifacts。记录应说明计划读什么、实际读了什么、提取了什么、跳过什么和原因。

没有参考材料要求的 case 会标记为不适用通过。

### search_plan_log

当 case 涉及 search/research/检索/调研/读取材料时，必须同时有 plan 和 log/result。可接受证据包括 `search_rounds/*/search_plan.json`、`source_status.json`、`coverage_review.json`、`docs/search-plan.md`、`docs/search-log.md` 等。

只有 `completed` 状态或最终摘要不算检索记录。

### domain_workflows

检查 `workflows/`、`docs/workflows/`、`docs/operation-guide.md` 和 `docs/project-overview.md`。项目必须包含当前 prompt 的领域词和实际流程，不能只有 Nexus 治理壳。

此维度用于发现“能初始化目录但不能生成用户会用的项目工作区”的失败。

### validation_script

检查 `scripts/validate_project.py` 或等价 validation script，并确认 operation guide 提到如何运行。第一版 evaluator 不运行目标项目 validation script，避免未知副作用；它只检查本地验证入口是否存在且不是空壳。

### sync_artifacts

当 case 涉及 GitHub private、Feishu、public 发布或同步时，检查 `<NEXUS_ARTIFACT_PATH>` 中的 `github_*`、`self_sync_*`、`post_change_*`、`*feishu*` artifacts。public 发布必须有 staging 和 validation 证据。

如果 Nexus 因缺少认证或外部配置明确阻断，并在 interaction/state 中保留原因，此维度可判为 `blocked`。

### recovery_rebind

当 case 涉及 debug handoff、rebind、recover 或恢复阻断时，检查：

- `handoffs/debug_handoff.json`
- `worklogs/debug_worklog.json`
- `rebind/rebind_result.json`

Worklog 至少应包含 `diagnose`、`edit`、`test` 三类证据，并保持原 `run_id`。

### playbook_persistence

当 case 要求恢复经验沉淀或复用时，检查目标项目 `.nexus/recovery-playbook.json` 和 run-local recovery context/related experience artifacts。第二次相似失败必须能看到 recovery-history lookup，而不是直接开一条全新恢复链。

### monitor_next_prompt

检查 `interaction.json` 和 `state.json` 是否能给 monitor agent 一个明确状态：下一条 `$nexus-workflow ...`、approval、continue、recover、handoff、rebind、stop/intervention 或具体 blocked reason。

泛泛的 `completed/running` 不足以通过这个维度。

### anti_hardcoding

检查目标项目 artifacts 是否出现当前 prompt 没有提供的样例名或样例领域词，例如 `feeler`、`probe`、简历/面试、事故复盘、博物馆、语言学习等。也检查当前 prompt 的领域词是否真正出现在项目产物中。

此检查只能发现显性的样例泄漏或弱领域覆盖；它不能证明 Nexus 源码没有硬编码。源码级硬编码仍需要 `modification_plan.json` 指向的修改 agent 或 monitor review 检查。

## 问题轴聚合

`scripts/lab/nexus_problem_matrix.json` 将 16 个 Nexus 问题映射到现有技术维度。Evaluator 对每个问题轴的判定规则是：

- 任一映射维度 `fail`，问题轴为 `fail`。
- 没有 `fail` 但存在 `blocked`，问题轴为 `blocked`。
- 所有映射维度通过，问题轴为 `pass`。
- 没有可用映射维度，问题轴为 `blocked`。

这不是智能评审替代品。它的作用是确保每个 case 都能暴露同一组 Nexus 系统问题，并把失败聚合成修改 agent 可消费的 `modification_request.json`。

## 当前能判什么

- 能判目标项目和 run artifacts 是否存在关键证据面。
- 能判原始意图、规范化理解、workflow、validation、sync、recovery、playbook、monitor prompt 是否有基础可审计证据。
- 能发现明显占位文本、缺失 artifact、public sync 缺 staging/validation、debug rebind 缺 worklog、样例领域词泄漏。
- 能把失败面映射到大致 Nexus 通用入口，填入 `required_nexus_change`。
- 能按 `<LOCAL_PATH_REDACTED>` 的 16 个问题轴聚合 case 结果。

## 当前不能判什么

- 不能替代完整智能评审，不能证明用户意图被高质量满足。
- 不运行目标项目验证脚本，因此不能证明目标项目业务校验真实通过。
- 不触发 GitHub、Feishu、public 发布或任何外部同步。
- 不审查 Nexus 源码 diff 是否真的没有硬编码。
- 不判断 UI/文档表达质量是否达到人工可交付标准，只做基础内容和证据检查。
- 不自动修改 Nexus；修改由当前对话或外部修改 agent 根据 `modification_request.json` 和 `modification_plan.json` 执行。

## 使用示例

```bash
python -m py_compile scripts/lab/evaluate_nexus_case.py
python scripts/lab/evaluate_nexus_case.py docs/lab/runs/<loop_id>/cases/<case_id>/execution.json
python scripts/lab/evaluate_nexus_case.py docs/lab/runs/<loop_id>/cases/<case_id> --output evaluation.json
```

输出应使用 `docs/lab/evaluation_schema.json` 校验。后续审批 agent 可以读取 `failed_surface`、`dimensions[*].observed`、`evidence_refs` 和 `required_nexus_change`，决定是否放行、阻断或生成修改请求。
