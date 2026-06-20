from __future__ import annotations

import json
from pathlib import Path

from nexus.config import DEFAULT_PROVIDER_PRIORITY, load_config, write_default_config
from nexus.model_profiles import IntensityModelConfig, ModelProfile, save_intensity_config, save_profile
from nexus.providers.base import ProviderStatus
from nexus.providers.registry import build_provider, doctor, iter_real_provider_candidates, select_real_provider


def test_default_config_uses_api_first_priority(tmp_path: Path) -> None:
    path = write_default_config(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["provider_priority"] == DEFAULT_PROVIDER_PRIORITY
    assert load_config(tmp_path).provider_priority == DEFAULT_PROVIDER_PRIORITY


def test_legacy_default_priority_is_migrated_to_api_first(tmp_path: Path) -> None:
    config_path = tmp_path / ".data" / "config" / "provider.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "provider_priority": ["codex-mcp", "codex-cli", "api"],
                "api_env_var": "OPENAI_API_KEY",
                "locale": {"locale": "zh-CN", "market_context": "chinese_internet"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert load_config(tmp_path).provider_priority == DEFAULT_PROVIDER_PRIORITY


def test_build_provider_api_resolves_to_configured_profile(tmp_path: Path) -> None:
    key_path = tmp_path / "api.key"
    key_path.write_text("secret-value", encoding="utf-8")
    save_profile(
        tmp_path,
        ModelProfile(
            name="api-first",
            provider="api-first",
            adapter="openai-compatible",
            model="demo-model",
            base_url="https://example.invalid/v1",
            api_key_file=str(key_path),
            configured=True,
        ),
    )
    save_intensity_config(tmp_path, IntensityModelConfig(low_api_profile="", high_api_profile="qwen3.7max"))
    provider = build_provider("api", root=tmp_path, cwd=tmp_path)
    assert provider.name == "api-first"


def test_select_real_provider_prefers_configured_api_profile(monkeypatch, tmp_path: Path) -> None:
    key_path = tmp_path / "api.key"
    key_path.write_text("secret-value", encoding="utf-8")
    save_profile(
        tmp_path,
        ModelProfile(
            name="api-first",
            provider="api-first",
            adapter="openai-compatible",
            model="demo-model",
            base_url="https://example.invalid/v1",
            api_key_file=str(key_path),
            configured=True,
        ),
    )

    def codex_unavailable(self) -> ProviderStatus:
        return ProviderStatus(self.name, "unavailable", "disabled for test")

    monkeypatch.setattr("nexus.providers.codex_cli.CodexCliProvider.status", codex_unavailable)
    monkeypatch.setattr("nexus.providers.codex_mcp.CodexMcpProvider.status", codex_unavailable)

    provider = select_real_provider(tmp_path, cwd=tmp_path)
    assert provider is not None
    assert provider.name == "api-first"


def test_low_intensity_uses_configured_api_slot_without_qwen(monkeypatch, tmp_path: Path) -> None:
    key_path = tmp_path / "deepseek.key"
    key_path.write_text("secret-value", encoding="utf-8")
    save_profile(
        tmp_path,
        ModelProfile(
            name="deepseek-chat",
            provider="deepseek",
            adapter="openai-compatible",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            api_key_file=str(key_path),
            configured=True,
        ),
    )
    save_intensity_config(tmp_path, IntensityModelConfig(low_api_profile="deepseek-chat", high_api_profile=""))

    def codex_unavailable(self) -> ProviderStatus:
        return ProviderStatus(self.name, "unavailable", "disabled for test")

    monkeypatch.setattr("nexus.providers.codex_cli.CodexCliProvider.status", codex_unavailable)
    monkeypatch.setattr("nexus.providers.codex_mcp.CodexMcpProvider.status", codex_unavailable)

    provider = select_real_provider(tmp_path, cwd=tmp_path)
    assert provider is not None
    assert provider.name == "deepseek-chat"


def test_select_real_provider_falls_back_to_codex_cli_before_mcp(monkeypatch, tmp_path: Path) -> None:
    def cli_available(self) -> ProviderStatus:
        return ProviderStatus(self.name, "available", "cli ready")

    def mcp_available(self) -> ProviderStatus:
        return ProviderStatus(self.name, "available", "mcp ready")

    monkeypatch.setattr("nexus.providers.codex_cli.CodexCliProvider.status", cli_available)
    monkeypatch.setattr("nexus.providers.codex_mcp.CodexMcpProvider.status", mcp_available)

    provider = select_real_provider(tmp_path, cwd=tmp_path)
    assert provider is not None
    assert provider.name == "codex-cli"
    assert getattr(provider, "model") == "gpt-5.4"


def test_select_real_provider_falls_back_to_codex_mcp_when_cli_unavailable(monkeypatch, tmp_path: Path) -> None:
    def cli_unavailable(self) -> ProviderStatus:
        return ProviderStatus(self.name, "unavailable", "cli disabled")

    def mcp_available(self) -> ProviderStatus:
        return ProviderStatus(self.name, "available", "mcp ready")

    monkeypatch.setattr("nexus.providers.codex_cli.CodexCliProvider.status", cli_unavailable)
    monkeypatch.setattr("nexus.providers.codex_mcp.CodexMcpProvider.status", mcp_available)

    provider = select_real_provider(tmp_path, cwd=tmp_path)
    assert provider is not None
    assert provider.name == "codex-mcp"


def test_high_intensity_candidates_start_with_gpt54_codex_cli(tmp_path: Path) -> None:
    candidates = iter_real_provider_candidates(tmp_path, cwd=tmp_path, intensity="high")

    assert candidates[0].name == "codex-cli"
    assert getattr(candidates[0], "model") == "gpt-5.4"


def test_auto_skip_providers_removes_codex_cli_from_candidates(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NEXUS_AUTO_SKIP_PROVIDERS", "codex-cli")

    low_candidates = iter_real_provider_candidates(tmp_path, cwd=tmp_path)
    high_candidates = iter_real_provider_candidates(tmp_path, cwd=tmp_path, intensity="high")

    assert all(provider.name != "codex-cli" for provider in low_candidates)
    assert all(provider.name != "codex-cli" for provider in high_candidates)


def test_provider_candidates_read_profiles_from_config_root_env(monkeypatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    config_root = tmp_path / "config"
    runtime_root.mkdir()
    key_path = config_root / "api.key"
    key_path.parent.mkdir(parents=True)
    key_path.write_text("secret-value", encoding="utf-8")
    save_profile(
        config_root,
        ModelProfile(
            name="api-from-config-root",
            provider="api-from-config-root",
            adapter="openai-compatible",
            model="demo-model",
            base_url="https://example.invalid/v1",
            api_key_file=str(key_path),
            configured=True,
        ),
    )
    save_intensity_config(config_root, IntensityModelConfig(low_api_profile="api-from-config-root", high_api_profile=""))
    monkeypatch.setenv("NEXUS_PROVIDER_CONFIG_ROOT", str(config_root))

    candidates = iter_real_provider_candidates(runtime_root, cwd=runtime_root)

    assert candidates[0].name == "api-from-config-root"


def test_doctor_audits_codex_mcp_skip_without_probing_mcp(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NEXUS_AUTO_SKIP_PROVIDERS", "codex-mcp")

    def mcp_status_should_not_run(self) -> ProviderStatus:
        raise AssertionError("codex-mcp status should be skipped")

    def cli_unavailable(self) -> ProviderStatus:
        return ProviderStatus(self.name, "unavailable", "disabled for test")

    monkeypatch.setattr("nexus.providers.codex_mcp.CodexMcpProvider.status", mcp_status_should_not_run)
    monkeypatch.setattr("nexus.providers.codex_cli.CodexCliProvider.status", cli_unavailable)

    payload = doctor(tmp_path)

    assert payload["environment"]["codex_mcp_skip_active"] is True
    assert payload["providers"]["codex-mcp"]["status"] == "skipped"
    assert payload["model_profiles"]["codex-mcp"]["status"] == "skipped"


def test_high_intensity_falls_back_to_qwen37max_api(monkeypatch, tmp_path: Path) -> None:
    key_path = tmp_path / "qwen.key"
    key_path.write_text("secret-value", encoding="utf-8")
    save_profile(
        tmp_path,
        ModelProfile(
            name="qwen3.7max",
            provider="qwen",
            adapter="openai-compatible",
            model="qwen3.7max",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_file=str(key_path),
            configured=True,
        ),
    )

    def cli_unavailable(self) -> ProviderStatus:
        return ProviderStatus(self.name, "unavailable", "cli disabled")

    def mcp_unavailable(self) -> ProviderStatus:
        return ProviderStatus(self.name, "unavailable", "mcp disabled")

    monkeypatch.setattr("nexus.providers.codex_cli.CodexCliProvider.status", cli_unavailable)
    monkeypatch.setattr("nexus.providers.codex_mcp.CodexMcpProvider.status", mcp_unavailable)

    provider = select_real_provider(tmp_path, cwd=tmp_path, intensity="high")
    assert provider is not None
    assert provider.name == "qwen3.7max"
