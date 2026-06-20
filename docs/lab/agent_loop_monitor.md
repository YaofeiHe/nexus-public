# Nexus Lab Agent Loop Monitor

日期：2026-06-17

本文档记录当前 Nexus lab loop 的 monitor 操作入口、历史 monitor-spawned subagent 记录和 Python orchestrator 的真实执行边界。它不是 Nexus 核心功能；本阶段完成的是 lab 项目构建和启动前验收，不表示 Nexus 核心已经通过完整端到端运行。

## 当前目标

建立一个可被当前对话监控的 Nexus 改进闭环：

1. 执行 subprocess/lab runner 每轮跑完整 case set，而不是一次只跑一个测试。
2. 评测 subprocess 对每个 case 输出 16 轴 `problem_axis_results`，问题轴来自 `<LOCAL_PATH_REDACTED>`。
3. loop 聚合所有 case 的失败，生成 `modification_request.json`。
4. 修改规划入口把失败证据转成 Nexus 通用入口的修改计划；真正修改 Nexus 核心时必须由 monitor 当前对话或显式 `--apply-modification-command` 执行。
5. 当前对话作为 monitor，负责查看状态、继续下一轮、暂停/终止或人工指导。

## 已完成准备产物

- `docs/lab/sample_project_baseline.md`：样例项目通用验收基准。
- `docs/lab/nexus_problem_matrix.md`：16 个 Nexus 问题轴的人读说明。
- `docs/lab/nexus_e2e_instruction_tests.md`：10 组 `$nexus-workflow ...` 端到端测试设计。
- `docs/lab/nexus_agent_loop_entrypoints.md`：Nexus 当前可复用入口和缺口审计。
- `docs/lab/real_project_e2e_cases.md`：基于真实 Forge 项目的自动化测试矩阵。
- `scripts/lab/nexus_problem_matrix.json`：由 `<LOCAL_PATH_REDACTED>` 转成的机器可读问题矩阵。
- `scripts/lab/nexus_real_project_cases.json`：基于 `codpm`、`forge-manager`、`feeler`、`thesis`、`probe`、`orbit`、`wljob` 的真实项目 case registry。
- `scripts/lab/run_nexus_lab_loop.py`：批量运行、评测、聚合修改请求和多轮回归入口。
- `scripts/lab/run_nexus_verix_orchestrator.py`：Nexus-Verix 顶层主流程入口，负责建分支、跑 lab case、跑 skill replay、跑 Verix 审计、调用可选 patch subprocess、回归并多轮迭代。
- `scripts/lab/run_nexus_skill_replay.py`：真实 Codex thread `$nexus-workflow ...` 输入 replay 的计划、记录和评测入口。
- `scripts/lab/plan_nexus_modification.py`：把 `modification_request.json` 转为 `modification_plan.json`，列出目标 Nexus 文件、函数、禁止捷径和回归命令。
- `scripts/lab/inspect_nexus_lab_status.py`：读取 `loop_state.json`，输出 monitor 状态；可写入 stop marker 请求优雅停止。
- `scripts/lab/build_nexus_lab_project.py`：启动前构建检查入口，不跑完整 case。
- `docs/lab/lab_project_manifest.json`：最新构建验收 manifest。
- `docs/lab/skill_input_replay.md`：skill replay 的执行契约。

## 执行模型边界

- `scripts/lab/run_nexus_verix_orchestrator.py` 的执行模型是 `orchestrated_subprocess_loop`。
- 它不是 `multi_agent_v1`，也不在 Python 脚本内部创建真实 subagent。
- 真实 subagent 只能由当前 Codex monitor 通过 `multi_agent_v1` spawn。下表中的具名条目是 monitor 层曾经创建或跟踪的工作单元，不表示 Python orchestrator 内置多 agent runtime。
- Python orchestrator 中的 lab loop、skill replay、Verix audit、Codex CLI patch 和显式 patch command 都是 subprocess/CLI 调用；状态应按 `orchestrator_state.json` 的 `execution_model`、`execution_boundary`、phase、iteration 和 subprocess result 判断。

## Monitor 跟踪对象

| tracked unit | id | 职责 | 允许写入范围 | 当前状态 |
|---|---|---|---|---|
| Ohm | `019ed3b5-0b14-7a20-953e-6c05a21097a5` | 创建 Codex 手工高质量样例项目 | `docs/lab/sample_projects/` | completed |
| Kierkegaard | `019ed3b5-0b7c-7d30-8211-b58f1fbd6f33` | 创建 Nexus E2E 执行 harness | `scripts/lab/run_nexus_e2e_case.py`, `scripts/lab/nexus_lab_cases.json`, `docs/lab/execution_harness.md` | completed |
| Carson | `019ed3b5-0bf3-77c2-8090-66c6c9d9ff02` | 创建 Verix 式结构化评测器 | `scripts/lab/evaluate_nexus_case.py`, `docs/lab/evaluation_rubric.md`, `docs/lab/evaluation_schema.json` | completed |
| Pascal | `019ed3b5-0c50-7472-876a-1ee9b3b5dd08` | 创建 Nexus 通用修改 backlog | `docs/lab/nexus_modification_backlog.md` | completed |
| Local planner | `scripts/lab/plan_nexus_modification.py` | 把失败聚合转成通用修改计划 | `modification_plan.json` | completed |
| Local monitor | `scripts/lab/inspect_nexus_lab_status.py` | 查询 loop 状态并写 stop marker | `loop_state.json`, `<loop_dir>/STOP` | completed |
| Build checker | `scripts/lab/build_nexus_lab_project.py` | 启动前验证 lab 项目构建完整性 | `docs/lab/lab_project_manifest.json` | completed |
| Nexus-Verix orchestrator | `scripts/lab/run_nexus_verix_orchestrator.py` | 总控 Nexus 自修复主流程 | `orchestrator_state.json`, Nexus 修改分支 | completed |

## Monitor 可执行控制

- 真正启动运行：当前对话先执行 `python -m nexus.cli --root <PROJECT_ROOT> nexus-verix-monitor --init-run`，再根据 `launch_packet.json` 调用 `multi_agent_v1.spawn_agent` 创建 5 个真实 subagent。
- 查询状态：在当前对话问“现在每个 tracked unit 或 subprocess 到哪一步了”。
- 调整方向：在当前对话说明要改哪条约束；若涉及真实 subagent，必须由当前 Codex monitor 通过 `multi_agent_v1` 转发或新建。
- 暂停/终止：在当前对话说“停止 lab loop”或指定 tracked unit/subprocess；Python orchestrator 只通过 stop marker 和 subprocess 状态响应，不会自行管理 `multi_agent_v1` subagent。
- 本地状态脚本：`python3 scripts/lab/inspect_nexus_lab_status.py --loop-state <loop_state.json>`。
- 本地停止脚本：`python3 scripts/lab/inspect_nexus_lab_status.py --loop-state <loop_state.json> --request-stop`。
- 进入真实执行：由 monitor 启动 `scripts/lab/run_nexus_lab_loop.py`，默认跑全量真实项目 case。
- 进入 subprocess Nexus-Verix 自构建/自验收执行：由 monitor 或某个 subagent 启动 `python -m nexus.cli --root <PROJECT_ROOT> nexus-verix-loop --execute`；目标是生成 lab evidence，不满足完整 `启动运行` 的 monitor-level multi-agent 契约。
- 进入核心代码修改：读取 `modification_request.json` 和 `modification_plan.json` 后修改 Nexus 通用入口，再启动下一轮全量回归。
- 单个 provider preflight 或模型节点长时间无终端输出时，先读取当前 run 的 `tool_results/provider_runtime_status_preflight_<provider>_<intensity>.json`（例如 `provider_runtime_status_preflight_codex_cli_high.json`）、`tool_results/provider_runtime_status_<node>.json` 和 `tool_results/wait_observation_<node>.json`；不要只凭 terminal idle 判定失败。

## 当前边界

- 目前没有运行会触发 GitHub public、Feishu 或其他外部副作用的测试。
- 样例项目只能作为质量参考，后续测试必须包含样例外变体。
- 修改 Nexus 时必须面向通用能力，例如项目意图转化、材料读取记录、search trace、recovery rebind、playbook 持久化、sync 链路和 monitor 状态。
- GitHub public、Feishu、登录、token、浏览器或外部授权路径默认不执行；它们只作为 blocked/approval evidence 进入 monitor，除非显式允许外部副作用。
- Codex CLI provider 的默认运行中状态输出间隔是 60 秒，覆盖 preflight smoke 和后续模型节点；短于这个时间的无输出不应触发中断。若状态文件持续 heartbeat 且未达到 provider timeout，monitor 应继续等待；若状态写为 `timeout` 或 `failed`，再按 Nexus pending action 执行恢复、fallback 或重启。

## 本轮部署验证

- 脚本语法检查通过：`scripts/lab/build_nexus_lab_project.py`、`scripts/lab/inspect_nexus_lab_status.py`、`scripts/lab/plan_nexus_modification.py`、`scripts/lab/run_nexus_lab_loop.py`、`scripts/lab/run_nexus_e2e_case.py`、`scripts/lab/evaluate_nexus_case.py`、`scripts/lab/run_nexus_skill_replay.py`。
- 启动前构建检查通过：`python3 scripts/lab/build_nexus_lab_project.py --run-sample-validators`。
- 最新 manifest：`docs/lab/lab_project_manifest.json`，状态 `pass`，warnings 为空。
- JSON 检查通过：`scripts/lab/nexus_lab_cases.json`、`scripts/lab/nexus_real_project_cases.json`、`scripts/lab/nexus_problem_matrix.json`、`docs/lab/evaluation_schema.json`。
- Harness dry-run 通过：`test_01_complex_init` 生成本地初始化命令，且带 `--no-github-sync`、`--no-feishu-sync`。
- Harness dry-run 通过：`test_04_sync_paths` 明确标记 `self-sync` 和 `github-sync public` 为外部副作用。
- 全量 registry dry-run 通过：共 10 个 case。
- 外部副作用拒绝检查通过：未加 `--allow-external-side-effects` 执行 `test_04_sync_paths` 时返回 `refused_external_side_effect`，未启动真实同步命令。
- 三个样例项目 validator 均通过：`nonprofit_grant_workspace`、`field_incident_review_workspace`、`open_source_maintainer_duty_workspace`。
- 最新真实 lab loop 已执行全量 8 个真实项目 case：7 个 pass，1 个外部副作用 case 按默认边界拒绝执行并被评为预期通过。
- 最新 skill replay 已执行 `real_07_wljob_heat_local_sequence`，评估状态 `pass`，共记录 10 个 turn。

## 真实项目测试套件

真实项目 registry 已建立，作为后续主测试入口：

```bash
python3 scripts/lab/run_nexus_e2e_case.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --dry-run-plan
```

当前包含 8 个 case：

- `real_01_codpm_workflow_rebuild`：从 `codpm` 成功 workflow 项目抽取通用构建标准。
- `real_02_forge_manager_workflow_rebuild`：从 `forge-manager` 成功 workflow 项目抽取项目/任务/状态管理标准。
- `real_03_feeler_skill_rebuild`：从 `feeler` 成功 skill/workspace 抽取复杂意图理解标准。
- `real_04_thesis_skill_rebuild`：从 `thesis` skill/workspace 抽取官方依据、模板保格式和影子目录边界。
- `real_05_probe_failure_contrast`：用 `probe` 失败输出和 `feeler` 成功输出对照测试意图理解。
- `real_06_orbit_wechat_intent_repair`：用 `orbit` 失败/半失败项目和微信子项目测试复杂初始意图修复。
- `real_07_wljob_heat_local_sequence`：把 `nexus_wljob_heat_e2e_test_commands.md` 中本地安全部分转成逐条测试。
- `real_08_sync_and_external_boundary_contract`：外部同步、public、Feishu、恢复边界测试；默认拒绝执行外部副作用。

已验证：

- `scripts/lab/nexus_real_project_cases.json` 是合法 JSON。
- 全量真实 registry dry-run 通过，共 8 个 case。
- `real_05_probe_failure_contrast` dry-run 能输出 `probe_rebuild_from_history` 目标路径和 5 条通过要求。
- `real_07_wljob_heat_local_sequence` dry-run 中 `{PROJECT_PATH}` 已解析为 `wljob_heat_rebuild`，不再落到默认 `project-under-test`。
- 未加 `--allow-external-side-effects` 执行 `real_08_sync_and_external_boundary_contract` 时返回 `refused_external_side_effect`，未启动真实同步命令。

## 当前批量 loop 入口

默认真实项目批量运行：

```bash
python3 scripts/lab/run_nexus_lab_loop.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --e2e-root /tmp/nexus-real-e2e-lab \
  --max-iterations 3 \
  --execute
```

## Nexus-Verix 自构建/自验收入口

### Monitor-level multi-agent 入口

完整 `启动运行` 主流程：

```bash
python -m nexus.cli --root <PROJECT_ROOT> nexus-verix-monitor --init-run
```

当前 Codex monitor 随后必须 spawn：

- `nexus_execution`
- `skill_replay`
- `verix_audit`
- `nexus_modification`
- `state_audit`

如果没有 `monitor_state.json.execution_model == "monitor_multi_agent_v1"` 和 5 个真实 `multi_agent_v1_id`，不得声称完成 multi-agent run。

### Subprocess lab loop 入口

仅启动 subprocess loop：

```bash
python -m nexus.cli --root <PROJECT_ROOT> nexus-verix-loop --execute
```

该入口写入 `<e2e-root>/orchestrator-runs/<run-id>/orchestrator_state.json`，默认纳入 `<LOCAL_PATH_REDACTED>` 的 0-34 `$nexus-workflow` skill 输入链路，patch 后运行全量 `pytest -q`。查询和停止仍使用：

```bash
python3 scripts/lab/inspect_nexus_lab_status.py --loop-state <orchestrator_state.json>
python3 scripts/lab/inspect_nexus_lab_status.py --loop-state <orchestrator_state.json> --request-stop
```

每轮写入：

- `loop_state.json`：当前 loop、分支、HEAD、dirty 状态、每个 case 的执行和评测路径。
- `iteration_summary.json`：全部 case 的 verdict 和 16 个问题轴聚合结果。
- `modification_request.json`：monitor patch step 或可选 patch subprocess 的输入，必须指向 Nexus 通用机制。
- `modification_plan.json`：修改规划入口生成的目标文件/函数/验证命令。
- `memory_candidates.jsonl`：候选经验记录；只有验证为通用经验后才提升到 Codex memory。

本阶段没有启动完整项目运行；这里只完成 lab 项目构建、入口接线和启动前验收。

## Skill 输入 Replay 入口

当要测试用户真实输入 `$nexus-workflow ...` 的行为时，先生成 replay run：

```bash
python3 scripts/lab/run_nexus_skill_replay.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --case-id real_07_wljob_heat_local_sequence \
  --e2e-root /tmp/nexus-skill-replay-lab \
  --init-run
```

当前 monitor 随后 fork 一个隔离 Codex thread，把 `send_queue.jsonl` 里的 prompt 逐条发送过去，读取回复，用 `--record-turn` 写回 replay run，最后运行 `--evaluate-run`。这层测试的是 Codex skill 用户输入面，不等同于 CLI harness。
