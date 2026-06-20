from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import shutil
import signal
import subprocess
import time
from tempfile import TemporaryDirectory
from typing import Callable


DEVICE_CODE_RE = re.compile(r"(?:one-time code|One-time code|code).*?([A-Z0-9]{4}-[A-Z0-9]{4})", re.IGNORECASE)
DEVICE_URL_RE = re.compile(r"(https://github\.com/login/device)")
AUTH_DIR_REL = Path(".github/nexus-auth")
GH_LOGIN_WAIT_AFTER_CODE_SECONDS = 180


def run_github_auth_login(project: Path, *, browser_mode: str = "native", echo: bool = False) -> dict[str, object]:
    auth_dir = project / AUTH_DIR_REL
    auth_dir.mkdir(parents=True, exist_ok=True)
    request_path = auth_dir / "github_auth_request.json"
    status_path = auth_dir / "github_auth_status.md"
    log_path = auth_dir / "browser_auth_run_log.md"
    host_path = auth_dir / "host_capability_request.json"
    _write_json(request_path, _auth_request_payload(project, browser_mode))
    _write_json(host_path, _host_request_payload(project, browser_mode))

    if shutil.which("gh") is None:
        status = _status_payload("GH_NOT_FOUND", project, browser_mode, "GitHub CLI (`gh`) is not installed or not on PATH.", "", "")
        _write_status(status_path, status)
        _append_log(log_path, "gh_not_found")
        return {**status, "request_path": str(request_path), "status_path": str(status_path), "log_path": str(log_path), "host_capability_request_path": str(host_path)}

    before = check_auth_status()
    if before["authenticated"]:
        status = _status_payload("AUTH_VERIFIED", project, browser_mode, "GitHub CLI is already authenticated.", "", "")
        _write_status(status_path, status)
        _append_log(log_path, "already_authenticated")
        return {**status, "request_path": str(request_path), "status_path": str(status_path), "log_path": str(log_path), "host_capability_request_path": str(host_path)}

    login = _run_gh_auth_login(lambda device_url, user_code: _open_temp_browser_or_record(log_path, device_url, user_code, browser_mode), echo=echo, open_native_browser=browser_mode == "native")
    after = check_auth_status()
    authenticated = bool(after["authenticated"])
    device_url = login.get("device_url") or "https://github.com/login/device"
    user_code = str(login.get("user_code") or "")
    if authenticated:
        status = _status_payload("AUTH_VERIFIED", project, browser_mode, "GitHub auth verified; retrying sync is safe.", "", "")
    else:
        instruction = "Complete GitHub password, 2FA, CAPTCHA, account confirmation, and authorization, then rerun the same sync command."
        if user_code:
            instruction = f"Open {device_url} and enter code {user_code}; then complete GitHub password/2FA/authorization and rerun the same sync command."
        status = _status_payload("LOGIN_INCOMPLETE", project, browser_mode, instruction, device_url, user_code)
        status["safe_error"] = str(login.get("output") or "")
    _write_status(status_path, status)
    _append_log(log_path, f"gh_auth_login_complete state={status['state']} code_hash={_stable_hash(user_code)}")
    return {**status, "request_path": str(request_path), "status_path": str(status_path), "log_path": str(log_path), "host_capability_request_path": str(host_path)}


def check_auth_status() -> dict[str, object]:
    command = ["gh", "auth", "status", "--json", "hosts", "--hostname", "github.com"]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    hosts: object = []
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
            hosts = payload.get("hosts", [])
        except json.JSONDecodeError:
            hosts = []
    authenticated = completed.returncode == 0 and _hosts_indicate_authenticated(hosts) and not _looks_not_authenticated(output)
    if not authenticated:
        fallback = subprocess.run(["gh", "auth", "status", "--hostname", "github.com"], check=False, capture_output=True, text=True)
        fallback_output = "\n".join(part for part in [fallback.stdout, fallback.stderr] if part).strip()
        if fallback.returncode == 0 and "Logged in to github.com account" in fallback_output and "token in keyring is invalid" not in fallback_output:
            return {"authenticated": True, "state": "authenticated", "output": _redact_status_output(fallback_output), "fallback": "plain_status"}
        output = "\n".join(part for part in [output, _redact_status_output(fallback_output)] if part).strip()
    return {"authenticated": authenticated, "state": "authenticated" if authenticated else "not_authenticated", "output": _redact_status_output(output)}


def _run_gh_auth_login(device_callback: Callable[[str, str], None], *, echo: bool, open_native_browser: bool = False) -> dict[str, object]:
    command = ["gh", "auth", "login", "--web", "--clipboard", "--skip-ssh-key", "--git-protocol", "https", "--hostname", "github.com"]
    output_parts: list[str] = []
    callback_sent = False
    returncode: int | None = None
    timed_out = False
    try:
        process = subprocess.Popen(command, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
        if process.stdout:
            for line in process.stdout:
                output_parts.append(line.rstrip("\n"))
                if echo:
                    print(line, end="", flush=True)
                output = "\n".join(output_parts)
                code = _extract_device_code(output)
                url = _extract_device_url(output) or "https://github.com/login/device"
                if code and not callback_sent:
                    callback_sent = True
                    device_callback(url, code)
                    if open_native_browser and process.stdin:
                        process.stdin.write("\n")
                        process.stdin.flush()
                    deadline = time.monotonic() + GH_LOGIN_WAIT_AFTER_CODE_SECONDS
                    while process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.25)
                    if process.poll() is None:
                        timed_out = True
                        process.send_signal(signal.SIGTERM)
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                        break
        if returncode is None:
            returncode = process.wait(timeout=2) if process.poll() is not None else process.returncode
    except FileNotFoundError:
        return {"exit_code": 127, "output": "`gh` is not installed or not on PATH.", "device_url": "", "user_code": ""}
    except subprocess.TimeoutExpired:
        returncode = process.returncode
    output = "\n".join(output_parts).strip()
    code = _extract_device_code(output)
    return {"exit_code": returncode, "output": _redact_code(output, code), "device_url": _extract_device_url(output), "user_code": code, "timed_out_after_device_code": timed_out}


_ACTIVE_BROWSER_SESSIONS: list[object] = []


def _open_temp_browser_or_record(log_path: Path, verification_url: str, user_code: str, browser_mode: str) -> None:
    if browser_mode != "cdp":
        _append_log(log_path, f"manual_device_code_ready code_hash={_stable_hash(user_code)} url={verification_url}")
        return
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError:
        _append_log(log_path, f"playwright_unavailable_manual_required code_hash={_stable_hash(user_code)} url={verification_url}")
        return
    try:
        temp_profile = TemporaryDirectory(prefix="nexus-gh-auth-")
        playwright = sync_playwright().start()
        context = playwright.chromium.launch_persistent_context(temp_profile.name, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(verification_url, wait_until="domcontentloaded", timeout=900_000)
        page.keyboard.type(user_code)
        page.keyboard.press("Enter")
        _ACTIVE_BROWSER_SESSIONS.extend([temp_profile, playwright, context])
        _append_log(log_path, f"temporary_browser_code_filled code_hash={_stable_hash(user_code)}")
    except Exception as exc:  # pragma: no cover - depends on local browser stack
        _append_log(log_path, f"temporary_browser_failed manual_required code_hash={_stable_hash(user_code)} error={type(exc).__name__}")


def _auth_request_payload(project: Path, browser_mode: str) -> dict[str, object]:
    return {
        "schema": "nexus.github_auth_request.v1",
        "project_path": str(project),
        "requested_browser": browser_mode,
        "allowed_commands": [
            "gh auth status --json hosts --hostname github.com",
            "gh auth login --web --clipboard --skip-ssh-key --git-protocol https --hostname github.com",
        ],
        "forbidden": ["gh auth token", "gh auth status --show-token", "read token/cookie/browser profile/ssh key/.env"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _host_request_payload(project: Path, browser_mode: str) -> dict[str, object]:
    return {
        "schema": "nexus.host_capability_request.v1",
        "capability_bundle": "github_auth_for_sync_bundle",
        "project_path": str(project),
        "requested_browser": browser_mode,
        "expected_escalation": "Run gh auth login and open GitHub CLI's browser flow for github.com/login/device.",
        "forbidden": ["token reads", "cookie reads", "browser profile reads", "SSH key reads", "password file reads", "password/2FA/CAPTCHA automation"],
    }


def _status_payload(state: str, project: Path, browser_mode: str, instruction: str, device_url: str, user_code: str) -> dict[str, object]:
    return {
        "schema": "nexus.github_auth_status.v1",
        "state": state,
        "project_path": str(project),
        "requested_browser": browser_mode,
        "device_url": device_url,
        "device_code": user_code,
        "device_code_hash": _stable_hash(user_code),
        "current_instruction": instruction,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_status(path: Path, status: dict[str, object]) -> None:
    path.write_text(
        "# GitHub Auth Status\n\n"
        f"state: {status.get('state')}\n"
        f"project_path: {status.get('project_path')}\n"
        f"requested_browser: {status.get('requested_browser')}\n"
        f"device_url: {status.get('device_url')}\n"
        f"device_code: {status.get('device_code')}\n"
        f"device_code_hash: {status.get('device_code_hash')}\n\n"
        "## Current instruction\n\n"
        f"{status.get('current_instruction')}\n",
        encoding="utf-8",
    )


def _append_log(path: Path, event: str) -> None:
    if not path.exists():
        path.write_text("# Browser Auth Run Log\n\nprofile_mode: temporary\nattach_existing_browser: false\nredaction: strict\n\n## Events\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"- {datetime.now(timezone.utc).isoformat()} {event}\n")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _extract_device_code(output: str) -> str:
    match = DEVICE_CODE_RE.search(output)
    return match.group(1) if match else ""


def _extract_device_url(output: str) -> str:
    match = DEVICE_URL_RE.search(output)
    return match.group(1) if match else ""


def _redact_code(output: str, code: str) -> str:
    return output.replace(code, "<device-code-redacted>") if code else output


def _redact_status_output(output: str) -> str:
    return re.sub(r"(Token:\s+)[^\n]+", r"\1<redacted-by-nexus>", output)


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else ""


def _looks_not_authenticated(output: str) -> bool:
    lowered = output.lower()
    return "not logged in" in lowered or "to log in, run: gh auth login" in lowered or "requires authentication" in lowered


def _hosts_indicate_authenticated(hosts: object) -> bool:
    if isinstance(hosts, dict):
        flattened: list[object] = []
        for value in hosts.values():
            if isinstance(value, list):
                flattened.extend(value)
            else:
                flattened.append(value)
        hosts = flattened
    if isinstance(hosts, list):
        return any(isinstance(item, dict) and str(item.get("state") or item.get("status") or "").lower() in {"success", "authenticated", "loggedin", "logged_in"} for item in hosts)
    return False
