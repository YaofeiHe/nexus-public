from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INIT = REPO_ROOT / "scripts" / "lab" / "init_monitor_multi_agent_run.py"
MERGE = REPO_ROOT / "scripts" / "lab" / "merge_monitor_agent_artifacts.py"


def load_merge_module():
    spec = importlib.util.spec_from_file_location("merge_monitor_agent_artifacts", MERGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_merge_monitor_agent_artifacts_records_blocked_missing_outputs(tmp_path: Path) -> None:
    run_id = "merge-test"
    subprocess.run(
        [sys.executable, str(INIT), "--init-run", "--e2e-root", str(tmp_path), "--run-id", run_id],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    state_path = tmp_path / "monitor-runs" / run_id / "monitor_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    skill_dir = Path(state["agents"]["skill_replay"]["output_dir"])
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "heartbeat_status.json").write_text(
        json.dumps({"status": "running", "blocker": "", "role_id": "skill_replay"}),
        encoding="utf-8",
    )
    (skill_dir / "skill_replay_summary.json").write_text(
        json.dumps({"status": "blocked", "blocker": "waiting_for_real_thread"}),
        encoding="utf-8",
    )
    (skill_dir / "prompt_turns.jsonl").write_text("{}", encoding="utf-8")
    verix_dir = Path(state["agents"]["verix_audit"]["output_dir"])
    verix_dir.mkdir(parents=True, exist_ok=True)
    (verix_dir / "heartbeat_status.json").write_text(json.dumps({"status": "done"}), encoding="utf-8")
    (verix_dir / "verix_verdict.json").write_text(json.dumps({"overall_verdict": "failed"}), encoding="utf-8")
    (verix_dir / "audit_evidence_index.json").write_text(json.dumps({"status": "done"}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(MERGE), "--state", str(state_path), "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["agent_statuses"]["skill_replay"] == "blocked"
    assert result["agent_statuses"]["verix_audit"] == "failed"
    assert result["status"] == "blocked"
    updated = json.loads(state_path.read_text(encoding="utf-8"))
    assert updated["agents"]["skill_replay"]["status"] == "blocked"
    assert "waiting_for_real_thread" in updated["agents"]["skill_replay"]["blocker"]
    assert "missing source_consumption" in updated["agents"]["skill_replay"]["blocker"]
    assert updated["artifact_index"]["skill_replay"]["missing_expected_outputs"] == []
    assert updated["artifact_index"]["skill_replay"]["source_consumption"]["status"] == "not_started"
    assert str(skill_dir / "heartbeat_status.json") in updated["source_consumption"]["missing_required"]["skill_replay"]
    assert updated["agents"]["skill_replay"]["latest_artifacts"]["skill_replay_summary.json"]["exists"] is True
    status_source_paths = [item["path"] for item in updated["artifact_index"]["skill_replay"]["status_sources"]]
    assert status_source_paths.count(str(skill_dir / "heartbeat_status.json")) == 1
    events = [
        json.loads(line)
        for line in Path(updated["events_log"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(event["type"] == "agent_artifacts_merged" for event in events)


def test_terminal_artifact_status_overrides_stale_running_heartbeat() -> None:
    module = load_merge_module()

    status = module.infer_status(
        missing_expected=[],
        status_sources=[
            {"path": "/tmp/agent/heartbeat_status.json", "status": "running"},
            {"path": "/tmp/agent/state_audit_report.json", "status": "done"},
        ],
    )

    assert status == "done"


def test_partial_report_with_done_heartbeat_is_completed_not_running() -> None:
    module = load_merge_module()

    status = module.infer_status(
        missing_expected=[],
        status_sources=[
            {"path": "/tmp/agent/heartbeat_status.json", "status": "done", "blocker": ""},
            {"path": "/tmp/agent/state_audit_report.json", "status": "partial_recoverable", "blocker": ""},
        ],
    )

    assert status == "completed"


def test_missing_source_consumption_blocks_terminal_success(tmp_path: Path) -> None:
    module = load_merge_module()
    output_dir = tmp_path / "state_audit"
    output_dir.mkdir()
    (output_dir / "heartbeat_status.json").write_text(
        json.dumps({"status": "done", "role_id": "state_audit"}),
        encoding="utf-8",
    )
    (output_dir / "state_audit_report.json").write_text(
        json.dumps({"status": "done", "verdict": "done"}),
        encoding="utf-8",
    )

    scan = module.scan_agent("state_audit", output_dir, ["heartbeat_status.json", "state_audit_report.json"])

    assert scan["status"] == "blocked"
    assert "missing source_consumption" in scan["blocker"]
    assert scan["source_consumption"]["status"] == "not_started"
    assert str(output_dir / "heartbeat_status.json") in scan["source_consumption"]["missing_artifacts"]


def test_source_consumption_allows_terminal_success(tmp_path: Path) -> None:
    module = load_merge_module()
    output_dir = tmp_path / "state_audit"
    output_dir.mkdir()
    source_consumption = [
        {
            "path": "/tmp/problem_backlog.json",
            "status": "done",
        }
    ]
    (output_dir / "heartbeat_status.json").write_text(
        json.dumps({"status": "done", "role_id": "state_audit", "source_consumption": source_consumption}),
        encoding="utf-8",
    )
    (output_dir / "state_audit_report.json").write_text(
        json.dumps({"status": "done", "verdict": "done", "source_consumption": source_consumption}),
        encoding="utf-8",
    )

    scan = module.scan_agent("state_audit", output_dir, ["heartbeat_status.json", "state_audit_report.json"])

    assert scan["status"] == "done"
    assert scan["blocker"] == ""
    assert scan["source_consumption"]["status"] == "done"
    assert scan["source_consumption"]["missing_artifacts"] == []


def test_nested_tool_artifacts_do_not_require_agent_source_consumption(tmp_path: Path) -> None:
    module = load_merge_module()
    output_dir = tmp_path / "verix_audit"
    nested = output_dir / "live_audit" / "checkpoints"
    nested.mkdir(parents=True)
    source_consumption = [{"path": "/tmp/problem_backlog.json", "status": "read"}]
    for name in ["heartbeat_status.json", "verix_verdict.json", "audit_evidence_index.json"]:
        (output_dir / name).write_text(
            json.dumps({"status": "done", "role_id": "verix_audit", "source_consumption": source_consumption}),
            encoding="utf-8",
        )
    (nested / "intent_block.json").write_text(
        json.dumps({"schema": "verix.checkpoint.v1", "status": "completed"}),
        encoding="utf-8",
    )

    scan = module.scan_agent(
        "verix_audit",
        output_dir,
        ["heartbeat_status.json", "verix_verdict.json", "audit_evidence_index.json"],
    )

    assert scan["status"] == "done"
    assert scan["source_consumption"]["missing_artifacts"] == []
    assert str(nested / "intent_block.json") not in scan["source_consumption"]["json_artifacts_checked"]
