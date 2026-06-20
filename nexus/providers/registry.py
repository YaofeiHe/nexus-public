from __future__ import annotations

import json
import os
from pathlib import Path

from nexus.config import load_config, provider_status_path
from nexus.model_profiles import (
    HIGH_INTENSITY_CODEX_PROFILE,
    LOW_INTENSITY_CODEX_PROFILE,
    ModelProfile,
    load_intensity_config,
    load_profiles,
    load_session_model,
    resolve_api_key,
    resolve_profile,
)

from .api import AnthropicProvider, ApiProvider, OpenAICompatibleProvider
from .base import HostModelProvider, ProviderStatus
from .codex_cli import CodexCliProvider
from .codex_mcp import CodexMcpProvider
from .mock import MockProvider


def build_provider(name: str, *, root: Path, cwd: Path | None = None) -> HostModelProvider:
    config_root = _provider_config_root(root)
    profile = resolve_profile(config_root, name)
    if profile is not None:
        return build_provider_from_profile(profile, cwd=cwd or root)
    if name == "mock":
        return MockProvider()
    if name == "codex-cli":
        return CodexCliProvider(cwd=cwd or root)
    if name == "codex-mcp":
        return CodexMcpProvider(cwd=cwd or root)
    if name == "api":
        profile = select_default_api_profile(root)
        if profile is not None:
            return build_provider_from_profile(profile, cwd=cwd or root)
        return ApiProvider(load_config(config_root).api_env_var)
    raise ValueError(f"Unknown provider: {name}")


def build_provider_from_profile(profile: ModelProfile, *, cwd: Path | None = None) -> HostModelProvider:
    if profile.adapter == "codex-mcp" or profile.provider == "codex-mcp":
        return CodexMcpProvider(cwd=cwd, model=profile.model)
    if profile.adapter == "codex-cli" or profile.provider == "codex-cli":
        return CodexCliProvider(cwd=cwd, model=profile.model)
    if profile.adapter == "anthropic":
        return AnthropicProvider(profile)
    if profile.adapter == "openai-compatible":
        return OpenAICompatibleProvider(profile)
    raise ValueError(f"Unsupported model profile adapter: {profile.adapter}")


def doctor(root: Path) -> dict[str, object]:
    config_root = _provider_config_root(root)
    cfg = load_config(config_root)
    skipped = _skip_providers()
    statuses = [
        _provider_status_or_skipped("codex-mcp", skipped, lambda: CodexMcpProvider(cwd=root).status()),
        _provider_status_or_skipped("codex-cli", skipped, lambda: CodexCliProvider(cwd=root).status()),
        ApiProvider(cfg.api_env_var).status(),
    ]
    profiles = load_profiles(config_root)
    profile_statuses = {}
    for name, profile in sorted(profiles.items()):
        try:
            if _profile_skipped(profile, skipped):
                profile_statuses[name] = _skipped_status(profile.name, skipped).to_dict()
                continue
            provider = build_provider_from_profile(profile, cwd=root)
            profile_statuses[name] = provider.status().to_dict()
        except Exception as exc:
            profile_statuses[name] = {"provider": name, "status": "unavailable", "reason": str(exc)}
    payload = {
        "schema": "nexus.provider_status.v1",
        "config_root": str(config_root),
        "runtime_root": str(root),
        "environment": {
            "NEXUS_PROVIDER_CONFIG_ROOT": os.environ.get("NEXUS_PROVIDER_CONFIG_ROOT", ""),
            "NEXUS_AUTO_SKIP_PROVIDERS": os.environ.get("NEXUS_AUTO_SKIP_PROVIDERS", ""),
            "skipped_providers": sorted(skipped),
            "codex_mcp_skip_active": "codex-mcp" in skipped,
            "skip_semantics": "Skipped providers are omitted from auto candidates and status probes; codex-mcp skip is treated as intentional fallback, not a terminal blocker.",
        },
        "provider_priority": cfg.provider_priority,
        "intensity": load_intensity_config(config_root).to_dict(),
        "intensity_priority": {
            "low": "configured API slot -> codex-cli gpt-5.4 -> codex-mcp",
            "high": "codex-cli gpt-5.4 -> configured API slot -> codex-mcp",
        },
        "locale": cfg.locale.to_dict(),
        "providers": {status.provider: status.to_dict() for status in statuses},
        "model_profiles": profile_statuses,
    }
    path = provider_status_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def select_default_api_profile(root: Path) -> ModelProfile | None:
    config_root = _provider_config_root(root)
    session_name = load_session_model(config_root)
    session_profile = resolve_profile(config_root, session_name)
    if session_profile is not None and session_profile.adapter in {"openai-compatible", "anthropic"} and resolve_api_key(session_profile):
        return session_profile
    configured_profiles: list[ModelProfile] = []
    for profile in load_profiles(config_root).values():
        if profile.adapter not in {"openai-compatible", "anthropic"}:
            continue
        if resolve_api_key(profile):
            configured_profiles.append(profile)
    if configured_profiles:
        return configured_profiles[0]
    if session_profile is not None and session_profile.adapter in {"openai-compatible", "anthropic"}:
        return session_profile
    for profile in load_profiles(config_root).values():
        if profile.adapter in {"openai-compatible", "anthropic"}:
            return profile
    return None


def iter_real_provider_candidates(root: Path, *, cwd: Path | None = None, intensity: str = "low") -> list[HostModelProvider]:
    providers: list[HostModelProvider] = []
    seen: set[tuple[str, str]] = set()
    skipped = _skip_providers()
    config_root = _provider_config_root(root)
    if intensity == "high":
        high_codex = resolve_profile(config_root, HIGH_INTENSITY_CODEX_PROFILE)
        if high_codex is not None and not _profile_skipped(high_codex, skipped):
            seen.add(("profile", high_codex.name))
            providers.append(build_provider_from_profile(high_codex, cwd=cwd or root))
        for profile in _high_api_profiles(config_root):
            key = ("profile", profile.name)
            if key in seen:
                continue
            if _profile_skipped(profile, skipped):
                continue
            seen.add(key)
            providers.append(build_provider_from_profile(profile, cwd=cwd or root))
        if "codex-mcp" not in skipped:
            seen.add(("provider", "codex-mcp"))
            providers.append(build_provider("codex-mcp", root=root, cwd=cwd or root))
        return providers
    for profile in _low_api_profiles(config_root):
        key = ("profile", profile.name)
        if key in seen:
            continue
        if _profile_skipped(profile, skipped):
            continue
        seen.add(key)
        providers.append(build_provider_from_profile(profile, cwd=cwd or root))
    low_codex = resolve_profile(config_root, LOW_INTENSITY_CODEX_PROFILE)
    if low_codex is not None and not _profile_skipped(low_codex, skipped):
        seen.add(("profile", low_codex.name))
        providers.append(build_provider_from_profile(low_codex, cwd=cwd or root))
    if "codex-mcp" not in skipped:
        seen.add(("provider", "codex-mcp"))
        providers.append(build_provider("codex-mcp", root=root, cwd=cwd or root))
    return providers


def select_real_provider(root: Path, *, cwd: Path | None = None, intensity: str = "low") -> HostModelProvider | None:
    for provider in iter_real_provider_candidates(root, cwd=cwd, intensity=intensity):
        status = provider.status()
        if isinstance(status, ProviderStatus) and status.status == "available":
            return provider
    return None


def _high_api_profiles(root: Path) -> list[ModelProfile]:
    return _ordered_api_slot_profiles(root, load_intensity_config(root).high_api_profile)


def _low_api_profiles(root: Path) -> list[ModelProfile]:
    return _ordered_api_slot_profiles(root, load_intensity_config(root).low_api_profile)


def _ordered_api_slot_profiles(root: Path, slot_profile: str) -> list[ModelProfile]:
    profiles = load_profiles(root)
    ordered: list[ModelProfile] = []
    seen: set[str] = set()

    def add(profile: ModelProfile | None) -> None:
        if profile is None:
            return
        key = profile.name.strip().lower()
        if key in seen:
            return
        if profile.adapter not in {"openai-compatible", "anthropic"}:
            return
        ordered.append(profile)
        seen.add(key)

    add(resolve_profile(root, slot_profile))
    if not slot_profile:
        add(resolve_profile(root, load_session_model(root)))
    for profile in profiles.values():
        if resolve_api_key(profile):
            add(profile)
    return ordered


def _skip_providers() -> set[str]:
    raw = os.environ.get("NEXUS_AUTO_SKIP_PROVIDERS", "")
    aliases = {"codexcli": "codex-cli", "codexmcp": "codex-mcp"}
    return {aliases.get(item.strip().lower(), item.strip().lower()) for item in raw.split(",") if item.strip()}


def _provider_config_root(root: Path) -> Path:
    override = os.environ.get("NEXUS_PROVIDER_CONFIG_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return root


def _provider_status_or_skipped(provider: str, skipped: set[str], factory) -> ProviderStatus:
    if provider in skipped:
        return _skipped_status(provider, skipped)
    return factory()


def _skipped_status(provider: str, skipped: set[str]) -> ProviderStatus:
    return ProviderStatus(
        provider,
        "skipped",
        f"{provider} skipped by NEXUS_AUTO_SKIP_PROVIDERS={','.join(sorted(skipped))}; this is an explicit fallback decision, not terminal provider blockage.",
    )


def _profile_skipped(profile: ModelProfile, skipped: set[str]) -> bool:
    values = {profile.name.strip().lower(), profile.provider.strip().lower(), profile.adapter.strip().lower()}
    normalized = {"codexcli": "codex-cli", "codexmcp": "codex-mcp"}
    values = {normalized.get(value, value) for value in values}
    return bool(values & skipped)
