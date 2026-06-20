from __future__ import annotations

from pathlib import Path

from nexus.guide_sync import GUIDE_REL as OPERATION_GUIDE_REL
from nexus.guide_sync import write_operation_guide


GUIDE_REL = Path("docs/github-sync-guide.md")


def write_github_sync_guide(project: Path) -> dict[str, object]:
    project = project.expanduser().resolve()
    guide = write_operation_guide(project)
    primary_path = Path(str(guide.get("path") or project / OPERATION_GUIDE_REL))
    legacy_path = project / GUIDE_REL
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    if primary_path.exists():
        legacy_path.write_text(primary_path.read_text(encoding="utf-8"), encoding="utf-8")
    return {
        "schema": "nexus.github_sync_guide.v1",
        "status": guide.get("status", "completed"),
        "reason": "compatibility_alias_operation_guide_written",
        "path": str(guide.get("path") or ""),
        "legacy_path": str(legacy_path),
        "primary_guide_rel": str(OPERATION_GUIDE_REL),
        "project_path": str(guide.get("project_path") or project),
        "private_repo": str(guide.get("private_repo") or ""),
        "public_repo": str(guide.get("public_repo") or ""),
        "updated_at": str(guide.get("updated_at") or ""),
        "compatibility_note": "github-sync guide is now an alias for docs/operation-guide.md; GitHub sync is a chapter inside the whole operation guide.",
    }
