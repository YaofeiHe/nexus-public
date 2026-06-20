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
    "workflows/funder-intake.md",
    "workflows/application-package-build.md",
    "workflows/deadline-feedback-review.md",
    "schemas/grant-record.schema.json",
    "indexes/grant-pipeline.json",
    ".nexus/project-intent.json",
]

REQUIRED_TERMS = {
    "docs/intent/normalized-requirement.md": ["资助方", "预算", "证据", "复盘", "明确不做"],
    "docs/requirement-trace.md": ["NPG-R01", "NPG-R08", "indexes/grant-pipeline.json"],
    "docs/source-material-index.md": ["已读取", "未提供", "提取出的需求"],
    "docs/operation-guide.md": ["public 发布不是默认动作", "validate_project.py"],
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
    path = ROOT / rel
    if not path.exists():
        fail(f"missing json file: {rel}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"invalid json in {rel}: {exc}")


def check_required_files() -> dict[str, str]:
    texts = {}
    for rel in REQUIRED_FILES:
        texts[rel] = read_rel(rel)
    return texts


def check_terms(texts: dict[str, str]) -> None:
    for rel, terms in REQUIRED_TERMS.items():
        text = texts[rel]
        for term in terms:
            if term not in text:
                fail(f"{rel} does not mention required term: {term}")


def check_json_contracts() -> None:
    schema = load_json("schemas/grant-record.schema.json")
    required = set(schema.get("required", []))
    expected = {
        "funder_id",
        "program_name",
        "eligibility_rules",
        "required_materials",
        "budget_items",
        "evidence_items",
        "deadline",
        "owner",
        "submission_status",
        "review",
    }
    missing = sorted(expected - required)
    if missing:
        fail(f"schema missing required fields: {missing}")

    pipeline = load_json("indexes/grant-pipeline.json")
    records = pipeline.get("records")
    if not isinstance(records, list) or not records:
        fail("grant-pipeline.json must contain non-empty records list")
    for record in records:
        for field in expected:
            if field not in record:
                fail(f"pipeline record missing field {field}: {record.get('funder_id', '<unknown>')}")
        if not record["required_materials"] or not record["budget_items"] or not record["evidence_items"]:
            fail(f"pipeline record lacks materials, budget, or evidence: {record['funder_id']}")
        for item in record["budget_items"]:
            if item.get("visibility") not in {"private", "public-summary"}:
                fail(f"invalid budget visibility in {record['funder_id']}")


def check_path_references(texts: dict[str, str]) -> None:
    combined = "\n".join(texts.values())
    for rel in [
        "workflows/funder-intake.md",
        "workflows/application-package-build.md",
        "workflows/deadline-feedback-review.md",
        "schemas/grant-record.schema.json",
        "indexes/grant-pipeline.json",
    ]:
        if rel not in combined:
            fail(f"required path is not cross-referenced: {rel}")


def check_sensitive_content(texts: dict[str, str]) -> None:
    for rel, text in texts.items():
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(text):
                fail(f"possible private or secret content in {rel}: {pattern.pattern}")


def main() -> int:
    texts = check_required_files()
    check_terms(texts)
    check_json_contracts()
    check_path_references(texts)
    check_sensitive_content(texts)
    print("OK: nonprofit grant workspace sample passed local validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
