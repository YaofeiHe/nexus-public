#!/usr/bin/env python3
"""Build an actionable Nexus core modification plan from a lab request.

This script does not patch Nexus by itself. It turns the evaluator's
modification_request.json into a concrete, auditable plan for the monitor or an
external modification agent. The loop runner can write this plan after every
failed iteration before any core-code edit is attempted.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]

PROBLEM_TARGETS: dict[str, dict[str, Any]] = {
    "P01": {
        "priority": 20,
        "target_files": ["nexus/runner.py", "nexus/interaction.py", "nexus/recovery.py", "tests/test_runner.py"],
        "target_functions": [
            "Runner.execute_code_change",
            "Runner.handoff_for_debug",
            "Runner.append_debug_worklog",
            "Runner.rebind_and_continue",
            "Runner._finish_debug_rebind",
        ],
        "implementation_goal": "Make external debug handoff and rebind a same-run continuation path with explicit interaction/state evidence.",
        "verification_commands": [
            "python3 -m pytest tests/test_runner.py -k 'debug or rebind or recovery'",
        ],
    },
    "P02": {
        "priority": 19,
        "target_files": ["nexus/recovery.py", "nexus/runner.py", "tests/test_runner.py"],
        "target_functions": [
            "match_recovery_playbook",
            "related_recovery_experience",
            "write_recovery_artifacts",
            "Runner._write_debug_recovery_result_if_possible",
            "Runner._write_recovery_playbook_approval_if_completed",
        ],
        "implementation_goal": "Persist successful recovery into the project playbook and look it up before later similar recovery paths.",
        "verification_commands": [
            "python3 -m pytest tests/test_runner.py -k 'recovery or playbook'",
        ],
    },
    "P03": {
        "priority": 16,
        "target_files": ["nexus/project_docs.py", "nexus/runner.py", "nexus/user_prompts.py", "tests/test_board_and_init.py"],
        "target_functions": ["write_project_docs", "Runner.init_project", "Runner.supplemental_init"],
        "implementation_goal": "Promote repeated user constraints into normalized requirements, trace records, and next-prompt state.",
        "verification_commands": [
            "python3 -m pytest tests/test_board_and_init.py -k 'init or supplement'",
        ],
    },
    "P04": {
        "priority": 15,
        "target_files": ["nexus/runner.py", "nexus/github_sync.py", "nexus/feishu_autosync.py", "tests/test_manager_github_showcase_feishu.py"],
        "target_functions": [
            "Runner.supplemental_init",
            "Runner._self_sync_flow",
            "Runner._post_change_autosync",
            "Runner._github_public_flow",
        ],
        "implementation_goal": "Keep init, supplement-init, self-sync, post-change sync, and public sync on a shared auditable sync contract.",
        "verification_commands": [
            "python3 -m pytest tests/test_manager_github_showcase_feishu.py -k 'sync or github or feishu'",
        ],
    },
    "P05": {
        "priority": 15,
        "target_files": ["nexus/runner.py", "nexus/github_sync.py", "nexus/feishu_autosync.py", "tests/test_manager_github_showcase_feishu.py"],
        "target_functions": ["Runner._github_auto_private_flow", "Runner._github_public_flow", "Runner._self_sync_flow"],
        "implementation_goal": "Separate default private sync, Feishu writeback resync, and explicit public release gates in artifacts and prompts.",
        "verification_commands": [
            "python3 -m pytest tests/test_manager_github_showcase_feishu.py -k 'public or private or feishu'",
        ],
    },
    "P06": {
        "priority": 18,
        "target_files": ["nexus/project_docs.py", "nexus/runner.py", "tests/test_board_and_init.py"],
        "target_functions": ["write_project_docs", "Runner.init_project", "Runner.approve_and_continue"],
        "implementation_goal": "Turn raw project intent into modules, objects, workflows, validation, and boundaries during project initialization.",
        "verification_commands": [
            "python3 -m pytest tests/test_board_and_init.py -k 'init or project_docs'",
        ],
    },
    "P07": {
        "priority": 17,
        "target_files": ["nexus/project_docs.py", "tests/test_board_and_init.py"],
        "target_functions": ["write_project_docs"],
        "implementation_goal": "Reject shallow one-paragraph project descriptions by requiring traceable project understanding and workflows.",
        "verification_commands": [
            "python3 -m pytest tests/test_board_and_init.py -k 'project_docs or intent'",
        ],
    },
    "P08": {
        "priority": 18,
        "target_files": ["nexus/project_docs.py", "nexus/runner.py", "nexus/conversation_manager.py", "nexus/tools/search_service.py", "tests/test_conversation_and_skill.py", "tests/test_search.py"],
        "target_functions": [
            "write_project_docs",
            "Runner.init_project",
            "Runner.conversation_from_file",
            "Runner._run_search_loop",
            "SearchService.execute_round",
        ],
        "implementation_goal": "Make user-named files, directories, prompts, and histories a source bundle with read plan, log, and requirement extraction.",
        "verification_commands": [
            "python3 -m pytest tests/test_search.py tests/test_conversation_and_skill.py",
        ],
    },
    "P09": {
        "priority": 14,
        "target_files": ["nexus/runner.py", "nexus/tools/search_service.py", "tests/test_search.py"],
        "target_functions": ["Runner._run_search_loop", "SearchService.execute_round"],
        "implementation_goal": "Expose search/read plan, actual hits, coverage gaps, and stop reasons as human-readable artifacts.",
        "verification_commands": [
            "python3 -m pytest tests/test_search.py",
        ],
    },
    "P10": {
        "priority": 17,
        "target_files": ["nexus/project_docs.py", "tests/test_board_and_init.py"],
        "target_functions": ["write_project_docs"],
        "implementation_goal": "Generate directly usable domain workspaces, not only Nexus governance documents.",
        "verification_commands": [
            "python3 -m pytest tests/test_board_and_init.py -k 'init or project_docs'",
        ],
    },
    "P11": {
        "priority": 13,
        "target_files": ["scripts/lab/run_nexus_lab_loop.py", "scripts/lab/evaluate_nexus_case.py", "nexus/board.py"],
        "target_functions": ["run_loop", "build_verdict", "build_modification_request"],
        "implementation_goal": "Keep build, evaluation, modification request, and retest artifacts as one loop contract.",
        "verification_commands": [
            "python3 -m py_compile scripts/lab/run_nexus_lab_loop.py scripts/lab/evaluate_nexus_case.py scripts/lab/plan_nexus_modification.py",
        ],
    },
    "P12": {
        "priority": 13,
        "target_files": ["scripts/lab/run_nexus_skill_replay.py", "scripts/lab/evaluate_nexus_case.py", "scripts/lab/run_nexus_lab_loop.py"],
        "target_functions": ["build_replay_plan", "evaluate_run", "build_verdict", "build_modification_request"],
        "implementation_goal": "Connect independent skill replay and evaluator feedback to a modification-agent input.",
        "verification_commands": [
            "python3 scripts/lab/run_nexus_skill_replay.py --cases-file scripts/lab/nexus_real_project_cases.json --case-id real_07_wljob_heat_local_sequence --dry-run-plan",
        ],
    },
    "P13": {
        "priority": 12,
        "target_files": ["scripts/lab/run_nexus_lab_loop.py", "scripts/lab/inspect_nexus_lab_status.py", "nexus/board.py", "nexus/runner.py"],
        "target_functions": ["run_loop", "Runner.status", "Runner.board_show"],
        "implementation_goal": "Give the monitor one readable loop/project state with phase, run IDs, blocked reason, next prompt, and stop marker.",
        "verification_commands": [
            "python3 scripts/lab/run_nexus_lab_loop.py --cases-file scripts/lab/nexus_real_project_cases.json --dry-run-plan",
        ],
    },
    "P14": {
        "priority": 12,
        "target_files": ["scripts/lab/evaluate_nexus_case.py", "nexus/interaction.py", "nexus/artifacts/run_store.py"],
        "target_functions": ["build_verdict", "write_interaction", "RunStore.write_json"],
        "implementation_goal": "Require artifact references and verification commands wherever the system claims auditability or closure.",
        "verification_commands": [
            "python3 -m py_compile scripts/lab/evaluate_nexus_case.py",
        ],
    },
    "P15": {
        "priority": 16,
        "target_files": ["scripts/lab/evaluate_nexus_case.py", "nexus/project_docs.py", "tests/test_board_and_init.py"],
        "target_functions": ["evaluate_anti_hardcoding", "write_project_docs"],
        "implementation_goal": "Verify examples are treated as same-class requirements and prevent sample-name branching or layout copying.",
        "verification_commands": [
            "python3 scripts/lab/run_nexus_e2e_case.py --case-id test_08_sample_external_variants --dry-run-plan",
        ],
    },
    "P16": {
        "priority": 11,
        "target_files": ["scripts/lab/run_nexus_lab_loop.py", "nexus/runner.py", "nexus/interaction.py", "nexus/board.py"],
        "target_functions": ["run_loop", "Runner.run", "Runner.continue_run", "write_interaction"],
        "implementation_goal": "Connect provider, search, recovery, sync, conversation, project docs, approvals, evaluation, and retest into a lower-manual-work loop.",
        "verification_commands": [
            "python3 scripts/lab/run_nexus_lab_loop.py --cases-file scripts/lab/nexus_real_project_cases.json --dry-run-plan",
        ],
    },
}

FORBIDDEN_SHORTCUTS = [
    "Do not branch behavior on sample project names such as feeler, probe, wljob, codpm, orbit, thesis, or forge-manager.",
    "Do not create a parallel recovery store when the existing recovery/playbook path can be extended.",
    "Do not replace real CLI/workflow calls with mock providers or offline demonstrations.",
    "Do not mark a case complete when the evidence path is only a status summary.",
    "Do not run GitHub public, Feishu, login, or token-dependent side effects without an explicit operator flag.",
]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Nexus lab modification plan from modification_request.json.")
    parser.add_argument("--request", type=Path, default=Path("modification_request.json"), help="modification_request.json path")
    parser.add_argument("--output", type=Path, help="write plan JSON to this path; stdout is used when omitted")
    parser.add_argument("--nexus-root", type=Path, default=REPO_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    request_path = args.request.expanduser().resolve()
    request = load_json(request_path)
    plan = build_plan(request, request_path=request_path, nexus_root=args.nexus_root.expanduser().resolve())
    text = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output_path = args.output.expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


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


def build_plan(request: dict[str, Any], *, request_path: Path, nexus_root: Path) -> dict[str, Any]:
    axes = [axis for axis in request.get("failing_problem_axes", []) if isinstance(axis, dict)]
    groups = [build_target_group(axis, nexus_root=nexus_root) for axis in axes]
    groups.sort(key=lambda item: (-int(item.get("priority", 0)), str(item.get("problem_id", ""))))
    all_commands = unique(
        command
        for group in groups
        for command in group.get("verification_commands", [])
        if isinstance(command, str) and command
    )
    status = "no_change_needed" if request.get("status") == "no_change_needed" or not groups else "ready_for_core_patch"
    return {
        "schema": "nexus.lab.modification_plan.v1",
        "generated_at": utc_now(),
        "source_request": str(request_path),
        "status": status,
        "loop_id": request.get("loop_id"),
        "iteration": request.get("iteration"),
        "nexus_root": str(nexus_root),
        "git_at_request": request.get("git", {}),
        "rules": [
            request.get("rule", "Modify Nexus general mechanisms only."),
            request.get("case_policy", "All selected cases should be evaluated before planning modifications."),
            *FORBIDDEN_SHORTCUTS,
        ],
        "target_groups": groups,
        "cross_case_order": build_cross_case_order(groups),
        "verification_commands": all_commands,
        "apply_contract": {
            "does_not_patch_core": True,
            "how_to_apply": (
                "Use this JSON as the input to the monitor/current Codex modification step or pass a real patching command "
                "to run_nexus_lab_loop.py --apply-modification-command. After patching, rerun all selected cases, not only "
                "the case that exposed the failure."
            ),
            "required_before_patch": [
                "Confirm the git branch/checkpoint for Nexus core edits.",
                "Read the target files/functions listed for the failing axes.",
                "Map every code change back to a general mechanism and at least one failing evidence path.",
            ],
        },
    }


def build_target_group(axis: dict[str, Any], *, nexus_root: Path) -> dict[str, Any]:
    problem_id = str(axis.get("problem_id", ""))
    target = PROBLEM_TARGETS.get(problem_id, {})
    cases = [case for case in axis.get("cases", []) if isinstance(case, dict)]
    evidence_paths = unique(
        path
        for case in cases
        for path in [case.get("execution_path"), case.get("evaluation_path")]
        if isinstance(path, str) and path
    )
    target_files = [str(nexus_root / rel) if not Path(rel).is_absolute() else rel for rel in target.get("target_files", [])]
    return {
        "problem_id": problem_id,
        "priority": target.get("priority", 0),
        "counts": axis.get("counts", {}),
        "case_count": len(cases),
        "failed_or_blocked_cases": [
            {
                "case_id": case.get("case_id", ""),
                "status": case.get("status", ""),
                "observed": case.get("observed", ""),
                "required_nexus_change": case.get("required_nexus_change"),
            }
            for case in cases
            if str(case.get("status", "")) != "pass"
        ],
        "evidence_paths": evidence_paths,
        "target_files": target_files,
        "target_functions": target.get("target_functions", []),
        "implementation_goal": target.get(
            "implementation_goal",
            "Add auditable evidence and a general Nexus mechanism for this failing problem axis.",
        ),
        "verification_commands": target.get("verification_commands", []),
        "forbidden_shortcuts": FORBIDDEN_SHORTCUTS,
    }


def build_cross_case_order(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not groups:
        return []
    order: list[dict[str, Any]] = []
    clusters = [
        ("intent_and_sources", {"P03", "P06", "P07", "P08", "P10", "P15"}),
        ("recovery", {"P01", "P02"}),
        ("sync", {"P04", "P05"}),
        ("loop_and_monitor", {"P11", "P12", "P13", "P14", "P16"}),
        ("search_trace", {"P09"}),
    ]
    by_id = {str(group.get("problem_id", "")): group for group in groups}
    for name, problem_ids in clusters:
        selected = [by_id[pid] for pid in problem_ids if pid in by_id]
        if not selected:
            continue
        order.append(
            {
                "cluster": name,
                "problem_ids": [str(group.get("problem_id", "")) for group in selected],
                "reason": cluster_reason(name),
                "target_files": unique(
                    path
                    for group in selected
                    for path in group.get("target_files", [])
                    if isinstance(path, str)
                ),
                "verification_commands": unique(
                    command
                    for group in selected
                    for command in group.get("verification_commands", [])
                    if isinstance(command, str)
                ),
            }
        )
    return order


def cluster_reason(name: str) -> str:
    reasons = {
        "intent_and_sources": "Most project-quality failures share the init/project-doc/source-reading path; fix the common input-to-workspace contract before sample-specific symptoms.",
        "recovery": "Recovery rebind and playbook persistence must stay on the same existing recovery path.",
        "sync": "Sync failures should be fixed through shared private/Feishu/public sync wrappers, not entry-specific branches.",
        "loop_and_monitor": "Loop, evaluator, skill replay, and monitor failures should keep one state and modification-request contract.",
        "search_trace": "Search trace summaries should aggregate existing search artifacts before changing search adapter behavior.",
    }
    return reasons.get(name, "Group related failing axes so one general mechanism can be patched and retested together.")


def unique(values: Any) -> list[Any]:
    seen: set[str] = set()
    items: list[Any] = []
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        if key in seen:
            continue
        seen.add(key)
        items.append(value)
    return items


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
