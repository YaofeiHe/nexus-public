from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from nexus.feishu_setup import run_markdown_import, run_setup
from nexus.project_docs import load_project_doc_targets


CONFIG_REL = Path(".nexus/feishu-autosync.json")
RECORDS_REL = Path("docs/feishu-records.md")
SKIP_MARKERS = ("不同步飞书", "跳过飞书", "no-feishu-sync", "--no-feishu-sync")


def wants_skip_feishu_sync(text: str) -> bool:
    lowered = text.lower()
    return any(marker in text or marker in lowered for marker in SKIP_MARKERS)


def write_autosync_config(project: Path, *, enabled: bool = True) -> Path:
    project = project.expanduser().resolve()
    path = project / CONFIG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "nexus.feishu_autosync.v1",
        "enabled": enabled,
        "guide_sync": enabled,
        "record_sync": enabled,
        "default_on_skill_activation": True,
        "skip_phrases": list(SKIP_MARKERS),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_autosync_config(project: Path) -> dict[str, object]:
    path = project / CONFIG_REL
    if not path.exists():
        return {"schema": "nexus.feishu_autosync.v1", "enabled": True, "guide_sync": True, "record_sync": True}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": "nexus.feishu_autosync.v1", "enabled": True, "guide_sync": True, "record_sync": True}
    return payload if isinstance(payload, dict) else {"schema": "nexus.feishu_autosync.v1", "enabled": True, "guide_sync": True, "record_sync": True}


def run_feishu_autosync(
    project: Path,
    *,
    event_type: str,
    title: str,
    summary: str,
    changed_paths: list[Path | str] | None = None,
    source_run_id: str = "",
    enabled: bool | None = None,
    guide_paths: list[Path | str] | None = None,
) -> dict[str, object]:
    project = project.expanduser().resolve()
    config = load_autosync_config(project)
    autosync_enabled = bool(config.get("enabled", True)) if enabled is None else enabled
    config_path = write_autosync_config(project, enabled=autosync_enabled)
    if not autosync_enabled:
        return {
            "schema": "nexus.feishu_autosync_result.v1",
            "status": "skipped",
            "reason": "feishu_autosync_disabled",
            "project_path": str(project),
            "config_path": str(config_path),
        }

    records_path = _append_record(project, event_type=event_type, title=title, summary=summary, changed_paths=changed_paths or [], source_run_id=source_run_id)
    setup = run_setup(project, no_network=True)
    if setup.get("status") != "completed":
        return {
            "schema": "nexus.feishu_autosync_result.v1",
            "status": "blocked",
            "reason": str(setup.get("reason") or "feishu_setup_required"),
            "project_path": str(project),
            "config_path": str(config_path),
            "setup": setup,
            "local_records_path": str(records_path),
        }

    sync_targets = _sync_targets(project, guide_paths or [])
    if bool(config.get("record_sync", True)):
        sync_targets.append((records_path, f"{project.name} 更新记录"))
    results: list[dict[str, object]] = []
    for path, doc_title in sync_targets:
        if path.exists():
            results.append(run_markdown_import(project, title=doc_title, markdown_path=path))
    blocked = [item for item in results if item.get("status") != "completed"]
    return {
        "schema": "nexus.feishu_autosync_result.v1",
        "status": "blocked" if blocked else "completed",
        "reason": str(blocked[0].get("reason") or "feishu_autosync_blocked") if blocked else "feishu_autosync_completed",
        "project_path": str(project),
        "config_path": str(config_path),
        "setup": setup,
        "local_records_path": str(records_path),
        "synced_documents": results,
    }


def _sync_targets(project: Path, guide_paths: list[Path | str]) -> list[tuple[Path, str]]:
    seen: set[str] = set()
    targets: list[tuple[Path, str]] = []
    defaults = [*load_project_doc_targets(project), project / "docs" / "system" / "architecture.md", project / ".nexus" / "board.md"]
    for raw in [*guide_paths, *defaults]:
        path = Path(raw).expanduser().resolve()
        key = str(path)
        if key in seen or not path.exists():
            continue
        seen.add(key)
        if path.name == "operation-guide.md":
            title = f"{project.name} 整体操作指南"
        elif path.name == "project-overview.md":
            title = f"{project.name} 项目说明文档"
        elif path.name == "normalized-requirement.md":
            title = f"{project.name} 规范化需求文档"
        elif path.name == "architecture.md":
            title = f"{project.name} 系统架构说明"
        elif path.name == "board.md":
            title = f"{project.name} 项目记录板"
        else:
            title = f"{project.name} {path.stem}"
        targets.append((path, title))
    return targets


def append_feishu_record_markdown(
    project: Path,
    *,
    title: str,
    content: str,
    event_type: str = "manual_record",
    source_run_id: str = "",
) -> Path:
    path = project.expanduser().resolve() / RECORDS_REL
    _ensure_records_header(path, project.name)
    timestamp = datetime.now(timezone.utc).isoformat()
    body = content.strip() or "未记录具体内容。"
    block = (
        f"## {title or '飞书记录'}\n\n"
        f"- 时间：`{timestamp}`\n"
        f"- 事件：`{event_type}`\n"
        f"- run_id：`{source_run_id or 'n/a'}`\n"
        f"- 内容：\n\n"
        f"{body}\n\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return path


def _append_record(project: Path, *, event_type: str, title: str, summary: str, changed_paths: list[Path | str], source_run_id: str) -> Path:
    path = project / RECORDS_REL
    _ensure_records_header(path, project.name)
    timestamp = datetime.now(timezone.utc).isoformat()
    changed = "\n".join(f"  - {_relative_or_absolute(project, Path(item))}" for item in changed_paths) or "  - 未记录具体文件"
    block = (
        f"## {title}\n\n"
        f"- 时间：`{timestamp}`\n"
        f"- 事件：`{event_type}`\n"
        f"- run_id：`{source_run_id or 'n/a'}`\n"
        f"- 摘要：{summary}\n"
        f"- 变更文件：\n{changed}\n\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return path


def _ensure_records_header(path: Path, project_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"# {project_name} 飞书自动同步记录\n\n", encoding="utf-8")


def _relative_or_absolute(project: Path, path: Path) -> str:
    try:
        return str(path.expanduser().resolve().relative_to(project))
    except ValueError:
        return str(path)
