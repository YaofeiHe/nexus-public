from __future__ import annotations

from collections import defaultdict
from hashlib import sha1
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {
    "spm",
    "from",
    "source",
    "ref",
    "ref_src",
    "fbclid",
    "gclid",
    "msclkid",
    "wd",
    "oq",
    "ie",
    "tn",
}

SOURCE_WEIGHTS = {
    "github_repo": 0.24,
    "gitee_repo": 0.2,
    "official_docs": 0.22,
    "mcp_registry": 0.18,
    "github_skill": 0.18,
    "chinese_web": 0.12,
    "local_inventory": 0.1,
    "local_content": 0.08,
}


def process_candidates(candidates: list[dict[str, Any]], *, max_candidates: int) -> dict[str, Any]:
    normalized = [_normalize_candidate(candidate) for candidate in candidates]
    merged = _merge_candidates(normalized)
    features = [_ranking_features(candidate) for candidate in merged]
    by_id = {item["candidate_id"]: item for item in features}
    ranked = []
    for candidate in merged:
        item = dict(candidate)
        item["tool_ranking"] = by_id.get(str(candidate.get("id")), {})
        ranked.append(item)
    ranked.sort(key=lambda item: float(item.get("tool_ranking", {}).get("tool_score") or 0.0), reverse=True)
    return {
        "schema": "nexus.candidate_processing.v1",
        "normalized": normalized,
        "merged": ranked,
        "ranking_features": features,
        "model_input": _model_input(ranked[:max_candidates]),
    }


def rerank_candidates(candidates: list[dict[str, Any]], review: dict[str, Any]) -> list[dict[str, Any]]:
    reviews = {str(item.get("candidate_id")): item for item in review.get("reviews", []) if isinstance(item, dict)}
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        judgment = reviews.get(str(candidate.get("id")), {})
        model_score = float(judgment.get("score") or 0.0)
        tool_score = float(item.get("tool_ranking", {}).get("tool_score") or 0.0)
        item["score"] = round((model_score * 0.72) + (tool_score * 0.28), 4)
        item["model_score"] = model_score
        item["tool_score"] = tool_score
        item["reason"] = str(judgment.get("reason") or item.get("tool_ranking", {}).get("reason") or "")
        item["risks"] = judgment.get("risks") if isinstance(judgment.get("risks"), list) else []
        item["recommended_use"] = str(judgment.get("recommended_use") or "unknown")
        ranked.append(item)
    ranked.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return ranked


def _normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    item = dict(candidate)
    canonical_url = canonicalize_url(str(item.get("url") or ""))
    repo = _canonical_repo(str(item.get("repo") or ""), canonical_url)
    source = str(item.get("source") or "")
    title = _compact_text(str(item.get("title") or ""))
    summary = _compact_text(str(item.get("summary") or ""))
    identity_parts = [repo, canonical_url or title.lower(), source if not canonical_url else ""]
    identity = "|".join(part for part in identity_parts if part)
    digest = sha1(identity.encode("utf-8")).hexdigest()[:12]
    item["id"] = f"cand-{digest}"
    item["canonical_url"] = canonical_url
    item["canonical_repo"] = repo
    item["normalized_title"] = title
    item["normalized_summary"] = summary
    item["identity_key"] = repo or canonical_url or f"{source}:{title.lower()}"
    item["evidence_count"] = len([e for e in item.get("evidence", []) if isinstance(e, str)])
    return item


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return url.strip()
    scheme = "https" if parsed.scheme in {"http", "https"} else parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/") or "/"
    query_pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        lower = key.lower()
        if lower in TRACKING_KEYS or any(lower.startswith(prefix) for prefix in TRACKING_PREFIXES):
            continue
        query_pairs.append((key, value))
    query = urlencode(sorted(query_pairs))
    return urlunparse((scheme, netloc, path, "", query, ""))


def _canonical_repo(repo: str, canonical_url: str) -> str:
    if repo:
        return repo.strip().lower().removeprefix("https://").removeprefix("http://").removesuffix(".git").strip("/")
    parsed = urlparse(canonical_url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]
    if host in {"github.com", "gitee.com"} and len(parts) >= 2:
        return f"{host}/{parts[0].lower()}/{parts[1].lower().removesuffix('.git')}"
    return ""


def _merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[str(candidate.get("identity_key") or candidate.get("id"))].append(candidate)
    merged: list[dict[str, Any]] = []
    for records in grouped.values():
        primary = max(records, key=_single_record_weight)
        item = dict(primary)
        sources = sorted({str(record.get("source") or "") for record in records if record.get("source")})
        queries = sorted({str(query) for record in records for query in record.get("matched_queries", []) if isinstance(query, str)})
        raw_refs = sorted({str(ref) for record in records for ref in record.get("raw_artifact_refs", []) if isinstance(ref, str)})
        evidence = sorted({str(e) for record in records for e in record.get("evidence", []) if isinstance(e, str)})
        urls = sorted({str(record.get("canonical_url") or record.get("url") or "") for record in records if record.get("url") or record.get("canonical_url")})
        item["merged_from_count"] = len(records)
        item["merged_sources"] = sources
        item["matched_queries"] = queries
        item["raw_artifact_refs"] = raw_refs
        item["evidence"] = evidence
        item["duplicate_urls"] = urls
        item["id"] = str(primary.get("id"))
        merged.append(item)
    return merged


def _ranking_features(candidate: dict[str, Any]) -> dict[str, Any]:
    sources = candidate.get("merged_sources") if isinstance(candidate.get("merged_sources"), list) else [candidate.get("source")]
    source_score = min(sum(SOURCE_WEIGHTS.get(str(source), 0.06) for source in sources), 0.42)
    stars = candidate.get("stars")
    star_score = 0.0
    if isinstance(stars, int):
        if stars >= 5000:
            star_score = 0.18
        elif stars >= 1000:
            star_score = 0.14
        elif stars >= 100:
            star_score = 0.09
        elif stars > 0:
            star_score = 0.04
    evidence_score = min(float(candidate.get("merged_from_count") or 1) * 0.04 + float(len(candidate.get("evidence", []) or [])) * 0.02, 0.18)
    availability = candidate.get("availability") if isinstance(candidate.get("availability"), dict) else {}
    availability_score = 0.1 if availability.get("ok") or availability.get("status") in {"ok", "reachable"} else 0.0
    local_score = 0.08 if "chinese_web" in sources or "gitee_repo" in sources else 0.0
    tool_score = round(min(source_score + star_score + evidence_score + availability_score + local_score, 1.0), 4)
    return {
        "candidate_id": candidate.get("id"),
        "tool_score": tool_score,
        "source_score": round(source_score, 4),
        "star_score": star_score,
        "evidence_score": round(evidence_score, 4),
        "availability_score": availability_score,
        "localization_score": local_score,
        "reason": f"sources={','.join(str(s) for s in sources if s)} merged={candidate.get('merged_from_count', 1)} stars={stars or 0}",
    }


def _model_input(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = []
    for candidate in candidates:
        payload.append(
            {
                "id": candidate.get("id"),
                "title": candidate.get("title"),
                "summary": candidate.get("summary"),
                "url": candidate.get("canonical_url") or candidate.get("url"),
                "repo": candidate.get("canonical_repo") or candidate.get("repo"),
                "sources": candidate.get("merged_sources"),
                "matched_queries": candidate.get("matched_queries"),
                "tool_ranking": candidate.get("tool_ranking"),
                "evidence": (candidate.get("evidence") or [])[:5],
                "raw_artifact_refs": (candidate.get("raw_artifact_refs") or [])[:8],
                "localization": candidate.get("localization", {}),
                "availability": candidate.get("availability", {}),
            }
        )
    return payload


def _single_record_weight(candidate: dict[str, Any]) -> float:
    source = str(candidate.get("source") or "")
    return SOURCE_WEIGHTS.get(source, 0.0) + (0.02 if candidate.get("summary") else 0.0)


def _compact_text(text: str) -> str:
    return " ".join(text.split())
