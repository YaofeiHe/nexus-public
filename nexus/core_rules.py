from __future__ import annotations

from pathlib import Path


CORE_RULES_PATH = Path(__file__).with_name("core_rules.md")


def load_core_rules() -> str:
    return CORE_RULES_PATH.read_text(encoding="utf-8")
