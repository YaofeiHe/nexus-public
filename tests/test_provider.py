from __future__ import annotations

import http.client
import subprocess
from pathlib import Path

import pytest

from nexus.providers.api import AnthropicProvider, OpenAICompatibleProvider
from nexus.providers.base import ModelRequest, ProviderExecutionError, ProviderStatus
from nexus.providers.codex_cli import CodexCliProvider
from nexus.providers.codex_mcp import CodexMcpProvider
from nexus.model_profiles import ModelProfile


def test_codex_cli_provider_status_is_explicit() -> None:
    status = CodexCliProvider().status()
    assert status.status in {"available", "needs_config", "unavailable"}
    assert status.provider == "codex-cli"


def test_codex_cli_smoke_status_auto_repairs_workspace_codex_home(monkeypatch, tmp_path: Path) -> None:
    provider = CodexCliProvider(cwd=tmp_path)

    monkeypatch.setattr(
        "nexus.providers.codex_cli.CodexCliProvider.status",
        lambda self: ProviderStatus("codex-cli", "available", "cli ready", command="codex"),
    )

    calls: list[Path | None] = []

    def fake_smoke(self, command: str, *, codex_home: Path | None = None, extra_args: list[str] | None = None) -> ProviderStatus:
        assert command == "codex"
        calls.append(codex_home)
        if codex_home is None:
            return ProviderStatus(
                "codex-cli",
                "needs_config",
                "codex exec smoke test failed: attempt to write a readonly database at state_5.sqlite Operation not permitted",
                command="codex",
            )
        return ProviderStatus("codex-cli", "available", "codex exec smoke test passed; real Codex model calls are available.", command="codex")

    monkeypatch.setattr("nexus.providers.codex_cli.CodexCliProvider._run_smoke_with_command", fake_smoke)

    status = provider.smoke_status()

    expected_home = (tmp_path / ".nexus" / "runtime" / "codex-home").resolve()
    assert status.status == "available"
    assert "auto-repair strategy workspace_local_codex_home" in status.reason
    assert calls == [None, expected_home]
    assert provider.codex_home == expected_home
    assert provider.last_smoke_details["repair_attempted"] is True
    assert provider.last_smoke_details["repair_succeeded"] is True
    assert provider.last_smoke_details["repair_module"] == "codex_cli_auto_repair_v2"


def test_codex_mcp_status_timeout_returns_needs_config(monkeypatch, tmp_path: Path) -> None:
    provider = CodexMcpProvider(cwd=tmp_path, status_timeout_seconds=1)
    monkeypatch.setattr("shutil.which", lambda command: "/usr/local/bin/codex" if command == "codex" else None)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))

    monkeypatch.setattr("subprocess.run", fake_run)

    status = provider.status()

    assert status.status == "needs_config"
    assert "timed out after 1s" in status.reason
    assert provider.last_heartbeat["stage"] == "status"
    assert provider.last_heartbeat["status"] == "timeout"


def test_codex_mcp_smoke_failure_is_bounded_needs_config(monkeypatch, tmp_path: Path) -> None:
    provider = CodexMcpProvider(cwd=tmp_path, smoke_timeout_seconds=2, startup_timeout_seconds=1)
    monkeypatch.setattr(
        "nexus.providers.codex_mcp.CodexMcpProvider.status",
        lambda self: ProviderStatus("codex-mcp", "available", "mcp ready", command="codex"),
    )

    def fake_call(self, prompt: str, *, timeout: int, startup_timeout: int | None = None) -> str:
        assert timeout == 2
        assert startup_timeout == 1
        self.last_heartbeat = {"stage": "request", "status": "timeout", "timeout_seconds": timeout, "heartbeats": 2}
        raise ProviderExecutionError("codex mcp request timed out after 2s")

    monkeypatch.setattr("nexus.providers.codex_mcp.CodexMcpProvider._call_codex", fake_call)

    status = provider.smoke_status()

    assert status.status == "needs_config"
    assert "bounded timeout" in status.reason
    assert "heartbeat" in status.reason


def test_openai_compatible_provider_wraps_remote_disconnect(monkeypatch, tmp_path: Path) -> None:
    key_path = tmp_path / "qwen.key"
    key_path.write_text("secret-value", encoding="utf-8")
    provider = OpenAICompatibleProvider(
        ModelProfile(
            name="qwen",
            provider="qwen",
            adapter="openai-compatible",
            model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_file=str(key_path),
            configured=True,
        )
    )
    request = ModelRequest(node_id="test", purpose="test", prompt="{}", schema={"type": "object"})

    def fake_urlopen(*args, **kwargs):
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ProviderExecutionError, match="HTTP request failed"):
        provider.complete_json(request)


def test_anthropic_provider_wraps_remote_disconnect(monkeypatch, tmp_path: Path) -> None:
    key_path = tmp_path / "claude.key"
    key_path.write_text("secret-value", encoding="utf-8")
    provider = AnthropicProvider(
        ModelProfile(
            name="anthropic",
            provider="anthropic",
            adapter="anthropic",
            model="claude-3-5-sonnet-latest",
            api_key_file=str(key_path),
            configured=True,
        )
    )
    request = ModelRequest(node_id="test", purpose="test", prompt="{}", schema={"type": "object"})

    def fake_urlopen(*args, **kwargs):
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ProviderExecutionError, match="HTTP request failed"):
        provider.complete_json(request)
