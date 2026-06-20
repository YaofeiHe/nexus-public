from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INIT = REPO_ROOT / "scripts" / "lab" / "init_monitor_multi_agent_run.py"
UPDATE = REPO_ROOT / "scripts" / "lab" / "update_monitor_multi_agent_run.py"


def test_monitor_update_recovers_false_stop_and_requires_respawn(tmp_path: Path) -> None:
    run_id = "test-false-stop"
    e2e_root = tmp_path / "e2e"
    subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--init-run",
            "--e2e-root",
            str(e2e_root),
            "--run-id",
            run_id,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    state_path = e2e_root / "monitor-runs" / run_id / "monitor_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for agent in state["agents"].values():
        agent["status"] = "shutdown"
        agent["multi_agent_v1_id"] = "agent-old-id"
        agent["blocker"] = "user_requested_termination"
    state["status"] = "stopped"
    state["phase"] = "stopped_by_user"
    state["finished_at"] = "2026-06-19T00:00:00+00:00"
    stop_file = Path(state["stop_file"])
    stop_file.write_text(json.dumps({"reason": "user_requested_termination"}), encoding="utf-8")
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(UPDATE),
            "--state",
            str(state_path),
            "--instruction",
            "补充要求，不是停止",
            "--migrate-current-contract",
            "--recover-false-stop",
            "--agent-handles-lost",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    updated = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated["status"] == "ready_to_spawn"
    assert updated["phase"] == "recovering_after_false_stop_agent_respawn_required"
    assert "finished_at" not in updated
    assert updated["main_flow"]["active"] is True
    assert updated["main_flow"]["next_monitor_action"] == "respawn_lost_agents"
    assert updated["instruction_log"][-1]["classification"] == "main_flow_amendment"
    backlog = json.loads(Path(updated["problem_backlog"]["path"]).read_text(encoding="utf-8"))
    assert any(
        item["title"] == "补充要求，不是停止"
        for item in backlog["current_round_new_requirements"]
    )
    assert backlog["status_values_allowed"] == ["done", "failed", "blocked", "not_started"]
    assert updated["events"][-1]["type"] == "false_stop_recovered"
    assert any(event["type"] == "monitor_contract_migrated" for event in updated["events"])
    assert [item["mode_id"] for item in updated["operation_modes"]] == [
        "step_by_step_skill_workflow",
        "default_autonomous_multi_agent_build",
    ]
    assert not stop_file.exists()
    archived = updated["resume_contract"]["archived_false_positive_stop_file"]
    assert Path(archived).exists()
    for agent in updated["agents"].values():
        assert agent["status"] == "pending_respawn"
        assert agent["multi_agent_v1_id"] == ""
        assert agent["previous_multi_agent_v1_ids"] == ["agent-old-id"]
        assert "heartbeat_status.json" in agent["expected_outputs"]
        task_text = Path(agent["task_prompt_path"]).read_text(encoding="utf-8")
        assert agent["output_dir"] in task_text
        assert "Reference1 Unresolved Proof Gaps" in task_text
        assert f"Problem backlog / issue memory: {updated['problem_backlog']['path']}" in task_text
        assert "source_consumption" in task_text


def test_monitor_update_records_respawned_agent_ids(tmp_path: Path) -> None:
    run_id = "test-record-agent-ids"
    e2e_root = tmp_path / "e2e"
    subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--init-run",
            "--e2e-root",
            str(e2e_root),
            "--run-id",
            run_id,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    state_path = e2e_root / "monitor-runs" / run_id / "monitor_state.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(UPDATE),
            "--state",
            str(state_path),
            "--record-agent-id",
            "nexus_execution=agent-1",
            "--record-agent-id",
            "skill_replay=agent-2",
            "--record-agent-id",
            "verix_audit=agent-3",
            "--record-agent-id",
            "nexus_modification=agent-4",
            "--record-agent-id",
            "state_audit=agent-5",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    updated = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated["status"] == "awaiting_agent"
    assert updated["phase"] == "agents_spawned_running"
    assert updated["main_flow"]["active"] is True
    assert updated["main_flow"]["next_monitor_action"] == "wait_for_agent_outputs"
    assert updated["agents"]["nexus_execution"]["multi_agent_v1_id"] == "agent-1"
    assert updated["agents"]["state_audit"]["status"] == "running"
    assert updated["events"][-1]["type"] == "agent_ids_recorded"
    assert updated["events"][-1]["all_required_agents_recorded"] is True

    second = subprocess.run(
        [
            sys.executable,
            str(UPDATE),
            "--state",
            str(state_path),
            "--record-agent-id",
            "nexus_execution=agent-1b",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    updated_again = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated_again["agents"]["nexus_execution"]["multi_agent_v1_id"] == "agent-1b"
    assert updated_again["agents"]["nexus_execution"]["previous_multi_agent_v1_ids"] == ["agent-1"]


def test_monitor_update_records_agent_runtime_status(tmp_path: Path) -> None:
    run_id = "test-runtime-status"
    e2e_root = tmp_path / "e2e"
    subprocess.run(
        [
            sys.executable,
            str(INIT),
            "--init-run",
            "--e2e-root",
            str(e2e_root),
            "--run-id",
            run_id,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    state_path = e2e_root / "monitor-runs" / run_id / "monitor_state.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(UPDATE),
            "--state",
            str(state_path),
            "--set-agent-status",
            "nexus_execution=pending_respawn",
            "--status-blocker",
            "nexus_execution=closed before required summary outputs",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    updated = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated["agents"]["nexus_execution"]["status"] == "pending_respawn"
    assert updated["agents"]["nexus_execution"]["blocker"] == "closed before required summary outputs"
    assert updated["main_flow"]["next_monitor_action"] == "respawn_or_wait_for_agents"
    assert updated["events"][-1]["type"] == "agent_runtime_status_recorded"
