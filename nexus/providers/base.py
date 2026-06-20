from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol


@dataclass(slots=True)
class ModelRequest:
    node_id: str
    purpose: str
    prompt: str
    schema: dict[str, object]
    context_refs: list[str] = field(default_factory=list)
    safety_boundary: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class ModelResponse:
    provider: str
    raw_text: str
    json_data: dict[str, object]
    diagnostics: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class ProviderStatus:
    provider: str
    status: str
    reason: str
    command: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class HostModelProvider(Protocol):
    name: str

    def status(self) -> ProviderStatus:
        ...

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        ...


class ProviderUnavailable(RuntimeError):
    pass


class ProviderExecutionError(RuntimeError):
    pass
