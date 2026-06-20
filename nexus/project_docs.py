from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re
import shlex
import tomllib


ORIGINAL_REL = Path("docs/intent/original-requirement.md")
NORMALIZED_REL = Path("docs/intent/normalized-requirement.md")
UNDERSTANDING_REL = Path("docs/intent/project-understanding.md")
SOURCE_INDEX_REL = Path("docs/intent/source-material-index.md")
REQUIREMENT_TRACE_REL = Path("docs/requirement-trace.md")
REFERENCE_MATERIALS_REL = Path("docs/reference-materials.md")
SEARCH_PLAN_REL = Path("docs/search-plan.md")
SEARCH_LOG_REL = Path("docs/search-log.md")
RECOVERY_RECORDS_REL = Path("docs/recovery-records.md")
VALIDATION_SCRIPT_REL = Path("scripts/validate_project.py")
RECOVERY_PLAYBOOK_REL = Path(".nexus/recovery-playbook.json")
OVERVIEW_REL = Path("docs/project-overview.md")
INDEX_REL = Path(".nexus/project-intent.json")
README_REL = Path("README.md")
README_PRIVATE_MARKER = "<!-- nexus:private-install -->"
README_PUBLIC_MARKER = "<!-- nexus:public-install -->"


def write_project_docs(
    project: Path,
    original_idea: str,
    *,
    private_repo: str,
    public_repo: str,
    github_sync_enabled: bool,
    feishu_sync_enabled: bool,
    run_id: str,
    project_name_context: dict[str, object] | None = None,
    normalized_idea: str | None = None,
    input_trace: dict[str, object] | None = None,
) -> dict[str, object]:
    project = project.expanduser().resolve()
    (project / ORIGINAL_REL).parent.mkdir(parents=True, exist_ok=True)
    (project / INDEX_REL).parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    source_paths = _referenced_intent_source_paths(original_idea)
    source_path = source_paths[0] if source_paths else None
    source_records = _source_records(source_paths)
    source_text = _read_text(source_path) if source_path else ""
    source_excerpt = _excerpt(source_text, limit=4000)
    name_context = project_name_context or _existing_project_name_context(project)
    normalized_text = normalized_idea if normalized_idea is not None else original_idea
    requirement_items = _requirement_items(original_idea, normalized_text, source_records)

    original_path = project / ORIGINAL_REL
    normalized_path = project / NORMALIZED_REL
    understanding_path = project / UNDERSTANDING_REL
    source_index_path = project / SOURCE_INDEX_REL
    requirement_trace_path = project / REQUIREMENT_TRACE_REL
    reference_materials_path = project / REFERENCE_MATERIALS_REL
    search_plan_path = project / SEARCH_PLAN_REL
    search_log_path = project / SEARCH_LOG_REL
    recovery_records_path = project / RECOVERY_RECORDS_REL
    validation_script_path = project / VALIDATION_SCRIPT_REL
    recovery_playbook_path = project / RECOVERY_PLAYBOOK_REL
    overview_path = project / OVERVIEW_REL
    index_path = project / INDEX_REL
    readme_path = project / README_REL
    doc_actions: dict[str, str] = {}

    original_content = _original_markdown(
        project,
        original_idea,
        run_id=run_id,
        timestamp=timestamp,
        source_path=source_path,
        source_excerpt=source_excerpt,
    )
    normalized_content = _normalized_markdown(
        project,
        original_idea,
        run_id=run_id,
        timestamp=timestamp,
        private_repo=private_repo,
        public_repo=public_repo,
        github_sync_enabled=github_sync_enabled,
        feishu_sync_enabled=feishu_sync_enabled,
        source_path=source_path,
        source_excerpt=source_excerpt,
        source_records=source_records,
        project_name_context=name_context,
        normalized_idea=normalized_text,
        requirement_items=requirement_items,
    )
    understanding_content = _project_understanding_markdown(
        project,
        original_idea,
        normalized_text,
        requirement_items=requirement_items,
        source_records=source_records,
        run_id=run_id,
        timestamp=timestamp,
    )
    requirement_trace_content = _requirement_trace_markdown(
        project,
        requirement_items=requirement_items,
        source_records=source_records,
        run_id=run_id,
        timestamp=timestamp,
    )
    source_index_content = _source_material_index_markdown(project, source_records=source_records, run_id=run_id, timestamp=timestamp)
    reference_materials_content = _reference_materials_markdown(project, source_records=source_records, run_id=run_id, timestamp=timestamp)
    search_plan_content = _search_plan_markdown(project, requirement_items=requirement_items, source_records=source_records, run_id=run_id, timestamp=timestamp)
    search_log_content = _search_log_markdown(project, source_records=source_records, run_id=run_id, timestamp=timestamp)
    recovery_records_content = _recovery_records_markdown(project, run_id=run_id, timestamp=timestamp)
    validation_script_content = _validation_script(project.name)
    overview_content = _overview_markdown(
        project,
        timestamp=timestamp,
        private_repo=private_repo,
        public_repo=public_repo,
        github_sync_enabled=github_sync_enabled,
        feishu_sync_enabled=feishu_sync_enabled,
        project_name_context=name_context,
    )
    doc_actions[str(ORIGINAL_REL)] = _write_or_supplement_doc(original_path, original_content, required_markers=["## 原始输入"])
    doc_actions[str(NORMALIZED_REL)] = _write_or_supplement_doc(normalized_path, normalized_content, required_markers=["## 文档职责", "## 规范化目标", "## 默认更新约束"])
    doc_actions[str(UNDERSTANDING_REL)] = _write_generated_fresh(understanding_path, understanding_content)
    doc_actions[str(REQUIREMENT_TRACE_REL)] = _write_generated_fresh(requirement_trace_path, requirement_trace_content)
    doc_actions[str(SOURCE_INDEX_REL)] = _write_generated_fresh(source_index_path, source_index_content)
    doc_actions[str(REFERENCE_MATERIALS_REL)] = _write_generated_fresh(reference_materials_path, reference_materials_content)
    doc_actions[str(SEARCH_PLAN_REL)] = _write_generated_fresh(search_plan_path, search_plan_content)
    doc_actions[str(SEARCH_LOG_REL)] = _write_generated_fresh(search_log_path, search_log_content)
    doc_actions[str(RECOVERY_RECORDS_REL)] = _write_or_supplement_doc(recovery_records_path, recovery_records_content, required_markers=["## Recovery Records", "## Playbook Policy"])
    doc_actions[str(VALIDATION_SCRIPT_REL)] = _write_or_replace_generated(validation_script_path, validation_script_content, required_markers=["def main", "Path", "missing", "validate"])
    doc_actions[str(RECOVERY_PLAYBOOK_REL)] = _write_recovery_playbook(recovery_playbook_path, project=project, run_id=run_id, timestamp=timestamp)
    doc_actions[str(OVERVIEW_REL)] = _write_or_supplement_doc(overview_path, overview_content, required_markers=["## 项目定位", "## 文档体系", "## 关键运行与同步约束"])
    doc_actions[str(README_REL)] = _write_or_supplement_readme(
        readme_path,
        project,
        private_repo=private_repo,
        public_repo=public_repo,
    )

    payload = {
        "schema": "nexus.project_intent.v2",
        "project": project.name,
        "project_path": str(project),
        "original_requirement_path": str(original_path),
        "normalized_requirement_path": str(normalized_path),
        "project_understanding_path": str(understanding_path),
        "requirement_trace_path": str(requirement_trace_path),
        "source_material_index_path": str(source_index_path),
        "reference_materials_path": str(reference_materials_path),
        "search_plan_path": str(search_plan_path),
        "search_log_path": str(search_log_path),
        "validation_script_path": str(validation_script_path),
        "recovery_playbook_path": str(recovery_playbook_path),
        "project_overview_path": str(overview_path),
        "operation_guide_path": str(project / "docs" / "operation-guide.md"),
        "intent_source_path": str(source_path) if source_path else "",
        "intent_source_paths": [str(path) for path in source_paths],
        "requirement_items": requirement_items,
        "run_id": run_id,
        "github_sync_enabled": github_sync_enabled,
        "feishu_sync_enabled": feishu_sync_enabled,
        "private_repo": private_repo,
        "public_repo": public_repo,
        "project_name_context": name_context,
        "input_trace": input_trace or {},
        "document_roles": {
            "docs/intent/original-requirement.md": "原始输入归档，只保留用户原始需求和引用来源，保证可追溯。",
            "docs/intent/normalized-requirement.md": "完整规范化需求主文档，记录背景、目标、能力、安全边界、MVP、非目标和更新约束，是需求更新时的主更新文件。",
            "docs/intent/project-understanding.md": "Nexus 对当前项目意图的结构化理解，连接原始输入、来源材料、能力、边界和验收。",
            "docs/intent/source-material-index.md": "初始化阶段显式来源材料读取记录，说明 planned/read/skipped/unavailable 状态。",
            "docs/requirement-trace.md": "需求追踪表，把用户意图和来源材料转成可验证项目能力与未确认点。",
            "docs/reference-materials.md": "引用材料读取摘录与提取需求记录。",
            "docs/search-plan.md": "检索/调研计划，即使当前只读取本地材料也要说明覆盖范围。",
            "docs/search-log.md": "检索/读取执行记录，说明结果、跳过原因和下一步。",
            "docs/project-overview.md": "项目说明文档，描述项目定位、目录结构、核心模块、artifact 和同步关系，应随代码结构变化刷新。",
            "docs/operation-guide.md": "操作指南，描述初始化、日常维护、同步和审批流程，应随 workflow 入口和实际操作方式变化刷新。",
            "docs/recovery-records.md": "恢复经验的人类可读记录，和 .nexus/recovery-playbook.json 一起维护。",
            "docs/feishu-records.md": "飞书同步记录流，记录初始化和后续更新事件，不承载长期说明职责。",
        },
        "feishu_sync_targets": [
            "docs/intent/normalized-requirement.md",
            "docs/intent/project-understanding.md",
            "docs/requirement-trace.md",
            "docs/project-overview.md",
            "docs/operation-guide.md",
            "docs/recovery-records.md",
        ],
        "updated_at": timestamp,
    }
    existing_index = _read_json(index_path)
    if existing_index:
        payload = {**existing_index, **payload}
        if input_trace:
            payload["input_trace"] = input_trace
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "schema": "nexus.project_docs_bundle.v1",
        "doc_actions": doc_actions,
        "paths": [
            str(original_path),
            str(normalized_path),
            str(understanding_path),
            str(requirement_trace_path),
            str(source_index_path),
            str(reference_materials_path),
            str(search_plan_path),
            str(search_log_path),
            str(recovery_records_path),
            str(validation_script_path),
            str(recovery_playbook_path),
            str(overview_path),
            str(readme_path),
            str(index_path),
        ],
        **payload,
    }


def load_project_doc_targets(project: Path) -> list[Path]:
    project = project.expanduser().resolve()
    path = project / INDEX_REL
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        targets = payload.get("feishu_sync_targets") if isinstance(payload.get("feishu_sync_targets"), list) else []
        resolved: list[Path] = []
        for item in targets:
            if not isinstance(item, str) or not item.strip():
                continue
            candidate = project / item
            if candidate.exists():
                resolved.append(candidate)
        if resolved:
            return resolved
    defaults = [project / NORMALIZED_REL, project / OVERVIEW_REL, project / "docs" / "operation-guide.md"]
    return [path for path in defaults if path.exists()]


def _write_or_supplement_doc(path: Path, content: str, *, required_markers: list[str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        return "created"
    existing = path.read_text(encoding="utf-8", errors="replace")
    if not existing.strip() or _looks_like_placeholder(existing):
        path.write_text(content, encoding="utf-8")
        return "rebuilt_from_empty_or_placeholder"
    missing = [marker for marker in required_markers if marker not in existing]
    if not missing:
        return "preserved_existing_valid"
    supplement = _supplement_markdown(content, missing)
    if supplement.strip():
        path.write_text(existing.rstrip() + "\n\n" + supplement, encoding="utf-8")
        return "supplemented_missing_sections"
    return "preserved_existing_nonempty"


def _write_or_replace_generated(path: Path, content: str, *, required_markers: list[str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        return "created"
    existing = path.read_text(encoding="utf-8", errors="replace")
    if not existing.strip() or _looks_like_placeholder(existing):
        path.write_text(content, encoding="utf-8")
        return "rebuilt_from_empty_or_placeholder"
    if all(marker in existing for marker in required_markers):
        return "preserved_existing_valid"
    path.write_text(content, encoding="utf-8")
    return "replaced_invalid_generated"


def _write_generated_fresh(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        return "created"
    existing = path.read_text(encoding="utf-8", errors="replace")
    if existing == content:
        return "preserved_existing_valid"
    path.write_text(content, encoding="utf-8")
    if not existing.strip() or _looks_like_placeholder(existing):
        return "rebuilt_from_empty_or_placeholder"
    return "refreshed_generated"


def _write_recovery_playbook(path: Path, *, project: Path, run_id: str, timestamp: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = {
        "schema": "nexus.recovery_playbook.v1",
        "project": project.name,
        "created_by_run_id": run_id,
        "updated_at": timestamp,
        "entries": [],
        "policy": {
            "write_rule": "Only approved recovery operations should be appended.",
            "reuse_rule": "Before a similar recovery, inspect related entries and cite the matched entry id.",
        },
    }
    if not path.exists():
        path.write_text(json.dumps(seed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return "created"
    existing = _read_json(path)
    entries = existing.get("entries") if isinstance(existing.get("entries"), list) else None
    if existing and entries is not None:
        changed = False
        if existing.get("schema") != seed["schema"]:
            existing["schema"] = seed["schema"]
            changed = True
        if "policy" not in existing:
            existing["policy"] = seed["policy"]
            changed = True
        if changed:
            existing["updated_at"] = timestamp
            path.write_text(json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return "supplemented_missing_policy"
        return "preserved_existing_valid"
    path.write_text(json.dumps(seed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return "rebuilt_invalid_json"


def _write_or_supplement_readme(path: Path, project: Path, *, private_repo: str, public_repo: str) -> str:
    content = render_readme_private_section(project, private_repo=private_repo)
    if not path.exists():
        path.write_text(f"# {project.name}\n\n{content}", encoding="utf-8")
        return "created"
    existing = path.read_text(encoding="utf-8", errors="replace")
    if not existing.strip() or _looks_like_placeholder(existing):
        path.write_text(f"# {project.name}\n\n{content}", encoding="utf-8")
        return "rebuilt_from_empty_or_placeholder"
    replaced = replace_nexus_readme_section(existing, content)
    if replaced != existing:
        path.write_text(replaced, encoding="utf-8")
        return "replaced_nexus_install"
    path.write_text(existing.rstrip() + "\n\n" + content, encoding="utf-8")
    return "supplemented_private_install"


def render_readme_private_section(project: Path, *, private_repo: str) -> str:
    install_target = private_repo or "OWNER/project"
    console_commands = _console_commands(project)
    command_lines = console_commands or [f"python -m {_module_name(project)} --help"]
    skill_paths = _skill_paths(project)
    skill_lines: list[str] = []
    if skill_paths:
        skill_command = direct_skill_install_command(install_target, skill_paths)
        skill_lines = [
            "",
            "Codex workflow/skill install:",
            "",
            "```bash",
            skill_command,
            "```",
            "",
            "This installs the workflow skill directly from the repository files into `$HOME/.agents/skills`.",
        ]
    return "\n".join(
        [
            README_PRIVATE_MARKER,
            "## Private Install",
            "",
            "Install the private package from GitHub:",
            "",
            "```bash",
            f"python -m pip install git+https://github.com/{install_target}.git",
            "```",
            "",
            "Smoke test the installed command:",
            "",
            "```bash",
            *command_lines,
            "```",
            *skill_lines,
            "",
            "This private repository is the source of truth for development, governance, and sync configuration. Public release instructions are generated only inside the Nexus public staging flow.",
            "",
        ]
    )


def render_readme_public_section(project: Path, *, public_repo: str) -> str:
    return _readme_public_section(project, public_repo=public_repo)


def direct_skill_install_command(repo: str, skill_paths: list[str]) -> str:
    install_target = repo or "OWNER/project"
    skill_dirs = sorted({str(Path(path).parent).replace("\\", "/") for path in skill_paths if path.endswith("/SKILL.md")})
    if not skill_dirs:
        return ""
    quoted_skill_dirs = " ".join(shlex.quote(path) for path in skill_dirs)
    return f'tmp="$(mktemp -d)" && git clone --depth 1 https://github.com/{install_target}.git "$tmp/repo" && mkdir -p "$HOME/.agents/skills" && for skill in {quoted_skill_dirs}; do cp -R "$tmp/repo/$skill" "$HOME/.agents/skills/"; done'


def replace_nexus_readme_section(existing: str, content: str) -> str:
    section = _find_nexus_readme_section(existing)
    if section is None:
        return existing
    start, end = section
    prefix = existing[:start].rstrip()
    tail = _strip_nexus_readme_sections(existing[end:])
    parts = [part for part in [prefix, content.rstrip(), tail.strip()] if part]
    return "\n\n".join(parts).rstrip() + "\n"


def _strip_nexus_readme_sections(text: str) -> str:
    cleaned = text
    while True:
        section = _find_nexus_readme_section(cleaned)
        if section is None:
            return cleaned
        start, end = section
        cleaned = (cleaned[:start].rstrip() + "\n\n" + cleaned[end:].lstrip()).strip()
        if cleaned:
            cleaned += "\n"


def _find_nexus_readme_section(text: str) -> tuple[int, int] | None:
    marker_positions = [pos for marker in [README_PRIVATE_MARKER, README_PUBLIC_MARKER] if (pos := text.find(marker)) >= 0]
    if not marker_positions:
        return None
    start = min(marker_positions)
    line_start = text.rfind("\n", 0, start) + 1
    next_line = text.find("\n", start)
    if next_line < 0:
        return line_start, len(text)
    cursor = next_line + 1
    first_heading_seen = False
    while cursor < len(text):
        line_end = text.find("\n", cursor)
        if line_end < 0:
            line_end = len(text)
        line = text[cursor:line_end]
        stripped = line.strip()
        if stripped in {README_PRIVATE_MARKER, README_PUBLIC_MARKER}:
            return line_start, cursor
        if re.match(r"#{1,6}\s+\S", stripped):
            if first_heading_seen:
                return line_start, cursor
            first_heading_seen = True
        cursor = line_end + 1
    return line_start, len(text)


def _readme_public_section(project: Path, *, public_repo: str) -> str:
    install_target = public_repo or "OWNER/project-public"
    console_commands = _console_commands(project)
    command_lines = console_commands or [f"python -m {_module_name(project)} --help"]
    skill_paths = _skill_paths(project)
    skill_lines: list[str] = []
    if skill_paths:
        skill_lines = [
            "",
            "Codex workflow/skill install:",
            "",
            "```bash",
            direct_skill_install_command(install_target, skill_paths),
            "```",
            "",
            "This installs the workflow skill directly from the repository files into `$HOME/.agents/skills`.",
        ]
    return "\n".join(
        [
            README_PUBLIC_MARKER,
            "## Public Install",
            "",
            "Install the public package from GitHub:",
            "",
            "```bash",
            f"python -m pip install git+https://github.com/{install_target}.git",
            "```",
            "",
            "Smoke test the installed command:",
            "",
            "```bash",
            *command_lines,
            "```",
            *skill_lines,
            "",
            "Private runtime files, credentials, `.env`, tokens, cookies, browser profiles, `.data/`, `.nexus/private/`, and local host paths are not part of the public release.",
            "",
        ]
    )


def write_public_readme_for_staging(staging: Path, project: Path, *, public_repo: str) -> str:
    path = staging / README_REL
    content = render_readme_public_section(project, public_repo=public_repo)
    if not path.exists():
        path.write_text(f"# {project.name}\n\n{content}", encoding="utf-8")
        return "created"
    existing = path.read_text(encoding="utf-8", errors="replace")
    if not existing.strip() or _looks_like_placeholder(existing):
        path.write_text(f"# {project.name}\n\n{content}", encoding="utf-8")
        return "rebuilt_from_empty_or_placeholder"
    replaced = replace_nexus_readme_section(existing, content)
    if replaced != existing:
        path.write_text(replaced, encoding="utf-8")
        return "replaced_nexus_install"
    path.write_text(existing.rstrip() + "\n\n" + content, encoding="utf-8")
    return "supplemented_public_install"


def _console_commands(project: Path) -> list[str]:
    pyproject = _read_pyproject(project)
    project_section = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
    scripts = project_section.get("scripts") if isinstance(project_section.get("scripts"), dict) else {}
    return [f"{name} --help" for name in scripts if isinstance(name, str)]


def _module_name(project: Path) -> str:
    pyproject = _read_pyproject(project)
    project_section = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
    name = str(project_section.get("name") or project.name).replace("-", "_")
    return name or project.name.replace("-", "_")


def _skill_names(project: Path) -> list[str]:
    names = {path.parent.name for root in [project / "skills", project / ".github" / "skills"] for path in root.glob("*/SKILL.md")}
    return sorted(name for name in names if name)


def _skill_paths(project: Path) -> list[str]:
    paths = {
        str(path.relative_to(project))
        for root in [project / "skills", project / ".github" / "skills"]
        for path in root.glob("*/SKILL.md")
    }
    return sorted(path for path in paths if path)


def _read_pyproject(project: Path) -> dict[str, object]:
    path = project / "pyproject.toml"
    if not path.exists():
        return {}
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _looks_like_placeholder(text: str) -> bool:
    value = text.strip()
    if not value:
        return True
    placeholders = {"todo", "tbd", "placeholder", "未填写", "待补充", "初始化占位"}
    return len(value) < 40 and any(item in value.lower() for item in placeholders)


def _supplement_markdown(content: str, missing_markers: list[str]) -> str:
    lines = [
        "## Nexus 补充初始化记录",
        "",
        "本节由补充初始化追加，用于补齐缺失的说明结构；已有正文保留不改。",
        "",
    ]
    for marker in missing_markers:
        section = _extract_markdown_section(content, marker)
        if section:
            lines.extend([section.strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def _extract_markdown_section(content: str, heading: str) -> str:
    lines = content.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## ") and not lines[index].startswith("### "):
            end = index
            break
    return "\n".join(lines[start:end])


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_project_overview(
    project: Path,
    *,
    private_repo: str = "",
    public_repo: str = "",
    github_sync_enabled: bool = True,
    feishu_sync_enabled: bool = True,
    project_name_context: dict[str, object] | None = None,
) -> dict[str, object]:
    project = project.expanduser().resolve()
    path = project / OVERVIEW_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    name_context = project_name_context or _existing_project_name_context(project)
    path.write_text(
        _overview_markdown(
            project,
            timestamp=timestamp,
            private_repo=private_repo,
            public_repo=public_repo,
            github_sync_enabled=github_sync_enabled,
            feishu_sync_enabled=feishu_sync_enabled,
            project_name_context=name_context,
        ),
        encoding="utf-8",
    )
    return {
        "schema": "nexus.project_overview.v1",
        "status": "completed",
        "reason": "project_overview_written",
        "path": str(path),
        "updated_at": timestamp,
    }


def _existing_project_name_context(project: Path) -> dict[str, object]:
    path = project / INDEX_REL
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    context = payload.get("project_name_context") if isinstance(payload.get("project_name_context"), dict) else {}
    return dict(context) if isinstance(context, dict) else {}


def _referenced_intent_source_path(text: str) -> Path | None:
    paths = _referenced_intent_source_paths(text)
    return paths[0] if paths else None


def _referenced_intent_source_paths(text: str) -> list[Path]:
    matches = re.findall(r"/[^\s，。；、）)]+", text or "")
    paths: list[Path] = []
    seen: set[str] = set()
    for raw in matches:
        cleaned = raw.rstrip(".,，。；;:：)]】")
        path = Path(cleaned).expanduser()
        if path.exists() and (path.is_file() or path.is_dir()):
            resolved = path.resolve()
            key = str(resolved)
            if key not in seen:
                paths.append(resolved)
                seen.add(key)
    return paths


def _source_records(paths: list[Path]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in paths:
        text = _read_source_text(path)
        status = "read_dir" if path.is_dir() and text else "read" if text else "unavailable"
        records.append(
            {
                "path": str(path),
                "name": path.name,
                "read_status": status,
                "chars": str(len(text)),
                "excerpt": _excerpt(text, limit=1600) if text else "",
            }
        )
    return records


def _read_source_text(path: Path) -> str:
    if path.is_file():
        return _read_text(path)
    if not path.is_dir():
        return ""
    preferred: list[Path] = []
    for rel in ["README.md", "AGENTS.md", "pyproject.toml", "SKILL.md"]:
        candidate = path / rel
        if candidate.is_file():
            preferred.append(candidate)
    for root in ["docs", "skills", ".codex/skills"]:
        base = path / root
        if base.exists():
            preferred.extend(sorted(item for item in base.rglob("*") if item.is_file() and item.suffix.lower() in {".md", ".txt", ".json", ".toml"})[:12])
    chunks: list[str] = []
    for item in preferred[:18]:
        text = _read_text(item)
        if text:
            chunks.append(f"--- {item.relative_to(path)} ---\n{_excerpt(text, limit=1200)}")
    return "\n\n".join(chunks)


def _read_text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _excerpt(text: str, *, limit: int = 4000) -> str:
    value = text.strip()
    if not value:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n\n...（已截断，完整内容见原始来源文件）"


def _original_markdown(
    project: Path,
    original_idea: str,
    *,
    run_id: str,
    timestamp: str,
    source_path: Path | None,
    source_excerpt: str,
) -> str:
    lines = [
        f"# {project.name} 原始意图需求",
        "",
        f"- 记录时间：`{timestamp}`",
        f"- 来源 run：`{run_id}`",
        f"- 外部意图来源：`{source_path}`" if source_path else "- 外部意图来源：`未显式提供`",
        "",
        "## 原始输入",
        "",
        original_idea.strip() or "未记录原始需求。",
        "",
    ]
    if source_excerpt:
        lines.extend(
            [
                "## 参考意图全文摘录",
                "",
                source_excerpt,
                "",
                "## 归档职责",
                "",
                "- 本文件只负责保留原始输入和引用来源，作为后续规范化需求的可追溯依据。",
                "- 需求解释、结构化目标和更新规则应写入 `docs/intent/normalized-requirement.md`。",
                "",
            ]
        )
    return "\n".join(lines)


def _normalized_markdown(
    project: Path,
    original_idea: str,
    *,
    run_id: str,
    timestamp: str,
    private_repo: str,
    public_repo: str,
    github_sync_enabled: bool,
    feishu_sync_enabled: bool,
    source_path: Path | None,
    source_excerpt: str,
    source_records: list[dict[str, str]],
    project_name_context: dict[str, object],
    normalized_idea: str,
    requirement_items: list[str],
) -> str:
    summary = _intent_summary(normalized_idea or original_idea, project_name=project.name)
    lines = [
        f"# {project.name} 规范化意图需求",
        "",
        f"- 记录时间：`{timestamp}`",
        f"- 来源 run：`{run_id}`",
        f"- 项目路径：`{project}`",
        f"- GitHub private 默认同步：`{'enabled' if github_sync_enabled else 'disabled'}`",
        f"- GitHub private 仓库：`{private_repo or '未配置'}`",
        f"- GitHub public 仓库：`{public_repo or '未配置'}`",
        f"- 飞书文档同步：`{'enabled' if feishu_sync_enabled else 'disabled'}`",
        f"- 参考意图来源：`{source_path}`" if source_path else "- 参考意图来源：`未显式提供`",
        "",
        "## 文档职责",
        "",
        "- `docs/intent/original-requirement.md`：原始输入归档，只保留用户原文和引用来源。",
        "- `docs/intent/normalized-requirement.md`：完整规范化需求主文档，需求更新时优先更新这里。",
        "- `docs/intent/project-understanding.md`：Nexus 对项目意图的结构化理解。",
        "- `docs/requirement-trace.md`：从用户输入和来源材料到项目能力、验收和未确认点的追踪表。",
        "- `docs/intent/source-material-index.md` 与 `docs/reference-materials.md`：来源读取计划、状态和提取记录。",
        "- `docs/search-plan.md` 与 `docs/search-log.md`：调研/检索计划和实际读取记录。",
        "- `docs/project-overview.md`：项目说明文档，随代码结构和模块演进更新。",
        "- `docs/operation-guide.md`：操作指南，随 workflow 入口和实际操作方式更新。",
        "- `docs/recovery-records.md` 与 `.nexus/recovery-playbook.json`：恢复经验记录和后续复用入口。",
        "- `docs/feishu-records.md`：飞书同步记录流，不承担长期说明职责。",
        "",
        "## 规范化目标",
        "",
        f"`{project.name}` 是由 Nexus 初始化和维护的真实项目。当前目标是：{summary}",
        "",
        *_project_name_section(project_name_context, project, heading="## 项目命名说明"),
        "## 项目范围",
        "",
        *_bullet_lines(requirement_items, fallback="把原始需求转成可维护的项目目标、流程、验证项和更新记录。"),
        "- 在任何敏感动作前产出 proposal、审批卡和审计 artifact。",
        "- 将项目文档、项目说明、操作指南和飞书同步记录纳入统一维护。",
        "- 作为 Nexus/Verix 的端到端测试样例，验证真实 provider、审批边界、artifact 和同步链路。",
        "",
        "## 默认能力边界",
        "",
        "- 项目能力必须从当前原始输入、显式引用材料和后续审批反馈中推导，不得套用其他样例项目的场景。",
        "- 将 GitHub private 默认同步、Feishu 文档同步和记录管理视为基础配套能力。",
        "- 项目说明文档与操作指南应根据代码结构和 workflow 入口持续刷新，不允许长期漂移。",
        "- 如果原始输入引用了历史、样例或本地文件，必须记录读取状态、提取结果和未覆盖部分。",
        "",
        "## 硬安全边界",
        "",
        "- 不读取 cookie、token、浏览器 profile、SSH key、`.env`、密码文件或其他凭据。",
        "- 不绕过登录、CAPTCHA、403、WAF、平台风控或反爬系统。",
        "- 不自动输入账号密码、不自动提交外部表单、不自动发送外部消息。",
        "- 附件上传、最终提交、外部写入、真实浏览器访问和外部 LLM 调用等动作都必须单独审批。",
        "- 遇到登录、验证码、风控或敏感页面时必须 blocked 并输出审计 artifact。",
        "",
        "## 来源读取状态",
        "",
        *_source_status_lines(source_records),
        "",
        "## 默认更新约束",
        "",
        "- 需求变更时必须更新 `docs/intent/normalized-requirement.md`，必要时同步修订原始需求引用和文档职责。",
        "- 代码结构、模块职责或 artifact 变化时必须刷新 `docs/project-overview.md`。",
        "- workflow 入口、同步方式或运维步骤变化时必须刷新 `docs/operation-guide.md`。",
        "- 上述三份长期文档默认作为飞书长期同步对象，缺配置或权限时必须 blocked，不得假装同步成功。",
        "",
        "## 原始输入摘要",
        "",
        original_idea.strip() or "未记录原始需求。",
        "",
    ]
    if normalized_idea.strip() and normalized_idea.strip() != original_idea.strip():
        lines.extend(
            [
                "## 规范化输入",
                "",
                normalized_idea.strip(),
                "",
            ]
        )
    return "\n".join(lines)


def _intent_summary(text: str, *, project_name: str) -> str:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return f"围绕 `{project_name}` 建立可追溯、可验证、可迭代的项目工作区。"
    cleaned = re.sub(r"\$nexus-workflow\s*", "", cleaned)
    if len(cleaned) > 240:
        cleaned = cleaned[:240].rstrip() + "..."
    return cleaned


def _requirement_items(original_idea: str, normalized_idea: str, source_records: list[dict[str, str]]) -> list[str]:
    chunks: list[str] = []
    for text in [normalized_idea, original_idea]:
        chunks.extend(_candidate_requirement_sentences(text))
    for record in source_records:
        excerpt = record.get("excerpt", "")
        if excerpt:
            chunks.extend(_candidate_requirement_sentences(excerpt))
    items: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        value = chunk.strip(" -\t")
        if not value or len(value) < 8:
            continue
        if len(value) > 180:
            value = value[:180].rstrip() + "..."
        key = value.lower()
        if key in seen:
            continue
        items.append(value)
        seen.add(key)
        if len(items) >= 10:
            break
    return items


def _candidate_requirement_sentences(text: str) -> list[str]:
    cleaned = (text or "").replace("\r\n", "\n")
    pieces = re.split(r"[\n。；;]+", cleaned)
    strong_markers = [
        "必须",
        "需要",
        "要求",
        "不能",
        "不要",
        "应该",
        "要",
        "测试",
        "验证",
        "同步",
        "恢复",
        "初始化",
        "workflow",
        "skill",
        "agent",
        "intent",
        "requirement",
        "validate",
    ]
    selected: list[str] = []
    for piece in pieces:
        value = " ".join(piece.strip(" -*\t").split())
        if not value:
            continue
        if any(marker.lower() in value.lower() for marker in strong_markers):
            selected.append(value)
    if selected:
        return selected
    return [" ".join(piece.strip(" -*\t").split()) for piece in pieces if len(piece.strip()) >= 12][:6]


def _bullet_lines(items: list[str], *, fallback: str) -> list[str]:
    values = items or [fallback]
    return [f"- {item}" for item in values]


def _source_status_lines(source_records: list[dict[str, str]]) -> list[str]:
    if not source_records:
        return [
            "- read_status: `skipped`; reason: 原始输入未提供可读取的本地来源文件路径。",
            "- extracted: 当前只能从本轮原始输入提取项目需求；后续补充来源材料时必须更新读取记录。",
        ]
    lines: list[str] = []
    for record in source_records:
        status = record.get("read_status", "unavailable")
        path = record.get("path", "")
        chars = record.get("chars", "0")
        lines.append(f"- source: `{path}`; read_status: `{status}`; chars: `{chars}`")
        excerpt = record.get("excerpt", "").strip()
        if excerpt:
            snippet = excerpt.replace("\n", " ")
            if len(snippet) > 240:
                snippet = snippet[:240].rstrip() + "..."
            lines.append(f"  - extracted: {snippet}")
        else:
            lines.append("  - extracted: unavailable; 文件为空、不可读或读取失败。")
    return lines


def _project_understanding_markdown(
    project: Path,
    original_idea: str,
    normalized_idea: str,
    *,
    requirement_items: list[str],
    source_records: list[dict[str, str]],
    run_id: str,
    timestamp: str,
) -> str:
    summary = _intent_summary(normalized_idea or original_idea, project_name=project.name)
    lines = [
        f"# {project.name} 项目意图理解",
        "",
        f"- 记录时间：`{timestamp}`",
        f"- 来源 run：`{run_id}`",
        "",
        "## 项目意图理解",
        "",
        f"Nexus 当前将 `{project.name}` 理解为：{summary}",
        "",
        "## 目标能力",
        "",
        *_bullet_lines(requirement_items, fallback="建立可追溯需求、项目说明、操作流程、验证入口和后续迭代记录。"),
        "",
        "## 数据与来源",
        "",
        *_source_status_lines(source_records),
        "",
        "## 边界与风险",
        "",
        "- 未经审批不执行外部写入、公开发布、登录态操作或凭据读取。",
        "- 对引用文件、历史记录或样例项目的读取必须留下 read_status 和 extracted 记录。",
        "- 后续修改必须更新需求追踪、项目说明、操作指南和验证入口。",
        "",
        "## 验收与更新",
        "",
        "- `scripts/validate_project.py` 应能在本地检查关键文档和 JSON 索引。",
        "- 每次端到端测试失败后，应把失败维度反馈到 `docs/requirement-trace.md` 或后续变更记录。",
        "- 恢复成功后，必须经审批沉淀到 `.nexus/recovery-playbook.json` 与 `docs/recovery-records.md`。",
        "",
    ]
    return "\n".join(lines)


def _requirement_trace_markdown(
    project: Path,
    *,
    requirement_items: list[str],
    source_records: list[dict[str, str]],
    run_id: str,
    timestamp: str,
) -> str:
    rows = []
    for index, item in enumerate(requirement_items or ["维护项目意图、项目文档、操作流程、验证入口和恢复记录。"], start=1):
        rows.append(f"| R{index:02d} | {item} | docs/intent/project-understanding.md | scripts/validate_project.py | open |")
    lines = [
        f"# {project.name} 需求追踪",
        "",
        f"- 记录时间：`{timestamp}`",
        f"- 来源 run：`{run_id}`",
        "",
        "## 需求追踪表",
        "",
        "| ID | 用户/来源需求 | 目标承载面 | 验收方式 | 状态 |",
        "| --- | --- | --- | --- | --- |",
        *rows,
        "",
        "## 来源覆盖",
        "",
        *_source_status_lines(source_records),
        "",
        "## 未确认点",
        "",
        "- 如果来源材料无法读取、只读取到截断摘录，或需求之间存在冲突，必须在后续审批/测试轮次中补充确认。",
        "- 如果测试反馈否定某一部分项目设计，应回写本追踪表并重新规划修改。",
        "",
    ]
    return "\n".join(lines)


def _source_material_index_markdown(
    project: Path,
    *,
    source_records: list[dict[str, str]],
    run_id: str,
    timestamp: str,
) -> str:
    lines = [
        f"# {project.name} Source Material Index",
        "",
        f"- updated_at: `{timestamp}`",
        f"- run_id: `{run_id}`",
        "",
        "## Source Materials",
        "",
        *_source_status_lines(source_records),
        "",
        "## Read Status",
        "",
        "- planned: 读取原始输入中显式给出的本地材料、历史记录或样例项目说明。",
        "- read_status: 见上方逐项记录；未提供路径时标记 skipped，不伪造读取。",
        "- extracted: 提取结果进入 `docs/requirement-trace.md` 和 `docs/intent/project-understanding.md`。",
        "",
    ]
    return "\n".join(lines)


def _reference_materials_markdown(
    project: Path,
    *,
    source_records: list[dict[str, str]],
    run_id: str,
    timestamp: str,
) -> str:
    lines = [
        f"# {project.name} Reference Reading Record",
        "",
        f"- updated_at: `{timestamp}`",
        f"- run_id: `{run_id}`",
        "",
        "## Reference Reading Record",
        "",
        *_source_status_lines(source_records),
        "",
        "## Extracted Requirements",
        "",
    ]
    if source_records:
        for record in source_records:
            excerpt = record.get("excerpt", "").strip()
            lines.append(f"- source `{record.get('path', '')}`: {(_intent_summary(excerpt, project_name=project.name) if excerpt else 'unavailable; no extracted requirement.')}")
    else:
        lines.append("- No external reference file was provided; extracted requirements come from the raw instruction.")
    lines.append("")
    return "\n".join(lines)


def _search_plan_markdown(
    project: Path,
    *,
    requirement_items: list[str],
    source_records: list[dict[str, str]],
    run_id: str,
    timestamp: str,
) -> str:
    lines = [
        f"# {project.name} Search Plan",
        "",
        f"- updated_at: `{timestamp}`",
        f"- run_id: `{run_id}`",
        "",
        "## Search Plan",
        "",
        "- scope: first inspect explicit local source paths and the current project workspace.",
        "- method: record planned/read/skipped/unavailable states before converting findings into requirements.",
        "- external_search: not run during initialization unless the user explicitly asks for network research.",
        "",
        "## Coverage",
        "",
        *_source_status_lines(source_records),
        "",
        "## Requirement Focus",
        "",
        *_bullet_lines(requirement_items, fallback="确认项目目标、操作流程、验证入口、同步边界和恢复记录是否齐全。"),
        "",
    ]
    return "\n".join(lines)


def _search_log_markdown(
    project: Path,
    *,
    source_records: list[dict[str, str]],
    run_id: str,
    timestamp: str,
) -> str:
    lines = [
        f"# {project.name} Search Log",
        "",
        f"- updated_at: `{timestamp}`",
        f"- run_id: `{run_id}`",
        "",
        "## Search Log",
        "",
        "- local_source_reading: completed for readable explicit paths; skipped when no path was provided.",
        "- network_search: skipped unless a later approved workflow explicitly requests it.",
        "",
        "## Results",
        "",
        *_source_status_lines(source_records),
        "",
    ]
    return "\n".join(lines)


def _recovery_records_markdown(project: Path, *, run_id: str, timestamp: str) -> str:
    return "\n".join(
        [
            f"# {project.name} Recovery Records",
            "",
            f"- updated_at: `{timestamp}`",
            f"- initialized_by_run_id: `{run_id}`",
            "",
            "## Recovery Records",
            "",
            "- No approved recovery entry has been added yet.",
            "- When an external Codex/debug handoff fixes a blocked Nexus run, record diagnose/edit/test/rebind evidence here.",
            "",
            "## Playbook Policy",
            "",
            "- `.nexus/recovery-playbook.json` is the machine-readable playbook.",
            "- Successful recovery must be approved before appending a reusable playbook entry.",
            "- Later similar failures must inspect the playbook first and cite matched experience before reattempting recovery.",
            "",
        ]
    )


def _validation_script(project_name: str) -> str:
    return f'''#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


REQUIRED_FILES = [
    "docs/intent/original-requirement.md",
    "docs/intent/normalized-requirement.md",
    "docs/intent/project-understanding.md",
    "docs/intent/source-material-index.md",
    "docs/requirement-trace.md",
    "docs/reference-materials.md",
    "docs/search-plan.md",
    "docs/search-log.md",
    "docs/project-overview.md",
    "docs/operation-guide.md",
    "docs/recovery-records.md",
    ".nexus/project-intent.json",
    ".nexus/recovery-playbook.json",
]


def validate(root: Path) -> list[str]:
    missing: list[str] = []
    for rel in REQUIRED_FILES:
        path = root / rel
        if not path.exists() or not path.read_text(encoding="utf-8", errors="replace").strip():
            missing.append(rel)
    for rel in [".nexus/project-intent.json", ".nexus/recovery-playbook.json"]:
        path = root / rel
        if path.exists():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                missing.append(f"{{rel}} invalid json: {{exc}}")
    return missing


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    missing = validate(root)
    if missing:
        print("{project_name} validation failed")
        for item in missing:
            print(f"- missing or invalid: {{item}}")
        return 1
    print("{project_name} validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _overview_markdown(
    project: Path,
    *,
    timestamp: str,
    private_repo: str,
    public_repo: str,
    github_sync_enabled: bool,
    feishu_sync_enabled: bool,
    project_name_context: dict[str, object],
) -> str:
    structure = _structure_snapshot(project)
    lines = [
        f"# {project.name} 项目说明",
        "",
        f"- 更新时间：`{timestamp}`",
        f"- 项目路径：`{project}`",
        f"- GitHub private：`{private_repo or '未配置'}` ({'enabled' if github_sync_enabled else 'disabled'})",
        f"- GitHub public：`{public_repo or '未配置'}`",
        f"- Feishu 长期文档同步：`{'enabled' if feishu_sync_enabled else 'disabled'}`",
        "",
        "## 项目定位",
        "",
        f"`{project.name}` 是由 Nexus 管理的项目，要求同时维护完整需求主文档、项目说明文档、操作指南、飞书记录和受控同步能力。",
        "",
        *_project_name_section(project_name_context, project, heading="## 项目命名说明"),
        "## 文档体系",
        "",
        "- `docs/intent/original-requirement.md`：原始需求归档。",
        "- `docs/intent/normalized-requirement.md`：完整规范化需求主文档。",
        "- `docs/intent/project-understanding.md`：项目意图理解。",
        "- `docs/requirement-trace.md`：需求追踪与验收映射。",
        "- `docs/intent/source-material-index.md` 与 `docs/reference-materials.md`：来源读取状态和提取记录。",
        "- `docs/search-plan.md` 与 `docs/search-log.md`：检索计划和执行记录。",
        "- `docs/project-overview.md`：项目说明文档。",
        "- `docs/operation-guide.md`：操作指南。",
        "- `docs/recovery-records.md`：恢复经验记录。",
        "- `scripts/validate_project.py`：本地项目结构验证入口。",
        "- `docs/feishu-records.md`：飞书同步记录。",
        "",
        "## 当前目录结构摘要",
        "",
        *[f"- `{item}`" for item in structure],
        "",
        "## 关键运行与同步约束",
        "",
        "- 长期文档默认同步到飞书，并复用已有线上文档绑定。",
        "- GitHub private 是默认同步能力；GitHub public 仍需要显式确认。",
        "- 任何超出安全边界的自动化动作都必须先审批。",
        "",
    ]
    return "\n".join(lines)


def _project_name_section(project_name_context: dict[str, object], project: Path, *, heading: str) -> list[str]:
    if not project_name_context:
        return [heading, "", f"- 当前项目目录名为 `{project.name}`。", "- 该名字的详细由来未记录到索引中；后续如有需要，应在初始化命名阶段补写命名理由。", ""]
    source = str(project_name_context.get("source") or "")
    name_source = str(project_name_context.get("name_source") or "")
    selected_name = str(project_name_context.get("selected_name") or project.name)
    model_recommended = str(project_name_context.get("model_recommended") or "")
    meaning = str(project_name_context.get("meaning") or "")
    memory_hook = str(project_name_context.get("memory_hook") or "")
    rationale = str(project_name_context.get("rationale") or "")
    functional_link = str(project_name_context.get("functional_link") or "")
    metaphor = str(project_name_context.get("metaphor") or "")
    word_validation = str(project_name_context.get("word_validation") or "")
    lines = [heading, ""]
    if source == "explicit_user_name":
        if name_source == "user_raw":
            lines.append(f"- 项目当前使用名称 `{selected_name}`，因为用户在原始初始化请求中已明确指定该项目名。")
        else:
            lines.append(f"- 项目当前使用名称 `{selected_name}`，但命名来源不是可确认的用户原始请求，应查阅 `.nexus/project-intent.json` 的 `name_source` 和初始化记录。")
        if model_recommended and model_recommended != selected_name:
            lines.append(f"- 同一轮命名候选的模型推荐名是 `{model_recommended}`，但最终以用户显式指定名称为准。")
    else:
        lines.append(f"- 项目当前使用名称 `{selected_name}`，因为它是初始化阶段的模型推荐命名结果。")
    if meaning:
        lines.append(f"- 含义：{meaning}")
    if memory_hook:
        lines.append(f"- 记忆点：{memory_hook}")
    if rationale:
        lines.append(f"- 采用理由：{rationale}")
    if functional_link:
        lines.append(f"- 功能关联：{functional_link}")
    if metaphor:
        lines.append(f"- 形象隐喻：{metaphor}")
    if word_validation:
        label = "名称校验" if source == "explicit_user_name" else "真实单词校验"
        lines.append(f"- {label}：{word_validation}")
    lines.append("")
    return lines


def _structure_snapshot(project: Path) -> list[str]:
    preferred = [
        "README.md",
        "docs",
        "src",
        "apps",
        "tests",
        ".nexus",
        ".github",
    ]
    seen: list[str] = []
    for name in preferred:
        path = project / name
        if path.exists():
            seen.append(name + ("/" if path.is_dir() else ""))
    extras = sorted(
        item.name + ("/" if item.is_dir() else "")
        for item in project.iterdir()
        if item.name not in {name.rstrip("/") for name in seen} and not item.name.startswith(".git")
    )
    return seen + extras[:8]
