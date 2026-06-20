---
name: nexus-workflow
description: Use this skill when the user says "使用 nexus", "nexus 调研", "用 nexus 调研当前项目", "调研某个项目是否有完整现成实现", "Codex-first workflow kernel", or asks Codex to run a local Nexus research / workflow orchestrator. This skill must call the local nexus CLI with a real provider and must not use mock unless the user explicitly asks for mock/test mode.
---

# Nexus Workflow

Use this skill as a thin trigger only. The workflow loop lives in `nexus.runner`, not in this `SKILL.md`.

For Codex, prefer the new interaction v4 orchestration behavior:

- Do not treat `blocked` as end-of-turn by default.
- Treat `blocked` as an intermediate recovery state whenever Nexus artifacts still expose any safe continuation, retry, approval, host-login, diagnostic, or `continue-after-input` path.
- Only treat `blocked` as terminal when no further safe action remains.
- If `interaction.json` has `lifecycle_status=awaiting_approval` and non-empty `pending_actions`, stay in the same Codex conversation turn:
  - summarize the action,
  - trigger Codex/host approval when needed,
  - execute the approved action,
  - then continue the same run via `approve-and-continue` or `continue-after-input`.
- If `interaction.json` has `lifecycle_status=awaiting_external_user`, render the user-only steps, wait for the user to say they are done, then continue the same run with `continue-after-input`.
- Use `approve-and-continue` instead of the old `approve` + `resume` two-step flow whenever the interaction provides it.
- Use `continue-after-input` after browser login / CAPTCHA / 2FA / any required manual completion.

Recovery Execution Protocol (Outer Codex):

- Outer Codex is the recovery executor, not just a reporter.
- If a nexus run still has any safe next action, do not stop the turn.
- Enter recovery mode automatically when any of the following appears:
  - `lifecycle_status=awaiting_approval`
  - `lifecycle_status=awaiting_external_user`
  - `recovery_mode=true`
  - `blocked` with any continuation / retry / approve-and-continue / continue-after-input path
  - login / device-code / OAuth / 2FA / CAPTCHA / browser handoff / provider preflight / host-permission recovery scenes
- In recovery mode, keep executing the chain until:
  - only manual user action remains and the required action is clearly explained, or
  - no more safe automatic / diagnostic / continuation actions remain
- When manual user action is required:
  - first launch any safe command or host flow that can be launched,
  - then render the manual steps,
  - if the state is observable, keep polling,
  - otherwise wait for the user to reply they are done, then immediately continue with `continue-after-input` or the equivalent continuation command
- Do not hand intermediate failures back to the user just because one fallback attempt failed.
- Host permission requests are part of the same recovery chain, not end-of-turn.
- Provider fallback is a Nexus semantic approval, not just a Codex host permission.
- Do not add or change `--provider ...` yourself when a run is blocked on provider preflight/runtime failure.
- Only switch, skip, or fallback providers by executing the `pending_actions` exposed in that run's `interaction.json`.
- If Nexus outputs `$nexus-workflow 修复 <provider> preflight 权限问题并继续 <run_id>` or `$nexus-workflow 跳过 <provider> fallback 继续 <run_id>`, route that exact prompt back through Nexus so it executes `recover <run_id>` on the original run; do not start a new run.
- If Nexus outputs `$nexus-workflow 查看 <run_id> 的状态`, treat it as a safe read-only internal status check: run Nexus `status <run_id>` yourself, relay the returned `interaction.json`, and do not ask the user to retype the status prompt.
- If the status result still has `pending_actions`, continue the safe recovery chain or render the concrete approval choices; do not keep returning another status prompt.
- Provider runtime failures such as usage limit, quota, 401 Unauthorized, auth failure, timeout, or model-not-found must be handled like provider recovery: do not silently change `--provider`; only execute fallback through Nexus `pending_actions`.
- If Nexus outputs `$nexus-workflow 脱离 workflow 进行 debug 并登记 ... <run_id>`, create the handoff before doing local debugging.
- If Nexus outputs `$nexus-workflow 回跳到刚才的 workflow，run_id <run_id>`, run `rebind-and-continue <run_id>` and let Nexus validate worklog/diff/test evidence.
- If a provider fix requires local debugging, create `handoff-for-debug` first, append worklog entries, then return via `rebind-and-continue`.

Debug Rebind Protocol (Outer Codex):

- If Outer Codex must leave the normal Nexus execution path to debug locally, it must first create or use a registered Nexus debug handoff.
- Do not perform freeform debug outside Nexus without a `debug_handoff`.
- During debug-mode execution, append a structured debug worklog for every material action: diagnosis, file edit, test, or local verification.
- After debug work reaches a stopping point, do not resume by memory or by freeform summary.
- Return through `rebind-and-continue`, let Nexus validate the worklog/diff/test evidence, then continue from the stored continuation or pending runner command.
- `rebind-and-continue` must resume the original `run_id`; starting a new run is not a valid rebind.
- Debugging outside Nexus without handoff is a non-rebindable path and violates this workflow.

What High-Intensity Recovery Logic Must Not Assume:

- Internal high-intensity recovery logic may guide recovery decisions only.
- It must not be treated as if it can:
  - hold a long-lived recovery workflow by itself,
  - request host permissions by itself,
  - wait for user manual completion by itself,
  - resume across outer approvals / manual handoffs / tool switches by itself.
- Its role is recovery decision-making only: diagnosis, branch selection, safe next-step planning, and handoff classification.

User-facing standardized output:

- Relay `interaction.json` only: 上一任务状态 / 上一任务输出 / 下一任务提示.
- The default `下一任务提示` must be a Codex-ready `$nexus-workflow ...` prompt, not a `python -m ...` command.
- If manual user action is required, render it as newline steps with `->`, then give the next `$nexus-workflow ...` prompt after completion.
- For research runs whose `reports/research_contract.json` has `requires_branch_reports=true`, the relayed artifacts must include the three branch reports and decision matrix:
  - `reports/branch_existing_wheel.md`
  - `reports/branch_subproject_wheels.md`
  - `reports/branch_from_scratch.md`
  - `reports/decision_matrix.md`
- In that same research mode, do not treat a generic “生成项目计划” prompt as valid until Nexus has written `reports/selected_research_branch.json`; the next prompt should ask the user to choose an existing-wheel, subproject-wheel, or from-scratch branch.
- If the user explicitly says "开启 CLI 下一步提示模式" or "使用 CLI 下一步提示模式", run the internal CLI with `--next-prompt-mode cli`; otherwise keep `--next-prompt-mode workflow`.
- For Codex, pass `--workflow-surface codex` or `NEXUS_WORKFLOW_SURFACE=codex` when invoking the local CLI so normalized prompts use `$nexus-workflow`.

Absolute local invocation:

- Nexus repo root is `<PROJECT_ROOT>`.
- Every local Nexus CLI call must be launched from that absolute repo root and must pass the same absolute `--root`.
- Use this command prefix by default:

```bash
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex
```

- Do not use relative repo jumps such as `cd ../nexus`, `cd nexus`, or commands that rely on the current terminal directory.
- If `<PROJECT_ROOT>` does not exist, return a standardized blocked result with `blocked_reason=repo_path_not_found`; do not fall back to mock or hand-written analysis.
- Any later `python -m nexus.cli ...` text is shorthand for the absolute command prefix above; actual execution must still use the absolute prefix.

Direct `$nexus-workflow` command routing:

- If the user says `启动运行` and the active context is the Nexus-Verix self-repair / self-build system, do not run `nexus-verix-loop --execute` as the main flow. Run `nexus-verix-monitor --init-run`, read the launch packet, spawn the five real `multi_agent_v1` subagents from the current Codex monitor conversation, record their ids in `monitor_state.json`, and keep this conversation as the monitor.
- `$nexus-workflow 启动 Nexus-Verix monitor` / `$nexus-workflow 启动 Nexus-Verix 自修复系统` -> run `nexus-verix-monitor --init-run`; then the outer Codex monitor must perform the real `multi_agent_v1.spawn_agent` calls. The CLI only creates the launch packet.
- `$nexus-workflow 检查当前 nexus workflow 是否安装并可用` -> run `skill doctor`, not `invoke`.
- `$nexus-workflow 查看当前可用的基座模型和 provider 状态` -> run `model status`, not `invoke`.
- `$nexus-workflow 初始化项目：<项目 idea>；父目录为 <parent-dir>` -> run `init-project "<项目 idea>" --parent <parent-dir>`.
- `$nexus-workflow 审批 <run_id> 的 project-root 并继续` -> run `approve-and-continue <run_id> project-root`.
- `$nexus-workflow 审批 <run_id> 的 online-search 并继续` -> run `approve-and-continue <run_id> online-search`.
- `$nexus-workflow 继续 <run_id>` -> run `resume <run_id>`.
- `$nexus-workflow 查看 <run_id> 的报告` -> run `report <run_id>`.
- `$nexus-workflow 调研项目 <project-path>：<需求>` -> run `research "<需求>" --project-root <project-path> --provider auto --approval-policy ask`; add `--approve-online-search` only when the user explicitly approved readonly online search.
- If a replay or test prompt appends a global problem/acceptance suffix after a blank line, treat the first `$nexus-workflow ...` block as the executable directive and the suffix as audit context. Do not route based on suffix keywords such as GitHub public, Feishu, recovery, or sync.
- Use `invoke "<用户一句话 nexus 请求>"` only when no direct route above or in “Other supported flows” matches.

Default invocation:

```text
$nexus-workflow 调研当前项目：<需求>
```

Equivalent local command:

```bash
cd <PROJECT_ROOT> && \
NEXUS_TAVILY_KEY_FILE=<LOCAL_PATH_REDACTED> \
NEXUS_SERPAPI_KEY_FILE=<LOCAL_PATH_REDACTED> \
NEXUS_CHINESE_WEB_PROVIDERS=tavily,serpapi_baidu \
NEXUS_ENABLE_BAIDU_SERP=1 \
python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex research "<需求>" --project-root <target-project> --provider auto --approval-policy ask --approve-online-search
```

Other supported flows:

```bash
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex init-project "<项目 idea>" --parent <parent-dir>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex init-project "<项目 idea>" --parent <parent-dir>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve <run_id> project-root
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve-and-continue <run_id> project-root
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve <run_id> online-search
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve-and-continue <run_id> online-search
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve <run_id> implementation-plan
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve-and-continue <run_id> implementation-plan
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex continue <run_id|latest> "<继续请求：生成项目计划/重新调研/局部调研/分块调研/更新意图>"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex recover <run_id|latest> "<恢复请求：恢复上一个中断 run/恢复 GitHub 初始化/恢复登录链路>"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex handoff-for-debug <run_id|latest> "<debug 原因>"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex append-debug-worklog <run_id|latest> --handoff-id <handoff_id> --kind <diagnose|edit|test> --summary "<说明>" [--command "<命令>"] [--path <changed-path>]
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex rebind-and-continue <run_id|latest> --handoff-id <handoff_id>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex debug-status <run_id|latest>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex resume <run_id> --approve online-search
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex resume <run_id|latest> --from-node <node_id>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex resume <run_id|latest> --force-node <node_id>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex plan-implementation <run_id>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve <run_id> code-change
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve-and-continue <run_id> code-change
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex execute-code-change <run_id> --provider auto
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex diff <run_id>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve <run_id> apply
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve-and-continue <run_id> apply
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex apply <run_id>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex continue-after-input <run_id|latest> --note "<用户已完成外部步骤>"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex test <run_id> --cmd "python -m pytest -q"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex board show
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex board show --project-path <target-project>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex board update --project-path <target-project> --status "<状态>"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex board point --project-path <target-project> "<记录点>"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex conversation-from-file <exported-conversation.md>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex conversation-from-file <exported-conversation.md> --selector "<问题编号/ref/关键词>"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex conversation sessions
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex conversation-to-workflow --current
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex conversation-to-workflow --all --match "<query>"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex conversation-to-workflow --task "<task name>"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve <run_id> conversation-session-read
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex rerank-candidates <run_id|latest>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex prepare-project <target-project>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex approve <run_id> git-baseline
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex install-generated-skill <run_id|latest> --confirm
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex conversation-manager init --project-path <target-project>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex conversation-manager ingest --project-path <target-project> --file <transcript>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex conversation-manager promote --project-path <target-project> --session-file <session.md> --to auto
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex github-sync configure --project-path <target-project> --private-repo OWNER/private --public-repo OWNER/public
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex github-sync bootstrap --project-path <target-project> --private-repo OWNER/private --public-repo OWNER/public
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex github-sync auto-private --project-path <target-project>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex github-sync private --project-path <target-project>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex github-sync public --project-path <target-project> --confirm
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex self-sync --project-path <target-project> --target auto
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex guide generate --project-path <target-project> --target auto
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex guide publish-feishu --project-path <target-project> --target auto
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex guide sync --project-path <target-project> --target auto
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex system-showcase generate --project-path <target-project>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex system-showcase publish-feishu --project-path <target-project> --confirm
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex feishu login
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex feishu configure --project-path <target-project> --app-id-path <LOCAL_PATH_REDACTED> --app-secret-path <LOCAL_PATH_REDACTED> --folder-token-path <LOCAL_PATH_REDACTED>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex feishu setup --project-path <target-project> --research-docs --approve-online-search
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex feishu setup --project-path <target-project> --app-id-path <LOCAL_PATH_REDACTED> --app-secret-path <LOCAL_PATH_REDACTED> --folder-token-path <LOCAL_PATH_REDACTED>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex feishu doctor --project-path <target-project>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex feishu record --project-path <target-project> --title "Nexus 记录" --content "<记录内容>"
cd <PROJECT_ROOT> && python scripts/feishu_smoke_test.py --app-id-path <LOCAL_PATH_REDACTED> --app-secret-path <LOCAL_PATH_REDACTED>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex invoke "<用户一句话 nexus 请求>"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex skill doctor
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex model status
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex model configure
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex model intent "<用户原句>"
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex model set <profile>
cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex nexus-verix-monitor --init-run
```

Project initialization intent-source rules:

- For `init-project`, the outer Codex layer must pass the user's raw project request unchanged through `--raw-user-request "<用户原话>"` whenever it normalizes, shortens, summarizes, or otherwise rewrites `<项目 idea>`.
- Do not inject project-name phrases such as `项目名固定为 ...`, `名为 ...`, `目录名为 ...`, or `命名为 ...` unless the user explicitly wrote that name in the current request or in a cited requirement file.
- If the user only says "从零新建一个项目" or describes business requirements without naming the project, let Nexus generate and validate the project-name candidates. Do not choose a stable slug in the skill layer.
- If the outer layer wants to propose a name for readability, phrase it as a non-binding note outside the `init-project` idea; never encode it as a fixed project name.
- If Nexus reports `explicit_project_name_agent_injected`, do not approve or continue the run. Ask the user to confirm the project name or rerun initialization with the raw request and no injected name.
- User-facing summaries must distinguish `用户原始请求显式指定` from `外层 Codex 建议/注入` and from `模型推荐`; do not call an agent-injected name a user-specified name.

Default provider behavior:

- Nexus `auto` is intensity-aware: low-intensity nodes use `configured API slot -> codex-cli gpt-5.4 -> codex-mcp`, while high-intensity nodes use `codex-cli gpt-5.4 -> configured API slot -> codex-mcp`.
- High-intensity research nodes are the initial intent understanding core, the overall architecture/research plan, the first search plan, the final research report, the implementation plan, and code-change execution.
- If no configured API slot is available, fall back to the corresponding `codex-cli` profile via `codex exec --output-schema`.
- If a provider reaches real preflight and fails, especially `codex-cli` state-storage errors such as `codex_state_db_permission_denied`, `codex_state_db_readonly`, `attempt to write a readonly database`, `state_5.sqlite`, or `Operation not permitted`, Nexus must block first and must not silently fallback. Relay the standardized output with the exact provider/stage/issue/reason in `上一任务输出`.
- For that preflight block, `下一任务提示` must include skill prompts for both choices: fix the failed provider and continue, or explicitly skip that provider and fallback. Example: `$nexus-workflow 修复 codex-cli preflight 权限问题并继续 <run_id>` and `$nexus-workflow 跳过 codex-cli fallback 继续 <run_id>`.
- If the user chooses the fix prompt, rerun the same Nexus CLI resume/continue command once with Codex `require_escalated` permissions so `codex exec` can initialize its state; relay the new `interaction.json`.
- If the user chooses the fallback prompt, rerun the same Nexus CLI resume/continue command with `NEXUS_AUTO_SKIP_PROVIDERS=<provider>` so auto selection skips the failed provider and tries the next real provider. This fallback is explicit user intent; do not use it before the blocked prompt.
- If `codex-cli` reaches real model execution and fails because the configured model text is invalid or unsupported for the current ChatGPT/Codex account, do not keep retrying the same wrong string. First search official OpenAI docs/help for the currently supported Codex model identifier, then rerun the configuration or the blocked command with Codex `require_escalated` permissions and replace the bad model string with the verified one before continuing.
- Use `codex-mcp` only as the standby fallback after API profiles and `codex-cli`, and do not pretend MCP succeeded if its smoke test fails.
- Do not silently use `MockProvider`.
- Use `--provider mock` only for tests or explicit local dry checks.
- If no real provider is available, nexus writes `interaction.json` with `blocked_reason=real_model_provider_not_configured` and asks for `python -m nexus.cli model configure`.
- If the user says "更换模型", "使用 qwen/deepseek/codexcli 模型", or "配置 <provider> 模型", call `python -m nexus.cli model intent "<用户原句>"`.
- If the user says "基座模型使用 xxx", pass the full user request to nexus or use `--model xxx`; every model node in that run must use the selected provider/profile.
- Supported first-stage API profiles include qwen, openai, deepseek, kimi, gemini, zhipu, minimax, doubao, baichuan, and anthropic; unsupported expansion providers must be reported as not yet implemented.
- Iterative search uses explicit model nodes for intent routing, search planning, coverage review, stop decision, localization review, candidate review, risk analysis, and final report.
- After discovery, `reports/next_options.json` is the standard continuation protocol; use `python -m nexus.cli continue <run_id|latest> "<USER_REQUEST>"` for one-sentence follow-up actions.
- Candidate ranking must run through tool-layer canonicalization/dedupe/evidence merge before model review; the model sees compact candidate evidence, not raw search dumps.
- If target project code will be modified and the project is not a git repo, use `python -m nexus.cli prepare-project <target-project>` to create an approval-gated git baseline before code-change.
- Conversation-to-skill/workflow requires a real transcript source. If the current Codex window history is not programmatically readable, nexus must block and ask for an exported markdown/json/ChatGPT zip transcript.
- `resume` must use node checkpoints when available: completed nodes reuse validated artifacts, failed or forced nodes rerun from the requested node.
- Reading local Codex session history requires `conversation-session-read` approval and must redact secrets before model calls.
- GitHub sync defaults to private. Project initialization must sync to GitHub private by default unless the user explicitly says "不同步 GitHub", "跳过 GitHub 同步", `no-github-sync`, or uses `--no-github-sync`. If repo names are omitted for a new ordinary project, Nexus defaults to `YaofeiHe/<project>` and `YaofeiHe/<project>-public` unless `NEXUS_DEFAULT_GITHUB_OWNER` overrides the owner. If `.github/nexus-sync.json` is missing for Nexus or Verix itself, `auto-private` / self-sync must automatically enter bootstrap with default repos (`<PRIVATE_REPO>` + `YaofeiHe/nexus-public`, `YaofeiHe/verix` + `YaofeiHe/verix-public`). Bootstrap creates/validates git, private/public remotes, GitHub repositories, private denylist scanning, first commit, and private push. Public sync may bootstrap missing self config first, but public push always requires staging, secret scan, and explicit confirmation.
- If GitHub CLI auth is missing during sync/bootstrap, enter the native GitHub CLI browser auth flow: run safe `gh auth login --web --clipboard --skip-ssh-key --git-protocol https --hostname github.com`, relay the one-time code such as `4D26-4ECE`, and let GitHub CLI open its own browser page. The user completes email/password/2FA/CAPTCHA/authorization manually in that browser. Never read tokens, cookies, browser profiles, SSH keys, `.env`, password files, passwords, 2FA codes, or CAPTCHA contents. If auth verifies, retry the original sync action once; otherwise block with the manual code and retry prompt.
- If the workflow returns `LOGIN_START_FAILED` or reports browser/proxy/network startup failure before a one-time code is shown, do not stop at a vague blocked summary. First try to run the same safe `gh auth login --web --clipboard --skip-ssh-key --git-protocol https --hostname github.com` from the host terminal with the required escalation/browser capability, then rerun the same `$nexus-workflow ...` command after the user finishes GitHub login. Only if host execution is unavailable should you fall back to printing the one-shot terminal command for the user.
- The whole operation guide lives at `docs/operation-guide.md`; GitHub sync is one chapter inside it, not a separate primary guide. Project initialization must generate/update the operation guide, append a default Feishu initialization record to `docs/feishu-records.md`, and auto-sync operation/guide documents to Feishu unless the user explicitly says "不同步飞书", "跳过飞书", `no-feishu-sync`, or uses `--no-feishu-sync`. If Feishu config is unavailable, keep local artifacts and block/route to setup/doctor with the real reason. If the user says "生成整体操作指南" or "同步整体操作指南到飞书", call `python -m nexus.cli guide generate/publish-feishu --project-path <target-project> --target auto`. Legacy "GitHub 同步指南" requests are compatibility aliases to the operation guide.
- Project initialization must persist the user's original requirement at `docs/intent/original-requirement.md`, the normalized requirement at `docs/intent/normalized-requirement.md`, and a machine-readable index at `.nexus/project-intent.json`; the operation guide and Feishu autosync must include these intent documents as project explanation artifacts.
- Feishu publishing/recording requires real Feishu Open Platform app credentials. If config or env secret is missing, nexus must block and present `feishu setup`; do not pretend browser login equals API authorization.
- Project initialization and skill-triggered project updates must default-check Feishu as the online doc capability and update existing Feishu documents through `.nexus/feishu-documents.json`; if unavailable, nexus must emit a setup guide and next prompt instead of silently skipping it. Do not create a new Feishu document for every update; reuse existing bindings and only create when a local Markdown path has no existing binding.
- Destructive initialization guard: do not interpret "补充初始化", "过一遍初始化流程", "完善初始化", "初始化同步", or similar wording for an existing project as permission to erase, empty, recreate, reset, or overwrite the project contents. Treat these as supplemental initialization unless the user explicitly asks for destructive cleanup/rebuild. If the user request appears to mean destructive initialization, such as "清空重建", "抹除后重新初始化", "删除原内容再初始化", "reset/recreate from scratch", or any equivalent instruction that would remove existing project files/data/history, do not execute it immediately. First stop and ask the user to confirm the destructive scope, target path, what will be deleted, and whether backups/git baseline are required.
- Supplemental initialization of an existing project must not change the project's internal business logic. It may only inspect and supplement Nexus-managed governance surfaces: intent docs, project overview, operation guide, GitHub/Feishu sync config, records, and board artifacts. Complete existing documents must be preserved; incomplete documents may be supplemented without deleting user text. Route this through `python -m nexus.cli supplement-init --project-path <target-project>`.
- Feishu setup can automate local credential path registration, folder_token/doc_token path registration, tenant_access_token smoke, docx creation/append when explicitly requested, and official-docs/chinese-web readonly research; it cannot automate creating the Feishu custom app, copying App Secret, publishing versions, admin review, or granting target folder/document resource permissions.
- Online sources require `online-search` approval before GitHub/MCP/Gitee/Chinese web/docs/skills adapters run; `research --approve-online-search` may pre-approve readonly public online search when the user asks for a one-shot end-to-end nexus research run.
- Chinese web search defaults to non-OpenAI providers: Tavily -> Brave.
- OpenAI web_search must not be used unless `NEXUS_ENABLE_OPENAI_WEB_SEARCH=1`.
- Baidu SERP providers require `NEXUS_ENABLE_BAIDU_SERP=1`.
- For this local setup, Tavily can be loaded from `NEXUS_TAVILY_KEY_FILE=<LOCAL_PATH_REDACTED>`, and SerpApi Baidu can be loaded from `NEXUS_SERPAPI_KEY_FILE=<LOCAL_PATH_REDACTED>`; never print those files or their contents.
- If no search provider API key is configured, nexus must report `auth_required` / blocked and must not invent URLs or candidates.
- A search result is valid only when it comes from a real provider response and has a source URL plus raw artifact.

Safety:

- Default mode is read-only discovery.
- Do not install dependencies, log in, read token/cookie/browser profile/SSH key/private files, bypass CAPTCHA/403/rate limits, submit forms, send messages, publish, push, create PRs, or write target-project code without explicit approval.
- Code changes must go through: implementation plan -> code-change approval -> isolated worktree -> diff preview -> apply approval -> tests.
- The user-facing reply should relay `interaction.json` only:
  - 上一任务状态
  - 上一任务输出
  - 下一任务提示

Continuation routing:

- If the user says "对上一个 nexus 调研生成项目计划", call `python -m nexus.cli continue latest "生成项目计划"`.
- If the user says "重新调研", call `python -m nexus.cli continue latest "重新调研：<scope>"`.
- If the user says "局部调研", call `python -m nexus.cli continue latest "局部调研：<scope>"`.
- If the user says "分块调研", call `python -m nexus.cli continue latest "分块调研：<chunks>"`.
- If the user says "更新项目意图/需求", call `python -m nexus.cli continue latest "更新项目意图：<new intent>"`.
- If the user says "恢复上一个中断 run", "恢复 GitHub 初始化", "恢复登录链路", "恢复授权链路", "恢复 provider preflight", or "恢复刚才卡住的步骤", call `python -m nexus.cli recover latest "<用户原句>"` and stay in recovery mode until only manual action or terminal blocked remains.
- If the user says "脱离 workflow 进行 debug 并登记", call `python -m nexus.cli handoff-for-debug latest "<用户原句>"`.
- If the user says "回跳到刚才的 workflow", "继续刚才的 debug 回绑", or "查看当前 debug 接管状态", call `python -m nexus.cli rebind-and-continue/debug-status latest ...` as appropriate.
- If the user says "候选去重/重排/重新排名", call `python -m nexus.cli rerank-candidates latest`.
- If the user says "查看记录板/查看项目记录板", call `python -m nexus.cli board show` unless a project path is explicit.
- If the user says "更新记录板当前状态", call `python -m nexus.cli board update --status "<状态>"` unless a project path is explicit.
- If the user says "记一个记录点/记录一点", call `python -m nexus.cli board point "<记录点>"` unless a project path is explicit.
- If the user says "将项目纳入 git 管理/建立 baseline", call `python -m nexus.cli prepare-project <target-project>`.
- If the user says "将本次对话整理成 skill/workflow" and gives a transcript path, call `python -m nexus.cli conversation-from-file <path>`.
- If the user says "将本次/当前对话整理成 skill/workflow" without a transcript path, call `python -m nexus.cli conversation-to-workflow --current`.
- If the user says "在全部对话历史中匹配 xxx", call `python -m nexus.cli conversation-to-workflow --all --match "xxx"`.
- If the user says "另一个任务的对话窗口/任务名称 xxx", call `python -m nexus.cli conversation-to-workflow --task "xxx"`.
- If the user says "resume/继续上一个中断 run", call `python -m nexus.cli resume latest`.
- If the user says "安装上一个生成的 skill", call `python -m nexus.cli install-generated-skill latest --confirm` only when the phrase is an explicit install request; otherwise show the blocked approval prompt.
- If the user says "管理对话记录", call `python -m nexus.cli conversation-manager init --project-path <target-project>`.
- If the user says "同步到 GitHub private", call `python -m nexus.cli github-sync private --project-path <target-project>`.
- If the user says "初始化 GitHub", "初始化 GitHub 同步", "初始化 GitHub 同步/建仓/bootstrap", or "GitHub 建仓", call `python -m nexus.cli github-sync bootstrap --project-path <target-project> --private-repo OWNER/private --public-repo OWNER/public`.
- If the user says "默认 private 自动同步/auto-private", call `python -m nexus.cli github-sync auto-private --project-path <target-project>`.
- If the user says "执行 Nexus 自同步", "执行 Verix 自同步", or "自同步", call `python -m nexus.cli self-sync --project-path <target-project> --target auto`. This path must first try GitHub private sync, automatically enter the GitHub CLI login flow when auth is required, then continue the same self-sync command and only after GitHub succeeds continue Feishu guide sync.
- If the user says "同步到 GitHub public", call `python -m nexus.cli github-sync public --project-path <target-project> --confirm` only after explicit confirmation.
- If the user says "生成整体操作指南", call `python -m nexus.cli guide generate --project-path <target-project> --target auto`.
- If the user says "同步整体操作指南到飞书", call `python -m nexus.cli guide publish-feishu --project-path <target-project> --target auto`.
- If the user says "生成 GitHub 同步指南" or "同步 GitHub 操作指南到飞书", treat it as a legacy alias and call the corresponding `guide generate/publish-feishu`; GitHub sync belongs inside `docs/operation-guide.md`.
- If the user says "生成系统架构展示", call `python -m nexus.cli system-showcase generate --project-path <target-project>`.
- If the user says "初始化飞书配置/配置飞书/进入飞书登录流程", call `python -m nexus.cli feishu setup --project-path <target-project> --research-docs --approve-online-search` and let nexus write setup/doctor artifacts.
- If the user provides app_id/app_secret/folder_token file paths for Feishu, call `python -m nexus.cli feishu setup --project-path <target-project> --app-id-path <path> --app-secret-path <path> [--folder-token-path <path>]`.
- If the user says "诊断飞书配置", call `python -m nexus.cli feishu doctor --project-path <target-project>`.
- If the user says "进行飞书记录/写入飞书记录", call `python -m nexus.cli feishu record --project-path <target-project> --title "<title>" --content "<content>"`; this model-formats content, writes it to local Markdown, then uses autosync to publish.
- If the user says "同步到飞书/发布到飞书", call `python -m nexus.cli guide publish-feishu --project-path <target-project> --target auto` so local Markdown documents are synced through autosync.
- If the user explicitly says "同步系统架构说明到飞书" or "发布系统架构展示到飞书", call `python -m nexus.cli system-showcase publish-feishu --project-path <target-project> --confirm`; this compatibility entry generates `docs/system/architecture.md` when needed and then uses autosync.
- If the user says "进入飞书登录流程/配置飞书", never open browser profiles or read cookies; call `python -m nexus.cli feishu setup --guide-only`.
- The continue route itself must be decided by nexus model nodes, not by SKILL.md logic.

Core rules:

- Intent routing, naming, planning, scoring, risk analysis, and next-option synthesis must call `HostModelProvider`.
- Do not downgrade requested end-to-end modules into mock-only placeholders; if blocked, write a blocked artifact and explain the missing condition.
- Board requests must also forward to the local CLI/runner and relay `interaction.json`; do not hand-write board summaries in the outer skill layer.
- User examples are intent evidence, not necessarily the final project scope.
