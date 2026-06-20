from __future__ import annotations

from typing import Any


def select_messages(messages: list[dict[str, Any]], selector: str = "") -> dict[str, object]:
    selector = selector.strip()
    if not selector:
        return {
            "schema": "nexus.conversation_selection.v1",
            "selector": "all",
            "selected_count": len(messages),
            "messages": messages,
        }
    selected = []
    numbers = _numbers(selector)
    lowered = selector.lower()
    for message in messages:
        index = int(message.get("index") or 0) + 1
        text = str(message.get("text") or "")
        if numbers and index in numbers:
            selected.append(message)
            continue
        if selector in text or lowered in text.lower():
            selected.append(message)
            continue
        if selector.startswith("ref:") and selector[4:].lower() in text.lower():
            selected.append(message)
    return {
        "schema": "nexus.conversation_selection.v1",
        "selector": selector,
        "selected_count": len(selected),
        "messages": selected,
    }


def _numbers(text: str) -> set[int]:
    import re

    return {int(item) for item in re.findall(r"\d+", text)}
