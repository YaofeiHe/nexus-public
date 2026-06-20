from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha1
from typing import Any


@dataclass(slots=True)
class CandidateRecord:
    title: str
    summary: str
    source: str
    url: str
    retrieval_mode: str
    candidate_type: str = "unknown"
    evidence: list[str] = field(default_factory=list)
    matched_queries: list[str] = field(default_factory=list)
    license: str = ""
    maintenance_signal: str = ""
    stars: int | None = None
    repo: str | None = None
    tags: list[str] = field(default_factory=list)
    raw_artifact_refs: list[str] = field(default_factory=list)
    availability: dict[str, Any] = field(default_factory=dict)
    localization: dict[str, Any] = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            digest = sha1(f"{self.source}|{self.url}|{self.title}".encode("utf-8")).hexdigest()[:12]
            self.id = f"cand-{digest}"

    def merge_query(self, query: str) -> None:
        normalized = " ".join(query.split())
        if normalized and normalized not in self.matched_queries:
            self.matched_queries.append(normalized)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceStatus:
    source: str
    status: str
    retrieval_mode: str
    provider: str = ""
    phase: str = "search"
    configured: bool = False
    attempted: bool = False
    query_count: int = 0
    raw_result_count: int = 0
    candidate_count: int = 0
    issue_type: str = "none"
    fallback_used: bool = False
    retryable: bool = False
    auth_required: bool = False
    auth_present: bool = False
    approval_required: bool = False
    approved: bool = False
    reason: str = ""
    next_options: list[str] = field(default_factory=list)
    raw_error_summary: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    raw_artifact_refs: list[str] = field(default_factory=list)
    http_statuses: list[int] = field(default_factory=list)
    retry_after_seconds: int | None = None
    online_search_blocked: bool = False
    provider_statuses: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = ""
    ended_at: str = ""
    latency_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
