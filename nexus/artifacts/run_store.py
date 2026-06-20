from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid


class RunStore:
    def __init__(self, root: Path, run_id: str | None = None) -> None:
        self.root = root
        self.run_id = run_id or self.new_run_id()
        self.run_dir = root / ".data" / "runs" / self.run_id

    @staticmethod
    def new_run_id() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"run-{stamp}-{uuid.uuid4().hex[:8]}"

    def ensure(self) -> None:
        for rel in [
            "",
            "model_requests",
            "model_responses",
            "tool_results",
            "candidates",
            "reports",
            "approvals",
            "logs",
        ]:
            (self.run_dir / rel).mkdir(parents=True, exist_ok=True)

    def path(self, *parts: str) -> Path:
        return self.run_dir.joinpath(*parts)

    def write_json(self, relative: str, payload: dict[str, object]) -> Path:
        target = self.path(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target

    def read_json(self, relative: str) -> dict[str, object]:
        payload = json.loads(self.path(*relative.split("/")).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{relative} must contain a JSON object")
        return payload

    def write_text(self, relative: str, text: str) -> Path:
        target = self.path(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def write_jsonl(self, relative: str, rows: list[dict[str, object]]) -> Path:
        target = self.path(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        return target

    def append_audit(self, event: str, payload: dict[str, object] | None = None) -> None:
        target = self.path("audit.json")
        existing: dict[str, object]
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
        else:
            existing = {"schema": "nexus.audit.v1", "events": []}
        events = existing.setdefault("events", [])
        if not isinstance(events, list):
            events = []
            existing["events"] = events
        events.append(
            {
                "time": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "payload": payload or {},
            }
        )
        self.write_json("audit.json", existing)

    def append_event(self, event: str, payload: dict[str, object] | None = None) -> Path:
        target = self.path("logs", "events.jsonl")
        target.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "schema": "nexus.event.v1",
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "payload": payload or {},
        }
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return target
