from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "scripts" / "lab" / "init_monitor_multi_agent_run.py"
INSPECTOR = REPO_ROOT / "scripts" / "lab" / "inspect_nexus_lab_status.py"


def run_launcher(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAUNCHER), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_monitor_multi_agent_dry_run_declares_real_monitor_boundary(tmp_path: Path) -> None:
    completed = run_launcher("--dry-run-plan", "--e2e-root", str(tmp_path), "--run-id", "dry-run")

    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["schema"] == "nexus.lab.monitor_multi_agent_plan.v1"
    assert plan["execution_model"] == "monitor_multi_agent_v1"
    assert plan["not_execution_model"] == "orchestrated_subprocess_loop_only"
    assert plan["python_embeds_subagents"] is False
    assert plan["spawn_source"] == "current_codex_conversation_multi_agent_v1"
    assert [item["mode_id"] for item in plan["operation_modes"]] == [
        "step_by_step_skill_workflow",
        "default_autonomous_multi_agent_build",
    ]
    assert any("$nexus-workflow 调研" in item for item in plan["acceptance"])
    assert plan["shared_artifacts"]["problem_backlog"].endswith("problem_backlog.json")
    assert plan["shared_artifacts"]["issue_memory"] == plan["shared_artifacts"]["problem_backlog"]
    assert plan["shared_artifacts"]["completion_ledger"].endswith("nexus_verix_completion_ledger.json")
    ordered_agents = [agent_id for agent_id, _ in sorted(plan["agents"].items(), key=lambda item: item[1]["order"])]
    assert ordered_agents == [
        "nexus_execution",
        "skill_replay",
        "verix_audit",
        "nexus_modification",
        "state_audit",
    ]
    assert "Skill replay covers the full wljob 0-34 command chain and records prompt-level results." in plan["acceptance"]


def test_monitor_multi_agent_init_run_writes_state_and_task_prompts(tmp_path: Path) -> None:
    completed = run_launcher("--init-run", "--e2e-root", str(tmp_path), "--run-id", "init-test")

    assert completed.returncode == 0, completed.stderr
    packet = json.loads(completed.stdout)
    state_path = Path(packet["state_path"])
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["schema"] == "nexus.lab.monitor_multi_agent_state.v1"
    assert state["status"] == "ready_to_spawn"
    assert state["phase"] == "awaiting_monitor_spawn"
    assert state["execution_model"] == "monitor_multi_agent_v1"
    assert state["python_embeds_subagents"] is False
    assert state["main_flow"]["interruption_policy"].startswith("User questions update")
    assert state["state_path"] == str(state_path)
    backlog_path = Path(state["problem_backlog"]["path"])
    assert state["problem_backlog"]["issue_memory_path"] == str(backlog_path)
    assert state["problem_backlog"]["single_source_of_truth"] is True
    assert state["shared_artifacts"]["problem_backlog"] == str(backlog_path)
    assert state["shared_artifacts"]["issue_memory"] == str(backlog_path)
    assert backlog_path.exists()
    backlog = json.loads(backlog_path.read_text(encoding="utf-8"))
    assert backlog["schema"] == "nexus.lab.problem_backlog.v1"
    assert backlog["canonical_name"] == "problem_backlog"
    assert backlog["issue_memory_alias"] == "issue_memory"
    assert backlog["status_values_allowed"] == ["done", "failed", "blocked", "not_started"]
    assert backlog["problem_matrix"]["path"].endswith("scripts/lab/nexus_problem_matrix.json")
    assert backlog["problem_matrix"]["problem_count"] == 16
    assert any(item["id"] == "L10" for item in backlog["previous_unresolved_items"])
    assert any(item["id"] == "current_round_operator_command" for item in backlog["current_round_new_requirements"])
    assert {
        item["status"]
        for item in [*backlog["previous_unresolved_items"], *backlog["current_round_new_requirements"]]
    } <= {"done", "failed", "blocked", "not_started"}
    assert Path(state["events_log"]).exists()
    assert Path(state["approval_queue"]).exists()
    assert Path(state["handoffs_dir"]).is_dir()
    assert packet["events_log"] == state["events_log"]
    assert packet["artifact_merge_command"].endswith(f"merge_monitor_agent_artifacts.py --state {state_path}")

    for agent_id in packet["spawn_order"]:
        agent = state["agents"][agent_id]
        task_path = Path(agent["task_prompt_path"])
        assert agent["status"] == "pending_spawn"
        assert task_path.exists()
        task_text = task_path.read_text(encoding="utf-8")
        assert "You are a real Codex monitor-spawned subagent" in task_text
        assert "Required First Read" in task_text
        assert f"Problem backlog / issue memory: {backlog_path}" in task_text
        assert "Every JSON artifact you write must include a top-level `source_consumption` field" in task_text
        assert f"State path: {state_path}" in task_text
        assert f"Events log: {state['events_log']}" in task_text
        assert "<written after task generation>" not in task_text
        assert agent["output_dir"] in task_text
        assert "<assigned after task generation>" not in task_text
        assert "step_by_step_skill_workflow" in task_text
        assert "Reference1 Unresolved Proof Gaps" in task_text
        assert "heartbeat_status.json" in task_text


def test_status_inspector_reads_monitor_multi_agent_state(tmp_path: Path) -> None:
    completed = run_launcher("--init-run", "--e2e-root", str(tmp_path), "--run-id", "inspect-test")
    assert completed.returncode == 0, completed.stderr
    state_path = Path(json.loads(completed.stdout)["state_path"])

    inspected = subprocess.run(
        [sys.executable, str(INSPECTOR), "--loop-state", str(state_path), "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert inspected.returncode == 0, inspected.stderr
    summary = json.loads(inspected.stdout)
    assert summary["schema"] == "nexus.lab.monitor_summary.v1"
    assert summary["loop_id"] == "inspect-test"
    assert summary["execution_model"] == "monitor_multi_agent_v1"
    assert summary["agent_statuses"]["nexus_execution"] == "pending_spawn"
    assert "Spawn real Codex subagents" in summary["next_monitor_actions"][0]
