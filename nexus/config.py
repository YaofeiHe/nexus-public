from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


LEGACY_DEFAULT_PROVIDER_PRIORITY = ["codex-mcp", "codex-cli", "api"]
DEFAULT_PROVIDER_PRIORITY = ["api", "codex-cli", "codex-mcp"]


@dataclass(slots=True)
class LocaleConfig:
    locale: str = "zh-CN"
    market_context: str = "chinese_internet"
    query_language_priority: list[str] | None = None
    report_language: str = "zh-CN"
    source_priority: list[str] | None = None

    def __post_init__(self) -> None:
        if self.query_language_priority is None:
            self.query_language_priority = ["zh", "en"]
        if self.source_priority is None:
            self.source_priority = [
                "official_cn_docs",
                "chinese_tech_blogs",
                "gitee",
                "github",
                "mcp_registry",
                "official_en_docs",
            ]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class NexusConfig:
    provider_priority: list[str]
    locale: LocaleConfig
    api_env_var: str = "OPENAI_API_KEY"

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_priority": self.provider_priority,
            "api_env_var": self.api_env_var,
            "locale": self.locale.to_dict(),
        }


def default_config() -> NexusConfig:
    return NexusConfig(provider_priority=list(DEFAULT_PROVIDER_PRIORITY), locale=LocaleConfig())


def config_dir(root: Path) -> Path:
    return root / ".data" / "config"


def config_path(root: Path) -> Path:
    return config_dir(root) / "provider.json"


def provider_status_path(root: Path) -> Path:
    return config_dir(root) / "provider_status.json"


def load_config(root: Path) -> NexusConfig:
    path = config_path(root)
    if not path.exists():
        return default_config()
    payload = json.loads(path.read_text(encoding="utf-8"))
    locale_payload = payload.get("locale") if isinstance(payload, dict) else {}
    if not isinstance(locale_payload, dict):
        locale_payload = {}
    raw_priority = payload.get("provider_priority", DEFAULT_PROVIDER_PRIORITY)
    provider_priority = [str(item) for item in raw_priority] if isinstance(raw_priority, list) and raw_priority else list(DEFAULT_PROVIDER_PRIORITY)
    if provider_priority == LEGACY_DEFAULT_PROVIDER_PRIORITY:
        provider_priority = list(DEFAULT_PROVIDER_PRIORITY)
    return NexusConfig(
        provider_priority=provider_priority,
        api_env_var=str(payload.get("api_env_var") or "OPENAI_API_KEY"),
        locale=LocaleConfig(
            locale=str(locale_payload.get("locale") or "zh-CN"),
            market_context=str(locale_payload.get("market_context") or "chinese_internet"),
            query_language_priority=[str(item) for item in locale_payload.get("query_language_priority", ["zh", "en"])],
            report_language=str(locale_payload.get("report_language") or "zh-CN"),
            source_priority=[str(item) for item in locale_payload.get("source_priority", LocaleConfig().source_priority)],
        ),
    )


def write_default_config(root: Path) -> Path:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default_config().to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
