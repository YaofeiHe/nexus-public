from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Any


CODEX_PROFILES = {"codex-mcp", "codexmcp", "codex-cli", "codexcli"}
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LOW_INTENSITY_CODEX_PROFILE = "codex-cli-gpt5.4middle"
HIGH_INTENSITY_CODEX_PROFILE = "codex-cli-gpt5.4high"


@dataclass(slots=True)
class ModelProfile:
    name: str
    provider: str
    adapter: str
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    api_key_file: str = ""
    structured_output_mode: str = "strict_json_retry"
    configured: bool = False
    notes: str = ""

    def to_dict(self, *, redacted: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if redacted and payload.get("api_key_file"):
            payload["api_key_file"] = _redact_path(str(payload["api_key_file"]))
        payload["api_key_present"] = bool(resolve_api_key(self))
        return payload


@dataclass(slots=True)
class IntensityModelConfig:
    low_api_profile: str = ""
    high_api_profile: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": "nexus.model_intensity.v1",
            "low_api_profile": self.low_api_profile,
            "high_api_profile": self.high_api_profile,
        }


def model_config_dir(root: Path) -> Path:
    return root / ".data" / "config" / "models"


def model_profiles_path(root: Path) -> Path:
    return model_config_dir(root) / "profiles.json"


def intensity_config_path(root: Path) -> Path:
    return model_config_dir(root) / "intensity.json"


def session_model_path(root: Path) -> Path:
    session_id = os.environ.get("CODEX_SESSION_ID") or os.environ.get("NEXUS_SESSION_ID")
    if session_id:
        return root / ".data" / "sessions" / _safe_name(session_id) / "model_profile.json"
    return root / ".data" / "session" / "current_model_profile.json"


def builtin_profiles() -> dict[str, ModelProfile]:
    return {
        "codex-mcp": ModelProfile("codex-mcp", "codex-mcp", "codex-mcp", configured=True, notes="Codex MCP stdio provider; standby fallback after API profiles and codex-cli."),
        "codexcli": ModelProfile("codexcli", "codex-cli", "codex-cli", configured=True, notes="Alias for codex-cli."),
        "codex-cli": ModelProfile("codex-cli", "codex-cli", "codex-cli", configured=True, notes="Codex CLI provider via codex exec; second choice after configured API profiles."),
        LOW_INTENSITY_CODEX_PROFILE: ModelProfile(LOW_INTENSITY_CODEX_PROFILE, "codex-cli", "codex-cli", model="gpt-5.4", configured=True, notes="Low-intensity Codex CLI fallback via codex exec --model gpt-5.4."),
        HIGH_INTENSITY_CODEX_PROFILE: ModelProfile(HIGH_INTENSITY_CODEX_PROFILE, "codex-cli", "codex-cli", model="gpt-5.4", configured=True, notes="High-intensity Codex CLI provider via codex exec --model gpt-5.4."),
        "qwen": ModelProfile("qwen", "qwen", "openai-compatible", model="qwen-plus", base_url=DASHSCOPE_BASE_URL, api_key_env="DASHSCOPE_API_KEY", notes="Alibaba Cloud Model Studio / DashScope Qwen."),
        "qwen-plus": ModelProfile("qwen-plus", "qwen", "openai-compatible", model="qwen-plus", base_url=DASHSCOPE_BASE_URL, api_key_env="DASHSCOPE_API_KEY", notes="Qwen Plus profile."),
        "qwen3.7max": ModelProfile("qwen3.7max", "qwen", "openai-compatible", model="qwen3.7max", base_url=DASHSCOPE_BASE_URL, api_key_env="DASHSCOPE_API_KEY", notes="Qwen high-intensity API fallback profile."),
        "openai": ModelProfile("openai", "openai", "openai-compatible", model="gpt-4.1-mini", base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY", structured_output_mode="json_schema", notes="OpenAI API."),
        "deepseek": ModelProfile("deepseek", "deepseek", "openai-compatible", model="deepseek-chat", base_url="https://api.deepseek.com", api_key_env="DEEPSEEK_API_KEY", notes="DeepSeek OpenAI-compatible API."),
        "kimi": ModelProfile("kimi", "kimi", "openai-compatible", model="moonshot-v1-8k", base_url="https://api.moonshot.cn/v1", api_key_env="MOONSHOT_API_KEY", notes="Moonshot/Kimi OpenAI-compatible API."),
        "gemini": ModelProfile("gemini", "gemini", "openai-compatible", model="gemini-2.5-flash", base_url="https://generativelanguage.googleapis.com/v1beta/openai", api_key_env="GEMINI_API_KEY", notes="Gemini OpenAI compatibility endpoint."),
        "zhipu": ModelProfile("zhipu", "zhipu", "openai-compatible", model="glm-4.5", base_url="https://open.bigmodel.cn/api/paas/v4", api_key_env="ZHIPU_API_KEY", notes="Zhipu GLM OpenAI-compatible profile."),
        "minimax": ModelProfile("minimax", "minimax", "openai-compatible", model="MiniMax-M1", base_url="https://api.minimax.chat/v1", api_key_env="MINIMAX_API_KEY", notes="MiniMax profile."),
        "doubao": ModelProfile("doubao", "doubao", "openai-compatible", model="doubao-seed-1-6", base_url="https://ark.cn-beijing.volces.com/api/v3", api_key_env="ARK_API_KEY", notes="ByteDance Volcano Ark / Doubao profile."),
        "baichuan": ModelProfile("baichuan", "baichuan", "openai-compatible", model="Baichuan4", base_url="https://api.baichuan-ai.com/v1", api_key_env="BAICHUAN_API_KEY", notes="Baichuan OpenAI-compatible profile."),
        "anthropic": ModelProfile("anthropic", "anthropic", "anthropic", model="claude-3-5-sonnet-latest", base_url="https://api.anthropic.com/v1", api_key_env="ANTHROPIC_API_KEY", notes="Anthropic Messages API."),
    }


def unsupported_profiles() -> list[str]:
    return [
        "groq",
        "together",
        "fireworks",
        "cerebras",
        "nvidia-nim",
        "azure-openai",
        "bedrock",
        "vertex-ai",
        "cloudflare-ai",
        "openrouter",
        "litellm",
        "ollama",
        "lmstudio",
    ]


def load_user_profiles(root: Path) -> dict[str, ModelProfile]:
    path = model_profiles_path(root)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    profiles = payload.get("profiles", payload)
    if not isinstance(profiles, dict):
        return {}
    loaded: dict[str, ModelProfile] = {}
    for name, raw in profiles.items():
        if not isinstance(raw, dict):
            continue
        profile = ModelProfile(
            name=str(raw.get("name") or name),
            provider=str(raw.get("provider") or name),
            adapter=str(raw.get("adapter") or "openai-compatible"),
            model=str(raw.get("model") or ""),
            base_url=str(raw.get("base_url") or ""),
            api_key_env=str(raw.get("api_key_env") or ""),
            api_key_file=str(raw.get("api_key_file") or ""),
            structured_output_mode=str(raw.get("structured_output_mode") or "strict_json_retry"),
            configured=bool(raw.get("configured", False)),
            notes=str(raw.get("notes") or ""),
        )
        loaded[_normalize_name(profile.name)] = profile
    return loaded


def load_profiles(root: Path) -> dict[str, ModelProfile]:
    profiles = builtin_profiles()
    profiles.update(load_user_profiles(root))
    return {_normalize_name(name): profile for name, profile in profiles.items()}


def save_profile(root: Path, profile: ModelProfile) -> Path:
    path = model_profiles_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    user_profiles = load_user_profiles(root)
    user_profiles[_normalize_name(profile.name)] = profile
    payload = {"schema": "nexus.model_profiles.v1", "profiles": {name: p.to_dict(redacted=False) for name, p in sorted(user_profiles.items())}}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def import_api_key_file(root: Path, profile_name: str, source_file: str) -> str:
    source = Path(source_file).expanduser()
    if not source.exists():
        return source_file
    target_dir = model_config_dir(root) / "secrets"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_safe_name(profile_name)}.api_key"
    target.write_text(source.read_text(encoding="utf-8").strip() + "\n", encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return str(target)


def load_intensity_config(root: Path) -> IntensityModelConfig:
    path = intensity_config_path(root)
    if not path.exists():
        return IntensityModelConfig()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return IntensityModelConfig()
    return IntensityModelConfig(
        low_api_profile=str(payload.get("low_api_profile") or ""),
        high_api_profile=str(payload.get("high_api_profile") or ""),
    )


def save_intensity_config(root: Path, config: IntensityModelConfig) -> Path:
    path = intensity_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def resolve_profile(root: Path, name: str | None) -> ModelProfile | None:
    if not name or name == "auto":
        name = load_session_model(root)
    if not name or name == "auto":
        return None
    normalized = _normalize_name(name)
    aliases = {"codexmcp": "codex-mcp", "codexcli": "codex-cli"}
    normalized = aliases.get(normalized, normalized)
    profiles = load_profiles(root)
    if normalized in profiles:
        return profiles[normalized]
    for profile in profiles.values():
        if profile.model and _normalize_name(profile.model) == normalized:
            return profile
    return None


def set_session_model(root: Path, profile_name: str) -> Path:
    path = session_model_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "nexus.session_model.v1",
        "profile": profile_name,
        "scope": "CODEX_SESSION_ID" if os.environ.get("CODEX_SESSION_ID") or os.environ.get("NEXUS_SESSION_ID") else "nexus-current-default",
        "note": "如果 Codex 没有暴露窗口 session id，此默认值不是严格窗口隔离。",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_session_model(root: Path) -> str:
    path = session_model_path(root)
    if not path.exists():
        return "auto"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "auto"
    return str(payload.get("profile") or "auto")


def detect_model_name(text: str) -> str:
    markers = ["基座模型使用", "模型使用", "使用模型", "base model"]
    lowered = text.lower()
    for marker in markers:
        index = lowered.find(marker.lower())
        if index == -1:
            continue
        tail = text[index + len(marker) :].strip(" ：:=，,。.\n\t")
        if not tail:
            return ""
        token = tail.split()[0].strip(" ，,。.;；")
        return token
    return ""


def resolve_api_key(profile: ModelProfile) -> str:
    if profile.api_key_env and os.environ.get(profile.api_key_env):
        return str(os.environ[profile.api_key_env])
    if profile.api_key_file:
        path = Path(profile.api_key_file).expanduser()
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return ""


def profile_status(root: Path) -> dict[str, Any]:
    profiles = load_profiles(root)
    available = []
    needs_config = []
    ordered_profiles = list(profiles.items())
    current = load_session_model(root)
    current_profile = resolve_profile(root, current)
    if current_profile is not None:
        current_key = _normalize_name(current_profile.name)
        ordered_profiles.sort(key=lambda item: (0 if item[0] == current_key else 1, item[0]))
    else:
        ordered_profiles.sort(key=lambda item: item[0])
    for name, profile in ordered_profiles:
        if profile.adapter in {"codex-mcp", "codex-cli"}:
            available.append(name)
        elif profile.base_url and profile.model and resolve_api_key(profile):
            available.append(name)
        else:
            needs_config.append(name)
    return {
        "schema": "nexus.model_status.v1",
        "current": current,
        "intensity": load_intensity_config(root).to_dict(),
        "available_or_builtin": available,
        "needs_config": needs_config,
        "unsupported": unsupported_profiles(),
        "profiles": {name: profile.to_dict() for name, profile in sorted(profiles.items())},
        "session_path": str(session_model_path(root)),
    }


def _normalize_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in value)[:120] or "default"


def _redact_path(value: str) -> str:
    path = Path(value).expanduser()
    return str(path.parent / "***")
