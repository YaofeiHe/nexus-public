# Nexus Agent Loop Entrypoints Audit

日期：2026-06-17

范围：只读审计 Nexus 核心代码结构，定位“样例项目 -> 端到端测试 -> 评测反馈 -> 修改 Nexus -> 再测试”自动闭环可以接入的位置和核心缺口。本文档审计的是 Nexus 核心入口；lab 层的外部 harness 已在 `scripts/lab/` 中构建。

## 结论

Nexus 现在已经有很多可复用入口：`nexus/cli.py` 暴露 `$nexus-workflow` 对应的 CLI，`nexus/runner.py` 持有 research、project init、code-change、test、recovery、sync、conversation 等主流程，`<NEXUS_ARTIFACT_PATH>` 已经保存 `input.json`、`state.json`、`interaction.json` 和各类 artifacts。

核心缺口不是“没有入口”，而是这些入口还没有被 Nexus 核心自己的自动化外层循环强制串起来。lab 层已经用 `run_nexus_lab_loop.py`、`evaluate_nexus_case.py`、`run_nexus_skill_replay.py` 和 `plan_nexus_modification.py` 建立了外部闭环入口；核心代码仍存在这些待修点：`init_project()` 只保存和治理项目意图，不会默认调研和构建领域工作区；`execute_code_change()` 调外部 Codex 时没有默认带入 recovery playbook 和 debug/recovery 历史；`run_tests()` 只跑命令和 post-change sync，不会评测项目产物是否符合初始意图；`conversation_to_workflow()` 和 research/search 是独立链路，没有成为项目初始化的必经材料读取步骤。

## 可复用入口和主要 artifacts

| surface | 当前入口 | 可复用能力 | 主要 artifacts | 当前缺口 |
|---|---|---|---|---|
| CLI / skill | `skills/nexus-workflow/SKILL.md`；`nexus/cli.py:build_parser()`；`nexus/cli.py:main()`；`nexus/user_prompts.py:normalize_next_prompt()` | 可以把 `$nexus-workflow ...` 转成本地 `python -m nexus.cli --root ... --next-prompt-mode workflow --workflow-surface codex ...` 调用；CLI 已有 `run/research/init-project/continue/recover/handoff/rebind/test/github-sync/self-sync/supplement-init/conversation-*` | CLI stdout；`<NEXUS_ARTIFACT_PATH>`；`<NEXUS_ARTIFACT_PATH>` | `Runner.invoke()` 只覆盖部分自然语言路由，不是完整的 `$skill` 解释器；测试执行 agent 应直接调用 CLI 子命令，避免自然语言路由漏掉 init/build/test case。 |
| runner | `nexus/runner.py:Runner.run()`；`resume()`；`continue_run()`；`approve()`；`approve_and_continue()` | 已有 run_id、checkpoint、state、interaction、approval gate、pending_actions 和 continuation 结构 | `<NEXUS_ARTIFACT_PATH>`；`state.json`；`interaction.json`；`audit.json`；`approvals/*` | 这些状态是单 run 粒度，还没有上层 loop 状态，例如 sample_case_id、test_case_id、eval_result_id、fix_iteration、orchestrator_status。 |
| project init | `Runner.init_project()`；`Runner.approve(..., "project-root")`；`nexus/project_docs.py:write_project_docs()` | 保存 `raw_user_request`、`normalized_request`，审批后创建目录，写 `docs/intent/original-requirement.md`、`docs/intent/normalized-requirement.md`、`docs/project-overview.md`、`.nexus/project-intent.json`，并触发 GitHub bootstrap、首次 public sync、Feishu autosync | `<NEXUS_ARTIFACT_PATH>`；`tool_results/project_docs_bundle.json`；目标项目 `docs/intent/*`、`.nexus/project-intent.json` | 它不会默认生成领域结构、workflow、schema、validator、历史材料索引；`write_project_docs()` 最多摘录一个显式引用源文件，不能覆盖多个历史文件和项目目录材料。 |
| recovery | `Runner.recover()`；`handoff_for_debug()`；`append_debug_worklog()`；`rebind_and_continue()`；`_finish_debug_rebind()`；`nexus/recovery.py` | 已有同 run 恢复、debug handoff、worklog、rebind、`recovery_result.json`、`recovery_playbook_write_required.json` 和项目级 `.nexus/recovery-playbook.json` 写入 | `handoffs/debug_handoff.json`；`worklogs/debug_worklog.json`；`worklogs/debug_summary.json`；`rebind/rebind_result.json`；`tool_results/recovery_context.json`；`tool_results/recovery_result.json`；`approvals/recovery_playbook_write_required.json` | 只有进入这些恢复入口时才沉淀 recovery；`execute_code_change()` 调外部 Codex 失败时直接写 `code_change/codex_stderr.txt` 并返回 failed，没有自动先查 playbook 或形成恢复计划。 |
| project docs | `nexus/project_docs.py:write_project_docs()`；`load_project_doc_targets()` | 能补齐和保留 Nexus 管理文档，不覆盖已有完整文档；Feishu sync targets 可读取 `.nexus/project-intent.json` | 目标项目 `docs/intent/original-requirement.md`、`docs/intent/normalized-requirement.md`、`docs/project-overview.md`、`README.md`、`.nexus/project-intent.json` | 文档模板偏治理和同步说明，不会自动产出“项目应该实现什么”的细化功能清单、数据模型、用户流程、验收脚本。 |
| research | `Runner.run()`；`_execute_workflow()`；`continue_run()`；`nexus/research_contract.py:build_research_contract()` | 已有 repo scan、intent route、task block、research contract、branch reports、decision matrix、next_options | `tool_results/repo_scan.json`；`reports/intent_route.json`；`reports/research_contract.{json,md}`；`reports/branch_*.md`；`reports/decision_matrix.md`；`reports/final_report.md`；`reports/next_options.json` | research 不是 init-project 的必经步骤；如果用户给的是“初始化项目并按历史构建”，现在不会自动先读所有指定历史和样例，再把结果写回 project intent。 |
| search | `Runner._run_search_loop()`；`nexus/tools/search_service.py:SearchService.execute_round()`；`nexus/tools/search.py` | 每轮会生成 search plan、执行 local/online adapter、保存 candidates/source_status/coverage/stop decision；online source 有审批 | `search_rounds/round_<n>/search_plan.json`；`candidates.jsonl`；`source_status.json`；`coverage_review.json`；`stop_decision.json`；`tool_results/source_status.json`；`reports/external_gpt_research_prompt.md` | 搜索记录存在，但没有默认汇总成人能直接审阅的“检索规划和检索记录报告”；评测 agent 需要自己读取多个 JSON/JSONL 文件。 |
| conversation | `Runner.conversation_to_workflow()`；`conversation_from_file()`；`conversation_manager_init/ingest/promote()`；`nexus/conversation_manager.py` | 能导入 Codex session 或 transcript，脱敏，选取消息，生成 workflow/skill/prompt 产物，项目内可维护 `docs/ai-conversations/*` | `conversation/session_manifest.json`；`conversation/source_selection.json`；`conversation/redacted_messages.jsonl`；`reports/generalized_workflow.md`；`drafts/SKILL.md`；项目 `docs/ai-conversations/*` | 这是单独入口，不会被 `init_project()` 自动调用；`choose_session()` 主要按 session metadata 匹配，无法保证读到用户想要的历史段；当前 UI 对话不可稳定直接读取时会要求导出文件。 |
| sync | `Runner._github_bootstrap_flow()`；`_github_auto_private_flow()`；`_github_public_flow()`；`_post_change_autosync()`；`_self_sync_flow()`；`supplemental_init()`；`nexus/github_sync.py`；`nexus/feishu_autosync.py` | GitHub private/bootstrap/auto-private/public、public staging/validation/fresh clone、Feishu autosync、GitHub auth/EOF continuation 已有较完整链路 | `tool_results/github_bootstrap.json`；`github_auto_private_sync.json`；`github_public_staging.json`；`github_public_validation.json`；`github_public_fresh_clone_validation.json`；`github_public_sync.json`；`post_change_*`；`self_sync_*`；`recovery/continuation.json` | 统一链路基础已存在，但不是所有入口都走完整闭环。`operation_guide()` 只生成/同步指南，不自动 GitHub private；direct `github-sync public` 独立于 self-sync；测试 agent 必须验证每个被测入口实际产生了预期 sync artifacts。 |
| test | `Runner.run_tests()`；`tests/test_runner.py`；`tests/test_board_and_init.py`；`tests/test_manager_github_showcase_feishu.py`；`tests/test_search.py`；`tests/test_conversation_and_skill.py` | 能对单个 run 执行测试命令，成功后走 `_post_change_autosync()`；已有单元测试覆盖 init public sync、debug rebind、recovery playbook、search、conversation、sync | `<NEXUS_ARTIFACT_PATH>`；`tests/stdout.txt`；`tests/stderr.txt`；post-change sync artifacts | `run_tests()` 不知道“样例项目质量”和“初始意图”；`$nexus-workflow` 指令集级别测试和 verdict artifact 已在 lab 层实现，但还没有进入 Nexus 核心 runner。 |

## 现在会绕开的约束

1. 初始意图绕开点：`Runner.init_project()` 把 `idea/raw_user_request/normalized_request` 写入 `input.json`，审批后 `write_project_docs()` 写目标项目文档，但 `Runner.run()` 的 research/search、`conversation_to_workflow()` 的历史导入、`conversation_manager_ingest()` 的项目内历史沉淀都不是 init 的默认步骤。

2. 检索记录绕开点：`Runner._run_search_loop()` 会保存每轮 `search_plan.json`、`candidates.jsonl`、`source_status.json`、`coverage_review.json`、`stop_decision.json`，但 CLI 输出仍以 `interaction.json` 的状态和 artifact refs 为主。没有一个固定 `reports/search_log.md` 或 `reports/search_trace_summary.json` 给评测 agent 直接判断“查了什么、没查什么、为什么停”。

3. recovery playbook 绕开点：`Runner.recover()`、`_recovery_guidance()`、debug rebind 会复用 `nexus/recovery.py`，但 `Runner.execute_code_change()` 直接组装 `_code_change_prompt()` 调 `codex exec`，失败时返回 `codex_exec_failed`；这里没有调用 `build_recovery_context()`、`match_recovery_playbook()`、`related_recovery_experience()` 或 `write_recovery_artifacts()`。

4. 统一同步链绕开点：`Runner.run_tests()` 成功后走 `_post_change_autosync()`，`self_sync()` 走 `_self_sync_flow()`，`supplemental_init()` 走 `_post_change_autosync()`，init project 审批后走 bootstrap + public + Feishu。`guide generate/publish-feishu` 和直接 `github-sync private/public` 是较窄入口，不能代表完整 post-change 或 self-sync 闭环。

5. 样例项目硬编码风险：当前没有测试约束阻止修改 agent 为 feeler/probe 这类样例写特殊判断。要避免“只会样例”，评测必须使用样例外变体，并检查修改是否落到 `init_project`、research/search、recovery、sync 等通用入口，而不是按项目名或固定目录模板分支。

## 后续 agent 应该怎样接入

### 执行 agent

职责：在隔离 workspace 中真实输入 `$nexus-workflow` 指令对应的 Nexus CLI，保存每个 case 的 run_id、stdout、生成文件和 artifact 清单。

建议调用入口：

- 项目初始化：`python -m nexus.cli --root <nexus-root> --next-prompt-mode workflow --workflow-surface codex init-project "<idea>" --parent <sandbox-parent> --raw-user-request "<raw>" --normalized-request "<normalized>"`
- 审批项目目录：`approve-and-continue <run_id> project-root`，不是只 `approve` 后停住。
- 调研和检索：`research "<idea>" --project-root <target-project> --provider auto --approval-policy ask --approve-online-search`，再按 `interaction.json` 决定 `continue/approve-and-continue/resume`。
- 对话材料：`conversation-manager init/ingest/promote --project-path <target-project>`，或 `conversation-to-workflow --file <history.md> --selector <selector>`。
- 修改链：`plan-implementation <run_id>`、`approve-and-continue <run_id> code-change`、`execute-code-change <run_id>`、`diff`、`approve-and-continue <run_id> apply`、`test <run_id> --cmd "<cmd>"`。
- 恢复链：遇到 `pending_actions`、`continuation`、`recovery_mode=true` 时优先执行 `continue-after-input`、`recover`、`handoff-for-debug`、`append-debug-worklog`、`rebind-and-continue`，保持原 run_id。
- 同步链：优先用 `self-sync` 或触发 `_post_change_autosync()` 的 `test` 成功路径；public 发布测试用 `github-sync public --confirm`，并读取 public staging/validation/fresh clone artifacts。

必须读取 artifacts：

- 每个 run：`input.json`、`state.json`、`interaction.json`、`audit.json`
- init：`approvals/project_root_required.json`、`tool_results/project_docs_bundle.json`、目标项目 `docs/intent/*`、`.nexus/project-intent.json`
- research/search：`reports/research_contract.{json,md}`、`search_rounds/round_*/search_plan.json`、`search_rounds/round_*/source_status.json`、`reports/final_report.md`、`reports/next_options.json`
- recovery：`handoffs/debug_handoff.json`、`worklogs/debug_worklog.json`、`rebind/rebind_result.json`、`tool_results/recovery_result.json`、`approvals/recovery_playbook_write_required.json`
- sync：`tool_results/github_*`、`tool_results/post_change_*`、`tool_results/self_sync_*`、`recovery/continuation.json`
- test：`tests/test_run_1.json`、`tests/stdout.txt`、`tests/stderr.txt`

### 评测 agent

职责：独立读取测试 case 的初始意图、样例项目通用期望、Nexus run artifacts 和目标项目产物，给出通过/失败/需要修改的结构化 verdict。

建议读取入口：

- case 输入：执行 agent 保存的 `$nexus-workflow` 指令、raw intent、normalized intent、expected_behavior。
- 目标项目：`docs/intent/original-requirement.md`、`docs/intent/normalized-requirement.md`、`docs/project-overview.md`、`.nexus/project-intent.json`、`README.md`、项目自己的 workflow/schema/test/validator 文件。
- Nexus 证据：`<NEXUS_ARTIFACT_PATH>`、`search_rounds/round_*/source_status.json`、`reports/final_report.md`、`reports/next_options.json`、`interaction.json`。
- 同步证据：`github_public_staging.json`、`github_public_validation.json`、`github_public_fresh_clone_validation.json`、`github_public_sync.json`、`post_change_github_auto_private.json`、`self_sync_feishu_sync.json`。
- 恢复证据：`recovery_result.json`、`debug_summary.json`、目标项目 `.nexus/recovery-playbook.json`、`docs/recovery-records.md`。

评测输出建议固定为：

- `case_id`
- `run_id`
- `verdict`: `pass` / `fail` / `blocked`
- `failed_surface`: `init_project` / `research` / `search` / `conversation` / `sync` / `recovery` / `test` / `code_change`
- `expected`
- `observed`
- `evidence_refs`
- `generalization_check`: 是否只对样例项目有效
- `required_nexus_change`: 要改 Nexus 的哪个通用入口或函数

### 修改 agent

职责：只根据评测 agent 的失败报告修改 Nexus 通用能力，不能按样例项目名称、固定目录名或固定 prompt 写特殊逻辑。

建议修改入口对应关系：

- 初始化理解差：优先看 `Runner.init_project()`、`Runner.approve(..., "project-root")`、`write_project_docs()`，以及是否需要把 conversation/search 作为 init 的可选或默认前置。
- 检索记录缺失：优先看 `Runner._run_search_loop()`、`SearchService.execute_round()`、`_write_report()`、`_write_next_options()`，新增统一 search trace artifact 时应覆盖所有 research 入口。
- recovery-first 缺失：优先看 `Runner.execute_code_change()`、`Runner.recover()`、`_recovery_guidance()`、`_write_debug_recovery_result_if_possible()`，不得另建平行 recovery 存储。
- 同步链不一致：优先看 `_post_change_autosync()`、`_self_sync_flow()`、`_github_auto_private_flow()`、`_github_public_flow()`、`supplemental_init()`，不要复制一条新 sync 链。
- 评测闭环缺失：应新增测试 harness 或 lab runner，而不是把评测逻辑塞进 `init_project()` 里；Nexus 核心应产出足够 artifacts，评测 agent 根据 artifacts 判定。

修改后至少跑对应窄测试，不跑长全量测试时必须说明。例如：

- recovery/debug：`tests/test_runner.py` 中 rebind/recovery 相关用例。
- init/project docs：`tests/test_board_and_init.py` 中 init/project_docs 用例。
- sync：`tests/test_manager_github_showcase_feishu.py` 中 self_sync/post_change/github_public 用例。
- search/research：`tests/test_runner.py` 中 online/search/build_decision 用例和 `tests/test_search.py`。
- conversation：`tests/test_conversation_and_skill.py` 和 `tests/test_manager_github_showcase_feishu.py::test_conversation_manager_init_and_ingest`。

## 自动闭环最小接入方案

1. 新增 lab 层，不先改 runner 主流程：创建一个独立 runner 负责 case registry、执行记录、评测结果、修改轮次和监控状态。它调用现有 CLI，不直接绕过 `nexus/cli.py`。

2. 样例项目只提供通用验收规则：从样例抽取“必须保存初始意图、必须生成需求理解、必须记录检索、必须有 workflow/validator、同步和恢复必须有 artifacts”等规则，但测试集必须包含样例外变体。

3. 执行 agent 每轮只写 lab artifacts：例如 `docs/lab/runs/<loop_id>/cases/<case_id>/execution.json`、`evaluation.json`、`modification_request.json`。Nexus 核心 run artifacts 仍保存在 `<NEXUS_ARTIFACT_PATH>`。

4. 评测 agent 不信 CLI 状态摘要：必须读 `state.json`、`interaction.json` 和具体 artifacts，再判断是否符合初始意图和同步/恢复要求。

5. 修改 agent 改通用入口：每个失败都要映射到具体函数，例如 `write_project_docs()`、`_run_search_loop()`、`execute_code_change()`、`_post_change_autosync()`，并用样例外变体回归，避免对某个样例项目写死。
