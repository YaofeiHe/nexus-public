#!/usr/bin/env python3
"""Update and recover a monitor-level Nexus-Verix multi-agent run state."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from init_monitor_multi_agent_run import (  # noqa: E402
    OPERATION_MODES,
    REFERENCE1_UNRESOLVED_PROOF_GAPS,
    append_current_requirement_to_problem_backlog,
    build_agent_specs,
    ensure_problem_backlog,
    render_agent_task,
)


REQUIRED_AGENTS = [
    "nexus_execution",
    "skill_replay",
    "verix_audit",
    "nexus_modification",
    "state_audit",
]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record monitor instructions and recover false-stop Nexus-Verix monitor runs.")
    parser.add_argument("--state", type=Path, required=True, help="path to monitor_state.json")
    parser.add_argument("--instruction", default="", help="user instruction text to append to the active monitor flow")
    parser.add_argument("--instruction-file", type=Path, help="file containing user instruction text")
    parser.add_argument("--recover-false-stop", action="store_true", help="treat current stopped state/STOP marker as a monitor misclassification")
    parser.add_argument("--agent-handles-lost", action="store_true", help="mark previous multi_agent_v1 handles as lost and require respawn")
    parser.add_argument("--migrate-current-contract", action="store_true", help="add current operation modes/proof gaps and rewrite agent task prompts")
    parser.add_argument("--record-agent-id", action="append", default=[], metavar="ROLE=ID", help="record a spawned multi_agent_v1 id for a role")
    parser.add_argument("--set-agent-status", action="append", default=[], metavar="ROLE=STATUS", help="set an agent runtime status after monitor wait/close")
    parser.add_argument("--status-blocker", action="append", default=[], metavar="ROLE=TEXT", help="blocker text used with --set-agent-status")
    parser.add_argument("--json", action="store_true", help="print updated state summary as JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    state_path = args.state.expanduser().resolve()
    state = load_state(state_path)
    ensure_problem_backlog(state, state_path=state_path)
    instruction = read_instruction(args)

    if instruction:
        append_instruction(state, instruction, state_path=state_path)
    if args.migrate_current_contract:
        migrate_current_contract(state, state_path=state_path)
    if args.recover_false_stop:
        recover_false_stop(state, state_path=state_path, agent_handles_lost=args.agent_handles_lost)
    if args.record_agent_id:
        record_agent_ids(state, args.record_agent_id)
    if args.set_agent_status:
        set_agent_statuses(state, args.set_agent_status, args.status_blocker)

    state["updated_at"] = utc_now()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        "schema": "nexus.lab.monitor_update_result.v1",
        "state_path": str(state_path),
        "status": state.get("status", ""),
        "phase": state.get("phase", ""),
        "next_monitor_action": state.get("main_flow", {}).get("next_monitor_action", ""),
        "agent_statuses": {
            agent_id: agent.get("status", "")
            for agent_id, agent in state.get("agents", {}).items()
            if isinstance(agent, dict)
        },
        "events": state.get("events", [])[-5:],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"status={summary['status']} phase={summary['phase']} next={summary['next_monitor_action']}")
    return 0


def load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"monitor_state.json not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid monitor_state.json {path}: {exc}") from exc
    if state.get("schema") != "nexus.lab.monitor_multi_agent_state.v1":
        raise SystemExit(f"not a monitor multi-agent state: {path}")
    return state


def read_instruction(args: argparse.Namespace) -> str:
    if args.instruction and args.instruction_file:
        raise SystemExit("use only one of --instruction or --instruction-file")
    if args.instruction_file:
        return args.instruction_file.expanduser().read_text(encoding="utf-8").strip()
    return args.instruction.strip()


def append_instruction(state: dict[str, Any], instruction: str, *, state_path: Path) -> None:
    entry = {
        "timestamp": utc_now(),
        "classification": "main_flow_amendment",
        "instruction": instruction,
    }
    state.setdefault("instruction_log", []).append(entry)
    append_current_requirement_to_problem_backlog(state, state_path=state_path, instruction=instruction)
    state.setdefault("events", []).append(
        {
            "timestamp": entry["timestamp"],
            "type": "user_instruction_recorded",
            "message": "User instruction recorded as a main-flow amendment and appended to the shared problem backlog.",
            "problem_backlog": state.get("problem_backlog", {}),
        }
    )


def recover_false_stop(state: dict[str, Any], *, state_path: Path, agent_handles_lost: bool) -> None:
    now = utc_now()
    stop_file = Path(str(state.get("stop_file") or state_path.parent / "STOP")).expanduser()
    archived_stop = ""
    if stop_file.exists():
        archived = stop_file.with_name(f"{stop_file.name}.false-positive.{timestamp_slug()}.json")
        shutil.move(str(stop_file), str(archived))
        archived_stop = str(archived)

    state["status"] = "ready_to_spawn" if agent_handles_lost else "awaiting_agent"
    state["phase"] = "recovering_after_false_stop_agent_respawn_required" if agent_handles_lost else "recovering_after_false_stop"
    state.pop("finished_at", None)
    state["blocker"] = ""
    state.setdefault("main_flow", {})["active"] = True
    state["main_flow"]["next_monitor_action"] = "respawn_lost_agents" if agent_handles_lost else "continue_waiting_for_agents"
    state.setdefault("resume_contract", {})["same_monitor_run_required"] = True
    state["resume_contract"]["false_stop_recovered_at"] = now
    if archived_stop:
        state["resume_contract"]["archived_false_positive_stop_file"] = archived_stop

    if agent_handles_lost:
        for agent_id in REQUIRED_AGENTS:
            agent = state.get("agents", {}).get(agent_id)
            if not isinstance(agent, dict):
                continue
            previous = str(agent.get("multi_agent_v1_id", "")).strip()
            if previous:
                agent.setdefault("previous_multi_agent_v1_ids", []).append(previous)
            agent["multi_agent_v1_id"] = ""
            agent["status"] = "pending_respawn"
            agent["blocker"] = "previous_multi_agent_v1_handle_not_found_after_context_resume"
            agent["last_update"] = now

    state.setdefault("events", []).append(
        {
            "timestamp": now,
            "type": "false_stop_recovered",
            "message": "Recovered a monitor stop marker that did not correspond to an explicit user termination request.",
            "archived_stop_file": archived_stop,
            "agent_handles_lost": agent_handles_lost,
        }
    )


def migrate_current_contract(state: dict[str, Any], *, state_path: Path) -> None:
    now = utc_now()
    ensure_problem_backlog(state, state_path=state_path)
    state["operation_modes"] = OPERATION_MODES
    state["reference1_unresolved_proof_gaps"] = REFERENCE1_UNRESOLVED_PROOF_GAPS
    state["control_semantics"] = {
        "user_message_default": "amend_or_query_active_main_flow",
        "stop_requires_explicit_intent": True,
        "explicit_stop_examples": ["停止", "终止", "强行停止", "stop this run", "terminate this run"],
        "non_stop_examples": ["状态怎么样", "补充要求", "调整方向", "继续完成", "为什么", "重新分析"],
    }
    acceptance = list(state.get("acceptance", []))
    required_acceptance = [
        "Completion ledger lists every user goal, required entrypoint, implementation surface, verification surface, external permission, and status using only done/failed/blocked/not_started.",
        "Both project-building modes are represented: step-by-step `$nexus-workflow` skill workflow and default autonomous multi-agent multi-round construction from a project intent.",
        "Skill replay cannot claim full `$nexus-workflow 调研` success unless online-search approval continuation reaches and evaluates the final report.",
        "False stop or lost subagent handles are recorded as recoverable monitor incidents and do not silently complete or discard the main flow.",
        "The monitor and every agent must read the shared problem_backlog/issue_memory before deciding completion or blockers.",
        "Every agent JSON artifact must include source_consumption; missing source_consumption is a monitor-visible gap, not completion.",
    ]
    for item in required_acceptance:
        if item not in acceptance:
            acceptance.append(item)
    state["acceptance"] = acceptance

    run_dir = Path(str(state.get("run_dir") or state_path.parent)).expanduser()
    task_dir = run_dir / "agent_tasks"
    artifacts_dir = run_dir / "artifacts"
    task_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for agent_id in REQUIRED_AGENTS:
        agent = state.get("agents", {}).get(agent_id)
        if not isinstance(agent, dict):
            continue
        current_spec = build_agent_specs().get(agent_id, {})
        for key in ["expected_outputs", "may_write", "must_not_write", "responsibility", "agent_type", "order"]:
            if key in current_spec:
                agent[key] = current_spec[key]
        task_path = Path(str(agent.get("task_prompt_path") or task_dir / f"{agent.get('order', 0):02d}_{agent_id}.md"))
        if not task_path.is_absolute():
            task_path = run_dir / task_path
        output_dir = Path(str(agent.get("output_dir") or artifacts_dir / agent_id))
        if not output_dir.is_absolute():
            output_dir = run_dir / output_dir
        agent["task_prompt_path"] = str(task_path)
        agent["output_dir"] = str(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        task_path.write_text(render_agent_task(agent_id, agent, state), encoding="utf-8")

    state.setdefault("events", []).append(
        {
            "timestamp": now,
            "type": "monitor_contract_migrated",
            "message": "State and agent task prompts were migrated to the current monitor contract.",
        }
    )


def record_agent_ids(state: dict[str, Any], assignments: list[str]) -> None:
    now = utc_now()
    recorded: dict[str, str] = {}
    for assignment in assignments:
        if "=" not in assignment:
            raise SystemExit(f"--record-agent-id must be ROLE=ID, got: {assignment}")
        role, agent_id = assignment.split("=", 1)
        role = role.strip()
        agent_id = agent_id.strip()
        if role not in REQUIRED_AGENTS:
            raise SystemExit(f"unknown agent role: {role}")
        if not agent_id:
            raise SystemExit(f"empty agent id for role: {role}")
        agent = state.get("agents", {}).get(role)
        if not isinstance(agent, dict):
            raise SystemExit(f"agent role missing from state: {role}")
        previous = str(agent.get("multi_agent_v1_id", "")).strip()
        if previous and previous != agent_id:
            agent.setdefault("previous_multi_agent_v1_ids", []).append(previous)
        agent["multi_agent_v1_id"] = agent_id
        agent["status"] = "running"
        agent["blocker"] = ""
        agent["last_update"] = now
        recorded[role] = agent_id

    agents = state.get("agents", {})
    all_recorded = all(
        isinstance(agents.get(role), dict) and str(agents[role].get("multi_agent_v1_id", "")).strip()
        for role in REQUIRED_AGENTS
    )
    if all_recorded:
        state["status"] = "awaiting_agent"
        state["phase"] = "agents_spawned_running"
        state["last_heartbeat_at"] = now
        state.setdefault("main_flow", {})["active"] = True
        state["main_flow"]["next_monitor_action"] = "wait_for_agent_outputs"
    state.setdefault("events", []).append(
        {
            "timestamp": now,
            "type": "agent_ids_recorded",
            "message": "Recorded monitor-spawned multi_agent_v1 ids.",
            "agent_ids": recorded,
            "all_required_agents_recorded": all_recorded,
        }
    )


def set_agent_statuses(state: dict[str, Any], assignments: list[str], blocker_assignments: list[str]) -> None:
    now = utc_now()
    blockers = parse_assignments(blocker_assignments)
    recorded: dict[str, str] = {}
    for assignment in assignments:
        role, status = parse_assignment(assignment, flag="--set-agent-status")
        if role not in REQUIRED_AGENTS:
            raise SystemExit(f"unknown agent role: {role}")
        agent = state.get("agents", {}).get(role)
        if not isinstance(agent, dict):
            raise SystemExit(f"agent role missing from state: {role}")
        agent["status"] = status
        agent["blocker"] = blockers.get(role, agent.get("blocker", ""))
        agent["last_update"] = now
        recorded[role] = status

    if any(status in {"pending_respawn", "running"} for status in recorded.values()):
        state["status"] = "awaiting_agent"
        state["phase"] = "agent_runtime_status_updated"
        state.setdefault("main_flow", {})["next_monitor_action"] = "respawn_or_wait_for_agents"
    elif any(status in {"blocked", "failed"} for status in recorded.values()):
        state["status"] = "blocked"
        state["phase"] = "agent_runtime_blocked"
        state.setdefault("main_flow", {})["next_monitor_action"] = "resolve_blockers_or_respawn_missing_agents"
    state.setdefault("main_flow", {})["active"] = state["status"] != "stopped"
    state.setdefault("events", []).append(
        {
            "timestamp": now,
            "type": "agent_runtime_status_recorded",
            "message": "Recorded monitor-observed agent runtime statuses.",
            "agent_statuses": recorded,
        }
    )


def parse_assignments(assignments: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for assignment in assignments:
        role, value = parse_assignment(assignment, flag="--status-blocker")
        parsed[role] = value
    return parsed


def parse_assignment(assignment: str, *, flag: str) -> tuple[str, str]:
    if "=" not in assignment:
        raise SystemExit(f"{flag} must be ROLE=VALUE, got: {assignment}")
    role, value = assignment.split("=", 1)
    role = role.strip()
    value = value.strip()
    if not role or not value:
        raise SystemExit(f"{flag} must be ROLE=VALUE, got: {assignment}")
    return role, value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
