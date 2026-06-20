# Nexus-Verix Monitor Multi-Agent System

日期：2026-06-19

## 要构建的系统

要构建的是一个由当前 Codex 对话窗口驱动的 Nexus-Verix 自修复实验系统。它的目标不是生成某一个样例项目，而是用真实样例项目、历史失败、`$nexus-workflow` skill 输入 replay、Verix 独立审计和修改 agent，持续修 Nexus-Verix，直到 Nexus 能可靠承担“根据初始意图自动搭建和迭代项目”的工作。

这个系统分两层：

1. 当前 Codex 对话窗口是 monitor-level orchestrator，负责 spawn subagent、记录主流程状态、接收用户插入指示、查询状态、暂停/终止、授权外部操作，并保证用户临时问题不会冲掉主流程。
2. Nexus 仓库内只保存持久化契约、测试 harness、状态文件、agent task prompt、评测 artifacts 和回归脚本；仓库内 Python 脚本不能自称真实多 agent runtime。

系统必须支持两种使用方式：

1. **逐条 skill 指令方式**：你按 workflow 一步步输入 `$nexus-workflow ...`，monitor 负责把这些输入作为真实 Nexus skill 交互处理，持续同一个 run 的 approval、`continue-after-input`、recovery、debug rebind 和报告生成。
2. **默认自动构建方式**：你只输入项目意图，monitor 把原始意图作为 Nexus execution 和 Verix audit 的共同输入，启动多 agent 多轮构建、测试、审计、修改、回归；你在当前窗口随时查询、补充、调整或显式停止。

## 面对的问题

当前问题不是 Nexus 没有零散能力，而是能力没有形成一个可靠闭环：

1. Nexus 经常保存原始输入，但没有把用户意图转化成可检查的项目功能、workflow、schema、validator 和验收标准。
2. Codex/Nexus 容易把样例当边界，只修单个 case，而不是抽象成通用机制。
3. 用户反复强调的限制条件没有沉淀成全局验收标准，导致同一问题在不同步骤反复出现。
4. 调研和检索缺少稳定的人读 source plan、search trace、覆盖缺口和停止原因。
5. 初始化、同步、public/private、Feishu、恢复回绑等链路分散修补，缺少统一端到端验收。
6. 外部 Codex 修复后回到原 Nexus 暂停点、并把经验写入 recovery playbook，不是所有路径都默认保证。
7. Verix 还没有作为独立审计 agent 驱动 Nexus 修改。
8. 现有 `run_nexus_verix_orchestrator.py` 是 subprocess loop，不是真实 `multi_agent_v1`。
9. 用户在 monitor 对话中临时提问时，系统容易只处理最新问题而忘掉主流程。
10. 当前只能说一个 wljob skill 序列跑到 `online_search_approval_required`，不能证明 `$nexus-workflow 调研` 的完整交互链和最终报告质量。
11. 16 个问题已经结构化进入测试矩阵，但自动结构评测通过不等于强智能审查证明彻底解决。
12. Verix 独立审计 agent 和 Nexus 修改 agent 的真实自动闭环还必须通过 monitor-level 多轮运行证明。

## Agent 布局

真实运行时由当前 Codex monitor 调用 `multi_agent_v1.spawn_agent` 创建以下 agent：

| agent | 职责 | 主要输出 |
|---|---|---|
| `nexus_execution` | 运行 Nexus/lab 执行面，生成每个 case 的 run、stdout、artifact index | `execution_summary.json`, `case_artifacts_index.json` |
| `skill_replay` | 在 monitor 控制下模拟用户输入 `$nexus-workflow ...`，覆盖 wljob 0-34 和其他项目类型 | `skill_replay_summary.json`, `prompt_turns.jsonl` |
| `verix_audit` | 独立读取原始意图、normalized intent、source index、Nexus run artifacts、项目结果并给出 verdict | `verix_verdict.json`, `audit_evidence_index.json` |
| `nexus_modification` | 根据聚合失败修改 Nexus 通用机制，不为样例写特例 | `patch_summary.md`, `verification_results.json` |
| `state_audit` | 审计 monitor 状态、agent 输出、stop marker、acceptance gap | `state_audit_report.json` |

## 信息流

1. monitor 初始化 `monitor_state.json` 和每个 agent 的 task prompt。
2. monitor spawn subagents，并把返回的 `multi_agent_v1_id` 写回状态。
3. `nexus_execution` 和 `skill_replay` 生成执行证据。
4. `verix_audit` 独立读取同一初始意图和执行证据，输出失败要求。
5. monitor 聚合失败，决定是否让 `nexus_modification` patch Nexus 通用机制。
6. patch 后重新跑全部 case、skill replay、Verix 审计和回归测试。
7. 只有全量验收通过、显式 stop、外部授权 blocked、或不可自动恢复 blocked，主流程才停止。
8. 如果你在中途输入新要求，monitor 先写入 `instruction_log` 并转发给相关 agent；除非你明确说停止，这条输入不能把主流程改成 `stopped`。
9. 每个 agent 先写 `heartbeat_status.json`，长等待时持续更新；monitor 定期运行 artifact merge，把真实产物状态写回 `monitor_state.json`。

## 启动方式

启动前创建 monitor launch packet：

```bash
python -m nexus.cli --root <PROJECT_ROOT> nexus-verix-monitor --init-run
```

该命令写入：

```text
/tmp/nexus-verix-monitor/monitor-runs/<run-id>/monitor_state.json
/tmp/nexus-verix-monitor/monitor-runs/<run-id>/launch_packet.json
/tmp/nexus-verix-monitor/monitor-runs/<run-id>/agent_tasks/*.md
```

真正的 subagent spawn 不在 Python 内完成，而是在当前 Codex 对话窗口完成。用户之后说“启动运行”时，monitor 必须：

1. 运行 `nexus-verix-monitor --init-run` 生成 launch packet。
2. 按 `launch_packet.json` 中的 task prompt 调用 `multi_agent_v1.spawn_agent`。
3. 把每个真实 subagent id 写回 `monitor_state.json`。
4. 用 `wait_agent` / `send_input` 协调 agent，而不是启动一个 Python loop 后丢给 subprocess。

## 用户控制语义

- “现在状态”：读取 `monitor_state.json` 和各 agent 输出，不打断主流程。
- “调整方向/补充要求”：把新指示写入 monitor 状态，并通过 `send_input` 转发给相关 agent；默认不终止主流程。
- “停止/终止”：写 `STOP` marker，并向正在运行的 agent 发送中断/停止指令。
- 授权请求：只在真实外部副作用、登录、publish、push、Feishu 写入等需要授权时返回用户确认。
- 误停恢复：如果出现 `STOP` 但用户没有明确停止，monitor 必须归档该 STOP、记录 `false_stop_recovered`，并继续同一个 run。

## 与 Python Orchestrator 的边界

`scripts/lab/run_nexus_verix_orchestrator.py` 仍然有价值，但它只是 `orchestrated_subprocess_loop`。它可以被某个 agent 当作工具调用，也可以产生 case/replay/Verix/patch artifacts；但它不能替代 monitor-level orchestrator，也不能声称自己 spawn 了真实 subagent。

验收时必须检查 `monitor_state.json` 的：

- `execution_model == "monitor_multi_agent_v1"`
- `python_embeds_subagents == false`
- 每个 agent 有真实 `multi_agent_v1_id`
- 用户插入指示只更新主流程，除非用户显式 stop
