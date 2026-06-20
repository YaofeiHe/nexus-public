from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import re
import shutil
from typing import Any

from nexus.conversation.redaction import redact_messages


CONV_ROOT = Path("docs/ai-conversations")
SUBDIRS = ["sessions", "summaries", "workflows", "action-reviews", "skill-candidates", "poc-candidates", "prompts", "inbox"]


def init_conversation_manager(project: Path) -> dict[str, object]:
    root = project / CONV_ROOT
    for subdir in SUBDIRS:
        directory = root / subdir
        directory.mkdir(parents=True, exist_ok=True)
        keep = directory / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(_readme_text(), encoding="utf-8")
    index = root / "index.yaml"
    if not index.exists():
        index.write_text("schema: nexus.ai_conversations.index.v1\nsessions: []\n", encoding="utf-8")
    return {"schema": "nexus.conversation_manager_init.v1", "root": str(root), "created": True}


def ingest_transcript(project: Path, source: Path, *, source_agent: str = "codex", tags: list[str] | None = None) -> dict[str, object]:
    project = project.expanduser().resolve()
    source = source.expanduser().resolve()
    if not source.exists():
        return {"schema": "nexus.conversation_ingest.v1", "status": "blocked", "reason": "source_not_found", "source": str(source)}
    init_conversation_manager(project)
    text = source.read_text(encoding="utf-8", errors="ignore")
    messages = [{"message_id": "source", "role": "unknown", "text": text, "source_file": str(source), "index": 0}]
    redacted = redact_messages(messages)
    redacted_text = str(redacted["messages"][0]["text"])
    digest = sha256(text.encode("utf-8")).hexdigest()
    session_id = _session_id(source, redacted_text)
    contains_redactions = bool(redacted.get("findings"))
    target = project / CONV_ROOT / "sessions" / f"{session_id}.md"
    if target.exists() and digest in target.read_text(encoding="utf-8", errors="ignore"):
        return {"schema": "nexus.conversation_ingest.v1", "status": "completed", "reason": "already_imported", "session_id": session_id, "target": str(target)}
    frontmatter = {
        "session_id": session_id,
        "source_agent": source_agent,
        "source_path": str(source),
        "project": project.name,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "tags": tags or ["ai-coding"],
        "status": "raw_imported",
        "contains_redactions": contains_redactions,
        "source_sha256": digest,
    }
    target.write_text(_frontmatter(frontmatter) + "\n" + redacted_text.rstrip() + "\n", encoding="utf-8")
    redaction_report = project / CONV_ROOT / "action-reviews" / f"{session_id}_redactions.json"
    redaction_report.write_text(json.dumps(redacted.get("findings", []), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _append_index(project / CONV_ROOT / "index.yaml", session_id=session_id, source=str(source), target=str(target), tags=tags or ["ai-coding"])
    return {
        "schema": "nexus.conversation_ingest.v1",
        "status": "completed",
        "session_id": session_id,
        "target": str(target),
        "contains_redactions": contains_redactions,
        "redaction_report": str(redaction_report),
    }


def write_summary(project: Path, session_id: str, summary: dict[str, Any]) -> Path:
    init_conversation_manager(project)
    path = project / CONV_ROOT / "summaries" / f"{session_id}.md"
    lines = [
        f"# Session Summary: {session_id}",
        "",
        "## 1. 背景",
        str(summary.get("background") or ""),
        "",
        "## 2. 用户原始目标",
        str(summary.get("user_goal") or ""),
        "",
        "## 3. 最终结论",
        str(summary.get("final_conclusion") or ""),
        "",
        "## 4. 项目改动相关结论",
        "| 结论 | 影响模块 | 证据/来源 | 是否需要马上落地 |",
        "|---|---|---|---|",
    ]
    for row in summary.get("project_conclusions", []) if isinstance(summary.get("project_conclusions"), list) else []:
        if isinstance(row, dict):
            lines.append(f"| {row.get('conclusion', '')} | {row.get('module', '')} | {row.get('evidence', '')} | {row.get('urgent', '')} |")
    lines.extend(["", "## 5. 已完成操作"])
    lines.extend(f"- {item}" for item in summary.get("completed_actions", []) if isinstance(item, str))
    lines.extend(["", "## 6. 待办"])
    lines.extend(f"- {item}" for item in summary.get("todos", []) if isinstance(item, str))
    lines.extend(["", "## 7. 风险和安全边界"])
    lines.extend(f"- {item}" for item in summary.get("risks", []) if isinstance(item, str))
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def write_prompt_pack(project: Path, artifact_id: str, payload: dict[str, Any]) -> Path:
    init_conversation_manager(project)
    path = project / CONV_ROOT / "prompts" / f"{_slug(artifact_id)}_implementation_prompt.md"
    lines = [
        f"# Implementation Prompt: {artifact_id}",
        "",
        "请根据以下对话总结和 workflow 规划，在目标项目中实现端到端功能。",
        "",
        "## 背景",
        str(payload.get("background") or ""),
        "",
        "## 目标",
        str(payload.get("goal") or ""),
        "",
        "## 要求",
    ]
    lines.extend(f"- {item}" for item in payload.get("requirements", []) if isinstance(item, str))
    lines.extend(["", "## 验收"])
    lines.extend(f"- {item}" for item in payload.get("acceptance", []) if isinstance(item, str))
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def write_workflow(project: Path, workflow_id: str, payload: dict[str, Any]) -> Path:
    init_conversation_manager(project)
    path = project / CONV_ROOT / "workflows" / f"{_slug(workflow_id)}.yaml"
    lines = [
        "schema: nexus.ai_conversation.workflow.v1",
        f"id: {_slug(workflow_id)}",
        f"title: {payload.get('title', workflow_id)}",
        "steps:",
    ]
    for step in payload.get("steps", []) if isinstance(payload.get("steps"), list) else []:
        lines.append(f"  - {str(step)}")
    lines.extend(["safety:", *[f"  - {item}" for item in payload.get("safety", []) if isinstance(item, str)]])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _readme_text() -> str:
    return """# AI Conversations

本目录由 nexus conversation-manager 维护，用于保存、总结和沉淀 Codex / ChatGPT / AI coding agent 对话。

- `sessions/`: 脱敏后的原始 transcript。
- `summaries/`: 可快速读取的会话总结。
- `workflows/`: 可复用 workflow。
- `skill-candidates/`: 待审查 skill 草案。
- `poc-candidates/`: POC 规划。
- `prompts/`: 可复制给 Codex/GPT 的实现 prompt。
"""


def _session_id(source: Path, text: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    words = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", text[:120])
    title = "-".join(words[:5]) or source.stem
    return f"{stamp}_{_slug(title)[:60]}"


def _frontmatter(payload: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in payload.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False) if isinstance(value, str) else str(value).lower() if isinstance(value, bool) else value}")
    lines.append("---")
    return "\n".join(lines)


def _append_index(index: Path, *, session_id: str, source: str, target: str, tags: list[str]) -> None:
    text = index.read_text(encoding="utf-8") if index.exists() else "schema: nexus.ai_conversations.index.v1\nsessions: []\n"
    if f"id: {session_id}" in text:
        return
    entry = [
        "  -",
        f"    id: {session_id}",
        f"    source: {source}",
        f"    target: {target}",
        "    tags:",
        *[f"      - {tag}" for tag in tags],
    ]
    if "sessions: []" in text:
        text = text.replace("sessions: []", "sessions:\n" + "\n".join(entry))
    else:
        text = text.rstrip() + "\n" + "\n".join(entry) + "\n"
    index.write_text(text, encoding="utf-8")


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", text).strip("-").lower()
    return slug or "artifact"
