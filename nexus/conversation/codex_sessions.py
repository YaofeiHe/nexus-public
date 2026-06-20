from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .importers import ConversationMessage


SESSION_DIR_NAMES = ["sessions", "session", "history", "conversations"]
SESSION_PATTERNS = ["*.jsonl", "*.json", "*.md", "*.txt"]


@dataclass(slots=True)
class CodexSession:
    session_id: str
    path: Path
    mtime: str
    size: int
    first_user_message: str
    title_guess: str
    message_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "path": str(self.path),
            "mtime": self.mtime,
            "size": self.size,
            "first_user_message": self.first_user_message,
            "title_guess": self.title_guess,
            "message_count": self.message_count,
        }


def discover_codex_sessions(codex_home: Path | None = None) -> dict[str, object]:
    home = (codex_home or Path.home() / ".codex").expanduser().resolve()
    candidates: list[Path] = []
    for dirname in SESSION_DIR_NAMES:
        root = home / dirname
        if not root.exists():
            continue
        for pattern in SESSION_PATTERNS:
            candidates.extend(path for path in root.rglob(pattern) if path.is_file())
    for pattern in ["state*.jsonl", "state*.json"]:
        candidates.extend(path for path in home.glob(pattern) if path.is_file())
    sessions = [_summarize_session(path) for path in sorted(set(candidates), key=lambda item: item.stat().st_mtime, reverse=True)]
    return {
        "schema": "nexus.codex_session_manifest.v1",
        "codex_home": str(home),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sessions": [session.to_dict() for session in sessions if session.message_count > 0],
    }


def choose_session(manifest: dict[str, object], *, current: bool = False, all_history: bool = False, match: str = "", task: str = "", session_id: str = "") -> dict[str, object]:
    sessions = [item for item in manifest.get("sessions", []) if isinstance(item, dict)]
    query = (match or task).strip().lower()
    if session_id:
        selected = [item for item in sessions if str(item.get("session_id")) == session_id]
    elif all_history and query:
        selected = [item for item in sessions if _matches(item, query)]
    elif all_history:
        selected = sessions
    elif query:
        selected = [item for item in sessions if _matches(item, query)]
    elif current:
        selected = sessions[:1]
    else:
        selected = sessions[:1]
    status = "selected"
    reason = "ok"
    if not selected:
        status = "blocked"
        reason = "no_matching_session"
    elif current and len(selected) > 1:
        status = "blocked"
        reason = "ambiguous_current_session"
    return {
        "schema": "nexus.codex_session_selection.v1",
        "status": status,
        "reason": reason,
        "criteria": {"current": current, "all": all_history, "match": match, "task": task, "session_id": session_id},
        "selected": selected,
    }


def import_codex_session(path: Path) -> dict[str, object]:
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    messages = _parse_session_file(path)
    return {
        "schema": "nexus.codex_session_import.v1",
        "source_path": str(path),
        "message_count": len(messages),
        "messages": [message.to_dict() for message in messages],
    }


def _summarize_session(path: Path) -> CodexSession:
    messages = _parse_session_file(path, limit=80)
    first_user = next((message.text for message in messages if message.role == "user"), "")
    stat = path.stat()
    return CodexSession(
        session_id=_session_id(path),
        path=path,
        mtime=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        size=stat.st_size,
        first_user_message=first_user[:240],
        title_guess=(first_user or path.stem)[:80],
        message_count=len(messages),
    )


def _parse_session_file(path: Path, limit: int | None = None) -> list[ConversationMessage]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return _parse_jsonl(path, limit=limit)
    if suffix == ".json":
        return _parse_json(path, limit=limit)
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        return []
    return [ConversationMessage("msg-1", "unknown", text[:20000], str(path), 0)]


def _parse_jsonl(path: Path, limit: int | None = None) -> list[ConversationMessage]:
    messages: list[ConversationMessage] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if limit is not None and len(messages) >= limit:
            break
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = _message_from_payload(payload, path, len(messages))
        if message is not None:
            messages.append(message)
    return messages


def _parse_json(path: Path, limit: int | None = None) -> list[ConversationMessage]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return []
    raw_messages = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(raw_messages, list):
        raw_messages = [payload]
    messages: list[ConversationMessage] = []
    for item in raw_messages:
        if limit is not None and len(messages) >= limit:
            break
        message = _message_from_payload(item, path, len(messages))
        if message is not None:
            messages.append(message)
    return messages


def _message_from_payload(payload: Any, path: Path, index: int) -> ConversationMessage | None:
    if not isinstance(payload, dict):
        text = str(payload).strip()
        return ConversationMessage(f"msg-{index + 1}", "unknown", text, str(path), index) if text else None
    role = str(payload.get("role") or payload.get("author") or payload.get("type") or payload.get("event") or "unknown")
    text = _extract_text(payload)
    if not text:
        return None
    message_id = str(payload.get("id") or payload.get("message_id") or payload.get("session_id") or f"msg-{index + 1}")
    return ConversationMessage(message_id, _normalize_role(role), text, str(path), index)


def _extract_text(payload: dict[str, Any]) -> str:
    for key in ["text", "content", "message", "prompt", "response"]:
        value = payload.get(key)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts = [str(item) for item in value if isinstance(item, str)]
            if parts:
                return "\n".join(parts).strip()
        if isinstance(value, dict):
            nested = _extract_text(value)
            if nested:
                return nested
    if isinstance(payload.get("item"), dict):
        return _extract_text(payload["item"])
    return ""


def _normalize_role(role: str) -> str:
    lowered = role.lower()
    if "user" in lowered:
        return "user"
    if "assistant" in lowered or "agent" in lowered or "model" in lowered:
        return "assistant"
    if "tool" in lowered:
        return "tool"
    return lowered or "unknown"


def _session_id(path: Path) -> str:
    return path.stem.replace(" ", "-")[:80]


def _matches(session: dict[str, object], query: str) -> bool:
    haystack = " ".join(str(session.get(key) or "") for key in ["session_id", "title_guess", "first_user_message", "path"]).lower()
    return query in haystack
