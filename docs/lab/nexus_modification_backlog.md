# Nexus 通用修改 Backlog

日期：2026-06-17

边界：本文记录后续通用修改 backlog，不代表 Nexus 核心代码、测试、配置已经修改。当前 lab 层已经补齐执行、评测、skill replay、修改规划、monitor 状态和构建检查入口；核心代码修改仍必须依据 `modification_request.json` 和 `modification_plan.json` 执行。

只读分析依据：已检查 `docs/lab/sample_project_baseline.md`、`docs/lab/nexus_e2e_instruction_tests.md`、`docs/lab/nexus_agent_loop_entrypoints.md`、`docs/lab/agent_loop_monitor.md`，以及 `nexus/cli.py`、`nexus/runner.py`、`nexus/project_docs.py`、`nexus/interaction.py`、`nexus/recovery.py`、`nexus/board.py`、`nexus/artifacts/run_store.py`、`nexus/tools/search_service.py` 的当前结构。

通用边界：所有修改都必须从当前用户输入、当前项目路径、当前 run artifacts 和通用 schema 推导行为，不得按 `feeler`、`probe`、样例项目名、固定目录名或固定测试文案写特殊分支。

外部副作用规则：GitHub public push、Feishu setup/publish/autosync、需要登录或授权的外部动作都不能由 lab loop 自动执行；Nexus 核心只能产生 `pending_actions`、`continuation`、明确 blocked reason 和下一条 `$nexus-workflow ...` 指令，必须由 monitor 或用户确认后再运行。

问题矩阵优先规则：后续修改先读取 `scripts/lab/nexus_problem_matrix.json` 和每轮 `modification_request.json`，按 16 个问题轴的跨 case 失败排序，再修改 Nexus 通用入口。不能因为某个 case 最醒目就只修那个 case，也不能把 `probe`、`feeler`、`wljob` 等项目名写成特殊分支。

## 1. 初始意图没有变成可用工作区

- 用户可见问题：用户给出完整项目目标后，只能看到少量治理文档，不能直接得到带领域 workflow、数据结构、校验入口和需求追踪的可用工作区。
- 通用修改入口：优先检查 `nexus/runner.py:Runner.init_project()`、`Runner.approve(..., "project-root")`、`nexus/project_docs.py:write_project_docs()` 和 `.nexus/project-intent.json` 的生成契约，必要时新增通用 project workspace generation 步骤，但不得按项目名或样例类型分支。
- 失败证据：来自 E2E Test 01、Test 02、Test 08、Test 10；读取 `<NEXUS_ARTIFACT_PATH>`、`state.json`、`interaction.json`、`approvals/project_root_required.json`、`tool_results/project_docs_bundle.json`、目标项目 `docs/intent/*`、`.nexus/project-intent.json`、`workflows/`、`schemas/` 和 `scripts/validate_project.py`。
- 最小安全修改顺序：先改 lab harness/evaluator 计算原始需求到产物的 trace coverage，再根据失败报告最小改 core init/project docs；不要一开始重写 `Runner.init_project()` 主流程。
- 外部副作用边界：初始化质量测试不得自动触发 GitHub public 或 Feishu 发布，若 core 认为需要同步，只能留下确认卡或 blocked prompt。

## 2. 历史材料读取没有形成计划和索引

- 用户可见问题：用户指定历史文件、目录或对话材料后，看不到 Nexus 先计划读取什么、实际读了什么、抽取了什么、跳过了什么。
- 通用修改入口：优先检查 `nexus/project_docs.py:_referenced_intent_source_path()` 的单源读取能力、`Runner.init_project()`、`Runner.conversation_from_file()`、`conversation_manager_*()`、`Runner._run_search_loop()` 和 `SearchService.execute_round()`，把材料读取抽成通用 source bundle plan/log，而不是在某类项目里硬编码。
- 失败证据：来自 E2E Test 03；读取目标项目 `docs/intent/source-material-index.md` 或等价索引、run artifact `reports/source_reading_plan.md`、`reports/source_reading_log.md`、`tool_results/source_status.json`、`search_rounds/round_*/source_status.json` 和 `interaction.json` 的 artifact refs。
- 最小安全修改顺序：先改 lab harness 创建多文件 fixture 并让 evaluator 检查索引完整性，再改 core 增加通用材料计划/读取/摘录/跳过原因记录。
- 外部副作用边界：本项默认只读本地材料；若读取外部网页或在线文档，必须沿用 online-search approval，不得绕过确认。

## 3. 检索记录存在但不能被用户审阅

- 用户可见问题：Nexus 可能写了零散 search JSON/JSONL，但用户和评测器仍难以看出检索目标、命中结果、覆盖缺口和停止理由。
- 通用修改入口：优先检查 `Runner._run_search_loop()`、`SearchService.execute_round()`、`Runner._write_report()`、`Runner._write_next_options()` 和 `nexus/tools/search_adapters.py` 的 source status 输出，新增统一 `reports/search_trace_summary.*` 或等价 artifact 时要覆盖所有 research/search 入口。
- 失败证据：来自 E2E Test 03 以及涉及调研的后续变体；读取 `search_rounds/round_*/search_plan.json`、`candidates.jsonl`、`source_status.json`、`coverage_review.json`、`stop_decision.json`、`reports/final_report.md` 和 `reports/next_options.json`。
- 最小安全修改顺序：先让 lab evaluator 直接读现有 search artifacts 并定义缺什么，再改 core 聚合成人读 summary；不要先改变搜索 adapter 行为。
- 外部副作用边界：在线检索仍必须受 approval 控制，summary 生成不能隐式打开新的 online search。

## 4. 同步链路没有统一成可确认的闭环

- 用户可见问题：用户难以确认一次更新是否完成 GitHub private、Feishu 写回、Feishu 后 private 再同步，以及 public 发布是否真的经过 staging 和验证。
- 通用修改入口：优先检查 `Runner._post_change_autosync()`、`Runner._self_sync_flow()`、`Runner._github_auto_private_flow()`、`Runner._github_public_flow()`、`Runner.operation_guide()`、`Runner.github_sync_public()`、`nexus/github_sync.py` 和 `nexus/feishu_autosync.py`，把不同入口对齐到同一同步状态和 artifact 契约。
- 失败证据：来自 E2E Test 04；读取 `tool_results/self_sync_operation_guide.json`、`self_sync_feishu_sync.json`、`self_sync_post_feishu_github_private.json`、`post_change_github_auto_private.json`、`post_change_feishu_autosync.json`、`github_public_staging.json`、`github_public_validation.json`、`github_public_fresh_clone_validation.json`、`github_public_sync.json` 和 `recovery/continuation.json`。
- 最小安全修改顺序：先改 lab harness 只观测 artifacts 和 blocked prompts，再改 core sync wrappers；不要为了让测试通过而新增平行 sync 命令。
- 外部副作用边界：GitHub public push 必须保留 `--confirm` 或等价 monitor/用户确认，Feishu 发布或写回必须在配置存在且 monitor/用户确认后运行。

## 5. 外部 Codex 修复没有强制同 run 回跳

- 用户可见问题：外部 Codex 修复失败或需要本地 debug 时，用户无法保证诊断、修改、测试和恢复会回到原 `run_id`，而不是变成一条旁路。
- 通用修改入口：优先检查 `Runner.execute_code_change()`、`_code_change_prompt()`、`Runner.handoff_for_debug()`、`Runner.append_debug_worklog()`、`Runner.rebind_and_continue()`、`Runner._finish_debug_rebind()` 和 `write_interaction()`，把 `codex_exec_failed`、dirty blocker、boundary blocker 等转成可恢复状态。
- 失败证据：来自 E2E Test 05；读取 `code_change/codex_prompt.md`、`code_change/codex_stderr.txt`、`handoffs/debug_handoff.json`、`handoffs/debug_session.json`、`worklogs/debug_worklog.json`、`worklogs/debug_summary.json`、`rebind/rebind_result.json`、`state.json` 和 `interaction.json`。
- 最小安全修改顺序：先改 lab harness 制造需要 debug 的失败并验证没有 handoff 就不能修，再改 core runner 的失败出口和 rebind 证据检查。
- 外部副作用边界：调用外部 Codex、打开登录流程或执行宿主命令都必须通过 pending action 或用户确认；lab 不能自动替用户完成这些动作。

## 6. Recovery playbook 没有覆盖所有恢复入口

- 用户可见问题：项目里即使已有恢复经验，新的相似失败仍可能直接进入外部 Codex 或新恢复思路，看不到先查 playbook 的证据。
- 通用修改入口：优先检查 `nexus/recovery.py:match_recovery_playbook()`、`related_recovery_experience()`、`build_recovery_context()`、`write_recovery_artifacts()`，以及 `Runner._recovery_guidance()`、`_write_recovery_playbook_approval_if_completed()`、`_write_debug_recovery_result_if_possible()` 和 `Runner.execute_code_change()` 的调用覆盖。
- 失败证据：来自 E2E Test 06；读取目标项目 `.nexus/recovery-playbook.json`、`docs/recovery-records.md`、run artifact `tool_results/related_recovery_experience.json`、`tool_results/recovery_context.json`、`tool_results/recovery_result.json`、`approvals/recovery_playbook_write_required.json` 和第二次失败 run 的 `interaction.json`。
- 最小安全修改顺序：先改 lab evaluator 判定每个恢复入口是否有 playbook lookup artifact，再补 core call site；不要另建新的恢复存储。
- 外部副作用边界：playbook 查询和写入是本地项目状态，若恢复建议包含 GitHub/Feishu/Codex 外部动作，只能输出待确认 action。

## 7. Verix 式评测反馈还没有驱动修改

- 用户可见问题：独立评测即使发现项目不满足初始意图，也没有稳定产物把失败点变成 Nexus 下一轮修改计划。
- 通用修改入口：优先在 lab loop/harness 层建立 `evaluation.json`、`modification_request.json`、`retest_result.json` 契约；core 层后续再考虑 `nexus/cli.py` 的 `plan-implementation` 路径、`Runner.approve(..., "implementation-plan")`、`Runner.run_tests()`、`Runner.continue_run()` 和 `nexus/board.py:update_board()` 的通用反馈接入。
- 失败证据：来自 E2E Test 07；读取 evaluator 实际读过的 input refs、目标项目 `docs/intent/original-requirement.md`、`docs/intent/normalized-requirement.md`、source/material index、验证脚本、评测 verdict、失败 requirement IDs、modification request 和 `.nexus/board.json`。
- 最小安全修改顺序：先改 lab evaluator 和 harness，证明评测反馈格式能独立定位失败，再把稳定的 modification request 接到 core planning；不要把评测逻辑直接塞进 `init_project()`。
- 外部副作用边界：评测和反馈生成必须只读项目产物，触发修改、同步、public 或 Feishu 发布前必须回到 monitor/用户确认。

## 8. Monitor 状态不能概括整条闭环

- 用户可见问题：用户问当前构建、评测、修改、同步或恢复状态时，Nexus 只能返回单个 run 或简化 board，不能说明整条闭环在哪一步、下一步做什么、怎么停止或人工介入。
- 通用修改入口：优先检查 `Runner.status()`、`Runner.board_show()`、`Runner.board_update()`、`Runner.board_point()`、`Runner._write_state()`、`Runner._write_active_project()`、`write_interaction()` 和 `RunStore`，lab 层先维护 loop/case/iteration 状态索引，再决定是否扩展 core status schema。
- 失败证据：来自 E2E Test 09，并可交叉读取任意 case 的 `<NEXUS_ARTIFACT_PATH>`、`interaction.json`、`audit.json`、目标项目 `.nexus/board.json`、lab `execution.json`、`evaluation.json`、`modification_request.json` 和 `retest_result.json`。
- 最小安全修改顺序：先改 lab loop/harness 的 monitor index 和只读状态汇总，再改 core `status/board` 输出；不要先把全局 orchestrator 状态写进每个 runner 分支。
- 外部副作用边界：monitor 只能展示和请求确认，不能自动执行 GitHub public、Feishu 或外部 Codex 恢复动作。
