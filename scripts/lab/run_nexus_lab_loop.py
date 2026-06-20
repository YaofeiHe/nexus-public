#!/usr/bin/env python3
"""Run the Nexus lab as a batch, evaluate every case, and aggregate fixes."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CASES_FILE = SCRIPT_DIR / "nexus_real_project_cases.json"
DEFAULT_PROBLEM_MATRIX_FILE = SCRIPT_DIR / "nexus_problem_matrix.json"
RUNNER = SCRIPT_DIR / "run_nexus_e2e_case.py"
EVALUATOR = SCRIPT_DIR / "evaluate_nexus_case.py"
SKILL_REPLAY = SCRIPT_DIR / "run_nexus_skill_replay.py"
MODIFICATION_PLANNER = SCRIPT_DIR / "plan_nexus_modification.py"
STATUS_INSPECTOR = SCRIPT_DIR / "inspect_nexus_lab_status.py"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run all selected Nexus lab cases through batch execution/evaluation loops.")
    parser.add_argument("--cases-file", type=Path, default=DEFAULT_CASES_FILE)
    parser.add_argument("--problem-matrix", type=Path, default=DEFAULT_PROBLEM_MATRIX_FILE)
    parser.add_argument("--case-id", action="append", default=[], help="case id to include; repeat to select multiple; default is all cases")
    parser.add_argument("--e2e-root", type=Path, default=Path("/tmp/nexus-e2e-lab"))
    parser.add_argument("--nexus-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--provider-config-root", type=Path, help="Nexus root whose real provider/model config is inherited by E2E case runs")
    parser.add_argument("--no-inherit-provider-config", action="store_true", help="do not inherit real provider/model config in E2E case runs")
    parser.add_argument("--preserve-codex-home", action="store_true", help="inherit the caller CODEX_HOME instead of using a lab-isolated CODEX_HOME")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--execute", action="store_true", help="actually run the batch loop")
    parser.add_argument("--dry-run-plan", action="store_true", help="print the batch loop plan without running")
    parser.add_argument("--allow-external-side-effects", action="store_true", help="allow cases marked as GitHub/Feishu/public side-effect cases")
    parser.add_argument("--stop-after-evaluation", action="store_true", help="stop after writing the first iteration modification_request.json")
    parser.add_argument("--apply-modification-command", help="optional shell command invoked between iterations with env vars pointing at the modification request")
    parser.add_argument("--stop-file", type=Path, help="optional monitor stop marker; default is <loop_dir>/STOP")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.execute and args.dry_run_plan:
        raise SystemExit("--execute and --dry-run-plan are mutually exclusive")
    if args.max_iterations < 1:
        raise SystemExit("--max-iterations must be >= 1")

    cases_payload = load_json(args.cases_file.expanduser())
    problem_matrix = load_json(args.problem_matrix.expanduser())
    cases = select_cases(cases_payload, args.case_id)
    if not cases:
        raise SystemExit("no cases selected")

    if not args.execute:
        print(json.dumps(build_loop_plan(args, cases, problem_matrix), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    return run_loop(args, cases, problem_matrix)


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return payload


def select_cases(payload: dict[str, Any], case_ids: list[str]) -> list[dict[str, Any]]:
    cases = [case for case in payload.get("cases", []) if isinstance(case, dict)]
    if not case_ids:
        return cases
    by_id = {str(case.get("id")): case for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise SystemExit(f"unknown case id(s): {', '.join(missing)}")
    return [by_id[case_id] for case_id in case_ids]


def build_loop_plan(args: argparse.Namespace, cases: list[dict[str, Any]], problem_matrix: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "nexus.lab.loop_plan.v1",
        "generated_at": utc_now(),
        "mode": "dry_run_plan",
        "nexus_root": str(args.nexus_root.expanduser().resolve()),
        "e2e_root": str(args.e2e_root.expanduser().resolve()),
        "provider_config": {
            "inherit_enabled": not args.no_inherit_provider_config,
            "source_root": str(args.provider_config_root.expanduser().resolve()) if args.provider_config_root else os.environ.get("NEXUS_LAB_PROVIDER_CONFIG_ROOT") or os.environ.get("NEXUS_PROVIDER_CONFIG_ROOT") or str(args.nexus_root.expanduser().resolve()),
            "codex_home": "inherited" if args.preserve_codex_home else "isolated_lab",
        },
        "cases_file": str(args.cases_file.expanduser().resolve()),
        "problem_matrix": {
            "source": problem_matrix.get("source"),
            "problem_count": len(problem_matrix.get("problems", [])),
            "coverage_policy": problem_matrix.get("coverage_policy", {}),
        },
        "iterations": args.max_iterations,
        "case_policy": "Every iteration runs every selected case before aggregating modifications.",
        "external_side_effects": "refused by default" if not args.allow_external_side_effects else "allowed by operator flag",
        "modification_mode": "command" if args.apply_modification_command else "monitor_or_current_chat",
        "modification_planner": {
            "available": MODIFICATION_PLANNER.exists(),
            "script": str(MODIFICATION_PLANNER),
            "note": "Writes modification_plan.json from modification_request.json; it does not patch Nexus core by itself.",
        },
        "monitor": {
            "status_script": str(STATUS_INSPECTOR),
            "stop_file": "<loop_dir>/STOP",
        },
        "skill_replay": {
            "available": SKILL_REPLAY.exists(),
            "script": str(SKILL_REPLAY),
            "mode": "codex_thread_message_replay",
            "note": "Use this layer to send real $nexus-workflow prompts to an isolated Codex thread; CLI harness alone does not cover it.",
        },
        "cases": [{"id": case.get("id", ""), "title": case.get("title", "")} for case in cases],
    }


def run_loop(args: argparse.Namespace, cases: list[dict[str, Any]], problem_matrix: dict[str, Any]) -> int:
    nexus_root = args.nexus_root.expanduser().resolve()
    e2e_root = args.e2e_root.expanduser().resolve()
    loop_id = timestamp_slug()
    loop_dir = e2e_root / "lab-loops" / loop_id
    loop_dir.mkdir(parents=True, exist_ok=True)
    stop_file = args.stop_file.expanduser().resolve() if args.stop_file else loop_dir / "STOP"

    state: dict[str, Any] = {
        "schema": "nexus.lab.loop_state.v1",
        "loop_id": loop_id,
        "started_at": utc_now(),
        "finished_at": "",
        "status": "running",
        "nexus_root": str(nexus_root),
        "e2e_root": str(e2e_root),
        "provider_config": {
            "inherit_enabled": not args.no_inherit_provider_config,
            "source_root": str(args.provider_config_root.expanduser().resolve()) if args.provider_config_root else os.environ.get("NEXUS_LAB_PROVIDER_CONFIG_ROOT") or os.environ.get("NEXUS_PROVIDER_CONFIG_ROOT") or str(nexus_root),
            "codex_home": "inherited" if args.preserve_codex_home else "isolated_lab",
        },
        "loop_dir": str(loop_dir),
        "stop_file": str(stop_file),
        "cases_file": str(args.cases_file.expanduser().resolve()),
        "problem_matrix": {
            "source": problem_matrix.get("source"),
            "problem_count": len(problem_matrix.get("problems", [])),
            "coverage_policy": problem_matrix.get("coverage_policy", {}),
        },
        "git": git_snapshot(nexus_root),
        "allow_external_side_effects": args.allow_external_side_effects,
        "max_iterations": args.max_iterations,
        "iterations": [],
        "memory_candidates_path": str(loop_dir / "memory_candidates.jsonl"),
        "modification_planner": {
            "script": str(MODIFICATION_PLANNER),
            "status": "available" if MODIFICATION_PLANNER.exists() else "missing",
            "note": "Each non-pass iteration writes modification_plan.json next to modification_request.json.",
        },
        "monitor": {
            "status_script": str(STATUS_INSPECTOR),
            "status_command": f"{sys.executable} {STATUS_INSPECTOR} --loop-state {loop_dir / 'loop_state.json'}",
            "stop_command": f"{sys.executable} {STATUS_INSPECTOR} --loop-state {loop_dir / 'loop_state.json'} --request-stop",
        },
        "skill_replay": {
            "script": str(SKILL_REPLAY),
            "status": "available" if SKILL_REPLAY.exists() else "missing",
            "note": "Generate skill replay runs separately with run_nexus_skill_replay.py when testing the real Codex user-message surface.",
        },
    }
    save_json(loop_dir / "loop_state.json", state)

    for iteration_number in range(1, args.max_iterations + 1):
        if stop_requested(stop_file):
            state["status"] = "stopped_by_monitor"
            break
        iteration_dir = loop_dir / f"iteration-{iteration_number:02d}"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        iteration_record: dict[str, Any] = {
            "iteration": iteration_number,
            "started_at": utc_now(),
            "finished_at": "",
            "status": "running",
            "case_results": [],
            "summary_path": str(iteration_dir / "iteration_summary.json"),
            "modification_request_path": str(iteration_dir / "modification_request.json"),
            "modification_plan_path": str(iteration_dir / "modification_plan.json"),
        }
        state["iterations"].append(iteration_record)
        save_json(loop_dir / "loop_state.json", state)

        for case in cases:
            if stop_requested(stop_file):
                iteration_record["status"] = "stopped_by_monitor"
                state["status"] = "stopped_by_monitor"
                save_json(loop_dir / "loop_state.json", state)
                break
            case_result = run_case_and_evaluate(args, case, iteration_dir, problem_matrix)
            iteration_record["case_results"].append(case_result)
            append_memory_candidates(loop_dir / "memory_candidates.jsonl", case_result)
            save_json(loop_dir / "loop_state.json", state)

        if state.get("status") == "stopped_by_monitor":
            break

        summary = aggregate_iteration(iteration_record, problem_matrix)
        save_json(iteration_dir / "iteration_summary.json", summary)
        modification_request = build_modification_request(summary, state, iteration_record)
        modification_request_path = iteration_dir / "modification_request.json"
        save_json(modification_request_path, modification_request)
        planner_result = write_modification_plan(modification_request_path, iteration_dir / "modification_plan.json", nexus_root)
        iteration_record["modification_planner"] = planner_result

        iteration_record["finished_at"] = utc_now()
        iteration_record["status"] = summary["status"]
        save_json(loop_dir / "loop_state.json", state)

        if summary["status"] == "pass":
            state["status"] = "pass"
            break
        if summary["status"] == "blocked_external_side_effects":
            state["status"] = "blocked_external_side_effects"
            break
        if args.stop_after_evaluation:
            state["status"] = "needs_modification_agent"
            break
        if not args.apply_modification_command:
            state["status"] = "needs_modification_agent"
            break

        modification = run_modification_command(args.apply_modification_command, iteration_dir, loop_dir, modification_request)
        iteration_record["modification"] = modification
        if modification["returncode"] != 0:
            state["status"] = "modification_failed"
            break
        state["git"] = git_snapshot(nexus_root)
        save_json(loop_dir / "loop_state.json", state)
    else:
        state["status"] = "iteration_limit_reached"

    state["finished_at"] = utc_now()
    save_json(loop_dir / "loop_state.json", state)
    print(str(loop_dir / "loop_state.json"))
    return 0 if state["status"] == "pass" else 1


def run_case_and_evaluate(args: argparse.Namespace, case: dict[str, Any], iteration_dir: Path, problem_matrix: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("id", "unknown_case"))
    case_dir = iteration_dir / "cases" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    runner_cmd = [
        sys.executable,
        str(RUNNER),
        "--cases-file",
        str(args.cases_file.expanduser().resolve()),
        "--problem-matrix",
        str(args.problem_matrix.expanduser().resolve()),
        "--case-id",
        case_id,
        "--e2e-root",
        str(args.e2e_root.expanduser().resolve()),
        "--nexus-root",
        str(args.nexus_root.expanduser().resolve()),
        "--execute",
    ]
    if args.provider_config_root:
        runner_cmd.extend(["--provider-config-root", str(args.provider_config_root.expanduser().resolve())])
    if args.no_inherit_provider_config:
        runner_cmd.append("--no-inherit-provider-config")
    if args.preserve_codex_home:
        runner_cmd.append("--preserve-codex-home")
    if args.allow_external_side_effects:
        runner_cmd.append("--allow-external-side-effects")

    runner = run_command(runner_cmd, cwd=args.nexus_root.expanduser().resolve())
    execution_path = parse_execution_path(runner["stdout"])
    result: dict[str, Any] = {
        "case_id": case_id,
        "title": case.get("title", ""),
        "runner": runner,
        "execution_path": str(execution_path) if execution_path else "",
        "evaluation_path": "",
        "evaluation": {},
        "status": "execution_failed" if runner["returncode"] else "executed",
    }

    if execution_path and execution_path.exists():
        execution_data = load_json(execution_path)
        execution_status = str(execution_data.get("status", ""))
        local_execution = case_dir / "execution.json"
        local_execution.write_text(execution_path.read_text(encoding="utf-8"), encoding="utf-8")
        evaluation_path = case_dir / "evaluation.json"
        evaluator_cmd = [
            sys.executable,
            str(EVALUATOR),
            str(execution_path),
            "--problem-matrix",
            str(args.problem_matrix.expanduser().resolve()),
            "--output",
            str(evaluation_path),
        ]
        evaluator = run_command(evaluator_cmd, cwd=args.nexus_root.expanduser().resolve())
        result["evaluator"] = evaluator
        result["evaluation_path"] = str(evaluation_path)
        if evaluation_path.exists():
            result["evaluation"] = load_json(evaluation_path)
            if execution_status == "refused_external_side_effect":
                result["status"] = "refused_external_side_effect"
            else:
                result["status"] = str(result["evaluation"].get("verdict", "evaluated"))
        elif evaluator["returncode"]:
            result["status"] = "evaluation_failed"
    elif external_side_effect_refused(runner):
        result["status"] = "refused_external_side_effect"
    elif runner["returncode"]:
        evaluation_path = case_dir / "evaluation.json"
        synthetic = build_aborted_case_evaluation(case, result, problem_matrix)
        save_json(evaluation_path, synthetic)
        result["evaluation_path"] = str(evaluation_path)
        result["evaluation"] = synthetic
        result["status"] = "fail"
    return result


def build_aborted_case_evaluation(case: dict[str, Any], case_result: dict[str, Any], problem_matrix: dict[str, Any]) -> dict[str, Any]:
    runner = case_result.get("runner", {}) if isinstance(case_result.get("runner"), dict) else {}
    stderr = str(runner.get("stderr", "")).strip()
    stdout = str(runner.get("stdout", "")).strip()
    reason = stderr or stdout or f"runner returned {runner.get('returncode')}"
    problems = [problem for problem in problem_matrix.get("problems", []) if isinstance(problem, dict)]
    axis_results = []
    for problem in problems:
        axis_results.append(
            {
                "id": str(problem.get("id", "")),
                "title": str(problem.get("title", "")),
                "status": "fail",
                "expected": str(problem.get("user_problem", "")),
                "observed": "Case aborted before a normal execution artifact could be evaluated: " + reason[:500],
                "dimensions": [str(item) for item in problem.get("evaluator_dimensions", [])],
                "surfaces": [str(item) for item in problem.get("nexus_surfaces", [])],
                "required_evidence": [str(item) for item in problem.get("required_evidence", [])],
                "applies_to_every_case": bool(problem.get("applies_to_every_case")),
                "evidence_refs": ["runner_failure"],
                "required_nexus_change": "Preserve failed case artifacts and route the aborted execution through the same 16-axis evaluation/modification request contract.",
            }
        )
    return {
        "schema": "nexus.lab.case_evaluation.v1",
        "case_id": case_result.get("case_id", case.get("id", "")),
        "verdict": "fail",
        "failed_surface": ["lab runner", "evaluation artifact"],
        "evidence_refs": [
            {
                "id": "runner_failure",
                "path": case_result.get("execution_path", ""),
                "note": "runner failed before a complete execution artifact was available",
                "exists": False,
            }
        ],
        "problem_matrix": {
            "schema": problem_matrix.get("schema"),
            "source": problem_matrix.get("source"),
            "coverage_policy": problem_matrix.get("coverage_policy", {}),
            "problem_count": len(problems),
        },
        "problem_axis_results": axis_results,
        "required_nexus_change": [
            {
                "dimension": "aborted_case_evaluation",
                "surface": "lab loop",
                "recommendation": "Write a failed evaluation artifact with per-axis reasons whenever runner execution aborts before normal evaluator input exists.",
                "reason": reason[:500],
            }
        ],
        "heuristic_review_notice": "Synthetic failed evaluation for an aborted case; this is not a pass and exists to keep modification aggregation auditable.",
    }


def aggregate_iteration(iteration_record: dict[str, Any], problem_matrix: dict[str, Any]) -> dict[str, Any]:
    problem_ids = [str(problem.get("id", "")) for problem in problem_matrix.get("problems", []) if isinstance(problem, dict)]
    problem_summary: dict[str, Any] = {
        problem_id: {"pass": 0, "fail": 0, "blocked": 0, "missing": 0, "cases": []}
        for problem_id in problem_ids
    }
    verdict_counts = {"pass": 0, "fail": 0, "blocked": 0, "missing": 0, "refused_external_side_effect": 0}
    failed_cases: list[dict[str, Any]] = []

    for case_result in iteration_record.get("case_results", []):
        case_id = str(case_result.get("case_id", ""))
        status = str(case_result.get("status", "missing"))
        if status in verdict_counts:
            verdict_counts[status] += 1
        elif status == "refused_external_side_effect":
            verdict_counts["refused_external_side_effect"] += 1
        else:
            verdict_counts["missing"] += 1

        evaluation = case_result.get("evaluation") if isinstance(case_result.get("evaluation"), dict) else {}
        axes = evaluation.get("problem_axis_results") if isinstance(evaluation, dict) else None
        if isinstance(axes, list):
            for axis in axes:
                if not isinstance(axis, dict):
                    continue
                problem_id = str(axis.get("id", ""))
                axis_status = str(axis.get("status", "missing"))
                bucket = problem_summary.setdefault(problem_id, {"pass": 0, "fail": 0, "blocked": 0, "missing": 0, "cases": []})
                if axis_status not in {"pass", "fail", "blocked"}:
                    axis_status = "missing"
                bucket[axis_status] += 1
                bucket["cases"].append(
                    {
                        "case_id": case_id,
                        "status": axis_status,
                        "observed": axis.get("observed", ""),
                        "required_nexus_change": axis.get("required_nexus_change"),
                    }
                )
        else:
            for bucket in problem_summary.values():
                bucket["missing"] += 1
                bucket["cases"].append({"case_id": case_id, "status": "missing", "observed": "no problem_axis_results"})

        if status != "pass":
            failed_cases.append(
                {
                    "case_id": case_id,
                    "status": status,
                    "execution_path": case_result.get("execution_path", ""),
                    "evaluation_path": case_result.get("evaluation_path", ""),
                }
            )

    blocked_only_by_external = (
        verdict_counts["fail"] == 0
        and verdict_counts["blocked"] == 0
        and verdict_counts["missing"] == 0
        and verdict_counts["refused_external_side_effect"] > 0
    )
    if verdict_counts["fail"] or verdict_counts["missing"]:
        status = "fail"
    elif verdict_counts["blocked"]:
        status = "blocked"
    elif blocked_only_by_external:
        status = "blocked_external_side_effects"
    else:
        status = "pass"

    return {
        "schema": "nexus.lab.iteration_summary.v1",
        "iteration": iteration_record.get("iteration"),
        "status": status,
        "verdict_counts": verdict_counts,
        "failed_cases": failed_cases,
        "problem_summary": problem_summary,
    }


def build_modification_request(summary: dict[str, Any], state: dict[str, Any], iteration_record: dict[str, Any]) -> dict[str, Any]:
    failing_axes: list[dict[str, Any]] = []
    for problem_id, bucket in summary.get("problem_summary", {}).items():
        if not isinstance(bucket, dict):
            continue
        if bucket.get("fail", 0) or bucket.get("blocked", 0) or bucket.get("missing", 0):
            failing_axes.append(
                {
                    "problem_id": problem_id,
                    "counts": {
                        "pass": bucket.get("pass", 0),
                        "fail": bucket.get("fail", 0),
                        "blocked": bucket.get("blocked", 0),
                        "missing": bucket.get("missing", 0),
                    },
                    "cases": bucket.get("cases", []),
                }
            )
    return {
        "schema": "nexus.lab.modification_request.v1",
        "loop_id": state.get("loop_id"),
        "iteration": iteration_record.get("iteration"),
        "status": "no_change_needed" if summary.get("status") == "pass" else "needs_nexus_modification",
        "source_summary": iteration_record.get("summary_path"),
        "nexus_root": state.get("nexus_root"),
        "git": state.get("git"),
        "rule": "Modify Nexus general mechanisms only. Do not hardcode case ids, sample project names, or fixed prompt strings.",
        "case_policy": "All selected cases were run before this request was generated.",
        "failing_problem_axes": failing_axes,
        "failed_cases": summary.get("failed_cases", []),
    }


def run_modification_command(command: str, iteration_dir: Path, loop_dir: Path, modification_request: dict[str, Any]) -> dict[str, Any]:
    request_path = iteration_dir / "modification_request.json"
    env = os.environ.copy()
    env["NEXUS_LAB_LOOP_DIR"] = str(loop_dir)
    env["NEXUS_LAB_ITERATION_DIR"] = str(iteration_dir)
    env["NEXUS_LAB_MODIFICATION_REQUEST"] = str(request_path)
    env["NEXUS_LAB_MODIFICATION_STATUS"] = str(modification_request.get("status", ""))
    completed = subprocess.run(command, cwd=REPO_ROOT, env=env, shell=True, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "cwd": str(REPO_ROOT),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "finished_at": utc_now(),
    }


def write_modification_plan(request_path: Path, output_path: Path, nexus_root: Path) -> dict[str, Any]:
    if not MODIFICATION_PLANNER.exists():
        return {
            "status": "missing",
            "script": str(MODIFICATION_PLANNER),
            "output_path": str(output_path),
            "finished_at": utc_now(),
        }
    result = run_command(
        [
            sys.executable,
            str(MODIFICATION_PLANNER),
            "--request",
            str(request_path),
            "--output",
            str(output_path),
            "--nexus-root",
            str(nexus_root),
        ],
        cwd=nexus_root,
    )
    result["status"] = "written" if result["returncode"] == 0 and output_path.exists() else "failed"
    result["output_path"] = str(output_path)
    return result


def run_command(argv: list[str], *, cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)
    return {
        "command": shlex.join(argv),
        "cwd": str(cwd),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "finished_at": utc_now(),
    }


def parse_execution_path(stdout: str) -> Path | None:
    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not text:
            continue
        path = Path(text)
        if path.name == "execution.json" and path.exists():
            return path
    return None


def external_side_effect_refused(result: dict[str, Any]) -> bool:
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".lower()
    return "refused_external_side_effect" in text


def stop_requested(path: Path) -> bool:
    return path.exists()


def append_memory_candidates(path: Path, case_result: dict[str, Any]) -> None:
    text = f"{case_result.get('runner', {}).get('stdout', '')}\n{case_result.get('runner', {}).get('stderr', '')}".lower()
    markers = ["login", "auth", "credential", "token", "eof", "permission", "not supported", "rate limit", "登录", "认证", "权限", "阻断"]
    matched = [marker for marker in markers if marker in text]
    if not matched:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": "nexus.lab.memory_candidate.v1",
        "created_at": utc_now(),
        "case_id": case_result.get("case_id"),
        "matched_markers": matched,
        "note": "Candidate only. Promote to Codex memory after the operation is verified useful and not case-specific.",
        "execution_path": case_result.get("execution_path", ""),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def git_snapshot(cwd: Path) -> dict[str, Any]:
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=cwd, text=True, capture_output=True, check=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, text=True, capture_output=True, check=False)
    status = subprocess.run(["git", "status", "--short"], cwd=cwd, text=True, capture_output=True, check=False)
    return {
        "branch": branch.stdout.strip(),
        "head": head.stdout.strip(),
        "status_short": status.stdout.splitlines(),
    }


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
