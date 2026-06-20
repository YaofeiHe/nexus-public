from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterable

from nexus.artifacts import RunStore


PLAYBOOK_REL = Path(".nexus/recovery-playbook.json")
RECORDS_REL = Path("docs/recovery-records.md")


def compact_text(value: str, limit: int = 280) -> str:
    compact = " ".join(str(value or "").split())
    if not compact:
        return ""
    return compact[:limit]


def summarize_attempts(attempts: list[dict[str, object]], *, limit: int = 4) -> str:
    tail = attempts[-limit:] if limit > 0 else attempts
    parts: list[str] = []
    for item in tail:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "unknown")
        stage = str(item.get("stage") or "unknown")
        status = str(item.get("status") or "unknown")
        issue = str(item.get("issue_type") or "")
        reason = compact_text(str(item.get("reason") or ""), limit=120)
        segment = f"{provider}/{stage}/{status}"
        if issue:
            segment += f"/{issue}"
        if reason:
            segment += f": {reason}"
        parts.append(segment)
    return "；".join(parts)


def iter_pending_actions(
    guidance: dict[str, object],
    attempts: list[dict[str, object]],
    *,
    aliases: dict[str, set[str]] | None = None,
    max_actions: int = 2,
) -> Iterable[str]:
    labels = {str(item.get("label") or "") for item in attempts if isinstance(item, dict)}
    recommended = guidance.get("recommended_actions") if isinstance(guidance.get("recommended_actions"), list) else []
    yielded = 0
    alias_map = aliases or {}
    for item in recommended:
        if yielded >= max_actions:
            break
        if not isinstance(item, dict):
            continue
        action_id = str(item.get("action_id") or "").strip()
        if not action_id:
            continue
        expected = alias_map.get(action_id, {action_id})
        if labels.intersection(expected):
            continue
        yielded += 1
        yield action_id


def build_recovery_context(
    *,
    store: RunStore,
    project: Path,
    module: str,
    node: str,
    status: str,
    reason: str,
    result: dict[str, object] | None = None,
    artifact_refs: list[str] | None = None,
    retry_prompt: str = "",
) -> dict[str, object]:
    project = project.expanduser().resolve()
    payload = {
        "schema": "nexus.recovery_context.v1",
        "run_id": store.run_id,
        "project_path": str(project),
        "module": module,
        "node": node,
        "status": status,
        "reason": compact_text(reason, limit=500),
        "failure_signature": recovery_signature(module=module, node=node, reason=reason, result=result or {}),
        "result": result or {},
        "artifact_refs": artifact_refs or [],
        "retry_prompt": retry_prompt,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return payload


def recovery_signature(*, module: str, node: str, reason: str, result: dict[str, object]) -> str:
    basis = {
        "module": module,
        "node": node,
        "reason": compact_text(reason, limit=180),
        "result_reason": compact_text(str(result.get("reason") or ""), limit=180),
        "result_status": str(result.get("status") or ""),
    }
    raw = json.dumps(basis, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_recovery_playbook(project: Path) -> dict[str, object]:
    path = project.expanduser().resolve() / PLAYBOOK_REL
    if not path.exists():
        return {"schema": "nexus.recovery_playbook.v1", "entries": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": "nexus.recovery_playbook.v1", "entries": []}
    return payload if isinstance(payload, dict) else {"schema": "nexus.recovery_playbook.v1", "entries": []}


def match_recovery_playbook(project: Path, context: dict[str, object]) -> dict[str, object]:
    playbook = load_recovery_playbook(project)
    entries = playbook.get("entries") if isinstance(playbook.get("entries"), list) else []
    signature = str(context.get("failure_signature") or "")
    module = str(context.get("module") or "")
    node = str(context.get("node") or "")
    reason = str(context.get("reason") or "")
    for item in reversed(entries):
        if not isinstance(item, dict):
            continue
        if item.get("failure_signature") == signature:
            return item
        if item.get("module") == module and item.get("node") == node and str(item.get("reason") or "") == reason:
            return item
    for item in builtin_recovery_playbook_entries(context):
        if not isinstance(item, dict):
            continue
        if item.get("module") == module and item.get("node") == node:
            return item
    return {}


def related_recovery_experience(project: Path, context: dict[str, object], *, limit: int = 5) -> dict[str, object]:
    project = project.expanduser().resolve()
    module = str(context.get("module") or "")
    node = str(context.get("node") or "")
    reason = str(context.get("reason") or "")
    context_text = " ".join([module, node, reason, _flatten_failure_text(context)]).lower()
    playbook = load_recovery_playbook(project)
    raw_entries = playbook.get("entries") if isinstance(playbook.get("entries"), list) else []
    candidates = [item for item in raw_entries if isinstance(item, dict)]
    candidates.extend(builtin_recovery_playbook_entries(context))
    scored: list[tuple[int, dict[str, object]]] = []
    for item in candidates:
        score = _experience_score(item, module=module, node=node, reason=reason, context_text=context_text)
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    entries = [_trim_experience_entry(item, score=score) for score, item in scored[:limit]]
    records_excerpt = ""
    records_path = project / RECORDS_REL
    if records_path.exists():
        try:
            records_excerpt = compact_text(records_path.read_text(encoding="utf-8", errors="ignore"), limit=2200)
        except OSError:
            records_excerpt = ""
    return {
        "schema": "nexus.related_recovery_experience.v1",
        "selection_policy": "仅作为参考证据，不作为限制或白名单；恢复模型必须自主判断相关性、有效性和当前风险。",
        "entries": entries,
        "records_excerpt": records_excerpt,
    }


def _experience_score(item: dict[str, object], *, module: str, node: str, reason: str, context_text: str) -> int:
    score = 0
    if str(item.get("module") or "") == module:
        score += 6
    if str(item.get("node") or "") == node:
        score += 4
    if str(item.get("reason") or "") == reason:
        score += 5
    item_text = _flatten_failure_text(item).lower()
    for token in _experience_tokens(context_text):
        if token in item_text:
            score += 1
    return score


def _experience_tokens(text: str) -> set[str]:
    raw = text.replace("_", " ").replace("-", " ").replace("/", " ").split()
    return {token for token in raw if len(token) >= 4 and token not in {"schema", "status", "blocked", "completed", "result"}}


def _trim_experience_entry(item: dict[str, object], *, score: int) -> dict[str, object]:
    return {
        "score": score,
        "source": str(item.get("source") or "project"),
        "failure_signature": str(item.get("failure_signature") or ""),
        "module": str(item.get("module") or ""),
        "node": str(item.get("node") or ""),
        "reason": str(item.get("reason") or ""),
        "summary": compact_text(str(item.get("summary") or ""), limit=700),
        "probable_root_cause": compact_text(str(item.get("probable_root_cause") or ""), limit=300),
        "recommended_actions": item.get("recommended_actions") if isinstance(item.get("recommended_actions"), list) else [],
        "successful_actions": item.get("successful_actions") if isinstance(item.get("successful_actions"), list) else [],
    }


def builtin_recovery_playbook_entries(context: dict[str, object]) -> list[dict[str, object]]:
    module = str(context.get("module") or "")
    node = str(context.get("node") or "")
    reason = str(context.get("reason") or "")
    result = context.get("result") if isinstance(context.get("result"), dict) else {}
    safe_error = str(result.get("safe_error") or "")
    diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
    probable_cause = str(diagnostics.get("probable_cause") or "")
    combined = " ".join([reason, safe_error, probable_cause, _flatten_failure_text(result)]).lower()
    if module == "github" and node == "github_auth" and reason == "LOGIN_START_FAILED":
        actions = [
            {
                "action_id": "retry_without_proxy_and_debug_api",
                "rationale": "GitHub CLI 登录启动失败且错误像代理/EOF/本地网络链路中断；优先绕开代理并开启 GH_DEBUG=api。历史经验显示，device-code POST EOF 后也要复查 gh auth status，因为登录可能已经就位。",
                "requires_escalation": True,
                "risk_summary": "会重新发起 GitHub CLI web/device 登录请求；用户仍需自行完成密码、2FA、CAPTCHA 和授权确认。",
                "command": "gh auth login --web --clipboard --skip-ssh-key --git-protocol https --hostname github.com",
                "service": "github.com",
                "paths": [],
            },
            {
                "action_id": "retry_default_login_after_diagnostics",
                "rationale": "若诊断后链路恢复，复试标准 GitHub CLI 登录流程可验证是否为瞬时网络故障。",
                "requires_escalation": True,
                "risk_summary": "会再次请求 GitHub device login，不读取 token/cookie/浏览器 profile。",
                "command": "gh auth login --web --clipboard --skip-ssh-key --git-protocol https --hostname github.com",
                "service": "github.com",
                "paths": [],
            },
        ]
        if "eof" in combined and "login/device/code" in combined:
            summary = "命中内置 GitHub 登录经验：GitHub device-code POST EOF，先按去代理 + debug API 重试，并在重试前后复查 gh auth status，避免重复要求用户登录。"
            root_cause = "github_device_login_post_eof_or_proxy_interrupted"
        elif "proxy" in combined or "eof" in combined or "operation not permitted" in combined or "dial tcp" in combined:
            summary = "命中内置 GitHub 登录经验：登录启动失败更像代理、EOF 或本地网络链路问题，先按去代理 + debug API 方向尝试。"
            root_cause = "proxy_or_local_network_interrupted"
        else:
            summary = "命中内置 GitHub 登录经验：GitHub CLI web/device 登录启动失败，先按标准诊断重试方向尝试。"
            root_cause = "github_cli_login_start_failed"
        return [
            {
                "schema": "nexus.recovery_playbook_entry.v1",
                "source": "builtin",
                "failure_signature": "builtin-github-auth-login-start-failed",
                "module": "github",
                "node": "github_auth",
                "reason": "LOGIN_START_FAILED",
                "summary": summary,
                "probable_root_cause": root_cause,
                "recommended_actions": actions,
                "successful_actions": [],
            }
        ]
    if module == "github" and reason == "github_repo_create_failed":
        return [
            _github_builtin_entry(
                node=node,
                reason=reason,
                signature="builtin-github-repo-create-failed",
                summary="命中内置 GitHub 仓库经验：仓库创建失败时先确认是否为同名仓库已存在；若 private/public 仓库已存在且可访问，应复用现有仓库并补齐 remote 后重试原同步。",
                root_cause="repo_exists_or_create_conflict",
                actions=[
                    {
                        "action_id": "reuse_existing_repo_after_create_conflict",
                        "rationale": "历史 wljob 同步中 public 仓库已存在并非权限故障；确认 repo view 可访问后应复用仓库。",
                        "requires_escalation": True,
                        "risk_summary": "会访问 GitHub repo 元数据，并可能设置本地 git remote；不会读取 token、cookie 或浏览器 profile。",
                        "command": "gh repo view <owner/repo> --json nameWithOwner,visibility,url",
                        "service": "github.com",
                        "paths": [],
                    },
                    {
                        "action_id": "retry_original_github_sync_after_repo_reuse",
                        "rationale": "仓库存在性确认后重试原 Nexus GitHub sync/bootstrap，不更换仓库名。",
                        "requires_escalation": True,
                        "risk_summary": "会继续执行原 GitHub 同步动作，可能 push private 或在显式确认后 push public。",
                        "command": "python -m nexus.cli github-sync <bootstrap|private|public> --project-path <project>",
                        "service": "github.com",
                        "paths": [str(context.get("project_path") or "")],
                    },
                ],
            )
        ]
    if module == "github" and reason == "gh_git_auth_setup_failed":
        return [
            _github_builtin_entry(
                node=node,
                reason=reason,
                signature="builtin-github-gh-git-auth-setup-failed",
                summary="命中内置 GitHub 凭证桥接经验：先确认 gh auth 已登录，再运行 gh auth setup-git，最后重试原同步。",
                root_cause="gh_git_credential_helper_not_ready",
                actions=[
                    {
                        "action_id": "verify_auth_status_then_setup_git",
                        "rationale": "历史同步中 GitHub CLI 登录就位后，git 凭证桥接是 private/public push 前的关键收口步骤。",
                        "requires_escalation": True,
                        "risk_summary": "会检查 GitHub CLI 登录状态并配置 git credential helper；不输出 token。",
                        "command": "gh auth status --hostname github.com && gh auth setup-git --hostname github.com",
                        "service": "github.com",
                        "paths": [],
                    }
                ],
            )
        ]
    if module == "github" and reason == "git_push_failed":
        return [
            _github_builtin_entry(
                node=node,
                reason=reason,
                signature="builtin-github-git-push-failed",
                summary="命中内置 GitHub push 经验：push 看似卡住或失败时，先检查本地 commit、remote、repo 可访问性和 gh git 凭证桥接，再重试原 push/sync。",
                root_cause="git_push_remote_or_credential_state_uncertain",
                actions=[
                    {
                        "action_id": "diagnose_remote_commit_and_git_auth",
                        "rationale": "历史 wljob private 同步中，push 前需要确认首提交、private remote、仓库存在和 gh credential helper。",
                        "requires_escalation": True,
                        "risk_summary": "会读取本地 git 状态、remote 配置并检查 GitHub CLI 凭证桥接；不会读取 token/cookie/SSH key。",
                        "command": "git status --porcelain=v1; git remote -v; gh auth setup-git --hostname github.com",
                        "service": "github.com",
                        "paths": [str(context.get("project_path") or "")],
                    },
                    {
                        "action_id": "retry_original_push_or_sync",
                        "rationale": "诊断显示 remote 和凭证桥接可用后，重试原 Nexus private/public 同步。",
                        "requires_escalation": True,
                        "risk_summary": "会再次 push 到配置的 GitHub 仓库；public push 仍必须显式确认。",
                        "command": "python -m nexus.cli github-sync <private|public|auto-private> --project-path <project>",
                        "service": "github.com",
                        "paths": [str(context.get("project_path") or "")],
                    },
                ],
            )
        ]
    return []


def _github_builtin_entry(
    *,
    node: str,
    reason: str,
    signature: str,
    summary: str,
    root_cause: str,
    actions: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema": "nexus.recovery_playbook_entry.v1",
        "source": "builtin",
        "failure_signature": f"{signature}-{node}",
        "module": "github",
        "node": node,
        "reason": reason,
        "summary": summary,
        "probable_root_cause": root_cause,
        "recommended_actions": actions,
        "successful_actions": [],
    }


def _flatten_failure_text(value: object, *, limit: int = 6000) -> str:
    parts: list[str] = []

    def visit(item: object) -> None:
        if len(" ".join(parts)) >= limit:
            return
        if isinstance(item, dict):
            for key, nested in item.items():
                if key in {"stdout", "stderr", "reason", "status", "safe_error", "output", "repo", "visibility"}:
                    parts.append(str(nested))
                else:
                    visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif isinstance(item, str):
            parts.append(item)

    visit(value)
    return compact_text(" ".join(parts), limit=limit)


def recovery_prompt(
    context: dict[str, object],
    playbook_match: dict[str, object] | None = None,
    related_experience: dict[str, object] | None = None,
) -> str:
    return (
        "你是 Nexus 的高精度恢复规划节点。"
        "请基于失败上下文生成可执行的恢复动作计划。"
        "不要把风险当作拒绝理由；如果动作需要权限，请明确 requires_escalation=true 并说明风险、会访问的服务、会读写的路径。"
        "如果动作需要用户本人完成平台交互，也要明确写入 manual_user_actions。"
        "推荐动作可以是新动作；后续是否执行由外层提权/审批边界决定。"
        "如果没有精确命中的经验，请先审阅 related_recovery_experience："
        "把经验库条款和历史操作结果当作可参考证据，而不是限制死的条框。"
        "你需要自主判断经验是否与当前情景有关、是否仍有效、是否值得尝试；"
        "如果经验无关或风险/前提不成立，直接说明并规划新的路线；"
        "如果经验有关，可以先从经验结果出发继续分析和恢复，但不能被经验牵着鼻子走。\n\n"
        f"failure_context:\n{json.dumps(context, ensure_ascii=False, indent=2)[:14000]}\n\n"
        f"matched_playbook_entry:\n{json.dumps(playbook_match or {}, ensure_ascii=False, indent=2)[:6000]}\n\n"
        f"related_recovery_experience:\n{json.dumps(related_experience or {}, ensure_ascii=False, indent=2)[:9000]}\n\n"
        "请输出 failure_recovery_guidance schema。recommended_actions 中每项尽量包含 action_id、rationale、requires_escalation、risk_summary、command、service、paths。"
    )


def fallback_recovery_guidance(context: dict[str, object]) -> dict[str, object]:
    reason = str(context.get("reason") or "")
    module = str(context.get("module") or "")
    return {
        "schema": "nexus.failure_recovery_guidance.v1",
        "summary": f"{module} 失败，当前没有可用高精度模型生成进一步计划，已保留原始失败上下文。",
        "probable_root_cause": reason or "unknown_failure",
        "safe_next_attempts": ["检查失败上下文中的配置、权限、网络或外部服务状态后重试。"],
        "manual_user_actions": ["按下一任务提示修复外部条件后继续同一 workflow。"],
        "stop_conditions": ["如果连续失败，记录失败上下文并等待用户授权下一步动作。"],
        "recommended_actions": [],
    }


def guidance_actions(guidance: dict[str, object]) -> list[dict[str, object]]:
    actions = guidance.get("recommended_actions") if isinstance(guidance.get("recommended_actions"), list) else []
    return [_normalize_recovery_action(item, index=index) for index, item in enumerate(actions) if isinstance(item, dict)]


def _normalize_recovery_action(item: dict[str, object], *, index: int) -> dict[str, object]:
    requires_escalation = bool(item.get("requires_escalation") or item.get("requires_host_permission"))
    manual_user_action = item.get("manual_user_action") if isinstance(item.get("manual_user_action"), dict) else {}
    runner_action = item.get("runner_action") if isinstance(item.get("runner_action"), dict) else {}
    kind = str(item.get("kind") or "")
    if not kind:
        if runner_action:
            kind = str(runner_action.get("kind") or "runner_command")
        elif manual_user_action:
            kind = "manual_continue"
        elif requires_escalation:
            kind = "shell_escalation"
        elif item.get("command"):
            kind = "shell"
        else:
            kind = "diagnostic"
    normalized = dict(item)
    normalized.update(
        {
            "action_id": str(item.get("action_id") or f"recovery_action_{index + 1}"),
            "kind": kind,
            "requires_approval": bool(item.get("requires_approval", requires_escalation or bool(manual_user_action))),
            "requires_host_permission": requires_escalation,
            "requires_escalation": requires_escalation,
            "runner_action": runner_action,
            "manual_user_action": manual_user_action,
            "continuation_policy": str(item.get("continuation_policy") or "return_to_original_run"),
        }
    )
    return normalized


def build_recovery_result(
    context: dict[str, object],
    guidance: dict[str, object],
    *,
    status: str,
    playbook_match: dict[str, object] | None = None,
    attempts: list[dict[str, object]] | None = None,
    recovered_by: str = "",
) -> dict[str, object]:
    actions = guidance_actions(guidance)
    requires_escalation = any(bool(item.get("requires_escalation")) for item in actions)
    return {
        "schema": "nexus.recovery_result.v1",
        "status": status,
        "context": context,
        "guidance": guidance,
        "attempts": attempts or [],
        "recovered_by": recovered_by,
        "playbook_match": playbook_match or {},
        "requires_escalation": requires_escalation,
        "recommended_actions": actions,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_recovery_artifacts(store: RunStore, result: dict[str, object]) -> dict[str, Path]:
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    guidance = result.get("guidance") if isinstance(result.get("guidance"), dict) else {}
    attempts = result.get("attempts") if isinstance(result.get("attempts"), list) else []
    return {
        "context": store.write_json("tool_results/recovery_context.json", context),
        "plan": store.write_json("tool_results/recovery_plan.json", guidance),
        "attempts": store.write_json("tool_results/recovery_attempts.json", {"schema": "nexus.recovery_attempts.v1", "attempts": attempts}),
        "result": store.write_json("tool_results/recovery_result.json", result),
    }


def build_playbook_write_approval(store: RunStore, project: Path, result: dict[str, object]) -> dict[str, object]:
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    guidance = result.get("guidance") if isinstance(result.get("guidance"), dict) else {}
    approval = {
        "schema": "nexus.recovery_playbook_write_required.v1",
        "stage": "recovery-playbook",
        "project_path": str(project.expanduser().resolve()),
        "run_id": store.run_id,
        "failure_signature": str(context.get("failure_signature") or ""),
        "module": str(context.get("module") or ""),
        "node": str(context.get("node") or ""),
        "reason": str(context.get("reason") or ""),
        "summary": str(guidance.get("summary") or ""),
        "recommended_actions": guidance_actions(guidance),
        "write_targets": [str(project.expanduser().resolve() / PLAYBOOK_REL), str(project.expanduser().resolve() / RECORDS_REL)],
        "risk_note": "将本次成功恢复经验写入项目级 playbook；以后类似失败会优先复用该经验，但高风险动作仍应发起提权说明。",
        "approval_command": f"python -m nexus.cli approve {store.run_id} recovery-playbook",
    }
    return approval


def apply_recovery_playbook_approval(project: Path, result: dict[str, object]) -> dict[str, object]:
    project = project.expanduser().resolve()
    playbook_path = project / PLAYBOOK_REL
    records_path = project / RECORDS_REL
    playbook_path.parent.mkdir(parents=True, exist_ok=True)
    records_path.parent.mkdir(parents=True, exist_ok=True)
    playbook = load_recovery_playbook(project)
    entries = playbook.get("entries") if isinstance(playbook.get("entries"), list) else []
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    guidance = result.get("guidance") if isinstance(result.get("guidance"), dict) else {}
    signature = str(context.get("failure_signature") or "")
    entry = {
        "schema": "nexus.recovery_playbook_entry.v1",
        "failure_signature": signature,
        "module": str(context.get("module") or ""),
        "node": str(context.get("node") or ""),
        "reason": str(context.get("reason") or ""),
        "summary": str(guidance.get("summary") or ""),
        "probable_root_cause": str(guidance.get("probable_root_cause") or ""),
        "successful_actions": result.get("attempts") if isinstance(result.get("attempts"), list) else [],
        "recommended_actions": guidance_actions(guidance),
        "source_run_id": str(context.get("run_id") or ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    entries = [item for item in entries if not (isinstance(item, dict) and item.get("failure_signature") == signature)]
    entries.append(entry)
    payload = {"schema": "nexus.recovery_playbook.v1", "entries": entries, "updated_at": datetime.now(timezone.utc).isoformat()}
    playbook_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not records_path.exists():
        records_path.write_text(f"# {project.name} Recovery Records\n\n", encoding="utf-8")
    with records_path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n".join(
                [
                    f"## {entry['module']} / {entry['node']}",
                    "",
                    f"- time: `{entry['updated_at']}`",
                    f"- run_id: `{entry['source_run_id']}`",
                    f"- signature: `{entry['failure_signature']}`",
                    f"- reason: {entry['reason']}",
                    f"- summary: {entry['summary']}",
                    "",
                ]
            )
        )
    return {"schema": "nexus.recovery_playbook_write_result.v1", "status": "completed", "playbook_path": str(playbook_path), "records_path": str(records_path), "entry": entry}


def recovery_output_text(result: dict[str, object]) -> str:
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    guidance = result.get("guidance") if isinstance(result.get("guidance"), dict) else {}
    attempts = result.get("attempts") if isinstance(result.get("attempts"), list) else []
    actions = guidance_actions(guidance)
    lines = [
        "Nexus 已进入全局恢复模块。",
        f"-> 失败位置：{context.get('module', '')}/{context.get('node', '')}",
        f"-> 失败原因：{context.get('reason', '')}",
    ]
    if result.get("playbook_match"):
        lines.append("-> 已命中项目 recovery playbook，优先复用历史经验。")
    elif guidance.get("related_experience_artifact"):
        lines.append("-> 未命中精确 playbook；已把相关经验库条款和历史结果交给高精度模型参考，但不作为限制。")
    if guidance.get("summary"):
        lines.append(f"-> 高精度模型分析：{guidance.get('summary')}")
    if guidance.get("probable_root_cause"):
        lines.append(f"-> 可能根因：{guidance.get('probable_root_cause')}")
    if actions:
        rendered = []
        for item in actions[:4]:
            action_id = str(item.get("action_id") or "")
            escalation = "需要提权" if item.get("requires_escalation") else "可直接尝试"
            risk = compact_text(str(item.get("risk_summary") or item.get("rationale") or ""), limit=120)
            rendered.append(f"{action_id} ({escalation}: {risk})")
        lines.append("-> 建议动作：" + "；".join(rendered))
    if attempts:
        lines.append("-> 已尝试：" + summarize_attempts(attempts))
    if result.get("requires_escalation"):
        lines.append("-> 下一步含需要授权的动作；请审阅 approval artifact，确认后继续执行。")
    return "\n".join(lines)
