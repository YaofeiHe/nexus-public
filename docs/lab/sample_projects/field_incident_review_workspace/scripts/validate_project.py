#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "docs/intent/original-requirement.md",
    "docs/intent/normalized-requirement.md",
    "docs/project-overview.md",
    "docs/requirement-trace.md",
    "docs/source-material-index.md",
    "docs/search-plan.md",
    "docs/search-log.md",
    "docs/operation-guide.md",
    "workflows/incident-intake.md",
    "workflows/root-cause-review.md",
    "workflows/remediation-verification.md",
    "workflows/weekly-leadership-summary.md",
    "schemas/incident-review.schema.json",
    "indexes/incident-register.json",
    ".nexus/project-intent.json",
]

REQUIRED_TERMS = {
    "docs/intent/normalized-requirement.md": ["事故", "根因", "整改", "领导周报", "明确不做"],
    "docs/requirement-trace.md": ["FIR-R01", "FIR-R08", "例子不是范围上限"],
    "docs/source-material-index.md": ["strategy_notes", "prior_chat_excerpt", "existing_files_index", "未提供"],
    "docs/search-log.md": ["实际读取结果", "未读取或跳过", "回写位置"],
    "docs/operation-guide.md": ["公开摘要", "validate_project.py"],
}

FORBIDDEN_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_]+"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
    re.compile(r"/Users/[^\\s)]+"),
    re.compile(r"<PRIVATE_REPO>"),
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def read_rel(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        fail(f"missing required file: {rel}")
    text = path.read_text(encoding="utf-8")
    if len(text.strip()) < 120:
        fail(f"file is too short or empty: {rel}")
    if "TODO" in text or "占位" in text:
        fail(f"placeholder marker found in {rel}")
    return text


def load_json(rel: str):
    try:
        return json.loads((ROOT / rel).read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing json file: {rel}")
    except json.JSONDecodeError as exc:
        fail(f"invalid json in {rel}: {exc}")


def check_files() -> dict[str, str]:
    return {rel: read_rel(rel) for rel in REQUIRED_FILES}


def check_terms(texts: dict[str, str]) -> None:
    for rel, terms in REQUIRED_TERMS.items():
        for term in terms:
            if term not in texts[rel]:
                fail(f"{rel} does not mention required term: {term}")


def check_schema_and_index() -> None:
    schema = load_json("schemas/incident-review.schema.json")
    required = set(schema.get("required", []))
    expected = {
        "incident_id",
        "reported_at",
        "site",
        "severity",
        "initial_impact",
        "evidence_sources",
        "root_cause",
        "remediation_actions",
        "leadership_summary",
        "public_boundary",
    }
    missing = sorted(expected - required)
    if missing:
        fail(f"schema missing required fields: {missing}")

    register = load_json("indexes/incident-register.json")
    incidents = register.get("incidents")
    if not isinstance(incidents, list) or not incidents:
        fail("incident-register.json must contain non-empty incidents list")
    for incident in incidents:
        for field in expected:
            if field not in incident:
                fail(f"incident missing field {field}: {incident.get('incident_id', '<unknown>')}")
        if not incident["evidence_sources"] or not incident["remediation_actions"]:
            fail(f"incident lacks evidence or remediation actions: {incident['incident_id']}")
        for action in incident["remediation_actions"]:
            if action.get("status") == "verified" and not action.get("evidence"):
                fail(f"verified action lacks evidence: {action.get('action_id')}")
        boundary = incident["public_boundary"]
        if not boundary.get("public_allowed") or not boundary.get("private_only"):
            fail(f"public/private boundary incomplete: {incident['incident_id']}")


def check_cross_references(texts: dict[str, str]) -> None:
    combined = "\n".join(texts.values())
    for rel in [
        "workflows/incident-intake.md",
        "workflows/root-cause-review.md",
        "workflows/remediation-verification.md",
        "workflows/weekly-leadership-summary.md",
        "schemas/incident-review.schema.json",
        "indexes/incident-register.json",
    ]:
        if rel not in combined:
            fail(f"required path is not cross-referenced: {rel}")


def check_sensitive_content(texts: dict[str, str]) -> None:
    for rel, text in texts.items():
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(text):
                fail(f"possible private or secret content in {rel}: {pattern.pattern}")


def main() -> int:
    texts = check_files()
    check_terms(texts)
    check_schema_and_index()
    check_cross_references(texts)
    check_sensitive_content(texts)
    print("OK: field incident review workspace sample passed local validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
