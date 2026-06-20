#!/usr/bin/env python3
"""Inspect or stop a Nexus lab loop from the monitor surface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_E2E_ROOT = Path("/tmp/nexus-real-e2e-lab")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect Nexus lab loop state or request a graceful stop.")
    parser.add_argument("--loop-state", type=Path, help="path to loop_state.json")
    parser.add_argument("--e2e-root", type=Path, default=DEFAULT_E2E_ROOT, help="root containing lab-loops/")
    parser.add_argument("--latest", action="store_true", help="inspect the latest loop_state.json under --e2e-root")
    parser.add_argument("--request-stop", action="store_true", help="write the loop stop marker recorded in loop_state.json")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    state_path = resolve_state_path(args)
    state = load_json(state_path)
    stop_written = ""
    if args.request_stop:
        stop_written = request_stop(state, state_path)
    summary = build_summary(state, state_path=state_path, stop_written=stop_written)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(summary))
    return 0


def resolve_state_path(args: argparse.Namespace) -> Path:
    if args.loop_state:
        path = args.loop_state.expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"loop_state.json not found: {path}")
        return path
    if args.latest:
        root = args.e2e_root.expanduser().resolve() / "lab-loops"
        candidates = sorted(root.glob("*/loop_state.json"))
        if not candidates:
            raise SystemExit(f"no loop_state.json found under {root}")
        return candidates[-1]
    raise SystemExit("provide --loop-state or --latest")


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


def request_stop(state: dict[str, Any], state_path: Path) -> str:
    stop_file_text = str(state.get("stop_file", "")) or str(state_path.parent / "STOP")
    stop_file = Path(stop_file_text).expanduser()
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "nexus.lab.stop_request.v1",
        "requested_at": utc_now(),
        "loop_id": state.get("loop_id", ""),
        "reason": "requested by monitor",
    }
    stop_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(stop_file)


def build_summary(state: dict[str, Any], *, state_path: Path, stop_written: str = "") -> dict[str, Any]:
    if state.get("schema") == "nexus.lab.monitor_multi_agent_state.v1":
        return build_monitor_multi_agent_summary(state, state_path=state_path, stop_written=stop_written)
    if state.get("schema") == "nexus.lab.nexus_verix_orchestrator_state.v1":
        return build_orchestrator_summary(state, state_path=state_path, stop_written=stop_written)
    iterations = [item for item in state.get("iterations", []) if isinstance(item, dict)]
    current = iterations[-1] if iterations else {}
    case_results = [item for item in current.get("case_results", []) if isinstance(item, dict)]
    counts: dict[str, int] = {}
    for case in case_results:
        status = str(case.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema": "nexus.lab.monitor_summary.v1",
        "generated_at": utc_now(),
        "loop_state": str(state_path),
        "loop_id": state.get("loop_id", ""),
        "status": state.get("status", ""),
        "started_at": state.get("started_at", ""),
        "finished_at": state.get("finished_at", ""),
        "nexus_root": state.get("nexus_root", ""),
        "e2e_root": state.get("e2e_root", ""),
        "stop_file": state.get("stop_file", str(state_path.parent / "STOP")),
        "stop_requested_file_written": stop_written,
        "current_iteration": current.get("iteration"),
        "iteration_status": current.get("status", ""),
        "summary_path": current.get("summary_path", ""),
        "modification_request_path": current.get("modification_request_path", ""),
        "modification_plan_path": current.get("modification_plan_path", ""),
        "case_status_counts": counts,
        "case_results": [
            {
                "case_id": case.get("case_id", ""),
                "status": case.get("status", ""),
                "execution_path": case.get("execution_path", ""),
                "evaluation_path": case.get("evaluation_path", ""),
            }
            for case in case_results
        ],
        "next_monitor_actions": next_actions(state, current),
    }


def build_orchestrator_summary(state: dict[str, Any], *, state_path: Path, stop_written: str = "") -> dict[str, Any]:
    iterations = [item for item in state.get("iterations", []) if isinstance(item, dict)]
    current = iterations[-1] if iterations else {}
    return {
        "schema": "nexus.lab.monitor_summary.v1",
        "generated_at": utc_now(),
        "loop_state": str(state_path),
        "loop_id": state.get("run_id", ""),
        "status": state.get("status", ""),
        "phase": state.get("phase", ""),
        "started_at": state.get("started_at", ""),
        "finished_at": state.get("finished_at", ""),
        "nexus_root": state.get("plan", {}).get("nexus_root", ""),
        "e2e_root": state.get("plan", {}).get("e2e_root", ""),
        "execution_model": state.get("execution_model", ""),
        "monitor_level_multi_agent": False,
        "spawned_agent_count": 0,
        "missing_required_agents": ["nexus_execution", "skill_replay", "verix_audit", "nexus_modification", "state_audit"],
        "stop_file": state.get("stop_file", str(state_path.parent / "STOP")),
        "stop_requested_file_written": stop_written,
        "current_iteration": current.get("iteration"),
        "iteration_status": current.get("status", ""),
        "summary_path": current.get("lab", {}).get("loop_state_path", "") if isinstance(current.get("lab"), dict) else "",
        "modification_request_path": current.get("merged_feedback", {}).get("path", "") if isinstance(current.get("merged_feedback"), dict) else "",
        "modification_plan_path": current.get("lab", {}).get("modification_plan_path", "") if isinstance(current.get("lab"), dict) else "",
        "case_status_counts": {},
        "agent_statuses": {
            "lab": current.get("lab", {}).get("status", "") if isinstance(current.get("lab"), dict) else "",
            "skill_replay": current.get("skill_replay", {}).get("status", "") if isinstance(current.get("skill_replay"), dict) else "",
            "verix": current.get("verix", {}).get("status", "") if isinstance(current.get("verix"), dict) else "",
            "patch": current.get("patch", {}).get("status", "") if isinstance(current.get("patch"), dict) else "",
        },
        "case_results": [],
        "next_monitor_actions": next_actions(state, current),
    }


def build_monitor_multi_agent_summary(state: dict[str, Any], *, state_path: Path, stop_written: str = "") -> dict[str, Any]:
    agents = state.get("agents", {})
    agents = agents if isinstance(agents, dict) else {}
    required = ["nexus_execution", "skill_replay", "verix_audit", "nexus_modification", "state_audit"]
    missing = [agent_id for agent_id in required if agent_id not in agents]
    spawned = [
        agent_id
        for agent_id, agent in agents.items()
        if isinstance(agent, dict) and str(agent.get("multi_agent_v1_id", "")).strip()
    ]
    return {
        "schema": "nexus.lab.monitor_summary.v1",
        "generated_at": utc_now(),
        "loop_state": str(state_path),
        "loop_id": state.get("run_id", ""),
        "status": state.get("status", ""),
        "phase": state.get("phase", ""),
        "started_at": state.get("created_at", ""),
        "finished_at": state.get("finished_at", ""),
        "nexus_root": state.get("nexus_root", ""),
        "e2e_root": state.get("run_dir", ""),
        "execution_model": state.get("execution_model", ""),
        "monitor_thread_id": state.get("monitor_thread_id", ""),
        "monitor_level_multi_agent": state.get("execution_model") == "monitor_multi_agent_v1",
        "spawned_agent_count": len(spawned),
        "missing_required_agents": missing,
        "stop_file": state.get("stop_file", str(state_path.parent / "STOP")),
        "stop_requested_file_written": stop_written,
        "current_iteration": state.get("current_iteration"),
        "iteration_status": state.get("status", ""),
        "summary_path": "",
        "modification_request_path": "",
        "modification_plan_path": "",
        "case_status_counts": {},
        "agent_statuses": {
            agent_id: agent.get("status", "") if isinstance(agent, dict) else ""
            for agent_id, agent in agents.items()
        },
        "agents": [
            {
                "role_id": agent_id,
                "status": agent.get("status", "") if isinstance(agent, dict) else "",
                "multi_agent_v1_id": agent.get("multi_agent_v1_id", "") if isinstance(agent, dict) else "",
                "last_update": agent.get("last_update", "") if isinstance(agent, dict) else "",
                "blocker": agent.get("blocker", "") if isinstance(agent, dict) else "",
                "task_prompt_path": agent.get("task_prompt_path", "") if isinstance(agent, dict) else "",
                "output_dir": agent.get("output_dir", "") if isinstance(agent, dict) else "",
            }
            for agent_id, agent in agents.items()
        ],
        "case_results": [],
        "next_monitor_actions": next_actions(state, {}),
    }


def next_actions(state: dict[str, Any], current: dict[str, Any]) -> list[str]:
    status = str(state.get("status", ""))
    actions: list[str] = []
    if state.get("schema") == "nexus.lab.monitor_multi_agent_state.v1":
        agents = state.get("agents", {})
        agents = agents if isinstance(agents, dict) else {}
        unspawned = [
            agent_id
            for agent_id, agent in agents.items()
            if isinstance(agent, dict) and not str(agent.get("multi_agent_v1_id", "")).strip()
        ]
        if unspawned:
            actions.append("Spawn real Codex subagents with multi_agent_v1.spawn_agent for: " + ", ".join(unspawned))
        elif status in {"running", "awaiting_agent"}:
            actions.append("Use wait_agent for active subagents, then merge outputs into monitor_state.json without ending the main flow.")
        elif status == "awaiting_user":
            actions.append("Present the pending user decision and continue the same monitor run after the user responds.")
        elif status == "stopped":
            actions.append("Only treat this as terminal if events/STOP show an explicit user stop request; otherwise recover the same monitor run with update_monitor_multi_agent_run.py --recover-false-stop.")
        else:
            actions.append("Inspect monitor_state.json, agent outputs, and events.jsonl for the next monitor action.")
    elif status == "running":
        actions.append("Inspect case_results in loop_state.json or write the stop marker to request a graceful stop.")
    elif status == "needs_modification_agent":
        request_path = current.get("modification_request_path", "")
        plan_path = current.get("modification_plan_path", "")
        actions.append(f"Read {request_path} and {plan_path}, then patch Nexus general mechanisms before rerunning all selected cases.")
    elif status == "blocked_external_side_effects":
        actions.append("Review refused external side-effect cases; rerun only with --allow-external-side-effects after explicit authorization.")
    elif status == "blocked_skill_replay_timeout":
        actions.append("Inspect skill_replay_runtime_status.json and the current step runtime_status.json; the Codex skill replay subprocess timed out before returning a response.")
    elif status == "stopped_by_monitor":
        actions.append("Resume by starting a new loop with the same cases/e2e-root after checking partial artifacts.")
    elif status == "pass":
        actions.append("Archive loop_state.json, iteration_summary.json, evaluations, and modification/retest evidence.")
    elif state.get("schema") == "nexus.lab.nexus_verix_orchestrator_state.v1":
        phase = state.get("phase", "")
        if status.startswith("blocked"):
            actions.append(f"Inspect orchestrator phase {phase}, current iteration agent_statuses, and stop/authorization requirements before rerun.")
        else:
            actions.append("Inspect orchestrator_state.json for lab, skill replay, Verix, patch, and regression artifacts.")
    else:
        actions.append("Inspect loop_state.json for the latest status and failed case paths.")
    return actions


def render_text(summary: dict[str, Any]) -> str:
    lines = [
        f"loop_id: {summary.get('loop_id', '')}",
        f"status: {summary.get('status', '')}",
        f"phase: {summary.get('phase', '')}",
        f"current_iteration: {summary.get('current_iteration', '')}",
        f"iteration_status: {summary.get('iteration_status', '')}",
        f"loop_state: {summary.get('loop_state', '')}",
        f"execution_model: {summary.get('execution_model', '')}",
        f"monitor_level_multi_agent: {summary.get('monitor_level_multi_agent', '')}",
        f"spawned_agent_count: {summary.get('spawned_agent_count', '')}",
        f"summary_path: {summary.get('summary_path', '')}",
        f"modification_request_path: {summary.get('modification_request_path', '')}",
        f"modification_plan_path: {summary.get('modification_plan_path', '')}",
        f"stop_file: {summary.get('stop_file', '')}",
    ]
    if summary.get("stop_requested_file_written"):
        lines.append(f"stop_requested_file_written: {summary['stop_requested_file_written']}")
    counts = summary.get("case_status_counts", {})
    if counts:
        lines.append("case_status_counts: " + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    agent_statuses = summary.get("agent_statuses", {})
    if agent_statuses:
        lines.append("agent_statuses: " + json.dumps(agent_statuses, ensure_ascii=False, sort_keys=True))
    missing = summary.get("missing_required_agents", [])
    if missing:
        lines.append("missing_required_agents: " + json.dumps(missing, ensure_ascii=False))
    actions = summary.get("next_monitor_actions", [])
    if actions:
        lines.append("next_monitor_actions:")
        lines.extend(f"- {action}" for action in actions)
    return "\n".join(lines)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
