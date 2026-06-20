from __future__ import annotations

import pytest

from nexus.schemas import SCHEMAS
from nexus.tools.schema_validation import SchemaValidationError, validate_json


def test_schema_validation_rejects_bad_response() -> None:
    with pytest.raises(SchemaValidationError):
        validate_json({"schema": "x", "goal": "missing fields"}, SCHEMAS["task_block"])


def test_schema_validation_accepts_nested_review() -> None:
    payload = {
        "schema": "nexus.candidate_review.v1",
        "reviews": [
            {
                "candidate_id": "a",
                "score": 0.5,
                "reason": "ok",
                "risks": [],
                "recommended_use": "reference",
            }
        ],
    }
    assert validate_json(payload, SCHEMAS["candidate_review"]) == payload
