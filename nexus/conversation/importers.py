from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import zipfile


@dataclass(slots=True)
class ConversationMessage:
    message_id: str
    role: str
    text: str
    source_file: str
    index: int

    def to_dict(self) -> dict[str, object]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "text": self.text,
            "source_file": self.source_file,
            "index": self.index,
        }


def import_conversation(path: Path) -> dict[str, object]:
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".zip":
        messages = _from_zip(path)
    elif path.suffix.lower() == ".json":
        messages = _from_json(path)
    else:
        messages = _from_text(path)
    return {
        "schema": "nexus.conversation_import.v1",
        "source_path": str(path),
        "message_count": len(messages),
        "messages": [message.to_dict() for message in messages],
    }


def _from_zip(path: Path) -> list[ConversationMessage]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        target = next((name for name in names if name.endswith("conversations.json")), "")
        if not target:
            raise ValueError("ChatGPT export zip missing conversations.json")
        payload = json.loads(archive.read(target).decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("conversations.json must contain a list")
    messages: list[ConversationMessage] = []
    for conv_idx, conversation in enumerate(payload):
        mapping = conversation.get("mapping") if isinstance(conversation, dict) else {}
        if not isinstance(mapping, dict):
            continue
        for node_id, node in mapping.items():
            message = node.get("message") if isinstance(node, dict) else None
            if not isinstance(message, dict):
                continue
            role = message.get("author", {}).get("role", "") if isinstance(message.get("author"), dict) else ""
            content = message.get("content", {})
            parts = content.get("parts", []) if isinstance(content, dict) else []
            text = "\n".join(str(part) for part in parts if isinstance(part, str)).strip()
            if not text:
                continue
            messages.append(ConversationMessage(str(node_id), str(role or "unknown"), text, str(path), len(messages)))
        if not messages and isinstance(conversation.get("title"), str):
            messages.append(ConversationMessage(f"conversation-{conv_idx}", "metadata", str(conversation.get("title")), str(path), len(messages)))
    return messages


def _from_json(path: Path) -> list[ConversationMessage]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_messages = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(raw_messages, list):
        raise ValueError("JSON transcript must contain a list or a messages list")
    messages: list[ConversationMessage] = []
    for index, item in enumerate(raw_messages):
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("content") or item.get("message") or "").strip()
            role = str(item.get("role") or item.get("author") or "unknown")
            message_id = str(item.get("id") or item.get("message_id") or f"msg-{index + 1}")
        else:
            text = str(item).strip()
            role = "unknown"
            message_id = f"msg-{index + 1}"
        if text:
            messages.append(ConversationMessage(message_id, role, text, str(path), len(messages)))
    return messages


def _from_text(path: Path) -> list[ConversationMessage]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    chunks = _split_text_transcript(text)
    messages = []
    for index, chunk in enumerate(chunks):
        role = "user" if chunk.startswith(("User:", "用户：", "## User")) else "unknown"
        messages.append(ConversationMessage(f"msg-{index + 1}", role, chunk.strip(), str(path), index))
    return messages


def _split_text_transcript(text: str) -> list[str]:
    markers = ["\n## User", "\n# User", "\n用户：", "\nUser:"]
    if not any(marker in text for marker in markers):
        return [text.strip()] if text.strip() else []
    chunks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith(("## User", "# User", "用户：", "User:")) and current:
            chunks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append("\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]
