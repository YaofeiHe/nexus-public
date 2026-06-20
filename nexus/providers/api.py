from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request

from nexus.model_profiles import ModelProfile, resolve_api_key

from .base import ModelRequest, ModelResponse, ProviderExecutionError, ProviderStatus, ProviderUnavailable
from .codex_cli import _render_prompt, _short


class ApiProvider:
    name = "api"

    def __init__(self, env_var: str = "OPENAI_API_KEY") -> None:
        self.env_var = env_var

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            self.name,
            "needs_config",
            f"ApiProvider 已迁移为 profile-based provider；请使用 qwen/openai/deepseek/kimi/gemini/anthropic 等 model profile。默认 env：{self.env_var}",
        )

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        raise ProviderUnavailable("ApiProvider 需要指定具体 model profile；请使用 --model qwen-plus 或 python -m nexus.cli model configure。")


class OpenAICompatibleProvider:
    def __init__(self, profile: ModelProfile, *, timeout_seconds: int = 180) -> None:
        self.profile = profile
        self.name = profile.name
        self.timeout_seconds = timeout_seconds

    def status(self) -> ProviderStatus:
        missing: list[str] = []
        if not self.profile.base_url:
            missing.append("base_url")
        if not self.profile.model:
            missing.append("model")
        if not resolve_api_key(self.profile):
            missing.append(self.profile.api_key_env or "api_key_file")
        if missing:
            return ProviderStatus(self.name, "needs_config", f"{self.profile.name} 缺少配置：{', '.join(missing)}。")
        return ProviderStatus(self.name, "available", f"{self.profile.name} 已配置 OpenAI-compatible HTTP API。")

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        status = self.status()
        if status.status != "available":
            raise ProviderUnavailable(status.reason)
        prompt = _render_prompt(request) + "\n如果无法严格满足 schema，请返回最接近 schema 的 JSON 对象，不要输出解释文本。"
        raw = self._chat_completion(prompt, request.schema)
        data = _json_from_text(raw)
        if data is None:
            repair = self._chat_completion(
                "请把下面内容修复为严格 JSON 对象，只输出 JSON，不要 Markdown：\n\n" + raw,
                request.schema,
            )
            data = _json_from_text(repair)
            raw = repair
        if data is None:
            raise ProviderExecutionError(f"{self.name} did not return parseable JSON: {_short(raw)}")
        return ModelResponse(
            provider=self.name,
            raw_text=raw,
            json_data=data,
            diagnostics={
                "adapter": "openai-compatible",
                "base_url": self.profile.base_url,
                "model": self.profile.model,
                "structured_output_mode": self.profile.structured_output_mode,
            },
        )

    def _chat_completion(self, prompt: str, schema: dict[str, object]) -> str:
        url = self.profile.base_url.rstrip("/") + "/chat/completions"
        body: dict[str, object] = {
            "model": self.profile.model,
            "messages": [
                {"role": "system", "content": "You are a JSON-only workflow model node. Return one JSON object only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        if self.profile.structured_output_mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "nexus_model_response", "strict": True, "schema": schema},
            }
        elif self.profile.structured_output_mode in {"json_object", "strict_json_retry"}:
            body["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {resolve_api_key(self.profile)}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderExecutionError(f"{self.name} HTTP {exc.code}: {_short(detail)}") from exc
        except urllib.error.URLError as exc:
            raise ProviderExecutionError(f"{self.name} HTTP request failed: {_short(str(exc))}") from exc
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            raise ProviderExecutionError(f"{self.name} HTTP request failed: {_short(str(exc))}") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ProviderExecutionError(f"{self.name} unexpected response shape: {_short(json.dumps(payload, ensure_ascii=False))}") from exc
        if not isinstance(content, str):
            raise ProviderExecutionError(f"{self.name} returned non-text content.")
        return content


class AnthropicProvider:
    def __init__(self, profile: ModelProfile, *, timeout_seconds: int = 180) -> None:
        self.profile = profile
        self.name = profile.name
        self.timeout_seconds = timeout_seconds

    def status(self) -> ProviderStatus:
        missing: list[str] = []
        if not self.profile.model:
            missing.append("model")
        if not resolve_api_key(self.profile):
            missing.append(self.profile.api_key_env or "api_key_file")
        if missing:
            return ProviderStatus(self.name, "needs_config", f"{self.profile.name} 缺少配置：{', '.join(missing)}。")
        return ProviderStatus(self.name, "available", f"{self.profile.name} 已配置 Anthropic Messages API。")

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        status = self.status()
        if status.status != "available":
            raise ProviderUnavailable(status.reason)
        prompt = _render_prompt(request)
        body = {
            "model": self.profile.model,
            "max_tokens": 4096,
            "temperature": 0,
            "system": "Return one JSON object only. Do not use Markdown.",
            "messages": [{"role": "user", "content": prompt}],
        }
        http_request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": resolve_api_key(self.profile),
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderExecutionError(f"{self.name} HTTP {exc.code}: {_short(detail)}") from exc
        except urllib.error.URLError as exc:
            raise ProviderExecutionError(f"{self.name} HTTP request failed: {_short(str(exc))}") from exc
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            raise ProviderExecutionError(f"{self.name} HTTP request failed: {_short(str(exc))}") from exc
        content_items = payload.get("content", [])
        raw = "\n".join(str(item.get("text", "")) for item in content_items if isinstance(item, dict))
        data = _json_from_text(raw)
        if data is None:
            raise ProviderExecutionError(f"{self.name} did not return parseable JSON: {_short(raw)}")
        return ModelResponse(provider=self.name, raw_text=raw, json_data=data, diagnostics={"adapter": "anthropic", "model": self.profile.model})


def _json_from_text(text: str) -> dict[str, object] | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None
