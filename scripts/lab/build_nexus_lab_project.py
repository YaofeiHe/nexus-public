#!/usr/bin/env python3
"""Validate the Nexus lab project construction without running full cases."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
DOC_DIR = REPO_ROOT / "docs" / "lab"

PROBLEM_MATRIX = SCRIPT_DIR / "nexus_problem_matrix.json"
STRUCTURED_CASES = SCRIPT_DIR / "nexus_lab_cases.json"
REAL_CASES = SCRIPT_DIR / "nexus_real_project_cases.json"
EVALUATION_SCHEMA = DOC_DIR / "evaluation_schema.json"
MONITOR_CONTRACT = DOC_DIR / "monitor_multi_agent_contract.md"
MONITOR_STATE_SCHEMA = DOC_DIR / "monitor_agent_state.schema.json"
MONITOR_AGENT_ROLES = DOC_DIR / "monitor_agent_roles.json"
COMPLETION_LEDGER = DOC_DIR / "nexus_verix_completion_ledger.json"

REQUIRED_FILES = [
    PROBLEM_MATRIX,
    STRUCTURED_CASES,
    REAL_CASES,
    EVALUATION_SCHEMA,
    SCRIPT_DIR / "run_nexus_e2e_case.py",
    SCRIPT_DIR / "evaluate_nexus_case.py",
    SCRIPT_DIR / "run_nexus_lab_loop.py",
    SCRIPT_DIR / "run_nexus_verix_orchestrator.py",
    SCRIPT_DIR / "init_monitor_multi_agent_run.py",
    SCRIPT_DIR / "merge_monitor_agent_artifacts.py",
    SCRIPT_DIR / "run_nexus_skill_replay.py",
    SCRIPT_DIR / "plan_nexus_modification.py",
    SCRIPT_DIR / "inspect_nexus_lab_status.py",
    DOC_DIR / "nexus_problem_matrix.md",
    DOC_DIR / "execution_harness.md",
    DOC_DIR / "skill_input_replay.md",
    DOC_DIR / "real_project_e2e_cases.md",
    DOC_DIR / "agent_loop_monitor.md",
    DOC_DIR / "monitor_multi_agent_system.md",
    MONITOR_CONTRACT,
    MONITOR_STATE_SCHEMA,
    MONITOR_AGENT_ROLES,
    COMPLETION_LEDGER,
    DOC_DIR / "evaluation_rubric.md",
    DOC_DIR / "sample_project_baseline.md",
]

ALLOWED_DIMENSIONS = {
    "intent_capture",
    "intent_understanding",
    "reference_reading_record",
    "search_plan_log",
    "domain_workflows",
    "validation_script",
    "sync_artifacts",
    "recovery_rebind",
    "playbook_persistence",
    "monitor_next_prompt",
    "anti_hardcoding",
}

EXTERNAL_COMMANDS = {"self-sync", "github-sync", "guide", "feishu", "system-showcase"}


class BuildReport:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checks: list[dict[str, Any]] = []

    def ok(self, name: str, **extra: Any) -> None:
        self.checks.append({"name": name, "status": "pass", **extra})

    def warn(self, name: str, message: str, **extra: Any) -> None:
        self.warnings.append(f"{name}: {message}")
        self.checks.append({"name": name, "status": "warning", "message": message, **extra})

    def fail(self, name: str, message: str, **extra: Any) -> None:
        self.errors.append(f"{name}: {message}")
        self.checks.append({"name": name, "status": "fail", "message": message, **extra})


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build-check the Nexus lab project without executing full E2E cases.")
    parser.add_argument("--write-manifest", type=Path, default=DOC_DIR / "lab_project_manifest.json")
    parser.add_argument("--no-write-manifest", action="store_true", help="do not write lab_project_manifest.json")
    parser.add_argument("--run-sample-validators", action="store_true", help="run validators for docs/lab/sample_projects/*")
    parser.add_argument("--skip-dry-runs", action="store_true", help="skip dry-run command checks")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = BuildReport()

    check_required_files(report)
    matrix = load_json(PROBLEM_MATRIX, report, "problem_matrix")
    structured = load_json(STRUCTURED_CASES, report, "structured_cases")
    real = load_json(REAL_CASES, report, "real_cases")
    load_json(EVALUATION_SCHEMA, report, "evaluation_schema")
    monitor_schema = load_json(MONITOR_STATE_SCHEMA, report, "monitor_state_schema")
    monitor_roles = load_json(MONITOR_AGENT_ROLES, report, "monitor_agent_roles")

    if matrix:
        check_problem_matrix(matrix, report)
    if structured:
        check_cases("structured_cases", structured, report)
    if real:
        check_cases("real_cases", real, report)
        check_real_source_paths(real, report)
    ledger = load_json(COMPLETION_LEDGER, report, "completion_ledger")
    if ledger:
        check_completion_ledger(ledger, report)
    check_sample_projects(report, run_validators=args.run_sample_validators)
    check_modification_planner(report)
    check_status_inspector(report)
    check_monitor_multi_agent_contract(report, monitor_schema, monitor_roles)
    if not args.skip_dry_runs:
        check_dry_run_commands(report)

    manifest = build_manifest(report, matrix=matrix, structured=structured, real=real)
    if not args.no_write_manifest:
        output = args.write_manifest.expanduser()
        if not output.is_absolute():
            output = (REPO_ROOT / output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report.ok("manifest_written", path=str(output))
        manifest = build_manifest(report, matrix=matrix, structured=structured, real=real)
        output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report.errors else 1


def load_json(path: Path, report: BuildReport, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report.fail(name, f"not found: {path}")
        return {}
    except json.JSONDecodeError as exc:
        report.fail(name, f"invalid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        report.fail(name, "expected JSON object", path=str(path))
        return {}
    report.ok(name, path=str(path))
    return payload


def check_required_files(report: BuildReport) -> None:
    missing = [str(path) for path in REQUIRED_FILES if not path.exists()]
    if missing:
        report.fail("required_files", "required lab files are missing", missing=missing)
    else:
        report.ok("required_files", count=len(REQUIRED_FILES))


def check_problem_matrix(matrix: dict[str, Any], report: BuildReport) -> None:
    problems = [item for item in matrix.get("problems", []) if isinstance(item, dict)]
    ids = [str(item.get("id", "")) for item in problems]
    expected_ids = [f"P{index:02d}" for index in range(1, 17)]
    if ids != expected_ids:
        report.fail("problem_matrix_axes", "problem ids must be P01..P16 in order", observed=ids)
    else:
        report.ok("problem_matrix_axes", problem_count=len(problems))

    bad_dimensions: list[dict[str, Any]] = []
    for problem in problems:
        dimensions = [str(item) for item in problem.get("evaluator_dimensions", [])]
        missing = [dimension for dimension in dimensions if dimension not in ALLOWED_DIMENSIONS]
        if missing:
            bad_dimensions.append({"problem_id": problem.get("id"), "bad_dimensions": missing})
        if not problem.get("applies_to_every_case"):
            report.fail("problem_matrix_applies_to_every_case", "problem axis is not marked applies_to_every_case", problem_id=problem.get("id"))
    if bad_dimensions:
        report.fail("problem_matrix_dimensions", "unknown evaluator dimensions", bad_dimensions=bad_dimensions)
    else:
        report.ok("problem_matrix_dimensions", dimension_count=len(ALLOWED_DIMENSIONS))

    coverage_policy = matrix.get("coverage_policy", {})
    required_policy = {
        "case_policy": "all_cases_cover_all_problem_axes",
        "sample_policy": "no_sample_maps_to_only_one_problem",
        "structured_policy": "structured and sample-derived tests both evaluate every problem axis",
    }
    for key, expected in required_policy.items():
        if coverage_policy.get(key) != expected:
            report.fail("problem_matrix_coverage_policy", f"coverage policy mismatch for {key}", observed=coverage_policy.get(key), expected=expected)
    if not report.errors or all("problem_matrix_coverage_policy" not in item for item in report.errors):
        report.ok("problem_matrix_coverage_policy", policy=coverage_policy)


def check_cases(name: str, payload: dict[str, Any], report: BuildReport) -> None:
    cases = [case for case in payload.get("cases", []) if isinstance(case, dict)]
    ids = [str(case.get("id", "")) for case in cases]
    duplicate_ids = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if not cases:
        report.fail(name, "case registry has no cases")
        return
    if duplicate_ids:
        report.fail(name, "duplicate case ids", duplicate_ids=duplicate_ids)
    external_mismatches: list[dict[str, str]] = []
    prompt_errors: list[dict[str, str]] = []
    mock_steps: list[dict[str, str]] = []
    for case in cases:
        case_id = str(case.get("id", ""))
        steps = [step for step in case.get("steps", []) if isinstance(step, dict)]
        if not steps:
            report.fail(name, "case has no steps", case_id=case_id)
            continue
        case_external = bool(case.get("requires_external_side_effect"))
        for step in steps:
            step_id = str(step.get("id", ""))
            prompt = str(step.get("prompt", ""))
            args = [str(item) for item in step.get("nexus_args", []) if isinstance(item, str)]
            if not prompt.startswith("$nexus-workflow"):
                prompt_errors.append({"case_id": case_id, "step_id": step_id, "prompt": prompt[:80]})
            if "--provider=mock" in args or provider_value(args) == "mock":
                mock_steps.append({"case_id": case_id, "step_id": step_id})
            is_external = bool(step.get("requires_external_side_effect")) or command_is_external(args)
            if is_external and not (case_external or step.get("requires_external_side_effect")):
                external_mismatches.append({"case_id": case_id, "step_id": step_id, "command": " ".join(args[:3])})
    if prompt_errors:
        report.fail(f"{name}_prompts", "all step prompts must use the real $nexus-workflow surface", prompt_errors=prompt_errors)
    if mock_steps:
        report.fail(f"{name}_mock_provider", "mock provider is not allowed in lab cases", mock_steps=mock_steps)
    if external_mismatches:
        report.fail(f"{name}_external_gate", "external side-effect commands must be marked on the case or step", external_mismatches=external_mismatches)
    report.ok(name, case_count=len(cases), step_count=sum(len(case.get("steps", [])) for case in cases if isinstance(case.get("steps"), list)))


def provider_value(args: list[str]) -> str:
    for index, item in enumerate(args):
        if item == "--provider" and index + 1 < len(args):
            return args[index + 1]
        if item.startswith("--provider="):
            return item.split("=", 1)[1]
    return ""


def command_is_external(args: list[str]) -> bool:
    if not args:
        return False
    command = args[0]
    if command in {"self-sync", "feishu"}:
        return True
    if command == "github-sync":
        return True
    if command == "guide" and len(args) > 1 and args[1] in {"publish-feishu", "sync"}:
        return True
    if command == "system-showcase" and len(args) > 1 and args[1] == "publish-feishu":
        return True
    return command in EXTERNAL_COMMANDS


def check_real_source_paths(real: dict[str, Any], report: BuildReport) -> None:
    text = json.dumps(real, ensure_ascii=False)
    raw_paths = sorted(set(match.group(0).rstrip("，。、；;:：)") for match in re.finditer(r"/Users/[^\s\"'，。、；;）)]+", text)))
    missing = [path for path in raw_paths if not Path(path).exists()]
    if missing:
        report.warn("real_case_source_paths", "some source paths do not exist on this host", missing=missing)
    else:
        report.ok("real_case_source_paths", count=len(raw_paths))


def check_completion_ledger(ledger: dict[str, Any], report: BuildReport) -> None:
    allowed_statuses = {"done", "failed", "blocked", "not_started"}
    required_fields = {
        "id",
        "user_goal",
        "required_entrypoint_or_chain",
        "implementation_location",
        "verification_or_observation",
        "external_permission_or_manual_action",
        "status",
    }
    items = [item for item in ledger.get("items", []) if isinstance(item, dict)]
    if not items:
        report.fail("completion_ledger_items", "completion ledger must include at least one item")
        return
    bad_items: list[dict[str, Any]] = []
    for item in items:
        missing = sorted(field for field in required_fields if not str(item.get(field, "")).strip())
        status = str(item.get("status", ""))
        if missing or status not in allowed_statuses:
            bad_items.append({"id": item.get("id", ""), "missing": missing, "status": status})
    if bad_items:
        report.fail("completion_ledger_items", "ledger items must use required fields and allowed statuses only", bad_items=bad_items)
        return
    report.ok("completion_ledger_items", count=len(items), statuses=sorted({str(item.get("status")) for item in items}))


def check_sample_projects(report: BuildReport, *, run_validators: bool) -> None:
    sample_root = DOC_DIR / "sample_projects"
    projects = sorted(path for path in sample_root.iterdir() if path.is_dir() and (path / "scripts" / "validate_project.py").exists())
    if len(projects) < 3:
        report.fail("sample_projects", "expected at least three hand-built sample projects with validators", observed=[str(path) for path in projects])
        return
    if not run_validators:
        report.ok("sample_projects", count=len(projects), validators="not_run")
        return
    failures: list[dict[str, Any]] = []
    for project in projects:
        completed = subprocess.run(
            [sys.executable, str(project / "scripts" / "validate_project.py")],
            cwd=project,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            failures.append(
                {
                    "project": str(project),
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            )
    if failures:
        report.fail("sample_project_validators", "one or more sample validators failed", failures=failures)
    else:
        report.ok("sample_project_validators", count=len(projects))


def check_modification_planner(report: BuildReport) -> None:
    with tempfile.TemporaryDirectory(prefix="nexus-lab-build-") as tmp:
        tmp_path = Path(tmp)
        request_path = tmp_path / "modification_request.json"
        output_path = tmp_path / "modification_plan.json"
        request_path.write_text(
            json.dumps(
                {
                    "schema": "nexus.lab.modification_request.v1",
                    "loop_id": "build-check",
                    "iteration": 1,
                    "status": "needs_nexus_modification",
                    "rule": "Modify Nexus general mechanisms only.",
                    "case_policy": "All selected cases were run before this request was generated.",
                    "git": {},
                    "failing_problem_axes": [
                        {
                            "problem_id": "P06",
                            "counts": {"pass": 0, "fail": 1, "blocked": 0, "missing": 0},
                            "cases": [{"case_id": "build_check", "status": "fail", "observed": "missing project workspace"}],
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        completed = run_command(
            [
                sys.executable,
                str(SCRIPT_DIR / "plan_nexus_modification.py"),
                "--request",
                str(request_path),
                "--output",
                str(output_path),
            ]
        )
        if completed["returncode"] != 0 or not output_path.exists():
            report.fail("modification_planner", "planner command failed", command=completed)
            return
        plan = json.loads(output_path.read_text(encoding="utf-8"))
        if plan.get("status") != "ready_for_core_patch" or not plan.get("target_groups"):
            report.fail("modification_planner", "planner output is not actionable", plan=plan)
        else:
            report.ok("modification_planner", output_status=plan.get("status"), target_groups=len(plan.get("target_groups", [])))


def check_status_inspector(report: BuildReport) -> None:
    with tempfile.TemporaryDirectory(prefix="nexus-lab-status-") as tmp:
        tmp_path = Path(tmp)
        loop_dir = tmp_path / "lab-loops" / "build-check"
        loop_dir.mkdir(parents=True, exist_ok=True)
        state_path = loop_dir / "loop_state.json"
        stop_file = loop_dir / "STOP"
        state_path.write_text(
            json.dumps(
                {
                    "schema": "nexus.lab.loop_state.v1",
                    "loop_id": "build-check",
                    "status": "needs_modification_agent",
                    "started_at": utc_now(),
                    "finished_at": "",
                    "nexus_root": str(REPO_ROOT),
                    "e2e_root": str(tmp_path),
                    "loop_dir": str(loop_dir),
                    "stop_file": str(stop_file),
                    "iterations": [
                        {
                            "iteration": 1,
                            "status": "fail",
                            "summary_path": str(loop_dir / "iteration-01" / "iteration_summary.json"),
                            "modification_request_path": str(loop_dir / "iteration-01" / "modification_request.json"),
                            "modification_plan_path": str(loop_dir / "iteration-01" / "modification_plan.json"),
                            "case_results": [{"case_id": "build_check", "status": "fail"}],
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        inspect_result = run_command(
            [
                sys.executable,
                str(SCRIPT_DIR / "inspect_nexus_lab_status.py"),
                "--loop-state",
                str(state_path),
                "--json",
            ]
        )
        stop_result = run_command(
            [
                sys.executable,
                str(SCRIPT_DIR / "inspect_nexus_lab_status.py"),
                "--loop-state",
                str(state_path),
                "--request-stop",
                "--json",
            ]
        )
        if inspect_result["returncode"] != 0 or stop_result["returncode"] != 0 or not stop_file.exists():
            report.fail(
                "status_inspector",
                "status inspector or stop marker command failed",
                inspect_result=inspect_result,
                stop_result=stop_result,
                stop_file_exists=stop_file.exists(),
            )
            return
        summary = parse_json_stdout(inspect_result["stdout"])
        if summary.get("status") != "needs_modification_agent" or summary.get("current_iteration") != 1:
            report.fail("status_inspector", "status summary did not preserve loop state", summary=summary)
        else:
            report.ok("status_inspector", stop_marker="verified")


def check_dry_run_commands(report: BuildReport) -> None:
    commands = [
        [
            sys.executable,
            str(SCRIPT_DIR / "run_nexus_e2e_case.py"),
            "--cases-file",
            str(STRUCTURED_CASES),
            "--dry-run-plan",
        ],
        [
            sys.executable,
            str(SCRIPT_DIR / "run_nexus_e2e_case.py"),
            "--cases-file",
            str(REAL_CASES),
            "--dry-run-plan",
        ],
        [
            sys.executable,
            str(SCRIPT_DIR / "run_nexus_lab_loop.py"),
            "--cases-file",
            str(REAL_CASES),
            "--dry-run-plan",
        ],
        [
            sys.executable,
            str(SCRIPT_DIR / "run_nexus_verix_orchestrator.py"),
            "--cases-file",
            str(REAL_CASES),
            "--dry-run-plan",
        ],
        [
            sys.executable,
            str(SCRIPT_DIR / "init_monitor_multi_agent_run.py"),
            "--dry-run-plan",
        ],
        [
            sys.executable,
            "-m",
            "nexus.cli",
            "--root",
            str(REPO_ROOT),
            "nexus-verix-monitor",
            "--dry-run-plan",
        ],
        [
            sys.executable,
            str(SCRIPT_DIR / "run_nexus_skill_replay.py"),
            "--cases-file",
            str(REAL_CASES),
            "--case-id",
            "real_07_wljob_heat_local_sequence",
            "--include-wljob-command-file",
            "--dry-run-plan",
        ],
        [
            sys.executable,
            str(SCRIPT_DIR / "run_nexus_skill_replay.py"),
            "--cases-file",
            str(REAL_CASES),
            "--case-id",
            "real_08_sync_and_external_boundary_contract",
            "--dry-run-plan",
        ],
    ]
    failures: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for command in commands:
        completed = run_command(command)
        if completed["returncode"] != 0:
            failures.append(completed)
            continue
        parsed = parse_json_stdout(completed["stdout"])
        summaries.append(summarize_dry_run(command, parsed))
    if failures:
        report.fail("dry_run_commands", "one or more dry-run commands failed", failures=failures)
    else:
        report.ok("dry_run_commands", commands=summaries)


def run_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    return {
        "command": shlex.join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def parse_json_stdout(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def summarize_dry_run(command: list[str], payload: dict[str, Any]) -> dict[str, Any]:
    cases = payload.get("cases")
    if not isinstance(cases, list):
        cases = payload.get("selected_cases", [])
    steps = payload.get("steps", [])
    queued = [step for step in steps if isinstance(step, dict) and step.get("queued")]
    refused = [step for step in steps if isinstance(step, dict) and step.get("skip_reason") == "external_side_effect_refused_by_default"]
    return {
        "command": shlex.join(command),
        "schema": payload.get("schema"),
        "case_count": len(cases) if isinstance(cases, list) else 0,
        "skill_replay_step_count": len(steps) if isinstance(steps, list) else 0,
        "skill_replay_queued": len(queued),
        "skill_replay_refused_external": len(refused),
        "problem_count": payload.get("problem_matrix", {}).get("problem_count") or payload.get("problem_matrix", {}).get("problem_count"),
        "execution_model": payload.get("execution_model"),
        "satisfies_start_run_contract": payload.get("satisfies_start_run_contract"),
    }


def check_monitor_multi_agent_contract(report: BuildReport, schema: dict[str, Any], roles: dict[str, Any]) -> None:
    contract_text = MONITOR_CONTRACT.read_text(encoding="utf-8") if MONITOR_CONTRACT.exists() else ""
    required_agents = ["nexus_execution", "skill_replay", "verix_audit", "nexus_modification", "state_audit"]
    missing_in_contract = [agent for agent in required_agents if agent not in contract_text]
    if "orchestrated_subprocess_loop" not in contract_text or "不能满足" not in contract_text:
        report.fail("monitor_multi_agent_contract", "contract must explicitly reject subprocess loop as full start-run acceptance")
    elif missing_in_contract:
        report.fail("monitor_multi_agent_contract", "contract does not list all required monitor agents", missing=missing_in_contract)
    else:
        report.ok("monitor_multi_agent_contract", required_agents=required_agents)

    execution_model = schema.get("properties", {}).get("execution_model", {}).get("const")
    if execution_model != "monitor_multi_agent_v1":
        report.fail("monitor_state_schema", "schema must require monitor_multi_agent_v1", observed=execution_model)
    else:
        report.ok("monitor_state_schema_execution_model", execution_model=execution_model)

    role_items = [item for item in roles.get("roles", []) if isinstance(item, dict)]
    role_ids = [str(item.get("role_id", "")) for item in role_items]
    if role_ids != required_agents:
        report.fail("monitor_agent_roles", "roles must match required monitor agents in order", observed=role_ids, expected=required_agents)
    elif not all(item.get("spawn_required") is True for item in role_items):
        report.fail("monitor_agent_roles", "all monitor roles must require spawn")
    elif not all("heartbeat_status.json" in item.get("required_artifacts", []) for item in role_items):
        report.fail("monitor_agent_roles", "all monitor roles must require heartbeat_status.json")
    else:
        report.ok("monitor_agent_roles", role_count=len(role_items))

    completed = run_command(
        [
            sys.executable,
            str(SCRIPT_DIR / "run_nexus_verix_orchestrator.py"),
            "--dry-run-plan",
        ]
    )
    plan = parse_json_stdout(completed["stdout"])
    if completed["returncode"] != 0:
        report.fail("orchestrator_boundary_plan", "orchestrator dry-run failed", result=completed)
    elif plan.get("execution_model") != "orchestrated_subprocess_loop" or plan.get("satisfies_start_run_contract") is not False:
        report.fail(
            "orchestrator_boundary_plan",
            "subprocess orchestrator must not satisfy monitor-level start-run contract",
            execution_model=plan.get("execution_model"),
            satisfies_start_run_contract=plan.get("satisfies_start_run_contract"),
        )
    else:
        report.ok("orchestrator_boundary_plan", execution_model=plan.get("execution_model"), satisfies_start_run_contract=False)

    launch = run_command(
        [
            sys.executable,
            str(SCRIPT_DIR / "init_monitor_multi_agent_run.py"),
            "--dry-run-plan",
        ]
    )
    launch_plan = parse_json_stdout(launch["stdout"])
    if launch["returncode"] != 0:
        report.fail("monitor_multi_agent_launcher", "monitor launcher dry-run failed", result=launch)
    elif launch_plan.get("execution_model") != "monitor_multi_agent_v1":
        report.fail("monitor_multi_agent_launcher", "monitor launcher has wrong execution model", observed=launch_plan.get("execution_model"))
    else:
        report.ok("monitor_multi_agent_launcher", execution_model=launch_plan.get("execution_model"))

    cli_launch = run_command(
        [
            sys.executable,
            "-m",
            "nexus.cli",
            "--root",
            str(REPO_ROOT),
            "nexus-verix-monitor",
            "--dry-run-plan",
        ]
    )
    cli_launch_plan = parse_json_stdout(cli_launch["stdout"])
    if cli_launch["returncode"] != 0:
        report.fail("monitor_multi_agent_cli_entrypoint", "nexus-verix-monitor CLI dry-run failed", result=cli_launch)
    elif cli_launch_plan.get("execution_model") != "monitor_multi_agent_v1":
        report.fail("monitor_multi_agent_cli_entrypoint", "nexus-verix-monitor has wrong execution model", observed=cli_launch_plan.get("execution_model"))
    else:
        report.ok("monitor_multi_agent_cli_entrypoint", execution_model=cli_launch_plan.get("execution_model"))


def build_manifest(report: BuildReport, *, matrix: dict[str, Any], structured: dict[str, Any], real: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "nexus.lab.project_manifest.v1",
        "generated_at": utc_now(),
        "status": "pass" if not report.errors else "fail",
        "nexus_root": str(REPO_ROOT),
        "problem_matrix": {
            "path": str(PROBLEM_MATRIX),
            "source": matrix.get("source"),
            "problem_count": len(matrix.get("problems", [])) if isinstance(matrix.get("problems"), list) else 0,
            "coverage_policy": matrix.get("coverage_policy", {}),
        },
        "case_registries": [
            {
                "name": "structured",
                "path": str(STRUCTURED_CASES),
                "case_count": len(structured.get("cases", [])) if isinstance(structured.get("cases"), list) else 0,
            },
            {
                "name": "real_project",
                "path": str(REAL_CASES),
                "case_count": len(real.get("cases", [])) if isinstance(real.get("cases"), list) else 0,
            },
        ],
        "agents": {
            "execution_agent": str(SCRIPT_DIR / "run_nexus_e2e_case.py"),
            "evaluation_agent": str(SCRIPT_DIR / "evaluate_nexus_case.py"),
            "skill_replay_agent": str(SCRIPT_DIR / "run_nexus_skill_replay.py"),
            "loop_orchestrator": str(SCRIPT_DIR / "run_nexus_lab_loop.py"),
            "nexus_verix_orchestrator": str(SCRIPT_DIR / "run_nexus_verix_orchestrator.py"),
            "monitor_multi_agent_launcher": str(SCRIPT_DIR / "init_monitor_multi_agent_run.py"),
            "monitor_multi_agent_cli": "python -m nexus.cli --root <PROJECT_ROOT> nexus-verix-monitor --init-run",
            "monitor_artifact_merger": str(SCRIPT_DIR / "merge_monitor_agent_artifacts.py"),
            "modification_planner": str(SCRIPT_DIR / "plan_nexus_modification.py"),
            "monitor_status": str(SCRIPT_DIR / "inspect_nexus_lab_status.py"),
        },
        "monitor_multi_agent_contract": {
            "contract": str(MONITOR_CONTRACT),
            "state_schema": str(MONITOR_STATE_SCHEMA),
            "roles": str(MONITOR_AGENT_ROLES),
            "completion_ledger": str(COMPLETION_LEDGER),
            "execution_model": "monitor_multi_agent_v1",
        },
        "checks": report.checks,
        "warnings": report.warnings,
        "errors": report.errors,
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
