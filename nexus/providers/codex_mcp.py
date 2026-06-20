from __future__ import annotations

import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import time
from typing import Any

from .base import ModelRequest, ModelResponse, ProviderExecutionError, ProviderStatus, ProviderUnavailable
from .codex_cli import _render_prompt, _short


class CodexMcpProvider:
    name = "codex-mcp"

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        timeout_seconds: int = 300,
        model: str = "",
        status_timeout_seconds: int | None = None,
        smoke_timeout_seconds: int | None = None,
        startup_timeout_seconds: int | None = None,
    ) -> None:
        self.cwd = cwd or Path.cwd()
        self.timeout_seconds = timeout_seconds
        self.status_timeout_seconds = status_timeout_seconds or _int_env("NEXUS_CODEX_MCP_STATUS_TIMEOUT", 5)
        self.smoke_timeout_seconds = smoke_timeout_seconds or _int_env("NEXUS_CODEX_MCP_SMOKE_TIMEOUT", 8)
        self.startup_timeout_seconds = startup_timeout_seconds or _int_env("NEXUS_CODEX_MCP_STARTUP_TIMEOUT", 5)
        self.heartbeat_interval_seconds = _int_env("NEXUS_CODEX_MCP_HEARTBEAT_INTERVAL", 1)
        self.model = model
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._thread_id = ""
        self._codex_command = ""
        self._tools: set[str] = set()
        self.last_heartbeat: dict[str, Any] = {}

    def status(self) -> ProviderStatus:
        codex = shutil.which("codex")
        if not codex:
            return ProviderStatus(self.name, "unavailable", "未找到 codex 命令，无法启动 codex mcp-server。")
        try:
            probe = subprocess.run(
                [codex, "mcp-server", "--help"],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.status_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            self.last_heartbeat = {
                "stage": "status",
                "status": "timeout",
                "timeout_seconds": self.status_timeout_seconds,
                "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            }
            return ProviderStatus(
                self.name,
                "needs_config",
                f"codex mcp-server status probe timed out after {self.status_timeout_seconds}s; set NEXUS_AUTO_SKIP_PROVIDERS=codex-mcp to skip this standby provider when another real provider is available.",
                command=codex,
            )
        if probe.returncode != 0:
            return ProviderStatus(self.name, "unavailable", "codex mcp-server 不可用。", command=codex)
        return ProviderStatus(self.name, "available", "已发现 codex mcp-server；nexus 将通过 stdio MCP tools/call 真实调用 Codex。", command=codex)

    def smoke_status(self) -> ProviderStatus:
        status = self.status()
        if status.status != "available":
            return status
        try:
            request = ModelRequest(
                node_id="codex_mcp_smoke",
                purpose="验证 Codex MCP 能返回结构化 JSON。",
                prompt='Return exactly this JSON and nothing else: {"ok": true, "provider": "codex-mcp"}.',
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["ok", "provider"],
                    "properties": {"ok": {"type": "boolean"}, "provider": {"type": "string"}},
                },
            )
            content = self._call_codex(_render_prompt(request), timeout=self.smoke_timeout_seconds, startup_timeout=self.startup_timeout_seconds)
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = _json_from_content(content)
        except Exception as exc:
            self.close()
            detail = _short(str(exc))
            heartbeat = json.dumps(self.last_heartbeat, ensure_ascii=False, sort_keys=True) if self.last_heartbeat else "{}"
            return ProviderStatus(self.name, "needs_config", f"codex mcp-server smoke test failed within bounded timeout: {detail}; heartbeat={_short(heartbeat)}", command=status.command)
        if isinstance(data, dict) and data.get("ok") is True:
            return ProviderStatus(self.name, "available", "codex mcp-server smoke test passed; real Codex MCP model calls are available.", command=status.command)
        return ProviderStatus(self.name, "needs_config", "codex mcp-server smoke test returned unexpected JSON.", command=status.command)

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        status = self.status()
        if status.status != "available":
            raise ProviderUnavailable(status.reason)
        content = self._call_codex(_render_prompt(request), timeout=self.timeout_seconds)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = _json_from_content(content)
        if not isinstance(data, dict):
            repair = self._call_codex("请把上一条回答修复为严格 JSON 对象，只输出 JSON，不要 Markdown。", timeout=self.timeout_seconds)
            try:
                data = json.loads(repair)
                content = repair
            except json.JSONDecodeError as exc:
                raise ProviderExecutionError(f"codex mcp did not return JSON: {_short(content)}") from exc
        return ModelResponse(
            provider=self.name,
            raw_text=content,
            json_data=data,
            diagnostics={
                "mcp_tool": "codex-reply" if self._thread_id else "codex",
                "thread_id": self._thread_id,
                "session_reuse": bool(self._thread_id),
                "heartbeat": self.last_heartbeat,
            },
        )

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def _ensure_started(self, *, startup_timeout: int | None = None) -> None:
        if self._proc and self._proc.poll() is None:
            return
        codex = shutil.which("codex")
        if not codex:
            raise ProviderUnavailable("未找到 codex 命令。")
        self._codex_command = codex
        self._proc = subprocess.Popen(
            [codex, "mcp-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        result = self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nexus", "version": "0"},
            },
            timeout=startup_timeout or self.startup_timeout_seconds,
        )
        if "result" not in result:
            raise ProviderUnavailable(f"codex mcp initialize failed: {_short(json.dumps(result, ensure_ascii=False))}")
        self._notify("notifications/initialized", {})
        tools = self._request("tools/list", {}, timeout=startup_timeout or self.startup_timeout_seconds)
        raw_tools = tools.get("result", {}).get("tools", []) if isinstance(tools.get("result"), dict) else []
        self._tools = {str(tool.get("name")) for tool in raw_tools if isinstance(tool, dict)}
        if "codex" not in self._tools:
            raise ProviderUnavailable("codex mcp-server 未暴露 codex tool。")

    def _call_codex(self, prompt: str, *, timeout: int, startup_timeout: int | None = None) -> str:
        self._ensure_started(startup_timeout=startup_timeout)
        if self._thread_id and "codex-reply" in self._tools:
            name = "codex-reply"
            arguments = {"threadId": self._thread_id, "prompt": prompt}
        else:
            name = "codex"
            arguments: dict[str, Any] = {"prompt": prompt, "sandbox": "read-only", "cwd": str(self.cwd)}
            if self.model:
                arguments["model"] = self.model
        response = self._request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        if "error" in response:
            raise ProviderExecutionError(_short(json.dumps(response["error"], ensure_ascii=False)))
        result = response.get("result")
        if not isinstance(result, dict):
            raise ProviderExecutionError(f"codex mcp returned invalid result: {_short(json.dumps(response, ensure_ascii=False))}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            self._thread_id = str(structured.get("threadId") or self._thread_id)
            content = structured.get("content")
            if isinstance(content, str) and content.strip():
                return content
        content_items = result.get("content", [])
        if isinstance(content_items, list):
            text = "\n".join(str(item.get("text", "")) for item in content_items if isinstance(item, dict))
            if text.strip():
                return text
        raise ProviderExecutionError(f"codex mcp returned no text content: {_short(json.dumps(response, ensure_ascii=False))}")

    def _request(self, method: str, params: dict[str, Any], *, timeout: int) -> dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        return self._read_response(msg_id, timeout=timeout)

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ProviderUnavailable("codex mcp process is not running.")
        self._proc.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._proc.stdin.flush()

    def _read_response(self, msg_id: int, *, timeout: int) -> dict[str, Any]:
        if self._proc is None or self._proc.stdout is None or self._proc.stderr is None:
            raise ProviderUnavailable("codex mcp process is not running.")
        deadline = time.time() + timeout
        stderr_tail: list[str] = []
        heartbeat_count = 0
        last_heartbeat = time.time()
        started_at = time.time()
        self.last_heartbeat = {
            "stage": "request",
            "msg_id": msg_id,
            "timeout_seconds": timeout,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "heartbeats": heartbeat_count,
            "stderr_tail": [],
        }
        while time.time() < deadline:
            if self._proc.poll() is not None:
                stderr = self._proc.stderr.read() if self._proc.stderr else ""
                raise ProviderUnavailable(f"codex mcp-server exited early: {_short(stderr)}")
            ready, _, _ = select.select([self._proc.stdout, self._proc.stderr], [], [], 0.5)
            now = time.time()
            if now - last_heartbeat >= self.heartbeat_interval_seconds:
                heartbeat_count += 1
                last_heartbeat = now
                self.last_heartbeat = {
                    "stage": "request",
                    "msg_id": msg_id,
                    "timeout_seconds": timeout,
                    "elapsed_seconds": round(now - started_at, 3),
                    "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
                    "heartbeats": heartbeat_count,
                    "stderr_tail": stderr_tail[-5:],
                }
            for stream in ready:
                line = stream.readline()
                if not line:
                    continue
                if stream is self._proc.stderr:
                    stderr_tail.append(line.strip())
                    stderr_tail[:] = stderr_tail[-20:]
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("id") == msg_id:
                    self.last_heartbeat = {
                        "stage": "request",
                        "msg_id": msg_id,
                        "status": "response",
                        "timeout_seconds": timeout,
                        "elapsed_seconds": round(time.time() - started_at, 3),
                        "heartbeats": heartbeat_count,
                        "stderr_tail": stderr_tail[-5:],
                    }
                    return payload
        self.last_heartbeat = {
            "stage": "request",
            "msg_id": msg_id,
            "status": "timeout",
            "timeout_seconds": timeout,
            "elapsed_seconds": round(time.time() - started_at, 3),
            "heartbeats": heartbeat_count,
            "stderr_tail": stderr_tail[-5:],
        }
        raise ProviderExecutionError(f"codex mcp request timed out after {timeout}s. stderr_tail={_short(' '.join(stderr_tail))}")


def _json_from_content(content: str) -> dict[str, object] | None:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)
