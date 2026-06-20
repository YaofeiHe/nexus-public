# Nexus Skill Input Replay

日期：2026-06-18

本文档定义真实 Codex skill 输入 replay 层。它和 CLI harness 不是一回事：

- CLI harness：把 case 中的 `$nexus-workflow ...` prompt 转成 `python -m nexus.cli ...` 命令执行。
- Skill replay：把 case 中的 `$nexus-workflow ...` prompt 当作真实用户消息发送到一个隔离 Codex thread，再读取 assistant 回复并评测回复是否符合 skill 输出契约。

## 目标

Skill replay 用来测试用户实际看到的 Nexus skill 行为：

1. Codex 是否识别 `$nexus-workflow` 并使用真实 Nexus skill。
2. 回复是否包含可读的状态、输出、下一条 `$nexus-workflow` prompt。
3. 回复是否能证明调用了本地 Nexus CLI，而不是 mock、泛泛说明或只给运行状态。
4. 外部副作用 prompt 是否默认被拒绝或进入明确审批链。
5. 多条 skill 输入是否能被 monitor 串成可审计 replay run。

## 文件

- `scripts/lab/run_nexus_skill_replay.py`：生成 replay 计划、写入 send queue、记录 Codex thread 回复、评测 replay 结果。
- `replay_plan.json`：本次 replay 的所有候选 prompt。
- `send_queue.jsonl`：默认允许发送到 Codex thread 的 prompt；外部副作用 prompt 默认不进入队列。
- `prompt_turns.jsonl`：本次 replay 的统一流水账；初始化时写入 `queue-only` 行，`record-turn` 时追加真实记录行。
- `replay_state.json`：当前 replay 状态、已记录 turn、评测路径。
- `turns/<case-id>/<step-id>/turn.json`：一条真实 Codex thread 输入/回复记录。
- `skill_replay_evaluation.json`：整次 replay 的评测结果和修改建议。

## Execution Model 字段

Replay artifact 必须区分三种模型：

- `queue-only`：只证明 prompt 已进入计划或 gate；不等于真实 thread replay。
- `codex_exec_subprocess`：通过 `codex exec` 或本地 CLI 子进程跑出的回复；可作为调试证据，但不满足真实 thread replay contract。
- `multi_agent_v1_thread`：当前 monitor 可控的真实 multi-agent thread；只有这种模型可满足真实 skill replay contract。

`replay_plan.json` 和 `send_queue.jsonl` 保留 queue/gate 规划，行内 `replay_execution_model=queue-only`，同时标注 `target_replay_execution_model=multi_agent_v1_thread`。`turn.json` 记录实际来源，`skill_replay_evaluation.json` 汇总三类计数，不能把 CLI 子进程当成真实线程。

## 生成 Replay 队列

```bash
python3 scripts/lab/run_nexus_skill_replay.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --case-id real_07_wljob_heat_local_sequence \
  --e2e-root /tmp/nexus-skill-replay-lab \
  --init-run
```

脚本会输出：

```text
<e2e-root>/skill-replays/<timestamp>/replay_state.json
```

随后读取同目录的 `send_queue.jsonl`。每一行就是应发送到隔离 Codex thread 的真实 `$nexus-workflow ...` 消息。

## Monitor 执行方式

当前对话作为 monitor 时，执行流程是：

1. 用 Codex App fork 一个同目录隔离 thread。
2. 从 `send_queue.jsonl` 取一条 prompt。
3. 用 Codex App thread tool 把 prompt 发给隔离 thread。
4. 读取 assistant 回复。
5. 把 prompt 和回复保存到临时文件。
6. 调用 `run_nexus_skill_replay.py --record-turn` 记录结果；当前 monitor 若使用 `multi_agent_v1` 子对话执行 prompt，应记录 `record_source=multi_agent_v1_thread`，`thread_id=<agent_id>`，`submission_id=<send_input submission id>`，以及 `completion_id=<completion id>` 或 `status_id=<wait/status id>`，并记录 `final_status=<done/blocked/failed/...>`。
7. 全部 queued prompt 记录后调用 `--evaluate-run`。

## 记录一条回复

```bash
python3 scripts/lab/run_nexus_skill_replay.py \
  --record-turn \
  --replay-dir /tmp/nexus-skill-replay-lab/skill-replays/<timestamp> \
  --thread-id <codex-thread-id> \
  --record-source multi_agent_v1_thread \
  --submission-id <multi-agent-submission-id> \
  --completion-id <multi-agent-completion-id> \
  --final-status done \
  --case-id-record real_07_wljob_heat_local_sequence \
  --step-id check_skill \
  --prompt-file /tmp/prompt.txt \
  --response-file /tmp/response.txt
```

如果 monitor 只能得到 status 而不是 completion id，应使用：

```bash
python3 scripts/lab/run_nexus_skill_replay.py \
  --record-turn \
  --replay-dir /tmp/nexus-skill-replay-lab/skill-replays/<timestamp> \
  --thread-id <agent-id> \
  --record-source multi_agent_v1_thread \
  --submission-id <send-input-submission-id> \
  --status-id <wait-status-id> \
  --final-status done \
  --case-id-record <case-id> \
  --step-id <step-id> \
  --prompt-file /tmp/prompt.txt \
  --response-file /tmp/response.txt
```

旧字段 `--user-message-id` 和 `--assistant-message-id` 只作为兼容别名保留，不是 `multi_agent_v1_thread` 契约要求的字段名。

评测会检查：

- prompt 是否确实是 `$nexus-workflow ...`。
- 回复是否包含 `状态` 或 `status`。
- 回复是否包含 `输出` 或 `output`。
- 回复是否包含下一条 `$nexus-workflow`、`next_prompt` 或 `下一步`。
- 回复是否出现本地 Nexus CLI 调用证据，例如 `python -m nexus.cli`、`nexus.cli` 或 `run_id`。
- 回复是否没有使用 mock provider。
- `turn.json` 是否包含真实 `multi_agent_v1_thread` 证据：非空 `thread_id`、`submission_id`、`completion_id` 或 `status_id`、`final_status`。

## 外部副作用

默认不发送以下 prompt：

- `self-sync`
- `github-sync`
- `guide publish-feishu`
- `feishu`
- `system-showcase publish-feishu`

这些 prompt 只有在 `--allow-external-side-effects` 下才进入队列。即使进入队列，也必须由 monitor 明确确认后发送到 Codex thread。

## 和 16 个问题轴的关系

Skill replay 不替代 `problem_axis_results`。它补的是用户真实输入面：

- P03：用户不应反复输入同一限定语。
- P09：检索后不能只给运行状态。
- P12：Verix 式独立评测必须驱动 Nexus 修改。
- P13：Monitor 必须能看到整条闭环阶段。
- P14：不能用专业词替代真实 artifacts。
- P16：分散 CLI 必须组合成节省机械操作的端到端系统。

如果 replay 失败，`skill_replay_evaluation.json` 会生成 `required_nexus_change`，修改对象应是 Nexus skill 输出契约、next prompt 生成、CLI 调用证据、artifact refs 或外部副作用审批链，而不是某个样例项目。

## Source Consumption

`replay_plan.json`、`replay_state.json` 和 `source_consumption.json` 必须记录本轮读取的 case registry、16 轴 problem matrix、wljob 0-34 command file 等来源。monitor 侧如果还提供 `issue_memory.json` 或 `completion_ledger.json`，skill_replay agent 的 summary artifact 必须把它们加入自己的 `source_consumption`，避免下一轮只依据 queue 或 subprocess 结果判断完成。
