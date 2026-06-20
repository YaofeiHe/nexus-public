# Nexus E2E Instruction Test Set

This document defines end-to-end `$nexus-workflow ...` instruction tests for the lab execution harness. It is a test definition artifact: do not run full cases from documentation edits, and do not modify Nexus core code unless the monitor explicitly enters the modification step.

The tests target the current Nexus goal: a system that can take a user's initial project intent, build or iterate a project workspace, preserve and explain what it read, keep sync/recovery behavior consistent, accept independent evaluation feedback, and continue modifying the project until the result matches the intended behavior rather than a hardcoded sample.

## Execution Harness Contract

- Execute in an isolated workspace such as `{E2E_ROOT}` and with a fresh `CODEX_HOME`; never reuse the user's live project roots as test targets.
- Treat `{E2E_ROOT}`, `{PROJECT_PATH}`, `{RUN_ID}`, `{HANDOFF_ID}`, and `{FIXTURE_DIR}` as placeholders that the execution agent must replace at runtime.
- Inputs must be real `$nexus-workflow ...` prompts. The execution agent may run local file inspection and checks between prompts, but it must not edit Nexus code except when the later modification agent is explicitly assigned to do so.
- Use real providers and real Nexus workflow behavior. Mock mode is invalid unless a test explicitly asks for a local dry check, which none of the tests below do.
- Samples such as `feeler` and `probe` may be used only as quality references by evaluators. Nexus must not pass by recognizing those names or copying their exact layout.
- A test may pass with equivalent files only when Nexus writes a machine-readable index that maps the expected canonical path to the actual path and the evaluator can verify the content.

## Shared Pass Requirements

Every test that creates or updates a project must preserve these common surfaces unless the test says otherwise:

- `docs/intent/original-requirement.md`
- `docs/intent/normalized-requirement.md`
- `.nexus/project-intent.json`
- `docs/project-overview.md`
- `docs/operation-guide.md`
- `.nexus/board.json`
- run-local `interaction.json` and `state.json` under `<NEXUS_ARTIFACT_PATH>`

Every test that reads external or local reference material must leave a human-readable record of:

- requested sources and why they were needed
- sources actually read or searched
- sources unavailable or skipped, with reason
- facts or requirements extracted from each source
- requirements that remain uncertain

## Test 01: Complex Project Initialization Without Template Leakage

**Purpose:** Verify that Nexus can initialize a complex, non-sample project and turn the raw user intent into a usable project system, not just a few generic governance files.

**Initial instruction:**

```text
$nexus-workflow 从零初始化一个项目：我需要一个面向非营利组织项目申请的工作区，帮助我把资助方要求、项目预算、受益人证据、申请材料草稿、提交截止日期和后续复盘联系起来。它要能记录原始申请意图，把不同资助方的要求拆成可比较字段，生成材料准备 workflow，保留证据来源，最后要有一个本地验证脚本检查关键文件是否齐全。不要给项目固定命名，让 Nexus 自己推荐并说明命名依据。父目录 {E2E_ROOT}/projects。
```

**Expected files, states, and artifacts:**

- `docs/intent/original-requirement.md` contains the full raw prompt, including budget, evidence, deadlines, workflow, and validation requirements.
- `docs/intent/normalized-requirement.md` rewrites the intent into project goals, user workflows, data objects, required documents, validation criteria, and known risks.
- `docs/intent/project-understanding.md` or indexed equivalent maps each user requirement to a concrete project capability.
- `.nexus/project-intent.json` records selected project name, whether the name came from the user or model, source requirement path, and generated artifact list.
- Project-specific docs exist for at least: funder requirement comparison, budget tracking, evidence/source registry, application material workflow, deadline tracking, and review/retrospective.
- A validation command or script exists, such as `scripts/validate_project.py`, and the operation guide explains how an executor should run it.
- `interaction.json` must expose a clear next `$nexus-workflow ...` prompt and must not hide pending approval or sync work behind a vague completed state.

**Forbidden pass conditions:**

- Passing with only `original-requirement.md`, `normalized-requirement.md`, and a generic `project-overview.md`.
- Using `feeler`, `probe`, resume/interview vocabulary, or the sample project names as fixed logic.
- Claiming the project name was user-specified when the prompt did not specify one.
- Omitting project-specific workflows and validation while reporting initialization as complete.

**Failure判定:**

- Fail if fewer than 80 percent of the raw user requirements can be traced to generated files or `.nexus/project-intent.json`.
- Fail if the evaluator cannot identify what project the workspace is supposed to support without rereading the original prompt.
- Fail if the generated structure is indistinguishable from a generic Nexus governance wrapper.

## Test 02: Supplemental Requirement Update Without Destructive Reinitialization

**Purpose:** Verify that "补充初始化" and later requirement updates preserve existing project content and update only the governance/intent/workflow surfaces needed for the new requirement.

**Initial instruction:**

```text
$nexus-workflow 对项目 {PROJECT_PATH} 补充初始化：新增一个“资助方反馈复盘”流程，要求记录被拒理由、需要补充的证据、下次申请窗口、负责人和下一步动作。不要清空、重建、覆盖已有业务文档；只补齐 Nexus 管理的意图、项目说明、操作指南、记录板、同步配置和必要索引。
```

**Expected files, states, and artifacts:**

- Existing business docs from Test 01 remain present and their substantive user text is not deleted.
- `docs/intent/normalized-requirement.md` or an intent update file records the new feedback-loop requirement and links it back to the original intent.
- `docs/project-overview.md` and `docs/operation-guide.md` describe the feedback-loop workflow without replacing existing workflows.
- `.nexus/board.json` records a point or status update for the supplemental initialization.
- Run artifact shows route `supplemental_init` or equivalent, not destructive `init-project` recreation.
- If sync is enabled, sync artifacts prove the update followed the standard private/Feishu path or block with the exact missing credential/auth reason.

**Forbidden pass conditions:**

- Deleting or recreating the project directory.
- Replacing detailed user-written docs with short boilerplate.
- Treating "补充初始化" as permission to rewrite business logic.
- Reporting success while silently skipping sync checks when sync is configured.

**Failure判定:**

- Fail if any preexisting business file is removed or materially overwritten without an explicit destructive confirmation.
- Fail if the new requirement is not traceable from intent docs to operation guide and board.
- Fail if the run starts a new unrelated project instead of updating `{PROJECT_PATH}`.

## Test 03: Local History and Reference Material Reading Record

**Purpose:** Verify that Nexus can use user-specified local history/reference files and leave a readable record of what was read, what was extracted, and what was not covered.

**Fixture setup for execution agent:**

Create fixture files under `{FIXTURE_DIR}/reference_pack/`:

- `strategy_notes.md`: describes a "field incident review workspace" with incident intake, root-cause notes, owner assignment, remediation verification, and weekly leadership summary.
- `prior_chat_excerpt.md`: includes corrections from the user: examples are not scope limits; record every skipped source; never summarize search as only "completed".
- `existing_files_index.md`: lists expected existing files that should be considered during initialization.

**Initial instruction:**

```text
$nexus-workflow 从零初始化一个项目：根据 {FIXTURE_DIR}/reference_pack/strategy_notes.md、{FIXTURE_DIR}/reference_pack/prior_chat_excerpt.md 和 {FIXTURE_DIR}/reference_pack/existing_files_index.md 构建一个现场事故复盘工作区。必须先记录读取/检索计划，说明每个文件要解决什么问题；再记录实际读取结果、提取出的需求、没有读到或仍不确定的内容；最后再生成项目结构。父目录 {E2E_ROOT}/projects。
```

**Expected files, states, and artifacts:**

- `docs/intent/source-material-index.md` or indexed equivalent lists all three fixture files with role, read status, and extracted requirements.
- Run artifacts include a reading/search plan before final project generation, such as `reports/source_reading_plan.md` or `search_rounds/round_1/search_plan*.json`.
- Run artifacts include actual reading/search records, such as `reports/source_reading_log.md`, `tool_results/source_status.json`, or `search_rounds/round_1/source_status.json`.
- The generated project docs include incident intake, root-cause analysis, owner assignment, remediation verification, weekly summary, and user-correction rules.
- `interaction.json` artifact refs include the plan and log, not just a final status.

**Forbidden pass conditions:**

- Storing only file paths without extracted requirements.
- Saying "检索完成" without a plan and log.
- Ignoring `prior_chat_excerpt.md` because it looks like a correction rather than a feature request.
- Treating the three fixture files as optional when the prompt explicitly requires them.

**Failure判定:**

- Fail if any of the three fixture files is absent from the source index.
- Fail if extracted requirements cannot be traced to generated project files.
- Fail if skipped/unread files are not reported with concrete reasons.

## Test 04: Sync Behavior Across Private, Feishu, and Public Paths

**Purpose:** Verify that initialization and explicit sync prompts use the expected sync sequence and do not confuse private sync, Feishu guide sync, and public release.

**Initial instruction:**

```text
$nexus-workflow 执行项目 {PROJECT_PATH} 的自同步。要求先同步 GitHub private；如果飞书配置可用，再同步整体操作指南到飞书；如果飞书同步写回了本地绑定或记录，再追加一次 GitHub private 同步保存写回结果。不要执行 GitHub public 发布，除非我另行明确确认。
```

**Follow-up public instruction:**

```text
$nexus-workflow 同步到 GitHub public，项目路径 {PROJECT_PATH}，确认 public 发布。public staging 必须做 secret/private metadata scan，并做可安装/可读取验证。
```

**Expected files, states, and artifacts:**

- Self-sync run records private sync first, then Feishu guide/autosync if configured, then private resync after Feishu writeback when applicable.
- Public sync occurs only after the explicit public instruction.
- Public run artifacts include staging, secret/private metadata scan, sync result, validation, and fresh-clone or fresh-copy validation when available.
- `interaction.json` must block with a concrete setup/auth reason if GitHub or Feishu credentials are missing; it must not report completed while silently skipping configured capabilities.
- Public staging must exclude local absolute paths, private repo names, Feishu URLs/tokens, Nexus internal run IDs, and private-only files.

**Forbidden pass conditions:**

- Triggering public push during self-sync without explicit public confirmation.
- Stopping after Feishu writeback without the additional private resync.
- Reporting "synced" while only generating local docs.
- Looking for sync artifacts under the target project root when Nexus run artifacts are under `<NEXUS_ARTIFACT_PATH>`.

**Failure判定:**

- Fail if sync order cannot be reconstructed from artifacts.
- Fail if public release lacks staging scan and validation artifacts.
- Fail if credential/auth blocks lack a concrete next `$nexus-workflow ...` prompt.

## Test 05: External Codex Debug Handoff and Same-Run Rebind

**Purpose:** Verify that leaving Nexus workflow for local Codex debugging is always registered, logged, and returned to the original run instead of starting a fresh side flow.

**Setup for execution agent:**

Use a target project with a deliberately failing validation script or a dirty-worktree/code-change blocker that requires local diagnosis. The execution agent must not fix the issue before registering a Nexus debug handoff.

**Initial instruction:**

```text
$nexus-workflow 对项目 {PROJECT_PATH} 生成一个实现计划：修复当前验证失败，使项目验证脚本能够检查 intent、source index、operation guide 和 workflow 文档是否齐全。计划通过后进入 code-change，并在需要本地 debug 时必须先登记 debug handoff。
```

**Required continuation prompts when debug is needed:**

```text
$nexus-workflow 脱离 workflow 进行 debug 并登记：修复验证脚本或 code-change 阻断，run_id {RUN_ID}
```

```text
$nexus-workflow 登记 debug 诊断，run_id {RUN_ID}，handoff_id {HANDOFF_ID}，kind diagnose，说明：定位验证失败/阻断根因
```

```text
$nexus-workflow 登记 debug 修改，run_id {RUN_ID}，handoff_id {HANDOFF_ID}，kind edit，说明：完成最小必要修复并记录变更路径
```

```text
$nexus-workflow 登记 debug 测试，run_id {RUN_ID}，handoff_id {HANDOFF_ID}，kind test，说明：验证脚本或 pytest 通过
```

```text
$nexus-workflow 回跳到刚才的 workflow，run_id {RUN_ID}，handoff_id {HANDOFF_ID}
```

**Expected files, states, and artifacts:**

- Run directory contains `handoffs/debug_handoff.json` and `handoffs/debug_session.json`.
- Run directory contains `worklogs/debug_worklog.json` with at least one `diagnose`, one `edit`, and one `test` entry.
- Rebind writes `rebind/rebind_result.json` and resumes the original `{RUN_ID}`.
- `interaction.json` after rebind shows the next original workflow step or recovery-playbook approval, not a new unrelated run.
- If rebind fails because evidence is incomplete, it blocks with the missing evidence category.

**Forbidden pass conditions:**

- Local debugging before `handoff-for-debug`.
- Rebinding with a new run ID.
- Rebind based on a freeform summary without debug worklog entries.
- Treating a failed fallback attempt as terminal while pending continuation or debug options remain.

**Failure判定:**

- Fail if `{RUN_ID}` changes across handoff, worklog, and rebind.
- Fail if `diagnose`, `edit`, or `test` evidence is missing.
- Fail if Nexus cannot explain whether the original workflow continued or why it is still blocked.

## Test 06: Recovery Playbook Persistence and Reuse

**Purpose:** Verify that successful external recovery is approved into the project recovery playbook and reused for a similar later failure before high-intensity recovery planning.

**Initial instruction after Test 05 rebind:**

```text
$nexus-workflow 审批 {RUN_ID} 的 recovery-playbook，把这次 debug handoff/rebind 成功经验沉淀到项目恢复记录。
```

**Second-run instruction with similar failure:**

```text
$nexus-workflow 在项目 {PROJECT_PATH} 上再次执行一个会触发相似验证阻断的修改流程。遇到问题时先查 recovery playbook 和相关恢复经验，再决定是否调用外部 Codex debug；不要直接开启一条全新的恢复思路。
```

**Expected files, states, and artifacts:**

- Project contains `.nexus/recovery-playbook.json` with an entry for the debug-rebind recovery.
- Project contains or updates `docs/recovery-records.md` when that surface is supported.
- The second run writes `tool_results/related_recovery_experience.json` or equivalent before recovery planning.
- Recovery prompt/result references the prior entry and explains whether it is applicable to the current failure.
- If the prior recovery is not applicable, Nexus records why, then proceeds with a new recovery path.

**Forbidden pass conditions:**

- Writing only run-local `recovery_result.json` without approving into the project playbook.
- Treating the existence of recovery code as proof that this run checked recovery history.
- Calling external Codex without first recording playbook/related-experience lookup.
- Reusing the old fix blindly when failure signature, target path, or risk differs.

**Failure判定:**

- Fail if `.nexus/recovery-playbook.json` is missing after approval.
- Fail if the second run has no artifact proving recovery-history lookup.
- Fail if the second run cannot distinguish exact match, related experience, and unrelated prior recovery.

## Test 07: Verix-Style Independent Evaluation Feedback Loop

**Purpose:** Verify that Nexus can accept an independent evaluator's failure report and convert it into a concrete modification loop driven by the original intent, not by Nexus self-review.

**Initial instruction:**

```text
$nexus-workflow 对项目 {PROJECT_PATH} 启动 Verix 式独立评测：评测者必须独立读取 docs/intent/original-requirement.md、docs/intent/normalized-requirement.md、source-material-index、项目产物和验证脚本，模拟用户通过 skill 输入使用这个项目，判断结果是否满足初始意图。把失败点写成可执行修改要求，然后让 Nexus 根据这些要求继续规划修改。
```

**Expected files, states, and artifacts:**

- Evaluation artifacts record independent inputs read by the evaluator, not only Nexus's final summary.
- Evaluation output contains at least: passed requirements, failed requirements, missing files/workflows, false assumptions, and user-visible consequences.
- A modification request artifact exists and is linked to the original intent and failed requirement IDs.
- Nexus continuation turns the evaluator feedback into a new implementation or project-doc update plan.
- Board or orchestrator state shows phase transitions such as evaluating, feedback_ready, modifying, retesting, or blocked_with_reason.

**Forbidden pass conditions:**

- Self-review that only says "looks good" without simulating user inputs.
- Feedback that is not tied to original requirements.
- Modification plan that changes only generic wording while leaving failed workflows unimplemented.
- Treating Verix as a name in a prompt without producing evaluator artifacts.

**Failure判定:**

- Fail if the evaluator did not read the original requirement directly.
- Fail if failed requirements cannot be traced into a concrete Nexus modification request.
- Fail if the loop ends after evaluation and never creates a next `$nexus-workflow ...` prompt for modification or retest.

## Test 08: Sample-External Variant Battery

**Purpose:** Verify that Nexus generalizes beyond the hand-built samples and does not pass by copying `feeler` or `probe`.

Run the following initialization prompts in separate fresh project directories under `{E2E_ROOT}/projects/variants/`.

**Variant A instruction:**

```text
$nexus-workflow 从零初始化一个项目：我要管理一个博物馆展览筹备工作区，包含策展主题、藏品来源、版权/授权状态、展陈空间限制、供应商任务、开幕前检查清单和观众反馈复盘。要求有项目意图记录、材料来源记录、工作流、验证脚本和同步策略。父目录 {E2E_ROOT}/projects/variants。
```

**Variant B instruction:**

```text
$nexus-workflow 从零初始化一个项目：我要管理一个开源维护者值班工作区，包含 issue 分级、release blocker、回归测试证据、维护者交接、公共 README 发布检查和事故恢复记录。要求能区分 private 维护记录和 public 发布材料。父目录 {E2E_ROOT}/projects/variants。
```

**Variant C instruction:**

```text
$nexus-workflow 从零初始化一个项目：我要管理一个语言学习课程迭代工作区，包含学生初始诊断、每周练习计划、错题/表达问题、课程材料版本、模拟测评反馈和下一轮课程调整。要求生成面向教师使用的 workflow 和验证脚本。父目录 {E2E_ROOT}/projects/variants。
```

**Expected files, states, and artifacts:**

- Each project has domain-specific requirement mapping, workflows, validation checks, and operation-guide sections.
- The three generated structures share only Nexus governance surfaces; domain files and workflow names differ by project.
- Public/private sync expectations are handled differently where the prompt requires it, especially Variant B.
- Each project records original and normalized intent independently and does not reuse another variant's naming or content.

**Forbidden pass conditions:**

- Copying `feeler` resume/interview structure, `probe` wording, or a single generic template.
- Producing nearly identical project-overview/workflow content across all three variants.
- Hardcoding pass rules to these three variants rather than deriving requirements from the prompt.
- Ignoring public/private split in Variant B.

**Failure判定:**

- Fail if evaluator similarity review finds mostly identical domain content across variants.
- Fail if any variant lacks at least three project-specific workflows or checks.
- Fail if generated files cannot be traced to the variant's own raw prompt.

## Test 09: Orchestrator Monitoring Surface

**Purpose:** Verify that a monitor agent can query current state without manually reading every run artifact and can safely interrupt or guide the loop.

**Initial instruction during or after any running test above:**

```text
$nexus-workflow 查看项目 {PROJECT_PATH} 的当前构建/测试/修改状态：告诉我当前阶段、关联 run_id、最近一次测试或评测结果、阻断原因、下一条可执行的 $nexus-workflow 指令，以及如果我要终止或人工介入应该使用什么提示。
```

**Expected files, states, and artifacts:**

- Output references the current project board and latest relevant run.
- It distinguishes build, sync, recovery, evaluation, modification, retest, completed, and blocked states.
- It provides a concrete next `$nexus-workflow ...` prompt for continue/recover/approve/retest/stop.
- It does not require the user to manually reconstruct state from raw artifact directories.

**Forbidden pass conditions:**

- Returning only a generic "running/completed" status.
- Omitting run ID or next prompt.
- Hiding manual action requirements such as GitHub/Feishu login/setup.
- Claiming automatic continuation when only manual user action remains.

**Failure判定:**

- Fail if the monitor cannot answer which phase the project is in.
- Fail if there is no safe stop/intervention path.
- Fail if the monitor state contradicts `interaction.json`, `state.json`, or `.nexus/board.json`.

## Test 10: Anti-Hardcoding Regression Check

**Purpose:** Verify that fixes made after these tests improve Nexus's general capability rather than adding special cases for this test document.

**Instruction after a modification agent changes Nexus:**

```text
$nexus-workflow 重新运行端到端能力验收，但换一个全新项目意图：我要管理一个跨校机器人比赛备赛工作区，包含规则阅读、零件采购、机械/电控/软件任务、测试场次、故障复盘、参赛材料提交和赛后总结。不要复用前面任何样例的领域词；只复用通用能力要求：初始意图记录、材料读取记录、工作流、验证、同步、恢复和评测反馈闭环。父目录 {E2E_ROOT}/projects/regression。
```

**Expected files, states, and artifacts:**

- Generated project uses robotics competition concepts, not grant, incident, museum, open-source, language-learning, feeler, or probe concepts.
- Intent mapping, source/material record, validation, sync, recovery, and evaluation expectations still appear.
- Any modification made to Nexus is justified as a general mechanism, not a project-name conditional.

**Forbidden pass conditions:**

- Conditional code or behavior keyed to sample names from this document.
- Passing by exact text matching rather than requirement extraction.
- Producing sample-domain artifacts in the robotics project.

**Failure判定:**

- Fail if generated content contains unexplained sample-domain terms.
- Fail if a reviewer cannot point to the general Nexus mechanism that enabled the pass.
- Fail if the test passes only when project names match earlier variants.
