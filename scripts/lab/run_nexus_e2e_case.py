#!/usr/bin/env python3
"""Run Nexus lab E2E cases through the real Nexus CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_FILE = Path(__file__).with_name("nexus_lab_cases.json")
DEFAULT_PROBLEM_MATRIX_FILE = Path(__file__).with_name("nexus_problem_matrix.json")

PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
RUN_ID_RE = re.compile(r"\brun_id\s*[：:]\s*([A-Za-z0-9_.-]+)")
HANDOFF_ID_RE = re.compile(r"(?:\bhandoff_id\b|--handoff-id)\s*[：:= ]\s*([A-Za-z0-9_.-]+)")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.execute and args.dry_run_plan:
        parser.error("--execute and --dry-run-plan are mutually exclusive")

    try:
        problem_matrix = load_problem_matrix(args.problem_matrix)
        cases_payload = apply_problem_matrix(load_cases(args.cases_file), problem_matrix, include_suffix=not args.no_problem_suffix)
        selected_cases = select_cases(cases_payload, args.case_id)
        context = build_context(args)
        provider_config = resolve_provider_config(args)
        source_consumption = build_source_consumption(args, provider_config)
    except ValueError as exc:
        parser.error(str(exc))

    if not args.execute:
        plan = build_plan(selected_cases, context, args.nexus_root.expanduser().resolve(), provider_config=provider_config, source_consumption=source_consumption)
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if len(selected_cases) != 1:
        parser.error("--execute requires exactly one --case-id")

    return execute_case(
        selected_cases[0],
        context,
        args.nexus_root.expanduser().resolve(),
        allow_external_side_effects=args.allow_external_side_effects,
        provider_config=provider_config,
        source_consumption=source_consumption,
        preserve_codex_home=args.preserve_codex_home,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or execute one Nexus lab E2E case.")
    parser.add_argument("--case-id", action="append", default=[], help="case id to plan or execute; repeat for dry-run planning")
    parser.add_argument("--e2e-root", type=Path, default=Path("/tmp/nexus-e2e-lab"), help="isolated root for lab projects, fixtures, CODEX_HOME, and harness results")
    parser.add_argument("--nexus-root", type=Path, default=REPO_ROOT, help="Nexus checkout whose CLI is invoked")
    parser.add_argument(
        "--provider-config-root",
        type=Path,
        help="Nexus root whose .data/config/provider.json and .data/config/models are inherited; defaults to NEXUS_LAB_PROVIDER_CONFIG_ROOT or --nexus-root",
    )
    parser.add_argument("--no-inherit-provider-config", action="store_true", help="do not inherit real Nexus provider/model configuration into this lab execution")
    parser.add_argument("--preserve-codex-home", action="store_true", help="inherit the caller CODEX_HOME instead of using an isolated lab CODEX_HOME")
    parser.add_argument("--dry-run-plan", action="store_true", help="print the execution plan without creating files or running commands")
    parser.add_argument("--execute", action="store_true", help="run the selected case through python -m nexus.cli")
    parser.add_argument("--allow-external-side-effects", action="store_true", help="allow GitHub/Feishu/public-sync style steps to run for cases that declare them")
    parser.add_argument("--cases-file", type=Path, default=DEFAULT_CASES_FILE, help="case registry JSON")
    parser.add_argument("--problem-matrix", type=Path, default=DEFAULT_PROBLEM_MATRIX_FILE, help="global Nexus problem matrix applied to every case")
    parser.add_argument("--no-problem-suffix", action="store_true", help="do not append the global problem suffix to case prompts")
    parser.add_argument("--var", action="append", default=[], metavar="KEY=VALUE", help="placeholder override, for example --var PROJECT_PATH=/tmp/project")
    return parser


def load_cases(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"case registry not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid case registry JSON: {exc}") from exc
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("case registry must contain a cases list")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    duplicate_ids = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicate_ids:
        raise ValueError(f"duplicate case ids: {', '.join(str(item) for item in duplicate_ids)}")
    return payload


def load_problem_matrix(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"problem matrix not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid problem matrix JSON: {exc}") from exc
    problems = payload.get("problems")
    if not isinstance(problems, list) or not problems:
        raise ValueError("problem matrix must contain a non-empty problems list")
    ids = [str(problem.get("id", "")) for problem in problems if isinstance(problem, dict)]
    if len(ids) != len(set(ids)):
        raise ValueError("problem matrix contains duplicate problem ids")
    return payload


def apply_problem_matrix(payload: dict[str, Any], matrix: dict[str, Any], *, include_suffix: bool) -> dict[str, Any]:
    problem_ids = [str(problem.get("id", "")) for problem in matrix.get("problems", []) if isinstance(problem, dict)]
    common_suffix = str(matrix.get("common_prompt_suffix", "")).strip() if include_suffix else ""
    payload["_problem_matrix"] = {
        "source": matrix.get("source", ""),
        "coverage_policy": matrix.get("coverage_policy", {}),
        "problem_ids": problem_ids,
        "common_prompt_suffix": common_suffix,
    }
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case["_problem_axes"] = problem_ids
        case["_problem_matrix_source"] = matrix.get("source", "")
        case["_coverage_policy"] = matrix.get("coverage_policy", {})
        case["_common_prompt_suffix"] = common_suffix
        case["_original_problem_targets"] = list(case.get("problem_targets", [])) if isinstance(case.get("problem_targets"), list) else []
        case["problem_targets"] = problem_ids
    return payload


def select_cases(payload: dict[str, Any], case_ids: list[str]) -> list[dict[str, Any]]:
    cases = [case for case in payload["cases"] if isinstance(case, dict)]
    if not case_ids:
        return cases
    by_id = {str(case.get("id")): case for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise ValueError(f"unknown case id(s): {', '.join(missing)}")
    return [by_id[case_id] for case_id in case_ids]


def build_context(args: argparse.Namespace) -> dict[str, str]:
    e2e_root = args.e2e_root.expanduser().resolve()
    nexus_root = args.nexus_root.expanduser().resolve()
    context = {
        "E2E_ROOT": str(e2e_root),
        "NEXUS_ROOT": str(nexus_root),
        "PROJECT_PATH": str(e2e_root / "projects" / "project-under-test"),
        "FIXTURE_DIR": str(e2e_root / "fixtures"),
    }
    for item in args.var:
        if "=" not in item:
            raise ValueError(f"--var must use KEY=VALUE syntax: {item}")
        key, value = item.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"invalid placeholder key: {key}")
        context[key] = value
    return context


def build_plan(
    cases: list[dict[str, Any]],
    context: dict[str, str],
    nexus_root: Path,
    *,
    provider_config: dict[str, Any],
    source_consumption: dict[str, Any],
) -> dict[str, Any]:
    planned_cases = []
    for case in cases:
        scoped_context = build_case_context(case, context)
        case_context = case_result_context(case, scoped_context)
        planned_steps = []
        for step in case.get("steps", []):
            rendered = render_step(step, scoped_context, nexus_root, common_suffix=str(case.get("_common_prompt_suffix", "")))
            external_reasons = external_side_effect_reasons(case, step, rendered["argv"])
            planned_steps.append(
                {
                    "id": step.get("id", ""),
                    "prompt": rendered["prompt"],
                    "command": rendered["command"],
                    "cwd": str(nexus_root),
                    "unresolved_placeholders": rendered["unresolved_placeholders"],
                    "global_problem_suffix_applied": rendered["global_problem_suffix_applied"],
                    "requires_external_side_effect": bool(external_reasons),
                    "external_side_effect_reasons": external_reasons,
                }
            )
        planned_cases.append(
            {
                "id": case.get("id", ""),
                "title": case.get("title", ""),
                "project_path": case_context.get("project_path", ""),
                "expected_behavior": case_context.get("expected_behavior", ""),
                "pass_requirements": case_context.get("pass_requirements", []),
                "problem_axes": case_context.get("problem_axes", []),
                "problem_matrix_source": case_context.get("problem_matrix_source", ""),
                "coverage_policy": case_context.get("coverage_policy", {}),
                "requires_external_side_effect": bool(case.get("requires_external_side_effect")),
                "steps": planned_steps,
            }
        )
    return {
        "schema": "nexus.lab.e2e_plan.v1",
        "mode": "dry_run_plan",
        "generated_at": utc_now(),
        "nexus_root": str(nexus_root),
        "e2e_root": context["E2E_ROOT"],
        "provider_config": provider_config,
        "source_consumption": source_consumption,
        "note": "No files are created and no Nexus command is executed unless --execute is used.",
        "problem_matrix": {
            "source": cases[0].get("_problem_matrix_source", "") if cases else "",
            "coverage_policy": cases[0].get("_coverage_policy", {}) if cases else {},
            "case_policy": "Every selected case carries the same global Nexus problem axes.",
        },
        "cases": planned_cases,
    }


def execute_case(
    case: dict[str, Any],
    context: dict[str, str],
    nexus_root: Path,
    *,
    allow_external_side_effects: bool,
    provider_config: dict[str, Any],
    source_consumption: dict[str, Any],
    preserve_codex_home: bool,
) -> int:
    if not nexus_root.exists():
        print(f"blocked: nexus root not found: {nexus_root}", file=sys.stderr)
        return 2

    context = build_case_context(case, context)
    case_id = str(case.get("id") or "unknown_case")
    timestamp = timestamp_slug()
    e2e_root = Path(context["E2E_ROOT"])
    result_dir = e2e_root / "case-runs" / case_id / timestamp
    result_dir.mkdir(parents=True, exist_ok=True)
    result_path = result_dir / "execution.json"

    result: dict[str, Any] = {
        "schema": "nexus.lab.e2e_execution.v1",
        "case_id": case_id,
        "title": case.get("title", ""),
        **case_result_context(case, context),
        "started_at": utc_now(),
        "finished_at": "",
        "status": "running",
        "nexus_root": str(nexus_root),
        "e2e_root": context["E2E_ROOT"],
        "result_dir": str(result_dir),
        "provider_config": provider_config,
        "environment": {},
        "source_consumption": source_consumption,
        "allow_external_side_effects": allow_external_side_effects,
        "fixtures": [],
        "prepared_directories": [],
        "steps": [],
        "context_updates": {},
    }

    def save() -> None:
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    save()
    prepare_execution_directories(context, result)
    provider_config["runtime_status"] = provider_config_runtime_status(provider_config, nexus_root)
    result["provider_config"] = provider_config
    save()

    try:
        prepare_fixtures(case, context, result)
    except RuntimeError as exc:
        result["status"] = "blocked_fixture_conflict"
        result["finished_at"] = utc_now()
        result["error"] = str(exc)
        save()
        print(str(result_path))
        return 2
    save()

    for step in case.get("steps", []):
        rendered = render_step(step, context, nexus_root, common_suffix=str(case.get("_common_prompt_suffix", "")))
        step_record: dict[str, Any] = {
            "id": step.get("id", ""),
            "prompt": rendered["prompt"],
            "command": rendered["command"],
            "argv": rendered["argv"],
            "cwd": str(nexus_root),
            "timestamp": utc_now(),
            "stdout": "",
            "stderr": "",
            "returncode": None,
            "run_id": "",
            "artifact_refs": [],
            "status": "planned",
            "global_problem_suffix_applied": rendered["global_problem_suffix_applied"],
        }
        result["steps"].append(step_record)

        if rendered["unresolved_placeholders"]:
            step_record["status"] = "blocked_unresolved_placeholders"
            step_record["unresolved_placeholders"] = rendered["unresolved_placeholders"]
            result["status"] = "blocked_unresolved_placeholders"
            result["finished_at"] = utc_now()
            save()
            print(str(result_path))
            return 2

        if uses_mock_provider(rendered["argv"]):
            step_record["status"] = "refused_mock_provider"
            result["status"] = "refused_mock_provider"
            result["finished_at"] = utc_now()
            save()
            print(str(result_path))
            return 3

        external_reasons = external_side_effect_reasons(case, step, rendered["argv"])
        if external_reasons and not allow_external_side_effects:
            step_record["status"] = "refused_external_side_effect"
            step_record["external_side_effect_reasons"] = external_reasons
            result["status"] = "refused_external_side_effect"
            result["finished_at"] = utc_now()
            save()
            print(str(result_path))
            return 3

        env, env_audit = build_lab_environment(context, provider_config=provider_config, preserve_codex_home=preserve_codex_home)
        env["NEXUS_LAB_CASE_ID"] = case_id
        result["environment"] = env_audit

        completed = subprocess.run(
            rendered["argv"],
            cwd=nexus_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        step_record["stdout"] = completed.stdout
        step_record["stderr"] = completed.stderr
        step_record["returncode"] = completed.returncode

        run_id = parse_run_id(completed.stdout) or context.get("RUN_ID", "")
        if run_id:
            context["RUN_ID"] = run_id
            step_record["run_id"] = run_id
            step_record["artifact_refs"] = collect_artifact_refs(nexus_root, run_id)
            resolved = resolve_final_project_path(nexus_root, run_id, context, step_record["artifact_refs"])
            if resolved is not None:
                resolved_project, resolved_source = resolved
                context["PROJECT_PATH"] = str(resolved_project)
                context["RESOLVED_PROJECT_PATH"] = str(resolved_project)
                result["project_path"] = str(resolved_project)
                result["resolved_project_path"] = str(resolved_project)
                result["resolved_project_path_source"] = resolved_source
            handoff_id = parse_handoff_id(nexus_root, run_id, completed.stdout)
            if handoff_id:
                context["HANDOFF_ID"] = handoff_id

        step_record["status"] = "completed" if completed.returncode == 0 else "failed"
        result["context_updates"] = {key: context[key] for key in ("RUN_ID", "HANDOFF_ID", "PROJECT_PATH", "RESOLVED_PROJECT_PATH") if context.get(key)}
        save()

        if completed.returncode != 0:
            result["status"] = "failed"
            result["finished_at"] = utc_now()
            save()
            print(str(result_path))
            return completed.returncode or 1

    result["status"] = "completed"
    result["finished_at"] = utc_now()
    save()
    print(str(result_path))
    return 0


def case_result_context(case: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
    project_path = str(case.get("project_path") or context.get("PROJECT_PATH") or "")
    raw_intent = str(case.get("raw_intent") or "")
    if not raw_intent:
        prompts = [str(step.get("prompt", "")) for step in case.get("steps", []) if isinstance(step, dict)]
        raw_intent = "\n\n".join(prompt for prompt in prompts if prompt)
    common_suffix = str(case.get("_common_prompt_suffix", ""))
    if common_suffix:
        raw_intent = append_common_suffix(raw_intent, common_suffix)
    payload: dict[str, Any] = {
        "project_path": substitute(project_path, context) if project_path else "",
        "raw_intent": substitute(raw_intent, context) if raw_intent else "",
        "expected_behavior": substitute(str(case.get("expected_behavior", "")), context),
        "pass_requirements": [
            substitute(str(item), context)
            for item in case.get("pass_requirements", [])
            if str(item)
        ],
        "source_projects": case.get("source_projects", []),
        "problem_targets": case.get("problem_targets", []),
        "original_problem_targets": case.get("_original_problem_targets", []),
        "problem_axes": case.get("_problem_axes", []),
        "problem_matrix_source": case.get("_problem_matrix_source", ""),
        "coverage_policy": case.get("_coverage_policy", {}),
    }
    return payload


def build_case_context(case: dict[str, Any], context: dict[str, str]) -> dict[str, str]:
    scoped = dict(context)
    project_template = str(case.get("project_path") or "")
    if project_template:
        scoped["PROJECT_PATH"] = substitute(project_template, scoped)
    return scoped


def prepare_execution_directories(context: dict[str, str], result: dict[str, Any]) -> None:
    directories: list[Path] = [
        Path(context["E2E_ROOT"]),
        Path(context["FIXTURE_DIR"]),
        Path(context["E2E_ROOT"]) / "codex_home",
    ]
    project_path = Path(context["PROJECT_PATH"])
    directories.append(project_path.parent)
    prepared: list[str] = []
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        prepared.append(str(directory))
    result["prepared_directories"] = dedupe(prepared)


def resolve_provider_config(args: argparse.Namespace) -> dict[str, Any]:
    nexus_root = args.nexus_root.expanduser().resolve()
    env_root = os.environ.get("NEXUS_LAB_PROVIDER_CONFIG_ROOT", "").strip() or os.environ.get("NEXUS_PROVIDER_CONFIG_ROOT", "").strip()
    if args.no_inherit_provider_config:
        source_root = nexus_root
        source = "disabled"
        inherit_enabled = False
    elif args.provider_config_root is not None:
        source_root = args.provider_config_root.expanduser().resolve()
        source = "explicit_argument"
        inherit_enabled = True
    elif env_root:
        source_root = Path(env_root).expanduser().resolve()
        source = "environment"
        inherit_enabled = True
    elif (nexus_root / ".data" / "config" / "models").exists():
        source_root = nexus_root
        source = "nexus_root"
        inherit_enabled = True
    else:
        source_root = REPO_ROOT
        source = "current_repo_default"
        inherit_enabled = True
    models_dir = source_root / ".data" / "config" / "models"
    provider_path = source_root / ".data" / "config" / "provider.json"
    return {
        "schema": "nexus.lab.provider_config.v1",
        "inherit_enabled": inherit_enabled,
        "source": source,
        "source_root": str(source_root),
        "target_root": str(nexus_root),
        "models_dir": str(models_dir),
        "models_exists": models_dir.exists(),
        "provider_config_path": str(provider_path),
        "provider_config_exists": provider_path.exists(),
        "secrets_copied": False,
        "policy": "Provider/model config is read from source_root via NEXUS_PROVIDER_CONFIG_ROOT; project outputs remain isolated under E2E_ROOT.",
    }


def provider_config_runtime_status(provider_config: dict[str, Any], nexus_root: Path) -> dict[str, Any]:
    if not provider_config.get("inherit_enabled"):
        return {"status": "disabled", "reason": "--no-inherit-provider-config"}
    source_root = Path(str(provider_config.get("source_root") or "")).expanduser().resolve()
    target_root = nexus_root.expanduser().resolve()
    if not provider_config.get("models_exists"):
        return {"status": "missing", "reason": "source .data/config/models not found"}
    mode = "native_nexus_root" if source_root == target_root else "external_config_root"
    return {
        "status": "ready",
        "mode": mode,
        "env_var": "NEXUS_PROVIDER_CONFIG_ROOT",
        "source_root": str(source_root),
        "target_root": str(target_root),
    }


def build_lab_environment(
    context: dict[str, str],
    *,
    provider_config: dict[str, Any],
    preserve_codex_home: bool,
) -> tuple[dict[str, str], dict[str, Any]]:
    env = os.environ.copy()
    codex_home = env.get("CODEX_HOME", "")
    codex_home_mode = "inherited"
    if not preserve_codex_home:
        codex_home = str(Path(context["E2E_ROOT"]) / "codex_home")
        env["CODEX_HOME"] = codex_home
        codex_home_mode = "isolated_lab"
    env["NEXUS_WORKFLOW_SURFACE"] = "codex"
    env["NEXUS_NEXT_PROMPT_MODE"] = "workflow"
    env["NEXUS_LAB_E2E_ROOT"] = context["E2E_ROOT"]
    env["NEXUS_LAB_PROVIDER_CONFIG_ROOT"] = str(provider_config.get("source_root") or "")
    env["NEXUS_LAB_PROVIDER_MODELS_DIR"] = str(provider_config.get("models_dir") or "")
    env["NEXUS_LAB_PROVIDER_CONFIG_INHERITED"] = "1" if provider_config.get("inherit_enabled") else "0"
    env["NEXUS_PROVIDER_CONFIG_ROOT"] = str(provider_config.get("source_root") or "") if provider_config.get("inherit_enabled") else ""
    skipped = normalize_skip_providers(env.get("NEXUS_AUTO_SKIP_PROVIDERS", ""))
    return env, {
        "schema": "nexus.lab.execution_environment.v1",
        "CODEX_HOME_mode": codex_home_mode,
        "CODEX_HOME": codex_home,
        "NEXUS_WORKFLOW_SURFACE": env["NEXUS_WORKFLOW_SURFACE"],
        "NEXUS_NEXT_PROMPT_MODE": env["NEXUS_NEXT_PROMPT_MODE"],
        "NEXUS_LAB_E2E_ROOT": env["NEXUS_LAB_E2E_ROOT"],
        "NEXUS_PROVIDER_CONFIG_ROOT": env["NEXUS_PROVIDER_CONFIG_ROOT"],
        "NEXUS_LAB_PROVIDER_CONFIG_ROOT": env["NEXUS_LAB_PROVIDER_CONFIG_ROOT"],
        "NEXUS_LAB_PROVIDER_MODELS_DIR": env["NEXUS_LAB_PROVIDER_MODELS_DIR"],
        "NEXUS_LAB_PROVIDER_CONFIG_INHERITED": env["NEXUS_LAB_PROVIDER_CONFIG_INHERITED"],
        "NEXUS_AUTO_SKIP_PROVIDERS": env.get("NEXUS_AUTO_SKIP_PROVIDERS", ""),
        "skipped_providers": skipped,
        "codex_mcp_skip_active": "codex-mcp" in skipped,
        "skip_semantics": "NEXUS_AUTO_SKIP_PROVIDERS removes named providers from auto candidates; codex-mcp skip is an operator-selected fallback, not a terminal blocker.",
    }


def build_source_consumption(args: argparse.Namespace, provider_config: dict[str, Any]) -> dict[str, Any]:
    sources = [
        {
            "path": str(args.cases_file.expanduser().resolve()),
            "status": "read",
            "used_for": "case registry and command templates",
        },
        {
            "path": str(args.problem_matrix.expanduser().resolve()),
            "status": "read",
            "used_for": "global Nexus problem axes and common suffix",
        },
        {
            "path": str(Path(str(provider_config.get("models_dir") or "")).expanduser()),
            "status": "available" if provider_config.get("models_exists") else "missing",
            "used_for": "real provider/model profile inheritance",
        },
        {
            "path": str(Path(str(provider_config.get("provider_config_path") or "")).expanduser()),
            "status": "available" if provider_config.get("provider_config_exists") else "missing",
            "used_for": "provider priority and locale config inheritance",
        },
    ]
    return {
        "schema": "nexus.lab.source_consumption.v1",
        "sources": sources,
    }


def normalize_skip_providers(raw: str) -> list[str]:
    aliases = {"codexcli": "codex-cli", "codexmcp": "codex-mcp"}
    return sorted({aliases.get(item.strip().lower(), item.strip().lower()) for item in raw.split(",") if item.strip()})


def render_step(step: dict[str, Any], context: dict[str, str], nexus_root: Path, *, common_suffix: str = "") -> dict[str, Any]:
    prompt = substitute(str(step.get("prompt", "")), context)
    raw_args = step.get("nexus_args", [])
    if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
        raise ValueError(f"step {step.get('id', '')} must contain a string nexus_args list")
    nexus_args = [substitute(item, context) for item in raw_args]
    suffix_applied = False
    if should_apply_common_suffix(step, nexus_args, common_suffix):
        prompt = append_common_suffix(prompt, common_suffix)
        nexus_args = append_common_suffix_to_nexus_args(nexus_args, common_suffix)
        suffix_applied = True
    argv = [
        sys.executable,
        "-m",
        "nexus.cli",
        "--root",
        str(nexus_root),
        "--next-prompt-mode",
        "workflow",
        "--workflow-surface",
        "codex",
        *nexus_args,
    ]
    unresolved = sorted(set(find_placeholders(prompt) + find_placeholders(nexus_args)))
    return {
        "prompt": prompt,
        "argv": argv,
        "command": display_command(nexus_root, argv),
        "unresolved_placeholders": unresolved,
        "global_problem_suffix_applied": suffix_applied,
    }


def should_apply_common_suffix(step: dict[str, Any], nexus_args: list[str], common_suffix: str) -> bool:
    if not common_suffix:
        return False
    if step.get("apply_global_problem_suffix") is False:
        return False
    if not nexus_args:
        return False
    command = nexus_args[0]
    return command in {"init-project", "supplement-init", "research", "invoke"}


def append_common_suffix(text: str, suffix: str) -> str:
    if not suffix or suffix in text:
        return text
    return text.rstrip() + "\n\n全局 Nexus 问题验收： " + suffix


def append_common_suffix_to_nexus_args(nexus_args: list[str], suffix: str) -> list[str]:
    if not nexus_args:
        return nexus_args
    updated = list(nexus_args)
    command = updated[0]
    if command in {"init-project", "research", "invoke"} and len(updated) > 1:
        updated[1] = append_common_suffix(updated[1], suffix)
    if command == "supplement-init" and "--idea" in updated:
        index = updated.index("--idea") + 1
        if index < len(updated):
            updated[index] = append_common_suffix(updated[index], suffix)
    if "--raw-user-request" in updated:
        index = updated.index("--raw-user-request") + 1
        if index < len(updated):
            updated[index] = append_common_suffix(updated[index], suffix)
    return updated


def substitute(value: str, context: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return context.get(key, match.group(0))

    return PLACEHOLDER_RE.sub(replace, value)


def find_placeholders(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.extend(PLACEHOLDER_RE.findall(value))
    elif isinstance(value, list):
        for item in value:
            found.extend(find_placeholders(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(find_placeholders(item))
    return found


def display_command(cwd: Path, argv: list[str]) -> str:
    return f"cd {shlex.quote(str(cwd))} && {shlex.join(argv)}"


def external_side_effect_reasons(case: dict[str, Any], step: dict[str, Any], argv: list[str]) -> list[str]:
    reasons: list[str] = []
    if case.get("requires_external_side_effect"):
        reasons.append("case_requires_external_side_effect")
    if step.get("requires_external_side_effect"):
        reasons.append("step_requires_external_side_effect")
    reasons.extend(detect_external_command(argv))
    return sorted(set(reasons))


def detect_external_command(argv: list[str]) -> list[str]:
    reasons: list[str] = []
    if "github-sync" in argv:
        index = argv.index("github-sync")
        subcommand = argv[index + 1] if index + 1 < len(argv) else ""
        if subcommand in {"bootstrap", "private", "auto-private", "public", "publish-guide-feishu"}:
            reasons.append(f"github-sync:{subcommand}")
    if "self-sync" in argv:
        reasons.append("self-sync")
    if "guide" in argv:
        index = argv.index("guide")
        subcommand = argv[index + 1] if index + 1 < len(argv) else ""
        if subcommand in {"publish-feishu", "sync"}:
            reasons.append(f"guide:{subcommand}")
    if "system-showcase" in argv:
        index = argv.index("system-showcase")
        subcommand = argv[index + 1] if index + 1 < len(argv) else ""
        if subcommand == "publish-feishu":
            reasons.append("system-showcase:publish-feishu")
    if "feishu" in argv:
        index = argv.index("feishu")
        subcommand = argv[index + 1] if index + 1 < len(argv) else ""
        reasons.append(f"feishu:{subcommand or 'unknown'}")
    return reasons


def uses_mock_provider(argv: list[str]) -> bool:
    for index, arg in enumerate(argv):
        if arg == "--provider" and index + 1 < len(argv) and argv[index + 1] == "mock":
            return True
        if arg == "--provider=mock":
            return True
    return False


def prepare_fixtures(case: dict[str, Any], context: dict[str, str], result: dict[str, Any]) -> None:
    fixture_root = Path(context["FIXTURE_DIR"])
    for fixture in case.get("fixtures", []):
        if not isinstance(fixture, dict):
            continue
        rel_path = Path(str(fixture.get("path", "")))
        if rel_path.is_absolute() or ".." in rel_path.parts or not str(rel_path):
            raise RuntimeError(f"unsafe fixture path: {rel_path}")
        content = substitute(str(fixture.get("content", "")), context)
        target = fixture_root / rel_path
        if target.exists():
            existing = target.read_text(encoding="utf-8", errors="ignore")
            if existing != content:
                raise RuntimeError(f"fixture exists with different content: {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        result["fixtures"].append(str(target))


def parse_run_id(stdout: str) -> str:
    matches = RUN_ID_RE.findall(stdout)
    return matches[-1] if matches else ""


def parse_handoff_id(nexus_root: Path, run_id: str, stdout: str) -> str:
    interaction = read_json(nexus_root / ".data" / "runs" / run_id / "interaction.json")
    debug_handoff = interaction.get("debug_handoff") if isinstance(interaction, dict) else {}
    if isinstance(debug_handoff, dict) and debug_handoff.get("handoff_id"):
        return str(debug_handoff["handoff_id"])
    handoff = read_json(nexus_root / ".data" / "runs" / run_id / "handoffs" / "debug_handoff.json")
    if isinstance(handoff, dict) and handoff.get("handoff_id"):
        return str(handoff["handoff_id"])
    matches = HANDOFF_ID_RE.findall(stdout)
    return matches[-1] if matches else ""


def collect_artifact_refs(nexus_root: Path, run_id: str) -> list[str]:
    run_dir = nexus_root / ".data" / "runs" / run_id
    refs: list[str] = []
    for rel in ("input.json", "state.json", "interaction.json"):
        path = run_dir / rel
        if path.exists():
            refs.append(str(path))
    interaction = read_json(run_dir / "interaction.json")
    artifact_refs = interaction.get("artifact_refs") if isinstance(interaction, dict) else []
    if isinstance(artifact_refs, list):
        refs.extend(str(item) for item in artifact_refs if str(item))
    return dedupe(refs)


PROJECT_PATH_KEYS = {
    "project_path",
    "project_root",
    "target_path",
    "target_project",
    "target_project_path",
    "target_project_root",
    "final_project_path",
    "resolved_project_path",
    "created_path",
    "playbook_path",
    "records_path",
}


def resolve_final_project_path(nexus_root: Path, run_id: str, context: dict[str, str], artifact_refs: list[str]) -> tuple[Path, str] | None:
    run_dir = nexus_root / ".data" / "runs" / run_id
    candidates: list[tuple[str, object]] = []
    for rel, keys in [
        ("approvals/APPROVED_project-root.json", {"created_path"}),
        ("approvals/project_root_required.json", {"target_path", "project_path"}),
        ("state.json", {"target_path", "final_project_path", "project_path"}),
        ("interaction.json", {"final_project_path", "resolved_project_path", "project_path"}),
        ("input.json", {"project_path", "target_project_path"}),
    ]:
        payload = read_json(run_dir / rel)
        for key, value in deep_path_items(payload):
            if key in keys:
                candidates.append((f"{rel}:{key}", value))
    for ref in artifact_refs:
        candidates.append(("artifact_ref", ref))
    tool_results = run_dir / "tool_results"
    if tool_results.exists():
        for path in sorted(tool_results.glob("*.json")):
            payload = read_json(path)
            for key, value in deep_path_items(payload):
                if key in PROJECT_PATH_KEYS or key.endswith("_path") or key.endswith("_root"):
                    candidates.append((f"tool_results/{path.name}:{key}", value))
    projects_root = Path(context.get("E2E_ROOT", "")) / "projects" if context.get("E2E_ROOT") else None
    if projects_root is not None and projects_root.exists():
        project_children = [path for path in sorted(projects_root.iterdir()) if path.is_dir() and looks_like_project_root(path, nexus_root)]
        if len(project_children) == 1:
            candidates.append(("e2e_projects_single_project_marker", project_children[0]))
    candidates.append(("context.PROJECT_PATH", context.get("PROJECT_PATH", "")))

    seen: set[str] = set()
    for source, value in candidates:
        for candidate in project_candidates_from_value(value):
            resolved = candidate.expanduser().resolve()
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            if looks_like_project_root(resolved, nexus_root):
                return resolved, source
    return None


def deep_path_items(value: object) -> list[tuple[str, object]]:
    items: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str):
                items.append((key, nested))
            items.extend(deep_path_items(nested))
    elif isinstance(value, list):
        for nested in value:
            items.extend(deep_path_items(nested))
    return items


def project_candidates_from_value(value: object) -> list[Path]:
    text = str(value or "").strip()
    if not text:
        return []
    path = Path(text)
    candidates = [path]
    parts = path.parts
    if ".nexus" in parts:
        candidates.append(Path(*parts[: parts.index(".nexus")]))
    if "docs" in parts:
        docs_index = parts.index("docs")
        if docs_index > 0:
            candidates.append(Path(*parts[:docs_index]))
    return candidates


def looks_like_project_root(path: Path, nexus_root: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    if path == nexus_root.expanduser().resolve():
        return False
    markers = [
        path / ".nexus" / "project-intent.json",
        path / ".nexus" / "board.md",
        path / ".nexus" / "recovery-playbook.json",
        path / "docs" / "intent" / "original-requirement.md",
        path / "docs" / "intent" / "normalized-requirement.md",
        path / "docs" / "project-overview.md",
        path / "docs" / "recovery-records.md",
    ]
    return any(marker.exists() for marker in markers)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
