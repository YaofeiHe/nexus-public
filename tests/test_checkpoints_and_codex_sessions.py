from __future__ import annotations

import json
from pathlib import Path

from nexus.checkpoints import CheckpointManager
from nexus.conversation.codex_sessions import choose_session, discover_codex_sessions, import_codex_session
from nexus.conversation.redaction import redact_messages
from nexus.runner import Runner


def test_checkpoint_reuses_completed_node(tmp_path: Path) -> None:
    runner = Runner(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    interaction = runner.run("中文 idea", project_path=project, provider_name="mock")
    run_id = str(interaction["run_id"])
    store_dir = tmp_path / ".data" / "runs" / run_id
    assert (store_dir / "nodes" / "intent_route" / "status.json").exists()
    status = json.loads((store_dir / "nodes" / "intent_route" / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "completed"


def test_checkpoint_completed_checks_hash_and_outputs(tmp_path: Path) -> None:
    from nexus.artifacts import RunStore

    store = RunStore(tmp_path)
    store.ensure()
    output = store.write_json("tool_results/example.json", {"ok": True})
    checkpoints = CheckpointManager(store)
    checkpoints.mark("example", kind="tool", status="completed", input_payload={"a": 1}, output_refs=[output])
    assert checkpoints.completed("example", {"a": 1}, [output])
    assert not checkpoints.completed("example", {"a": 2}, [output])


def test_codex_session_discovery_and_import(tmp_path: Path, monkeypatch) -> None:
    codex_home = tmp_path / ".codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    session = session_dir / "abc.jsonl"
    session.write_text(
        json.dumps({"role": "user", "content": "nexus provider 配置链路"}, ensure_ascii=False) + "\n"
        + json.dumps({"role": "assistant", "content": "可以整理成 workflow"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    manifest = discover_codex_sessions()
    assert manifest["sessions"]
    selection = choose_session(manifest, match="provider")
    assert selection["status"] == "selected"
    imported = import_codex_session(Path(selection["selected"][0]["path"]))
    assert imported["message_count"] == 2


def test_redaction_hides_secret_values() -> None:
    payload = redact_messages([{"message_id": "1", "text": "api_key=sk-abcdefghijklmnopqrstuvwxyz", "role": "user"}])
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in payload["messages"][0]["text"]
    assert payload["findings"]


def test_conversation_to_workflow_requires_session_read_approval_then_resumes(tmp_path: Path, monkeypatch) -> None:
    codex_home = tmp_path / ".codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    (session_dir / "abc.jsonl").write_text(
        json.dumps({"role": "user", "content": "把 nexus session 功能整理成 skill/workflow"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = Runner(tmp_path)

    first = runner.conversation_to_workflow(current=True, provider_name="mock")
    assert first["blocked_reason"] == "conversation_session_read_approval_required"
    run_id = str(first["run_id"])
    approved = runner.approve(run_id, "conversation-session-read")
    assert approved["blocked_reason"] == "resume_required_after_conversation_session_read_approval"
    resumed = runner.resume(run_id)

    assert resumed["previous_task_status"] == "completed"
    run_dir = tmp_path / ".data" / "runs" / run_id
    assert (run_dir / "conversation" / "redacted_messages.jsonl").exists()
    assert (run_dir / "drafts" / "SKILL.md").exists()
