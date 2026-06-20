from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = REPO_ROOT / "docs" / "lab" / "monitor_multi_agent_contract.md"
STATE_SCHEMA = REPO_ROOT / "docs" / "lab" / "monitor_agent_state.schema.json"
ROLES = REPO_ROOT / "docs" / "lab" / "monitor_agent_roles.json"
ORCHESTRATOR = REPO_ROOT / "scripts" / "lab" / "run_nexus_verix_orchestrator.py"


REQUIRED_AGENTS = [
    "nexus_execution",
    "skill_replay",
    "verix_audit",
    "nexus_modification",
    "state_audit",
]


def test_monitor_multi_agent_contract_declares_required_boundary() -> None:
    assert CONTRACT.exists()
    text = CONTRACT.read_text(encoding="utf-8")
    assert "`execution_model`: `monitor_multi_agent_v1`" in text
    assert "orchestrated_subprocess_loop" in text
    assert "不能满足 monitor-level multi-agent acceptance" in text
    assert "step_by_step_skill_workflow" in text
    assert "default_autonomous_multi_agent_build" in text
    assert "$nexus-workflow 调研" in text
    assert "false_stop_recovered" in text
    for agent in REQUIRED_AGENTS:
        assert agent in text


def test_monitor_state_schema_requires_monitor_multi_agent_execution_model() -> None:
    schema = json.loads(STATE_SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["execution_model"]["const"] == "monitor_multi_agent_v1"
    assert schema["properties"]["python_embeds_subagents"]["const"] is False
    assert schema["properties"]["agents"]["required"] == REQUIRED_AGENTS
    mode_ids = schema["properties"]["operation_modes"]["items"]["properties"]["mode_id"]["enum"]
    assert mode_ids == ["step_by_step_skill_workflow", "default_autonomous_multi_agent_build"]


def test_monitor_agent_roles_require_real_spawn_for_all_agents() -> None:
    roles = json.loads(ROLES.read_text(encoding="utf-8"))
    role_items = roles["roles"]
    assert [item["role_id"] for item in role_items] == REQUIRED_AGENTS
    assert all(item["spawn_required"] is True for item in role_items)
    assert all(item["output_contract_path"] for item in role_items)
    assert all("heartbeat_status.json" in item["required_artifacts"] for item in role_items)
    skill_replay = next(item for item in role_items if item["role_id"] == "skill_replay")
    assert any("online-search approval continuation" in item for item in skill_replay["done_conditions"])


def test_subprocess_orchestrator_does_not_satisfy_start_run_contract() -> None:
    completed = subprocess.run(
        [sys.executable, str(ORCHESTRATOR), "--dry-run-plan"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    assert plan["execution_model"] == "orchestrated_subprocess_loop"
    assert plan["satisfies_start_run_contract"] is False
    assert plan["requires_external_monitor_spawn"] is True
