from __future__ import annotations

from pathlib import Path

from nexus.cli import main


NEXUS_REPO = "<PROJECT_ROOT>"


def test_conversation_from_file_generates_workflow(tmp_path: Path) -> None:
    transcript = tmp_path / "conversation.md"
    transcript.write_text("用户想把一个具体自动化项目泛化成通用 workflow。", encoding="utf-8")
    assert main(["--root", str(tmp_path), "conversation-from-file", str(transcript), "--provider", "mock"]) == 0
    run_dir = next((tmp_path / ".data" / "runs").iterdir())
    assert (run_dir / "reports" / "generalized_workflow.md").exists()


def test_skill_doctor_reports_install_state(tmp_path: Path, capsys) -> None:
    assert main(["--root", str(tmp_path), "skill", "doctor"]) == 0
    out = capsys.readouterr().out
    assert "nexus-workflow" in out
    assert "hot_reload_note" in out
    assert NEXUS_REPO in out
    assert "--root " + NEXUS_REPO in out


def test_skill_docs_use_absolute_nexus_repo_path() -> None:
    forge_root = Path(__file__).resolve().parents[2]
    skill_paths = [
        forge_root / "nexus" / "skills" / "nexus-workflow" / "SKILL.md",
        forge_root / ".github" / "skills" / "nexus-workflow" / "SKILL.md",
    ]
    for skill_path in skill_paths:
        text = skill_path.read_text(encoding="utf-8")
        assert NEXUS_REPO in text
        assert f"cd {NEXUS_REPO}" in text
        assert f"--root {NEXUS_REPO}" in text
        command_lines = [line.strip() for line in text.splitlines()]
        assert not any(line.startswith("cd ../nexus") or line.startswith("cd nexus") for line in command_lines)
        for line in command_lines:
            if line.startswith("python -m nexus.cli") or "&& python -m nexus.cli" in line:
                assert f"--root {NEXUS_REPO}" in line
