from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterator

from nexus.artifacts import RunStore


def stable_hash(payload: object) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return sha256(text.encode("utf-8")).hexdigest()


class CheckpointManager:
    def __init__(self, store: RunStore, *, force_node: str = "", from_node: str = "") -> None:
        self.store = store
        self.force_node = force_node
        self.from_node = from_node

    def status_path(self, node_id: str) -> Path:
        return self.store.path("nodes", node_id, "status.json")

    def input_path(self, node_id: str) -> Path:
        return self.store.path("nodes", node_id, "input.json")

    def output_refs_path(self, node_id: str) -> Path:
        return self.store.path("nodes", node_id, "output_refs.json")

    def read_status(self, node_id: str) -> dict[str, Any] | None:
        path = self.status_path(node_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None

    def completed(self, node_id: str, input_payload: object, required_outputs: list[Path]) -> bool:
        if self.force_node and node_id == self.force_node:
            return False
        status = self.read_status(node_id)
        if not status or status.get("status") != "completed":
            return False
        if status.get("input_hash") != stable_hash(input_payload):
            return False
        return all(path.exists() for path in required_outputs)

    def mark(
        self,
        node_id: str,
        *,
        kind: str,
        status: str,
        input_payload: object,
        output_refs: list[Path] | None = None,
        error: str = "",
        provider: str = "",
        resume_strategy: str = "",
    ) -> Path:
        now = datetime.now(timezone.utc).isoformat()
        previous = self.read_status(node_id) or {}
        payload = {
            "schema": "nexus.node_status.v1",
            "node_id": node_id,
            "kind": kind,
            "status": status,
            "started_at": previous.get("started_at") if previous.get("status") == "running" else now,
            "ended_at": now if status in {"completed", "blocked", "failed"} else "",
            "input_hash": stable_hash(input_payload),
            "output_refs": [str(path) for path in output_refs or []],
            "error": error,
            "provider": provider,
            "provider_session_id": str(previous.get("provider_session_id") or ""),
            "resume_strategy": resume_strategy or _default_resume_strategy(kind, status),
        }
        self.input_path(node_id).parent.mkdir(parents=True, exist_ok=True)
        self.input_path(node_id).write_text(json.dumps(input_payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        self.output_refs_path(node_id).write_text(json.dumps({"schema": "nexus.node_output_refs.v1", "refs": payload["output_refs"]}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return self.store.write_json(f"nodes/{node_id}/status.json", payload)

    @contextmanager
    def running(self, node_id: str, *, kind: str, input_payload: object, provider: str = "") -> Iterator[None]:
        self.mark(node_id, kind=kind, status="running", input_payload=input_payload, provider=provider, resume_strategy="rerun_node")
        try:
            yield
        except Exception as exc:
            self.mark(node_id, kind=kind, status="failed", input_payload=input_payload, error=str(exc), provider=provider, resume_strategy="rerun_node")
            raise


def _default_resume_strategy(kind: str, status: str) -> str:
    if status == "completed":
        return "reuse_artifact"
    if kind == "approval":
        return "approval_required"
    if kind == "model":
        return "provider_resume_or_replay_request"
    return "rerun_node"
