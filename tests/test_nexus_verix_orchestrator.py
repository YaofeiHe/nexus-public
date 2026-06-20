from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = REPO_ROOT / "scripts" / "lab" / "run_nexus_verix_orchestrator.py"
INSPECTOR = REPO_ROOT / "scripts" / "lab" / "inspect_nexus_lab_status.py"


def load_orchestrator_module():
    spec = importlib.util.spec_from_file_location("run_nexus_verix_orchestrator", ORCHESTRATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nexus_verix_orchestrator_dry_run_plan_lists_full_loop(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ORCHESTRATOR),
            "--nexus-root",
            str(REPO_ROOT),
            "--e2e-root",
            str(tmp_path),
            "--dry-run-plan",
            "--skill-replay-mode",
            "queue-only",
            "--verix-mode",
            "off",
            "--patch-mode",
            "off",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["schema"] == "nexus.lab.nexus_verix_orchestrator_plan.v1"
    assert plan["execution_model"] == "orchestrated_subprocess_loop"
    assert plan["execution_boundary"]["execution_model"] == "orchestrated_subprocess_loop"
    assert plan["execution_boundary"]["not_execution_model"] == "multi_agent_v1"
    assert plan["execution_boundary"]["python_orchestrator_embeds_subagents"] is False
    assert plan["execution_boundary"]["real_subagent_spawn_source"] == "current_codex_monitor_multi_agent_v1_only"
    assert plan["satisfies_start_run_contract"] is False
    assert plan["requires_external_monitor_spawn"] is True
    assert plan["subprocess_agents_are_not_real_agents"] is True
    assert plan["execution_boundary"]["satisfies_start_run_contract"] is False
    assert "run_all_nexus_cases" in plan["phases"]
    assert "run_skill_replay_surface_including_full_wljob_0_34_chain" in plan["phases"]
    assert "run_verix_independent_audit" in plan["phases"]
    assert "patch_nexus_general_mechanisms" in plan["phases"]
    assert plan["objective"] == "construct, test, and repair Nexus-Verix itself until the self-build acceptance loop passes"
    assert plan["regression_mode"] == "full"
    assert plan["problem_matrix"]["problem_count"] == 16


def test_orchestrator_verdicts_block_missing_subprocess_dependencies() -> None:
    module = load_orchestrator_module()

    assert module.iteration_verdict({"lab": {"status": "pass"}, "skill_replay": {"status": "pass"}, "verix": {"status": "pass"}}) == "pass"
    assert module.iteration_verdict({"lab": {"status": "blocked_external_side_effects"}, "skill_replay": {"status": "pass"}, "verix": {"status": "pass"}}) == "blocked_external_side_effects"
    assert module.iteration_verdict({"lab": {"status": "blocked_external_side_effects"}, "skill_replay": {"status": "blocked_skill_replay_timeout"}, "verix": {"status": "blocked"}}) == "blocked_skill_replay_timeout"
    assert module.iteration_verdict({"lab": {"status": "pass"}, "skill_replay": {"status": "blocked_no_skill_replay"}, "verix": {"status": "pass"}}) == "blocked_no_skill_replay"
    assert module.iteration_verdict({"lab": {"status": "pass"}, "skill_replay": {"status": "blocked_skill_replay_timeout"}, "verix": {"status": "pass"}}) == "blocked_skill_replay_timeout"
    assert module.iteration_verdict({"lab": {"status": "pass"}, "skill_replay": {"status": "pass"}, "verix": {"status": "blocked_no_verix"}}) == "blocked_no_verix"
    assert module.iteration_verdict({"lab": {"status": "fail"}, "skill_replay": {"status": "pass"}, "verix": {"status": "pass"}}) == "needs_patch"


def test_status_inspector_reads_orchestrator_state(tmp_path: Path) -> None:
    state = {
        "schema": "nexus.lab.nexus_verix_orchestrator_state.v1",
        "run_id": "orchestrator-test",
        "status": "running",
        "phase": "run_verix_independent_audit",
        "started_at": "2026-06-18T00:00:00+00:00",
        "finished_at": "",
        "stop_file": str(tmp_path / "STOP"),
        "plan": {"nexus_root": str(REPO_ROOT), "e2e_root": str(tmp_path)},
        "iterations": [
            {
                "iteration": 1,
                "status": "running",
                "lab": {"status": "pass", "loop_state_path": str(tmp_path / "lab-loop.json")},
                "skill_replay": {"status": "pass"},
                "verix": {"status": "blocked"},
                "merged_feedback": {"path": str(tmp_path / "request.json")},
            }
        ],
    }
    state_path = tmp_path / "orchestrator_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(INSPECTOR), "--loop-state", str(state_path), "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["loop_id"] == "orchestrator-test"
    assert summary["phase"] == "run_verix_independent_audit"
    assert summary["agent_statuses"]["verix"] == "blocked"


def test_heartbeat_command_timeout_decodes_partial_utf8(tmp_path: Path) -> None:
    module = load_orchestrator_module()
    status_path = tmp_path / "step_status.json"
    aggregate_path = tmp_path / "aggregate_status.json"

    result = module.run_command_with_heartbeat(
        [
            sys.executable,
            "-c",
            "import sys,time; sys.stdout.buffer.write(b'\\xe6'); sys.stdout.flush(); time.sleep(2)",
        ],
        cwd=REPO_ROOT,
        timeout=1,
        interval=1,
        step_status_path=status_path,
        aggregate_status_path=aggregate_path,
        current_step={"case_id": "case", "step_id": "step", "step_index": 1, "total_steps": 1},
    )

    assert result["returncode"] == 124
    assert "�" in result["stdout"]
    step_status = json.loads(status_path.read_text(encoding="utf-8"))
    assert step_status["status"] == "timeout"
    assert step_status["timed_out"] is True
