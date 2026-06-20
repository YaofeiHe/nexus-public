from __future__ import annotations

import json
from pathlib import Path

from nexus.candidates import process_candidates
from nexus.runner import Runner


def test_candidate_processing_merges_duplicate_urls() -> None:
    raw = [
        {
            "id": "a",
            "title": "Example Workflow",
            "summary": "repo",
            "source": "github_repo",
            "url": "https://github.com/Example/Workflow?utm_source=x",
            "retrieval_mode": "online",
            "matched_queries": ["workflow"],
            "evidence": ["github"],
            "raw_artifact_refs": ["raw1.json"],
            "stars": 1200,
        },
        {
            "id": "b",
            "title": "Example Workflow 中文介绍",
            "summary": "blog",
            "source": "chinese_web",
            "url": "https://github.com/example/workflow/",
            "retrieval_mode": "online",
            "matched_queries": ["工作流"],
            "evidence": ["cn"],
            "raw_artifact_refs": ["raw2.json"],
        },
    ]

    processed = process_candidates(raw, max_candidates=8)

    assert len(processed["merged"]) == 1
    merged = processed["merged"][0]
    assert set(merged["matched_queries"]) == {"workflow", "工作流"}
    assert set(merged["raw_artifact_refs"]) == {"raw1.json", "raw2.json"}
    assert processed["ranking_features"][0]["tool_score"] > 0


def test_prepare_project_creates_git_baseline_after_approval(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / "README.md").write_text("# target\n", encoding="utf-8")
    runner = Runner(tmp_path)

    first = runner.prepare_project(project)
    assert first["previous_task_status"] == "blocked"
    assert first["blocked_reason"] == "git_baseline_approval_required"

    run_id = str(first["run_id"])
    approved = runner.approve(run_id, "git-baseline")
    assert approved["previous_task_status"] == "completed"
    assert (project / ".git").exists()
    result = json.loads((tmp_path / ".data" / "runs" / run_id / "tool_results" / "git_baseline_result.json").read_text(encoding="utf-8"))
    assert result["status"] == "completed"


def test_prepare_project_blocks_sensitive_files(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    (project / ".env").write_text("TOKEN=x\n", encoding="utf-8")

    interaction = Runner(tmp_path).prepare_project(project)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "sensitive_files_detected"


def test_conversation_from_file_writes_skill_draft_and_installs(tmp_path: Path, monkeypatch) -> None:
    transcript = tmp_path / "conversation.md"
    transcript.write_text("用户想把某个项目经验整理成可复用 skill/workflow。", encoding="utf-8")
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("HOME", str(codex_home))
    runner = Runner(tmp_path)

    interaction = runner.conversation_from_file(transcript, provider_name="mock")
    assert interaction["previous_task_status"] == "completed"
    run_id = str(interaction["run_id"])
    run_dir = tmp_path / ".data" / "runs" / run_id
    assert (run_dir / "drafts" / "SKILL.md").exists()
    blocked = runner.install_generated_skill(run_id)
    assert blocked["blocked_reason"] == "install_confirmation_required"
    installed = runner.install_generated_skill(run_id, confirm=True)
    assert installed["previous_task_status"] == "completed"
    assert list((codex_home / ".codex" / "skills").glob("*/SKILL.md"))
