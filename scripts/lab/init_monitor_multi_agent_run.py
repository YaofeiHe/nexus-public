#!/usr/bin/env python3
"""Initialize a monitor-level Nexus-Verix multi-agent run.

This script does not spawn Codex subagents. It creates the durable launch
packet that the current Codex conversation monitor uses before calling
multi_agent_v1.spawn_agent. Keeping this boundary explicit prevents the
Python lab loop from being mistaken for a real multi-agent runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
FORGE_ROOT = REPO_ROOT.parent
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_HISTORY = FORGE_ROOT / "codex_history" / "06191031-nexus-verix-system-history.md"
DEFAULT_E2E_ROOT = Path("/tmp/nexus-verix-monitor")
DEFAULT_VERIX_ROOT = FORGE_ROOT / "verix"

EXECUTION_MODEL = "monitor_multi_agent_v1"
NOT_EXECUTION_MODEL = "orchestrated_subprocess_loop_only"
STATUS_VALUES = ["done", "failed", "blocked", "not_started"]
PROBLEM_BACKLOG_FILENAME = "problem_backlog.json"


AGENT_ORDER = [
    "nexus_execution",
    "skill_replay",
    "verix_audit",
    "nexus_modification",
    "state_audit",
]


OPERATION_MODES = [
    {
        "mode_id": "step_by_step_skill_workflow",
        "description": "用户按 workflow 逐条输入 `$nexus-workflow ...` skill 指令，monitor 必须让 Nexus skill 层和 Outer Codex recovery 协议参与真实交互，而不是只解析文本后跑一个本地 CLI 替代品。",
        "acceptance": [
            "prompt-level replay records each user-facing `$nexus-workflow` turn",
            "approval/continue-after-input/rebind paths stay on the same Nexus run",
            "research chains include online-search approval continuation evidence before claiming full research success",
        ],
    },
    {
        "mode_id": "default_autonomous_multi_agent_build",
        "description": "用户只提供项目意图，monitor 将该意图分发给 Nexus execution、Skill Replay、Verix audit、Modification 和 State Audit agent，按默认路径多轮构建、审计、修改和回归。",
        "acceptance": [
            "original intent is shared as input to Nexus and independently to Verix",
            "monitor remains the control-plane conversation for status/query/intervention/stop",
            "new user instructions amend the active main flow unless they are explicit stop/terminate requests",
        ],
    },
]


REFERENCE1_UNRESOLVED_PROOF_GAPS = [
    "Only `real_07_wljob_heat_local_sequence` had been verified, and only until the research `online_search_approval_required` blocker.",
    "`$nexus-workflow 调研` is not proven fully usable across all research scenarios.",
    "Online-search approval continuation to a high-quality final report is not proven.",
    "The 16 problem axes are represented in the lab matrix and current structural checks passed, but the axes are not proven fully solved.",
    "Complex project intent understanding quality is not proven to match the hand-guided feeler result.",
    "A real Verix independent audit agent plus Nexus modification agent automatic test-judge-modify-retest loop is not proven complete.",
]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the launch packet for a real Codex monitor multi-agent Nexus-Verix run.")
    parser.add_argument("--dry-run-plan", action="store_true", help="print the launch plan without writing run files")
    parser.add_argument("--init-run", action="store_true", help="write monitor_state.json and per-agent task prompts")
    parser.add_argument("--e2e-root", type=Path, default=DEFAULT_E2E_ROOT)
    parser.add_argument("--nexus-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--verix-root", type=Path, default=DEFAULT_VERIX_ROOT)
    parser.add_argument("--history-export", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--run-id", default="", help="optional stable run id for tests or controlled restarts")
    parser.add_argument("--allow-external-side-effects", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.dry_run_plan == args.init_run:
        raise SystemExit("choose exactly one of --dry-run-plan or --init-run")
    if args.max_iterations < 1:
        raise SystemExit("--max-iterations must be >= 1")

    plan = build_plan(args)
    if args.dry_run_plan:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    run_dir = Path(plan["run_dir"])
    task_dir = run_dir / "agent_tasks"
    artifacts_dir = run_dir / "artifacts"
    task_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    state = build_initial_state(plan)
    state_path = run_dir / "monitor_state.json"
    state["state_path"] = str(state_path)
    state["launch_packet_path"] = str(run_dir / "launch_packet.json")
    state["events_log"] = str(run_dir / "events.jsonl")
    state["approval_queue"] = str(run_dir / "approval_queue.jsonl")
    state["handoffs_dir"] = str(run_dir / "handoffs")
    state["artifact_merge_command"] = f"{sys.executable} {SCRIPT_DIR / 'merge_monitor_agent_artifacts.py'} --state {state_path}"
    state["state_update_command"] = f"{sys.executable} {SCRIPT_DIR / 'update_monitor_multi_agent_run.py'} --state {state_path}"
    backlog_path = run_dir / PROBLEM_BACKLOG_FILENAME
    state["problem_backlog"] = build_problem_backlog_reference(backlog_path, plan)
    state.setdefault("shared_artifacts", {})["problem_backlog"] = str(backlog_path)
    state["shared_artifacts"]["issue_memory"] = str(backlog_path)
    Path(state["handoffs_dir"]).mkdir(parents=True, exist_ok=True)
    write_problem_backlog(backlog_path, build_problem_backlog(state, plan=plan))
    Path(state["events_log"]).write_text(
        json.dumps(
            {
                "timestamp": utc_now(),
                "type": "monitor_state_initialized",
                "message": "Run is ready for current Codex monitor to spawn real multi_agent_v1 subagents.",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    Path(state["approval_queue"]).write_text("", encoding="utf-8")
    for agent_id, agent in state["agents"].items():
        task_path = task_dir / f"{agent['order']:02d}_{agent_id}.md"
        agent["task_prompt_path"] = str(task_path)
        agent["output_dir"] = str(artifacts_dir / agent_id)
        Path(agent["output_dir"]).mkdir(parents=True, exist_ok=True)
        task_path.write_text(render_agent_task(agent_id, agent, state), encoding="utf-8")

    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    launch_packet = {
        "schema": "nexus.lab.monitor_multi_agent_launch_packet.v1",
        "state_path": str(state_path),
        "run_dir": str(run_dir),
        "spawn_source": "current_codex_conversation_only",
        "spawn_order": AGENT_ORDER,
        "agent_task_prompts": {agent_id: state["agents"][agent_id]["task_prompt_path"] for agent_id in AGENT_ORDER},
        "events_log": state["events_log"],
        "approval_queue": state["approval_queue"],
        "handoffs_dir": state["handoffs_dir"],
        "problem_backlog": state["problem_backlog"],
        "artifact_merge_command": state["artifact_merge_command"],
        "state_update_command": state["state_update_command"],
        "monitor_next_action": "Call multi_agent_v1.spawn_agent for each task prompt, record returned agent ids in monitor_state.json, then coordinate with wait_agent/send_input.",
    }
    Path(state["launch_packet_path"]).write_text(json.dumps(launch_packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(launch_packet, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or timestamp_slug()
    nexus_root = args.nexus_root.expanduser().resolve()
    verix_root = args.verix_root.expanduser().resolve()
    history_export = args.history_export.expanduser().resolve()
    e2e_root = args.e2e_root.expanduser().resolve()
    run_dir = e2e_root / "monitor-runs" / run_id
    return {
        "schema": "nexus.lab.monitor_multi_agent_plan.v1",
        "generated_at": utc_now(),
        "execution_model": EXECUTION_MODEL,
        "not_execution_model": NOT_EXECUTION_MODEL,
        "python_embeds_subagents": False,
        "spawn_source": "current_codex_conversation_multi_agent_v1",
        "contract_path": str(REPO_ROOT / "docs" / "lab" / "monitor_multi_agent_contract.md"),
        "state_schema_path": str(REPO_ROOT / "docs" / "lab" / "monitor_agent_state.schema.json"),
        "roles_path": str(REPO_ROOT / "docs" / "lab" / "monitor_agent_roles.json"),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "nexus_root": str(nexus_root),
        "verix_root": str(verix_root),
        "history_export": str(history_export),
        "max_iterations": args.max_iterations,
        "external_side_effects": "allowed" if args.allow_external_side_effects else "authorization-gated",
        "operator_command": "启动运行",
        "objective": "Use real Codex monitor subagents plus Nexus/Verix/lab artifacts to repair Nexus-Verix until full acceptance passes or an explicit stop/block occurs.",
        "agents": build_agent_specs(),
        "shared_artifacts": {
            "problem_matrix": str(SCRIPT_DIR / "nexus_problem_matrix.json"),
            "real_project_cases": str(SCRIPT_DIR / "nexus_real_project_cases.json"),
            "structured_cases": str(SCRIPT_DIR / "nexus_lab_cases.json"),
            "completion_ledger": str(REPO_ROOT / "docs" / "lab" / "nexus_verix_completion_ledger.json"),
            "wljob_command_file": "<LOCAL_PATH_REDACTED>",
            "subprocess_orchestrator": str(SCRIPT_DIR / "run_nexus_verix_orchestrator.py"),
            "status_inspector": str(SCRIPT_DIR / "inspect_nexus_lab_status.py"),
            "artifact_merger": str(SCRIPT_DIR / "merge_monitor_agent_artifacts.py"),
            "problem_backlog": str(run_dir / PROBLEM_BACKLOG_FILENAME),
            "issue_memory": str(run_dir / PROBLEM_BACKLOG_FILENAME),
        },
        "operation_modes": OPERATION_MODES,
        "reference1_unresolved_proof_gaps": REFERENCE1_UNRESOLVED_PROOF_GAPS,
        "control_semantics": {
            "user_message_default": "amend_or_query_active_main_flow",
            "stop_requires_explicit_intent": True,
            "explicit_stop_examples": ["停止", "终止", "强行停止", "stop this run", "terminate this run"],
            "non_stop_examples": ["状态怎么样", "补充要求", "调整方向", "继续完成", "为什么", "重新分析"],
        },
        "acceptance": [
            "Completion ledger lists every user goal, required entrypoint, implementation surface, verification surface, external permission, and status using only done/failed/blocked/not_started.",
            "Both project-building modes are represented: step-by-step `$nexus-workflow` skill workflow and default autonomous multi-agent multi-round construction from a project intent.",
            "Every selected case is evaluated against all 16 problem axes.",
            "Skill replay covers the full wljob 0-34 command chain and records prompt-level results.",
            "Skill replay cannot claim full `$nexus-workflow 调研` success unless online-search approval continuation reaches and evaluates the final report.",
            "Verix audit independently reads original intent, normalized intent, source indexes, run artifacts, and project outputs.",
            "Nexus modifications target general mechanisms only, not sample-project-specific branches.",
            "Regression tests pass after each accepted patch.",
            "The monitor state preserves main-flow status across user questions until explicit stop.",
            "False stop or lost subagent handles are recorded as recoverable monitor incidents and do not silently complete or discard the main flow.",
            "Every agent writes heartbeat_status.json before long-running work and monitor merges artifacts back into monitor_state.json before claiming status.",
        ],
    }


def build_agent_specs() -> dict[str, dict[str, Any]]:
    return {
        "nexus_execution": {
            "order": 1,
            "agent_type": "worker",
            "responsibility": "Run Nexus/lab execution surfaces and produce execution artifacts.",
            "may_write": ["artifacts/nexus_execution/**"],
            "must_not_write": ["nexus/**", "tests/**"],
            "expected_outputs": ["heartbeat_status.json", "execution_summary.json", "case_artifacts_index.json"],
        },
        "skill_replay": {
            "order": 2,
            "agent_type": "worker",
            "responsibility": "Simulate user-level $nexus-workflow prompts through the monitor-controlled replay contract.",
            "may_write": ["artifacts/skill_replay/**"],
            "must_not_write": ["nexus/**", "tests/**"],
            "expected_outputs": ["heartbeat_status.json", "skill_replay_summary.json", "prompt_turns.jsonl"],
        },
        "verix_audit": {
            "order": 3,
            "agent_type": "worker",
            "responsibility": "Run or prepare independent Verix audit evidence and produce structured verdicts.",
            "may_write": ["artifacts/verix_audit/**"],
            "must_not_write": ["nexus/**", "tests/**"],
            "expected_outputs": ["heartbeat_status.json", "verix_verdict.json", "audit_evidence_index.json"],
        },
        "nexus_modification": {
            "order": 4,
            "agent_type": "worker",
            "responsibility": "Patch Nexus general mechanisms from aggregated failures after monitor approval.",
            "may_write": ["nexus/**", "tests/**", "scripts/lab/**", "docs/lab/**"],
            "must_not_write": ["docs/lab/sample_projects/** by hardcoding project names"],
            "expected_outputs": ["heartbeat_status.json", "patch_summary.md", "verification_results.json"],
        },
        "state_audit": {
            "order": 5,
            "agent_type": "explorer",
            "responsibility": "Continuously audit monitor_state.json, stop markers, agent outputs, and acceptance gaps.",
            "may_write": ["artifacts/state_audit/**"],
            "must_not_write": ["nexus/**", "tests/**"],
            "expected_outputs": ["heartbeat_status.json", "state_audit_report.json"],
        },
    }


def build_initial_state(plan: dict[str, Any]) -> dict[str, Any]:
    agents = {
        agent_id: {
            **spec,
            "status": "pending_spawn",
            "multi_agent_v1_id": "",
            "last_update": "",
            "blocker": "",
        }
        for agent_id, spec in plan["agents"].items()
    }
    return {
        "schema": "nexus.lab.monitor_multi_agent_state.v1",
        "run_id": plan["run_id"],
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "status": "ready_to_spawn",
        "phase": "awaiting_monitor_spawn",
        "execution_model": EXECUTION_MODEL,
        "not_execution_model": NOT_EXECUTION_MODEL,
        "spawn_source": plan["spawn_source"],
        "python_embeds_subagents": False,
        "nexus_root": plan["nexus_root"],
        "verix_root": plan["verix_root"],
        "history_export": plan["history_export"],
        "objective": plan["objective"],
        "run_dir": plan["run_dir"],
        "stop_file": str(Path(plan["run_dir"]) / "STOP"),
        "events_log": str(Path(plan["run_dir"]) / "events.jsonl"),
        "approval_queue": str(Path(plan["run_dir"]) / "approval_queue.jsonl"),
        "handoffs_dir": str(Path(plan["run_dir"]) / "handoffs"),
        "artifact_merge_command": "",
        "state_update_command": "",
        "max_iterations": plan["max_iterations"],
        "external_side_effects": plan["external_side_effects"],
        "operation_modes": plan["operation_modes"],
        "reference1_unresolved_proof_gaps": plan["reference1_unresolved_proof_gaps"],
        "control_semantics": plan["control_semantics"],
        "instruction_log": [],
        "current_iteration": 0,
        "main_flow": {
            "active": False,
            "interruption_policy": "User questions update or query this state; only explicit stop/terminate ends the main flow.",
            "next_monitor_action": "spawn_agents",
        },
        "agents": agents,
        "acceptance": plan["acceptance"],
        "shared_artifacts": plan["shared_artifacts"],
        "events": [
            {
                "timestamp": utc_now(),
                "type": "monitor_state_initialized",
                "message": "Run is ready for current Codex monitor to spawn real multi_agent_v1 subagents.",
            }
        ],
    }


def build_problem_backlog_reference(backlog_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "nexus.lab.problem_backlog_reference.v1",
        "path": str(backlog_path),
        "issue_memory_path": str(backlog_path),
        "single_source_of_truth": True,
        "read_required_by": ["monitor", *AGENT_ORDER],
        "status_values_allowed": STATUS_VALUES,
        "problem_matrix_path": plan["shared_artifacts"]["problem_matrix"],
        "completion_ledger_path": plan["shared_artifacts"]["completion_ledger"],
    }


def build_problem_backlog(state: dict[str, Any], *, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    now = utc_now()
    shared = plan.get("shared_artifacts", {}) if plan else state.get("shared_artifacts", {})
    problem_matrix_path = str(shared.get("problem_matrix") or SCRIPT_DIR / "nexus_problem_matrix.json")
    completion_ledger_path = str(shared.get("completion_ledger") or REPO_ROOT / "docs" / "lab" / "nexus_verix_completion_ledger.json")
    previous_unresolved = load_previous_unresolved_items(completion_ledger_path)
    for index, proof_gap in enumerate(state.get("reference1_unresolved_proof_gaps", []), start=1):
        previous_unresolved.append(
            {
                "id": f"reference1_gap_{index:02d}",
                "status": "not_started",
                "title": str(proof_gap),
                "source": "reference1_unresolved_proof_gaps",
            }
        )
    current_round_new_requirements = build_current_round_requirements(state, plan=plan)
    matrix_summary = load_problem_matrix_summary(problem_matrix_path)
    return {
        "schema": "nexus.lab.problem_backlog.v1",
        "canonical_name": "problem_backlog",
        "issue_memory_alias": "issue_memory",
        "single_source_of_truth": True,
        "purpose": "Persistent problem source for each Nexus-Verix monitor iteration; every agent must read this before claiming completion or blockers.",
        "run_id": state.get("run_id", ""),
        "created_at": state.get("created_at") or now,
        "updated_at": now,
        "status": "not_started",
        "status_values_allowed": STATUS_VALUES,
        "user_requirements": build_user_requirements(state, plan=plan),
        "fixed_source_paths": {
            "history_export": state.get("history_export", ""),
            "problem_matrix": problem_matrix_path,
            "completion_ledger": completion_ledger_path,
            "wljob_skill_commands": str(shared.get("wljob_command_file", "")),
        },
        "problem_matrix": matrix_summary,
        "previous_unresolved_items": previous_unresolved,
        "current_round_new_requirements": current_round_new_requirements,
        "open_issues": [*previous_unresolved, *current_round_new_requirements],
        "completion_rule": "Do not claim done until relevant ledger rows are done, source_consumption is present in agent artifacts, and real evidence exists or a precise blocked reason is recorded.",
        "source_consumption_contract": {
            "required_field": "source_consumption",
            "required_in": "agent JSON artifacts including heartbeat_status.json and role summary/verdict/index files",
            "minimum_sources": ["problem_backlog", "problem_matrix", "completion_ledger"],
            "missing_field_status": "not_started",
        },
    }


def build_user_requirements(state: dict[str, Any], *, plan: dict[str, Any] | None = None) -> list[str]:
    requirements = [
        "Run as real monitor_multi_agent_v1 subagents; Python scripts may prepare artifacts but must not impersonate the runtime.",
        "Use one persistent problem_backlog/issue_memory artifact so every round reads the same unresolved issues.",
        "Reuse existing monitor/lab/replay/evaluator surfaces and do not create duplicate parallel chains for the same responsibility.",
        "Do not claim the 16 Nexus problem axes solved from structural checks alone.",
        "Each agent artifact must record source_consumption so the monitor can audit which sources were read.",
    ]
    if plan:
        requirements.append(str(plan.get("objective", "")))
    for mode in state.get("operation_modes", []):
        if isinstance(mode, dict) and mode.get("description"):
            requirements.append(str(mode["description"]))
    return [item for item in dict.fromkeys(requirements) if item]


def build_current_round_requirements(state: dict[str, Any], *, plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    if plan and plan.get("operator_command"):
        requirements.append(
            {
                "id": "current_round_operator_command",
                "status": "not_started",
                "title": str(plan["operator_command"]),
                "source": "operator_command",
            }
        )
    for index, entry in enumerate(state.get("instruction_log", []), start=1):
        if not isinstance(entry, dict):
            continue
        instruction = str(entry.get("instruction", "")).strip()
        if instruction:
            requirements.append(
                {
                    "id": f"current_round_instruction_{index:02d}",
                    "status": "not_started",
                    "title": instruction,
                    "source": "instruction_log",
                    "timestamp": str(entry.get("timestamp", "")),
                }
            )
    return requirements


def load_previous_unresolved_items(completion_ledger_path: str) -> list[dict[str, Any]]:
    path = Path(completion_ledger_path).expanduser()
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [
            {
                "id": "completion_ledger_unavailable",
                "status": "blocked",
                "title": f"completion ledger could not be read: {path}",
                "source": str(path),
            }
        ]
    unresolved: list[dict[str, Any]] = []
    for item in ledger.get("items", []):
        if not isinstance(item, dict):
            continue
        status = normalize_backlog_status(str(item.get("status", "not_started")))
        if status == "done":
            continue
        unresolved.append(
            {
                "id": str(item.get("id", "")),
                "status": status,
                "title": str(item.get("user_goal") or item.get("title") or ""),
                "source": str(path),
                "required_entrypoint": str(item.get("required_entrypoint_or_chain") or item.get("required_entrypoint") or ""),
                "verification": str(item.get("verification_or_observation") or item.get("verification_surface") or ""),
            }
        )
    return unresolved


def load_problem_matrix_summary(problem_matrix_path: str) -> dict[str, Any]:
    path = Path(problem_matrix_path).expanduser()
    try:
        matrix = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "path": str(path),
            "status": "blocked",
            "problem_count": 0,
            "problem_ids": [],
        }
    problems = [item for item in matrix.get("problems", []) if isinstance(item, dict)]
    return {
        "path": str(path),
        "status": "not_started",
        "problem_count": len(problems),
        "problem_ids": [str(item.get("id", "")) for item in problems if item.get("id")],
        "source": str(matrix.get("source", "")),
    }


def normalize_backlog_status(status: str) -> str:
    value = status.strip()
    if value in STATUS_VALUES:
        return value
    if value in {"completed", "complete", "pass", "passed"}:
        return "done"
    if value in {"fail", "failure"}:
        return "failed"
    if value in {"running", "pending", "partial", "missing"}:
        return "not_started"
    return "blocked" if value else "not_started"


def write_problem_backlog(path: Path, backlog: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(backlog, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ensure_problem_backlog(state: dict[str, Any], *, state_path: Path) -> Path:
    shared = state.setdefault("shared_artifacts", {})
    current = state.get("problem_backlog")
    if isinstance(current, dict) and str(current.get("path", "")).strip():
        backlog_path = Path(str(current["path"])).expanduser()
    elif str(shared.get("problem_backlog", "")).strip():
        backlog_path = Path(str(shared["problem_backlog"])).expanduser()
    else:
        run_dir = Path(str(state.get("run_dir") or state_path.parent)).expanduser()
        backlog_path = run_dir / PROBLEM_BACKLOG_FILENAME
    if not backlog_path.is_absolute():
        backlog_path = state_path.parent / backlog_path

    plan_like = {
        "shared_artifacts": {
            "problem_matrix": str(shared.get("problem_matrix") or SCRIPT_DIR / "nexus_problem_matrix.json"),
            "completion_ledger": str(shared.get("completion_ledger") or REPO_ROOT / "docs" / "lab" / "nexus_verix_completion_ledger.json"),
            "wljob_command_file": str(shared.get("wljob_command_file", "")),
        }
    }
    state["problem_backlog"] = build_problem_backlog_reference(backlog_path, plan_like)
    shared["problem_backlog"] = str(backlog_path)
    shared["issue_memory"] = str(backlog_path)
    if not backlog_path.exists():
        write_problem_backlog(backlog_path, build_problem_backlog(state, plan=plan_like))
    return backlog_path


def append_current_requirement_to_problem_backlog(state: dict[str, Any], *, state_path: Path, instruction: str) -> None:
    backlog_path = ensure_problem_backlog(state, state_path=state_path)
    try:
        backlog = json.loads(backlog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        backlog = build_problem_backlog(state)
    current_requirements = backlog.setdefault("current_round_new_requirements", [])
    existing = {str(item.get("title", "")) for item in current_requirements if isinstance(item, dict)}
    if instruction not in existing:
        current_requirements.append(
            {
                "id": f"current_round_instruction_{len(current_requirements) + 1:02d}",
                "status": "not_started",
                "title": instruction,
                "source": "instruction_log",
                "timestamp": utc_now(),
            }
        )
    backlog["previous_unresolved_items"] = load_previous_unresolved_items(
        str(state.get("shared_artifacts", {}).get("completion_ledger") or REPO_ROOT / "docs" / "lab" / "nexus_verix_completion_ledger.json")
    )
    backlog["open_issues"] = [*backlog.get("previous_unresolved_items", []), *current_requirements]
    backlog["updated_at"] = utc_now()
    write_problem_backlog(backlog_path, backlog)


def render_agent_task(agent_id: str, agent: dict[str, Any], state: dict[str, Any]) -> str:
    return f"""# Nexus-Verix Monitor Agent Task: {agent_id}

Run id: {state['run_id']}
Execution model: {state['execution_model']}
State path: {state.get('state_path', '<written after task generation>')}
Problem backlog / issue memory: {state.get('problem_backlog', {}).get('path', '') if isinstance(state.get('problem_backlog'), dict) else state.get('problem_backlog', '')}
Nexus root: {state['nexus_root']}
Verix root: {state['verix_root']}
History export: {state['history_export']}
Events log: {state.get('events_log', '')}
Approval queue: {state.get('approval_queue', '')}
Handoffs dir: {state.get('handoffs_dir', '')}
Artifact merge command: {state.get('artifact_merge_command', '')}
State update command: {state.get('state_update_command', '')}

## Responsibility

{agent['responsibility']}

## Required First Read

Before deciding status, blockers, or implementation scope, read the single problem source:

```text
{state.get('problem_backlog', {}).get('path', '') if isinstance(state.get('problem_backlog'), dict) else state.get('problem_backlog', '')}
```

Treat this as both `problem_backlog` and `issue_memory`. Do not ask the user to restate issues that are already listed there. Use the problem matrix and completion ledger paths recorded in that artifact.

## Write Scope

Allowed:

{json.dumps(agent['may_write'], ensure_ascii=False, indent=2)}

Forbidden:

{json.dumps(agent['must_not_write'], ensure_ascii=False, indent=2)}

## Shared Acceptance

{json.dumps(state['acceptance'], ensure_ascii=False, indent=2)}

## Operation Modes

{json.dumps(state['operation_modes'], ensure_ascii=False, indent=2)}

## Reference1 Unresolved Proof Gaps

{json.dumps(state['reference1_unresolved_proof_gaps'], ensure_ascii=False, indent=2)}

## Control Semantics

{json.dumps(state['control_semantics'], ensure_ascii=False, indent=2)}

## Required Output

Before any long-running work, first write:

```text
{agent.get('output_dir', '<assigned after task generation>')}/heartbeat_status.json
```

The heartbeat must contain `schema`, `role_id`, `status`, `current_action`, `files_written`, `blocker`, `next_action`, `timestamp`, and `source_consumption`. If work blocks or waits, update that file before waiting.

Every JSON artifact you write must include a top-level `source_consumption` field listing the problem backlog / issue memory, problem matrix, completion ledger, and any role-specific source files you actually read. If a source is unavailable, record it with status `blocked` or `not_started`; do not omit the field.

Write outputs under:

```text
{agent.get('output_dir', '<assigned after task generation>')}
```

Expected files:

{json.dumps(agent['expected_outputs'], ensure_ascii=False, indent=2)}

## Boundary

You are a real Codex monitor-spawned subagent. Do not describe the Python lab loop as the multi-agent runtime. Python scripts may be used as tools, but the monitor conversation remains the orchestrator and owns spawn, stop, user-interruption handling, and cross-agent state.
"""


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
