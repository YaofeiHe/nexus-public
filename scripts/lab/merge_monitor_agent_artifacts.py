#!/usr/bin/env python3
"""Merge Nexus-Verix monitor agent artifacts back into monitor_state.json."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_AGENTS = [
    "nexus_execution",
    "skill_replay",
    "verix_audit",
    "nexus_modification",
    "state_audit",
]

STATUS_PRIORITY = {
    "failed": 5,
    "fail": 5,
    "blocked": 4,
    "partial": 3,
    "partial_recoverable": 3,
    "running": 3,
    "completed": 2,
    "done": 2,
    "pass": 1,
    "not_started": 0,
    "missing": 0,
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan monitor agent artifacts and update monitor_state.json.")
    parser.add_argument("--state", type=Path, required=True, help="path to monitor_state.json")
    parser.add_argument("--json", action="store_true", help="print JSON summary")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    state_path = args.state.expanduser().resolve()
    state = load_state(state_path)
    summary = merge_artifacts(state)
    state["updated_at"] = utc_now()
    merge_event = {
        "timestamp": state["updated_at"],
        "type": "agent_artifacts_merged",
        "message": "Merged agent artifact status into monitor_state.json.",
        "agent_statuses": summary["agent_statuses"],
    }
    state.setdefault("events", []).append(merge_event)
    sync_events_log(state)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary["state_path"] = str(state_path)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"status={summary['status']} phase={summary['phase']}")
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


def merge_artifacts(state: dict[str, Any]) -> dict[str, Any]:
    artifact_index: dict[str, Any] = {}
    agent_statuses: dict[str, str] = {}
    blockers: dict[str, str] = {}
    missing_expected: dict[str, list[str]] = {}
    source_consumption: dict[str, Any] = {}
    now = utc_now()
    for role in REQUIRED_AGENTS:
        agent = state.get("agents", {}).get(role)
        if not isinstance(agent, dict):
            continue
        output_dir = Path(str(agent.get("output_dir", ""))).expanduser()
        scan = scan_agent(role, output_dir, [str(item) for item in agent.get("expected_outputs", [])])
        artifact_index[role] = scan
        status = scan["status"]
        agent["status"] = status
        agent["blocker"] = scan.get("blocker", "")
        agent["last_update"] = now
        agent["latest_artifacts"] = scan.get("latest_artifacts", {})
        agent["source_consumption"] = scan.get("source_consumption", {})
        if scan.get("recovery_context"):
            agent["recovery_context"] = scan["recovery_context"]
        agent_statuses[role] = status
        blockers[role] = scan.get("blocker", "")
        missing_expected[role] = scan["missing_expected_outputs"]
        source_consumption[role] = scan.get("source_consumption", {})

    state["artifact_index"] = artifact_index
    state["source_consumption"] = {
        "schema": "nexus.lab.monitor_source_consumption_index.v1",
        "updated_at": now,
        "agents": source_consumption,
        "missing_required": {
            role: details.get("missing_artifacts", [])
            for role, details in source_consumption.items()
            if isinstance(details, dict) and details.get("missing_artifacts")
        },
    }
    if any(status == "failed" for status in agent_statuses.values()):
        state["status"] = "blocked"
        state["phase"] = "agent_artifact_failure"
        state.setdefault("main_flow", {})["next_monitor_action"] = "inspect_failed_agent_artifacts"
    elif any(status == "running" for status in agent_statuses.values()):
        state["status"] = "awaiting_agent"
        state["phase"] = "awaiting_agent_outputs"
        state.setdefault("main_flow", {})["next_monitor_action"] = "wait_for_or_request_agent_heartbeats"
    elif any(status == "blocked" for status in agent_statuses.values()):
        state["status"] = "blocked"
        state["phase"] = "agent_outputs_blocked"
        state.setdefault("main_flow", {})["next_monitor_action"] = "resolve_blockers_or_respawn_missing_agents"
    else:
        state["status"] = "awaiting_agent"
        state["phase"] = "agent_outputs_ready_for_monitor_merge"
        state.setdefault("main_flow", {})["next_monitor_action"] = "merge_agent_outputs_and_decide_next_iteration"
    state.setdefault("main_flow", {})["active"] = state["status"] != "stopped"
    return {
        "schema": "nexus.lab.monitor_artifact_merge_result.v1",
        "status": state["status"],
        "phase": state["phase"],
        "agent_statuses": agent_statuses,
        "blockers": blockers,
        "missing_expected_outputs": missing_expected,
        "source_consumption": source_consumption,
        "artifact_index": artifact_index,
    }


def scan_agent(role: str, output_dir: Path, expected_outputs: list[str]) -> dict[str, Any]:
    files = sorted(path for path in output_dir.rglob("*") if path.is_file()) if output_dir.exists() else []
    files_by_name = {path.name: path for path in files}
    expected_paths = [output_dir / name for name in expected_outputs]
    missing_expected = [str(path) for path in expected_paths if not path.exists()]
    status_sources: list[dict[str, Any]] = []
    for name in dict.fromkeys(["heartbeat_status.json", *expected_outputs]):
        path = files_by_name.get(name)
        if not path or path.suffix != ".json":
            continue
        payload = load_optional_json(path)
        if payload:
            status_sources.append(
                {
                    "path": str(path),
                    "status": extract_status(payload),
                    "blocker": extract_blocker(payload),
                }
            )

    source_consumption_paths = [
        path
        for name in dict.fromkeys(["heartbeat_status.json", *expected_outputs])
        for path in [files_by_name.get(name)]
        if path is not None and path.suffix == ".json"
    ]
    source_consumption = collect_source_consumption(source_consumption_paths)
    status = infer_status(missing_expected=missing_expected, status_sources=status_sources)
    blocker = infer_blocker(missing_expected=missing_expected, status_sources=status_sources)
    if source_consumption["missing_artifacts"] and status in {"completed", "done", "pass"}:
        status = "blocked"
        source_blocker = "missing source_consumption in artifacts: " + ", ".join(source_consumption["missing_artifacts"])
        blocker = "; ".join(item for item in [blocker, source_blocker] if item)
    elif source_consumption["missing_artifacts"] and status == "blocked":
        source_blocker = "missing source_consumption in artifacts: " + ", ".join(source_consumption["missing_artifacts"])
        blocker = "; ".join(item for item in [blocker, source_blocker] if item)
    return {
        "role_id": role,
        "output_dir": str(output_dir),
        "status": status,
        "blocker": blocker,
        "expected_outputs": expected_outputs,
        "missing_expected_outputs": missing_expected,
        "files": [str(path) for path in files],
        "latest_artifacts": latest_artifacts(output_dir, expected_outputs),
        "recovery_context": collect_recovery_context(files),
        "source_consumption": source_consumption,
        "status_sources": status_sources,
    }


def infer_status(*, missing_expected: list[str], status_sources: list[dict[str, Any]]) -> str:
    source_statuses = [
        (normalize_status(str(item.get("status", ""))), Path(str(item.get("path", ""))).name)
        for item in status_sources
        if str(item.get("status", ""))
    ]
    artifact_statuses = [status for status, name in source_statuses if name != "heartbeat_status.json"]
    statuses = artifact_statuses or [status for status, _name in source_statuses]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "blocked" for status in statuses):
        return "blocked"
    if any(status == "partial" for status in statuses):
        if any(str(item.get("blocker", "")).strip() for item in status_sources):
            return "blocked"
        all_statuses = [status for status, _name in source_statuses]
        if not missing_expected and any(status in {"completed", "done", "pass"} for status in all_statuses):
            return "completed"
        return "running"
    if missing_expected:
        if any(status == "running" for status in statuses):
            return "running"
        return "blocked"
    terminal_success = [status for status in statuses if status in {"completed", "done", "pass"}]
    if terminal_success:
        return max(terminal_success, key=lambda value: STATUS_PRIORITY.get(value, 0))
    if statuses:
        return max(statuses, key=lambda value: STATUS_PRIORITY.get(value, 0))
    return "blocked"


def infer_blocker(*, missing_expected: list[str], status_sources: list[dict[str, Any]]) -> str:
    explicit = [str(item.get("blocker", "")).strip() for item in status_sources if str(item.get("blocker", "")).strip()]
    if explicit:
        return "; ".join(explicit)
    if missing_expected:
        return "missing expected outputs: " + ", ".join(missing_expected)
    return ""


def load_optional_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def extract_status(payload: dict[str, Any]) -> str:
    for key in ("status", "overall_status", "overall_verdict", "verdict"):
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("status") or value.get("verdict")
        if isinstance(value, str) and value:
            return normalize_status(value)
    return ""


def normalize_status(status: str) -> str:
    value = status.strip()
    if value == "fail":
        return "failed"
    if value == "failed":
        return "failed"
    if value.startswith("partial"):
        return "partial"
    return value


def extract_blocker(payload: dict[str, Any]) -> str:
    for key in ("blocker", "blocked_reason", "status_reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    provider_blocker = payload.get("provider_blocker")
    if isinstance(provider_blocker, dict) and provider_blocker.get("blocked"):
        reason = str(provider_blocker.get("stage") or provider_blocker.get("blocked_reason") or "provider_blocked")
        provider = str(provider_blocker.get("provider") or "")
        return f"{provider} {reason}".strip()
    return ""


def latest_artifacts(output_dir: Path, expected_outputs: list[str]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for name in expected_outputs:
        path = output_dir / name
        latest[name] = {
            "path": str(path),
            "exists": path.exists(),
            "status": extract_status(load_optional_json(path)) if path.suffix == ".json" and path.exists() else "",
        }
    return latest


def collect_source_consumption(json_files: list[Path]) -> dict[str, Any]:
    consumed_sources: list[Any] = []
    artifacts: dict[str, Any] = {}
    missing_artifacts: list[str] = []
    invalid_artifacts: list[str] = []
    for path in json_files:
        payload = load_optional_json(path)
        if not payload:
            invalid_artifacts.append(str(path))
            missing_artifacts.append(str(path))
            artifacts[str(path)] = {
                "status": "blocked",
                "source_consumption_present": False,
                "reason": "invalid_or_empty_json",
            }
            continue
        value = payload.get("source_consumption")
        present = source_consumption_present(value)
        artifacts[str(path)] = {
            "status": "done" if present else "not_started",
            "source_consumption_present": present,
            "source_consumption": value if present else [],
        }
        if present:
            consumed_sources.append(value)
        else:
            missing_artifacts.append(str(path))
    status = "done" if json_files and not missing_artifacts else "not_started"
    return {
        "schema": "nexus.lab.agent_source_consumption.v1",
        "status": status,
        "json_artifacts_checked": [str(path) for path in json_files],
        "missing_artifacts": missing_artifacts,
        "invalid_artifacts": invalid_artifacts,
        "artifacts": artifacts,
        "consumed_sources": consumed_sources,
    }


def source_consumption_present(value: Any) -> bool:
    if isinstance(value, list):
        return any(item for item in value)
    if isinstance(value, dict):
        return bool(value)
    return False


def collect_recovery_context(files: list[Path]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for path in files:
        if path.suffix != ".json":
            continue
        payload = load_optional_json(path)
        if not payload:
            continue
        pending_actions = payload.get("pending_actions")
        if isinstance(pending_actions, list) and pending_actions:
            contexts.append(
                {
                    "path": str(path),
                    "blocked_reason": str(payload.get("blocked_reason", "")),
                    "lifecycle_status": str(payload.get("lifecycle_status", "")),
                    "next_task_prompt": str(payload.get("next_task_prompt", "")),
                    "pending_actions": pending_actions,
                }
            )
        provider_blocker = payload.get("provider_blocker")
        if isinstance(provider_blocker, dict) and provider_blocker.get("blocked"):
            contexts.append(
                {
                    "path": str(path),
                    "blocked_reason": str(provider_blocker.get("stage") or "provider_blocked"),
                    "provider": str(provider_blocker.get("provider", "")),
                    "pending_actions": provider_blocker.get("pending_actions", []),
                }
            )
    return contexts


def sync_events_log(state: dict[str, Any]) -> None:
    events_log = state.get("events_log")
    if not isinstance(events_log, str) or not events_log:
        return
    path = Path(events_log).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[tuple[str, str]] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                existing.add((str(payload.get("type", "")), str(payload.get("message", ""))))
    with path.open("a", encoding="utf-8") as handle:
        for event in state.get("events", []):
            if not isinstance(event, dict):
                continue
            key = (str(event.get("type", "")), str(event.get("message", "")))
            if key in existing:
                continue
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            existing.add(key)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
