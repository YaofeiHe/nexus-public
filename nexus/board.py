from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path


def board_dir(project_path: Path) -> Path:
    return project_path.expanduser().resolve() / ".nexus"


def board_json_path(project_path: Path) -> Path:
    return board_dir(project_path) / "board.json"


def board_md_path(project_path: Path) -> Path:
    return board_dir(project_path) / "board.md"


def load_board(project_path: Path) -> dict[str, object]:
    path = board_json_path(project_path)
    if not path.exists():
        return {
            "schema": "nexus.project_board.v1",
            "current_status": "未记录",
            "updated_at": "",
            "points": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("board.json must contain an object")
    return payload


def update_board(project_path: Path, *, status: str | None = None, point: str | None = None) -> dict[str, object]:
    payload = load_board(project_path)
    now = datetime.now(timezone.utc).isoformat()
    if status is not None:
        payload["current_status"] = status
    payload["updated_at"] = now
    points = payload.get("points")
    if not isinstance(points, list):
        points = []
    if point:
        points.insert(0, {"time": now, "text": point})
    payload["points"] = points[:5]
    _write_board(project_path, payload)
    return payload


def _write_board(project_path: Path, payload: dict[str, object]) -> None:
    target_dir = board_dir(project_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    board_json_path(project_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Project Board",
        "",
        "## Current Status",
        "",
        f"- status: {payload.get('current_status', '')}",
        f"- updated_at: {payload.get('updated_at', '')}",
        "",
        "## Recent Points",
        "",
    ]
    points = payload.get("points")
    if isinstance(points, list):
        for item in points:
            if isinstance(item, dict):
                lines.append(f"- [{item.get('time', '')}] {item.get('text', '')}")
    board_md_path(project_path).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
