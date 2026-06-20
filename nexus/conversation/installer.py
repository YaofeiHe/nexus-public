from __future__ import annotations

from pathlib import Path
import re


def install_generated_skill(run_dir: Path, *, codex_home: Path | None = None, confirm: bool = False) -> dict[str, object]:
    draft = run_dir / "drafts" / "SKILL.md"
    if not draft.exists():
        return {
            "schema": "nexus.skill_install_result.v1",
            "status": "blocked",
            "reason": "draft_skill_not_found",
            "message": "没有找到 drafts/SKILL.md。",
        }
    skill_text = draft.read_text(encoding="utf-8")
    name = _skill_name(skill_text)
    if not name:
        return {
            "schema": "nexus.skill_install_result.v1",
            "status": "blocked",
            "reason": "skill_name_missing",
            "message": "SKILL.md 缺少 frontmatter name。",
        }
    if not confirm:
        return {
            "schema": "nexus.skill_install_result.v1",
            "status": "blocked",
            "reason": "install_confirmation_required",
            "skill_name": name,
            "message": "安装 skill 会写入 Codex skills 目录，需要显式确认。",
        }
    home = codex_home or Path.home() / ".codex"
    target_dir = home / "skills" / name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "SKILL.md"
    target.write_text(skill_text, encoding="utf-8")
    return {
        "schema": "nexus.skill_install_result.v1",
        "status": "completed",
        "reason": "installed",
        "skill_name": name,
        "target": str(target),
        "hot_reload_note": "当前 Codex 会话可能不会热加载新安装 skill；请用 $<skill-name> 测试，必要时重启会话。",
    }


def _skill_name(text: str) -> str:
    match = re.search(r"^---\s*\n(?P<body>.*?)\n---", text, flags=re.DOTALL)
    if not match:
        return ""
    for line in match.group("body").splitlines():
        if line.strip().startswith("name:"):
            value = line.split(":", 1)[1].strip().strip("\"'")
            if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,62}", value):
                return value
    return ""
