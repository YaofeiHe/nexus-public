from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from nexus.tools.search import local_inventory_search
from nexus.tools.search_models import CandidateRecord, SourceStatus


DEFAULT_NON_OPENAI_CHINESE_WEB_PROVIDERS = ["tavily", "brave"]
BAIDU_SERP_PROVIDERS = {"serpapi_baidu", "searchapi_baidu"}
DEV_FALLBACK_PROVIDERS = {"searxng", "jina_reader"}
SECRET_ENV_NAMES = [
    "TAVILY_API_KEY",
    "BRAVE_SEARCH_API_KEY",
    "SERPAPI_API_KEY",
    "SEARCHAPI_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
]
SECRET_KEYS = {"authorization", "x-subscription-token", "api_key", "cookie", "set-cookie"}


class SearchAdapter(Protocol):
    source: str

    def search(
        self,
        queries: list[str],
        *,
        project_path: Path,
        repo_scan: dict[str, object],
        online: bool,
        raw_dir: Path | None = None,
    ) -> tuple[list[CandidateRecord], SourceStatus]:
        ...


class LocalAdapter:
    source = "local"

    def search(
        self,
        queries: list[str],
        *,
        project_path: Path,
        repo_scan: dict[str, object],
        online: bool = False,
        raw_dir: Path | None = None,
    ) -> tuple[list[CandidateRecord], SourceStatus]:
        del online, raw_dir
        raw_candidates, _statuses = local_inventory_search(repo_scan, queries, project_path=project_path)
        records = [_candidate_from_dict(item) for item in raw_candidates]
        return records, _status(
            self.source,
            "ok",
            "local",
            queries,
            raw_result_count=len(records),
            candidate_count=len(records),
            reason="本地 inventory 和文本检索完成。",
            approved=True,
        )


class GithubRepoAdapter:
    source = "github_repo"

    def search(
        self,
        queries: list[str],
        *,
        project_path: Path,
        repo_scan: dict[str, object],
        online: bool,
        raw_dir: Path | None = None,
    ) -> tuple[list[CandidateRecord], SourceStatus]:
        del project_path, repo_scan
        if not online:
            return [], _approval_required(self.source, queries, "GitHub repo 检索需要 online-search 审批。")
        raw_dir = _ensure_raw_dir(raw_dir, self.source)
        try:
            records = self._search_gh(queries, raw_dir=raw_dir)
            if records:
                return records, _status(self.source, "ok", "online_api", queries, len(records), len(records), approved=True, evidence_refs=_refs(records))
            rest_records = self._search_rest(queries, raw_dir=raw_dir)
            if rest_records:
                return rest_records, _status(
                    self.source,
                    "partial",
                    "online_api",
                    queries,
                    len(rest_records),
                    len(rest_records),
                    issue_type="gh_empty_rest_fallback",
                    fallback_used=True,
                    reason="gh 搜索返回 0 个候选，已使用 GitHub REST fallback。",
                    approved=True,
                    evidence_refs=_refs(rest_records),
                )
            return [], _status(self.source, "no_results", "online_api", queries, 0, 0, issue_type="no_results", reason="GitHub repo 在线检索完成但没有候选。", approved=True)
        except Exception as gh_exc:
            try:
                records = self._search_rest(queries, raw_dir=raw_dir)
                return records, _status(
                    self.source,
                    "partial" if records else "failed",
                    "online_api",
                    queries,
                    len(records),
                    len(records),
                    issue_type="gh_failed_rest_fallback" if records else _issue_type(gh_exc),
                    fallback_used=True,
                    reason=f"gh 搜索失败，REST fallback 返回 {len(records)} 个候选：{_short_error(gh_exc)}",
                    approved=True,
                    retryable=not records,
                    evidence_refs=_refs(records),
                )
            except Exception as rest_exc:
                return [], _exception_status(self.source, "online_api", queries, rest_exc, approved=True)

    def _search_gh(self, queries: list[str], *, raw_dir: Path | None = None) -> list[CandidateRecord]:
        if not shutil.which("gh"):
            raise RuntimeError("gh CLI not found")
        records: list[CandidateRecord] = []
        for idx, query in enumerate(queries, start=1):
            completed = subprocess.run(
                [
                    "gh",
                    "search",
                    "repos",
                    query,
                    "--json",
                    "description,license,fullName,pushedAt,stargazersCount,url",
                    "--limit",
                    "10",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            raw_path = _write_raw(raw_dir, f"gh_repo_{idx}.json", completed.stdout)
            for item in json.loads(completed.stdout or "[]"):
                records.append(_github_candidate(item, query=query, retrieval_mode="online_api:gh", raw_path=raw_path))
        return records

    def _search_rest(self, queries: list[str], *, raw_dir: Path | None = None) -> list[CandidateRecord]:
        records: list[CandidateRecord] = []
        token = os.environ.get("GITHUB_TOKEN")
        for idx, query in enumerate(queries, start=1):
            params = urlencode({"q": query, "per_page": "10", "sort": "updated"})
            headers = {"Accept": "application/vnd.github+json", "User-Agent": "nexus-readonly-search"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            payload = _get_json(f"https://api.github.com/search/repositories?{params}", headers=headers)
            raw_path = _write_raw(raw_dir, f"github_rest_{idx}.json", json.dumps(payload, ensure_ascii=False))
            for item in payload.get("items", []) if isinstance(payload, dict) else []:
                if isinstance(item, dict):
                    records.append(_github_candidate(item, query=query, retrieval_mode="online_api:github_rest", raw_path=raw_path))
        return records


class GiteeRepoAdapter:
    source = "gitee_repo"
    url = "https://gitee.com/api/v5/search/repositories"

    def search(
        self,
        queries: list[str],
        *,
        project_path: Path,
        repo_scan: dict[str, object],
        online: bool,
        raw_dir: Path | None = None,
    ) -> tuple[list[CandidateRecord], SourceStatus]:
        del project_path, repo_scan
        if not online:
            return [], _approval_required(self.source, queries, "Gitee repo 检索需要 online-search 审批。")
        raw_dir = _ensure_raw_dir(raw_dir, self.source)
        token = os.environ.get("GITEE_TOKEN", "")
        records: list[CandidateRecord] = []
        try:
            for idx, query in enumerate(queries, start=1):
                params = {"q": query, "per_page": "10"}
                if token:
                    params["access_token"] = token
                payload = _get_json(f"{self.url}?{urlencode(params)}", headers={"User-Agent": "nexus-readonly-search"})
                raw_path = _write_raw(raw_dir, f"gitee_{idx}.json", json.dumps(payload, ensure_ascii=False))
                items = payload if isinstance(payload, list) else payload.get("items", []) if isinstance(payload, dict) else []
                for item in items:
                    if isinstance(item, dict):
                        records.append(_gitee_candidate(item, query=query, raw_path=raw_path))
            if records:
                return records, _status(self.source, "ok", "online_api", queries, len(records), len(records), approved=True, auth_present=bool(token), evidence_refs=_refs(records))
            return [], _status(
                self.source,
                "partial",
                "online_api",
                queries,
                0,
                0,
                issue_type="empty_response",
                reason="Gitee API 真实请求完成但没有候选；该来源需在运行时复核稳定性。",
                approved=True,
                auth_present=bool(token),
                next_options=["try_chinese_web", "broaden_queries", "external_prompt"],
            )
        except Exception as exc:
            status = _exception_status(self.source, "online_api", queries, exc, approved=True, auth_present=bool(token))
            if status.issue_type in {"unauthorized", "auth_missing"}:
                status.auth_required = True
                status.next_options = ["set_GITEE_TOKEN", "try_chinese_web"]
            return [], status


class McpRegistryAdapter:
    source = "mcp_registry"
    url = "https://registry.modelcontextprotocol.io/v0.1/servers"

    def search(
        self,
        queries: list[str],
        *,
        project_path: Path,
        repo_scan: dict[str, object],
        online: bool,
        raw_dir: Path | None = None,
    ) -> tuple[list[CandidateRecord], SourceStatus]:
        del project_path, repo_scan
        if not online:
            return [], _approval_required(self.source, queries, "MCP registry 检索需要 online-search 审批。")
        raw_dir = _ensure_raw_dir(raw_dir, self.source)
        try:
            payload = _get_json(f"{self.url}?{urlencode({'limit': '100'})}", headers={"Accept": "application/json", "User-Agent": "nexus-readonly-search"})
            raw_path = _write_raw(raw_dir, "mcp_registry.json", json.dumps(payload, ensure_ascii=False))
            servers = payload.get("servers") or payload.get("items") or [] if isinstance(payload, dict) else payload
            records: list[CandidateRecord] = []
            for query in queries:
                words = [word.lower() for word in query.split() if len(word) >= 3]
                for item in servers if isinstance(servers, list) else []:
                    if not isinstance(item, dict):
                        continue
                    text = " ".join(str(item.get(key) or "") for key in ["name", "description", "repository", "homepage"]).lower()
                    if not words or any(word in text for word in words[:8]):
                        records.append(_mcp_candidate(item, query=query, raw_path=raw_path))
            if records:
                return records, _status(self.source, "ok", "online_api", queries, len(records), len(records), approved=True, evidence_refs=_refs(records))
            return [], _status(self.source, "no_results", "online_api", queries, 0, 0, issue_type="no_results", reason="MCP registry 在线检索完成但没有候选。", approved=True)
        except Exception as exc:
            return [], _exception_status(self.source, "online_api", queries, exc, approved=True)


class OfficialDocsAdapter:
    source = "official_docs"

    def search(
        self,
        queries: list[str],
        *,
        project_path: Path,
        repo_scan: dict[str, object],
        online: bool,
        raw_dir: Path | None = None,
    ) -> tuple[list[CandidateRecord], SourceStatus]:
        del repo_scan
        if not online:
            return [], _approval_required(self.source, queries, "官方文档检索需要 online-search 审批。")
        raw_dir = _ensure_raw_dir(raw_dir, self.source)
        domains = _extract_domains(queries)
        if not domains:
            project_domains = _extract_domains([str(project_path)])
            domains = project_domains
        if not domains:
            return [], _status(
                self.source,
                "skipped",
                "online_http",
                queries,
                0,
                0,
                issue_type="unsupported",
                reason="检索计划没有给出官方域名或 URL；nexus 不做泛爬虫。",
                approved=True,
                next_options=["ask_model_for_domains", "chinese_web"],
            )
        records: list[CandidateRecord] = []
        failures: list[str] = []
        for domain in domains[:5]:
            for suffix in ["/llms.txt", "/llms-full.txt", "/sitemap.xml"]:
                url = f"https://{domain}{suffix}"
                try:
                    text = _get_text(url, headers={"User-Agent": "nexus-readonly-docs"}, limit=50000)
                    raw_path = _write_raw(raw_dir, f"{_safe_name(domain)}{suffix.replace('/', '_')}.txt", text)
                    if text.strip():
                        records.append(
                            CandidateRecord(
                                title=f"{domain}{suffix}",
                                summary=text[:500],
                                source=self.source,
                                url=url,
                                retrieval_mode="online_http",
                                candidate_type="docs",
                                evidence=[text[:300]],
                                matched_queries=queries[:5],
                                raw_artifact_refs=[str(raw_path)] if raw_path else [],
                                tags=["official-docs"],
                            )
                        )
                except Exception as exc:
                    failures.append(f"{url}: {_short_error(exc)}")
        if records:
            return records, _status(self.source, "ok", "online_http", queries, len(records), len(records), approved=True, evidence_refs=_refs(records))
        return [], _status(
            self.source,
            "partial",
            "online_http",
            queries,
            0,
            0,
            issue_type="no_results",
            reason="官方文档只读探测没有拿到 llms/sitemap 候选。",
            approved=True,
            raw_error_summary="; ".join(failures)[:500],
            next_options=["chinese_web", "manual_domain"],
        )


class ChineseWebAdapter:
    source = "chinese_web"

    def __init__(self, providers: list[str] | None = None) -> None:
        self.providers = providers

    def search(
        self,
        queries: list[str],
        *,
        project_path: Path,
        repo_scan: dict[str, object],
        online: bool,
        raw_dir: Path | None = None,
    ) -> tuple[list[CandidateRecord], SourceStatus]:
        del project_path, repo_scan
        if not online:
            return [], _approval_required(self.source, queries, "中文互联网检索需要 online-search 审批。")
        raw_dir = _ensure_raw_dir(raw_dir, self.source)
        provider_statuses: list[SourceStatus] = []
        records: list[CandidateRecord] = []
        provider_names = self.providers or resolve_chinese_web_providers()
        for provider_name in provider_names:
            provider_records, provider_status = self._search_provider(provider_name, queries, raw_dir=raw_dir)
            provider_statuses.append(provider_status)
            records.extend(provider_records)
            if provider_status.status == "ok" and len(records) >= 5:
                break
        deduped = _dedupe_records(records)
        aggregate = _aggregate_chinese_web_status(queries, provider_statuses, deduped)
        return deduped, aggregate

    def _search_provider(self, provider_name: str, queries: list[str], *, raw_dir: Path | None) -> tuple[list[CandidateRecord], SourceStatus]:
        if provider_name == "tavily":
            return self._tavily_search(queries, raw_dir=raw_dir)
        if provider_name == "brave":
            return self._brave_search(queries, raw_dir=raw_dir)
        if provider_name == "serpapi_baidu":
            return self._serpapi_baidu_search(queries, raw_dir=raw_dir)
        if provider_name == "searchapi_baidu":
            return self._searchapi_baidu_search(queries, raw_dir=raw_dir)
        if provider_name == "openai_web_search":
            return self._openai_web_search(queries, raw_dir=raw_dir)
        if provider_name == "searxng":
            return self._searxng_search(queries, raw_dir=raw_dir)
        if provider_name == "jina_reader":
            return self._jina_reader_search(queries, raw_dir=raw_dir)
        status = _status(
            self.source,
            "disabled",
            "online_api",
            queries,
            issue_type="unsupported",
            reason=f"未知中文互联网 provider：{provider_name}",
            approved=True,
            provider=provider_name,
            configured=False,
            attempted=False,
        )
        raw_path = _write_raw_artifact(raw_dir, provider_name, "provider_disabled", {"provider": provider_name, "queries": queries}, None, {"status": "disabled", "reason": status.reason})
        status.raw_artifact_refs = [str(raw_path)] if raw_path else []
        return [], status

    def _openai_web_search(self, queries: list[str], *, raw_dir: Path | None) -> tuple[list[CandidateRecord], SourceStatus]:
        if os.environ.get("NEXUS_ENABLE_OPENAI_WEB_SEARCH") != "1":
            status = _status(
                self.source,
                "disabled",
                "online_api",
                queries,
                issue_type="disabled",
                reason="OpenAI web_search 默认禁用；只有 NEXUS_ENABLE_OPENAI_WEB_SEARCH=1 才允许使用。",
                approved=True,
                provider="openai_web_search",
                configured=False,
                attempted=False,
            )
            raw_path = _write_raw_artifact(raw_dir, "openai_web_search", "disabled", {"queries": queries}, None, {"status": "disabled", "reason": status.reason})
            status.raw_artifact_refs = [str(raw_path)] if raw_path else []
            return [], status
        if not os.environ.get("OPENAI_API_KEY"):
            return _missing_key_status(self.source, "openai_web_search", queries, "OPENAI_API_KEY", raw_dir)
        body = {
            "model": os.environ.get("NEXUS_OPENAI_WEB_MODEL", "gpt-4.1-mini"),
            "tools": [{"type": "web_search_preview"}],
            "input": "请面向中文互联网检索以下问题，返回相关项目、官方文档、中文技术资料和来源链接：\n" + "\n".join(f"- {query}" for query in queries[:6]),
        }
        try:
            payload, http_status, headers = _post_json_with_meta(
                "https://api.openai.com/v1/responses",
                body,
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
            )
            raw_path = _write_raw_artifact(
                raw_dir,
                "openai_web_search",
                "openai_web_search",
                {"method": "POST", "url": "https://api.openai.com/v1/responses", "headers": {"Authorization": "Bearer [REDACTED]", "Content-Type": "application/json"}, "json": body},
                {"http_status": http_status, "headers": headers, "body": payload},
                None,
            )
            records = _records_from_openai_response(payload, queries, raw_path)
            status_value = "ok" if records else "partial"
            issue = "none" if records else "no_results"
            status = _status(self.source, status_value, "online_api", queries, 1, len(records), issue_type=issue, reason="OpenAI web_search 真实调用完成。", approved=True, auth_present=True, provider="openai_web_search", configured=True, attempted=True, evidence_refs=_refs(records), raw_artifact_refs=[str(raw_path)] if raw_path else [], http_statuses=[http_status])
            return records, status
        except Exception as exc:
            status = _exception_status(self.source, "online_api", queries, exc, approved=True, auth_present=True, provider="openai_web_search", configured=True, attempted=True)
            raw_path = _write_error_artifact(raw_dir, "openai_web_search", "openai_web_search_error", {"queries": queries}, exc)
            status.raw_artifact_refs = [str(raw_path)] if raw_path else []
            return [], status

    def _brave_search(self, queries: list[str], *, raw_dir: Path | None) -> tuple[list[CandidateRecord], SourceStatus]:
        api_key = _secret_from_env_or_file("BRAVE_SEARCH_API_KEY", "NEXUS_BRAVE_KEY_FILE")
        if not api_key:
            return _missing_key_status(self.source, "brave", queries, "BRAVE_SEARCH_API_KEY", raw_dir)
        records: list[CandidateRecord] = []
        raw_refs: list[str] = []
        http_statuses: list[int] = []
        try:
            for idx, query in enumerate(queries[:5], start=1):
                params = {"q": query, "count": "10", "country": "CN", "search_lang": "zh", "ui_lang": "zh-CN", "safesearch": "moderate", "extra_snippets": "true"}
                url = "https://api.search.brave.com/res/v1/web/search?" + urlencode(params)
                payload, http_status, headers = _get_json_with_meta(
                    url,
                    headers={"Accept": "application/json", "X-Subscription-Token": api_key},
                )
                raw_path = _write_raw_artifact(
                    raw_dir,
                    "brave",
                    f"brave_{idx}",
                    {"method": "GET", "url": "https://api.search.brave.com/res/v1/web/search", "params": params, "headers": {"Accept": "application/json", "X-Subscription-Token": "[REDACTED]"}},
                    {"http_status": http_status, "headers": headers, "body": payload},
                    None,
                )
                if raw_path:
                    raw_refs.append(str(raw_path))
                http_statuses.append(http_status)
                for item in (payload.get("web") or {}).get("results", []) if isinstance(payload, dict) else []:
                    if isinstance(item, dict) and item.get("url"):
                        records.append(_web_candidate(item, source=self.source, query=query, provider="brave", raw_path=raw_path))
            return records, _status(self.source, "ok" if records else "no_results", "online_api", queries, len(records), len(records), issue_type="none" if records else "no_results", reason="Brave Search 真实调用完成。", approved=True, auth_present=True, provider="brave", configured=True, attempted=True, evidence_refs=_refs(records), raw_artifact_refs=raw_refs, http_statuses=http_statuses)
        except Exception as exc:
            status = _exception_status(self.source, "online_api", queries, exc, approved=True, auth_present=True, provider="brave", configured=True, attempted=True)
            raw_path = _write_error_artifact(raw_dir, "brave", "brave_error", {"queries": queries}, exc)
            status.raw_artifact_refs = [str(raw_path)] if raw_path else []
            return [], status

    def _tavily_search(self, queries: list[str], *, raw_dir: Path | None) -> tuple[list[CandidateRecord], SourceStatus]:
        api_key = _secret_from_env_or_file("TAVILY_API_KEY", "NEXUS_TAVILY_KEY_FILE")
        if not api_key:
            return _missing_key_status(self.source, "tavily", queries, "TAVILY_API_KEY", raw_dir)
        records: list[CandidateRecord] = []
        raw_refs: list[str] = []
        http_statuses: list[int] = []
        try:
            for idx, query in enumerate(queries[:5], start=1):
                body = {
                    "query": query,
                    "max_results": 10,
                    "search_depth": "basic",
                    "topic": "general",
                    "country": "china",
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_favicon": True,
                    "include_usage": True,
                }
                payload, http_status, headers = _post_json_with_meta(
                    "https://api.tavily.com/search",
                    body,
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                )
                raw_path = _write_raw_artifact(
                    raw_dir,
                    "tavily",
                    f"tavily_{idx}",
                    {"method": "POST", "url": "https://api.tavily.com/search", "headers": {"Content-Type": "application/json", "Authorization": "Bearer [REDACTED]"}, "json": body},
                    {"http_status": http_status, "headers": headers, "body": payload},
                    None,
                )
                if raw_path:
                    raw_refs.append(str(raw_path))
                http_statuses.append(http_status)
                for item in payload.get("results", []) if isinstance(payload, dict) else []:
                    if isinstance(item, dict) and item.get("url"):
                        records.append(_web_candidate(item, source=self.source, query=query, provider="tavily", raw_path=raw_path))
            return records, _status(self.source, "ok" if records else "no_results", "online_api", queries, len(records), len(records), issue_type="none" if records else "no_results", reason="Tavily Search 真实调用完成。", approved=True, auth_present=True, provider="tavily", configured=True, attempted=True, evidence_refs=_refs(records), raw_artifact_refs=raw_refs, http_statuses=http_statuses)
        except Exception as exc:
            status = _exception_status(self.source, "online_api", queries, exc, approved=True, auth_present=True, provider="tavily", configured=True, attempted=True)
            raw_path = _write_error_artifact(raw_dir, "tavily", "tavily_error", {"queries": queries}, exc)
            status.raw_artifact_refs = [str(raw_path)] if raw_path else []
            return [], status

    def _serpapi_baidu_search(self, queries: list[str], *, raw_dir: Path | None) -> tuple[list[CandidateRecord], SourceStatus]:
        if os.environ.get("NEXUS_ENABLE_BAIDU_SERP") != "1":
            return _disabled_status(self.source, "serpapi_baidu", queries, "Baidu SERP 需要 NEXUS_ENABLE_BAIDU_SERP=1。", raw_dir)
        api_key = _secret_from_env_or_file("SERPAPI_API_KEY", "NEXUS_SERPAPI_KEY_FILE")
        if not api_key:
            return _missing_key_status(self.source, "serpapi_baidu", queries, "SERPAPI_API_KEY", raw_dir)
        records: list[CandidateRecord] = []
        raw_refs: list[str] = []
        http_statuses: list[int] = []
        try:
            for idx, query in enumerate(queries[:5], start=1):
                params = {"engine": "baidu", "q": query, "ct": "2", "rn": "10", "api_key": api_key, "output": "json"}
                url = "https://serpapi.com/search?" + urlencode(params)
                payload, http_status, headers = _get_json_with_meta(url, headers={"Accept": "application/json", "User-Agent": "nexus-readonly-search"})
                raw_path = _write_raw_artifact(raw_dir, "serpapi_baidu", f"serpapi_baidu_{idx}", {"method": "GET", "url": "https://serpapi.com/search", "params": params}, {"http_status": http_status, "headers": headers, "body": payload}, None)
                if raw_path:
                    raw_refs.append(str(raw_path))
                http_statuses.append(http_status)
                records.extend(_baidu_candidates("serpapi_baidu", query, payload, raw_path))
            return records, _status(self.source, "ok" if records else "no_results", "online_api", queries, len(records), len(records), issue_type="none" if records else "no_results", reason="SerpApi Baidu 真实调用完成。", approved=True, auth_present=True, provider="serpapi_baidu", configured=True, attempted=True, evidence_refs=_refs(records), raw_artifact_refs=raw_refs, http_statuses=http_statuses)
        except Exception as exc:
            status = _exception_status(self.source, "online_api", queries, exc, approved=True, auth_present=True, provider="serpapi_baidu", configured=True, attempted=True)
            raw_path = _write_error_artifact(raw_dir, "serpapi_baidu", "serpapi_baidu_error", {"queries": queries}, exc)
            status.raw_artifact_refs = [str(raw_path)] if raw_path else []
            return [], status

    def _searchapi_baidu_search(self, queries: list[str], *, raw_dir: Path | None) -> tuple[list[CandidateRecord], SourceStatus]:
        if os.environ.get("NEXUS_ENABLE_BAIDU_SERP") != "1":
            return _disabled_status(self.source, "searchapi_baidu", queries, "Baidu SERP 需要 NEXUS_ENABLE_BAIDU_SERP=1。", raw_dir)
        api_key = _secret_from_env_or_file("SEARCHAPI_API_KEY", "NEXUS_SEARCHAPI_KEY_FILE")
        if not api_key:
            return _missing_key_status(self.source, "searchapi_baidu", queries, "SEARCHAPI_API_KEY", raw_dir)
        records: list[CandidateRecord] = []
        raw_refs: list[str] = []
        http_statuses: list[int] = []
        try:
            for idx, query in enumerate(queries[:5], start=1):
                params = {"engine": "baidu", "q": query, "ct": "1", "num": "10", "api_key": api_key}
                url = "https://www.searchapi.io/api/v1/search?" + urlencode(params)
                payload, http_status, headers = _get_json_with_meta(url, headers={"Accept": "application/json", "User-Agent": "nexus-readonly-search"})
                raw_path = _write_raw_artifact(raw_dir, "searchapi_baidu", f"searchapi_baidu_{idx}", {"method": "GET", "url": "https://www.searchapi.io/api/v1/search", "params": params}, {"http_status": http_status, "headers": headers, "body": payload}, None)
                if raw_path:
                    raw_refs.append(str(raw_path))
                http_statuses.append(http_status)
                records.extend(_baidu_candidates("searchapi_baidu", query, payload, raw_path))
            return records, _status(self.source, "ok" if records else "no_results", "online_api", queries, len(records), len(records), issue_type="none" if records else "no_results", reason="SearchApi Baidu 真实调用完成。", approved=True, auth_present=True, provider="searchapi_baidu", configured=True, attempted=True, evidence_refs=_refs(records), raw_artifact_refs=raw_refs, http_statuses=http_statuses)
        except Exception as exc:
            status = _exception_status(self.source, "online_api", queries, exc, approved=True, auth_present=True, provider="searchapi_baidu", configured=True, attempted=True)
            raw_path = _write_error_artifact(raw_dir, "searchapi_baidu", "searchapi_baidu_error", {"queries": queries}, exc)
            status.raw_artifact_refs = [str(raw_path)] if raw_path else []
            return [], status

    def _searxng_search(self, queries: list[str], *, raw_dir: Path | None) -> tuple[list[CandidateRecord], SourceStatus]:
        if os.environ.get("NEXUS_ENABLE_SEARXNG") != "1":
            return _disabled_status(self.source, "searxng", queries, "SearXNG 仅在 NEXUS_ENABLE_SEARXNG=1 时作为开发兜底。", raw_dir)
        base = os.environ.get("SEARXNG_BASE_URL", "").rstrip("/")
        if not base:
            return _missing_key_status(self.source, "searxng", queries, "SEARXNG_BASE_URL", raw_dir)
        records: list[CandidateRecord] = []
        raw_refs: list[str] = []
        http_statuses: list[int] = []
        try:
            for idx, query in enumerate(queries[:5], start=1):
                params = {"q": query, "format": "json", "language": "zh-CN", "categories": "general"}
                payload, http_status, headers = _get_json_with_meta(f"{base}/search?{urlencode(params)}", headers={"Accept": "application/json"})
                raw_path = _write_raw_artifact(raw_dir, "searxng", f"searxng_{idx}", {"method": "GET", "url": f"{base}/search", "params": params}, {"http_status": http_status, "headers": headers, "body": payload}, None)
                if raw_path:
                    raw_refs.append(str(raw_path))
                http_statuses.append(http_status)
                for item in payload.get("results", []) if isinstance(payload, dict) else []:
                    if isinstance(item, dict) and item.get("url"):
                        records.append(_web_candidate(item, source=self.source, query=query, provider="searxng", raw_path=raw_path))
            return records, _status(self.source, "ok" if records else "no_results", "online_api", queries, len(records), len(records), issue_type="none" if records else "no_results", reason="SearXNG 开发兜底真实调用完成。", approved=True, provider="searxng", configured=True, attempted=True, evidence_refs=_refs(records), raw_artifact_refs=raw_refs, http_statuses=http_statuses)
        except Exception as exc:
            status = _exception_status(self.source, "online_api", queries, exc, approved=True, provider="searxng", configured=True, attempted=True)
            raw_path = _write_error_artifact(raw_dir, "searxng", "searxng_error", {"queries": queries}, exc)
            status.raw_artifact_refs = [str(raw_path)] if raw_path else []
            return [], status

    def _jina_reader_search(self, queries: list[str], *, raw_dir: Path | None) -> tuple[list[CandidateRecord], SourceStatus]:
        if os.environ.get("NEXUS_ENABLE_JINA_READER") != "1":
            return _disabled_status(self.source, "jina_reader", queries, "Jina Reader SERP 仅在 NEXUS_ENABLE_JINA_READER=1 时作为开发兜底。", raw_dir)
        records: list[CandidateRecord] = []
        raw_refs: list[str] = []
        try:
            for idx, query in enumerate(queries[:5], start=1):
                text, http_status, headers = _get_text_with_meta("https://s.jina.ai/?" + urlencode({"q": query}), headers={"Accept": "text/plain", "User-Agent": "nexus-readonly-search"})
                raw_path = _write_raw_artifact(raw_dir, "jina_reader", f"jina_reader_{idx}", {"method": "GET", "url": "https://s.jina.ai/", "params": {"q": query}}, {"http_status": http_status, "headers": headers, "body": text}, None)
                if raw_path:
                    raw_refs.append(str(raw_path))
                records.extend(_jina_candidates(query, text, raw_path))
            return records, _status(self.source, "ok" if records else "no_results", "online_http", queries, len(records), len(records), issue_type="none" if records else "no_results", reason="Jina Reader SERP 开发兜底真实调用完成。", approved=True, provider="jina_reader", configured=True, attempted=True, evidence_refs=_refs(records), raw_artifact_refs=raw_refs)
        except Exception as exc:
            status = _exception_status(self.source, "online_http", queries, exc, approved=True, provider="jina_reader", configured=True, attempted=True)
            raw_path = _write_error_artifact(raw_dir, "jina_reader", "jina_reader_error", {"queries": queries}, exc)
            status.raw_artifact_refs = [str(raw_path)] if raw_path else []
            return [], status


class LocalSkillAdapter:
    source = "local_skill"

    def search(
        self,
        queries: list[str],
        *,
        project_path: Path,
        repo_scan: dict[str, object],
        online: bool,
        raw_dir: Path | None = None,
    ) -> tuple[list[CandidateRecord], SourceStatus]:
        del repo_scan, online, raw_dir
        roots = [
            Path.home() / ".codex" / "skills",
            Path.home() / ".agents" / "skills",
            project_path / ".codex" / "skills",
            project_path / ".agents" / "skills",
        ]
        records: list[CandidateRecord] = []
        query_text = " ".join(queries).lower()
        for root in roots:
            if not root.exists():
                continue
            for skill in root.glob("**/SKILL.md"):
                text = skill.read_text(encoding="utf-8", errors="ignore")
                if query_text and not any(part and part in text.lower() for part in query_text.split()[:12]):
                    continue
                name = _frontmatter_value(text, "name") or skill.parent.name
                desc = _frontmatter_value(text, "description") or text[:300]
                records.append(
                    CandidateRecord(
                        title=name,
                        summary=desc,
                        source=self.source,
                        url=str(skill),
                        retrieval_mode="local",
                        candidate_type="skill",
                        evidence=[desc],
                        matched_queries=queries[:5],
                        tags=["codex-skill"],
                    )
                )
        return records, _status(self.source, "ok" if records else "no_results", "local", queries, len(records), len(records), issue_type="none" if records else "no_results", reason="本地 Codex/agents skills inventory 检索完成。", approved=True)


class GithubSkillAdapter(GithubRepoAdapter):
    source = "github_skill"

    def search(
        self,
        queries: list[str],
        *,
        project_path: Path,
        repo_scan: dict[str, object],
        online: bool,
        raw_dir: Path | None = None,
    ) -> tuple[list[CandidateRecord], SourceStatus]:
        del project_path, repo_scan
        if not online:
            return [], _approval_required(self.source, queries, "GitHub SKILL.md 代码检索需要 online-search 审批。")
        if not shutil.which("gh"):
            return [], _status(self.source, "auth_required", "online_api", queries, 0, 0, issue_type="provider_unavailable", reason="gh CLI 不可用，无法稳定执行 GitHub code search。", approved=True, next_options=["install_gh", "external_prompt"])
        raw_dir = _ensure_raw_dir(raw_dir, self.source)
        records: list[CandidateRecord] = []
        try:
            for idx, query in enumerate(queries[:4], start=1):
                completed = subprocess.run(
                    ["gh", "search", "code", f"filename:SKILL.md {query}", "--json", "path,repository,url", "--limit", "10"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                raw_path = _write_raw(raw_dir, f"github_skill_{idx}.json", completed.stdout)
                for item in json.loads(completed.stdout or "[]"):
                    repo = item.get("repository") if isinstance(item, dict) else {}
                    repo_name = repo.get("fullName") if isinstance(repo, dict) else ""
                    records.append(
                        CandidateRecord(
                            title=f"{repo_name}/{item.get('path', 'SKILL.md')}",
                            summary="GitHub code search discovered a SKILL.md candidate.",
                            source=self.source,
                            url=str(item.get("url") or ""),
                            retrieval_mode="online_api:gh_code",
                            candidate_type="skill",
                            repo=str(repo_name or ""),
                            matched_queries=[query],
                            raw_artifact_refs=[str(raw_path)] if raw_path else [],
                            tags=["github", "skill"],
                            raw=dict(item),
                        )
                    )
            return records, _status(self.source, "ok" if records else "no_results", "online_api", queries, len(records), len(records), issue_type="none" if records else "no_results", approved=True, evidence_refs=_refs(records))
        except Exception as exc:
            return [], _exception_status(self.source, "online_api", queries, exc, approved=True)


class ExternalPromptAdapter:
    source = "external_prompt"

    def search(
        self,
        queries: list[str],
        *,
        project_path: Path,
        repo_scan: dict[str, object],
        online: bool,
        raw_dir: Path | None = None,
    ) -> tuple[list[CandidateRecord], SourceStatus]:
        del project_path, repo_scan, online, raw_dir
        record = CandidateRecord(
            title="外部 GPT 调研 prompt",
            summary="自动检索 blocked/partial 时，可交给外部 GPT/搜索工具补充调研；该候选不代表真实检索完成。",
            source=self.source,
            url="local://external-gpt-research-prompt",
            retrieval_mode="external_prompt",
            candidate_type="external_prompt",
            evidence=queries[:10],
            matched_queries=queries[:10],
            raw={"queries": queries[:20]},
            risk_flags=["not_a_real_search_result"],
        )
        return [record], _status(self.source, "ok", "external_prompt", queries, 1, 1, reason="已生成外部补充调研候选；不计为真实在线检索完成。", approved=True)


def adapter_for_source(source: str) -> SearchAdapter:
    normalized = source.strip().lower()
    if normalized in {"local", "local_inventory", "local_content"}:
        return LocalAdapter()
    if normalized == "github_repo":
        return GithubRepoAdapter()
    if normalized == "gitee_repo":
        return GiteeRepoAdapter()
    if normalized == "mcp_registry":
        return McpRegistryAdapter()
    if normalized in {"official_docs", "official_cn_docs", "official_en_docs"}:
        return OfficialDocsAdapter()
    if normalized in {"chinese_web", "chinese_tech_blogs"}:
        return ChineseWebAdapter()
    if normalized == "tavily_search":
        return ChineseWebAdapter(["tavily"])
    if normalized == "brave_search":
        return ChineseWebAdapter(["brave"])
    if normalized == "openai_web_search":
        return ChineseWebAdapter(["openai_web_search"])
    if normalized in {"serpapi_baidu", "searchapi_baidu", "searxng", "jina_reader"}:
        return ChineseWebAdapter([normalized])
    if normalized == "local_skill":
        return LocalSkillAdapter()
    if normalized in {"github_skill", "openai_skills"}:
        return GithubSkillAdapter()
    if normalized == "external_prompt":
        return ExternalPromptAdapter()
    return ExternalPromptAdapter()


def _candidate_from_dict(item: dict[str, object]) -> CandidateRecord:
    record = CandidateRecord(
        id=str(item.get("id") or ""),
        title=str(item.get("title") or ""),
        summary=str(item.get("summary") or item.get("description") or ""),
        source=str(item.get("source") or ""),
        url=str(item.get("url") or ""),
        retrieval_mode=str(item.get("retrieval_mode") or "local"),
        evidence=[str(value) for value in item.get("evidence", [])] if isinstance(item.get("evidence"), list) else [],
        raw=dict(item.get("raw") or {}) if isinstance(item.get("raw"), dict) else {},
    )
    for query in record.raw.get("queries", []) if isinstance(record.raw.get("queries"), list) else []:
        if isinstance(query, str):
            record.merge_query(query)
    return record


def _github_candidate(item: dict[str, object], *, query: str, retrieval_mode: str, raw_path: Path | None = None) -> CandidateRecord:
    license_value = item.get("license")
    license_text = ""
    if isinstance(license_value, dict):
        license_text = str(license_value.get("spdx_id") or license_value.get("key") or "")
    full_name = str(item.get("fullName") or item.get("nameWithOwner") or item.get("full_name") or "")
    record = CandidateRecord(
        title=full_name or str(item.get("name") or ""),
        summary=str(item.get("description") or ""),
        source="github_repo",
        url=str(item.get("url") or item.get("html_url") or ""),
        retrieval_mode=retrieval_mode,
        candidate_type="repository",
        license=license_text,
        maintenance_signal=str(item.get("pushedAt") or item.get("pushed_at") or ""),
        stars=int(item.get("stargazersCount") or item.get("stargazers_count") or 0),
        repo=full_name or None,
        tags=["github"],
        raw_artifact_refs=[str(raw_path)] if raw_path else [],
        raw=dict(item),
    )
    record.merge_query(query)
    return record


def _gitee_candidate(item: dict[str, object], *, query: str, raw_path: Path | None = None) -> CandidateRecord:
    full_name = str(item.get("full_name") or item.get("human_name") or item.get("name") or "")
    record = CandidateRecord(
        title=full_name,
        summary=str(item.get("description") or ""),
        source="gitee_repo",
        url=str(item.get("html_url") or item.get("url") or ""),
        retrieval_mode="online_api:gitee",
        candidate_type="repository",
        license=str(item.get("license") or ""),
        maintenance_signal=str(item.get("updated_at") or item.get("pushed_at") or ""),
        stars=int(item.get("stargazers_count") or item.get("stars_count") or 0),
        repo=full_name or None,
        tags=["gitee", "cn"],
        raw_artifact_refs=[str(raw_path)] if raw_path else [],
        raw=dict(item),
    )
    record.merge_query(query)
    return record


def _mcp_candidate(item: dict[str, object], *, query: str, raw_path: Path | None = None) -> CandidateRecord:
    repository = item.get("repository")
    url = ""
    if isinstance(repository, dict):
        url = str(repository.get("url") or "")
    elif repository:
        url = str(repository)
    record = CandidateRecord(
        title=str(item.get("name") or item.get("id") or ""),
        summary=str(item.get("description") or ""),
        source="mcp_registry",
        url=url or str(item.get("homepage") or item.get("url") or McpRegistryAdapter.url),
        retrieval_mode="online_api:mcp_registry",
        candidate_type="mcp_server",
        tags=["mcp", "registry"],
        raw_artifact_refs=[str(raw_path)] if raw_path else [],
        raw=dict(item),
    )
    record.merge_query(query)
    return record


def _web_candidate(item: dict[str, object], *, source: str, query: str, provider: str, raw_path: Path | None = None) -> CandidateRecord:
    extra = item.get("extra_snippets")
    snippets: list[str] = []
    for key in ["description", "content", "snippet"]:
        if item.get(key):
            snippets.append(str(item.get(key)))
    if isinstance(extra, list):
        snippets.extend(str(value) for value in extra if value)
    record = CandidateRecord(
        title=str(item.get("title") or item.get("name") or item.get("url") or ""),
        summary="\n".join(snippets),
        source=source,
        url=str(item.get("url") or item.get("link") or ""),
        retrieval_mode=f"online_api:{provider}",
        candidate_type="article",
        tags=["chinese-web", provider],
        raw_artifact_refs=[str(raw_path)] if raw_path else [],
        raw={"provider": provider, "item": dict(item)},
    )
    record.merge_query(query)
    return record


def _baidu_candidates(provider: str, query: str, payload: dict[str, Any], raw_path: Path | None) -> list[CandidateRecord]:
    records: list[CandidateRecord] = []
    for rank, item in enumerate(payload.get("organic_results", []) if isinstance(payload, dict) else [], start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("link") or item.get("url") or "")
        if not url:
            continue
        record = CandidateRecord(
            title=str(item.get("title") or url),
            summary=str(item.get("snippet") or item.get("displayed_link") or ""),
            source="chinese_web",
            url=url,
            retrieval_mode=f"online_api:{provider}",
            candidate_type="article",
            tags=["chinese-web", "baidu-serp", provider],
            raw_artifact_refs=[str(raw_path)] if raw_path else [],
            raw={
                "provider": provider,
                "rank": item.get("position") or rank,
                "displayed_link": item.get("displayed_link"),
                "cached_page_link": item.get("cached_page_link"),
                "thumbnail": item.get("thumbnail"),
                "item": dict(item),
            },
        )
        record.merge_query(query)
        records.append(record)
    return records


def _jina_candidates(query: str, text: str, raw_path: Path | None) -> list[CandidateRecord]:
    records: list[CandidateRecord] = []
    for idx, match in enumerate(re.finditer(r"https?://[^\s)>\]]+", text), start=1):
        url = match.group(0).rstrip(".,;")
        records.append(
            CandidateRecord(
                title=f"Jina Reader SERP result {idx}",
                summary=text[max(0, match.start() - 160) : match.end() + 260],
                source="chinese_web",
                url=url,
                retrieval_mode="online_http:jina_reader",
                candidate_type="article",
                tags=["chinese-web", "jina-reader"],
                raw_artifact_refs=[str(raw_path)] if raw_path else [],
                raw={"provider": "jina_reader", "rank": idx},
            )
        )
        records[-1].merge_query(query)
        if len(records) >= 5:
            break
    return records


def _records_from_openai_response(payload: dict[str, Any], queries: list[str], raw_path: Path | None) -> list[CandidateRecord]:
    records: list[CandidateRecord] = []
    output = payload.get("output") if isinstance(payload, dict) else []
    text_chunks: list[str] = []
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if isinstance(content, dict):
                if content.get("type") in {"output_text", "text"}:
                    text_chunks.append(str(content.get("text") or ""))
                for ann in content.get("annotations", []) if isinstance(content.get("annotations"), list) else []:
                    if isinstance(ann, dict) and ann.get("url"):
                        records.append(
                            CandidateRecord(
                                title=str(ann.get("title") or ann.get("url")),
                                summary="OpenAI web_search citation",
                                source="chinese_web",
                                url=str(ann.get("url")),
                                retrieval_mode="online_api:openai_web_search",
                                candidate_type="article",
                                matched_queries=queries[:5],
                                raw_artifact_refs=[str(raw_path)] if raw_path else [],
                                raw=ann,
                                tags=["openai-web-search", "citation"],
                            )
                        )
    if not records and text_chunks:
        records.append(
            CandidateRecord(
                title="OpenAI web_search summarized result",
                summary="\n".join(text_chunks)[:1200],
                source="chinese_web",
                url="local://openai-web-search-summary",
                retrieval_mode="online_api:openai_web_search",
                candidate_type="article",
                evidence=text_chunks[:3],
                matched_queries=queries[:5],
                raw_artifact_refs=[str(raw_path)] if raw_path else [],
                tags=["openai-web-search", "summary-only"],
            )
        )
    return records


def _status(
    source: str,
    status: str,
    retrieval_mode: str,
    queries: list[str],
    raw_result_count: int = 0,
    candidate_count: int = 0,
    *,
    issue_type: str = "none",
    fallback_used: bool = False,
    retryable: bool = False,
    auth_required: bool = False,
    auth_present: bool = False,
    approval_required: bool = False,
    approved: bool = False,
    reason: str = "",
    next_options: list[str] | None = None,
    raw_error_summary: str = "",
    evidence_refs: list[str] | None = None,
    raw_artifact_refs: list[str] | None = None,
    http_statuses: list[int] | None = None,
    provider: str = "",
    phase: str = "search",
    configured: bool = False,
    attempted: bool = False,
    retry_after_seconds: int | None = None,
    online_search_blocked: bool = False,
    provider_statuses: list[dict[str, Any]] | None = None,
) -> SourceStatus:
    now = datetime.now(timezone.utc).isoformat()
    return SourceStatus(
        source=source,
        status=status,
        retrieval_mode=retrieval_mode,
        provider=provider or source,
        phase=phase,
        configured=configured,
        attempted=attempted,
        query_count=len(queries),
        raw_result_count=raw_result_count,
        candidate_count=candidate_count,
        issue_type=issue_type,
        fallback_used=fallback_used,
        retryable=retryable,
        auth_required=auth_required,
        auth_present=auth_present,
        approval_required=approval_required,
        approved=approved,
        reason=reason,
        next_options=next_options or [],
        raw_error_summary=raw_error_summary,
        evidence_refs=evidence_refs or [],
        raw_artifact_refs=raw_artifact_refs or [],
        http_statuses=http_statuses or [],
        retry_after_seconds=retry_after_seconds,
        online_search_blocked=online_search_blocked,
        provider_statuses=provider_statuses or [],
        started_at=now,
        ended_at=now,
    )


def _approval_required(source: str, queries: list[str], reason: str) -> SourceStatus:
    return _status(
        source,
        "approval_required",
        "online_api",
        queries,
        issue_type="approval_required",
        reason=reason,
        approval_required=True,
        next_options=["approve_online_search", "external_prompt"],
    )


def _exception_status(
    source: str,
    retrieval_mode: str,
    queries: list[str],
    exc: Exception,
    *,
    approved: bool,
    auth_present: bool = False,
    provider: str = "",
    configured: bool = False,
    attempted: bool = False,
) -> SourceStatus:
    issue = _issue_type(exc)
    status = {
        "unauthorized": "auth_required",
        "auth_missing": "auth_required",
        "quota_exhausted": "quota_exhausted",
        "rate_limit": "rate_limited",
        "forbidden": "forbidden",
        "captcha_or_waf": "captcha_or_waf",
        "timeout": "failed",
    }.get(issue, "failed")
    retry_after = _retry_after_seconds(exc)
    return _status(
        source,
        status,
        retrieval_mode,
        queries,
        issue_type=issue,
        reason=_short_error(exc),
        retryable=issue in {"rate_limit", "timeout", "network"},
        auth_required=issue in {"unauthorized", "auth_missing"},
        auth_present=auth_present,
        approved=approved,
        raw_error_summary=_short_error(exc),
        next_options=_next_options_for_issue(issue),
        http_statuses=[exc.code] if isinstance(exc, HTTPError) else [],
        retry_after_seconds=retry_after,
        provider=provider or source,
        configured=configured,
        attempted=attempted,
    )


def _get_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    request = Request(url, headers=headers or {}, method="GET")
    with urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _get_json_with_meta(url: str, *, headers: dict[str, str] | None = None) -> tuple[Any, int, dict[str, str]]:
    request = Request(url, headers=headers or {}, method="GET")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8") or "{}"), int(response.status), _allowlist_headers(dict(response.headers))


def _get_text(url: str, *, headers: dict[str, str] | None = None, limit: int = 50000) -> str:
    request = Request(url, headers=headers or {}, method="GET")
    with urlopen(request, timeout=20) as response:
        return response.read(limit).decode("utf-8", errors="ignore")


def _get_text_with_meta(url: str, *, headers: dict[str, str] | None = None, limit: int = 50000) -> tuple[str, int, dict[str, str]]:
    request = Request(url, headers=headers or {}, method="GET")
    with urlopen(request, timeout=30) as response:
        return response.read(limit).decode("utf-8", errors="ignore"), int(response.status), _allowlist_headers(dict(response.headers))


def _post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> Any:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers or {}, method="POST")
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _post_json_with_meta(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> tuple[Any, int, dict[str, str]]:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers or {}, method="POST")
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8") or "{}"), int(response.status), _allowlist_headers(dict(response.headers))


def _write_raw(raw_dir: Path | None, filename: str, text: str) -> Path | None:
    if raw_dir is None:
        return None
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / filename
    path.write_text(text, encoding="utf-8")
    return path


def _write_raw_artifact(
    raw_dir: Path | None,
    provider: str,
    query_id: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any] | None,
    error_payload: dict[str, Any] | None,
) -> Path | None:
    if raw_dir is None:
        return None
    raw_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": provider,
        "adapter_version": "chinese_web_non_openai_v1",
        "request": request_payload,
        "response": response_payload,
        "error": error_payload,
    }
    body = _redact_secrets(payload)
    path = raw_dir / f"{_safe_name(query_id)}.raw.json"
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_error_artifact(raw_dir: Path | None, provider: str, query_id: str, request_payload: dict[str, Any], exc: Exception) -> Path | None:
    return _write_raw_artifact(
        raw_dir,
        provider,
        query_id,
        request_payload,
        None,
        {
            "error_type": _issue_type(exc),
            "error_message": _short_error(exc),
            "http_status": exc.code if isinstance(exc, HTTPError) else None,
        },
    )


def _ensure_raw_dir(raw_dir: Path | None, source: str) -> Path | None:
    if raw_dir is None:
        return None
    path = raw_dir / source
    path.mkdir(parents=True, exist_ok=True)
    return path


def _secret_from_env_or_file(env_name: str, file_env_name: str) -> str:
    value = os.environ.get(env_name)
    if value:
        return value.strip()
    file_value = os.environ.get(file_env_name)
    if not file_value:
        return ""
    path = Path(file_value).expanduser()
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def resolve_chinese_web_providers() -> list[str]:
    configured = os.environ.get("NEXUS_CHINESE_WEB_PROVIDERS")
    if configured:
        providers = [item.strip().lower() for item in configured.split(",") if item.strip()]
    else:
        providers = list(DEFAULT_NON_OPENAI_CHINESE_WEB_PROVIDERS)
    if os.environ.get("NEXUS_ENABLE_BAIDU_SERP") != "1":
        providers = [provider for provider in providers if provider not in BAIDU_SERP_PROVIDERS]
    if os.environ.get("NEXUS_ENABLE_SEARXNG") != "1":
        providers = [provider for provider in providers if provider != "searxng"]
    if os.environ.get("NEXUS_ENABLE_JINA_READER") != "1":
        providers = [provider for provider in providers if provider != "jina_reader"]
    if os.environ.get("NEXUS_ENABLE_OPENAI_WEB_SEARCH") != "1":
        providers = [provider for provider in providers if provider not in {"openai", "openai_web_search"}]
    normalized: list[str] = []
    for provider in providers:
        provider = "openai_web_search" if provider == "openai" else provider
        if provider and provider not in normalized:
            normalized.append(provider)
    return normalized or list(DEFAULT_NON_OPENAI_CHINESE_WEB_PROVIDERS)


def _aggregate_chinese_web_status(queries: list[str], statuses: list[SourceStatus], records: list[CandidateRecord]) -> SourceStatus:
    payloads = [status.to_dict() for status in statuses]
    raw_refs: list[str] = []
    http_statuses: list[int] = []
    for status in statuses:
        raw_refs.extend(status.raw_artifact_refs)
        http_statuses.extend(status.http_statuses)
    if not statuses:
        aggregate_status = "auth_required"
        issue = "auth_missing"
        reason = "没有可用中文互联网 provider；不会伪造结果。"
    elif records:
        aggregate_status = "ok" if any(status.status == "ok" for status in statuses) else "partial"
        issue = "none" if aggregate_status == "ok" else "partial"
        reason = "中文互联网真实 provider 调用完成。"
    elif all(status.status in {"auth_required", "disabled"} for status in statuses):
        aggregate_status = "auth_required"
        issue = "auth_missing"
        reason = "所有中文互联网 provider 都缺少配置或被禁用；不会伪造结果。"
    elif any(status.status in {"rate_limited", "forbidden", "quota_exhausted", "failed", "captcha_or_waf"} for status in statuses):
        aggregate_status = "partial"
        issue = "provider_unavailable"
        reason = "中文互联网 provider 已真实尝试，但没有得到 URL-bearing 候选。"
    else:
        aggregate_status = "no_results"
        issue = "no_results"
        reason = "中文互联网 provider 真实请求完成但没有 URL-bearing 结果。"
    return _status(
        "chinese_web",
        aggregate_status,
        "online_api",
        queries,
        raw_result_count=sum(status.raw_result_count for status in statuses),
        candidate_count=len(records),
        issue_type=issue,
        auth_required=aggregate_status == "auth_required",
        approved=True,
        provider="chinese_web",
        configured=any(status.configured for status in statuses),
        attempted=any(status.attempted for status in statuses),
        reason=reason,
        next_options=_aggregate_next_options(statuses),
        raw_artifact_refs=raw_refs,
        http_statuses=http_statuses,
        online_search_blocked=aggregate_status == "auth_required",
        provider_statuses=payloads,
        evidence_refs=_refs(records),
    )


def _aggregate_next_options(statuses: list[SourceStatus]) -> list[str]:
    options: list[str] = []
    for status in statuses:
        for option in status.next_options:
            if option not in options:
                options.append(option)
    if not options:
        options = ["external_prompt", "manual_check"]
    return options


def _missing_key_status(source: str, provider: str, queries: list[str], env_var: str, raw_dir: Path | None) -> tuple[list[CandidateRecord], SourceStatus]:
    status = _status(
        source,
        "auth_required",
        "online_api",
        queries,
        issue_type="auth_missing",
        reason=f"缺少 {env_var}；{provider} 未发起请求，nexus 不会伪造中文互联网检索结果。",
        approved=True,
        auth_required=True,
        provider=provider,
        configured=False,
        attempted=False,
        next_options=[f"set_{env_var}", "try_next_provider", "external_prompt"],
        online_search_blocked=True,
    )
    raw_path = _write_raw_artifact(
        raw_dir,
        provider,
        f"{provider}_auth_required",
        {"provider": provider, "queries": queries, "required_env": env_var, "attempted": False},
        None,
        {"status": "auth_required", "reason": status.reason},
    )
    status.raw_artifact_refs = [str(raw_path)] if raw_path else []
    return [], status


def _disabled_status(source: str, provider: str, queries: list[str], reason: str, raw_dir: Path | None) -> tuple[list[CandidateRecord], SourceStatus]:
    status = _status(
        source,
        "disabled",
        "online_api",
        queries,
        issue_type="disabled",
        reason=reason,
        approved=True,
        provider=provider,
        configured=False,
        attempted=False,
        next_options=["enable_provider", "try_next_provider"],
    )
    raw_path = _write_raw_artifact(raw_dir, provider, f"{provider}_disabled", {"provider": provider, "queries": queries, "attempted": False}, None, {"status": "disabled", "reason": reason})
    status.raw_artifact_refs = [str(raw_path)] if raw_path else []
    return [], status


def _dedupe_records(records: list[CandidateRecord]) -> list[CandidateRecord]:
    seen: dict[str, CandidateRecord] = {}
    for record in records:
        if not record.url or not record.url.startswith(("http://", "https://")):
            continue
        key = _normalize_url_for_dedupe(record.url)
        if key in seen:
            existing = seen[key]
            for query in record.matched_queries:
                existing.merge_query(query)
            for ref in record.raw_artifact_refs:
                if ref not in existing.raw_artifact_refs:
                    existing.raw_artifact_refs.append(ref)
            continue
        seen[key] = record
    return list(seen.values())


def _normalize_url_for_dedupe(url: str) -> str:
    parsed = urlparse(url)
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if not key.lower().startswith("utm_")])
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", query, ""))


def _allowlist_headers(headers: dict[str, str]) -> dict[str, str]:
    allowed = {"content-type", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"}
    return {key.lower(): value for key, value in headers.items() if key.lower() in allowed}


def _redact_secrets(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for item_key, item_value in value.items():
            lowered = str(item_key).lower()
            if lowered in SECRET_KEYS:
                redacted[item_key] = "[REDACTED]"
            else:
                redacted[item_key] = _redact_secrets(item_value, lowered)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item, key) for item in value]
    if isinstance(value, str):
        text = value
        for secret in _configured_secret_values():
            text = text.replace(secret, "[REDACTED]")
        if key in SECRET_KEYS:
            return "[REDACTED]"
        if "api_key=" in text:
            parsed = urlparse(text)
            if parsed.query:
                query = urlencode([(item_key, "[REDACTED]" if item_key.lower() == "api_key" else item_value) for item_key, item_value in parse_qsl(parsed.query, keep_blank_values=True)])
                text = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))
        return text
    return value


def _configured_secret_values() -> list[str]:
    values: list[str] = []
    for env_name in SECRET_ENV_NAMES:
        secret = os.environ.get(env_name)
        if secret:
            values.append(secret)
    for env_name in ["NEXUS_TAVILY_KEY_FILE", "NEXUS_BRAVE_KEY_FILE", "NEXUS_SERPAPI_KEY_FILE", "NEXUS_SEARCHAPI_KEY_FILE"]:
        path_value = os.environ.get(env_name)
        if not path_value:
            continue
        try:
            path = Path(path_value).expanduser()
            if path.is_file():
                secret = path.read_text(encoding="utf-8").strip()
                if secret:
                    values.append(secret)
        except OSError:
            continue
    return values


def _extract_domains(values: list[str]) -> list[str]:
    domains: list[str] = []
    for value in values:
        tokens = value.replace(",", " ").split()
        for token in tokens:
            token = token.strip().strip("()[]{}<>\"'`，。；;")
            if not token:
                continue
            if "://" not in token and not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/[^\s]*)?", token):
                continue
            try:
                parsed = urlparse(token if "://" in token else f"https://{token}")
            except ValueError:
                continue
            host = parsed.netloc
            if re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", host) and not any(part in host for part in ["github.com", "gitee.com"]):
                domains.append(host.rstrip("/"))
    return list(dict.fromkeys(domains))


def _frontmatter_value(text: str, key: str) -> str:
    if not text.startswith("---"):
        return ""
    for line in text.splitlines()[1:30]:
        if line.strip() == "---":
            break
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip().strip('"')
    return ""


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)[:80]


def _refs(records: list[CandidateRecord]) -> list[str]:
    refs: list[str] = []
    for record in records:
        refs.extend(record.raw_artifact_refs)
        if record.url:
            refs.append(record.url)
    return refs[:20]


def _short_error(exc: Exception, limit: int = 300) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP Error {exc.code}: {exc.reason}"
    if isinstance(exc, URLError):
        return f"URL Error: {exc.reason}"
    text = " ".join(str(exc).split())
    return text[:limit]


def _issue_type(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        if exc.code == 401:
            return "unauthorized"
        if exc.code == 402:
            return "quota_exhausted"
        if exc.code == 403:
            return "forbidden"
        if exc.code == 429:
            return "rate_limit"
        return f"http_{exc.code}"
    text = str(exc).lower()
    if "captcha" in text or "waf" in text or "cloudflare" in text:
        return "captcha_or_waf"
    if "403" in text or "forbidden" in text:
        return "forbidden"
    if "401" in text or "unauthorized" in text or "auth" in text or "login" in text:
        return "unauthorized"
    if "402" in text or "quota" in text or "credit" in text or "balance" in text:
        return "quota_exhausted"
    if "rate" in text or "limit" in text or "429" in text:
        return "rate_limit"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "dns" in text:
        return "dns_error"
    return "network"


def _next_options_for_issue(issue: str) -> list[str]:
    if issue in {"unauthorized", "auth_missing"}:
        return ["set_api_token", "external_prompt"]
    if issue == "quota_exhausted":
        return ["check_billing_or_quota", "try_next_provider", "external_prompt"]
    if issue == "rate_limit":
        return ["wait_and_retry", "set_token", "external_prompt"]
    if issue in {"forbidden", "captcha_or_waf"}:
        return ["do_not_bypass", "external_prompt", "manual_check"]
    return ["retry", "external_prompt"]


def _retry_after_seconds(exc: Exception) -> int | None:
    if not isinstance(exc, HTTPError):
        return None
    value = exc.headers.get("Retry-After") if exc.headers else None
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None
