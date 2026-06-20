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
    "docs/reference-materials.md",
    "docs/search-plan.md",
    "docs/search-log.md",
    "docs/operation-guide.md",
    "workflows/issue-triage.md",
    "workflows/release-blocker-review.md",
    "workflows/regression-evidence-capture.md",
    "workflows/maintainer-handoff.md",
    "workflows/public-readme-release-check.md",
    "workflows/recovery-record-update.md",
    "schemas/maintainer-duty.schema.json",
    "indexes/duty-board.json",
    "indexes/public-private-boundary.json",
    ".nexus/project-intent.json",
]

REQUIRED_TERMS = {
    "docs/intent/normalized-requirement.md": ["issue", "release blocker", "回归证据", "交接", "明确不做"],
    "docs/requirement-trace.md": ["OSM-R01", "OSM-R08", "public"],
    "docs/reference-materials.md": ["未提供", "security policy", "release checklist"],
    "docs/operation-guide.md": ["Public 发布必须显式确认", "recovery_record", "validate_project.py"],
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


def check_schema_and_indexes() -> None:
    schema = load_json("schemas/maintainer-duty.schema.json")
    required = set(schema.get("required", []))
    expected = {
        "issues",
        "release_blockers",
        "regression_evidence",
        "handoff_packet",
        "recovery_records",
    }
    missing = sorted(expected - required)
    if missing:
        fail(f"schema missing required sections: {missing}")

    board = load_json("indexes/duty-board.json")
    for section in expected:
        if section not in board:
            fail(f"duty-board missing section: {section}")
    if not board["issues"] or not board["release_blockers"] or not board["regression_evidence"]:
        fail("duty-board requires non-empty issues, release_blockers, and regression_evidence")

    blocker_issue_ids = {blocker["linked_issue"] for blocker in board["release_blockers"]}
    issue_ids = {issue["issue_id"] for issue in board["issues"]}
    if not blocker_issue_ids <= issue_ids:
        fail(f"release blockers reference missing issues: {sorted(blocker_issue_ids - issue_ids)}")
    for issue in board["issues"]:
        if issue["classification"] == "security-sensitive" and issue["public_status"] == "public":
            fail(f"security-sensitive issue marked public: {issue['issue_id']}")
    for blocker in board["release_blockers"]:
        if blocker["status"] == "cleared" and not blocker["evidence_refs"]:
            fail(f"cleared blocker lacks evidence refs: {blocker['blocker_id']}")

    boundary = load_json("indexes/public-private-boundary.json")
    for key in ["public_allowed", "private_only", "scan_rules"]:
        if not isinstance(boundary.get(key), list) or not boundary[key]:
            fail(f"public-private-boundary missing non-empty {key}")
    private_text = " ".join(boundary["private_only"])
    for required_term in ["tokens", "local absolute paths", "security-sensitive"]:
        if required_term not in private_text:
            fail(f"private boundary does not mention {required_term}")


def check_cross_references(texts: dict[str, str]) -> None:
    combined = "\n".join(texts.values())
    for rel in [
        "workflows/issue-triage.md",
        "workflows/release-blocker-review.md",
        "workflows/regression-evidence-capture.md",
        "workflows/maintainer-handoff.md",
        "workflows/public-readme-release-check.md",
        "workflows/recovery-record-update.md",
        "schemas/maintainer-duty.schema.json",
        "indexes/duty-board.json",
        "indexes/public-private-boundary.json",
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
    check_schema_and_indexes()
    check_cross_references(texts)
    check_sensitive_content(texts)
    print("OK: open source maintainer duty workspace sample passed local validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
