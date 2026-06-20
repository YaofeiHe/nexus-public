from __future__ import annotations

from pathlib import Path

from nexus.artifacts import RunStore
from nexus.user_prompts import normalize_next_prompt


def write_interaction(
    store: RunStore,
    *,
    status: str,
    output: str,
    next_prompt: str,
    blocked_reason: str = "",
    approval_request: dict[str, object] | None = None,
    artifact_refs: list[str] | None = None,
    lifecycle_status: str = "",
    pending_actions: list[dict[str, object]] | None = None,
    continuation: dict[str, object] | None = None,
    auto_resume_supported: bool = False,
    recovery_mode: bool = False,
    recovery_state: str = "",
    recovery_kind: str = "",
    safe_next_actions: list[str] | None = None,
    recommended_executor: str = "",
    debug_handoff: dict[str, object] | None = None,
    debug_session: dict[str, object] | None = None,
    rebind_requirements: list[str] | None = None,
    rebind_command: str = "",
    terminal: bool = False,
    **extra: object,
) -> dict[str, object]:
    if pending_actions and next_prompt.strip() in {"", "恢复动作待审批。"}:
        next_prompt = _prompt_from_pending_actions(pending_actions)
    next_prompt = normalize_next_prompt(next_prompt)
    payload = {
        "schema": "nexus.interaction.v1",
        "run_id": store.run_id,
        "previous_task_status": status,
        "previous_task_output": output,
        "next_task_prompt": next_prompt,
        "blocked_reason": blocked_reason,
        "approval_request": approval_request,
        "artifact_refs": artifact_refs or [],
        "lifecycle_status": lifecycle_status or _default_lifecycle_status(status, pending_actions),
        "pending_actions": pending_actions or [],
        "continuation": continuation or {},
        "auto_resume_supported": auto_resume_supported,
        "recovery_mode": recovery_mode or bool(pending_actions),
        "recovery_state": recovery_state,
        "recovery_kind": recovery_kind,
        "safe_next_actions": safe_next_actions or [],
        "recommended_executor": recommended_executor,
        "debug_handoff": debug_handoff or {},
        "debug_session": debug_session or {},
        "rebind_requirements": rebind_requirements or [],
        "rebind_command": rebind_command,
        "terminal": terminal,
        **extra,
    }
    store.write_json("interaction.json", payload)
    store.write_text(
        "interaction.md",
        "\n".join(
            [
                f"上一任务状态：{status}",
                f"上一任务输出：{output}",
                f"下一任务提示：{next_prompt}",
            ]
        )
        + "\n",
    )
    return payload


def _default_lifecycle_status(status: str, pending_actions: list[dict[str, object]] | None) -> str:
    if status == "completed":
        return "completed"
    if pending_actions:
        return "awaiting_approval"
    return "blocked" if status == "blocked" else status


def _prompt_from_pending_actions(pending_actions: list[dict[str, object]]) -> str:
    prompts: list[str] = []
    for action in pending_actions:
        command = str(action.get("command") or "").strip()
        if command:
            prompts.append(command)
    if not prompts:
        return "恢复动作待审批。"
    return "\n".join(prompts)


def render_cli_interaction(interaction: dict[str, object]) -> str:
    next_prompt = normalize_next_prompt(interaction.get("next_task_prompt", ""))
    return "\n".join(
        [
            f"上一任务状态：{interaction.get('previous_task_status', '')}",
            f"上一任务输出：{interaction.get('previous_task_output', '')}",
            f"下一任务提示：{next_prompt}",
        ]
    )


def load_interaction(run_dir: Path) -> dict[str, object]:
    import json

    return json.loads((run_dir / "interaction.json").read_text(encoding="utf-8"))
