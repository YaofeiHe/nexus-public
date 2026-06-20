#!/usr/bin/env python3
"""Run the Nexus-Verix self-build and acceptance loop.

This is the top-level monitor/orchestrator for improving Nexus-Verix itself.
It reuses the existing Nexus lab runner, skill replay planner, Verix CLI, and
modification planner instead of creating a parallel test framework.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
FORGE_ROOT = REPO_ROOT.parent
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CASES_FILE = SCRIPT_DIR / "nexus_real_project_cases.json"
DEFAULT_PROBLEM_MATRIX_FILE = SCRIPT_DIR / "nexus_problem_matrix.json"
BUILD_CHECKER = SCRIPT_DIR / "build_nexus_lab_project.py"
LAB_LOOP = SCRIPT_DIR / "run_nexus_lab_loop.py"
SKILL_REPLAY = SCRIPT_DIR / "run_nexus_skill_replay.py"
STATUS_INSPECTOR = SCRIPT_DIR / "inspect_nexus_lab_status.py"
DEFAULT_VERIX_ROOT = FORGE_ROOT / "verix"
EXECUTION_MODEL = "orchestrated_subprocess_loop"
EXECUTION_BOUNDARY = {
    "execution_model": EXECUTION_MODEL,
    "not_execution_model": "multi_agent_v1",
    "python_orchestrator_embeds_subagents": False,
    "real_subagent_spawn_source": "current_codex_monitor_multi_agent_v1_only",
    "monitor_level_compatible": False,
    "satisfies_start_run_contract": False,
    "requires_external_monitor_spawn": True,
    "subprocess_agents_are_not_real_agents": True,
    "monitor_contract_path": str(REPO_ROOT / "docs" / "lab" / "monitor_multi_agent_contract.md"),
    "description": (
        "This Python entrypoint is an orchestrated subprocess/lab loop. It calls existing "
        "CLI surfaces and subprocesses, records state, and may invoke Codex CLI as an "
        "external patch subprocess. It does not implement or spawn true multi_agent_v1 "
        "subagents internally."
    ),
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Nexus-Verix orchestrated subprocess/lab-loop self-improvement orchestrator.")
    parser.add_argument("--cases-file", type=Path, default=DEFAULT_CASES_FILE)
    parser.add_argument("--problem-matrix", type=Path, default=DEFAULT_PROBLEM_MATRIX_FILE)
    parser.add_argument("--case-id", action="append", default=[], help="case id to include; repeat to select multiple; default is all")
    parser.add_argument("--e2e-root", type=Path, default=Path("/tmp/nexus-verix-orchestrator"))
    parser.add_argument("--nexus-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--verix-root", type=Path, default=DEFAULT_VERIX_ROOT)
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--dry-run-plan", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-external-side-effects", action="store_true")
    parser.add_argument("--no-create-branch", action="store_true", help="do not create a git branch/checkpoint before execution")
    parser.add_argument("--branch-prefix", default="codex/nexus-verix-orchestrator")
    parser.add_argument("--skill-replay-mode", default="auto", choices=["auto", "queue-only", "off"])
    parser.add_argument("--skill-replay-step-timeout", type=int, default=600)
    parser.add_argument("--skill-replay-status-interval", type=int, default=15)
    parser.add_argument("--verix-mode", default="auto", choices=["auto", "off"])
    parser.add_argument("--patch-mode", default="auto", choices=["auto", "command", "off"])
    parser.add_argument("--patch-command", default="", help="command used when --patch-mode command is selected")
    parser.add_argument("--regression-mode", default="full", choices=["full", "build-check"], help="full runs build checks plus pytest; build-check only validates lab construction")
    parser.add_argument("--stop-file", type=Path, help="optional stop marker; default is <orchestrator_dir>/STOP")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.execute and args.dry_run_plan:
        raise SystemExit("--execute and --dry-run-plan are mutually exclusive")
    if not args.execute and not args.dry_run_plan:
        raise SystemExit("choose --dry-run-plan or --execute")
    if args.max_iterations < 1:
        raise SystemExit("--max-iterations must be >= 1")
    if args.patch_mode == "command" and not args.patch_command:
        raise SystemExit("--patch-mode command requires --patch-command")

    plan = build_plan(args)
    if args.dry_run_plan:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return execute(args, plan)


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_json(args.cases_file.expanduser()).get("cases", [])
    selected_cases = select_case_ids(cases, args.case_id)
    problem_matrix = load_json(args.problem_matrix.expanduser())
    return {
        "schema": "nexus.lab.nexus_verix_orchestrator_plan.v1",
        "generated_at": utc_now(),
        "mode": "dry_run_plan" if args.dry_run_plan else "execute",
        "execution_model": EXECUTION_MODEL,
        "execution_boundary": EXECUTION_BOUNDARY,
        "monitor_level_compatible": False,
        "satisfies_start_run_contract": False,
        "requires_external_monitor_spawn": True,
        "subprocess_agents_are_not_real_agents": True,
        "monitor_contract_path": str(REPO_ROOT / "docs" / "lab" / "monitor_multi_agent_contract.md"),
        "nexus_root": str(args.nexus_root.expanduser().resolve()),
        "verix_root": str(args.verix_root.expanduser().resolve()),
        "e2e_root": str(args.e2e_root.expanduser().resolve()),
        "cases_file": str(args.cases_file.expanduser().resolve()),
        "problem_matrix": {
            "path": str(args.problem_matrix.expanduser().resolve()),
            "source": problem_matrix.get("source", ""),
            "problem_count": len(problem_matrix.get("problems", [])),
            "coverage_policy": problem_matrix.get("coverage_policy", {}),
        },
        "selected_cases": selected_cases,
        "max_iterations": args.max_iterations,
        "external_side_effects": "allowed" if args.allow_external_side_effects else "authorization-gated",
        "branch_policy": "disabled" if args.no_create_branch else f"create {args.branch_prefix}-<timestamp>",
        "skill_replay_mode": args.skill_replay_mode,
        "skill_replay_step_timeout": args.skill_replay_step_timeout,
        "skill_replay_status_interval": args.skill_replay_status_interval,
        "verix_mode": args.verix_mode,
        "patch_mode": args.patch_mode,
        "regression_mode": args.regression_mode,
        "objective": "construct, test, and repair Nexus-Verix itself until the self-build acceptance loop passes",
        "phases": [
            "create_state_and_git_checkpoint",
            "build_check_lab_project",
            "run_all_nexus_cases",
            "run_skill_replay_surface_including_full_wljob_0_34_chain",
            "run_verix_independent_audit",
            "merge_failures_into_modification_request",
            "patch_nexus_general_mechanisms",
            "run_regression_checks",
            "repeat_until_nexus_verix_self_build_passes_or_stop_or_external_authorization_block",
        ],
        "monitor_contract": {
            "status_file": "<e2e-root>/orchestrator-runs/<run-id>/orchestrator_state.json",
            "stop_file": "<orchestrator-dir>/STOP",
            "user_messages": "operator instructions update the active run; only an explicit stop writes STOP",
        },
    }


def execute(args: argparse.Namespace, plan: dict[str, Any]) -> int:
    nexus_root = args.nexus_root.expanduser().resolve()
    verix_root = args.verix_root.expanduser().resolve()
    e2e_root = args.e2e_root.expanduser().resolve()
    run_id = timestamp_slug()
    run_dir = e2e_root / "orchestrator-runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stop_file = args.stop_file.expanduser().resolve() if args.stop_file else run_dir / "STOP"
    state: dict[str, Any] = {
        "schema": "nexus.lab.nexus_verix_orchestrator_state.v1",
        "run_id": run_id,
        "started_at": utc_now(),
        "finished_at": "",
        "status": "running",
        "phase": "starting",
        "plan": plan,
        "run_dir": str(run_dir),
        "stop_file": str(stop_file),
        "execution_model": EXECUTION_MODEL,
        "execution_boundary": EXECUTION_BOUNDARY,
        "monitor_level_compatible": False,
        "satisfies_start_run_contract": False,
        "requires_external_monitor_spawn": True,
        "subprocess_agents_are_not_real_agents": True,
        "monitor_contract_path": str(REPO_ROOT / "docs" / "lab" / "monitor_multi_agent_contract.md"),
        "git": git_snapshot(nexus_root),
        "iterations": [],
        "monitor": {
            "status_command": f"{sys.executable} {STATUS_INSPECTOR} --loop-state {run_dir / 'orchestrator_state.json'}",
            "stop_command": f"{sys.executable} {STATUS_INSPECTOR} --loop-state {run_dir / 'orchestrator_state.json'} --request-stop",
        },
    }
    state_path = run_dir / "orchestrator_state.json"
    save_json(state_path, state)

    if not args.no_create_branch:
        set_phase(state, state_path, "create_state_and_git_checkpoint")
        branch = create_branch(nexus_root, args.branch_prefix, run_id)
        state["git_checkpoint"] = branch
        state["git"] = git_snapshot(nexus_root)
        save_json(state_path, state)

    set_phase(state, state_path, "build_check_lab_project")
    build_check = run_build_check(nexus_root)
    state["build_check"] = build_check
    save_json(state_path, state)
    if build_check["returncode"] != 0:
        return finish(state, state_path, "blocked_build_check_failed")

    for iteration_no in range(1, args.max_iterations + 1):
        if stop_requested(stop_file):
            return finish(state, state_path, "stopped_by_monitor")
        iteration_dir = run_dir / f"iteration-{iteration_no:02d}"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        iteration: dict[str, Any] = {
            "iteration": iteration_no,
            "started_at": utc_now(),
            "finished_at": "",
            "status": "running",
            "iteration_dir": str(iteration_dir),
            "artifacts": {},
        }
        state["iterations"].append(iteration)
        save_json(state_path, state)

        set_phase(state, state_path, "run_all_nexus_cases")
        lab = run_lab_iteration(args, iteration_dir)
        iteration["lab"] = lab
        save_json(state_path, state)

        set_phase(state, state_path, "run_skill_replay_surface")
        skill = run_skill_replay(args, iteration_dir)
        iteration["skill_replay"] = skill
        save_json(state_path, state)

        set_phase(state, state_path, "run_verix_independent_audit")
        verix = run_verix_audit(args, iteration_dir, verix_root, nexus_root)
        iteration["verix"] = verix
        save_json(state_path, state)

        set_phase(state, state_path, "merge_failures_into_modification_request")
        merged = merge_iteration_feedback(iteration, iteration_dir)
        iteration["merged_feedback"] = merged
        save_json(state_path, state)

        verdict = iteration_verdict(iteration)
        if verdict == "pass":
            iteration["status"] = "pass"
            iteration["finished_at"] = utc_now()
            save_json(state_path, state)
            return finish(state, state_path, "pass")
        if verdict == "blocked_external_side_effects":
            iteration["status"] = "blocked_external_side_effects"
            iteration["finished_at"] = utc_now()
            save_json(state_path, state)
            return finish(state, state_path, "blocked_external_side_effects")
        if verdict == "blocked_no_skill_replay":
            iteration["status"] = "blocked_no_skill_replay"
            iteration["finished_at"] = utc_now()
            save_json(state_path, state)
            return finish(state, state_path, "blocked_no_skill_replay")
        if verdict == "blocked_skill_replay_timeout":
            iteration["status"] = "blocked_skill_replay_timeout"
            iteration["finished_at"] = utc_now()
            save_json(state_path, state)
            return finish(state, state_path, "blocked_skill_replay_timeout")
        if verdict == "blocked_no_verix":
            iteration["status"] = "blocked_no_verix"
            iteration["finished_at"] = utc_now()
            save_json(state_path, state)
            return finish(state, state_path, "blocked_no_verix")

        set_phase(state, state_path, "patch_nexus_general_mechanisms")
        patch = run_patch_step(args, iteration_dir, merged)
        iteration["patch"] = patch
        save_json(state_path, state)
        if patch["status"] != "completed":
            iteration["status"] = patch["status"]
            iteration["finished_at"] = utc_now()
            save_json(state_path, state)
            return finish(state, state_path, patch["status"])

        set_phase(state, state_path, "run_regression_checks")
        regression = run_regression_checks(nexus_root, mode=args.regression_mode)
        iteration["regression"] = regression
        iteration["status"] = "patched"
        iteration["finished_at"] = utc_now()
        state["git"] = git_snapshot(nexus_root)
        save_json(state_path, state)
        if regression["returncode"] != 0:
            return finish(state, state_path, "blocked_regression_failed")

    return finish(state, state_path, "iteration_limit_reached")


def run_lab_iteration(args: argparse.Namespace, iteration_dir: Path) -> dict[str, Any]:
    lab_root = iteration_dir / "nexus-lab"
    cmd = [
        sys.executable,
        str(LAB_LOOP),
        "--cases-file",
        str(args.cases_file.expanduser().resolve()),
        "--problem-matrix",
        str(args.problem_matrix.expanduser().resolve()),
        "--e2e-root",
        str(lab_root),
        "--nexus-root",
        str(args.nexus_root.expanduser().resolve()),
        "--max-iterations",
        "1",
        "--execute",
        "--stop-after-evaluation",
    ]
    for case_id in args.case_id:
        cmd.extend(["--case-id", case_id])
    if args.allow_external_side_effects:
        cmd.append("--allow-external-side-effects")
    result = run_command(cmd, cwd=args.nexus_root.expanduser().resolve())
    loop_state = parse_last_existing_path(result["stdout"], "loop_state.json")
    payload = load_json(loop_state) if loop_state else {}
    return {
        "status": payload.get("status", "command_failed" if result["returncode"] else "unknown"),
        "command_result": result,
        "loop_state_path": str(loop_state) if loop_state else "",
        "loop_state": payload,
        "modification_request_path": latest_iteration_field(payload, "modification_request_path"),
        "modification_plan_path": latest_iteration_field(payload, "modification_plan_path"),
    }


def run_skill_replay(args: argparse.Namespace, iteration_dir: Path) -> dict[str, Any]:
    if args.skill_replay_mode == "off":
        return {"status": "skipped", "reason": "skill_replay_mode_off"}
    replay_root = iteration_dir / "skill-replay"
    init_cmd = [
        sys.executable,
        str(SKILL_REPLAY),
        "--cases-file",
        str(args.cases_file.expanduser().resolve()),
        "--problem-matrix",
        str(args.problem_matrix.expanduser().resolve()),
        "--e2e-root",
        str(replay_root),
        "--nexus-root",
        str(args.nexus_root.expanduser().resolve()),
        "--include-wljob-command-file",
        "--init-run",
    ]
    for case_id in args.case_id:
        init_cmd.extend(["--case-id", case_id])
    if args.allow_external_side_effects:
        init_cmd.append("--allow-external-side-effects")
    init_result = run_command(init_cmd, cwd=args.nexus_root.expanduser().resolve())
    state_path = parse_last_existing_path(init_result["stdout"], "replay_state.json")
    if not state_path:
        return {"status": "failed", "init": init_result}
    if args.skill_replay_mode == "queue-only":
        return {"status": "queued", "state_path": str(state_path), "init": init_result}
    codex = shutil.which("codex")
    if not codex:
        return {"status": "blocked_no_skill_replay", "state_path": str(state_path), "reason": "codex_cli_not_found"}
    replay_result = run_codex_skill_replay(
        codex,
        state_path,
        args.nexus_root.expanduser().resolve(),
        step_timeout=args.skill_replay_step_timeout,
        status_interval=args.skill_replay_status_interval,
    )
    return {"status": replay_result.get("status", "fail"), "state_path": str(state_path), "init": init_result, "replay": replay_result}


def run_codex_skill_replay(
    codex: str,
    state_path: Path,
    nexus_root: Path,
    *,
    step_timeout: int,
    status_interval: int,
) -> dict[str, Any]:
    state = load_json(state_path)
    queue_path = Path(str(state.get("send_queue", "")))
    replay_dir = Path(str(state.get("replay_dir", state_path.parent)))
    if not queue_path.exists():
        return {"status": "fail", "reason": "send_queue_missing", "send_queue": str(queue_path)}
    turns: list[dict[str, Any]] = []
    queue_records = load_skill_replay_queue(queue_path)
    dispatchable_steps = [step for step in queue_records if step.get("queued") is not False]
    runtime_status = replay_dir / "skill_replay_runtime_status.json"
    save_json(
        runtime_status,
        {
            "schema": "nexus.lab.skill_replay_runtime_status.v1",
            "status": "running",
            "started_at": utc_now(),
            "last_heartbeat": utc_now(),
            "queue_path": str(queue_path),
            "total_steps": len(dispatchable_steps),
            "queue_rows": len(queue_records),
            "gated_steps": len(queue_records) - len(dispatchable_steps),
            "completed_steps": 0,
            "current_step": {},
        },
    )
    for line_no, step in enumerate(dispatchable_steps, start=1):
        prompt = str(step.get("prompt", ""))
        step_dir = replay_dir / "codex-exec" / str(step.get("case_id", "")) / str(step.get("step_id", line_no))
        step_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = step_dir / "prompt.txt"
        response_path = step_dir / "response.txt"
        step_status_path = step_dir / "runtime_status.json"
        prompt_path.write_text(prompt, encoding="utf-8")
        cmd = [codex, "exec", "--skip-git-repo-check", prompt]
        result = run_command_with_heartbeat(
            cmd,
            cwd=nexus_root,
            timeout=step_timeout,
            interval=max(status_interval, 1),
            step_status_path=step_status_path,
            aggregate_status_path=runtime_status,
            current_step={
                "case_id": step.get("case_id", ""),
                "step_id": step.get("step_id", line_no),
                "step_index": line_no,
                "total_steps": len(dispatchable_steps),
                "prompt_path": str(prompt_path),
                "response_path": str(response_path),
            },
        )
        response_path.write_text(result["stdout"] + ("\n" + result["stderr"] if result["stderr"] else ""), encoding="utf-8")
        record_cmd = [
            sys.executable,
            str(SKILL_REPLAY),
            "--record-turn",
            "--replay-dir",
            str(replay_dir),
            "--thread-id",
            f"codex-exec-{line_no}",
            "--case-id-record",
            str(step.get("case_id", "")),
            "--step-id",
            str(step.get("step_id", line_no)),
            "--prompt-file",
            str(prompt_path),
            "--response-file",
            str(response_path),
        ]
        record = run_command(record_cmd, cwd=nexus_root)
        turns.append({"step": step, "codex": result, "record": record})
        update_runtime_status(
            runtime_status,
            status="running",
            completed_steps=line_no,
            current_step={
                "case_id": step.get("case_id", ""),
                "step_id": step.get("step_id", line_no),
                "returncode": result["returncode"],
                "runtime_status": str(step_status_path),
            },
        )
        if result["returncode"] != 0:
            break
    eval_cmd = [sys.executable, str(SKILL_REPLAY), "--evaluate-run", "--replay-dir", str(replay_dir)]
    evaluation = run_command(eval_cmd, cwd=nexus_root)
    evaluation_path = parse_last_existing_path(evaluation["stdout"], "skill_replay_evaluation.json")
    evaluation_payload = load_json(evaluation_path) if evaluation_path else {}
    if any(turn.get("codex", {}).get("returncode") == 124 for turn in turns):
        final_status = "blocked_skill_replay_timeout"
    else:
        final_status = evaluation_payload.get("status", "fail" if evaluation["returncode"] else "unknown")
    update_runtime_status(runtime_status, status=final_status, completed_steps=len(turns), current_step={})
    return {
        "status": final_status,
        "runtime_status_path": str(runtime_status),
        "turns": turns,
        "evaluation": evaluation,
        "evaluation_path": str(evaluation_path) if evaluation_path else "",
        "evaluation_payload": evaluation_payload,
    }


def load_skill_replay_queue(queue_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            records.append(payload)
    return records


def run_verix_audit(args: argparse.Namespace, iteration_dir: Path, verix_root: Path, nexus_root: Path) -> dict[str, Any]:
    if args.verix_mode == "off":
        return {"status": "skipped", "reason": "verix_mode_off"}
    if not (verix_root / "verix" / "cli.py").exists():
        return {"status": "blocked_no_verix", "reason": "verix_cli_missing", "verix_root": str(verix_root)}
    intent = (
        "审计 Nexus 是否已经形成从初始意图、历史材料、$nexus-workflow skill replay、"
        "Verix 独立反馈、patch subprocess、多轮回归到监控/停止的完整闭环。"
    )
    cmd = [
        sys.executable,
        "-m",
        "verix.cli",
        "--root",
        str(verix_root),
        "audit",
        "--project-path",
        str(nexus_root),
        "--intent",
        intent,
        "--intent-source",
        "project",
        "--provider",
        "auto",
    ]
    result = run_command(cmd, cwd=verix_root, timeout=1800)
    output_path = iteration_dir / "verix_audit_output.txt"
    output_path.write_text(result["stdout"] + ("\n" + result["stderr"] if result["stderr"] else ""), encoding="utf-8")
    status = "pass" if result["returncode"] == 0 and "上一任务状态：completed" in result["stdout"] else "fail"
    if result["returncode"] == 0 and "上一任务状态：blocked" in result["stdout"]:
        status = "blocked"
    return {"status": status, "command_result": result, "output_path": str(output_path)}


def merge_iteration_feedback(iteration: dict[str, Any], iteration_dir: Path) -> dict[str, Any]:
    lab_request_path = Path(str(iteration.get("lab", {}).get("modification_request_path", "")))
    lab_request = load_json(lab_request_path) if lab_request_path.exists() else {}
    skill = iteration.get("skill_replay", {})
    verix = iteration.get("verix", {})
    request = {
        "schema": "nexus.lab.nexus_verix_merged_modification_request.v1",
        "generated_at": utc_now(),
        "status": "needs_nexus_modification",
        "lab_modification_request": str(lab_request_path) if lab_request_path.exists() else "",
        "failing_problem_axes": lab_request.get("failing_problem_axes", []),
        "failed_cases": lab_request.get("failed_cases", []),
        "skill_replay": summarize_subprocess_result(skill),
        "verix": summarize_subprocess_result(verix),
        "rule": "Modify Nexus general mechanisms only. Do not hardcode sample project names or one-off prompts.",
    }
    if not needs_change(iteration):
        request["status"] = "no_change_needed"
    output = iteration_dir / "nexus_verix_modification_request.json"
    save_json(output, request)
    return {"status": request["status"], "path": str(output), "request": request}


def run_patch_step(args: argparse.Namespace, iteration_dir: Path, merged: dict[str, Any]) -> dict[str, Any]:
    if merged.get("status") == "no_change_needed":
        return {"status": "completed", "reason": "no_change_needed"}
    request_path = str(merged.get("path", ""))
    if args.patch_mode == "off":
        return {"status": "blocked_patch_step_disabled", "request_path": request_path}
    if args.patch_mode == "command":
        return run_patch_command(args.patch_command, request_path, iteration_dir, args.nexus_root.expanduser().resolve())
    codex = shutil.which("codex")
    if not codex:
        return {"status": "blocked_no_patcher", "reason": "codex_cli_not_found", "request_path": request_path}
    prompt = build_codex_patch_prompt(request_path)
    prompt_path = iteration_dir / "codex_patch_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    result = run_command([codex, "exec", "--skip-git-repo-check", prompt], cwd=args.nexus_root.expanduser().resolve(), timeout=3600)
    output_path = iteration_dir / "codex_patch_output.txt"
    output_path.write_text(result["stdout"] + ("\n" + result["stderr"] if result["stderr"] else ""), encoding="utf-8")
    return {"status": "completed" if result["returncode"] == 0 else "patch_failed", "command_result": result, "prompt_path": str(prompt_path), "output_path": str(output_path)}


def run_patch_command(command: str, request_path: str, iteration_dir: Path, nexus_root: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["NEXUS_VERIX_MODIFICATION_REQUEST"] = request_path
    env["NEXUS_VERIX_ITERATION_DIR"] = str(iteration_dir)
    result = subprocess.run(command, cwd=nexus_root, env=env, shell=True, text=True, capture_output=True, check=False)
    return {
        "status": "completed" if result.returncode == 0 else "patch_failed",
        "command": command,
        "cwd": str(nexus_root),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "finished_at": utc_now(),
    }


def run_regression_checks(nexus_root: Path, mode: str = "full") -> dict[str, Any]:
    commands = [
        [sys.executable, str(BUILD_CHECKER), "--run-sample-validators", "--skip-dry-runs"],
    ]
    if mode == "full":
        commands.append([sys.executable, "-m", "pytest", "-q"])
    results = [run_command(command, cwd=nexus_root) for command in commands]
    return {
        "schema": "nexus.lab.regression_result.v1",
        "mode": mode,
        "returncode": 0 if all(result["returncode"] == 0 for result in results) else 1,
        "commands": results,
        "finished_at": utc_now(),
    }


def run_build_check(nexus_root: Path) -> dict[str, Any]:
    return run_command([sys.executable, str(BUILD_CHECKER), "--run-sample-validators"], cwd=nexus_root)


def iteration_verdict(iteration: dict[str, Any]) -> str:
    statuses = [
        str(iteration.get("lab", {}).get("status", "")),
        str(iteration.get("skill_replay", {}).get("status", "")),
        str(iteration.get("verix", {}).get("status", "")),
    ]
    active = [status for status in statuses if status not in {"", "skipped", "queued"}]
    if any(status == "blocked_no_skill_replay" for status in active):
        return "blocked_no_skill_replay"
    if any(status == "blocked_skill_replay_timeout" for status in active):
        return "blocked_skill_replay_timeout"
    if any(status == "blocked_no_verix" for status in active):
        return "blocked_no_verix"
    if any(status == "blocked_external_side_effects" for status in active):
        return "blocked_external_side_effects"
    if active and all(status in {"pass", "completed"} for status in active):
        return "pass"
    return "needs_patch"


def needs_change(iteration: dict[str, Any]) -> bool:
    return iteration_verdict(iteration) not in {"pass"}


def summarize_subprocess_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"status": "missing"}
    return {
        "status": result.get("status", ""),
        "state_path": result.get("state_path", ""),
        "loop_state_path": result.get("loop_state_path", ""),
        "evaluation_path": result.get("replay", {}).get("evaluation_path", "") if isinstance(result.get("replay"), dict) else "",
        "output_path": result.get("output_path", ""),
        "reason": result.get("reason", ""),
    }


def build_codex_patch_prompt(request_path: str) -> str:
    return f"""You are modifying the Nexus repository to satisfy the Nexus-Verix lab feedback.

Read this merged modification request first:
{request_path}

Requirements:
- Modify general Nexus mechanisms only; do not hardcode sample names such as wljob, feeler, probe, orbit, codpm, thesis, or forge-manager.
- Reuse existing Nexus runner, recovery, sync, project docs, lab loop, and Verix integration surfaces where possible.
- Do not replace real workflow paths with mock providers or offline demos.
- Preserve explicit authorization gates for GitHub public, Feishu, login, token, and other external side effects.
- After patching, run the focused verification commands that match the files you touched.

Return a concise summary of changed files, verification commands, and any remaining blocker.
"""


def create_branch(nexus_root: Path, prefix: str, run_id: str) -> dict[str, Any]:
    branch_name = f"{prefix}-{run_id}"
    result = run_command(["git", "switch", "-c", branch_name], cwd=nexus_root)
    return {"branch": branch_name, "result": result}


def finish(state: dict[str, Any], state_path: Path, status: str) -> int:
    state["status"] = status
    state["phase"] = "finished"
    state["finished_at"] = utc_now()
    save_json(state_path, state)
    print(str(state_path))
    return 0 if status == "pass" else 1


def set_phase(state: dict[str, Any], state_path: Path, phase: str) -> None:
    state["phase"] = phase
    state["updated_at"] = utc_now()
    save_json(state_path, state)


def run_command(argv: list[str], *, cwd: Path, timeout: int | None = None) -> dict[str, Any]:
    try:
        completed = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout)
        return {
            "command": shlex.join(argv),
            "cwd": str(cwd),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "finished_at": utc_now(),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": shlex.join(argv),
            "cwd": str(cwd),
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "timeout",
            "finished_at": utc_now(),
        }


def run_command_with_heartbeat(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    interval: int,
    step_status_path: Path,
    aggregate_status_path: Path,
    current_step: dict[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()
    started_at = utc_now()
    process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    base_status = {
        "schema": "nexus.lab.subprocess_runtime_status.v1",
        "status": "running",
        "command": shlex.join(argv),
        "cwd": str(cwd),
        "pid": process.pid,
        "started_at": started_at,
        "last_heartbeat": started_at,
        "timeout_seconds": timeout,
        **current_step,
    }
    save_json(step_status_path, base_status)
    update_runtime_status(aggregate_status_path, status="running", current_step={**current_step, "pid": process.pid, "runtime_status": str(step_status_path)})
    timed_out = False
    while process.poll() is None:
        elapsed = time.monotonic() - started
        heartbeat = {
            **base_status,
            "status": "running",
            "last_heartbeat": utc_now(),
            "elapsed_seconds": round(elapsed, 3),
        }
        save_json(step_status_path, heartbeat)
        update_runtime_status(
            aggregate_status_path,
            status="running",
            current_step={**current_step, "pid": process.pid, "elapsed_seconds": round(elapsed, 3), "runtime_status": str(step_status_path)},
        )
        if elapsed >= timeout:
            timed_out = True
            process.kill()
            break
        time.sleep(min(interval, max(timeout - elapsed, 1)))
    stdout_bytes, stderr_bytes = process.communicate()
    stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
    stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")
    returncode = 124 if timed_out else int(process.returncode or 0)
    finished = {
        **base_status,
        "status": "timeout" if timed_out else "completed",
        "finished_at": utc_now(),
        "last_heartbeat": utc_now(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "returncode": returncode,
        "timed_out": timed_out,
    }
    save_json(step_status_path, finished)
    return {
        "command": shlex.join(argv),
        "cwd": str(cwd),
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr + ("\ntimeout" if timed_out else ""),
        "finished_at": utc_now(),
        "runtime_status_path": str(step_status_path),
    }


def update_runtime_status(path: Path, **updates: Any) -> None:
    payload = load_json(path)
    if not payload:
        payload = {"schema": "nexus.lab.skill_replay_runtime_status.v1", "started_at": utc_now()}
    payload.update(updates)
    payload["last_heartbeat"] = utc_now()
    save_json(path, payload)


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def select_case_ids(cases: Any, case_ids: list[str]) -> list[dict[str, str]]:
    case_list = [case for case in cases if isinstance(case, dict)]
    selected = case_list if not case_ids else [case for case in case_list if str(case.get("id", "")) in set(case_ids)]
    return [{"id": str(case.get("id", "")), "title": str(case.get("title", ""))} for case in selected]


def parse_last_existing_path(stdout: str, filename: str) -> Path | None:
    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not text:
            continue
        path = Path(text)
        if path.name == filename and path.exists():
            return path
    return None


def latest_iteration_field(loop_state: dict[str, Any], key: str) -> str:
    iterations = [item for item in loop_state.get("iterations", []) if isinstance(item, dict)]
    if not iterations:
        return ""
    return str(iterations[-1].get(key, ""))


def git_snapshot(cwd: Path) -> dict[str, Any]:
    return {
        "branch": command_stdout(["git", "branch", "--show-current"], cwd),
        "head": command_stdout(["git", "rev-parse", "HEAD"], cwd),
        "status_short": command_stdout(["git", "status", "--short"], cwd).splitlines(),
    }


def command_stdout(argv: list[str], cwd: Path) -> str:
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)
    return result.stdout.strip()


def stop_requested(path: Path) -> bool:
    return path.exists()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
