from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nexus.tools.search_adapters import adapter_for_source
from nexus.tools.search_models import CandidateRecord, SourceStatus


@dataclass(slots=True)
class SearchRoundResult:
    round_no: int
    candidates: list[CandidateRecord]
    statuses: list[SourceStatus]
    online_blocked: bool = False


class SearchService:
    def execute_round(
        self,
        *,
        round_no: int,
        search_plan: dict[str, object],
        project_path: Path,
        repo_scan: dict[str, object],
        online_allowed: bool,
        raw_dir: Path | None = None,
    ) -> SearchRoundResult:
        candidates: list[CandidateRecord] = []
        statuses: list[SourceStatus] = []
        online_blocked = False
        source_plan = search_plan.get("source_plan")
        if not isinstance(source_plan, list):
            source_plan = []
        for item in source_plan:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "external_prompt")
            queries = [str(query) for query in item.get("queries", []) if isinstance(query, str)] if isinstance(item.get("queries"), list) else []
            adapter = adapter_for_source(source)
            records, status = adapter.search(queries, project_path=project_path, repo_scan=repo_scan, online=online_allowed, raw_dir=raw_dir)
            if status.source == "local" and source in {"local_inventory", "local_content"}:
                status.source = source
            if status.status in {"blocked", "approval_required"} and status.issue_type == "approval_required":
                online_blocked = True
            candidates.extend(records)
            statuses.append(status)
        return SearchRoundResult(round_no=round_no, candidates=_dedupe(candidates), statuses=statuses, online_blocked=online_blocked)


def _dedupe(candidates: list[CandidateRecord]) -> list[CandidateRecord]:
    seen: dict[str, CandidateRecord] = {}
    for candidate in candidates:
        key = candidate.url or candidate.id
        existing = seen.get(key)
        if existing is None:
            seen[key] = candidate
            continue
        for query in candidate.matched_queries:
            existing.merge_query(query)
        for evidence in candidate.evidence:
            if evidence not in existing.evidence:
                existing.evidence.append(evidence)
    return list(seen.values())
