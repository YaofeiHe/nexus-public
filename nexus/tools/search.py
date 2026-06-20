from __future__ import annotations

from pathlib import Path
import re


TEXT_SUFFIXES = {".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".py", ".ts", ".tsx", ".js", ".jsx"}


def local_inventory_search(repo_scan: dict[str, object], queries: list[str], *, project_path: Path | None = None) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    package_files = repo_scan.get("package_files")
    package_count = len(package_files) if isinstance(package_files, list) else 0
    candidates = [
        {
            "id": "local-project",
            "title": "当前项目本地结构",
            "source": "local_inventory",
            "url": str(repo_scan.get("project_path") or ""),
            "summary": f"只读扫描到 {package_count} 个 package/说明文件，可用于判断项目技术栈和 workflow 接入点。",
            "evidence": package_files if isinstance(package_files, list) else [],
            "retrieval_mode": "local",
            "raw": {"queries": queries[:5]},
        },
    ]
    statuses = [
        {"source": "local_inventory", "status": "ok", "retrieval_mode": "local", "candidate_count": 1},
    ]
    if project_path is not None:
        content_candidates = local_content_search(project_path, repo_scan, queries)
        candidates.extend(content_candidates)
        statuses.append(
            {
                "source": "local_content_keyword",
                "status": "ok",
                "retrieval_mode": "local",
                "candidate_count": len(content_candidates),
            }
        )
    candidates.append(
        {
            "id": "mcp-pattern",
            "title": "Codex MCP / CLI provider 集成模式",
            "source": "builtin_chinese_internet_prior",
            "url": "local://nexus/provider-pattern",
            "summary": "面向中文互联网工作流时，模型节点应通过 MCP/CLI/API provider 显式调用，工具检索只负责机械化执行。",
            "evidence": ["codex mcp-server", "codex exec --output-schema", "中文优先 query planning"],
            "retrieval_mode": "builtin",
            "raw": {"queries": queries[:5]},
        }
    )
    statuses.append({"source": "builtin_chinese_internet_prior", "status": "ok", "retrieval_mode": "builtin", "candidate_count": 1})
    return candidates, statuses


def local_content_search(project_path: Path, repo_scan: dict[str, object], queries: list[str], *, max_files: int = 20) -> list[dict[str, object]]:
    root = project_path.expanduser().resolve()
    samples = repo_scan.get("file_samples")
    files = [str(item) for item in samples if isinstance(item, str)] if isinstance(samples, list) else []
    terms = _query_terms(queries)
    candidates: list[dict[str, object]] = []
    for rel in files:
        if len(candidates) >= max_files:
            break
        path = root / rel
        if not _safe_text_file(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        score, hits = _score_text(text, terms)
        if score <= 0:
            continue
        candidates.append(
            {
                "id": "local-file-" + re.sub(r"[^A-Za-z0-9]+", "-", rel).strip("-")[:80],
                "title": rel,
                "source": "local_content_keyword",
                "url": str(path),
                "summary": f"只读内容匹配到 {len(hits)} 个调研关键词，可作为候选证据文件。",
                "evidence": hits[:10],
                "retrieval_mode": "local",
                "raw": {"score": score, "matched_terms": hits[:20]},
            }
        )
    return sorted(candidates, key=lambda item: float(item["raw"]["score"]), reverse=True)


def _query_terms(queries: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for query in queries:
        for term in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}", query):
            key = term.lower()
            if key in seen or key in {"the", "and", "for", "with", "workflow"}:
                continue
            seen.add(key)
            terms.append(term)
    return terms[:30]


def _safe_text_file(path: Path) -> bool:
    lowered = str(path).lower()
    if any(marker in lowered for marker in [".env", ".ssh", "token", "cookie", "credential", "id_rsa", "id_ed25519"]):
        return False
    return path.is_file() and path.suffix in TEXT_SUFFIXES


def _score_text(text: str, terms: list[str]) -> tuple[float, list[str]]:
    lowered = text.lower()
    hits = [term for term in terms if term.lower() in lowered]
    if not hits:
        return 0.0, []
    return len(set(term.lower() for term in hits)) / max(len(terms), 1), hits
