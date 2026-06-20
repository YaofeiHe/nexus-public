from __future__ import annotations

from datetime import datetime, timezone
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from nexus.tools.search_models import CandidateRecord


CAPTCHA_WAF_PATTERNS = [
    "captcha",
    "verify you are human",
    "cloudflare",
    "access denied",
    "attention required",
    "waf",
    "安全验证",
    "人机验证",
    "请输入验证码",
    "访问过于频繁",
    "请完成验证",
    "访问验证",
]


def check_candidates(candidates: list[CandidateRecord], *, limit: int = 20) -> list[CandidateRecord]:
    checked: list[CandidateRecord] = []
    for candidate in candidates:
        updated = candidate
        if len(checked) < limit:
            updated.availability = check_url(candidate.url)
        else:
            updated.availability = {"status": "skipped", "reason": "availability check limit reached"}
        checked.append(updated)
    return checked


def check_url(url: str, *, timeout: float = 8.0) -> dict[str, object]:
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {
            "status": "skipped",
            "issue_type": "unsupported_url",
            "reason": "Only http(s) URLs are checked.",
            "url": url,
            "started_at": started_at,
            "ended_at": datetime.now(timezone.utc).isoformat(),
        }
    payload: dict[str, object] = {
        "status": "unknown",
        "issue_type": "none",
        "url": url,
        "host": parsed.netloc,
        "started_at": started_at,
        "http_statuses": [],
        "redirected_url": "",
        "captcha_or_waf_signal": False,
        "login_signal": False,
    }
    try:
        socket.gethostbyname(parsed.hostname or parsed.netloc)
        payload["dns"] = "ok"
    except OSError as exc:
        payload.update({"status": "failed", "issue_type": "dns_error", "reason": _short(str(exc)), "dns": "failed"})
        payload["ended_at"] = datetime.now(timezone.utc).isoformat()
        payload["latency_ms"] = int((time.monotonic() - started) * 1000)
        return payload

    status, body, final_url, error = _request("HEAD", url, timeout=timeout)
    if status in {405, 403, 404, 0}:
        status, body, final_url, error = _request("GET", url, timeout=timeout)
    if status:
        payload["http_statuses"] = [status]
        payload["redirected_url"] = final_url
    text = body.lower()
    payload["captcha_or_waf_signal"] = any(token in text for token in CAPTCHA_WAF_PATTERNS)
    payload["login_signal"] = any(token in text for token in ["login", "sign in", "登录", "请先登录"])
    if error:
        issue = _error_issue(error)
        payload.update({"status": "blocked" if issue in {"forbidden", "captcha_or_waf"} else "failed", "issue_type": issue, "reason": _short(error)})
    elif 200 <= status < 400:
        payload.update({"status": "ok", "issue_type": "none", "reason": "URL is reachable with readonly request."})
    elif status in {401}:
        payload.update({"status": "auth_required", "issue_type": "unauthorized", "reason": "Readonly request requires authentication."})
    elif status in {403}:
        payload.update({"status": "blocked", "issue_type": "forbidden", "reason": "Readonly request was forbidden."})
    elif status in {429}:
        payload.update({"status": "rate_limited", "issue_type": "rate_limit", "reason": "Readonly request was rate limited."})
    else:
        payload.update({"status": "partial", "issue_type": "http_status", "reason": f"Readonly request returned HTTP {status}."})
    if payload["captcha_or_waf_signal"]:
        payload["status"] = "captcha_or_waf"
        payload["issue_type"] = "captcha_or_waf"
        payload["reason"] = "Page appears to require CAPTCHA/WAF verification; nexus will not bypass it."
    if payload["login_signal"] and payload["status"] == "ok":
        payload["status"] = "partial"
        payload["issue_type"] = "login_signal"
        payload["reason"] = "Page is reachable but appears to include login signals."
    payload["ended_at"] = datetime.now(timezone.utc).isoformat()
    payload["latency_ms"] = int((time.monotonic() - started) * 1000)
    return payload


def _request(method: str, url: str, *, timeout: float) -> tuple[int, str, str, str]:
    headers = {
        "User-Agent": "nexus-readonly-availability-check",
        "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.1",
        "Range": "bytes=0-4095",
    }
    try:
        request = Request(url, method=method, headers=headers)
        with urlopen(request, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", errors="ignore")
            return int(response.status), body, response.geturl(), ""
    except HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="ignore")
        return int(exc.code), body, exc.geturl(), ""
    except URLError as exc:
        return 0, "", url, str(exc.reason)
    except Exception as exc:
        return 0, "", url, str(exc)


def _error_issue(error: str) -> str:
    lowered = error.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "ssl" in lowered or "tls" in lowered:
        return "tls_error"
    if "403" in lowered:
        return "forbidden"
    return "network"


def _short(value: str, limit: int = 300) -> str:
    return " ".join(value.split())[:limit]
