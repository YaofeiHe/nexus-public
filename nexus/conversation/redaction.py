from __future__ import annotations

import re
from typing import Any


PATTERNS = [
    ("openai_key", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("api_key_assignment", re.compile(r"(?i)(api[_-]?key|token|secret|authorization|cookie)\s*[:=]\s*['\"]?[^'\"\s]{8,}")),
    ("ssh_private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
]


def redact_messages(messages: list[dict[str, Any]]) -> dict[str, object]:
    redacted: list[dict[str, Any]] = []
    findings: list[dict[str, object]] = []
    for message in messages:
        item = dict(message)
        text = str(item.get("text") or "")
        new_text = text
        for label, pattern in PATTERNS:
            matches = list(pattern.finditer(new_text))
            if not matches:
                continue
            findings.append(
                {
                    "message_id": item.get("message_id", ""),
                    "pattern": label,
                    "count": len(matches),
                }
            )
            new_text = pattern.sub(f"[REDACTED:{label}]", new_text)
        item["text"] = new_text
        redacted.append(item)
    return {
        "schema": "nexus.conversation_redaction.v1",
        "messages": redacted,
        "findings": findings,
    }
