# Nexus E2E Execution Harness

This lab harness turns the documented `$nexus-workflow ...` instruction tests into executable case registries, dry-run plans, one-case executions, batch loop state, skill replay plans, evaluation outputs, and modification requests. It does not change Nexus runtime code unless an explicit modification command or monitor patch step is supplied. It calls the real CLI through `python -m nexus.cli` and records what happened.

## Files

- `scripts/lab/nexus_lab_cases.json`: registry of test-document prompts mapped to real Nexus CLI argv templates.
- `scripts/lab/nexus_problem_matrix.json`: 16-axis Nexus problem matrix applied to every case.
- `scripts/lab/run_nexus_e2e_case.py`: dry-run planner and one-case executor.
- `scripts/lab/run_nexus_lab_loop.py`: batch loop runner that executes every selected case, evaluates every case, aggregates failures, and writes modification requests.
- `scripts/lab/init_monitor_multi_agent_run.py`: monitor-level launch packet generator for the real Codex `multi_agent_v1` Nexus-Verix run.
- `scripts/lab/run_nexus_verix_orchestrator.py`: subprocess Nexus-Verix lab loop that creates a git checkpoint, runs lab cases, runs skill replay, runs Verix audit, invokes an optional patch subprocess, runs regression checks, and repeats until pass or an explicit block.
- `scripts/lab/run_nexus_skill_replay.py`: Codex-thread skill replay planner, recorder, and evaluator for real `$nexus-workflow ...` user-message tests.
- `scripts/lab/plan_nexus_modification.py`: turns a `modification_request.json` into an actionable Nexus core modification plan with target files/functions and verification commands; it does not patch core code by itself.
- `scripts/lab/inspect_nexus_lab_status.py`: monitor status reader and graceful stop marker writer for `loop_state.json`.
- `scripts/lab/build_nexus_lab_project.py`: build-level validator for this lab project; it checks registries, 16-axis coverage, dry-run plans, sample validators, side-effect gates, and monitor entrypoints without running full cases.
- `docs/lab/lab_project_manifest.json`: latest build manifest produced by `build_nexus_lab_project.py`.
- `docs/lab/execution_harness.md`: this operator guide.
- `docs/lab/skill_input_replay.md`: operator guide for the Codex-thread replay layer.

## Project Construction Check

Run this before starting a full lab run:

```bash
python3 scripts/lab/build_nexus_lab_project.py --run-sample-validators
```

This check does not execute Nexus case workflows. It verifies the lab project itself:

- the 16 problem axes from `<LOCAL_PATH_REDACTED>`;
- the 10 structured cases and 8 real-project cases;
- dry-run plans for CLI harness, batch loop, and skill replay;
- that external side-effect prompts are refused by default;
- the three hand-built sample project validators;
- the modification planner and monitor entrypoints;
- `docs/lab/nexus_verix_completion_ledger.json` with one row per user goal / entrypoint / implementation surface / verification surface / external permission / current status.

The result is written to `docs/lab/lab_project_manifest.json`.

## Nexus-Verix Monitor Multi-Agent Start

Use this when the operator says "启动运行" for the Nexus-Verix self-build and acceptance system. This is the real monitor-level contract:

```bash
python -m nexus.cli --root <PROJECT_ROOT> nexus-verix-monitor --init-run
```

The command writes:

```text
<e2e-root>/monitor-runs/<run-id>/monitor_state.json
<e2e-root>/monitor-runs/<run-id>/launch_packet.json
<e2e-root>/monitor-runs/<run-id>/agent_tasks/*.md
```

The current Codex conversation then reads `launch_packet.json`, calls `multi_agent_v1.spawn_agent` for `nexus_execution`, `skill_replay`, `verix_audit`, `nexus_modification`, and `state_audit`, and writes returned agent ids back into `monitor_state.json`.

Equivalent direct script entry:

```bash
python3 scripts/lab/init_monitor_multi_agent_run.py --init-run
```

Acceptance boundary:

- `monitor_state.json.execution_model` must be `monitor_multi_agent_v1`.
- `monitor_state.json.python_embeds_subagents` must be `false`.
- Every required agent must have a non-empty `multi_agent_v1_id`.
- If only `orchestrator_state.json` exists, the run has not satisfied the monitor-level start-run contract.

## Nexus-Verix Subprocess Orchestrator

Use this only when the operator explicitly asks to start the subprocess lab loop. The target being modified and accepted is Nexus-Verix itself, not an external business project:

```bash
python -m nexus.cli --root <PROJECT_ROOT> nexus-verix-loop --execute
```

Equivalent direct script entry:

```bash
python3 scripts/lab/run_nexus_verix_orchestrator.py --execute
```

The orchestrator writes:

```text
<e2e-root>/orchestrator-runs/<run-id>/orchestrator_state.json
```

Execution boundary:

- The Python orchestrator execution model is `orchestrated_subprocess_loop`.
- It is not `multi_agent_v1` and does not embed or spawn true subagents inside the Python script.
- True subagents are only created by the current Codex monitor through `multi_agent_v1` spawn. When this script uses Codex, Verix, lab loop, skill replay, or a patch command, those are subprocess calls coordinated by the orchestrator and recorded in state.
- `satisfies_start_run_contract` is always `false` for this entrypoint.
- Dry-run plans and live `orchestrator_state.json` include `execution_model` and `execution_boundary` fields so downstream readers do not infer a real multi-agent runtime from this entrypoint.

The run phases are:

```text
create_state_and_git_checkpoint
build_check_lab_project
run_all_nexus_cases
run_skill_replay_surface
run_verix_independent_audit
merge_failures_into_modification_request
patch_nexus_general_mechanisms
run_regression_checks
```

The default run creates a git branch named `codex/nexus-verix-orchestrator-<timestamp>`, refuses GitHub public / Feishu / login side effects unless `--allow-external-side-effects` is present, includes the full `<LOCAL_PATH_REDACTED>` 0-34 `$nexus-workflow` command-file replay, runs Verix as an independent auditor when available, uses Codex CLI as a patch subprocess when `--patch-mode auto` can find `codex`, and runs full `pytest -q` regression after patching.

Status and stop use the same monitor script:

```bash
python3 scripts/lab/inspect_nexus_lab_status.py --loop-state <orchestrator_state.json>
python3 scripts/lab/inspect_nexus_lab_status.py --loop-state <orchestrator_state.json> --request-stop
```

## Safety Model

Default mode is plan-only. Running the script without `--execute`, or with `--dry-run-plan`, prints JSON and does not create fixtures, result directories, projects, Nexus runs, GitHub state, or Feishu state.

Execution is explicit:

```bash
python scripts/lab/run_nexus_e2e_case.py --case-id test_01_complex_init --execute
```

The executor uses:

```bash
cd <nexus-root> && python -m nexus.cli --root <nexus-root> --next-prompt-mode workflow --workflow-surface codex ...
```

It also sets an isolated `CODEX_HOME` under `<e2e-root>/codex_home`. Project targets, fixtures, and harness result JSON are under `--e2e-root`.

By default the executor also reads `scripts/lab/nexus_problem_matrix.json` and appends the global Nexus problem suffix to init/research/invoke-style prompts. This is intentional: every case must test the same 16 Nexus problems instead of mapping one case to one issue. Use `--no-problem-suffix` only for debugging command rendering, not for acceptance runs.

External side-effect cases are refused by default. This includes `self-sync`, GitHub private/public/bootstrap/auto-private, Feishu commands, guide Feishu publishing/sync, and system-showcase Feishu publishing. To intentionally run them, the operator must pass:

```bash
python scripts/lab/run_nexus_e2e_case.py --case-id test_04_sync_paths --execute --allow-external-side-effects
```

Do not use that flag unless the run is intentionally allowed to perform real GitHub or Feishu actions.

## Planning

Plan all cases:

```bash
python scripts/lab/run_nexus_e2e_case.py --dry-run-plan
```

Plan the real Forge-project case suite:

```bash
python scripts/lab/run_nexus_e2e_case.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --dry-run-plan
```

Plan one case:

```bash
python scripts/lab/run_nexus_e2e_case.py --case-id test_05_debug_handoff_rebind --dry-run-plan
```

Plan one real-project case:

```bash
python scripts/lab/run_nexus_e2e_case.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --case-id real_05_probe_failure_contrast \
  --dry-run-plan
```

The plan includes each prompt, resolved command, unresolved placeholders, and external side-effect gates.

Plan the full batch loop:

```bash
python scripts/lab/run_nexus_lab_loop.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --dry-run-plan
```

## Execution

Run a local-safe case:

```bash
python scripts/lab/run_nexus_e2e_case.py \
  --case-id test_03_reference_reading_record \
  --e2e-root /tmp/nexus-e2e-lab \
  --nexus-root <PROJECT_ROOT> \
  --execute
```

The script prints the path to:

```text
<e2e-root>/case-runs/<case-id>/<timestamp>/execution.json
```

That JSON records each step's `prompt`, `command`, `stdout`, `stderr`, `returncode`, parsed `run_id`, parsed `artifact_refs`, and timestamp.

Run the real-project batch loop:

```bash
python scripts/lab/run_nexus_lab_loop.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --e2e-root /tmp/nexus-real-e2e-lab \
  --max-iterations 3 \
  --execute
```

The loop prints the path to:

```text
<e2e-root>/lab-loops/<loop-id>/loop_state.json
```

Each iteration writes:

- `iteration_summary.json`: verdict counts plus 16-axis problem summary across all selected cases.
- `modification_request.json`: the input for the monitor patch step or optional patch subprocess.
- `cases/<case-id>/execution.json`: local copy of the case execution.
- `cases/<case-id>/evaluation.json`: evaluator result with `problem_axis_results`.

If `--apply-modification-command` is provided, the loop calls it between iterations with `NEXUS_LAB_MODIFICATION_REQUEST`, `NEXUS_LAB_ITERATION_DIR`, and `NEXUS_LAB_LOOP_DIR` in the environment, then reruns the full selected case set. Without that command, the current monitor conversation reads the modification request and performs the Nexus patching step.

Each non-pass iteration also writes:

- `modification_plan.json`: produced by `scripts/lab/plan_nexus_modification.py`, mapping failed 16-axis evidence to Nexus target files/functions and verification commands.

Monitor status:

```bash
python3 scripts/lab/inspect_nexus_lab_status.py --loop-state <e2e-root>/lab-loops/<loop-id>/loop_state.json
```

Request graceful stop:

```bash
python3 scripts/lab/inspect_nexus_lab_status.py --loop-state <e2e-root>/lab-loops/<loop-id>/loop_state.json --request-stop
```

The batch loop checks the stop marker between cases and iterations. It still does not gracefully interrupt a currently running Nexus command in the middle of a single command, but Codex CLI preflight and model nodes now write runtime status files under `tool_results/provider_runtime_status_preflight_<provider>_<intensity>.json` and `tool_results/provider_runtime_status_<node>.json`. When terminal output is idle for a long time, the monitor should inspect those status files before deciding whether to wait, stop, recover, or rerun.

Run a Codex-thread skill replay plan:

```bash
python scripts/lab/run_nexus_skill_replay.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --case-id real_07_wljob_heat_local_sequence \
  --e2e-root /tmp/nexus-skill-replay-lab \
  --init-run
```

This writes `replay_plan.json`, `send_queue.jsonl`, and `replay_state.json`. The monitor then sends each queued `$nexus-workflow ...` prompt to an isolated Codex thread, records the assistant response with `--record-turn`, and runs `--evaluate-run`.

## Placeholders

Built-in placeholders:

- `{E2E_ROOT}`: value of `--e2e-root`
- `{NEXUS_ROOT}`: value of `--nexus-root`
- `{PROJECT_PATH}`: defaults to `{E2E_ROOT}/projects/project-under-test`
- `{FIXTURE_DIR}`: defaults to `{E2E_ROOT}/fixtures`
- `{RUN_ID}`: captured from earlier step stdout when available
- `{HANDOFF_ID}`: captured from `interaction.json` or `handoffs/debug_handoff.json` when available

Override placeholders with `--var`:

```bash
python scripts/lab/run_nexus_e2e_case.py \
  --case-id test_02_supplemental_init \
  --var PROJECT_PATH=/tmp/nexus-e2e-lab/projects/example \
  --execute
```

If a required placeholder is still unresolved at execution time, the harness refuses that step and writes a blocked execution JSON instead of guessing.

## Current Scope

The one-case executor remains deliberately small. The batch loop now handles all-case execution, evaluation aggregation, modification-request generation, iteration state, and memory-candidate capture. The skill replay script covers the user-message surface that the CLI harness cannot exercise by itself.

It still does not secretly perform GitHub public, Feishu, login, token, browser, or other external side effects. Those paths are refused by default and only run when `--allow-external-side-effects` is explicitly supplied.
