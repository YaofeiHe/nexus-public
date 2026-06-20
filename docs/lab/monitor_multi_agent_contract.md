# Nexus-Verix Monitor Multi-Agent Contract

日期：2026-06-19

## Start Trigger

`start_trigger`: 用户在当前 Codex 对话窗口输入 `启动运行`，且上下文目标是 Nexus-Verix 自修复实验系统。

## Execution Model

`execution_model`: `monitor_multi_agent_v1`

该执行模型只能由当前 Codex monitor 通过 `multi_agent_v1.spawn_agent` 实现。仓库内 Python 脚本可以初始化状态、生成 task prompt、运行 lab harness、记录 artifacts，但不能自行创建真实 subagent。

## Project-Building Modes

系统必须同时支持两种项目构建方式，不能只实现其中一种：

1. `step_by_step_skill_workflow`：用户在当前 monitor 对话窗口按 workflow 逐条输入 `$nexus-workflow ...` 指令，Nexus skill 层、Outer Codex recovery 协议、approval / `continue-after-input` / debug rebind 必须按真实交互链路执行和记录。
2. `default_autonomous_multi_agent_build`：用户只提供项目意图，monitor 将原始意图分发给 Nexus execution、Skill Replay、Verix audit、Modification、State Audit agent，默认多轮运行“构建、测试、审计、修改、回归”，直到验收通过、显式停止或真实阻断。

其中 `$nexus-workflow 调研` 不能只因为某个 replay 到达 `online_search_approval_required` 就被判定全面可用；必须继续同一 run 的在线检索审批并评估最终报告质量，才能声称该场景通过。

## Forbidden Equivalent

`forbidden_equivalent`: 不得把以下入口等同于完整的 `启动运行`：

```text
python -m nexus.cli --root <PROJECT_ROOT> nexus-verix-loop --execute
python3 scripts/lab/run_nexus_verix_orchestrator.py --execute
```

这些入口的执行模型是 `orchestrated_subprocess_loop`，只能作为某个 subagent 使用的工具或 fallback evidence，不能满足 monitor-level multi-agent acceptance。

## Required Agents

启动后必须有 5 个真实 monitor-spawned agent：

1. `nexus_execution`
2. `skill_replay`
3. `verix_audit`
4. `nexus_modification`
5. `state_audit`

每个 agent 必须在 `monitor_state.json` 中记录：

- `multi_agent_v1_id`
- `status`
- `task_prompt_path`
- `output_dir`
- `last_update`
- `blocker`

## Monitor Responsibilities

当前 Codex 对话窗口负责：

1. 运行 `scripts/lab/init_monitor_multi_agent_run.py --init-run`。
2. 读取 `launch_packet.json` 和 `agent_tasks/*.md`。
3. 调用 `multi_agent_v1.spawn_agent` 创建真实 subagent。
4. 把返回的 agent id 写回 `monitor_state.json`。
5. 用 `wait_agent` / `send_input` 协调 agent。
6. 处理用户插入指令；除非用户明确说停止，否则插入指令只更新主流程，不终止主流程。
7. 遇到授权、登录、外部副作用、provider recovery、Nexus debug rebind 时，按照 Nexus/Verix skill 的恢复协议继续同一 run。
8. 写入 `events.jsonl`、`approval_queue.jsonl`、`handoffs/*.json`，保证状态可审计和可恢复。
9. 将用户新输入默认分类为“查询/补充/调整当前主流程”；只有明确包含停止、终止、强行停止等终止意图时，才写 `STOP` marker。
10. 如果 monitor 误写 `STOP` 或上下文恢复后丢失 subagent handle，必须在同一个 monitor run 中记录事故、归档误停文件、重新 spawn agent 或恢复 agent，而不是把主流程当作已完成。
11. 每个 agent 在长时间任务前必须先写 `heartbeat_status.json`；monitor 不能只靠等待 `wait_agent` 判断状态，必须把 artifacts merge 回 `monitor_state.json`。
12. Skill replay 的真实线程证据可以来自当前 monitor 可控的 `multi_agent_v1_thread`；此时必须记录 agent id、`send_input` 的 `submission_id`、`completion_id` 或 `status_id`、`final_status` 和完整响应，不能把普通 CLI/subprocess 输出伪装成真实线程 replay。

## Repo Runtime Role

Nexus 仓库负责提供：

- monitor contract/schema/roles；
- agent task prompt；
- lab case registry；
- skill replay plan；
- Verix audit evidence；
- modification request/plan；
- status inspector；
- regression tests。

Nexus 仓库不负责在 Python 内部 spawn 真实 Codex subagent。

## Acceptance

一个运行只有同时满足以下条件，才能声称完成 monitor-level multi-agent 启动：

1. `monitor_state.json.execution_model == "monitor_multi_agent_v1"`。
2. `monitor_state.json.spawn_source == "current_codex_conversation_multi_agent_v1"`。
3. 5 个 required agents 都存在。
4. 5 个 required agents 都有非空 `multi_agent_v1_id`。
5. `python_embeds_subagents == false`。
6. `main_flow.interruption_policy` 明确用户临时问题不终止主流程。
7. 若只存在 `orchestrator_state.json`，没有 `monitor_state.json` 和 `spawned_agents` 证据，则 verdict 必须是 `blocked` 或 `not_monitor_multi_agent`。
8. `operation_modes` 同时包含 `step_by_step_skill_workflow` 和 `default_autonomous_multi_agent_build`。
9. `reference1_unresolved_proof_gaps` 明确记录未证明项：wljob 受限 replay、`$nexus-workflow 调研` 全链路、在线检索审批后最终报告、16 轴强智能审查、多轮自动测判改、feeler 级意图理解。
10. `instruction_log` 中的普通用户补充要求不会导致 `status=stopped`；误停恢复必须留下 `false_stop_recovered` event。
11. 每个 agent 的 required artifacts 至少包含 `heartbeat_status.json`；`merge_monitor_agent_artifacts.py` 必须能把缺失产物、blocked 状态和下一步写回 `monitor_state.json`。
12. `run_nexus_skill_replay.py --record-turn` 接受 `multi_agent_v1_thread`，并要求非空 `thread_id`、`submission_id`、`completion_id` 或 `status_id`、`final_status` 后才满足真实 replay contract；旧的 `user_message_id` / `assistant_message_id` 只能作为兼容别名，不能作为契约字段名。
13. `prompt_turns.jsonl`、`turn.json`、`skill_replay_evaluation.json` 必须显式区分 `queue-only`、`codex_exec_subprocess`、`multi_agent_v1_thread`；只有 `multi_agent_v1_thread` 可以让 `satisfies_real_thread_replay_contract=true`。
14. Skill replay agent artifact 必须包含 `source_consumption`，至少列出 monitor 提供的 `issue_memory.json`、`completion_ledger.json`，以及 replay 使用的 problem matrix 和 wljob command file。
