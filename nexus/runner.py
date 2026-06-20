from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from typing import Any

from nexus.artifacts import RunStore
from nexus.board import load_board, update_board
from nexus.checkpoints import CheckpointManager
from nexus.config import load_config, provider_status_path
from nexus.candidates import process_candidates, rerank_candidates
from nexus.conversation import choose_session, discover_codex_sessions, import_codex_session, import_conversation, install_generated_skill, redact_messages, select_messages
from nexus.conversation_manager import ingest_transcript, init_conversation_manager, write_prompt_pack, write_summary, write_workflow
from nexus.core_rules import load_core_rules
from nexus.feishu_autosync import append_feishu_record_markdown, run_feishu_autosync, wants_skip_feishu_sync
from nexus.feishu_sync import load_config as load_feishu_config
from nexus.feishu_setup import build_publish_required_approval, build_setup_guide, run_doctor as run_feishu_doctor, run_setup as run_feishu_setup
from nexus.github_auth import run_github_auth_login
from nexus.github_guide import write_github_sync_guide
from nexus.github_sync import auto_private_sync, bootstrap_project, load_config as load_github_sync_config, prepare_public_staging, scan_public_staging, sync_private, sync_public, validate_public_fresh_clone, validate_public_staging, write_config as write_github_sync_config
from nexus.guide_sync import write_operation_guide
from nexus.interaction import write_interaction
from nexus.model_profiles import HIGH_INTENSITY_CODEX_PROFILE, detect_model_name, load_session_model, profile_status, resolve_profile
from nexus.project_docs import write_project_docs
from nexus.providers.base import HostModelProvider, ModelRequest, ProviderExecutionError, ProviderStatus, ProviderUnavailable
from nexus.providers.codex_cli import CodexCliProvider
from nexus.providers.codex_mcp import CodexMcpProvider
from nexus.providers.registry import build_provider, doctor, iter_real_provider_candidates
from nexus.research_contract import (
    BRANCH_LABELS,
    branch_id_from_route,
    build_branch_research_artifacts,
    build_research_contract,
    render_branch_report,
    render_decision_matrix,
    render_research_contract,
    selected_branch_is_valid,
)
from nexus.schemas import SCHEMAS
from nexus.tools.availability import check_candidates
from nexus.tools.git_baseline import create_git_baseline, inspect_git_baseline
from nexus.tools.repo_scan import scan_repo
from nexus.tools.safety import detect_high_risk_actions, safety_boundary
from nexus.tools.schema_validation import validate_json
from nexus.tools.search_models import CandidateRecord, SourceStatus
from nexus.tools.search_service import SearchService
from nexus.recovery import (
    apply_recovery_playbook_approval,
    build_playbook_write_approval,
    build_recovery_context,
    build_recovery_result,
    fallback_recovery_guidance,
    match_recovery_playbook,
    recovery_output_text,
    recovery_prompt,
    related_recovery_experience,
    write_recovery_artifacts,
)
from nexus.system_showcase import explain_node, generate_showcase
from nexus.user_prompts import normalize_next_prompt


REAL_FIVE_LETTER_PROJECT_WORDS = {
    "atlas",
    "bloom",
    "brisk",
    "build",
    "craft",
    "field",
    "forge",
    "frame",
    "glyph",
    "guide",
    "haven",
    "index",
    "nexus",
    "orbit",
    "pilot",
    "pivot",
    "prism",
    "probe",
    "query",
    "relay",
    "scout",
    "scope",
    "sieve",
    "spark",
    "stack",
    "trace",
    "vault",
    "vista",
    "weave",
}


class Runner:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._resume_from_node = ""
        self._force_node = ""

    def run(
        self,
        idea: str,
        *,
        project_path: Path,
        provider_name: str = "auto",
        model_name: str = "",
        max_candidates: int = 8,
        approve_online_search: bool = False,
    ) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        cfg = load_config(self.root)
        selected_provider = self._resolve_provider_name(provider_name, idea, model_name)
        store.write_json(
            "input.json",
            {
                "schema": "nexus.input.v1",
                "idea": idea,
                "project_path": str(project),
                "provider": selected_provider,
                "requested_provider": provider_name,
                "model": model_name or detect_model_name(idea) or load_session_model(self.root),
                "max_candidates": max_candidates,
                "approve_online_search": approve_online_search,
                "locale": cfg.locale.to_dict(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        self._write_state(store, "created", current_node="provider_preflight")
        store.append_audit("run_created", {"idea": idea, "provider": selected_provider})
        self._write_active_project(project, source="research", run_id=store.run_id)
        provider = self._select_provider(store, selected_provider, project)
        if provider is None:
            return self._block_for_provider_setup(store)
        store.write_json(
            "model_selection.json",
            {
                "schema": "nexus.model_selection.v1",
                "selected_provider": provider.name,
                "requested_provider": provider_name,
                "requested_model": model_name or detect_model_name(idea) or "",
                "session_default": load_session_model(self.root),
                "note": "HostModelProvider 模型节点按强度选择 provider；低强度 auto 默认 API 槽位 -> codex-cli gpt5.4middle -> codex-mcp，高强度 auto 默认 codex-cli gpt5.4high -> API 槽位 -> codex-mcp；工具层检索 adapter 不受该字段控制。",
            },
        )
        if approve_online_search:
            self._write_online_search_approval_marker(store, "research --approve-online-search")
        board = load_board(project)
        store.write_json("tool_results/project_board.json", board)
        return self._execute_workflow(store, provider=provider, idea=idea, project=project, max_candidates=max_candidates)

    def resume(self, run_id: str, *, from_node: str = "", force_node: str = "") -> dict[str, object]:
        actual_run_id = self._resolve_resumable_run_id(run_id)
        store = RunStore(self.root, actual_run_id)
        if not store.path("input.json").exists():
            return write_interaction(
                store,
                status="blocked",
                output="该 run 缺少 input.json，不是可 resume 的 workflow run。",
                next_prompt="运行 python -m nexus.cli status <run_id> 查看状态，或指定另一个 run_id。",
                blocked_reason="run_not_resumable",
            )
        input_payload = store.read_json("input.json")
        state = store.read_json("state.json")
        if state.get("status") == "completed" and not force_node:
            return write_interaction(
                store,
                status="completed",
                output="该 run 已完成，无需重复执行。",
                next_prompt=f"查看报告：python -m nexus.cli report {actual_run_id}；如需重跑某节点，使用 --force-node <node_id>。",
                artifact_refs=[str(store.path("reports", "final_report.md"))],
            )
        store.write_json(
            "resume/decision.json",
            {
                "schema": "nexus.resume_decision.v1",
                "run_id": actual_run_id,
                "from_node": from_node,
                "force_node": force_node,
                "previous_state": state,
                "strategy": "node_checkpoint_reuse_completed_artifacts",
            },
        )
        input_schema = str(input_payload.get("schema") or "")
        if input_schema == "nexus.conversation_to_workflow_input.v1":
            return self._resume_conversation_to_workflow(store)
        if input_schema == "nexus.project_init_input.v1":
            if force_node == "project_name_candidates" or str(state.get("blocked_reason") or "") == "project_name_model_failed":
                return self._continue_init_project_after_provider_recovery(store)
            return write_interaction(
                store,
                status=str(state.get("status") or "blocked"),
                output="该 init-project run 不能通过通用 resume 恢复；请使用当前状态对应的 approve/recover 提示。",
                next_prompt=f"查看状态：python -m nexus.cli status {actual_run_id}",
                blocked_reason=str(state.get("blocked_reason") or "init_project_resume_not_supported"),
            )
        project = Path(str(input_payload["project_path"])).expanduser().resolve()
        provider_name = str(input_payload.get("provider") or "auto")
        provider = self._select_provider(store, provider_name, project)
        if provider is None:
            return self._block_for_provider_setup(store)
        self._resume_from_node = from_node
        self._force_node = force_node
        lock_path = self._acquire_workflow_resume_lock(store)
        if isinstance(lock_path, dict):
            return lock_path
        try:
            return self._execute_workflow(
                store,
                provider=provider,
                idea=str(input_payload["idea"]),
                project=project,
                max_candidates=int(input_payload.get("max_candidates") or 8),
            )
        finally:
            self._release_workflow_resume_lock(lock_path)

    def _select_provider(self, store: RunStore, provider_name: str, project: Path, *, intensity: str = "low") -> HostModelProvider | None:
        if provider_name == "mock":
            return build_provider("mock", root=self.root, cwd=project)
        status_payload = doctor(self.root)
        store.write_json("tool_results/provider_status.json", status_payload)
        attempts_payload = _read_provider_attempts(store)
        attempts = attempts_payload["attempts"]
        if provider_name != "auto":
            try:
                provider = build_provider(provider_name, root=self.root, cwd=project)
            except ValueError as exc:
                store.write_json("tool_results/provider_selection_error.json", {"schema": "nexus.provider_selection_error.v1", "provider": provider_name, "reason": str(exc), "model_status": profile_status(self.root)})
                return None
            status = provider.status()
            if status.status != "available":
                _append_provider_attempt(store, attempts_payload, provider=provider.name, status=status.status, reason=status.reason, stage="status", intensity=intensity)
                return None
            if self._preflight_provider(store, provider, attempts=attempts, intensity=intensity):
                attempts_payload.setdefault("selected_by_intensity", {})[intensity] = provider.name
                store.write_json("tool_results/provider_attempts.json", attempts_payload)
                return provider
            store.write_json("tool_results/provider_attempts.json", attempts_payload)
            _write_provider_recovery(store, project, attempts_payload, failed_provider=provider.name, intensity=intensity, exhausted=not _has_fallback_provider(self.root, project, provider.name, intensity=intensity))
            return None
        for provider in iter_real_provider_candidates(self.root, cwd=project, intensity=intensity):
            status = provider.status()
            if not isinstance(status, ProviderStatus) or status.status != "available":
                _append_provider_attempt(store, attempts_payload, provider=provider.name, status=getattr(status, "status", "unknown"), reason=getattr(status, "reason", ""), stage="status", intensity=intensity)
                continue
            if self._preflight_provider(store, provider, attempts=attempts, intensity=intensity):
                attempts_payload.setdefault("selected_by_intensity", {})[intensity] = provider.name
                store.write_json("tool_results/provider_attempts.json", attempts_payload)
                return provider
            store.write_json("tool_results/provider_attempts.json", attempts_payload)
            _write_provider_recovery(store, project, attempts_payload, failed_provider=provider.name, intensity=intensity, exhausted=not _has_fallback_provider(self.root, project, provider.name, intensity=intensity))
            return None
        return None

    def _preflight_provider(self, store: RunStore, provider: HostModelProvider, *, attempts: list[dict[str, object]] | None = None, intensity: str = "low") -> bool:
        if provider.name == "mock":
            return True
        if isinstance(provider, CodexCliProvider):
            runtime_status_path = store.path("tool_results", f"provider_runtime_status_preflight_{_safe_node_suffix(provider.name)}_{_safe_node_suffix(intensity)}.json")
            previous_runtime_status_path = provider.runtime_status_path
            provider.runtime_status_path = runtime_status_path
            try:
                status = provider.smoke_status()
            finally:
                provider.runtime_status_path = previous_runtime_status_path
            store.write_json("tool_results/provider_preflight.json", {"schema": "nexus.provider_preflight.v1", "status": status.to_dict()})
            _record_preflight_attempt(provider, status, attempts, intensity=intensity)
            return status.status == "available"
        if isinstance(provider, CodexMcpProvider):
            status = provider.smoke_status()
            store.write_json("tool_results/provider_preflight.json", {"schema": "nexus.provider_preflight.v1", "status": status.to_dict()})
            _record_preflight_attempt(provider, status, attempts, intensity=intensity)
            return status.status == "available"
        status = provider.status()
        store.write_json("tool_results/provider_preflight.json", {"schema": "nexus.provider_preflight.v1", "status": status.to_dict()})
        _record_preflight_attempt(provider, status, attempts, intensity=intensity)
        return status.status == "available"

    def _provider_for_intensity(self, store: RunStore, provider: HostModelProvider, project: Path, intensity: str) -> HostModelProvider | None:
        if intensity != "high":
            return provider
        try:
            input_payload = store.read_json("input.json")
        except Exception:
            input_payload = {}
        requested = str(input_payload.get("requested_provider") or input_payload.get("provider") or "auto")
        if requested != "auto":
            return provider
        selected = self._select_provider(store, "auto", project, intensity="high")
        return selected or provider

    def _block_for_provider_setup(self, store: RunStore) -> dict[str, object]:
        attempts_payload = _read_provider_attempts(store)
        attempts = attempts_payload.get("attempts") if isinstance(attempts_payload.get("attempts"), list) else []
        if attempts and any(str(item.get("stage") or "") == "preflight" and str(item.get("status") or "") != "available" for item in attempts if isinstance(item, dict)):
            failed = next((item for item in reversed(attempts) if isinstance(item, dict) and str(item.get("stage") or "") == "preflight" and str(item.get("status") or "") != "available"), {})
            provider = str(failed.get("provider") or "provider")
            self._write_state(store, "blocked", current_node="provider_preflight", blocked_reason="provider_preflight_failed", provider=provider)
            recovery_path = store.path("tool_results", "provider_recovery.json")
            pending = _provider_preflight_pending_actions(store.run_id, provider)
            recovery_payload = _read_json_if_exists(recovery_path)
            exhausted_text = "已达到当前恢复链路的自动尝试上限。" if bool(recovery_payload.get("exhausted")) else ""
            return write_interaction(
                store,
                status="blocked",
                output=f"provider preflight 失败：{provider}；Nexus 已进入通用恢复链路。高强度模型指导和 provider attempts 已写入 artifact。{exhausted_text}",
                next_prompt=f"$nexus-workflow 修复 {provider} preflight 权限问题并继续 {store.run_id}\n$nexus-workflow 跳过 {provider} fallback 继续 {store.run_id}",
                blocked_reason="provider_preflight_failed",
                pending_actions=pending,
                artifact_refs=[str(store.path("tool_results", "provider_attempts.json")), str(recovery_path)],
                lifecycle_status="awaiting_approval",
                recovery_mode=True,
            )
        self._write_state(store, "blocked", current_node="provider_preflight", blocked_reason="real_model_provider_not_configured")
        provider_status = {}
        status_path = provider_status_path(self.root)
        if status_path.exists():
            provider_status = json.loads(status_path.read_text(encoding="utf-8"))
        setup = {
            "schema": "nexus.provider_setup_required.v1",
            "blocked_reason": "real_model_provider_not_configured",
            "message": "未发现可用真实模型 provider；未使用 MockProvider。",
            "next_commands": [
                "python -m nexus.cli doctor",
                "python -m nexus.cli model configure",
                f"python -m nexus.cli resume {store.run_id}",
            ],
            "provider_status": provider_status,
            "model_status": profile_status(self.root),
        }
        setup_path = store.write_json("approvals/provider_setup_required.json", setup)
        requested = ""
        try:
            requested = str(store.read_json("input.json").get("provider") or "")
        except Exception:
            requested = ""
        summary = _provider_setup_summary(provider_status, requested=requested)
        return write_interaction(
            store,
            status="blocked",
            output=f"未发现可用真实模型 provider；未使用 MockProvider。{summary}",
            next_prompt=f"运行 python -m nexus.cli model configure 优先配置 API profile；如果 API 不可用，再检查 codex-cli，最后检查 codex-mcp。配置完成后运行 python -m nexus.cli resume {store.run_id}。",
            blocked_reason="real_model_provider_not_configured",
            artifact_refs=[str(setup_path), str(store.path("tool_results", "provider_status.json"))],
        )

    def _execute_workflow(
        self,
        store: RunStore,
        *,
        provider: HostModelProvider,
        idea: str,
        project: Path,
        max_candidates: int,
    ) -> dict[str, object]:
        try:
            self._write_state(store, "running", current_node="repo_scan", provider=provider.name)
            checkpoints = CheckpointManager(store, force_node=self._force_node, from_node=self._resume_from_node)
            repo_scan_input = {"project": str(project)}
            repo_scan_path = store.path("tool_results", "repo_scan.json")
            if checkpoints.completed("repo_scan", repo_scan_input, [repo_scan_path]):
                repo_scan = store.read_json("tool_results/repo_scan.json")
            else:
                with checkpoints.running("repo_scan", kind="tool", input_payload=repo_scan_input):
                    repo_scan = scan_repo(project)
                    store.write_json("tool_results/repo_scan.json", repo_scan)
                checkpoints.mark("repo_scan", kind="tool", status="completed", input_payload=repo_scan_input, output_refs=[repo_scan_path])
            high_provider = self._provider_for_intensity(store, provider, project, "high") or provider
            intent_route = self._model_node(store, high_provider, "intent_route", _intent_prompt(idea, repo_scan))
            store.write_json("reports/intent_route.json", intent_route)
            task_block = self._model_node(store, high_provider, "task_block", _task_prompt(idea, repo_scan))
            research_contract = build_research_contract(idea=idea, project=project, repo_scan=repo_scan, intent_route=intent_route, task_block=task_block)
            contract_json_path = store.write_json("reports/research_contract.json", research_contract)
            contract_md_path = store.write_text("reports/research_contract.md", render_research_contract(research_contract))
            research_plan = self._model_node(store, high_provider, "research_plan", _research_prompt(idea, task_block, research_contract))
            all_candidates, source_statuses, blocked = self._run_search_loop(
                store,
                provider=provider,
                idea=idea,
                project=project,
                repo_scan=repo_scan,
                research_plan=research_plan,
                research_contract=research_contract,
                max_candidates=max_candidates,
            )
            candidates = [candidate.to_dict() for candidate in all_candidates[:max_candidates]]
            store.write_jsonl("candidates/candidates.jsonl", candidates)
            store.write_jsonl("candidates/all_candidates.jsonl", [candidate.to_dict() for candidate in all_candidates])
            store.write_json(
                "tool_results/source_status.json",
                {"schema": "nexus.source_status.v1", "sources": [status.to_dict() for status in source_statuses]},
            )
            if blocked:
                approval = {
                    "schema": "nexus.online_search_approval.v1",
                    "message": "模型检索计划需要只读在线检索；本地检索与 blocked source status 已写入。",
                    "allowed_scope": "只读访问公开 GitHub/MCP registry/官方文档/Gitee/中文资料；禁止登录、读取凭据、绕过验证码/403/限流、提交表单或写目标项目。",
                    "command": f"python -m nexus.cli approve {store.run_id} online-search",
                }
                approval_path = store.write_json("approvals/online_search_required.json", approval)
                prompt_path = self._write_external_prompt(store, idea, research_plan, source_statuses)
                self._write_state(store, "blocked", current_node="online_search_approval", blocked_reason="online_search_approval_required", provider=provider.name)
                return write_interaction(
                    store,
                    status="blocked",
                    output="本地检索已完成；模型建议进行只读在线检索以覆盖 GitHub/MCP/中文资料等来源。",
                    next_prompt=_multi_option_prompt(
                        [
                            f"选项 1：允许只读在线检索 python -m nexus.cli approve {store.run_id} online-search，然后 python -m nexus.cli resume {store.run_id}",
                            f"选项 2：使用外部 GPT 调研 prompt：{prompt_path}",
                            "选项 3：只使用当前本地结果生成报告",
                        ]
                    ),
                    blocked_reason="online_search_approval_required",
                    approval_request=approval,
                    artifact_refs=[str(approval_path), str(prompt_path)],
                )
            checked_candidates = check_candidates(all_candidates[:max_candidates])
            candidates = [candidate.to_dict() for candidate in checked_candidates]
            store.write_jsonl("availability/candidates.jsonl", candidates)
            localization_review = self._model_node_with_schema(
                store,
                provider,
                "candidate_localization_review",
                "candidate_localization_review",
                _localization_prompt(idea, candidates, source_statuses),
            )
            store.write_json("candidates/localization_review.json", localization_review)
            candidates = _merge_localization(candidates, localization_review)
            store.write_jsonl("candidates/candidates.jsonl", candidates)
            candidate_processing = process_candidates(candidates, max_candidates=max_candidates)
            store.write_jsonl("candidates/normalized_candidates.jsonl", candidate_processing["normalized"])
            store.write_json("candidates/merged_candidates.json", {"schema": "nexus.merged_candidates.v1", "candidates": candidate_processing["merged"]})
            store.write_json("candidates/ranking_features.json", {"schema": "nexus.ranking_features.v1", "features": candidate_processing["ranking_features"]})
            store.write_json("candidates/model_review_input.json", {"schema": "nexus.model_review_input.v1", "candidates": candidate_processing["model_input"]})
            candidate_review = self._model_node(store, provider, "candidate_review", _review_prompt(idea, candidate_processing["model_input"], source_statuses, research_contract))
            ranked = rerank_candidates(candidate_processing["merged"], candidate_review)
            store.write_json("candidates/ranked_candidates.json", {"schema": "nexus.ranked_candidates.v1", "candidates": ranked})
            risk_analysis = self._model_node(store, provider, "risk_analysis", _risk_prompt(idea, ranked, research_contract))
            final_report = self._model_node(store, high_provider, "final_report", _report_prompt(idea, ranked, risk_analysis, research_contract))
            branch_artifacts = build_branch_research_artifacts(
                contract=research_contract,
                ranked=ranked,
                final_report=final_report,
                risk_analysis=risk_analysis,
                source_statuses=[status.to_dict() for status in source_statuses],
            )
            branch_refs = self._write_branch_research_artifacts(store, branch_artifacts)
            report_path = self._write_report(store, final_report, ranked, high_provider.name, research_contract=research_contract, branch_refs=branch_refs)
            next_path = self._write_next_action(store, final_report, risk_analysis)
            weak_points = _weak_points_from_statuses(source_statuses)
            completion_quality = "partial" if weak_points else "complete"
            next_options_path = self._write_next_options(
                store,
                stage="discovery_completed",
                completion_quality=completion_quality,
                summary=str(final_report.get("summary") or "discovery 已完成"),
                weak_points=weak_points,
                research_contract=research_contract,
                branch_refs=branch_refs,
            )
            high_risk = detect_high_risk_actions(" ".join(str(item) for item in final_report.get("next_action_plan", [])))
            if (high_risk or bool(risk_analysis.get("approval_required"))) and not bool(research_contract.get("requires_branch_reports")):
                approval = {
                    "schema": "nexus.approval_request.v1",
                    "blocked_actions": high_risk or risk_analysis.get("blocked_actions", []),
                    "message": "下一步包含高风险或写入动作，必须确认后继续。",
                }
                approval_path = store.write_json("approvals/implementation_plan_required.json", approval)
                self._write_state(store, "blocked", current_node="approval", blocked_reason="approval_required", provider=provider.name)
                update_board(project, status="discovery 完成，等待 implementation-plan 审批", point=f"run {store.run_id} 完成调研但进入审批阻断")
                return write_interaction(
                    store,
                    status="blocked",
                    output="已完成 discovery 和报告；下一步包含需要确认的动作。",
                    next_prompt=_multi_option_prompt(
                        [
                            f"选项 1：审阅 {approval_path} 后运行 python -m nexus.cli approve {store.run_id} implementation-plan",
                            "选项 2：继续只读调研并补充更具体范围",
                            "选项 3：生成外部 GPT 调研 prompt",
                            f"选项 4：一句话继续 python -m nexus.cli continue {store.run_id} \"生成项目计划\"",
                        ]
                    ),
                    blocked_reason="approval_required",
                    approval_request=approval,
                    artifact_refs=[str(report_path), str(next_path), str(next_options_path), str(approval_path), str(contract_json_path), str(contract_md_path), *[str(item) for item in branch_refs.values()]],
                )
            self._write_state(store, "completed", current_node="done", provider=provider.name)
            update_board(project, status="discovery 完成", point=f"run {store.run_id} 已完成 discovery")
            return write_interaction(
                store,
                status="completed",
                output=f"已完成中文互联网优先的只读 discovery；模型 provider：{provider.name}；报告已生成。",
                next_prompt=_discovery_next_prompt(store, research_contract),
                artifact_refs=[str(report_path), str(next_path), str(next_options_path), str(contract_json_path), str(contract_md_path), *[str(item) for item in branch_refs.values()]],
            )
        except (ProviderUnavailable, ProviderExecutionError, ValueError) as exc:
            if provider.name == "codex-mcp" and self._should_runtime_fallback_to_codex_cli(store):
                fallback_marker = store.write_json(
                    "tool_results/fallback_codex_cli.json",
                    {
                        "schema": "nexus.provider_runtime_fallback.v1",
                        "from_provider": provider.name,
                        "to_provider": "codex-cli",
                        "reason": str(exc),
                    },
                )
                fallback_provider = self._select_provider(store, "codex-cli", project)
                if fallback_provider is not None:
                    store.append_audit("provider_runtime_fallback", {"from": provider.name, "to": fallback_provider.name, "reason": str(exc), "artifact": str(fallback_marker)})
                    return self._execute_workflow(store, provider=fallback_provider, idea=idea, project=project, max_candidates=max_candidates)
            self._write_state(store, "failed", current_node="model_or_validation", blocked_reason=str(exc), provider=provider.name)
            last_model_provider = _read_json_if_exists(store.path("tool_results/last_model_provider.json"))
            failed_provider = str(last_model_provider.get("provider") or provider.name)
            failed_node = str(last_model_provider.get("node_id") or "")
            target = _next_fallback_provider_name(self.root, project, failed_provider, intensity="high")
            runtime_status_path = store.path("tool_results", f"provider_runtime_status_{_safe_node_suffix(failed_node)}.json") if failed_node else None
            pending = []
            if target:
                pending.append(
                    {
                        "action_id": "skip_current_provider_and_continue",
                        "kind": "provider_fallback_approval",
                        "skip_provider": failed_provider,
                        "target_provider": target,
                        "requires_host_permission": False,
                        "command": f"python -m nexus.cli recover {store.run_id} \"跳过 {failed_provider} fallback 到 {target} 并继续\"",
                    }
                )
            chain_path = _append_provider_fallback_chain(
                store,
                {
                    "event": "provider_runtime_failed",
                    "status": "blocked",
                    "node_id": failed_node,
                    "failed_provider": failed_provider,
                    "target_provider": target,
                    "reason": str(exc),
                    "error_type": exc.__class__.__name__,
                    "runtime_status_path": str(runtime_status_path) if runtime_status_path and runtime_status_path.exists() else "",
                    "recovery_command": pending[0]["command"] if pending else "",
                    "environment_skipped_providers": _env_skip_providers(),
                },
            )
            store.append_audit("provider_fallback_chain_updated", {"event": "provider_runtime_failed", "artifact": str(chain_path), "failed_provider": failed_provider, "target_provider": target})
            self._write_state(store, "blocked", current_node="model_or_validation", blocked_reason="provider_runtime_failed", provider=failed_provider)
            return write_interaction(
                store,
                status="blocked",
                output=f"provider runtime 失败：{failed_provider}；{str(exc)}。未使用 mock，等待用户批准 fallback 或修复 provider。",
                next_prompt=f"$nexus-workflow 跳过 {failed_provider} fallback 到 {target or '<下一个真实 provider>'} 并继续 {store.run_id}" if target else "配置可用真实 provider 后恢复原 run。",
                blocked_reason="provider_runtime_failed",
                pending_actions=pending,
                lifecycle_status="awaiting_approval",
                recovery_mode=True,
            )

    def _should_runtime_fallback_to_codex_cli(self, store: RunStore) -> bool:
        if store.path("tool_results", "fallback_codex_cli.json").exists():
            return False
        try:
            input_payload = store.read_json("input.json")
        except Exception:
            return False
        return str(input_payload.get("requested_provider") or "auto") == "auto"

    def _model_node(self, store: RunStore, provider: HostModelProvider, node_id: str, prompt: str) -> dict[str, Any]:
        return self._model_node_with_schema(store, provider, node_id, node_id, prompt)

    def _model_node_with_schema(self, store: RunStore, provider: HostModelProvider, node_id: str, schema_key: str, prompt: str) -> dict[str, Any]:
        checkpoints = CheckpointManager(store, force_node=self._force_node, from_node=self._resume_from_node)
        request = ModelRequest(
            node_id=node_id,
            purpose=f"生成 {node_id} 的结构化 JSON。",
            prompt=prompt,
            schema=SCHEMAS[schema_key],
            context_refs=[str(store.run_dir)],
            safety_boundary=safety_boundary(),
        )
        request_path = store.path("model_requests", node_id, "model_request.json")
        response_path = store.path("model_responses", node_id, "model_response.json")
        validated_path = store.path("model_responses", node_id, "validated_response.json")
        if checkpoints.completed(node_id, request.to_dict(), [validated_path]):
            return store.read_json(f"model_responses/{node_id}/validated_response.json")
        runtime_status_path = store.path("tool_results", f"provider_runtime_status_{_safe_node_suffix(node_id)}.json")
        _write_wait_observation(store, node_id=node_id, decision="long_running_model_node_started", wait_seconds=120, runtime_status_path=runtime_status_path)
        self._write_state(store, "running", current_node=node_id, provider=provider.name, node_kind="model", model_node_status="started", runtime_status_path=str(runtime_status_path))
        try:
            with checkpoints.running(node_id, kind="model", input_payload=request.to_dict(), provider=provider.name):
                store.write_json(f"model_requests/{node_id}/model_request.json", request.to_dict())
                store.write_json("tool_results/last_model_provider.json", {"schema": "nexus.last_model_provider.v1", "provider": provider.name, "node_id": node_id, "intensity_note": "actual provider used for the current model node"})
                previous_runtime_status_path = getattr(provider, "runtime_status_path", None)
                if hasattr(provider, "runtime_status_path"):
                    setattr(provider, "runtime_status_path", runtime_status_path)
                try:
                    response = provider.complete_json(request)
                finally:
                    if hasattr(provider, "runtime_status_path"):
                        setattr(provider, "runtime_status_path", previous_runtime_status_path)
                store.write_json(f"model_responses/{node_id}/model_response.json", response.to_dict())
                validated = validate_json(response.json_data, SCHEMAS[schema_key])
                if schema_key == "project_name_candidates":
                    validated = _validate_project_name_candidates(validated)
                store.write_json(f"model_responses/{node_id}/validated_response.json", validated)
        except Exception as exc:
            self._write_state(store, "failed", current_node=node_id, blocked_reason=str(exc), provider=provider.name, node_kind="model", model_node_status="failed", runtime_status_path=str(runtime_status_path))
            raise
        checkpoints.mark(node_id, kind="model", status="completed", input_payload=request.to_dict(), output_refs=[request_path, response_path, validated_path], provider=provider.name)
        store.append_audit("model_node_completed", {"node_id": node_id, "provider": provider.name})
        self._write_state(store, "running", current_node=node_id, provider=provider.name, node_kind="model", model_node_status="completed", runtime_status_path=str(runtime_status_path))
        return validated

    def _resolve_provider_name(self, provider_name: str, idea: str, model_name: str = "") -> str:
        requested = (model_name or detect_model_name(idea)).strip()
        if requested:
            profile = resolve_profile(self.root, requested)
            return profile.name if profile is not None else requested
        if provider_name != "auto":
            return provider_name
        return "auto"

    def _run_search_loop(
        self,
        store: RunStore,
        *,
        provider: HostModelProvider,
        idea: str,
        project: Path,
        repo_scan: dict[str, object],
        research_plan: dict[str, object],
        research_contract: dict[str, object],
        max_candidates: int,
    ) -> tuple[list[CandidateRecord], list[SourceStatus], bool]:
        service = SearchService()
        online_allowed = self._online_search_approved(store)
        all_candidates: list[CandidateRecord] = []
        all_statuses: list[SourceStatus] = []
        max_rounds = 3
        blocked = False
        previous_round_summary = ""
        for round_no in range(1, max_rounds + 1):
            round_provider = self._provider_for_intensity(store, provider, project, "high") if round_no == 1 else provider
            round_provider = round_provider or provider
            search_plan = self._model_node_with_schema(
                store,
                round_provider,
                f"search_plan_round_{round_no}",
                "search_plan",
                _search_plan_prompt(idea, research_plan, research_contract, round_no, previous_round_summary),
            )
            if online_allowed:
                search_plan = _ensure_online_sources(search_plan, research_plan, research_contract)
            round_dir = f"search_rounds/round_{round_no}"
            store.write_json(f"{round_dir}/search_plan.json", search_plan)
            tool_node = f"tool_search_round_{round_no}"
            checkpoints = CheckpointManager(store, force_node=self._force_node, from_node=self._resume_from_node)
            tool_input = {"round_no": round_no, "search_plan": search_plan, "project": str(project), "online_allowed": online_allowed}
            candidates_path = store.path(round_dir, "candidates.jsonl")
            statuses_path = store.path(round_dir, "source_status.json")
            if checkpoints.completed(tool_node, tool_input, [candidates_path, statuses_path]):
                round_candidates = [_candidate_from_dict(json.loads(line)) for line in candidates_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                status_payload = json.loads(statuses_path.read_text(encoding="utf-8"))
                round_statuses = [_status_from_dict(item) for item in status_payload.get("sources", []) if isinstance(item, dict)]
                result = type("SearchRoundReplay", (), {"candidates": round_candidates, "statuses": round_statuses, "online_blocked": any(status.online_search_blocked for status in round_statuses)})()
            else:
                with checkpoints.running(tool_node, kind="tool", input_payload=tool_input):
                    result = service.execute_round(
                        round_no=round_no,
                        search_plan=search_plan,
                        project_path=project,
                        repo_scan=repo_scan,
                        online_allowed=online_allowed,
                        raw_dir=store.path(round_dir, "raw"),
                    )
                    store.write_jsonl(f"{round_dir}/candidates.jsonl", [candidate.to_dict() for candidate in result.candidates])
                    store.write_json(f"{round_dir}/source_status.json", {"schema": "nexus.source_status.v1", "sources": [status.to_dict() for status in result.statuses]})
                checkpoints.mark(tool_node, kind="tool", status="completed", input_payload=tool_input, output_refs=[candidates_path, statuses_path])
            all_candidates = _dedupe_candidate_records([*all_candidates, *result.candidates])
            all_statuses.extend(result.statuses)
            coverage = self._model_node_with_schema(
                store,
                provider,
                f"coverage_review_round_{round_no}",
                "coverage_review",
                _coverage_prompt(round_no, result.candidates, result.statuses, research_contract),
            )
            store.write_json(f"{round_dir}/coverage_review.json", coverage)
            stop = self._model_node_with_schema(
                store,
                provider,
                f"stop_decision_round_{round_no}",
                "stop_decision",
                _stop_prompt(round_no, coverage, result.candidates, result.statuses, research_contract),
            )
            store.write_json(f"{round_dir}/stop_decision.json", stop)
            previous_round_summary = json.dumps(
                {
                    "round_no": round_no,
                    "candidate_count": len(result.candidates),
                    "statuses": [status.to_dict() for status in result.statuses],
                    "coverage": coverage,
                    "stop": stop,
                },
                ensure_ascii=False,
            )
            if result.online_blocked and not online_allowed:
                blocked = True
                break
            if not bool(stop.get("should_continue")):
                break
            if len(all_candidates) >= max_candidates and round_no >= 2:
                break
        store.write_jsonl("candidates/deduped_candidates.jsonl", [candidate.to_dict() for candidate in all_candidates])
        return all_candidates, all_statuses, blocked

    def approve(self, run_id: str, stage: str) -> dict[str, object]:
        store = RunStore(self.root, run_id)
        state = store.read_json("state.json")
        if stage == "project-root":
            if state.get("status") != "blocked" or state.get("blocked_reason") != "project_root_approval_required":
                return write_interaction(
                    store,
                    status=str(state.get("status") or "unknown"),
                    output="当前 run 不在 project-root 审批阻断状态。",
                    next_prompt=f"查看状态：python -m nexus.cli status {run_id}",
                )
            target = Path(str(state.get("target_path") or "")).expanduser()
            if not target:
                raise ValueError("target_path missing from project-root state")
            if target.exists() and not target.is_dir():
                raise ValueError(f"target_path exists but is not a directory: {target}")
            if not target.exists():
                target.mkdir(parents=True, exist_ok=False)
            (target / ".nexus").mkdir(parents=True, exist_ok=True)
            update_board(target, status="项目目录已创建", point=f"nexus 创建项目目录：{target.name}")
            self._write_active_project(target, source="project-root", run_id=store.run_id)
            input_payload = store.read_json("input.json") if store.path("input.json").exists() else {}
            approval_payload = store.read_json("approvals/project_root_required.json") if store.path("approvals/project_root_required.json").exists() else {}
            raw_user_request = str(input_payload.get("raw_user_request") or input_payload.get("idea") or "")
            normalized_request = str(input_payload.get("normalized_request") or input_payload.get("idea") or "")
            github_sync_enabled = bool(input_payload.get("github_sync", True))
            feishu_sync_enabled = bool(input_payload.get("feishu_sync", True))
            github_private_repo = str(input_payload.get("private_repo") or "")
            github_public_repo = str(input_payload.get("public_repo") or "")
            github_result: dict[str, object] | None = None
            github_path: Path | None = None
            public_result: dict[str, object] | None = None
            public_path: Path | None = None
            public_interaction: dict[str, object] | None = None
            github_interaction: dict[str, object] | None = None
            auth_refs: list[str] = []
            if github_sync_enabled:
                if not github_private_repo:
                    github_private_repo, default_public_repo = _default_github_repos_for_new_project(target)
                    github_public_repo = github_public_repo or default_public_repo
                write_github_sync_config(target, github_private_repo, github_public_repo, project_kind="user_project")
            marker = store.write_json(
                "approvals/APPROVED_project-root.json",
                {
                    "schema": "nexus.approval_marker.v1",
                    "stage": stage,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "created_path": str(target),
                },
            )
            project_name_context = _project_name_context_from_approval(approval_payload, target)
            docs_bundle = write_project_docs(
                target,
                raw_user_request,
                private_repo=github_private_repo,
                public_repo=github_public_repo,
                github_sync_enabled=github_sync_enabled,
                feishu_sync_enabled=feishu_sync_enabled,
                run_id=store.run_id,
                project_name_context=project_name_context,
                normalized_idea=normalized_request,
                input_trace={
                    "schema": "nexus.project_input_trace.v1",
                    "raw_user_request_recorded": bool(raw_user_request),
                    "normalized_request_recorded": bool(normalized_request),
                    "operational_idea_differs_from_raw": str(input_payload.get("idea") or "") != raw_user_request,
                    "name_source": approval_payload.get("name_source") or "",
                },
            )
            docs_bundle_path = store.write_json("tool_results/project_docs_bundle.json", docs_bundle)
            github_guide_result = write_github_sync_guide(target)
            github_guide_path = store.write_json("tool_results/github_sync_guide.json", github_guide_result)
            if github_sync_enabled:
                github_config = load_github_sync_config(target) or {}
                github_interaction = self._github_bootstrap_flow(
                    store,
                    target,
                    private_repo=str(github_config.get("private_repo") or github_private_repo),
                    public_repo=str(github_config.get("public_repo") or github_public_repo),
                    create_remote_repos=True,
                    commit_message=f"bootstrap {target.name}",
                    continuation_operation="github_bootstrap",
                    continuation_note="project_init",
                )
                github_path = store.path("tool_results", "github_bootstrap.json") if store.path("tool_results", "github_bootstrap.json").exists() else None
                github_result = _github_result_from_interaction(github_interaction, github_path)
                auth_refs = [str(item) for item in github_interaction.get("artifact_refs", []) if str(item)]
                if github_result.get("status") == "completed":
                    public_interaction = self._github_public_flow(store, target, confirm=False, continuation_note="project_init")
                    public_path = store.path("tool_results", "github_public_sync.json") if store.path("tool_results", "github_public_sync.json").exists() else None
                    public_result = _public_result_from_interaction(public_interaction, public_path)
            feishu_record_path: Path | None = None
            feishu_autowrite_status = ""
            feishu_autowrite_reason = ""
            autowrite_changed_paths: list[str] = []
            if bool(state.get("enable_feishu_autowrite")) or bool(input_payload.get("enable_feishu_autowrite")):
                provider = self._select_provider(store, str(state.get("provider") or input_payload.get("provider") or "auto"), target)
                if provider is None:
                    feishu_autowrite_status = "provider_blocked"
                    feishu_autowrite_reason = "real_model_provider_not_configured"
                else:
                    try:
                        record_content = self._model_node_with_schema(
                            store,
                            provider,
                            "feishu_project_init_record",
                            "feishu_record_content",
                            _feishu_record_prompt(target, "项目初始化记录", f"项目 {target.name} 已由 nexus 初始化，请生成一条飞书在线文档记录。", load_board(target), scan_repo(target)),
                        )
                        store.write_json("tool_results/feishu_project_init_record_content.json", record_content)
                        local_record_path = append_feishu_record_markdown(
                            target,
                            title=str(record_content.get("title") or f"{target.name} 初始化记录"),
                            content=str(record_content.get("markdown") or f"项目 {target.name} 已初始化。"),
                            event_type="project_init_autowrite",
                            source_run_id=store.run_id,
                        )
                        feishu_record = {
                            "schema": "nexus.local_feishu_record.v1",
                            "status": "completed",
                            "reason": "local_markdown_record_written",
                            "path": str(local_record_path),
                        }
                        feishu_record_path = store.write_json("tool_results/feishu_project_init_record.json", feishu_record)
                        autowrite_changed_paths.append(str(local_record_path))
                        feishu_autowrite_status = str(feishu_record.get("status") or "")
                        feishu_autowrite_reason = str(feishu_record.get("reason") or "")
                    except (ProviderUnavailable, ProviderExecutionError, ValueError) as exc:
                        feishu_autowrite_status = "blocked"
                        feishu_autowrite_reason = "feishu_project_init_record_model_failed"
                        feishu_record_path = store.write_json("tool_results/feishu_project_init_record_error.json", {"schema": "nexus.feishu_project_init_record_error.v1", "provider": provider.name, "message": str(exc)})
            autosync, autosync_path, _, _ = self._sync_project_docs_to_feishu(
                store,
                target,
                event_type="project_init",
                title="项目初始化记录",
                summary=f"项目 {target.name} 已初始化，已生成项目意图文档、项目说明和整体操作指南。",
                changed_paths=[*docs_bundle.get("paths", []), str(github_guide_result.get("path") or ""), *autowrite_changed_paths],
                guide_paths=[path for path in [github_guide_result.get("path")] if isinstance(path, str) and path],
                enabled=feishu_sync_enabled,
                artifact_name="feishu_autosync.json",
            )
            feishu_doctor = autosync.get("setup") if feishu_sync_enabled and isinstance(autosync.get("setup"), dict) else ({"status": "skipped", "reason": "feishu_sync_disabled"} if not feishu_sync_enabled else run_feishu_setup(target, no_network=True))
            feishu_path = store.write_json("tool_results/feishu_autostart.json", feishu_doctor)
            feishu_guide_status = "merged_into_feishu_autosync" if feishu_sync_enabled else "skipped"
            feishu_guide_reason = str(autosync.get("reason") or ("feishu_sync_disabled" if not feishu_sync_enabled else ""))
            if feishu_sync_enabled and feishu_doctor.get("status") != "completed":
                guide_path = store.write_json("tool_results/feishu_setup_guide.json", build_setup_guide())
                approval_path = store.write_json("approvals/feishu_publish_required.json", build_publish_required_approval())
                refs = [str(marker), str(target / ".nexus" / "board.md"), str(docs_bundle_path), *[path for path in docs_bundle.get("paths", []) if isinstance(path, str)], str(github_guide_path), str(github_guide_result.get("path")), str(feishu_path), str(autosync_path), str(guide_path), str(approval_path)]
                next_prompt = (
                    f"项目已创建；飞书在线文档未就绪。下一步先输入“使用 $nexus-workflow 初始化飞书配置”，"
                    f"或运行 python -m nexus.cli feishu setup --project-path {target}。"
                )
            else:
                refs = [str(marker), str(target / ".nexus" / "board.md"), str(docs_bundle_path), *[path for path in docs_bundle.get("paths", []) if isinstance(path, str)], str(github_guide_path), str(github_guide_result.get("path")), str(feishu_path), str(autosync_path)]
                next_prompt = (
                    f"飞书本地配置检查通过；下一步可以运行 python -m nexus.cli run \"调研项目初始化方案\" --project-path {target}"
                    if feishu_sync_enabled
                    else f"项目已创建，并按要求跳过飞书同步；下一步可以运行 python -m nexus.cli run \"调研项目初始化方案\" --project-path {target}"
                )
            if github_path is not None:
                refs.append(str(github_path))
                if public_interaction is not None:
                    refs.extend(str(item) for item in public_interaction.get("artifact_refs", []) if str(item))
                refs.extend(ref for ref in auth_refs if ref)
                if github_result and github_result.get("status") != "completed":
                    if github_result.get("reason") in {"LOGIN_INCOMPLETE", "GH_NOT_FOUND"}:
                        next_prompt = f"项目已创建，但 GitHub 登录尚未完成；完成登录后运行 python -m nexus.cli github-sync bootstrap --project-path {target}。"
                    else:
                        next_prompt = f"项目已创建，但 GitHub private 初始化/同步阻断：{github_result.get('reason')}；请修复 gh/repo 权限后运行 github-sync bootstrap。"
                elif public_result and public_result.get("status") != "completed":
                    if public_interaction and public_interaction.get("next_task_prompt"):
                        next_prompt = str(public_interaction.get("next_task_prompt") or "")
                    else:
                        next_prompt = f"项目已创建，GitHub private 已完成，但首次 GitHub public 同步阻断：{public_result.get('reason')}；请审阅 public staging/secret scan 后重试 github-sync public。"
            if feishu_record_path is not None:
                refs.append(str(feishu_record_path))
                next_prompt = f"飞书初始化记录结果：{feishu_autowrite_status}；下一步可以调研或继续飞书记录。"
            elif feishu_autowrite_status == "provider_blocked":
                next_prompt = "项目已创建，但飞书初始化记录需要真实模型 provider；请运行 python -m nexus.cli model status/configure 后再记录。"
            final_status = "completed"
            blocked_reason = ""
            if feishu_autowrite_status and feishu_autowrite_status != "completed":
                final_status = "blocked"
                blocked_reason = feishu_autowrite_reason or "feishu_autowrite_blocked"
            if feishu_sync_enabled and autosync.get("status") == "blocked":
                final_status = "blocked"
                blocked_reason = str(autosync.get("reason") or "feishu_autosync_blocked")
            if github_result and github_result.get("status") != "completed":
                final_status = "blocked"
                blocked_reason = str(github_result.get("reason") or "github_bootstrap_blocked")
            if public_result and public_result.get("status") != "completed":
                final_status = "blocked"
                blocked_reason = str(public_result.get("reason") or "github_public_sync_blocked")
            recovery_source = public_interaction if public_interaction and public_result and public_result.get("status") != "completed" else (github_interaction if github_result and github_result.get("status") != "completed" else None)
            recovery_kwargs = _recovery_kwargs_from_interaction(recovery_source)
            self._write_state(store, final_status, current_node="project_root_created", target_path=str(target), feishu_sync=feishu_sync_enabled, feishu_autostart_status=str(feishu_doctor.get("status") or "unknown"), feishu_autosync_status=str(autosync.get("status") or "unknown"), feishu_github_guide_status=feishu_guide_status, feishu_github_guide_reason=feishu_guide_reason, feishu_autowrite_status=feishu_autowrite_status, github_bootstrap_status=str((github_result or {}).get("status") or ("skipped" if not github_sync_enabled else "not_configured")), github_public_init_status=str((public_result or {}).get("status") or ("skipped" if not github_sync_enabled else "not_attempted")), blocked_reason=blocked_reason, continuation=recovery_kwargs.get("continuation") or {})
            interaction = write_interaction(
                store,
                status=final_status,
                output=f"已创建项目目录：{target}；项目意图文档：completed；GitHub private 初始化：{(github_result or {}).get('status') or ('skipped' if not github_sync_enabled else 'not_configured')}；GitHub public 首次同步：{(public_result or {}).get('status') or ('skipped' if not github_sync_enabled else 'not_attempted')}；GitHub 指南：{github_guide_result.get('status')}；飞书自动同步：{autosync.get('status')}；已默认检查飞书在线文档能力，结果：{feishu_doctor.get('status')}；飞书初始化记录：{feishu_autowrite_status or 'not_requested'}",
                next_prompt=next_prompt,
                blocked_reason=blocked_reason,
                artifact_refs=refs,
                **recovery_kwargs,
            )
            _record_successful_recovery_outcome(
                store,
                interaction,
                recovered_by="project_root_approval",
                action_applied="approve_project_root_after_recovery",
            )
            approval_path = _write_recovery_playbook_approval_if_completed(store, {"project_path": str(target)})
            if approval_path is not None:
                return _with_recovery_playbook_prompt(
                    store,
                    interaction,
                    {"approval_path": str(approval_path), "artifact_refs": [str(approval_path)]},
                )
            return interaction

        if stage == "online-search":
            if state.get("status") != "blocked" or state.get("blocked_reason") != "online_search_approval_required":
                return write_interaction(
                    store,
                    status=str(state.get("status") or "unknown"),
                    output="当前 run 不在 online-search 审批阻断状态。",
                    next_prompt=f"查看状态：python -m nexus.cli status {run_id}",
                )
            marker = self._write_online_search_approval_marker(store, "approve online-search")
            self._write_state(store, "blocked", current_node="online_search_approved", blocked_reason="resume_required_after_online_search_approval", provider=state.get("provider", ""))
            return write_interaction(
                store,
                status="blocked",
                output="已记录 online-search 审批；下一步 resume 将执行只读在线检索。",
                next_prompt=f"运行 python -m nexus.cli resume {run_id}",
                blocked_reason="resume_required_after_online_search_approval",
                artifact_refs=[str(marker)],
            )

        if stage == "conversation-session-read":
            if state.get("status") != "blocked" or state.get("blocked_reason") != "conversation_session_read_approval_required":
                return write_interaction(
                    store,
                    status=str(state.get("status") or "unknown"),
                    output="当前 run 不在 conversation-session-read 审批阻断状态。",
                    next_prompt=f"查看状态：python -m nexus.cli status {run_id}",
                )
            marker = store.write_json(
                "approvals/APPROVED_conversation-session-read.json",
                {
                    "schema": "nexus.approval_marker.v1",
                    "stage": stage,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "scope": "允许读取已选择的本地 Codex session 文件并进行敏感信息脱敏；不允许读取 token/cookie/SSH key/private profile。",
                },
            )
            self._write_state(store, "blocked", current_node="conversation_session_read_approved", blocked_reason="resume_required_after_conversation_session_read_approval")
            return write_interaction(
                store,
                status="blocked",
                output="已记录 conversation session 读取审批；下一步 resume 将导入并脱敏选中的对话。",
                next_prompt=f"运行 python -m nexus.cli resume {run_id}",
                blocked_reason="resume_required_after_conversation_session_read_approval",
                artifact_refs=[str(marker)],
            )

        if stage == "git-baseline":
            if state.get("status") != "blocked" or state.get("blocked_reason") != "git_baseline_approval_required":
                return write_interaction(
                    store,
                    status=str(state.get("status") or "unknown"),
                    output="当前 run 不在 git-baseline 审批阻断状态。",
                    next_prompt=f"先运行 python -m nexus.cli prepare-project <project_path>。",
                )
            input_payload = store.read_json("input.json")
            project = Path(str(input_payload.get("project_path") or "")).expanduser().resolve()
            result = create_git_baseline(project)
            result_path = store.write_json("tool_results/git_baseline_result.json", result)
            if result.get("status") != "completed":
                self._write_state(store, "blocked", current_node="git_baseline", blocked_reason=str(result.get("reason") or "git_baseline_failed"))
                return write_interaction(
                    store,
                    status="blocked",
                    output=f"git baseline 未完成：{result.get('reason')}",
                    next_prompt="请处理敏感文件或 git 配置问题后重试。",
                    blocked_reason=str(result.get("reason") or "git_baseline_failed"),
                    artifact_refs=[str(result_path)],
                )
            update_board(project, status="git baseline 已建立", point=f"run {store.run_id} 已建立 git baseline")
            self._write_state(store, "completed", current_node="git_baseline_created", project_path=str(project))
            return write_interaction(
                store,
                status="completed",
                output=f"已为目标项目建立 git baseline：{project}，commit={result.get('commit', '')}",
                next_prompt="现在可以继续 nexus 的 implementation plan -> code-change -> diff -> apply -> test 链路。",
                artifact_refs=[str(result_path)],
            )

        if stage == "code-change":
            if state.get("status") != "blocked" or state.get("blocked_reason") != "code_change_approval_required":
                return write_interaction(
                    store,
                    status=str(state.get("status") or "unknown"),
                    output="当前 run 不在 code-change 二次确认状态。",
                    next_prompt=f"先生成实现计划：python -m nexus.cli plan-implementation {run_id}",
                )
            marker = store.write_json(
                "approvals/APPROVED_code-change.json",
                {
                    "schema": "nexus.approval_marker.v1",
                    "stage": stage,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "scope": "允许在隔离 git worktree 中调用 Codex 修改副本并生成 diff；不得直接写目标项目。",
                },
            )
            self._write_state(store, "blocked", current_node="code_change_approved", blocked_reason="execute_code_change_required", provider=state.get("provider", ""))
            return write_interaction(
                store,
                status="blocked",
                output="已记录 code-change 审批；下一步将在隔离 worktree 中调用 Codex 生成 diff。",
                next_prompt=f"运行 python -m nexus.cli execute-code-change {run_id} --provider codex-cli",
                blocked_reason="execute_code_change_required",
                artifact_refs=[str(marker)],
            )

        if stage == "apply":
            if state.get("status") != "blocked" or state.get("blocked_reason") != "apply_approval_required":
                return write_interaction(
                    store,
                    status=str(state.get("status") or "unknown"),
                    output="当前 run 不在 apply 审批状态。",
                    next_prompt=f"先查看 diff：python -m nexus.cli diff {run_id}",
                )
            marker = store.write_json(
                "approvals/APPROVED_apply.json",
                {
                    "schema": "nexus.approval_marker.v1",
                    "stage": stage,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "scope": "允许把已预览 patch 应用到目标项目；仍不得读取密钥、登录、提交表单、push 或发布。",
                },
            )
            self._write_state(store, "blocked", current_node="apply_approved", blocked_reason="apply_required", provider=state.get("provider", ""))
            return write_interaction(
                store,
                status="blocked",
                output="已记录 apply 审批；下一步可应用 patch。",
                next_prompt=f"运行 python -m nexus.cli apply {run_id}",
                blocked_reason="apply_required",
                artifact_refs=[str(marker)],
            )

        if stage == "skill-install":
            return self.install_generated_skill(run_id, confirm=True)

        if stage == "recovery-playbook":
            input_payload = _read_json_if_exists(store.path("input.json"))
            result = _read_json_if_exists(store.path("tool_results/recovery_result.json"))
            if not result:
                return write_interaction(store, status="blocked", output="没有可写入 recovery playbook 的 recovery_result。", next_prompt=f"先触发恢复链路后再运行 approve {run_id} recovery-playbook。", blocked_reason="recovery_result_missing")
            project, project_source = _resolve_recovery_project_path(store, input_payload=input_payload, result=result)
            if project is None or not project.exists() or not project.is_dir():
                pending = store.write_json(
                    "tool_results/recovery_playbook_pending_project.json",
                    {
                        "schema": "nexus.recovery_playbook_pending_project.v1",
                        "status": "blocked",
                        "reason": "project_path_unresolved" if project is None else "project_path_not_ready",
                        "project_path": str(project) if project is not None else "",
                        "project_path_source": project_source,
                        "recovery_result_path": str(store.path("tool_results", "recovery_result.json")),
                    },
                )
                self._write_state(store, "blocked", current_node="recovery_playbook_target_resolution", blocked_reason="recovery_playbook_project_path_unresolved")
                return write_interaction(
                    store,
                    status="blocked",
                    output="不能写入 recovery playbook：尚未解析到已创建的目标项目目录。",
                    next_prompt=f"先完成目标项目创建/回灌后再运行 python -m nexus.cli approve {run_id} recovery-playbook。",
                    blocked_reason="recovery_playbook_project_path_unresolved",
                    artifact_refs=[str(pending)],
                )
            written = apply_recovery_playbook_approval(project, result)
            path = store.write_json("tool_results/recovery_playbook_write_result.json", written)
            self._write_state(store, "completed", current_node="recovery_playbook_written", project_path=str(project), final_project_path=str(project), project_path_source=project_source)
            return write_interaction(store, status="completed", output="已将本次恢复经验写入项目 recovery playbook。", next_prompt="后续类似失败会先读取该经验，但仍需按风险审批执行。", artifact_refs=[str(path), str(project / ".nexus" / "recovery-playbook.json"), str(project / "docs" / "recovery-records.md")])

        if stage != "implementation-plan":
            return write_interaction(
                store,
                status="failed",
                output=f"不支持的审批阶段：{stage}",
                next_prompt="当前支持 approve <run_id> implementation-plan、project-root、online-search、code-change 或 apply。",
                blocked_reason="unsupported_approval_stage",
            )
        can_generate_plan = (
            state.get("status") == "blocked"
            and state.get("blocked_reason") == "approval_required"
        ) or state.get("status") == "completed"
        if not can_generate_plan:
            return write_interaction(
                store,
                status=str(state.get("status") or "unknown"),
                output="当前 run 不在 implementation-plan 审批阻断状态。",
                next_prompt=f"查看状态：python -m nexus.cli status {run_id}",
            )
        marker = store.write_json(
            "approvals/APPROVED_implementation-plan.json",
            {
                "schema": "nexus.approval_marker.v1",
                "stage": stage,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "scope": "允许进入实现计划生成；仍不得自动安装、登录、提交表单、发送消息、push/PR/release 或读取密钥。",
            },
        )
        input_payload = store.read_json("input.json")
        project = Path(str(input_payload["project_path"])).expanduser().resolve()
        provider = self._select_provider(store, str(input_payload.get("provider") or "auto"), project)
        if provider is None:
            return self._block_for_provider_setup(store)
        return self._generate_implementation_plan(store, provider=provider, project=project, approval_ref=marker, trigger="approve")

    def approve_and_continue(self, run_id: str, stage: str) -> dict[str, object]:
        interaction = self.approve(run_id, stage)
        status = str(interaction.get("previous_task_status") or "")
        blocked_reason = str(interaction.get("blocked_reason") or "")
        if status != "blocked":
            return interaction
        if blocked_reason in {"resume_required_after_online_search_approval", "resume_required_after_conversation_session_read_approval"}:
            return self.resume(run_id)
        if blocked_reason == "execute_code_change_required":
            return self.execute_code_change(run_id, provider_name="codex-cli")
        if blocked_reason == "apply_required":
            return self.apply_patch_to_target(run_id)
        return interaction

    def _generate_implementation_plan(
        self,
        store: RunStore,
        *,
        provider: HostModelProvider,
        project: Path,
        approval_ref: Path | None = None,
        trigger: str,
    ) -> dict[str, object]:
        gate = _implementation_plan_gate(store)
        if gate is not None:
            self._write_state(store, "blocked", current_node="implementation_plan_gate", blocked_reason=str(gate.get("blocked_reason") or "implementation_plan_blocked"), provider=provider.name, continuation_trigger=trigger)
            gate_path = store.write_json("reports/implementation_plan_blocked.json", gate)
            return write_interaction(
                store,
                status="blocked",
                output=str(gate.get("message") or "当前调研结果不能直接生成 implementation_plan。"),
                next_prompt=str(gate.get("next_prompt") or f"python -m nexus.cli continue {store.run_id} \"从零搭建方案\""),
                blocked_reason=str(gate.get("blocked_reason") or "implementation_plan_blocked"),
                artifact_refs=[str(gate_path)],
            )
        report_text = store.path("reports", "final_report.md").read_text(encoding="utf-8") if store.path("reports", "final_report.md").exists() else ""
        selected = _read_json_if_exists(store.path("reports", "selected_research_branch.json"))
        branch_text = ""
        if selected:
            branch_ref = str(selected.get("branch_report_ref") or "")
            if branch_ref and Path(branch_ref).exists():
                branch_text = Path(branch_ref).read_text(encoding="utf-8")
        decision_text = store.path("reports", "decision_matrix.md").read_text(encoding="utf-8") if store.path("reports", "decision_matrix.md").exists() else ""
        high_provider = self._provider_for_intensity(store, provider, project, "high") or provider
        plan = self._model_node(store, high_provider, "implementation_plan", _implementation_prompt(selected, report_text, branch_text, decision_text))
        plan_path = store.write_json("reports/implementation_plan.json", plan)
        plan_md_path = store.write_text("reports/implementation_plan.md", _render_implementation_plan(plan))
        self._write_state(store, "blocked", current_node="code_change_approval", blocked_reason="code_change_approval_required", provider=high_provider.name, approval_stage="implementation-plan", continuation_trigger=trigger)
        update_board(project, status="implementation plan 已生成，等待代码修改二次确认", point=f"run {store.run_id} 已生成 implementation_plan")
        refs = [str(plan_path), str(plan_md_path)]
        if approval_ref is not None:
            refs.insert(0, str(approval_ref))
        return write_interaction(
            store,
            status="blocked",
            output="已记录 implementation-plan 审批，并生成结构化 implementation plan；代码修改仍需二次确认。",
            next_prompt=_multi_option_prompt(
                [
                    f"选项 1：审阅 {plan_md_path}",
                    f"选项 2：确认代码修改 python -m nexus.cli approve {store.run_id} code-change",
                    f"选项 3：返回 discovery 修改范围 python -m nexus.cli continue {store.run_id} \"局部调研：<范围>\"",
                ]
            ),
            blocked_reason="code_change_approval_required",
            artifact_refs=refs,
        )

    def execute_code_change(self, run_id: str, *, provider_name: str = "codex-cli") -> dict[str, object]:
        store = RunStore(self.root, run_id)
        state = store.read_json("state.json")
        if not store.path("approvals", "APPROVED_code-change.json").exists():
            return write_interaction(
                store,
                status=str(state.get("status") or "unknown"),
                output="尚未审批 code-change；不会调用 Codex 修改副本。",
                next_prompt=f"先运行 python -m nexus.cli approve {run_id} code-change",
                blocked_reason="code_change_approval_required",
            )
        if provider_name == "mock":
            return write_interaction(
                store,
                status="failed",
                output="代码修改链路不允许使用 MockProvider；必须真实调用 Codex 或明确 blocked。",
                next_prompt="使用 --provider codex-cli，或先运行 python -m nexus.cli doctor。",
                blocked_reason="mock_not_allowed_for_code_change",
            )
        input_payload = store.read_json("input.json")
        project = Path(str(input_payload["project_path"])).expanduser().resolve()
        git_root = _git_root(project)
        if git_root is None:
            self._write_state(store, "blocked", current_node="execute_code_change", blocked_reason="non_git_target")
            return write_interaction(
                store,
                status="blocked",
                output="目标项目不是 git repo；当前端到端写入链路需要 git worktree 来隔离 diff。",
                next_prompt=f"先运行 python -m nexus.cli prepare-project {project}，审批 git baseline 后再重试 code-change。",
                blocked_reason="non_git_target",
            )
        dirty = _git(git_root, ["status", "--porcelain=v1"], check=False).stdout.strip()
        if dirty:
            dirty_path = store.write_text("code_change/dirty_worktree.txt", dirty + "\n")
            self._write_state(store, "blocked", current_node="execute_code_change", blocked_reason="dirty_worktree")
            return write_interaction(
                store,
                status="blocked",
                output="目标项目存在未提交修改；拒绝在不清楚用户改动的情况下生成可应用 patch。",
                next_prompt="请先 commit/stash 当前改动，或之后增加显式 allow-dirty-worktree 策略。",
                blocked_reason="dirty_worktree",
                artifact_refs=[str(dirty_path)],
            )
        codex = shutil.which("codex")
        if not codex:
            self._write_state(store, "blocked", current_node="execute_code_change", blocked_reason="codex_cli_unavailable")
            return write_interaction(
                store,
                status="blocked",
                output="未找到 codex CLI；无法真实调用 Codex 修改隔离 worktree。",
                next_prompt="安装/登录 Codex CLI 后重试，或运行 python -m nexus.cli doctor。",
                blocked_reason="codex_cli_unavailable",
            )
        worktree = store.path("code_change", "worktree")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        if not worktree.exists():
            add = _git(git_root, ["worktree", "add", "--detach", str(worktree), "HEAD"], check=False)
            if add.returncode != 0:
                return write_interaction(
                    store,
                    status="failed",
                    output=f"创建隔离 worktree 失败：{_short_text(add.stderr or add.stdout)}",
                    next_prompt="检查 git worktree 状态后重试。",
                    blocked_reason="worktree_create_failed",
                )
        plan_text = store.path("reports", "implementation_plan.md").read_text(encoding="utf-8") if store.path("reports", "implementation_plan.md").exists() else ""
        discovery = store.path("reports", "final_report.md").read_text(encoding="utf-8") if store.path("reports", "final_report.md").exists() else ""
        prompt = _code_change_prompt(str(input_payload.get("idea") or ""), discovery, plan_text)
        prompt_path = store.write_text("code_change/codex_prompt.md", prompt)
        last_path = store.path("code_change", "codex_last_message.md")
        codex_profile = resolve_profile(self.root, HIGH_INTENSITY_CODEX_PROFILE)
        codex_model = codex_profile.model if codex_profile and codex_profile.model else "gpt-5.4"
        cmd = [
            codex,
            "exec",
            "--model",
            codex_model,
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--output-last-message",
            str(last_path),
            "-",
        ]
        completed = subprocess.run(cmd, input=prompt, cwd=worktree, capture_output=True, text=True, timeout=900, check=False)
        store.write_text("code_change/codex_stdout.txt", completed.stdout)
        store.write_text("code_change/codex_stderr.txt", completed.stderr)
        if completed.returncode != 0:
            self._write_state(store, "failed", current_node="execute_code_change", blocked_reason="codex_exec_failed")
            return write_interaction(
                store,
                status="failed",
                output=f"Codex 执行失败：{_short_text(completed.stderr or completed.stdout)}",
                next_prompt="查看 code_change/codex_stderr.txt 后修复 provider/sandbox 问题再重试。",
                blocked_reason="codex_exec_failed",
                artifact_refs=[str(prompt_path), str(store.path("code_change", "codex_stderr.txt"))],
            )
        diff = _git(worktree, ["diff", "--binary"], check=False)
        patch_path = store.write_text("diff/changes.patch", diff.stdout)
        stat = _git(worktree, ["diff", "--stat"], check=False)
        stat_path = store.write_text("diff/diff_stat.txt", stat.stdout)
        boundary = _boundary_check(diff.stdout)
        boundary_path = store.write_json("diff/boundary_check.json", boundary)
        if not diff.stdout.strip():
            self._write_state(store, "blocked", current_node="execute_code_change", blocked_reason="empty_diff")
            return write_interaction(
                store,
                status="blocked",
                output="Codex 已真实执行，但隔离 worktree 没有产生 diff。",
                next_prompt="审阅 codex_last_message 和 implementation_plan，必要时调整计划后重试。",
                blocked_reason="empty_diff",
                artifact_refs=[str(prompt_path), str(last_path)],
            )
        if not boundary["allowed"]:
            self._write_state(store, "blocked", current_node="boundary_check", blocked_reason="boundary_check_failed")
            return write_interaction(
                store,
                status="blocked",
                output="已生成 diff，但 boundary check 未通过；不会应用到目标项目。",
                next_prompt=f"审阅 {boundary_path} 和 {patch_path}，必要时重新生成计划。",
                blocked_reason="boundary_check_failed",
                artifact_refs=[str(patch_path), str(stat_path), str(boundary_path)],
            )
        self._write_state(store, "blocked", current_node="diff_preview", blocked_reason="apply_approval_required")
        return write_interaction(
            store,
            status="blocked",
                output=f"已在隔离 worktree 中真实调用 Codex {codex_model} 并生成 patch；应用到目标项目前需要再次确认。",
            next_prompt=f"先运行 python -m nexus.cli diff {run_id} 查看 diff；确认后运行 python -m nexus.cli approve {run_id} apply，再运行 python -m nexus.cli apply {run_id}",
            blocked_reason="apply_approval_required",
            artifact_refs=[str(patch_path), str(stat_path), str(boundary_path), str(last_path)],
        )

    def diff(self, run_id: str) -> dict[str, object]:
        store = RunStore(self.root, run_id)
        patch_path = store.path("diff", "changes.patch")
        stat_path = store.path("diff", "diff_stat.txt")
        if not patch_path.exists():
            return write_interaction(
                store,
                status="blocked",
                output="还没有可预览的 diff。",
                next_prompt=f"先运行 python -m nexus.cli execute-code-change {run_id} --provider codex-cli",
                blocked_reason="diff_not_found",
            )
        summary = stat_path.read_text(encoding="utf-8") if stat_path.exists() else patch_path.read_text(encoding="utf-8")[:4000]
        return write_interaction(
            store,
            status="blocked",
            output="Diff 预览已生成：\n" + summary,
            next_prompt=f"确认后运行 python -m nexus.cli approve {run_id} apply；然后 python -m nexus.cli apply {run_id}",
            blocked_reason="apply_approval_required",
            artifact_refs=[str(patch_path), str(stat_path)],
        )

    def apply_patch_to_target(self, run_id: str) -> dict[str, object]:
        store = RunStore(self.root, run_id)
        state = store.read_json("state.json")
        if not store.path("approvals", "APPROVED_apply.json").exists():
            return write_interaction(
                store,
                status=str(state.get("status") or "unknown"),
                output="尚未审批 apply；不会写目标项目。",
                next_prompt=f"先运行 python -m nexus.cli approve {run_id} apply",
                blocked_reason="apply_approval_required",
            )
        input_payload = store.read_json("input.json")
        project = Path(str(input_payload["project_path"])).expanduser().resolve()
        git_root = _git_root(project)
        patch_path = store.path("diff", "changes.patch")
        if git_root is None or not patch_path.exists():
            return write_interaction(
                store,
                status="failed",
                output="无法应用 patch：目标不是 git repo 或 patch 不存在。",
                next_prompt=f"重新运行 python -m nexus.cli execute-code-change {run_id} --provider codex-cli",
                blocked_reason="apply_precondition_failed",
            )
        dirty = _git(git_root, ["status", "--porcelain=v1"], check=False).stdout.strip()
        if dirty:
            self._write_state(store, "blocked", current_node="apply", blocked_reason="dirty_worktree")
            return write_interaction(
                store,
                status="blocked",
                output="目标项目在生成 diff 后出现未提交改动；拒绝应用 patch。",
                next_prompt="请先处理当前改动后重试 apply。",
                blocked_reason="dirty_worktree",
            )
        check = subprocess.run(["git", "-C", str(git_root), "apply", "--check", str(patch_path)], capture_output=True, text=True, check=False)
        if check.returncode != 0:
            return write_interaction(
                store,
                status="failed",
                output=f"git apply --check 失败：{_short_text(check.stderr or check.stdout)}",
                next_prompt="重新生成 diff 或手动审阅冲突。",
                blocked_reason="apply_check_failed",
            )
        apply = subprocess.run(["git", "-C", str(git_root), "apply", str(patch_path)], capture_output=True, text=True, check=False)
        store.write_text("diff/apply_stdout.txt", apply.stdout)
        store.write_text("diff/apply_stderr.txt", apply.stderr)
        if apply.returncode != 0:
            return write_interaction(
                store,
                status="failed",
                output=f"应用 patch 失败：{_short_text(apply.stderr or apply.stdout)}",
                next_prompt="查看 diff/apply_stderr.txt 后处理。",
                blocked_reason="apply_failed",
            )
        self._write_state(store, "blocked", current_node="tests_required", blocked_reason="tests_required")
        update_board(project, status="patch 已应用，等待测试", point=f"run {run_id} 已应用 patch")
        return write_interaction(
            store,
            status="blocked",
            output="patch 已应用到目标项目；下一步需要运行测试。",
            next_prompt=f"运行 python -m nexus.cli test {run_id} --cmd \"python -m pytest -q\"",
            blocked_reason="tests_required",
            artifact_refs=[str(patch_path)],
        )

    def run_tests(self, run_id: str, *, command: str = "") -> dict[str, object]:
        store = RunStore(self.root, run_id)
        input_payload = store.read_json("input.json")
        project = Path(str(input_payload["project_path"])).expanduser().resolve()
        if not command:
            command = _default_test_command(project)
        if not command:
            self._write_state(store, "blocked", current_node="tests", blocked_reason="test_command_required")
            return write_interaction(
                store,
                status="blocked",
                output="无法推断测试命令；没有执行任意命令。",
                next_prompt=f"请显式运行 python -m nexus.cli test {run_id} --cmd \"<test command>\"",
                blocked_reason="test_command_required",
            )
        completed = subprocess.run(shlex.split(command), cwd=project, capture_output=True, text=True, timeout=600, check=False)
        store.write_text("tests/stdout.txt", completed.stdout)
        store.write_text("tests/stderr.txt", completed.stderr)
        result = store.write_json(
            "tests/test_run_1.json",
            {
                "schema": "nexus.test_run.v1",
                "command": command,
                "returncode": completed.returncode,
                "passed": completed.returncode == 0,
                "stdout_ref": str(store.path("tests", "stdout.txt")),
                "stderr_ref": str(store.path("tests", "stderr.txt")),
            },
        )
        if completed.returncode == 0:
            post = self._post_change_autosync(
                store,
                project,
                event_type="code_change_test_passed",
                title="代码修改测试通过后的自动同步",
                summary=f"run {run_id} 的代码修改已应用并通过测试命令：{command}",
                changed_paths=[result, store.path("tests", "stdout.txt"), store.path("tests", "stderr.txt")],
                feishu_sync_enabled=True,
                github_private_enabled=True,
                continuation_note="tests_passed",
            )
            refs = [str(result), str(store.path("tests", "stdout.txt")), str(store.path("tests", "stderr.txt")), *[str(item) for item in post.get("artifact_refs", []) if str(item)]]
            post["artifact_refs"] = refs
            post["previous_task_output"] = f"测试通过；{post.get('previous_task_output', '')}"
            store.write_json("interaction.json", post)
            store.write_text(
                "interaction.md",
                "\n".join(
                    [
                        f"上一任务状态：{post.get('previous_task_status', '')}",
                        f"上一任务输出：{post.get('previous_task_output', '')}",
                        f"下一任务提示：{post.get('next_task_prompt', '')}",
                    ]
                )
                + "\n",
            )
            return post
        final_status = "blocked"
        blocked_reason = "tests_failed"
        self._write_state(store, final_status, current_node="tests", blocked_reason=blocked_reason, github_auto_private_status="skipped_tests_failed")
        refs = [str(result), str(store.path("tests", "stdout.txt")), str(store.path("tests", "stderr.txt"))]
        return write_interaction(
            store,
            status=final_status,
            output=f"测试失败，返回码 {completed.returncode}。GitHub/Feishu/post-change autosync 已跳过。",
            next_prompt=f"修复测试后重试 python -m nexus.cli test {run_id} --cmd {shlex.quote(command)}。",
            blocked_reason=blocked_reason,
            artifact_refs=refs,
        )

    def continue_run(self, run_id: str, user_request: str, *, provider_name: str = "auto") -> dict[str, object]:
        actual_run_id = self._resolve_run_id(run_id)
        store = RunStore(self.root, actual_run_id)
        input_payload = store.read_json("input.json")
        project = Path(str(input_payload["project_path"])).expanduser().resolve()
        selected_provider = self._resolve_provider_name(provider_name, user_request) if provider_name != "auto" else self._resolve_provider_name(str(input_payload.get("provider") or "auto"), user_request)
        provider = self._select_provider(store, selected_provider, project)
        if provider is None:
            return self._block_for_provider_setup(store)
        route_provider = self._provider_for_intensity(store, provider, project, "high") or provider
        route = self._model_node_with_schema(
            store,
            route_provider,
            f"continue_intent_route_{_safe_node_suffix(user_request)}",
            "continue_intent_route",
            _continue_route_prompt(user_request, store),
        )
        route_path = store.write_json("reports/continue_intent_route.json", route)
        normalized_route = str(route.get("route") or "").strip().lower()
        store.append_audit("continue_routed", {"request": user_request, "route": normalized_route})
        selected_branch = branch_id_from_route(normalized_route, str(route.get("scope") or ""), user_request)
        if normalized_route in {"select_existing_wheel", "select_subproject_wheels", "select_from_scratch_build"} and selected_branch:
            return self._select_research_branch(store, selected_branch, route, project, user_request)
        if normalized_route == "implementation_plan":
            marker = store.write_json(
                "approvals/APPROVED_implementation-plan.json",
                {
                    "schema": "nexus.approval_marker.v1",
                    "stage": "implementation-plan",
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "scope": "用户通过 continue 一句话明确要求生成 implementation plan；仍不得修改代码。",
                    "user_request": user_request,
                },
            )
            return self._generate_implementation_plan(store, provider=provider, project=project, approval_ref=marker, trigger="continue")
        if normalized_route == "rerun_research":
            return self._continue_rerun_research(store, route, project, selected_provider)
        if normalized_route == "local_research":
            return self._continue_local_research(store, route, project, selected_provider)
        if normalized_route == "chunked_research":
            return self._continue_chunked_research(store, provider, route, project)
        if normalized_route == "update_intent":
            return self._continue_update_intent(store, route_provider, route, project, user_request)
        return write_interaction(
            store,
            status="blocked",
            output=f"continue intent route 未能匹配可执行节点：{normalized_route}",
            next_prompt=f"请改写为：选择已有轮子方案 / 分块调研：<范围> / 从零搭建方案 / 生成项目计划 / 重新调研 / 局部调研：<范围> / 更新项目意图：<内容>。",
            blocked_reason="unsupported_continue_route",
            artifact_refs=[str(route_path)],
        )

    def _select_research_branch(self, store: RunStore, branch_id: str, route: dict[str, object], project: Path, user_request: str) -> dict[str, object]:
        if not selected_branch_is_valid(branch_id):
            return write_interaction(
                store,
                status="blocked",
                output=f"无法识别调研分支：{branch_id}",
                next_prompt=f"请改写为：选择已有轮子方案 / 分块调研：<范围> / 从零搭建方案。",
                blocked_reason="unsupported_research_branch",
            )
        contract = _read_json_if_exists(store.path("reports", "research_contract.json"))
        if not bool(contract.get("requires_branch_reports")):
            return write_interaction(
                store,
                status="blocked",
                output="当前 run 没有三方向调研契约和分支报告，不能选择搭建分支。",
                next_prompt=f"python -m nexus.cli continue {store.run_id} \"重新调研：按已有轮子/分块调研/从零搭建三方向\"",
                blocked_reason="branch_reports_missing",
            )
        selected = {
            "schema": "nexus.selected_research_branch.v1",
            "branch_id": branch_id,
            "label": BRANCH_LABELS.get(branch_id, branch_id),
            "selected_at": datetime.now(timezone.utc).isoformat(),
            "user_request": user_request,
            "route": route,
            "branch_report_ref": str(_branch_report_path(store, branch_id)),
        }
        selected_path = store.write_json("reports/selected_research_branch.json", selected)
        self._write_state(store, "blocked", current_node="research_branch_selected", blocked_reason="implementation_plan_required", selected_research_branch=branch_id)
        update_board(project, status=f"已选择调研分支：{BRANCH_LABELS.get(branch_id, branch_id)}，等待 implementation plan", point=f"run {store.run_id} 已选择 research branch {branch_id}")
        next_options_path = self._write_next_options(
            store,
            stage="research_branch_selected",
            completion_quality="branch_selected",
            summary=f"已选择调研分支：{BRANCH_LABELS.get(branch_id, branch_id)}。",
            weak_points=[],
            research_contract=_read_json_if_exists(store.path("reports", "research_contract.json")),
            branch_refs=_existing_branch_refs(store),
        )
        return write_interaction(
            store,
            status="blocked",
            output=f"已选择调研分支：{BRANCH_LABELS.get(branch_id, branch_id)}；现在可以基于该分支生成 implementation_plan。",
            next_prompt=f"python -m nexus.cli continue {store.run_id} \"生成项目计划\"",
            blocked_reason="implementation_plan_required",
            artifact_refs=[str(selected_path), str(next_options_path), str(_branch_report_path(store, branch_id))],
        )

    def continue_after_input(self, run_id: str, note: str = "") -> dict[str, object]:
        actual_run_id = self._resolve_run_id(run_id)
        store = RunStore(self.root, actual_run_id)
        continuation = _read_json_if_exists(store.path("recovery", "continuation.json"))
        operation = str(continuation.get("operation") or "")
        project_raw = str(continuation.get("project_path") or "")
        if not operation or not project_raw:
            return write_interaction(
                store,
                status="blocked",
                output="没有找到可 continue-after-input 的恢复上下文。",
                next_prompt=f"运行 python -m nexus.cli status {actual_run_id} 查看当前状态。",
                blocked_reason="continuation_missing",
            )
        project = Path(project_raw).expanduser().resolve()
        retry_env = continuation.get("retry_env") if isinstance(continuation.get("retry_env"), dict) else {}
        with _temporary_env({str(key): str(value) for key, value in retry_env.items() if value is not None}):
            if operation == "github_auto_private":
                return self._github_auto_private_flow(
                    store,
                    project,
                    commit_message=str(continuation.get("commit_message") or "nexus auto private sync"),
                    continuation_operation="github_auto_private",
                    continuation_note=note,
                    force_bootstrap=bool(continuation.get("force_bootstrap")),
                )
            if operation == "github_bootstrap":
                return self._github_bootstrap_flow(
                    store,
                    project,
                    private_repo=str(continuation.get("private_repo") or ""),
                    public_repo=str(continuation.get("public_repo") or ""),
                    create_remote_repos=bool(continuation.get("create_remote_repos", True)),
                    commit_message=str(continuation.get("commit_message") or f"bootstrap {project.name}"),
                    continuation_operation="github_bootstrap",
                    continuation_note=note,
                )
            if operation == "github_private":
                return self._github_private_flow(
                    store,
                    project,
                    continuation_note=note,
                )
            if operation == "post_change_autosync":
                changed_paths = continuation.get("changed_paths") if isinstance(continuation.get("changed_paths"), list) else []
                return self._post_change_autosync(
                    store,
                    project,
                    event_type=str(continuation.get("event_type") or "post_change_autosync"),
                    title=str(continuation.get("title") or "变更后自动同步"),
                    summary=str(continuation.get("summary") or "恢复变更后自动同步。"),
                    target=str(continuation.get("target") or "auto"),
                    changed_paths=[str(path) for path in changed_paths if isinstance(path, str)],
                    feishu_sync_enabled=bool(continuation.get("feishu_sync_enabled", True)),
                    github_private_enabled=True,
                    continuation_note=note,
                )
            if operation == "self_sync":
                return self._self_sync_flow(
                    store,
                    project,
                    target=str(continuation.get("target") or "auto"),
                    feishu_sync_enabled=bool(continuation.get("feishu_sync_enabled", True)),
                    continuation_note=note,
                    force_bootstrap_private=bool(continuation.get("force_bootstrap")),
                )
            if operation == "github_public":
                return self._github_public_flow(
                    store,
                    project,
                    confirm=bool(continuation.get("confirm", True)),
                    continuation_note=note,
                )
        return write_interaction(
            store,
            status="blocked",
            output=f"未知恢复 operation：{operation}",
            next_prompt=f"运行 python -m nexus.cli status {actual_run_id} 查看当前状态。",
            blocked_reason="unsupported_continuation_operation",
        )

    def status(self, run_id: str = "latest") -> dict[str, object]:
        actual_run_id = self._resolve_run_id(run_id)
        store = RunStore(self.root, actual_run_id)
        interaction = _read_json_if_exists(store.path("interaction.json"))
        if interaction:
            return interaction
        state = _read_json_if_exists(store.path("state.json"))
        status = str(state.get("status") or "blocked")
        return write_interaction(
            store,
            status=status,
            output=f"run {actual_run_id} 当前状态：{status}",
            next_prompt=f"运行 python -m nexus.cli status {actual_run_id} 查看状态。",
            blocked_reason=str(state.get("blocked_reason") or ""),
            artifact_refs=[str(store.path("state.json"))],
        )

    def recover(self, run_id: str = "latest", request: str = "") -> dict[str, object]:
        actual_run_id = self._resolve_run_id(run_id)
        store = RunStore(self.root, actual_run_id)
        interaction = _read_json_if_exists(store.path("interaction.json"))
        skip_provider = _skip_provider_from_recovery_request(request)
        if skip_provider:
            previous_skipped = _fallback_chain_skipped_providers(store)
            target_provider = _target_provider_from_recovery_request(request)
            chain_path = _append_provider_fallback_chain(
                store,
                {
                    "event": "provider_fallback_approved",
                    "status": "approved",
                    "skip_provider": skip_provider,
                    "target_provider": target_provider,
                    "request": request,
                    "previously_skipped_providers": previous_skipped,
                },
            )
            store.append_audit("provider_fallback_chain_updated", {"event": "provider_fallback_approved", "artifact": str(chain_path), "skip_provider": skip_provider, "target_provider": target_provider})
            with _temporary_env({"NEXUS_AUTO_SKIP_PROVIDERS": _merged_skip_providers(skip_provider, previous_skipped)}):
                input_payload = _read_json_if_exists(store.path("input.json"))
                if str(input_payload.get("schema") or "") == "nexus.project_init_input.v1":
                    result = self._continue_init_project_after_provider_recovery(store)
                else:
                    result = self.resume(actual_run_id)
                state_after_recovery = _read_json_if_exists(store.path("state.json"))
                last_model_provider = _read_json_if_exists(store.path("tool_results/last_model_provider.json"))
                final_status = str(result.get("previous_task_status") or result.get("status") or state_after_recovery.get("status") or "")
                final_provider = str(last_model_provider.get("provider") or state_after_recovery.get("provider") or "")
                chain_event = "provider_fallback_completed" if final_status in {"completed", "done"} else "provider_fallback_blocked"
                chain_path = _append_provider_fallback_chain(
                    store,
                    {
                        "event": chain_event,
                        "status": final_status or str(state_after_recovery.get("status") or ""),
                        "skip_provider": skip_provider,
                        "target_provider": target_provider,
                        "final_provider": final_provider,
                        "blocked_reason": str(result.get("blocked_reason") or state_after_recovery.get("blocked_reason") or ""),
                        "state_status": str(state_after_recovery.get("status") or ""),
                        "state_current_node": str(state_after_recovery.get("current_node") or ""),
                        "request": request,
                    },
                )
                store.append_audit("provider_fallback_chain_updated", {"event": chain_event, "artifact": str(chain_path), "skip_provider": skip_provider, "final_provider": final_provider})
                _record_successful_recovery_outcome(
                    store,
                    result,
                    recovered_by="provider_fallback",
                    action_applied="skip_current_provider_and_continue",
                )
                _write_recovery_playbook_approval_if_completed(store, input_payload)
                return result
        input_payload = _read_json_if_exists(store.path("input.json"))
        if str(input_payload.get("schema") or "") == "nexus.project_init_input.v1" and ("项目命名" in request or "project_name" in request or str(_read_json_if_exists(store.path("state.json")).get("blocked_reason") or "") == "project_name_model_failed"):
            result = self._continue_init_project_after_provider_recovery(store)
            _record_successful_recovery_outcome(
                store,
                result,
                recovered_by="provider_recovery",
                action_applied="retry_project_initialization_after_provider_recovery",
            )
            _write_recovery_playbook_approval_if_completed(store, input_payload)
            return result
        if "修复" in request and "provider" in request.lower() or "preflight" in request.lower():
            if str(input_payload.get("schema") or "") == "nexus.project_init_input.v1":
                result = self._continue_init_project_after_provider_recovery(store)
            else:
                result = self.resume(actual_run_id)
            _record_successful_recovery_outcome(
                store,
                result,
                recovered_by="provider_recovery",
                action_applied="retry_current_provider_preflight",
            )
            _write_recovery_playbook_approval_if_completed(store, input_payload)
            return result
        continuation = interaction.get("continuation") if isinstance(interaction.get("continuation"), dict) else {}
        if continuation.get("operation") and ("continue-after-input" in request or "完成" in request or "恢复" in request or "继续" in request):
            return self.continue_after_input(actual_run_id, note=request)
        pending = interaction.get("pending_actions") if isinstance(interaction.get("pending_actions"), list) else []
        for action in pending:
            if not isinstance(action, dict):
                continue
            command = str(action.get("command") or "")
            if "continue-after-input" in command:
                return self.continue_after_input(actual_run_id, note=request)
            if "approve-and-continue" in command:
                parts = command.split()
                try:
                    idx = parts.index("approve-and-continue")
                    return self.approve_and_continue(parts[idx + 1], parts[idx + 2])
                except (ValueError, IndexError):
                    continue
        return write_interaction(
            store,
            status="blocked",
            output="恢复动作需要外层 Codex/宿主环境执行 pending_actions。",
            next_prompt=f"按 interaction.json 中 pending_actions 执行后运行 python -m nexus.cli continue-after-input {actual_run_id} --note \"已完成外部步骤\"。",
            blocked_reason="host_action_required",
            pending_actions=[item for item in pending if isinstance(item, dict)],
            continuation=continuation if isinstance(continuation, dict) else {},
            lifecycle_status=str(interaction.get("lifecycle_status") or "awaiting_approval"),
            recovery_mode=True,
        )

    def handoff_for_debug(self, run_id: str = "latest", *, reason: str = "") -> dict[str, object]:
        actual_run_id = self._resolve_run_id(run_id)
        store = RunStore(self.root, actual_run_id)
        source_state = _read_json_if_exists(store.path("state.json"))
        source_interaction = _read_json_if_exists(store.path("interaction.json"))
        pending = source_interaction.get("pending_actions") if isinstance(source_interaction.get("pending_actions"), list) else []
        continuation = source_interaction.get("continuation") if isinstance(source_interaction.get("continuation"), dict) else _read_json_if_exists(store.path("recovery", "continuation.json"))
        handoff = {
            "schema": "nexus.debug_handoff.v1",
            "handoff_id": f"debug-{actual_run_id}",
            "source_run_id": actual_run_id,
            "reason": reason,
            "source_node": str(source_state.get("current_node") or ""),
            "source_state": source_state,
            "source_interaction": source_interaction,
            "pending_actions": pending,
            "continuation": continuation if isinstance(continuation, dict) else {},
        }
        store.write_json("handoffs/debug_handoff.json", handoff)
        session = {"schema": "nexus.debug_session.v1", "handoff_id": handoff["handoff_id"], "source_run_id": actual_run_id, "status": "open", "created_at": datetime.now(timezone.utc).isoformat(), "required_worklog_kinds": ["diagnose", "edit", "test"]}
        store.write_json("handoffs/debug_session.json", session)
        return write_interaction(store, status="blocked", output="已登记 debug handoff。", next_prompt=f"完成 debug 后运行 python -m nexus.cli rebind-and-continue {actual_run_id} --handoff-id {handoff['handoff_id']}。", blocked_reason="debug_handoff_registered", lifecycle_status="awaiting_debug_rebind", recovery_state="recoverable_via_debug_rebind", debug_handoff=handoff)

    def append_debug_worklog(self, run_id: str = "latest", *, handoff_id: str = "", kind: str = "diagnose", summary: str = "", result: str = "", command: str = "", paths: list[str] | None = None) -> dict[str, object]:
        actual_run_id = self._resolve_run_id(run_id)
        store = RunStore(self.root, actual_run_id)
        handoff = _read_json_if_exists(store.path("handoffs/debug_handoff.json"))
        if not handoff:
            return write_interaction(store, status="blocked", output="没有注册 debug handoff，拒绝追加 worklog。", next_prompt=f"先运行 python -m nexus.cli handoff-for-debug {actual_run_id} \"<debug 原因>\"。", blocked_reason="debug_handoff_missing")
        expected = str(handoff.get("handoff_id") or "")
        if handoff_id and expected and handoff_id != expected:
            return write_interaction(store, status="blocked", output="handoff_id 不匹配。", next_prompt=f"使用 handoff_id={expected} 重试。", blocked_reason="debug_handoff_mismatch")
        entry = {"schema": "nexus.debug_worklog_entry.v1", "handoff_id": handoff_id, "kind": kind, "summary": summary, "result": result, "command": command, "paths": paths or [], "created_at": datetime.now(timezone.utc).isoformat()}
        entries = _read_debug_worklog_entries(store)
        entries.append(entry)
        store.write_json("worklogs/debug_worklog.json", {"schema": "nexus.debug_worklog.v1", "entries": entries})
        return write_interaction(store, status="blocked", output="已追加 debug worklog。", next_prompt=f"继续 debug 或运行 python -m nexus.cli rebind-and-continue {actual_run_id} --handoff-id {handoff_id}。", blocked_reason="debug_rebind_pending")

    def rebind_and_continue(self, run_id: str = "latest", *, handoff_id: str = "") -> dict[str, object]:
        actual_run_id = self._resolve_run_id(run_id)
        store = RunStore(self.root, actual_run_id)
        handoff = _read_json_if_exists(store.path("handoffs/debug_handoff.json"))
        if not handoff:
            return write_interaction(store, status="blocked", output="没有找到 debug handoff，不能 rebind。", next_prompt=f"先运行 python -m nexus.cli handoff-for-debug {actual_run_id} \"<debug 原因>\"。", blocked_reason="debug_handoff_missing")
        expected = str(handoff.get("handoff_id") or "")
        if handoff_id and expected and handoff_id != expected:
            return write_interaction(store, status="blocked", output="handoff_id 不匹配。", next_prompt=f"使用 handoff_id={expected} 重试。", blocked_reason="debug_handoff_mismatch")
        entries = _read_debug_worklog_entries(store)
        kinds = {str(item.get("kind") or "") for item in entries}
        if "diagnose" not in kinds:
            return write_interaction(store, status="blocked", output="debug worklog 缺少 diagnose 记录，不能回跳。", next_prompt=f"先 append-debug-worklog {actual_run_id} --handoff-id {expected} --kind diagnose --summary \"<诊断>\"。", blocked_reason="debug_diagnose_missing")
        if "edit" in kinds and "test" not in kinds:
            return write_interaction(store, status="blocked", output="debug 修改后缺少 test 记录，不能回跳。", next_prompt=f"先 append-debug-worklog {actual_run_id} --handoff-id {expected} --kind test --summary \"<测试结果>\"。", blocked_reason="debug_test_missing")
        summary = {"schema": "nexus.debug_summary.v1", "handoff_id": expected, "entries": entries, "rebind_at": datetime.now(timezone.utc).isoformat()}
        store.write_json("worklogs/debug_summary.json", summary)
        current_state = _read_json_if_exists(store.path("state.json"))
        if str(current_state.get("status") or "") == "completed":
            result = {
                "previous_task_status": "completed",
                "previous_task_output": "debug worklog 已回绑；原 run 当前已完成，无需额外 pending runner command。",
                "next_task_prompt": str(_read_json_if_exists(store.path("interaction.json")).get("next_task_prompt") or ""),
                "run_id": actual_run_id,
                "artifact_refs": [str(store.path("state.json"))],
            }
            return self._finish_debug_rebind(store, handoff, summary, result)
        source_state = handoff.get("source_state") if isinstance(handoff.get("source_state"), dict) else {}
        blocked_reason = str(source_state.get("blocked_reason") or "")
        if blocked_reason == "git_baseline_approval_required":
            result = self.approve_and_continue(actual_run_id, "git-baseline")
            return self._finish_debug_rebind(store, handoff, summary, result)
        if blocked_reason == "project_name_model_failed":
            result = self._continue_init_project_after_provider_recovery(store)
            return self._finish_debug_rebind(store, handoff, summary, result)
        continuation = handoff.get("continuation") if isinstance(handoff.get("continuation"), dict) else {}
        if continuation.get("operation"):
            store.write_json("recovery/continuation.json", continuation)
            result = self.continue_after_input(actual_run_id, note=f"debug handoff {expected} completed")
            return self._finish_debug_rebind(store, handoff, summary, result)
        pending = handoff.get("pending_actions") if isinstance(handoff.get("pending_actions"), list) else []
        for action in pending:
            if not isinstance(action, dict):
                continue
            command = str(action.get("command") or action.get("continue_command") or "")
            if "continue-after-input" in command:
                result = self.continue_after_input(actual_run_id, note=f"debug handoff {expected} completed")
                return self._finish_debug_rebind(store, handoff, summary, result)
            if "approve-and-continue" in command:
                parts = command.split()
                try:
                    idx = parts.index("approve-and-continue")
                    result = self.approve_and_continue(parts[idx + 1], parts[idx + 2])
                    return self._finish_debug_rebind(store, handoff, summary, result)
                except (ValueError, IndexError):
                    continue
        return write_interaction(store, status="blocked", output="没有找到可自动回跳的 pending runner command。", next_prompt=f"查看状态：python -m nexus.cli status {actual_run_id}", blocked_reason="debug_rebind_target_missing")

    def _finish_debug_rebind(self, store: RunStore, handoff: dict[str, object], debug_summary: dict[str, object], result: dict[str, object]) -> dict[str, object]:
        expected = str(handoff.get("handoff_id") or "")
        rebind_path = store.write_json("rebind/rebind_result.json", {"schema": "nexus.debug_rebind_result.v1", "handoff_id": expected, "result": result})
        memory = _write_debug_recovery_result_if_possible(store, handoff, debug_summary, result, rebind_path=rebind_path)
        return _with_recovery_playbook_prompt(store, result, memory)

    def debug_status(self, run_id: str = "latest") -> dict[str, object]:
        actual_run_id = self._resolve_run_id(run_id)
        store = RunStore(self.root, actual_run_id)
        return write_interaction(store, status="blocked", output="debug 状态已读取。", next_prompt=f"运行 python -m nexus.cli status {actual_run_id} 查看主状态。", blocked_reason="debug_status")

    def _recovery_guidance(self, store: RunStore, project: Path, context: dict[str, object], playbook_match: dict[str, object]) -> dict[str, object]:
        related = related_recovery_experience(project, context)
        store.write_json("tool_results/related_recovery_experience.json", related)
        provider = self._select_provider(store, "auto", project, intensity="high")
        if provider is None:
            return fallback_recovery_guidance(context)
        try:
            return self._model_node_with_schema(store, provider, "failure_recovery_guidance", "failure_recovery_guidance", recovery_prompt(context, playbook_match, related))
        except (ProviderUnavailable, ProviderExecutionError, ValueError):
            return fallback_recovery_guidance(context)

    def _effective_provider_for_existing_run(self, store: RunStore, input_payload: dict[str, object], *, source: str) -> str:
        stored = str(input_payload.get("provider") or input_payload.get("requested_provider") or "auto")
        effective = "auto" if stored in {"codex-cli", "codex-mcp"} or stored.startswith("codex-cli") else stored
        store.write_json(f"{source}/provider_rebind.json", {"schema": "nexus.provider_rebind.v1", "stored_provider": stored, "effective_provider": effective, "reason": "existing runs rebind stale provider to auto for recovery" if effective != stored else "no_rebind_needed"})
        return effective

    def _continue_init_project_after_provider_recovery(self, store: RunStore) -> dict[str, object]:
        input_payload = store.read_json("input.json")
        idea = str(input_payload.get("idea") or "")
        raw_user_request = str(input_payload.get("raw_user_request") or idea)
        parent = Path(str(input_payload.get("parent") or ".")).expanduser().resolve()
        selected_provider = str(input_payload.get("provider") or "auto")
        try:
            raw_explicit_name = _explicit_project_name_from_idea(raw_user_request)
            operational_explicit_name = _explicit_project_name_from_idea(idea)
        except ValueError as exc:
            failure_path = store.write_json("tool_results/explicit_project_name_failure.json", {"schema": "nexus.explicit_project_name_failure.v1", "reason": str(exc), "idea": idea})
            self._write_state(store, "blocked", current_node="explicit_project_name", blocked_reason="explicit_project_name_invalid")
            return write_interaction(store, status="blocked", output=f"用户显式指定的项目名不安全，不能继续初始化：{exc}", next_prompt=f"请修正项目名后重新运行 $nexus-workflow 初始化项目；详情见 {failure_path}", blocked_reason="explicit_project_name_invalid", artifact_refs=[str(failure_path)])
        if operational_explicit_name and not raw_explicit_name:
            failure_path = store.write_json(
                "tool_results/agent_injected_project_name_failure.json",
                {
                    "schema": "nexus.agent_injected_project_name_failure.v1",
                    "operational_explicit_name": operational_explicit_name,
                    "raw_user_request": raw_user_request,
                    "operational_idea": idea,
                    "reason": "operational_idea_contains_explicit_project_name_but_raw_user_request_does_not",
                },
            )
            self._write_state(store, "blocked", current_node="explicit_project_name", blocked_reason="explicit_project_name_agent_injected")
            return write_interaction(store, status="blocked", output=f"初始化输入包含项目名 `{operational_explicit_name}`，但原始用户请求没有显式指定同一项目名；不能把外层工具注入的名称当成用户显式命名。", next_prompt=f"请让用户确认项目名，或移除注入命名后重新运行 $nexus-workflow 初始化项目；详情见 {failure_path}", blocked_reason="explicit_project_name_agent_injected", artifact_refs=[str(failure_path)])
        if raw_explicit_name and operational_explicit_name and raw_explicit_name != operational_explicit_name:
            failure_path = store.write_json(
                "tool_results/explicit_project_name_mismatch.json",
                {
                    "schema": "nexus.explicit_project_name_mismatch.v1",
                    "raw_explicit_name": raw_explicit_name,
                    "operational_explicit_name": operational_explicit_name,
                    "reason": "raw_user_request_and_operational_idea_disagree",
                },
            )
            self._write_state(store, "blocked", current_node="explicit_project_name", blocked_reason="explicit_project_name_mismatch")
            return write_interaction(store, status="blocked", output=f"原始用户请求指定 `{raw_explicit_name}`，但运行输入指定 `{operational_explicit_name}`；项目名来源不一致，不能继续初始化。", next_prompt=f"请修正项目名来源后重新运行 $nexus-workflow 初始化项目；详情见 {failure_path}", blocked_reason="explicit_project_name_mismatch", artifact_refs=[str(failure_path)])
        explicit_name = raw_explicit_name
        provider = self._select_provider(store, selected_provider, parent)
        if provider is None:
            return self._block_for_provider_setup(store)
        try:
            candidates = self._model_node(store, provider, "project_name_candidates", _project_name_prompt(idea, parent))
        except (ProviderUnavailable, ProviderExecutionError, ValueError) as exc:
            if explicit_name:
                candidates = {"schema": "nexus.project_name_candidates.v1", "recommended": "", "candidates": []}
                store.write_json(
                    "tool_results/project_name_model_warning.json",
                    {
                        "schema": "nexus.project_name_model_warning.v1",
                        "provider": provider.name,
                        "reason": str(exc),
                        "policy": "explicit_user_name_overrides_model_candidates",
                        "explicit_user_name": explicit_name,
                    },
                )
            else:
                failure_path = store.write_json("tool_results/project_name_model_failure.json", {"schema": "nexus.project_name_model_failure.v1", "provider": provider.name, "reason": str(exc)})
                self._write_state(store, "blocked", current_node="project_name_candidates", blocked_reason="project_name_model_failed", provider=provider.name)
                return write_interaction(store, status="blocked", output=f"项目命名模型失败，不能继续初始化：{exc}", next_prompt=f"修复真实模型/命名输出后重试：python -m nexus.cli recover {store.run_id} \"恢复项目命名\"", blocked_reason="project_name_model_failed", artifact_refs=[str(failure_path)])
        model_recommended = str(candidates.get("recommended") or "")
        name_collision_skipped: list[str] = []
        if explicit_name:
            recommended = explicit_name
        else:
            recommended, name_collision_skipped = _select_non_colliding_project_name(candidates, model_recommended, parent)
            if not recommended:
                failure_path = store.write_json("tool_results/project_name_collision.json", {"schema": "nexus.project_name_collision.v1", "model_recommended": model_recommended, "collisions": name_collision_skipped, "parent": str(parent)})
                self._write_state(store, "blocked", current_node="project_name_candidates", blocked_reason="project_name_collision", provider=provider.name)
                return write_interaction(store, status="blocked", output=f"项目命名候选均已存在，不能把已有目录当成从零新建项目：{', '.join(name_collision_skipped)}", next_prompt=f"请指定明确项目名后重新运行 $nexus-workflow 初始化项目；详情见 {failure_path}", blocked_reason="project_name_collision", artifact_refs=[str(failure_path)])
        target = parent / recommended
        name_source = "user_raw" if explicit_name else ("model_recommended" if recommended == model_recommended else "model_candidate_non_colliding")
        naming_policy = "explicit_user_name_overrides_model_candidates" if explicit_name else ("model_recommended_real_five_letter_word" if recommended == model_recommended else "model_candidate_non_colliding_real_five_letter_word")
        approval = {
            "schema": "nexus.project_root_approval.v1",
            "recommended": recommended,
            "model_recommended": model_recommended,
            "explicit_user_name": explicit_name,
            "name_source": name_source,
            "naming_policy": naming_policy,
            "name_collision_skipped": name_collision_skipped,
            "target_path": str(target),
            "candidates": candidates.get("candidates", []),
            "enable_feishu_autowrite": bool(input_payload.get("enable_feishu_autowrite")),
            "github_sync": bool(input_payload.get("github_sync", True)),
            "feishu_sync": bool(input_payload.get("feishu_sync", True)),
            "private_repo": str(input_payload.get("private_repo") or ""),
            "public_repo": str(input_payload.get("public_repo") or ""),
            "initial_public_sync": False,
            "initial_public_staging": bool(input_payload.get("github_sync", True)),
            "public_sync_requires_project_root_approval": False,
            "public_sync_requires_explicit_confirmation": bool(input_payload.get("github_sync", True)),
            "public_allowlist_summary": "首次 project-root 审批只允许生成 public staging、执行 secret scan 和本地可用性验证。",
            "public_risk_summary": "project-root 审批不授权 public push；public push 必须在 staging/secret scan/validation 通过后单独显式确认。",
            "message": "provider 恢复后继续创建项目目录。",
        }
        approval_path = store.write_json("approvals/project_root_required.json", approval)
        self._write_state(store, "blocked", current_node="project_root_approval", blocked_reason="project_root_approval_required", target_path=str(target), provider=provider.name, enable_feishu_autowrite=bool(input_payload.get("enable_feishu_autowrite")), github_sync=bool(input_payload.get("github_sync", True)), feishu_sync=bool(input_payload.get("feishu_sync", True)))
        github_sync = bool(input_payload.get("github_sync", True))
        private_repo = str(input_payload.get("private_repo") or "")
        public_repo = str(input_payload.get("public_repo") or "")
        github_text = "显式跳过" if not github_sync else (private_repo or f"默认 YaofeiHe/{recommended}")
        public_text = "；批准 project-root 后只生成 public staging/secret scan/validation，public push 仍需单独确认" if github_sync else ""
        if explicit_name:
            naming_text = f"已采用用户显式指定项目名：{recommended}"
        elif name_collision_skipped:
            naming_text = f"模型推荐项目名已存在：{model_recommended}；已改用未占用候选：{recommended}"
        else:
            naming_text = f"已由模型生成真实五字母英文项目名候选，推荐：{recommended}"
        return write_interaction(
            store,
            status="blocked",
            output=f"{naming_text}。GitHub private_repo：{github_text}；public_repo：{public_repo or (f'YaofeiHe/{recommended}-public' if github_sync else '显式跳过')}{public_text}。",
            next_prompt=f"审阅 {approval_path}；确认后运行 python -m nexus.cli approve {store.run_id} project-root。",
            blocked_reason="project_root_approval_required",
            approval_request=approval,
            artifact_refs=[str(approval_path)],
        )

    def _continue_rerun_research(self, store: RunStore, route: dict[str, object], project: Path, provider_name: str) -> dict[str, object]:
        child_idea = _continuation_idea("重新调研", store, route)
        interaction = self.run(child_idea, project_path=project, provider_name=provider_name, max_candidates=8)
        child_id = str(interaction.get("run_id") or "")
        if child_id:
            child = RunStore(self.root, child_id)
            child.write_json("continuation.json", {"schema": "nexus.continuation.v1", "parent_run_id": store.run_id, "continuation_type": "rerun_research", "route": route})
        return interaction

    def _continue_local_research(self, store: RunStore, route: dict[str, object], project: Path, provider_name: str) -> dict[str, object]:
        child_idea = _continuation_idea(f"局部调研：{route.get('scope') or '未指定范围'}", store, route)
        interaction = self.run(child_idea, project_path=project, provider_name=provider_name, max_candidates=6)
        child_id = str(interaction.get("run_id") or "")
        if child_id:
            child = RunStore(self.root, child_id)
            child.write_json("continuation.json", {"schema": "nexus.continuation.v1", "parent_run_id": store.run_id, "continuation_type": "local_research", "route": route})
        return interaction

    def _continue_chunked_research(self, store: RunStore, provider: HostModelProvider, route: dict[str, object], project: Path) -> dict[str, object]:
        plan = self._model_node_with_schema(store, provider, "chunked_research_plan", "chunked_research_plan", _chunked_research_prompt(route, store))
        plan_path = store.write_json("reports/chunked_research_plan.json", plan)
        plan_md_path = store.write_text("reports/chunked_research_plan.md", _render_chunked_research_plan(plan, store.run_id))
        self._write_state(store, "blocked", current_node="chunked_research_plan", blocked_reason="chunk_selection_required", provider=provider.name)
        update_board(project, status="已生成分块调研计划，等待选择 chunk", point=f"run {store.run_id} 已生成 chunked_research_plan")
        return write_interaction(
            store,
            status="blocked",
            output="已根据上一份调研报告生成分块调研计划；请选择一个 chunk 继续局部调研。",
            next_prompt=f"审阅 {plan_md_path}；继续示例：python -m nexus.cli continue {store.run_id} \"局部调研：provider\"",
            blocked_reason="chunk_selection_required",
            artifact_refs=[str(plan_path), str(plan_md_path)],
        )

    def _continue_update_intent(self, store: RunStore, provider: HostModelProvider, route: dict[str, object], project: Path, user_request: str) -> dict[str, object]:
        updated = self._model_node_with_schema(store, provider, "updated_intent", "updated_intent", _updated_intent_prompt(user_request, route, store))
        updated_path = store.write_json("reports/updated_intent.json", updated)
        next_options_path = self._write_next_options(
            store,
            stage="intent_updated",
            completion_quality="partial",
            summary=str(updated.get("reason") or "项目需求意图已更新。"),
            weak_points=["尚未基于新意图重新执行 discovery。"],
        )
        self._write_state(store, "blocked", current_node="intent_updated", blocked_reason="next_step_required", provider=provider.name, updated_idea=updated.get("updated_idea", ""))
        update_board(project, status="项目意图已更新，等待下一步", point=f"run {store.run_id} 更新意图：{updated.get('updated_idea', '')}")
        return write_interaction(
            store,
            status="blocked",
            output=f"已更新项目需求意图：{updated.get('updated_idea', '')}",
            next_prompt=_multi_option_prompt(
                [
                    f"选项 1：基于新意图重新调研 python -m nexus.cli continue {store.run_id} \"重新调研：{updated.get('updated_idea', '')}\"",
                    f"选项 2：生成项目计划 python -m nexus.cli continue {store.run_id} \"生成项目计划\"",
                    f"选项 3：局部调研 python -m nexus.cli continue {store.run_id} \"局部调研：<范围>\"",
                ]
            ),
            blocked_reason="next_step_required",
            artifact_refs=[str(updated_path), str(next_options_path)],
        )

    def init_project(
        self,
        idea: str,
        *,
        parent: Path,
        provider_name: str = "auto",
        enable_feishu_autowrite: bool = False,
        private_repo: str = "",
        public_repo: str = "",
        github_sync: bool = True,
        feishu_sync: bool = True,
        raw_user_request: str = "",
        normalized_request: str = "",
    ) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        parent = parent.expanduser().resolve()
        raw_user_request = raw_user_request if raw_user_request else idea
        normalized_request = normalized_request if normalized_request else idea
        selected_provider = self._resolve_provider_name(provider_name, idea)
        enable_feishu_autowrite = enable_feishu_autowrite or _wants_feishu_autowrite(idea)
        github_sync = github_sync and not _wants_skip_github_sync(idea)
        feishu_sync = feishu_sync and not wants_skip_feishu_sync(idea)
        parsed_private_repo, parsed_public_repo = _github_repos_from_text(idea)
        private_repo = private_repo or parsed_private_repo
        public_repo = public_repo or parsed_public_repo
        store.write_json(
            "input.json",
            {
                "schema": "nexus.project_init_input.v1",
                "idea": idea,
                "raw_user_request": raw_user_request,
                "normalized_request": normalized_request,
                "parent": str(parent),
                "provider": selected_provider,
                "requested_provider": provider_name,
                "enable_feishu_autowrite": enable_feishu_autowrite,
                "github_sync": github_sync,
                "feishu_sync": feishu_sync,
                "private_repo": private_repo,
                "public_repo": public_repo,
            },
        )
        try:
            raw_explicit_name = _explicit_project_name_from_idea(raw_user_request)
            operational_explicit_name = _explicit_project_name_from_idea(idea)
        except ValueError as exc:
            failure_path = store.write_json("tool_results/explicit_project_name_failure.json", {"schema": "nexus.explicit_project_name_failure.v1", "reason": str(exc), "idea": idea})
            self._write_state(store, "blocked", current_node="explicit_project_name", blocked_reason="explicit_project_name_invalid")
            return write_interaction(
                store,
                status="blocked",
                output=f"用户显式指定的项目名不安全，不能继续初始化：{exc}",
                next_prompt=f"请修正项目名后重新运行 $nexus-workflow 初始化项目；详情见 {failure_path}",
                blocked_reason="explicit_project_name_invalid",
                artifact_refs=[str(failure_path)],
            )
        if operational_explicit_name and not raw_explicit_name:
            failure_path = store.write_json(
                "tool_results/agent_injected_project_name_failure.json",
                {
                    "schema": "nexus.agent_injected_project_name_failure.v1",
                    "operational_explicit_name": operational_explicit_name,
                    "raw_user_request": raw_user_request,
                    "operational_idea": idea,
                    "reason": "operational_idea_contains_explicit_project_name_but_raw_user_request_does_not",
                },
            )
            self._write_state(store, "blocked", current_node="explicit_project_name", blocked_reason="explicit_project_name_agent_injected")
            return write_interaction(
                store,
                status="blocked",
                output=f"初始化输入包含项目名 `{operational_explicit_name}`，但原始用户请求没有显式指定同一项目名；不能把外层工具注入的名称当成用户显式命名。",
                next_prompt=f"请让用户确认项目名，或移除注入命名后重新运行 $nexus-workflow 初始化项目；详情见 {failure_path}",
                blocked_reason="explicit_project_name_agent_injected",
                artifact_refs=[str(failure_path)],
            )
        if raw_explicit_name and operational_explicit_name and raw_explicit_name != operational_explicit_name:
            failure_path = store.write_json(
                "tool_results/explicit_project_name_mismatch.json",
                {
                    "schema": "nexus.explicit_project_name_mismatch.v1",
                    "raw_explicit_name": raw_explicit_name,
                    "operational_explicit_name": operational_explicit_name,
                    "reason": "raw_user_request_and_operational_idea_disagree",
                },
            )
            self._write_state(store, "blocked", current_node="explicit_project_name", blocked_reason="explicit_project_name_mismatch")
            return write_interaction(
                store,
                status="blocked",
                output=f"原始用户请求指定 `{raw_explicit_name}`，但运行输入指定 `{operational_explicit_name}`；项目名来源不一致，不能继续初始化。",
                next_prompt=f"请修正项目名来源后重新运行 $nexus-workflow 初始化项目；详情见 {failure_path}",
                blocked_reason="explicit_project_name_mismatch",
                artifact_refs=[str(failure_path)],
            )
        explicit_name = raw_explicit_name
        provider = self._select_provider(store, selected_provider, parent)
        if provider is None:
            return self._block_for_provider_setup(store)
        try:
            candidates = self._model_node(store, provider, "project_name_candidates", _project_name_prompt(idea, parent))
        except (ProviderUnavailable, ProviderExecutionError, ValueError) as exc:
            if explicit_name:
                candidates = {"schema": "nexus.project_name_candidates.v1", "recommended": "", "candidates": []}
                store.write_json(
                    "tool_results/project_name_model_warning.json",
                    {
                        "schema": "nexus.project_name_model_warning.v1",
                        "provider": provider.name,
                        "reason": str(exc),
                        "policy": "explicit_user_name_overrides_model_candidates",
                        "explicit_user_name": explicit_name,
                    },
                )
            else:
                failure = {
                    "schema": "nexus.project_name_model_failure.v1",
                    "provider": provider.name,
                    "reason": str(exc),
                    "policy": "project initialization requires a real valid five-letter English word name; Nexus will not fabricate or downgrade naming offline.",
                }
                failure_path = store.write_json("tool_results/project_name_model_failure.json", failure)
                self._write_state(store, "blocked", current_node="project_name_candidates", blocked_reason="project_name_model_failed", provider=provider.name)
                return write_interaction(
                    store,
                    status="blocked",
                    output=f"项目命名模型失败，不能继续初始化：{exc}",
                    next_prompt=f"修复真实模型/命名输出后重试：python -m nexus.cli recover {store.run_id} \"恢复项目命名\"",
                    blocked_reason="project_name_model_failed",
                    artifact_refs=[str(failure_path)],
                )
        model_recommended = str(candidates.get("recommended") or "")
        name_collision_skipped: list[str] = []
        if explicit_name:
            recommended = explicit_name
        else:
            recommended, name_collision_skipped = _select_non_colliding_project_name(candidates, model_recommended, parent)
            if not recommended:
                failure_path = store.write_json("tool_results/project_name_collision.json", {"schema": "nexus.project_name_collision.v1", "model_recommended": model_recommended, "collisions": name_collision_skipped, "parent": str(parent)})
                self._write_state(store, "blocked", current_node="project_name_candidates", blocked_reason="project_name_collision", provider=provider.name)
                return write_interaction(
                    store,
                    status="blocked",
                    output=f"项目命名候选均已存在，不能把已有目录当成从零新建项目：{', '.join(name_collision_skipped)}",
                    next_prompt=f"请指定明确项目名后重新运行 $nexus-workflow 初始化项目；详情见 {failure_path}",
                    blocked_reason="project_name_collision",
                    artifact_refs=[str(failure_path)],
                )
        target = parent / recommended
        name_source = "user_raw" if explicit_name else ("model_recommended" if recommended == model_recommended else "model_candidate_non_colliding")
        naming_policy = "explicit_user_name_overrides_model_candidates" if explicit_name else ("model_recommended_real_five_letter_word" if recommended == model_recommended else "model_candidate_non_colliding_real_five_letter_word")
        approval = {
            "schema": "nexus.project_root_approval.v1",
            "recommended": recommended,
            "model_recommended": model_recommended,
            "explicit_user_name": explicit_name,
            "name_source": name_source,
            "naming_policy": naming_policy,
            "name_collision_skipped": name_collision_skipped,
            "target_path": str(target),
            "candidates": candidates.get("candidates", []),
            "enable_feishu_autowrite": enable_feishu_autowrite,
            "github_sync": github_sync,
            "feishu_sync": feishu_sync,
            "private_repo": private_repo,
            "public_repo": public_repo,
            "initial_public_sync": False,
            "initial_public_staging": github_sync,
            "public_sync_requires_project_root_approval": False,
            "public_sync_requires_explicit_confirmation": github_sync,
            "public_allowlist_summary": "首次 project-root 审批只允许生成 public staging、执行 secret scan 和本地可用性验证。",
            "public_risk_summary": "project-root 审批不授权 public push；public push 必须在 staging/secret scan/validation 通过后单独显式确认。",
            "message": "创建项目目录前必须确认。",
        }
        approval_path = store.write_json("approvals/project_root_required.json", approval)
        self._write_state(store, "blocked", current_node="project_root_approval", blocked_reason="project_root_approval_required", target_path=str(target), provider=provider.name, enable_feishu_autowrite=enable_feishu_autowrite, github_sync=github_sync, feishu_sync=feishu_sync)
        github_text = "显式跳过" if not github_sync else (private_repo or f"默认 YaofeiHe/{recommended}")
        public_text = "；批准 project-root 后只生成 public staging/secret scan/validation，public push 仍需单独确认" if github_sync else ""
        if explicit_name:
            naming_text = f"已采用用户显式指定项目名：{recommended}"
        elif name_collision_skipped:
            naming_text = f"模型推荐项目名已存在：{model_recommended}；已改用未占用候选：{recommended}"
        else:
            naming_text = f"已由模型生成真实五字母英文项目名候选，推荐：{recommended}"
        return write_interaction(
            store,
            status="blocked",
            output=f"{naming_text}。GitHub private_repo：{github_text}；public_repo：{public_repo or (f'YaofeiHe/{recommended}-public' if github_sync else '显式跳过')}{public_text}。",
            next_prompt=f"审阅 {approval_path}；确认后运行 python -m nexus.cli approve {store.run_id} project-root。",
            blocked_reason="project_root_approval_required",
            approval_request=approval,
            artifact_refs=[str(approval_path)],
        )

    def prepare_project(self, project_path: Path) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        store.write_json("input.json", {"schema": "nexus.prepare_project_input.v1", "project_path": str(project)})
        if not project.exists() or not project.is_dir():
            self._write_state(store, "blocked", current_node="prepare_project", blocked_reason="project_path_not_found", project_path=str(project))
            return write_interaction(
                store,
                status="blocked",
                output=f"目标项目目录不存在：{project}",
                next_prompt="请先创建目标项目目录，或使用 python -m nexus.cli init-project 进入项目初始化流程。",
                blocked_reason="project_path_not_found",
            )
        self._write_active_project(project, source="prepare-project", run_id=store.run_id)
        plan = inspect_git_baseline(project)
        plan_path = store.write_json("tool_results/git_baseline_plan.json", plan.to_dict())
        if plan.is_git_repo and plan.has_commits:
            self._write_state(store, "completed", current_node="git_baseline_ready", project_path=str(project))
            return write_interaction(
                store,
                status="completed",
                output="目标项目已经是有 commit 的 git repo，可以进入安全代码修改链路。",
                next_prompt="下一步可以运行 nexus 调研、生成 implementation plan，或执行 code-change 链路。",
                artifact_refs=[str(plan_path)],
            )
        if plan.sensitive_hits:
            self._write_state(store, "blocked", current_node="git_baseline_safety", blocked_reason="sensitive_files_detected", project_path=str(project))
            return write_interaction(
                store,
                status="blocked",
                output=f"发现疑似敏感文件，拒绝自动 git baseline：{', '.join(plan.sensitive_hits[:8])}",
                next_prompt=f"请处理这些文件后重试；详情见 {plan_path}",
                blocked_reason="sensitive_files_detected",
                artifact_refs=[str(plan_path)],
            )
        approval = {
            "schema": "nexus.git_baseline_approval.v1",
            "project_path": str(project),
            "message": "目标项目尚未建立可审计 baseline；创建 git baseline 前必须确认。",
            "suggested_gitignore": plan.suggested_gitignore,
            "command": f"python -m nexus.cli approve {store.run_id} git-baseline",
        }
        approval_path = store.write_json("approvals/git_baseline_required.json", approval)
        self._write_state(store, "blocked", current_node="git_baseline_approval", blocked_reason="git_baseline_approval_required", project_path=str(project))
        return write_interaction(
            store,
            status="blocked",
            output="目标项目还没有可审计 git baseline；已生成 baseline 审批卡。",
            next_prompt=f"审阅 {approval_path}；确认后运行 python -m nexus.cli approve {store.run_id} git-baseline。",
            blocked_reason="git_baseline_approval_required",
            approval_request=approval,
            artifact_refs=[str(plan_path), str(approval_path)],
            lifecycle_status="awaiting_approval",
            pending_actions=[
                {
                    "action_id": "approve_git_baseline",
                    "kind": "runner_command",
                    "command": f"python -m nexus.cli approve-and-continue {store.run_id} git-baseline",
                    "requires_host_permission": False,
                }
            ],
            recovery_mode=True,
            recovery_state="recoverable_via_approve_and_continue",
            recovery_kind="approval",
            recommended_executor="outer_codex",
        )

    def rerank_candidates(self, run_id: str, *, provider_name: str = "auto") -> dict[str, object]:
        actual_run_id = self._resolve_run_id(run_id)
        store = RunStore(self.root, actual_run_id)
        input_payload = store.read_json("input.json")
        project = Path(str(input_payload.get("project_path") or ".")).expanduser().resolve()
        selected_provider = self._resolve_provider_name(provider_name, "")
        provider = self._select_provider(store, selected_provider, project)
        if provider is None:
            return self._block_for_provider_setup(store)
        candidate_path = store.path("candidates", "candidates.jsonl")
        if not candidate_path.exists():
            return write_interaction(
                store,
                status="blocked",
                output="没有找到可重排的候选 artifact。",
                next_prompt="先运行 nexus research 产生 candidates/candidates.jsonl。",
                blocked_reason="candidate_artifact_missing",
            )
        candidates = [json.loads(line) for line in candidate_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        processed = process_candidates(candidates, max_candidates=int(input_payload.get("max_candidates") or 8))
        store.write_jsonl("candidates/normalized_candidates.jsonl", processed["normalized"])
        store.write_json("candidates/merged_candidates.json", {"schema": "nexus.merged_candidates.v1", "candidates": processed["merged"]})
        store.write_json("candidates/ranking_features.json", {"schema": "nexus.ranking_features.v1", "features": processed["ranking_features"]})
        store.write_json("candidates/model_review_input.json", {"schema": "nexus.model_review_input.v1", "candidates": processed["model_input"]})
        statuses_payload = store.read_json("tool_results/source_status.json") if store.path("tool_results", "source_status.json").exists() else {"sources": []}
        statuses = [_status_from_dict(item) for item in statuses_payload.get("sources", []) if isinstance(item, dict)]
        research_contract = _read_json_if_exists(store.path("reports", "research_contract.json"))
        review = self._model_node_with_schema(
            store,
            provider,
            "candidate_review_rerank",
            "candidate_review",
            _review_prompt(str(input_payload.get("idea") or ""), processed["model_input"], statuses, research_contract),
        )
        ranked = rerank_candidates(processed["merged"], review)
        ranked_path = store.write_json("candidates/ranked_candidates.json", {"schema": "nexus.ranked_candidates.v1", "candidates": ranked})
        self._write_state(store, "completed", current_node="candidate_rerank", provider=provider.name)
        return write_interaction(
            store,
            status="completed",
            output="已对上一轮候选完成归一化、去重、证据合并和模型辅助重排。",
            next_prompt=f"查看候选排行：{ranked_path}",
            artifact_refs=[str(ranked_path), str(store.path("candidates", "merged_candidates.json")), str(store.path("candidates", "ranking_features.json"))],
        )

    def conversation_from_file(self, transcript_path: Path, *, provider_name: str = "auto", selector: str = "") -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        path = transcript_path.expanduser().resolve()
        if not path.exists():
            self._write_state(store, "blocked", current_node="conversation_import", blocked_reason="conversation_export_file_required")
            return write_interaction(
                store,
                status="blocked",
                output="无法从当前 Codex UI 自动提取对话历史；需要先提供导出的对话文本文件。",
                next_prompt="请导出对话历史为 markdown/txt 后重试 python -m nexus.cli conversation-from-file <path>。",
                blocked_reason="conversation_export_file_required",
            )
        selected_provider = self._resolve_provider_name(provider_name, "")
        provider = self._select_provider(store, selected_provider, path.parent)
        if provider is None:
            return self._block_for_provider_setup(store)
        imported = import_conversation(path)
        store.write_json("conversation/input_manifest.json", {key: value for key, value in imported.items() if key != "messages"})
        store.write_jsonl("conversation/messages.jsonl", [item for item in imported["messages"] if isinstance(item, dict)])
        selected = select_messages([item for item in imported["messages"] if isinstance(item, dict)], selector)
        if not selected.get("messages"):
            self._write_state(store, "blocked", current_node="conversation_selection", blocked_reason="conversation_selection_empty", provider=provider.name)
            return write_interaction(
                store,
                status="blocked",
                output="对话历史已导入，但 selector 没有选中任何消息。",
                next_prompt="请换一个问题编号/ref/关键词，或不传 --selector 使用全部对话。",
                blocked_reason="conversation_selection_empty",
                artifact_refs=[str(store.path("conversation", "messages.jsonl"))],
            )
        store.write_json("conversation/selected_messages.json", selected)
        text = _conversation_text([item for item in selected["messages"] if isinstance(item, dict)])[:24000]
        result = self._model_node(store, provider, "conversation_workflow", _conversation_prompt(text, selector))
        store.write_json("reports/conversation_workflow.json", result)
        draft_path = store.write_text("reports/generalized_workflow.md", _render_conversation_workflow(result))
        skill_path = store.write_text("drafts/SKILL.md", _skill_draft_from_conversation(result))
        decision_path = store.write_json(
            "reports/skill_or_workflow_decision.json",
            {
                "schema": "nexus.skill_or_workflow_decision.v1",
                "decision": "draft_generated",
                "source": str(path),
                "selector": selector or "all",
                "draft_skill_ref": str(skill_path),
                "workflow_ref": str(draft_path),
                "requires_install_approval": True,
            },
        )
        self._write_state(store, "completed", current_node="conversation_workflow", provider=provider.name)
        return write_interaction(
            store,
            status="completed",
            output="已从真实导出的对话历史中生成通用 workflow 和 SKILL.md 草案。",
            next_prompt=f"审阅 {draft_path} 和 {skill_path}；确认安装可运行 python -m nexus.cli install-generated-skill {store.run_id} --confirm。",
            artifact_refs=[str(draft_path), str(skill_path), str(decision_path)],
        )

    def conversation_sessions(self) -> dict[str, object]:
        return discover_codex_sessions()

    def conversation_to_workflow(
        self,
        *,
        current: bool = False,
        all_history: bool = False,
        match: str = "",
        task: str = "",
        session_id: str = "",
        file_path: str = "",
        selector: str = "",
        provider_name: str = "auto",
    ) -> dict[str, object]:
        if file_path:
            return self.conversation_from_file(Path(file_path), provider_name=provider_name, selector=selector)
        store = RunStore(self.root)
        store.ensure()
        manifest = discover_codex_sessions()
        manifest_path = store.write_json("conversation/session_manifest.json", manifest)
        selection = choose_session(manifest, current=current or not any([all_history, match, task, session_id]), all_history=all_history, match=match, task=task, session_id=session_id)
        selection_path = store.write_json("conversation/source_selection.json", selection)
        store.write_json(
            "input.json",
            {
                "schema": "nexus.conversation_to_workflow_input.v1",
                "provider": self._resolve_provider_name(provider_name, ""),
                "requested_provider": provider_name,
                "project_path": str(self.root),
                "current": current,
                "all_history": all_history,
                "match": match,
                "task": task,
                "session_id": session_id,
                "selector": selector,
                "selection_ref": str(selection_path),
            },
        )
        if selection.get("status") != "selected":
            self._write_state(store, "blocked", current_node="conversation_source_selection", blocked_reason=str(selection.get("reason") or "conversation_session_not_selected"))
            return write_interaction(
                store,
                status="blocked",
                output=f"没有唯一可用的 Codex session：{selection.get('reason')}",
                next_prompt="运行 python -m nexus.cli conversation sessions 查看可用 session，或使用 --file <transcript> 指定真实导出文件。",
                blocked_reason=str(selection.get("reason") or "conversation_session_not_selected"),
                artifact_refs=[str(manifest_path), str(selection_path)],
            )
        approval = {
            "schema": "nexus.conversation_session_read_approval.v1",
            "message": "读取本地 Codex session 前必须确认；nexus 会在送入模型前脱敏 token/cookie/key。",
            "selected": selection.get("selected", []),
            "command": f"python -m nexus.cli approve {store.run_id} conversation-session-read",
        }
        approval_path = store.write_json("approvals/conversation_session_read_required.json", approval)
        self._write_state(store, "blocked", current_node="conversation_session_read_approval", blocked_reason="conversation_session_read_approval_required", provider=provider_name)
        return write_interaction(
            store,
            status="blocked",
            output="已定位到可读取的 Codex session；读取本地对话历史前需要审批。",
            next_prompt=f"审阅 {approval_path}；确认后运行 python -m nexus.cli approve {store.run_id} conversation-session-read，然后 python -m nexus.cli resume {store.run_id}。",
            blocked_reason="conversation_session_read_approval_required",
            approval_request=approval,
            artifact_refs=[str(manifest_path), str(selection_path), str(approval_path)],
        )

    def _resume_conversation_to_workflow(self, store: RunStore) -> dict[str, object]:
        input_payload = store.read_json("input.json")
        if not store.path("approvals", "APPROVED_conversation-session-read.json").exists():
            return write_interaction(
                store,
                status="blocked",
                output="尚未审批读取本地 Codex session。",
                next_prompt=f"先运行 python -m nexus.cli approve {store.run_id} conversation-session-read",
                blocked_reason="conversation_session_read_approval_required",
            )
        selection = store.read_json("conversation/source_selection.json")
        selected = selection.get("selected") if isinstance(selection.get("selected"), list) else []
        if not selected:
            return write_interaction(
                store,
                status="blocked",
                output="没有可导入的 Codex session。",
                next_prompt="重新运行 conversation-to-workflow 并指定 --session-id/--match/--file。",
                blocked_reason="conversation_session_not_selected",
            )
        imported_messages: list[dict[str, Any]] = []
        sources: list[str] = []
        for item in selected:
            if not isinstance(item, dict):
                continue
            path = Path(str(item.get("path") or "")).expanduser()
            imported = import_codex_session(path)
            imported_messages.extend([message for message in imported.get("messages", []) if isinstance(message, dict)])
            sources.append(str(path))
        store.write_json("conversation/imported_sessions.json", {"schema": "nexus.imported_codex_sessions.v1", "sources": sources, "message_count": len(imported_messages)})
        store.write_jsonl("conversation/raw_messages.jsonl", imported_messages)
        redacted = redact_messages(imported_messages)
        store.write_json("conversation/sensitive_redactions.json", {"schema": "nexus.sensitive_redactions.v1", "findings": redacted.get("findings", [])})
        redacted_messages = [message for message in redacted.get("messages", []) if isinstance(message, dict)]
        store.write_jsonl("conversation/redacted_messages.jsonl", redacted_messages)
        selected_messages = select_messages(redacted_messages, str(input_payload.get("selector") or input_payload.get("match") or input_payload.get("task") or ""))
        if not selected_messages.get("messages"):
            self._write_state(store, "blocked", current_node="conversation_selection", blocked_reason="conversation_selection_empty")
            return write_interaction(
                store,
                status="blocked",
                output="Codex session 已导入并脱敏，但没有消息匹配 selector。",
                next_prompt="改用 --all 或换一个 --match/--selector 后重试。",
                blocked_reason="conversation_selection_empty",
                artifact_refs=[str(store.path("conversation", "redacted_messages.jsonl"))],
            )
        store.write_json("conversation/selected_messages.json", selected_messages)
        provider = self._select_provider(store, str(input_payload.get("provider") or "auto"), self.root)
        if provider is None:
            return self._block_for_provider_setup(store)
        text = _conversation_text([item for item in selected_messages["messages"] if isinstance(item, dict)])[:24000]
        result = self._model_node(store, provider, "conversation_workflow", _conversation_prompt(text, str(input_payload.get("selector") or "")))
        store.write_json("reports/conversation_workflow.json", result)
        draft_path = store.write_text("reports/generalized_workflow.md", _render_conversation_workflow(result))
        skill_path = store.write_text("drafts/SKILL.md", _skill_draft_from_conversation(result))
        decision_path = store.write_json(
            "reports/skill_or_workflow_decision.json",
            {
                "schema": "nexus.skill_or_workflow_decision.v1",
                "decision": "draft_generated_from_codex_session",
                "sources": sources,
                "draft_skill_ref": str(skill_path),
                "workflow_ref": str(draft_path),
                "requires_install_approval": True,
            },
        )
        self._write_state(store, "completed", current_node="conversation_workflow", provider=provider.name)
        return write_interaction(
            store,
            status="completed",
            output="已从本地 Codex session 中导入、脱敏并生成 workflow/SKILL.md 草案。",
            next_prompt=f"审阅 {draft_path} 和 {skill_path}；确认安装可运行 python -m nexus.cli install-generated-skill {store.run_id} --confirm。",
            artifact_refs=[str(draft_path), str(skill_path), str(decision_path)],
        )

    def install_generated_skill(self, run_id: str, *, confirm: bool = False) -> dict[str, object]:
        actual_run_id = self._resolve_run_id(run_id)
        store = RunStore(self.root, actual_run_id)
        result = install_generated_skill(store.run_dir, confirm=confirm)
        result_path = store.write_json("tool_results/skill_install_result.json", result)
        if result.get("status") != "completed":
            self._write_state(store, "blocked", current_node="skill_install", blocked_reason=str(result.get("reason") or "skill_install_blocked"))
            return write_interaction(
                store,
                status="blocked",
                output=str(result.get("message") or result.get("reason") or "skill 安装被阻断。"),
                next_prompt=f"确认后运行 python -m nexus.cli install-generated-skill {actual_run_id} --confirm，或 python -m nexus.cli approve {actual_run_id} skill-install。",
                blocked_reason=str(result.get("reason") or "skill_install_blocked"),
                artifact_refs=[str(result_path)],
            )
        self._write_state(store, "completed", current_node="skill_installed", skill_name=result.get("skill_name", ""))
        return write_interaction(
            store,
            status="completed",
            output=f"已安装 skill：{result.get('skill_name')} -> {result.get('target')}",
            next_prompt=f"在 Codex 输入框测试：${result.get('skill_name')} <你的任务>；如未热加载，请重启会话后再测。",
            artifact_refs=[str(result_path)],
        )

    def conversation_manager_init(self, project_path: Path) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        result = init_conversation_manager(project)
        path = store.write_json("tool_results/conversation_manager_init.json", result)
        self._write_state(store, "completed", current_node="conversation_manager_init", project_path=str(project))
        return write_interaction(
            store,
            status="completed",
            output=f"已初始化 conversation-manager：{result.get('root')}",
            next_prompt="下一步可 ingest transcript，或使用 conversation-to-workflow 从 Codex session 生成 workflow/skill。",
            artifact_refs=[str(path)],
        )

    def conversation_manager_ingest(self, project_path: Path, file_path: Path, *, source_agent: str = "codex") -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        result = ingest_transcript(project, file_path, source_agent=source_agent)
        path = store.write_json("tool_results/conversation_ingest.json", result)
        status = "completed" if result.get("status") == "completed" else "blocked"
        self._write_state(store, status, current_node="conversation_ingest", project_path=str(project), blocked_reason="" if status == "completed" else str(result.get("reason") or "conversation_ingest_blocked"))
        return write_interaction(
            store,
            status=status,
            output=f"conversation ingest 结果：{result.get('reason') or result.get('session_id')}",
            next_prompt="下一步可运行 conversation-manager promote 生成 skill/workflow/poc/prompt。",
            blocked_reason="" if status == "completed" else str(result.get("reason") or "conversation_ingest_blocked"),
            artifact_refs=[str(path)],
        )

    def conversation_manager_promote(self, project_path: Path, session_file: Path, *, target: str = "auto", provider_name: str = "auto") -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        if not session_file.expanduser().exists():
            self._write_state(store, "blocked", current_node="conversation_promote", blocked_reason="session_file_not_found")
            return write_interaction(store, status="blocked", output=f"未找到 session 文件：{session_file}", next_prompt="先运行 conversation-manager ingest，或传入 docs/ai-conversations/sessions/<id>.md。", blocked_reason="session_file_not_found")
        provider = self._select_provider(store, self._resolve_provider_name(provider_name, ""), project)
        if provider is None:
            return self._block_for_provider_setup(store)
        text = session_file.expanduser().read_text(encoding="utf-8", errors="ignore")[:24000]
        result = self._model_node(store, provider, "conversation_workflow", _conversation_prompt(text, f"promote_to:{target}"))
        session_id = session_file.stem
        summary_path = write_summary(project, session_id, _summary_payload_from_workflow(result))
        workflow_path = write_workflow(project, session_id, {"title": result.get("generalized_project_type", session_id), "steps": result.get("workflow_blueprint", []), "safety": result.get("safety_notes", [])})
        prompt_path = write_prompt_pack(project, session_id, {"background": result.get("source_summary", ""), "goal": result.get("generalized_project_type", ""), "requirements": result.get("workflow_blueprint", []), "acceptance": result.get("next_options", [])})
        skill_dir = project / "docs" / "ai-conversations" / "skill-candidates" / session_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(_skill_draft_from_conversation(result), encoding="utf-8")
        report = store.write_json("reports/conversation_promote.json", {"schema": "nexus.conversation_promote.v1", "summary": str(summary_path), "workflow": str(workflow_path), "prompt": str(prompt_path), "skill": str(skill_path)})
        self._write_state(store, "completed", current_node="conversation_promote", provider=provider.name, project_path=str(project))
        return write_interaction(store, status="completed", output="已把对话沉淀为 summary/workflow/prompt/skill candidate。", next_prompt=f"审阅 {summary_path}、{workflow_path}、{prompt_path}、{skill_path}。", artifact_refs=[str(report)])

    def github_sync_configure(self, project_path: Path, *, private_repo: str, public_repo: str = "") -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        path = write_github_sync_config(project, private_repo, public_repo, project_kind=_project_kind(project))
        self._write_state(store, "completed", current_node="github_sync_configure", project_path=str(project))
        return write_interaction(store, status="completed", output=f"已写入 GitHub 同步配置：{path}", next_prompt="下一步可运行 github-sync private；public 同步需要 --confirm。", artifact_refs=[str(path)])

    def _github_bootstrap_flow(
        self,
        store: RunStore,
        project: Path,
        *,
        private_repo: str = "",
        public_repo: str = "",
        create_remote_repos: bool = True,
        commit_message: str = "",
        continuation_operation: str = "github_bootstrap",
        continuation_note: str = "",
        artifact_name: str = "github_bootstrap.json",
        current_node: str = "github_sync_bootstrap",
    ) -> dict[str, object]:
        project = project.expanduser().resolve()
        if not private_repo:
            private_repo, inferred_public_repo = _default_github_repos(project)
            public_repo = public_repo or inferred_public_repo
        if private_repo:
            config_path = write_github_sync_config(project, private_repo, public_repo, project_kind=_project_kind(project))
        else:
            config_path = project / ".github" / "nexus-sync.json"
        config = load_github_sync_config(project)
        if config is None:
            reason = "github_sync_config_missing_and_repo_target_unknown"
            self._write_state(store, "blocked", current_node=current_node, blocked_reason=reason, project_path=str(project), continuation_note=continuation_note)
            return write_interaction(
                store,
                status="blocked",
                output="缺少 .github/nexus-sync.json，且无法从项目名推断 GitHub 仓库。",
                next_prompt="运行 github-sync bootstrap --project-path <path> --private-repo OWNER/private --public-repo OWNER/public。",
                blocked_reason=reason,
            )
        commit_message = commit_message or f"bootstrap {project.name}"
        result = bootstrap_project(project, config, create_remote_repos=create_remote_repos, commit_message=commit_message)
        auth_refs: list[str] = []
        if result.get("reason") == "gh_auth_required":
            pre_auth_path = store.write_json(f"tool_results/{artifact_name}", result)
            auth = run_github_auth_login(project, browser_mode="native")
            auth_refs = [str(auth.get("request_path") or ""), str(auth.get("status_path") or ""), str(auth.get("log_path") or ""), str(auth.get("host_capability_request_path") or "")]
            if auth.get("state") == "AUTH_VERIFIED":
                result = bootstrap_project(project, load_github_sync_config(project) or config, create_remote_repos=create_remote_repos, commit_message=commit_message)
            else:
                auth_path = store.write_json("tool_results/github_auth_login.json", auth)
                return self._block_for_github_external_login(
                    store,
                    project,
                    auth,
                    continuation_operation=continuation_operation,
                    commit_message=commit_message,
                    target="auto",
                    feishu_sync_enabled=True,
                    artifact_refs=[str(pre_auth_path), str(auth_path), *[ref for ref in auth_refs if ref]],
                    continuation_extra={
                        "private_repo": private_repo,
                        "public_repo": public_repo,
                        "create_remote_repos": create_remote_repos,
                        "force_bootstrap": True,
                    },
                )
        result_path = store.write_json(f"tool_results/{artifact_name}", result)
        if _github_result_contains_api_eof(result):
            return self._block_for_github_api_retry(
                store,
                project,
                result,
                continuation_operation=continuation_operation,
                commit_message=commit_message,
                target="auto",
                feishu_sync_enabled=True,
                artifact_refs=[str(config_path), str(result_path), *[ref for ref in auth_refs if ref]],
                continuation_extra={
                    "private_repo": private_repo,
                    "public_repo": public_repo,
                    "create_remote_repos": create_remote_repos,
                    "force_bootstrap": True,
                },
            )
        status = "completed" if result.get("status") == "completed" else "blocked"
        reason = "bootstrapped_and_pushed_private" if status == "completed" else str(result.get("reason") or "github_bootstrap_blocked")
        self._write_state(store, status, current_node=current_node, project_path=str(project), blocked_reason="" if status == "completed" else reason, continuation_note=continuation_note)
        return write_interaction(
            store,
            status=status,
            output=f"GitHub bootstrap 结果：{result.get('reason')}",
            next_prompt="成功后普通修改会走 github-sync auto-private；public 发布仍需 github-sync public --confirm。",
            blocked_reason="" if status == "completed" else reason,
            artifact_refs=[str(config_path), str(result_path), *[ref for ref in auth_refs if ref]],
            terminal=status == "completed",
        )

    def github_sync_bootstrap(self, project_path: Path, *, private_repo: str = "", public_repo: str = "", create_remote_repos: bool = True) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        return self._github_bootstrap_flow(
            store,
            project,
            private_repo=private_repo,
            public_repo=public_repo,
            create_remote_repos=create_remote_repos,
            commit_message=f"bootstrap {project.name}",
            continuation_operation="github_bootstrap",
        )

    def _github_private_flow(self, store: RunStore, project: Path, *, continuation_note: str = "") -> dict[str, object]:
        project = project.expanduser().resolve()
        config = load_github_sync_config(project)
        if config is None:
            self._write_state(store, "blocked", current_node="github_sync_private", blocked_reason="github_sync_config_missing")
            return write_interaction(store, status="blocked", output="缺少 .github/nexus-sync.json，不能同步 GitHub private。", next_prompt="先运行 python -m nexus.cli github-sync configure --private-repo OWNER/private --public-repo OWNER/public。", blocked_reason="github_sync_config_missing")
        result = auto_private_sync(
            project,
            {**config, "default_private_sync": True},
            commit_message=f"sync {project.name} to GitHub private",
        )
        auth_refs: list[str] = []
        if result.get("reason") == "gh_auth_required":
            auth = run_github_auth_login(project, browser_mode="native")
            auth_refs = [str(auth.get("request_path") or ""), str(auth.get("status_path") or ""), str(auth.get("log_path") or ""), str(auth.get("host_capability_request_path") or "")]
            if auth.get("state") == "AUTH_VERIFIED":
                result = auto_private_sync(
                    project,
                    {**config, "default_private_sync": True},
                    commit_message=f"sync {project.name} to GitHub private",
                )
            else:
                auth_path = store.write_json("tool_results/github_auth_login.json", auth)
                return self._block_for_github_external_login(
                    store,
                    project,
                    auth,
                    continuation_operation="github_private",
                    commit_message="",
                    target="private",
                    feishu_sync_enabled=False,
                    artifact_refs=[str(auth_path), *[ref for ref in auth_refs if ref]],
                )
        path = store.write_json("tool_results/github_private_sync.json", result)
        if _github_result_contains_api_eof(result):
            return self._block_for_github_api_retry(
                store,
                project,
                result,
                continuation_operation="github_private",
                commit_message="",
                target="private",
                feishu_sync_enabled=False,
                artifact_refs=[str(path), *[ref for ref in auth_refs if ref]],
            )
        status = "completed" if result.get("status") == "completed" else "blocked"
        self._write_state(store, status, current_node="github_sync_private", blocked_reason="" if status == "completed" else str(result.get("reason") or "github_sync_private_blocked"), continuation_note=continuation_note)
        return write_interaction(store, status=status, output=f"GitHub private 同步结果：{result.get('reason')}", next_prompt="如果成功，可继续 public staging。", blocked_reason="" if status == "completed" else str(result.get("reason") or ""), artifact_refs=[str(path), *[ref for ref in auth_refs if ref]])

    def github_sync_private(self, project_path: Path) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        return self._github_private_flow(store, project_path)

    def _github_auto_private_flow(
        self,
        store: RunStore,
        project: Path,
        *,
        commit_message: str = "nexus auto private sync",
        continuation_operation: str = "github_auto_private",
        continuation_note: str = "",
        target: str = "auto",
        feishu_sync_enabled: bool = True,
        artifact_name: str | None = None,
        current_node: str | None = None,
        force_bootstrap: bool = False,
        continuation_extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        project = project.expanduser().resolve()
        config = load_github_sync_config(project)
        if config is None or force_bootstrap:
            private_repo, public_repo = _default_github_repos(project)
            if not private_repo and config is None:
                self._write_state(store, "blocked", current_node="github_sync_auto_private", blocked_reason="github_sync_config_missing_and_repo_target_unknown", project_path=str(project))
                return write_interaction(store, status="blocked", output="缺少 .github/nexus-sync.json，且该项目不是可自动推断仓库的 nexus/verix 自身项目。", next_prompt="先运行 github-sync bootstrap --project-path <path> --private-repo OWNER/private --public-repo OWNER/public。", blocked_reason="github_sync_config_missing_and_repo_target_unknown")
            if config is None:
                config = _write_and_load_github_config(project, private_repo, public_repo)
            result = bootstrap_project(project, config, create_remote_repos=True, commit_message=f"bootstrap {project.name}")
            default_artifact_name = "github_auto_private_bootstrap.json"
            default_current_node = "github_sync_auto_private_bootstrap"
        else:
            result = auto_private_sync(project, config, commit_message=commit_message)
            default_artifact_name = "github_auto_private_sync.json"
            default_current_node = "github_sync_auto_private"
        artifact_name = artifact_name or default_artifact_name
        current_node = current_node or default_current_node

        if result.get("reason") == "gh_auth_required":
            auth = run_github_auth_login(project, browser_mode="native")
            auth_refs = [str(auth.get("request_path") or ""), str(auth.get("status_path") or ""), str(auth.get("log_path") or ""), str(auth.get("host_capability_request_path") or "")]
            if auth.get("state") == "AUTH_VERIFIED":
                config = load_github_sync_config(project) or config
                if config is None or force_bootstrap or default_current_node == "github_sync_auto_private_bootstrap":
                    result = bootstrap_project(project, config, create_remote_repos=True, commit_message=f"bootstrap {project.name}")
                else:
                    result = auto_private_sync(project, config, commit_message=commit_message)
            else:
                auth_path = store.write_json("tool_results/github_auth_login.json", auth)
                return self._block_for_github_external_login(
                    store,
                    project,
                    auth,
                    continuation_operation=continuation_operation,
                    commit_message=commit_message,
                    target=target,
                    feishu_sync_enabled=feishu_sync_enabled,
                    artifact_refs=[str(auth_path), *[ref for ref in auth_refs if ref]],
                    continuation_extra={**(continuation_extra or {}), "force_bootstrap": default_current_node == "github_sync_auto_private_bootstrap"},
                )

        result_path = store.write_json(f"tool_results/{artifact_name}", result)
        if _github_result_contains_api_eof(result):
            return self._block_for_github_api_retry(
                store,
                project,
                result,
                continuation_operation=continuation_operation,
                commit_message=commit_message,
                target=target,
                feishu_sync_enabled=feishu_sync_enabled,
                artifact_refs=[str(result_path)],
                continuation_extra={**(continuation_extra or {}), "force_bootstrap": default_current_node == "github_sync_auto_private_bootstrap"},
            )

        status = "completed" if result.get("status") in {"completed", "skipped"} else "blocked"
        reason = "private_synced" if result.get("status") == "completed" else str(result.get("reason") or "github_auto_private_sync_blocked")
        self._write_state(store, status, current_node=current_node, project_path=str(project), blocked_reason="" if status == "completed" else reason, continuation_note=continuation_note)
        return write_interaction(
            store,
            status=status,
            output=f"GitHub auto-private 同步结果：{reason}",
            next_prompt="public 发布必须显式运行 github-sync public --confirm。",
            blocked_reason="" if status == "completed" else reason,
            artifact_refs=[str(result_path)],
            terminal=status == "completed",
        )

    def github_sync_auto_private(self, project_path: Path, *, commit_message: str = "nexus auto private sync") -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        return self._github_auto_private_flow(store, project, commit_message=commit_message, continuation_operation="github_auto_private")

    def _block_for_github_external_login(
        self,
        store: RunStore,
        project: Path,
        auth: dict[str, object],
        *,
        continuation_operation: str,
        commit_message: str,
        target: str,
        feishu_sync_enabled: bool,
        artifact_refs: list[str],
        public_confirm: bool | None = None,
        continuation_extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        continuation = {
            "schema": "nexus.external_input_continuation.v1",
            "operation": continuation_operation,
            "project_path": str(project),
            "commit_message": commit_message,
            "target": target,
            "feishu_sync_enabled": feishu_sync_enabled,
            "resume_after": "github_auth",
        }
        if public_confirm is not None:
            continuation["confirm"] = public_confirm
        if continuation_extra:
            continuation.update(continuation_extra)
        store.write_json("recovery/continuation.json", continuation)
        device_url = str(auth.get("device_url") or "https://github.com/login/device")
        device_code = str(auth.get("device_code") or "")
        pending_actions = [
            {
                "action_id": "github_cli_device_login",
                "kind": "host_command",
                "command": "gh auth login --web --clipboard --skip-ssh-key --git-protocol https --hostname github.com",
                "requires_host_permission": True,
                "verification_url": device_url,
                "device_code": device_code,
                "observable_status_command": "gh auth status --hostname github.com",
                "continue_command": f"python -m nexus.cli continue-after-input {store.run_id} --note \"用户已完成 GitHub 登录授权\"",
                "manual_steps": [
                    f"打开 {device_url}",
                    f"输入设备码 {device_code}" if device_code else "按 GitHub CLI 提示输入设备码",
                    "完成 GitHub 密码、2FA、CAPTCHA、账号确认和授权。",
                ],
            }
        ]
        self._write_state(store, "blocked", current_node="github_auth_login", project_path=str(project), blocked_reason=str(auth.get("state") or "github_auth_incomplete"), continuation=continuation)
        return write_interaction(
            store,
            status="blocked",
            output=_github_auth_output(auth),
            next_prompt=f"完成 GitHub 登录后运行 python -m nexus.cli continue-after-input {store.run_id} --note \"用户已完成 GitHub 登录授权\"。",
            blocked_reason=str(auth.get("state") or "github_auth_incomplete"),
            artifact_refs=artifact_refs,
            lifecycle_status="awaiting_external_user",
            pending_actions=pending_actions,
            continuation=continuation,
            auto_resume_supported=True,
            recovery_mode=True,
            recovery_state="recoverable_via_continue_after_input",
            recovery_kind="external_user_action",
            recommended_executor="outer_codex",
        )

    def _block_for_github_api_retry(
        self,
        store: RunStore,
        project: Path,
        result: dict[str, object],
        *,
        continuation_operation: str,
        commit_message: str,
        target: str,
        feishu_sync_enabled: bool,
        artifact_refs: list[str],
        public_confirm: bool | None = None,
        continuation_extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        continuation = {
            "schema": "nexus.external_input_continuation.v1",
            "operation": continuation_operation,
            "project_path": str(project),
            "commit_message": commit_message,
            "target": target,
            "feishu_sync_enabled": feishu_sync_enabled,
            "resume_after": "github_api_eof",
            "retry_env": {
                "GODEBUG": "http2client=0",
                "HTTPS_PROXY": "",
                "HTTP_PROXY": "",
                "ALL_PROXY": "",
                "https_proxy": "",
                "http_proxy": "",
                "all_proxy": "",
            },
        }
        if public_confirm is not None:
            continuation["confirm"] = public_confirm
        if continuation_extra:
            continuation.update(continuation_extra)
        store.write_json("recovery/continuation.json", continuation)
        pending_actions = [
            {
                "action_id": "retry_github_api_without_proxy_http2",
                "kind": "runner_command",
                "command": f"python -m nexus.cli continue-after-input {store.run_id} --note \"GitHub API EOF，使用去代理和禁用 HTTP2 重试\"",
                "requires_host_permission": True,
                "retry_env": continuation["retry_env"],
                "rationale": "GitHub CLI 已进入 repo view/create/push 阶段，但 GitHub API 返回 EOF；按恢复经验清理代理变量并禁用 Go HTTP/2 后重试原流程。",
            }
        ]
        self._write_state(store, "blocked", current_node="github_api_retry", project_path=str(project), blocked_reason="github_api_eof", continuation=continuation)
        return write_interaction(
            store,
            status="blocked",
            output=f"GitHub API 调用遇到 EOF，已准备通过 continue-after-input 重试原流程：{result.get('reason')}",
            next_prompt=f"运行 python -m nexus.cli continue-after-input {store.run_id} --note \"GitHub API EOF，使用去代理和禁用 HTTP2 重试\"。",
            blocked_reason="github_api_eof",
            artifact_refs=artifact_refs,
            lifecycle_status="awaiting_approval",
            pending_actions=pending_actions,
            continuation=continuation,
            auto_resume_supported=True,
            recovery_mode=True,
            recovery_state="recoverable_via_continue_after_input",
            recovery_kind="host_retry",
            recommended_executor="outer_codex",
        )

    def github_sync_public(self, project_path: Path, *, confirm: bool = False) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        return self._github_public_flow(store, project, confirm=confirm)

    def _github_public_flow(self, store: RunStore, project: Path, *, confirm: bool = False, continuation_note: str = "") -> dict[str, object]:
        project = project.expanduser().resolve()
        continuation = _read_json_if_exists(store.path("recovery", "continuation.json"))
        force_bootstrap = str(continuation.get("operation") or "") == "github_public" and bool(continuation.get("force_bootstrap"))
        config = load_github_sync_config(project)
        if config is None or force_bootstrap:
            private_repo, public_repo = _default_github_repos(project)
            if not private_repo and config is not None:
                private_repo = str(config.get("private_repo") or "")
                public_repo = str(config.get("public_repo") or "")
            if private_repo:
                bootstrap_interaction = self._github_bootstrap_flow(
                    store,
                    project,
                    private_repo=private_repo,
                    public_repo=public_repo,
                    create_remote_repos=True,
                    commit_message=f"bootstrap {project.name}",
                    continuation_operation="github_public",
                    continuation_note=continuation_note,
                    artifact_name="github_public_bootstrap.json",
                    current_node="github_public_bootstrap",
                )
                bootstrap_path = store.path("tool_results", "github_public_bootstrap.json") if store.path("tool_results", "github_public_bootstrap.json").exists() else None
                bootstrap = _github_result_from_interaction(bootstrap_interaction, bootstrap_path)
                if bootstrap.get("status") != "completed":
                    if bootstrap_interaction.get("pending_actions"):
                        return bootstrap_interaction
                    self._write_state(store, "blocked", current_node="github_public_bootstrap", blocked_reason=str(bootstrap.get("reason") or "github_bootstrap_blocked"))
                    return write_interaction(store, status="blocked", output=f"public 同步前缺少配置，已自动进入 bootstrap 但被阻断：{bootstrap.get('reason')}", next_prompt="修复 gh/repo/secret scan 后重试 public 同步；public push 仍需要确认。", blocked_reason=str(bootstrap.get("reason") or ""), artifact_refs=[str(bootstrap_path)] if bootstrap_path else [])
                config = load_github_sync_config(project)
            else:
                self._write_state(store, "blocked", current_node="github_sync_public", blocked_reason="github_sync_config_missing_and_repo_target_unknown")
                return write_interaction(store, status="blocked", output="缺少 .github/nexus-sync.json，且该项目不是可自动推断仓库的 nexus/verix 自身项目。", next_prompt="先配置 GitHub sync，并确认 public_repo。", blocked_reason="github_sync_config_missing_and_repo_target_unknown")
        if config is None:
            self._write_state(store, "blocked", current_node="github_sync_public", blocked_reason="github_sync_config_missing")
            return write_interaction(store, status="blocked", output="缺少 .github/nexus-sync.json，不能同步 GitHub public。", next_prompt="先配置 GitHub sync，并确认 public_repo。", blocked_reason="github_sync_config_missing")
        staging = store.path("github_public", "staging")
        staged = prepare_public_staging(project, config, staging)
        staged_path = store.write_json("tool_results/github_public_staging.json", staged)
        if staged.get("status") != "completed":
            self._write_state(store, "blocked", current_node="github_public_staging", blocked_reason="public_secret_scan_failed")
            return write_interaction(store, status="blocked", output="public staging 发现敏感风险，拒绝 push。", next_prompt=f"审阅 {staged_path} 后调整 allowlist/denylist。", blocked_reason="public_secret_scan_failed", artifact_refs=[str(staged_path)])
        validation_config = {**config, "_public_discovery": staged.get("discovery") if isinstance(staged.get("discovery"), dict) else {}}
        validation = validate_public_staging(staging, validation_config)
        validation_path = store.write_json("tool_results/github_public_validation.json", validation)
        if validation.get("status") == "blocked":
            blocked_reason = str(validation.get("blocked_reason") or "public_validation_failed")
            self._write_state(store, "blocked", current_node="github_public_validation", project_path=str(project), blocked_reason=blocked_reason, continuation_note=continuation_note)
            return write_interaction(
                store,
                status="blocked",
                output=f"public staging validation 失败：{blocked_reason}。",
                next_prompt=f"审阅 {validation_path}，修复 public allowlist/required paths/源码或验证命令后重试 public sync；不能确认 push。",
                blocked_reason=blocked_reason,
                artifact_refs=[str(staged_path), str(validation_path)],
            )
        fresh_clone = validate_public_fresh_clone(staging, validation_config, store.path("github_public", "fresh_clone"))
        fresh_clone_path = store.write_json("tool_results/github_public_fresh_clone_validation.json", fresh_clone)
        if fresh_clone.get("status") == "blocked":
            blocked_reason = str(fresh_clone.get("blocked_reason") or "public_fresh_clone_validation_failed")
            self._write_state(store, "blocked", current_node="github_public_fresh_clone_validation", project_path=str(project), blocked_reason=blocked_reason, continuation_note=continuation_note)
            return write_interaction(
                store,
                status="blocked",
                output=f"public fresh-clone validation 失败：{blocked_reason}。",
                next_prompt=f"审阅 {fresh_clone_path}，修复 public staging 产物的可下载可用性后重试 public sync；不能确认 push。",
                blocked_reason=blocked_reason,
                artifact_refs=[str(staged_path), str(validation_path), str(fresh_clone_path)],
            )
        if not confirm:
            self._write_state(store, "blocked", current_node="github_public_approval", blocked_reason="github_public_sync_confirmation_required")
            return write_interaction(store, status="blocked", output="public staging 已生成，secret scan、可复用 validation 和 fresh-clone validation 均通过；push public 前需要显式确认。", next_prompt=f"确认后运行 python -m nexus.cli github-sync public --project-path {project} --confirm。", blocked_reason="github_public_sync_confirmation_required", artifact_refs=[str(staged_path), str(validation_path), str(fresh_clone_path)])
        result = sync_public(project, config, staging)
        auth_refs: list[str] = []
        if result.get("reason") == "gh_auth_required":
            auth = run_github_auth_login(project, browser_mode="native")
            auth_refs = [str(auth.get("request_path") or ""), str(auth.get("status_path") or ""), str(auth.get("log_path") or ""), str(auth.get("host_capability_request_path") or "")]
            if auth.get("state") == "AUTH_VERIFIED":
                result = sync_public(project, config, staging)
            else:
                auth_path = store.write_json("tool_results/github_auth_login.json", auth)
                return self._block_for_github_external_login(
                    store,
                    project,
                    auth,
                    continuation_operation="github_public",
                    commit_message="",
                    target="public",
                    feishu_sync_enabled=False,
                    artifact_refs=[str(auth_path), *[ref for ref in auth_refs if ref]],
                    public_confirm=True,
                )
        result_path = store.write_json("tool_results/github_public_sync.json", result)
        if _github_result_contains_api_eof(result):
            return self._block_for_github_api_retry(
                store,
                project,
                result,
                continuation_operation="github_public",
                commit_message="",
                target="public",
                feishu_sync_enabled=False,
                artifact_refs=[str(staged_path), str(validation_path), str(fresh_clone_path), str(result_path), *[ref for ref in auth_refs if ref]],
                public_confirm=True,
            )
        status = "completed" if result.get("status") == "completed" else "blocked"
        self._write_state(store, status, current_node="github_sync_public", blocked_reason="" if status == "completed" else str(result.get("reason") or "github_public_sync_blocked"))
        return write_interaction(store, status=status, output=f"GitHub public 同步结果：{result.get('reason')}", next_prompt="查看 tool_results/github_public_sync.json。", blocked_reason="" if status == "completed" else str(result.get("reason") or ""), artifact_refs=[str(staged_path), str(validation_path), str(fresh_clone_path), str(result_path), *[ref for ref in auth_refs if ref]])

    def github_sync_guide(self, project_path: Path, *, publish_feishu: bool = False) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        guide = write_github_sync_guide(project)
        guide_path = store.write_json("tool_results/github_sync_guide.json", guide)
        refs = [str(guide_path), str(guide.get("path") or "")]
        if not publish_feishu:
            self._write_state(store, "completed", current_node="github_sync_guide", project_path=str(project))
            return write_interaction(
                store,
                status="completed",
                output=f"已生成 GitHub 同步操作指南：{guide.get('path')}",
                next_prompt=f"如需同步到飞书，运行 python -m nexus.cli github-sync publish-guide-feishu --project-path {project}。",
                artifact_refs=[ref for ref in refs if ref],
            )
        guide_path = str(guide.get("path") or "")
        feishu, feishu_path, status, blocked_reason = self._sync_project_docs_to_feishu(
            store,
            project,
            event_type="github_sync_guide_publish",
            title="整体操作指南同步",
            summary="兼容 GitHub 同步指南发布入口，刷新整体操作指南并同步到飞书。",
            changed_paths=[guide_path] if guide_path else [],
            guide_paths=[guide_path] if guide_path else [],
            enabled=True,
            artifact_name="github_sync_guide_feishu_sync.json",
        )
        next_prompt = "整体操作指南已同步到飞书；后续 private 修改仍会默认 auto-private。"
        if status != "completed":
            next_prompt = f"飞书未就绪或写入失败；先运行 python -m nexus.cli feishu setup --project-path {project}，配置完成后重试 python -m nexus.cli guide sync --project-path {project}。"
        self._write_state(store, status, current_node="github_sync_guide_publish_feishu", project_path=str(project), blocked_reason=blocked_reason)
        return write_interaction(
            store,
            status=status,
            output=f"整体操作指南：{guide.get('status')}；飞书文档同步：{feishu.get('status')} ({feishu.get('reason')})",
            next_prompt=next_prompt,
            blocked_reason=blocked_reason,
            artifact_refs=[ref for ref in [*refs, str(feishu_path)] if ref],
        )

    def _sync_project_docs_to_feishu(
        self,
        store: RunStore,
        project: Path,
        *,
        event_type: str,
        title: str,
        summary: str,
        changed_paths: list[Path | str] | None = None,
        guide_paths: list[Path | str] | None = None,
        enabled: bool = True,
        artifact_name: str,
    ) -> tuple[dict[str, object], Path, str, str]:
        result = run_feishu_autosync(
            project,
            event_type=event_type,
            title=title,
            summary=summary,
            changed_paths=changed_paths or [],
            source_run_id=store.run_id,
            guide_paths=guide_paths or [],
            enabled=enabled,
        )
        artifact_path = store.write_json(f"tool_results/{artifact_name}", result)
        status = "completed" if result.get("status") == "completed" else "blocked"
        if result.get("status") == "skipped":
            status = "completed"
        blocked_reason = "" if status == "completed" else str(result.get("reason") or "feishu_docs_sync_blocked")
        return result, artifact_path, status, blocked_reason

    def _post_change_autosync(
        self,
        store: RunStore,
        project: Path,
        *,
        event_type: str,
        title: str,
        summary: str,
        target: str = "auto",
        changed_paths: list[Path | str] | None = None,
        feishu_sync_enabled: bool = True,
        github_private_enabled: bool = True,
        continuation_note: str = "",
    ) -> dict[str, object]:
        project = project.expanduser().resolve()
        blocked = self._block_if_project_missing(store, project, current_node="post_change_autosync")
        if blocked:
            return blocked
        config = load_github_sync_config(project)
        private_repo = str((config or {}).get("private_repo") or "")
        public_repo = str((config or {}).get("public_repo") or "")
        if not private_repo:
            private_repo, public_repo = _default_github_repos(project)
        docs_bundle = write_project_docs(
            project,
            summary,
            private_repo=private_repo,
            public_repo=public_repo,
            github_sync_enabled=github_private_enabled,
            feishu_sync_enabled=feishu_sync_enabled,
            run_id=store.run_id,
        )
        docs_bundle_path = store.write_json("tool_results/post_change_project_docs.json", docs_bundle)
        guide = write_operation_guide(project, target=target)
        guide_artifact = store.write_json("tool_results/post_change_operation_guide.json", guide)
        guide_path = str(guide.get("path") or "")
        refs = [str(docs_bundle_path), *[path for path in docs_bundle.get("paths", []) if isinstance(path, str)], str(guide_artifact), guide_path, *[str(path) for path in (changed_paths or [])]]
        self._write_active_project(project, source="post-change-autosync", run_id=store.run_id)

        github: dict[str, object] | None = None
        if github_private_enabled:
            github = self._github_auto_private_flow(
                store,
                project,
                commit_message=f"{project.name} {event_type} {store.run_id}",
                continuation_operation="post_change_autosync",
                target=target,
                feishu_sync_enabled=feishu_sync_enabled,
                continuation_note=continuation_note,
                artifact_name="post_change_github_auto_private.json",
                current_node="post_change_github_auto_private",
                continuation_extra={
                    "event_type": event_type,
                    "title": title,
                    "summary": summary,
                    "changed_paths": [str(path) for path in (changed_paths or [])],
                },
            )
            refs.extend(str(item) for item in github.get("artifact_refs", []) if str(item))
            if github.get("previous_task_status") != "completed":
                blocked_reason = str(github.get("blocked_reason") or "post_change_github_auto_private_blocked")
                self._write_state(store, "blocked", current_node="post_change_github_auto_private", project_path=str(project), blocked_reason=blocked_reason, continuation=github.get("continuation") or {})
                github["previous_task_output"] = f"项目文档/整体操作指南已更新；GitHub private 后置同步未完成：{github.get('previous_task_output')}"
                github["artifact_refs"] = refs
                store.write_json("interaction.json", github)
                store.write_text("interaction.md", "\n".join([f"上一任务状态：{github.get('previous_task_status', '')}", f"上一任务输出：{github.get('previous_task_output', '')}", f"下一任务提示：{github.get('next_task_prompt', '')}"]) + "\n")
                return github

        if not feishu_sync_enabled:
            self._write_state(store, "completed", current_node="post_change_completed", project_path=str(project), guide_target=str(guide.get("target") or target))
            return write_interaction(
                store,
                status="completed",
                output=f"项目文档/整体操作指南已更新：{guide_path}；GitHub private：{(github or {}).get('previous_task_status') or 'skipped'}；按要求跳过飞书同步。",
                next_prompt="public 发布必须显式运行 github-sync public --confirm。",
                artifact_refs=[ref for ref in refs if ref],
            )

        autosync, autosync_artifact, status, blocked_reason = self._sync_project_docs_to_feishu(
            store,
            project,
            event_type=event_type,
            title=title,
            summary=summary,
            changed_paths=[*docs_bundle.get("paths", []), guide_path, *[str(path) for path in (changed_paths or [])]],
            guide_paths=[guide_path] if guide_path else [],
            enabled=True,
            artifact_name="post_change_feishu_autosync.json",
        )
        refs.append(str(autosync_artifact))
        if status != "completed":
            self._write_state(store, "blocked", current_node="post_change_feishu_autosync", project_path=str(project), blocked_reason=blocked_reason)
            return write_interaction(
                store,
                status="blocked",
                output=f"项目文档/整体操作指南已更新；GitHub private：{(github or {}).get('previous_task_status') or 'skipped'}；飞书同步：{autosync.get('status')} ({autosync.get('reason')})。",
                next_prompt=f"先运行 python -m nexus.cli feishu setup --project-path {project}，配置完成后重试当前同步或 self-sync。",
                blocked_reason=blocked_reason,
                artifact_refs=[ref for ref in refs if ref],
            )

        post_feishu_github: dict[str, object] | None = None
        if github_private_enabled:
            post_feishu_github = self._github_auto_private_flow(
                store,
                project,
                commit_message=f"{project.name} {event_type} feishu records",
                continuation_operation="github_auto_private",
                target=target,
                feishu_sync_enabled=False,
                continuation_note=continuation_note,
                artifact_name="post_change_post_feishu_github_private.json",
                current_node="post_change_post_feishu_github",
            )
            refs.extend(str(item) for item in post_feishu_github.get("artifact_refs", []) if str(item))
            if post_feishu_github.get("previous_task_status") != "completed":
                blocked_reason = str(post_feishu_github.get("blocked_reason") or "post_change_post_feishu_github_blocked")
                self._write_state(store, "blocked", current_node="post_change_post_feishu_github", project_path=str(project), blocked_reason=blocked_reason, continuation=post_feishu_github.get("continuation") or {})
                post_feishu_github["previous_task_output"] = f"飞书同步已完成，但飞书写回后的 private 再同步未完成：{post_feishu_github.get('previous_task_output')}"
                post_feishu_github["artifact_refs"] = refs
                store.write_json("interaction.json", post_feishu_github)
                store.write_text("interaction.md", "\n".join([f"上一任务状态：{post_feishu_github.get('previous_task_status', '')}", f"上一任务输出：{post_feishu_github.get('previous_task_output', '')}", f"下一任务提示：{post_feishu_github.get('next_task_prompt', '')}"]) + "\n")
                return post_feishu_github

        self._write_state(store, "completed", current_node="post_change_completed", project_path=str(project), guide_target=str(guide.get("target") or target), blocked_reason="")
        return write_interaction(
            store,
            status="completed",
            output=f"项目文档/整体操作指南已更新：{guide_path}；GitHub private：{(github or {}).get('previous_task_status') or 'skipped'}；飞书同步：{autosync.get('status')} ({autosync.get('reason')})；飞书写回 private 再同步：{(post_feishu_github or {}).get('previous_task_status') or 'skipped'}",
            next_prompt="post-change autosync 已完成；public 发布仍需显式 github-sync public --confirm。",
            artifact_refs=[ref for ref in refs if ref],
        )

    def operation_guide(
        self,
        project_path: Path,
        *,
        target: str = "auto",
        publish_feishu: bool = False,
        feishu_sync_enabled: bool = True,
    ) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        blocked = self._block_if_project_missing(store, project, current_node="operation_guide")
        if blocked:
            return blocked
        guide = write_operation_guide(project, target=target)
        guide_artifact = store.write_json("tool_results/operation_guide.json", guide)
        guide_path = str(guide.get("path") or "")
        refs = [ref for ref in [str(guide_artifact), guide_path] if ref]
        self._write_active_project(project, source="operation-guide", run_id=store.run_id)
        if not publish_feishu or not feishu_sync_enabled:
            self._write_state(store, "completed", current_node="operation_guide_generate", project_path=str(project), guide_target=str(guide.get("target") or target))
            return write_interaction(
                store,
                status="completed",
                output=f"已生成整体操作指南：{guide_path}",
                next_prompt=f"如需同步到飞书，运行 python -m nexus.cli guide sync --project-path {project} --target {guide.get('target') or target}。",
                artifact_refs=refs,
            )
        autosync = run_feishu_autosync(
            project,
            event_type="operation_guide_sync",
            title="整体操作指南同步",
            summary="刷新整体操作指南并同步到飞书。",
            changed_paths=[guide_path] if guide_path else [],
            source_run_id=store.run_id,
            guide_paths=[guide_path] if guide_path else [],
            enabled=True,
        )
        autosync_artifact = store.write_json("tool_results/operation_guide_feishu_sync.json", autosync)
        status = "completed" if autosync.get("status") == "completed" else "blocked"
        blocked_reason = "" if status == "completed" else str(autosync.get("reason") or "feishu_operation_guide_sync_blocked")
        next_prompt = "整体操作指南已同步到飞书；后续可继续 github-sync auto-private 或 self-sync。"
        if status != "completed":
            next_prompt = f"先运行 python -m nexus.cli feishu setup --project-path {project}，配置完成后再重试 python -m nexus.cli guide sync --project-path {project} --target {guide.get('target') or target}。"
        self._write_state(store, status, current_node="operation_guide_publish_feishu", project_path=str(project), guide_target=str(guide.get("target") or target), blocked_reason=blocked_reason)
        return write_interaction(
            store,
            status=status,
            output=f"整体操作指南：{guide.get('status')}；飞书同步：{autosync.get('status')} ({autosync.get('reason')})",
            next_prompt=next_prompt,
            blocked_reason=blocked_reason,
            artifact_refs=[*refs, str(autosync_artifact)],
        )

    def _self_sync_flow(
        self,
        store: RunStore,
        project: Path,
        *,
        target: str = "auto",
        feishu_sync_enabled: bool = True,
        continuation_note: str = "",
        force_bootstrap_private: bool = False,
    ) -> dict[str, object]:
        project = project.expanduser().resolve()
        blocked = self._block_if_project_missing(store, project, current_node="self_sync")
        if blocked:
            return blocked
        guide = write_operation_guide(project, target=target)
        guide_artifact = store.write_json("tool_results/self_sync_operation_guide.json", guide)
        guide_path = str(guide.get("path") or "")
        self._write_active_project(project, source="self-sync", run_id=store.run_id)

        github = self._github_auto_private_flow(
            store,
            project,
            commit_message=f"{project.name} self sync",
            continuation_operation="self_sync",
            target=target,
            feishu_sync_enabled=feishu_sync_enabled,
            continuation_note=continuation_note,
            force_bootstrap=force_bootstrap_private,
        )
        refs = [ref for ref in [str(guide_artifact), guide_path, *[str(item) for item in github.get("artifact_refs", []) if str(item)]] if ref]
        if github.get("previous_task_status") != "completed":
            blocked_reason = str(github.get("blocked_reason") or "github_auto_private_sync_blocked")
            self._write_state(store, "blocked", current_node="self_sync_github", project_path=str(project), guide_target=str(guide.get("target") or target), blocked_reason=blocked_reason, continuation=github.get("continuation") or {})
            github["previous_task_output"] = f"整体操作指南已更新：{guide_path}；GitHub private 自同步未完成：{github.get('previous_task_output')}"
            github["artifact_refs"] = refs
            store.write_json("interaction.json", github)
            store.write_text(
                "interaction.md",
                "\n".join(
                    [
                        f"上一任务状态：{github.get('previous_task_status', '')}",
                        f"上一任务输出：{github.get('previous_task_output', '')}",
                        f"下一任务提示：{github.get('next_task_prompt', '')}",
                    ]
                )
                + "\n",
            )
            return github

        if not feishu_sync_enabled:
            self._write_state(store, "completed", current_node="self_sync_completed", project_path=str(project), guide_target=str(guide.get("target") or target))
            return write_interaction(
                store,
                status="completed",
                output=f"整体操作指南已更新：{guide_path}；{github.get('previous_task_output')}",
                next_prompt=f"如需同步整体操作指南到飞书，运行 python -m nexus.cli guide sync --project-path {project} --target {guide.get('target') or target}。",
                artifact_refs=refs,
            )

        autosync = run_feishu_autosync(
            project,
            event_type="self_sync",
            title="自同步",
            summary="刷新整体操作指南，完成 GitHub private 同步，并尝试同步飞书文档。",
            changed_paths=[guide_path] if guide_path else [],
            source_run_id=store.run_id,
            guide_paths=[guide_path] if guide_path else [],
            enabled=True,
        )
        autosync_artifact = store.write_json("tool_results/self_sync_feishu_sync.json", autosync)
        refs.append(str(autosync_artifact))
        status = "completed" if autosync.get("status") == "completed" else "blocked"
        blocked_reason = "" if status == "completed" else str(autosync.get("reason") or "self_sync_feishu_blocked")
        post_feishu_github: dict[str, object] | None = None
        if status == "completed":
            post_feishu_github = self._github_auto_private_flow(
                store,
                project,
                commit_message=f"{project.name} self sync feishu records",
                continuation_operation="self_sync",
                target=target,
                feishu_sync_enabled=False,
                continuation_note=continuation_note,
                artifact_name="self_sync_post_feishu_github_private.json",
                current_node="self_sync_post_feishu_github",
            )
            refs.extend(str(item) for item in post_feishu_github.get("artifact_refs", []) if str(item))
            if post_feishu_github.get("previous_task_status") != "completed":
                blocked_reason = str(post_feishu_github.get("blocked_reason") or "self_sync_post_feishu_github_blocked")
                self._write_state(store, "blocked", current_node="self_sync_post_feishu_github", project_path=str(project), guide_target=str(guide.get("target") or target), blocked_reason=blocked_reason, continuation=post_feishu_github.get("continuation") or {})
                post_feishu_github["previous_task_output"] = f"整体操作指南已更新：{guide_path}；{github.get('previous_task_output')}；飞书同步已完成，但飞书写回后的 private 再同步未完成：{post_feishu_github.get('previous_task_output')}"
                post_feishu_github["artifact_refs"] = refs
                store.write_json("interaction.json", post_feishu_github)
                store.write_text(
                    "interaction.md",
                    "\n".join(
                        [
                            f"上一任务状态：{post_feishu_github.get('previous_task_status', '')}",
                            f"上一任务输出：{post_feishu_github.get('previous_task_output', '')}",
                            f"下一任务提示：{post_feishu_github.get('next_task_prompt', '')}",
                        ]
                    )
                    + "\n",
                )
                return post_feishu_github
        next_prompt = "Nexus 自同步已完成；后续 private 更新可继续执行 self-sync，public 发布仍需显式确认。"
        if status != "completed":
            next_prompt = f"先运行 python -m nexus.cli feishu setup --project-path {project}，完成后再重试 python -m nexus.cli self-sync --project-path {project} --target {guide.get('target') or target}。"
        self._write_state(store, status, current_node="self_sync_feishu", project_path=str(project), guide_target=str(guide.get("target") or target), blocked_reason=blocked_reason)
        return write_interaction(
            store,
            status=status,
            output=f"整体操作指南已更新：{guide_path}；{github.get('previous_task_output')}；飞书同步：{autosync.get('status')} ({autosync.get('reason')})；飞书写回 private 再同步：{(post_feishu_github or {}).get('previous_task_status') or 'skipped'}",
            next_prompt=next_prompt,
            blocked_reason=blocked_reason,
            artifact_refs=refs,
        )

    def self_sync(
        self,
        project_path: Path,
        *,
        target: str = "auto",
        feishu_sync_enabled: bool = True,
    ) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        return self._self_sync_flow(store, project_path, target=target, feishu_sync_enabled=feishu_sync_enabled)

    def supplemental_init(
        self,
        project_path: Path,
        *,
        idea: str = "补充初始化",
        target: str = "auto",
        github_private_enabled: bool = True,
        feishu_sync_enabled: bool = True,
    ) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        blocked = self._block_if_project_missing(store, project, current_node="supplemental_init")
        if blocked:
            return blocked
        return self._post_change_autosync(
            store,
            project,
            event_type="supplemental_init",
            title="补充初始化",
            summary=idea or "补充初始化：只补齐 Nexus 管理所需的意图文档、项目说明、操作指南、同步配置与记录，不修改项目业务逻辑。",
            target=target,
            changed_paths=[],
            feishu_sync_enabled=feishu_sync_enabled,
            github_private_enabled=github_private_enabled,
        )

    def system_showcase_generate(self, project_path: Path, *, provider_name: str = "auto", feishu_sync_enabled: bool = True) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        repo_scan = scan_repo(project)
        graph = generate_showcase(project, repo_scan)
        path = store.write_json("tool_results/system_showcase.json", graph)
        self._write_state(store, "completed", current_node="system_showcase_generate", project_path=str(project), feishu_sync=feishu_sync_enabled)
        return write_interaction(store, status="completed", output=f"已生成系统架构展示：{graph.get('architecture_md')}", next_prompt="下一步可运行 system-showcase publish-feishu。", artifact_refs=[str(path), str(graph.get("architecture_md"))])

    def system_showcase_explain(self, project_path: Path, node_id: str) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        result = explain_node(project, node_id)
        path = store.write_json("tool_results/system_node_explain.json", result)
        status = "completed" if result.get("status") == "completed" else "blocked"
        self._write_state(store, status, current_node="system_showcase_explain", blocked_reason="" if status == "completed" else str(result.get("reason") or "node_explain_blocked"))
        return write_interaction(store, status=status, output=json.dumps(result.get("node", result), ensure_ascii=False), next_prompt="如果需要更细解释，可基于该节点继续让 nexus 生成说明。", blocked_reason="" if status == "completed" else str(result.get("reason") or ""), artifact_refs=[str(path)])

    def feishu_setup_flow(
        self,
        project_path: Path,
        *,
        app_id_path: str = "",
        app_secret_path: str = "",
        folder_token: str = "",
        folder_token_path: str = "",
        doc_token: str = "",
        doc_token_path: str = "",
        doc_base_url: str = "",
        guide_only: bool = False,
        no_network: bool = False,
        research_docs: bool = False,
        approve_online_search: bool = False,
        provider_name: str = "auto",
    ) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        store.write_json(
            "input.json",
            {
                "schema": "nexus.feishu_setup_input.v1",
                "project_path": str(project),
                "app_id_path": app_id_path,
                "app_secret_path": app_secret_path,
                "folder_token_configured": bool(folder_token),
                "folder_token_path": folder_token_path,
                "doc_token_configured": bool(doc_token),
                "doc_token_path": doc_token_path,
                "guide_only": guide_only,
                "no_network": no_network,
                "research_docs": research_docs,
                "approve_online_search": approve_online_search,
                "provider": provider_name,
            },
        )
        guide_path = store.write_json("tool_results/feishu_setup_guide.json", build_setup_guide())
        approval_path = store.write_json("approvals/feishu_publish_required.json", build_publish_required_approval())
        research_path: Path | None = None
        if research_docs:
            research_path = self._feishu_docs_research(store, project, provider_name=provider_name, approve_online_search=approve_online_search)
        result = run_feishu_setup(
            project,
            app_id_path=app_id_path,
            app_secret_path=app_secret_path,
            folder_token=folder_token,
            folder_token_path=folder_token_path,
            doc_token=doc_token,
            doc_token_path=doc_token_path,
            doc_base_url=doc_base_url,
            guide_only=guide_only,
            no_network=no_network,
        )
        setup_path = store.write_json("tool_results/feishu_setup.json", result)
        doctor = result.get("doctor") if isinstance(result.get("doctor"), dict) else result
        doctor_path = store.write_json("tool_results/feishu_doctor.json", doctor)
        status = "completed" if result.get("status") == "completed" else "blocked"
        blocked_reason = "" if status == "completed" else str(result.get("reason") or "feishu_setup_required")
        self._write_state(store, status, current_node="feishu_setup", project_path=str(project), blocked_reason=blocked_reason)
        refs = [str(setup_path), str(doctor_path), str(guide_path), str(approval_path)]
        if research_path is not None:
            refs.append(str(research_path))
        return write_interaction(
            store,
            status=status,
            output=_feishu_setup_output(result),
            next_prompt=_feishu_setup_next_prompt(project, result),
            blocked_reason=blocked_reason,
            artifact_refs=refs,
        )

    def feishu_doctor_flow(
        self,
        project_path: Path,
        *,
        no_network: bool = False,
        create_doc: bool = False,
        title: str = "Nexus Feishu smoke test",
        folder_token: str = "",
        folder_token_path: str = "",
    ) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        if create_doc:
            del folder_token, folder_token_path, no_network
            record_path = append_feishu_record_markdown(
                project,
                title=title,
                content="Nexus Feishu smoke test",
                event_type="feishu_doctor_smoke",
                source_run_id=store.run_id,
            )
            result, sync_path, _, _ = self._sync_project_docs_to_feishu(
                store,
                project,
                event_type="feishu_doctor_smoke",
                title=title,
                summary="生成本地 Markdown smoke 记录并通过 autosync 验证飞书 Markdown 同步链路。",
                changed_paths=[record_path],
                guide_paths=[record_path],
                enabled=True,
                artifact_name="feishu_doctor_autosync.json",
            )
            node = "feishu_doctor_create_doc"
            extra_refs = [str(sync_path), str(record_path)]
        else:
            del folder_token, folder_token_path
            result = run_feishu_doctor(project, no_network=no_network)
            node = "feishu_doctor"
            extra_refs = []
        path = store.write_json("tool_results/feishu_doctor.json", result)
        status = "completed" if result.get("status") == "completed" else "blocked"
        blocked_reason = "" if status == "completed" else str(result.get("reason") or "feishu_doctor_blocked")
        refs = [str(path), *extra_refs]
        output = _feishu_doctor_output(result)
        if status == "blocked":
            context = build_recovery_context(store=store, project=project, module="feishu", node=node, status=status, reason=blocked_reason, result=result, artifact_refs=[str(path)])
            playbook_match = match_recovery_playbook(project, context)
            guidance = self._recovery_guidance(store, project, context, playbook_match)
            recovery_result = build_recovery_result(context, guidance, status="blocked", playbook_match=playbook_match)
            recovery_refs = write_recovery_artifacts(store, recovery_result)
            refs.extend(str(item) for item in recovery_refs.values())
            output = f"{output}\nNexus 已进入全局恢复模块：{guidance.get('summary')}"
        self._write_state(store, status, current_node=node, project_path=str(project), blocked_reason=blocked_reason)
        return write_interaction(
            store,
            status=status,
            output=output,
            next_prompt=_feishu_doctor_next_prompt(project, result),
            blocked_reason=blocked_reason,
            artifact_refs=refs,
        )

    def feishu_record_flow(
        self,
        project_path: Path,
        *,
        title: str = "",
        content: str = "",
        folder_token: str = "",
        folder_token_path: str = "",
        doc_token: str = "",
        doc_token_path: str = "",
        doc_base_url: str = "",
        provider_name: str = "auto",
        no_network: bool = False,
    ) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        del folder_token, folder_token_path, doc_token, doc_token_path, doc_base_url, no_network
        raw_content = content.strip() or f"记录项目 {project.name} 的当前状态。"
        feishu_config = load_feishu_config(project)
        if feishu_config is None:
            record_path = append_feishu_record_markdown(
                project,
                title=title or "Nexus 记录",
                content=raw_content,
                event_type="manual_record",
                source_run_id=store.run_id,
            )
            autosync, autosync_path, status, blocked_reason = self._sync_project_docs_to_feishu(
                store,
                project,
                event_type="manual_record",
                title=title or "Nexus 记录",
                summary="已先写入本地 Markdown 记录；飞书同步由 autosync 统一处理。",
                changed_paths=[record_path],
                guide_paths=[record_path],
                enabled=True,
                artifact_name="feishu_record_autosync.json",
            )
            result_path = store.write_json("tool_results/feishu_record.json", {"schema": "nexus.local_feishu_record.v1", "status": status, "reason": autosync.get("reason"), "path": str(record_path), "autosync_artifact": str(autosync_path)})
            self._write_state(store, status, current_node="feishu_record_autosync", project_path=str(project), blocked_reason=blocked_reason)
            return write_interaction(
                store,
                status=status,
                output=f"飞书记录已写入本地 Markdown：{record_path}；autosync：{autosync.get('status')} ({autosync.get('reason')})。",
                next_prompt="记录已通过本地 Markdown -> autosync 链路处理；如 autosync blocked，请先完成 feishu setup 后重试。",
                blocked_reason=blocked_reason,
                artifact_refs=[str(result_path), str(autosync_path), str(record_path)],
            )
        selected_provider = self._resolve_provider_name(provider_name, raw_content)
        provider = self._select_provider(store, selected_provider, project)
        if provider is None:
            return self._block_for_provider_setup(store)
        board = load_board(project)
        repo_scan = scan_repo(project)
        try:
            record_content = self._model_node_with_schema(
                store,
                provider,
                "feishu_record_content",
                "feishu_record_content",
                _feishu_record_prompt(project, title, raw_content, board, repo_scan),
            )
        except (ProviderUnavailable, ProviderExecutionError, ValueError) as exc:
            self._write_state(store, "blocked", current_node="feishu_record_model", provider=provider.name, project_path=str(project), blocked_reason="feishu_record_model_failed")
            error_path = store.write_json(
                "tool_results/feishu_record_model_error.json",
                {
                    "schema": "nexus.feishu_record_model_error.v1",
                    "provider": provider.name,
                    "reason": "feishu_record_model_failed",
                    "message": str(exc),
                    "next_checks": [
                        "检查真实模型 provider 是否可用。",
                        "如果是 API provider，检查 API key、base_url、网络权限和额度。",
                        "配置完成后重试 feishu record；记录会先写入本地 Markdown，再由 autosync 同步。",
                    ],
                },
            )
            return write_interaction(
                store,
                status="blocked",
                output=f"飞书记录的大模型整理节点失败：{provider.name}；未使用 mock。",
                next_prompt="请检查模型 provider/API 网络/额度，或切换可用真实模型后重试 feishu record。",
                blocked_reason="feishu_record_model_failed",
                artifact_refs=[str(error_path)],
            )
        content_path = store.write_json("tool_results/feishu_record_content.json", record_content)
        record_title = str(record_content.get("title") or title or "Nexus 记录")
        record_path = append_feishu_record_markdown(
            project,
            title=record_title,
            content=str(record_content.get("markdown") or raw_content),
            event_type="manual_record",
            source_run_id=store.run_id,
        )
        autosync, autosync_path, status, blocked_reason = self._sync_project_docs_to_feishu(
            store,
            project,
            event_type="manual_record",
            title=record_title,
            summary="已先写入本地 Markdown 记录；飞书同步由 autosync 统一处理。",
            changed_paths=[record_path],
            guide_paths=[record_path],
            enabled=True,
            artifact_name="feishu_record_autosync.json",
        )
        result = {"schema": "nexus.local_feishu_record.v1", "status": status, "reason": autosync.get("reason"), "path": str(record_path), "autosync_artifact": str(autosync_path)}
        result_path = store.write_json("tool_results/feishu_record.json", result)
        self._write_state(store, status, current_node="feishu_record", provider=provider.name, project_path=str(project), blocked_reason=blocked_reason)
        return write_interaction(
            store,
            status=status,
            output=f"飞书记录已写入本地 Markdown：{record_path}；autosync：{autosync.get('status')} ({autosync.get('reason')})",
            next_prompt="记录已通过本地 Markdown -> autosync 链路处理；后续记录继续追加 docs/feishu-records.md。",
            blocked_reason=blocked_reason,
            artifact_refs=[str(content_path), str(result_path), str(autosync_path), str(record_path)],
        )

    def _feishu_docs_research(self, store: RunStore, project: Path, *, provider_name: str, approve_online_search: bool) -> Path:
        if approve_online_search:
            self._write_online_search_approval_marker(store, "feishu setup --approve-online-search")
        provider = self._select_provider(store, self._resolve_provider_name(provider_name, "飞书配置官方文档调研"), project)
        search_plan = {
            "schema": "nexus.search_plan.v1",
            "round_no": 1,
            "requires_online": True,
            "coverage_gates": ["飞书企业自建应用", "tenant_access_token", "docx 创建文档", "docx block 写入", "权限发布审核"],
            "stop_conditions": ["找到官方文档来源并能生成配置指南"],
            "source_plan": [
                {
                    "source": "official_docs",
                    "priority": "high",
                    "queries": [
                        "飞书开放平台 企业自建应用 App ID App Secret tenant_access_token docx 创建文档",
                        "飞书开放平台 docx block 创建文档 权限 发布版本",
                    ],
                    "reason": "优先检索飞书官方文档。",
                },
                {
                    "source": "chinese_web",
                    "priority": "medium",
                    "queries": ["飞书新版云文档 docx API app_secret tenant_access_token 权限"],
                    "reason": "补充中文互联网可操作经验。",
                },
            ],
        }
        service = SearchService()
        result = service.execute_round(
            round_no=1,
            search_plan=search_plan,
            project_path=project,
            repo_scan=scan_repo(project),
            online_allowed=self._online_search_approved(store),
            raw_dir=store.path("feishu_docs_research", "raw"),
        )
        payload: dict[str, object] = {
            "schema": "nexus.feishu_docs_research.v1",
            "search_plan": search_plan,
            "candidates": [candidate.to_dict() for candidate in result.candidates],
            "source_status": [status.to_dict() for status in result.statuses],
            "online_blocked": result.online_blocked,
        }
        if provider is not None and result.candidates:
            summary = self._model_node_with_schema(
                store,
                provider,
                "feishu_docs_summary",
                "feishu_docs_summary",
                _feishu_docs_summary_prompt(result.candidates, result.statuses),
            )
            payload["model_summary"] = summary
        return store.write_json("tool_results/feishu_docs_research.json", payload)

    def feishu_configure(
        self,
        project_path: Path,
        *,
        app_id: str = "",
        app_secret_env: str = "",
        app_id_path: str = "<LOCAL_PATH_REDACTED>",
        app_secret_path: str = "<LOCAL_PATH_REDACTED>",
        folder_token: str = "",
        folder_token_path: str = "",
        doc_token: str = "",
        doc_token_path: str = "",
        doc_base_url: str = "",
    ) -> dict[str, object]:
        del app_id, app_secret_env
        return self.feishu_setup_flow(project_path, app_id_path=app_id_path, app_secret_path=app_secret_path, folder_token=folder_token, folder_token_path=folder_token_path, doc_token=doc_token, doc_token_path=doc_token_path, doc_base_url=doc_base_url)

    def feishu_login_flow(self, project_path: Path | None = None) -> dict[str, object]:
        return self.feishu_setup_flow(project_path or Path("."), guide_only=True)

    def system_showcase_publish_feishu(self, project_path: Path, *, confirm: bool = False) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = project_path.expanduser().resolve()
        markdown = project / "docs" / "system" / "architecture.md"
        generated_path: Path | None = None
        if not markdown.exists():
            generated = generate_showcase(project, scan_repo(project))
            markdown = Path(str(generated["architecture_md"]))
            generated_path = store.write_json("tool_results/system_showcase.json", generated)
        if not confirm:
            self._write_state(store, "blocked", current_node="feishu_publish_approval", blocked_reason="feishu_publish_confirmation_required")
            refs = [str(markdown)]
            if generated_path is not None:
                refs.append(str(generated_path))
            return write_interaction(store, status="blocked", output=f"准备通过 autosync 同步系统架构说明：{markdown}；同步前需要确认。", next_prompt=f"确认后运行 python -m nexus.cli system-showcase publish-feishu --project-path {project} --confirm。", blocked_reason="feishu_publish_confirmation_required", artifact_refs=refs)
        result, result_path, status, blocked_reason = self._sync_project_docs_to_feishu(
            store,
            project,
            event_type="system_showcase_sync",
            title="系统架构说明同步",
            summary="生成或刷新系统架构说明，并通过 autosync 同步到飞书。",
            changed_paths=[markdown],
            guide_paths=[markdown],
            enabled=True,
            artifact_name="system_showcase_feishu_sync.json",
        )
        self._write_state(store, status, current_node="system_showcase_feishu_sync", blocked_reason=blocked_reason)
        refs = [str(result_path), str(markdown)]
        if generated_path is not None:
            refs.append(str(generated_path))
        next_prompt = "系统架构说明已通过本地 Markdown -> autosync 链路同步到飞书。"
        if status != "completed":
            next_prompt = f"先运行 python -m nexus.cli feishu setup --project-path {project}，配置完成后重试 system-showcase publish-feishu --confirm。"
        return write_interaction(store, status=status, output=f"系统架构说明 autosync：{result.get('status')} ({result.get('reason')})", next_prompt=next_prompt, blocked_reason=blocked_reason, artifact_refs=refs)

    def board_show(self, project_path: Path | None = None) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = self._resolve_active_project(project_path)
        if project is None:
            self._write_state(store, "blocked", current_node="board_show", blocked_reason="active_project_not_found")
            return write_interaction(
                store,
                status="blocked",
                output="没有找到当前项目上下文；请先调研/初始化项目，或显式提供项目路径。",
                next_prompt="python -m nexus.cli board show --project-path <project-path>",
                blocked_reason="active_project_not_found",
            )
        blocked = self._block_if_project_missing(store, project, current_node="board_show")
        if blocked:
            return blocked
        board = load_board(project)
        board_path = store.write_json("tool_results/project_board.json", board)
        self._write_active_project(project, source="board-show", run_id=store.run_id)
        self._write_state(store, "completed", current_node="board_show", project_path=str(project))
        return write_interaction(
            store,
            status="completed",
            output=_board_summary(project, board),
            next_prompt=f"python -m nexus.cli board point --project-path {project} \"<记录点>\"",
            artifact_refs=[str(board_path), str(project / ".nexus" / "board.md")],
        )

    def board_update(self, status: str, *, project_path: Path | None = None) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = self._resolve_active_project(project_path)
        if project is None:
            self._write_state(store, "blocked", current_node="board_update", blocked_reason="active_project_not_found")
            return write_interaction(
                store,
                status="blocked",
                output="没有找到当前项目上下文；请先调研/初始化项目，或显式提供项目路径。",
                next_prompt="python -m nexus.cli board update --project-path <project-path> --status \"<状态>\"",
                blocked_reason="active_project_not_found",
            )
        blocked = self._block_if_project_missing(store, project, current_node="board_update")
        if blocked:
            return blocked
        board = update_board(project, status=status)
        board_path = store.write_json("tool_results/project_board.json", board)
        self._write_active_project(project, source="board-update", run_id=store.run_id)
        self._write_state(store, "completed", current_node="board_update", project_path=str(project))
        return write_interaction(
            store,
            status="completed",
            output=f"已更新记录板当前状态。{_board_summary(project, board)}",
            next_prompt=f"python -m nexus.cli board show --project-path {project}",
            artifact_refs=[str(board_path), str(project / ".nexus" / "board.md")],
        )

    def board_point(self, point: str, *, project_path: Path | None = None) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        project = self._resolve_active_project(project_path)
        if project is None:
            self._write_state(store, "blocked", current_node="board_point", blocked_reason="active_project_not_found")
            return write_interaction(
                store,
                status="blocked",
                output="没有找到当前项目上下文；请先调研/初始化项目，或显式提供项目路径。",
                next_prompt="python -m nexus.cli board point --project-path <project-path> \"<记录点>\"",
                blocked_reason="active_project_not_found",
            )
        blocked = self._block_if_project_missing(store, project, current_node="board_point")
        if blocked:
            return blocked
        board = update_board(project, point=point)
        board_path = store.write_json("tool_results/project_board.json", board)
        self._write_active_project(project, source="board-point", run_id=store.run_id)
        self._write_state(store, "completed", current_node="board_point", project_path=str(project))
        return write_interaction(
            store,
            status="completed",
            output=f"已写入记录点：{point}。{_board_summary(project, board)}",
            next_prompt=f"python -m nexus.cli board show --project-path {project}",
            artifact_refs=[str(board_path), str(project / ".nexus" / "board.md")],
        )

    def skill_doctor(self) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        installed = Path.home() / ".codex" / "skills" / "nexus-workflow" / "SKILL.md"
        local = self.root / "skills" / "nexus-workflow" / "SKILL.md"
        payload = {
            "schema": "nexus.skill_doctor.v1",
            "installed_skill": str(installed),
            "installed_exists": installed.exists(),
            "local_skill": str(local),
            "local_exists": local.exists(),
            "recommended_absolute_repo": "<PROJECT_ROOT>",
            "recommended_command_prefix_codex": "cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface codex",
            "recommended_command_prefix_copilot": "cd <PROJECT_ROOT> && python -m nexus.cli --root <PROJECT_ROOT> --next-prompt-mode workflow --workflow-surface copilot",
            "relative_repo_jumps_forbidden": ["cd ../nexus", "cd nexus"],
            "hot_reload_note": "当前 Codex 会话可能不会热加载新安装 skill；若一句话触发无效，请重启会话。",
            "example": "$nexus-workflow 调研当前项目：检查是否已有可复用 workflow/kernel",
        }
        path = store.write_json("tool_results/skill_doctor.json", payload)
        self._write_state(store, "completed", current_node="skill_doctor", blocked_reason="")
        return write_interaction(
            store,
            status="completed",
            output=f"已检查 nexus-workflow skill：installed_exists={payload['installed_exists']}，local_exists={payload['local_exists']}。",
            next_prompt="$nexus-workflow 查看当前可用的基座模型和 provider 状态",
            artifact_refs=[str(path)],
        )

    def model_status(self) -> dict[str, object]:
        store = RunStore(self.root)
        store.ensure()
        model_payload = profile_status(self.root)
        provider_payload = doctor(self.root)
        model_path = store.write_json("tool_results/model_status.json", model_payload)
        provider_path = store.write_json("tool_results/provider_status.json", provider_payload)
        self._write_state(store, "completed", current_node="model_status", blocked_reason="")
        return write_interaction(
            store,
            status="completed",
            output=_runner_model_status_summary(model_payload),
            next_prompt="$nexus-workflow 初始化项目：<项目意图>；父目录为 <parent-dir>",
            artifact_refs=[str(model_path), str(provider_path)],
        )

    def invoke(self, request: str) -> dict[str, object]:
        lowered = request.lower()
        directive = _primary_invoke_directive(request)
        directive_lowered = directive.lower()
        run_id = _run_id_from_text(request)
        if run_id and ("fallback" in lowered or "preflight" in lowered or "权限问题" in request) and ("跳过" in request or "修复" in request):
            return self.recover(run_id, request)
        if run_id and "脱离 workflow" in request and "debug" in lowered:
            return self.handoff_for_debug(run_id, reason=request)
        if run_id and ("回跳" in request or "rebind" in lowered):
            return self.rebind_and_continue(run_id)
        status_match = re.search(r"(run-[0-9TZ-]+-[a-f0-9]+).{0,20}状态|状态.{0,20}(run-[0-9TZ-]+-[a-f0-9]+)", request)
        if status_match and ("查看" in request or "status" in lowered or "状态" in request):
            return self.status(status_match.group(1) or status_match.group(2))
        if "cli" in lowered and "下一步提示模式" in request:
            mode = "workflow" if any(marker in request for marker in ["关闭", "退出", "恢复默认", "workflow"]) else "cli"
            os.environ["NEXUS_NEXT_PROMPT_MODE"] = mode
            store = RunStore(self.root)
            store.ensure()
            path = self.root / ".data" / "session" / "next_prompt_mode.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"schema": "nexus.next_prompt_mode.v1", "mode": mode}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            output = "已开启 CLI 下一步提示模式。" if mode == "cli" else "已关闭 CLI 下一步提示模式，恢复 workflow 指令提示。"
            next_prompt = "后续 Nexus 标准化输出将显示 CLI 下一步。" if mode == "cli" else "后续 Nexus 标准化输出将显示 workflow 下一步指令。"
            return write_interaction(store, status="completed", output=output, next_prompt=next_prompt, artifact_refs=[str(path)])
        if _is_nexus_workflow_skill_check(directive):
            return self.skill_doctor()
        if _is_model_status_request(directive):
            return self.model_status()
        if run_id and ("审批" in directive or "approve" in directive_lowered):
            stage = _approval_stage_from_text(directive)
            if stage:
                if "并继续" in directive or "approve-and-continue" in directive_lowered:
                    return self.approve_and_continue(run_id, stage)
                return self.approve(run_id, stage)
        if _is_init_project_request(directive):
            parent = _parent_from_init_directive(directive)
            idea = _idea_from_init_directive(directive)
            return self.init_project(
                idea,
                parent=Path(parent or "."),
                raw_user_request=idea,
                normalized_request=idea,
            )
        if _is_research_request(directive):
            project = _research_project_from_directive(directive) or _path_after_marker(directive, ["项目路径", "project-root", "project_path"])
            idea = _research_idea_from_directive(directive)
            return self.run(
                idea,
                project_path=Path(project or "."),
                approve_online_search=_approves_online_search(directive),
            )
        if "更新上一个项目意图" in directive or "更新项目意图" in directive:
            return self.continue_run("latest", directive)
        if "记录板" in request or "记录点" in request:
            project_text = _path_after_marker(request, ["项目路径", "project-path", "project_path"]) or _path_after(request, ["项目", "路径"])
            project = Path(project_text) if project_text else None
            if "查看" in request or "show" in lowered:
                return self.board_show(project)
            if "更新" in request and ("状态" in request or "当前状态" in request):
                status = _text_after_markers(request, ["当前状态", "状态"]) or _text_after_colon(request)
                if not status:
                    status = "未记录"
                return self.board_update(status, project_path=project)
            if "记" in request or "point" in lowered:
                point = _text_after_colon(request) or _text_after_markers(request, ["记录点", "point"])
                if not point:
                    point = request
                result = self.board_point(point, project_path=project)
                result["previous_task_status"] = "blocked"
                result["blocked_reason"] = "next_step_required"
                return result
        if "补充初始化" in request or ("supplement" in lowered and "init" in lowered):
            path = _path_after_marker(request, ["项目路径", "project-path", "project_path"]) or _path_after(request, ["项目", "路径"]) or _self_project_path_from_text(request)
            return self.supplemental_init(
                Path(path or "."),
                idea=request,
                target="auto",
                github_private_enabled=not _wants_skip_github_sync(request),
                feishu_sync_enabled=not wants_skip_feishu_sync(request),
            )
        if "飞书" in request and ("初始化" in request or "登录" in request or "配置" in request):
            path = _path_after_marker(request, ["项目路径", "project-path", "project_path", "项目"])
            app_id_path = _path_after_marker(request, ["app_id 文件路径", "app-id-path", "app_id_path"])
            app_secret_path = _path_after_marker(request, ["app_secret 文件路径", "app-secret-path", "app_secret_path"])
            folder_token = _value_after_marker(request, ["folder_token", "folder-token", "文件夹 token"])
            folder_token_path = _path_after_marker(request, ["folder_token 文件路径", "folder-token-path", "folder_token_path", "文件夹 token 文件路径"])
            doc_token = _value_after_marker(request, ["doc_token", "document_id", "doc-token", "文档 id"])
            doc_token_path = _path_after_marker(request, ["doc_token 文件路径", "doc-token-path", "doc_token_path", "文档 token 文件路径"])
            return self.feishu_setup_flow(
                Path(path or "."),
                app_id_path=app_id_path,
                app_secret_path=app_secret_path,
                folder_token=folder_token,
                folder_token_path=folder_token_path,
                doc_token=doc_token,
                doc_token_path=doc_token_path,
                research_docs=False,
                approve_online_search=False,
            )
        if "飞书" in request and ("诊断" in request or "doctor" in lowered):
            path = _path_after_marker(request, ["项目路径", "project-path", "project_path", "项目"])
            return self.feishu_doctor_flow(Path(path or "."))
        if "github" in lowered and "指南" in request and "飞书" in request:
            path = _path_after_marker(request, ["项目路径", "project-path", "project_path"]) or _path_after(request, ["项目", "路径"]) or _self_project_path_from_text(request)
            return self.github_sync_guide(Path(path or "."), publish_feishu=True)
        if "github" in lowered and "指南" in request:
            path = _path_after_marker(request, ["项目路径", "project-path", "project_path"]) or _path_after(request, ["项目", "路径"]) or _self_project_path_from_text(request)
            return self.github_sync_guide(Path(path or "."))
        if "飞书" in request and ("记录" in request or "记一条" in request or "写入" in request):
            path = _path_after_marker(request, ["项目路径", "project-path", "project_path", "项目"])
            title = _quoted_text(request) or "Nexus 飞书记录"
            return self.feishu_record_flow(Path(path or "."), title=title, content=request)
        if "飞书" in request and ("同步" in request or "发布" in request):
            path = _path_after(request, ["项目", "路径"])
            if "架构" in request or "展示" in request or "system-showcase" in lowered:
                return self.system_showcase_publish_feishu(Path(path or "."), confirm="确认" in request)
            return self.operation_guide(Path(path or "."), publish_feishu=True)
        if "github" in lowered and ("bootstrap" in lowered or "初始化" in request or "建仓" in request):
            path = _path_after(request, ["项目", "路径"]) or _self_project_path_from_text(request)
            private_repo, public_repo = _github_repos_from_text(request)
            return self.github_sync_bootstrap(Path(path or "."), private_repo=private_repo, public_repo=public_repo)
        if "github" in lowered and "public" in lowered:
            path = _path_after(request, ["项目", "路径"]) or _self_project_path_from_text(request)
            return self.github_sync_public(Path(path or "."), confirm="确认" in request)
        if "github" in lowered and ("auto-private" in lowered or "自动同步" in request):
            path = _path_after(request, ["项目", "路径"]) or _self_project_path_from_text(request)
            return self.github_sync_auto_private(Path(path or "."), commit_message="nexus invoke auto private sync")
        if "github" in lowered and ("private" in lowered or "同步" in request):
            path = _path_after(request, ["项目", "路径"]) or _self_project_path_from_text(request)
            return self.github_sync_private(Path(path or "."))
        if "架构" in request and ("展示" in request or "系统" in request):
            path = _path_after(request, ["项目", "路径"])
            return self.system_showcase_generate(Path(path or "."))
        if "对话记录" in request and ("管理" in request or "初始化" in request):
            path = _path_after(request, ["项目", "路径"])
            return self.conversation_manager_init(Path(path or "."))
        if "安装" in request and "skill" in lowered:
            return self.install_generated_skill("latest", confirm=True)
        if "整理" in request and ("skill" in lowered or "workflow" in lowered):
            path = _path_after(request, ["从", "文件", "transcript"])
            if path:
                return self.conversation_from_file(Path(path), selector=_selector_from_text(request))
            return self.conversation_to_workflow(
                current="本次" in request or "当前" in request,
                all_history="全部对话历史" in request or "所有对话" in request,
                match=_quoted_text(request),
                task=_task_name_from_text(request),
                selector=_selector_from_text(request),
            )
        if "git" in lowered and ("baseline" in lowered or "纳入" in request or "管理" in request):
            path = _path_after(request, ["项目", "路径"])
            return self.prepare_project(Path(path or "."))
        if "候选" in request and ("重排" in request or "排名" in request or "去重" in request):
            return self.rerank_candidates("latest")
        store = RunStore(self.root)
        store.ensure()
        self._write_state(store, "blocked", current_node="invoke", blocked_reason="unsupported_invoke")
        return write_interaction(
            store,
            status="blocked",
            output="这句 nexus invoke 暂未匹配到可执行端到端节点。",
            next_prompt="可用入口：调研项目 / 候选去重排名 / 项目 git baseline / 从 transcript 整理 skill/workflow / 安装上一个 skill。",
            blocked_reason="unsupported_invoke",
        )

    def _write_state(self, store: RunStore, status: str, **extra: object) -> None:
        payload = {
            "schema": "nexus.state.v1",
            "run_id": store.run_id,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        store.write_json("state.json", payload)

    def _acquire_workflow_resume_lock(self, store: RunStore) -> Path | dict[str, object]:
        lock_path = store.path("workflow_resume.lock")
        lock_payload = {"pid": os.getpid(), "created_at": datetime.now(timezone.utc).isoformat()}
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = _read_json_if_exists(lock_path)
            pid = int(existing.get("pid") or 0) if str(existing.get("pid") or "").isdigit() else 0
            if pid and _pid_is_running(pid):
                state = _read_json_if_exists(store.path("state.json"))
                return write_interaction(
                    store,
                    status="blocked",
                    output=f"该 run 已有 resume 进程在执行：pid={pid}；当前节点：{state.get('current_node', 'unknown')}。",
                    next_prompt=f"$nexus-workflow 查看 {store.run_id} 的状态",
                    blocked_reason="workflow_resume_already_running",
                    artifact_refs=[str(lock_path), str(store.path("state.json"))],
                )
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            return self._acquire_workflow_resume_lock(store)
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(lock_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            return lock_path

    def _release_workflow_resume_lock(self, lock_path: Path) -> None:
        try:
            payload = _read_json_if_exists(lock_path)
            pid = int(payload.get("pid") or 0) if str(payload.get("pid") or "").isdigit() else 0
            if pid in {0, os.getpid()}:
                lock_path.unlink()
        except FileNotFoundError:
            pass

    def _block_if_project_missing(self, store: RunStore, project: Path, *, current_node: str) -> dict[str, object] | None:
        if project.exists() and project.is_dir():
            return None
        self._write_state(store, "blocked", current_node=current_node, blocked_reason="project_path_not_found", project_path=str(project))
        return write_interaction(
            store,
            status="blocked",
            output=f"目标项目目录不存在：{project}",
            next_prompt="python -m nexus.cli board show --project-path <project-path>",
            blocked_reason="project_path_not_found",
        )

    def _write_active_project(self, project: Path, *, source: str, run_id: str = "") -> None:
        path = self.root / ".data" / "session" / "active_context.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "nexus.active_context.v1",
            "project_path": str(project.expanduser().resolve()),
            "source": source,
            "run_id": run_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _resolve_active_project(self, project_path: Path | None) -> Path | None:
        if project_path is not None:
            return project_path.expanduser().resolve()
        latest = self._project_from_latest_run()
        if latest is not None:
            return latest
        path = self.root / ".data" / "session" / "active_context.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            project = str(payload.get("project_path") or "")
            if project:
                return Path(project).expanduser().resolve()
        return None

    def _project_from_latest_run(self) -> Path | None:
        runs_dir = self.root / ".data" / "runs"
        if not runs_dir.exists():
            return None
        runs = sorted((path for path in runs_dir.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True)
        for run_dir in runs:
            for relative in ["input.json", "state.json"]:
                path = run_dir / relative
                if not path.exists():
                    continue
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                for key in ["project_path", "target_path"]:
                    value = str(payload.get(key) or "")
                    if value:
                        return Path(value).expanduser().resolve()
        return None

    def _write_online_search_approval_marker(self, store: RunStore, approved_by: str) -> Path:
        return store.write_json(
            "approvals/APPROVED_online-search.json",
            {
                "schema": "nexus.approval_marker.v1",
                "stage": "online-search",
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "approved_by": approved_by,
                "scope": "允许只读在线检索公开来源；不允许登录、读密钥、绕过验证码/403/限流、提交表单或写目标项目。",
            },
        )

    def _write_branch_research_artifacts(self, store: RunStore, branch_artifacts: dict[str, object]) -> dict[str, Path]:
        if not bool(branch_artifacts.get("enabled")):
            return {}
        reports = branch_artifacts.get("reports") if isinstance(branch_artifacts.get("reports"), dict) else {}
        refs: dict[str, Path] = {}
        mapping = {
            "existing_wheel_build": "reports/branch_existing_wheel.md",
            "subproject_wheel_research": "reports/branch_subproject_wheels.md",
            "from_scratch_build": "reports/branch_from_scratch.md",
        }
        for branch_id, relative in mapping.items():
            report = reports.get(branch_id) if isinstance(reports.get(branch_id), dict) else {}
            refs[branch_id] = store.write_text(relative, render_branch_report(report))
            store.write_json(relative.replace(".md", ".json"), report)
        matrix = branch_artifacts.get("decision_matrix") if isinstance(branch_artifacts.get("decision_matrix"), dict) else {}
        refs["decision_matrix"] = store.write_json("reports/decision_matrix.json", matrix)
        refs["decision_matrix_md"] = store.write_text("reports/decision_matrix.md", render_decision_matrix(matrix))
        return refs

    def _write_report(
        self,
        store: RunStore,
        report: dict[str, object],
        ranked: list[dict[str, object]],
        provider_name: str,
        *,
        research_contract: dict[str, object] | None = None,
        branch_refs: dict[str, Path] | None = None,
    ) -> Path:
        lines = [
            "# Nexus Discovery Report",
            "",
            f"- provider: `{provider_name}`",
            "- locale: `zh-CN`",
            "- market_context: `chinese_internet`",
            "",
            "## 摘要",
            str(report.get("summary") or ""),
            "",
        ]
        if research_contract and bool(research_contract.get("requires_branch_reports")):
            refs = branch_refs or {}
            lines.extend(
                [
                    "## 调研决策分支",
                    "",
                    f"- research_contract: `{store.path('reports', 'research_contract.md')}`",
                    f"- existing_wheel_build: `{refs.get('existing_wheel_build', store.path('reports', 'branch_existing_wheel.md'))}`",
                    f"- subproject_wheel_research: `{refs.get('subproject_wheel_research', store.path('reports', 'branch_subproject_wheels.md'))}`",
                    f"- from_scratch_build: `{refs.get('from_scratch_build', store.path('reports', 'branch_from_scratch.md'))}`",
                    f"- decision_matrix: `{refs.get('decision_matrix_md', store.path('reports', 'decision_matrix.md'))}`",
                    "",
                    "本轮属于搭建决策型调研。未选择上述任一分支前，不允许直接进入 implementation_plan。",
                    "",
                ]
            )
        lines.append("## 发现")
        lines.extend(f"- {item}" for item in report.get("findings", []) if isinstance(item, str))
        lines.extend(["", "## 候选排行"])
        for item in ranked:
            lines.append(f"- `{item['id']}` score={item.get('score')}: {item.get('title')} - {item.get('reason', '')}")
        lines.extend(["", "## 下一步"])
        lines.extend(f"- {item}" for item in report.get("next_action_plan", []) if isinstance(item, str))
        return store.write_text("reports/final_report.md", "\n".join(lines).rstrip() + "\n")

    def _write_next_action(self, store: RunStore, report: dict[str, object], risk: dict[str, object]) -> Path:
        payload = {
            "schema": "nexus.next_action_plan.v1",
            "actions": report.get("next_action_plan", []),
            "risks": risk.get("risks", []),
            "approval_required": risk.get("approval_required", False),
        }
        store.write_json("reports/next_action_plan.json", payload)
        return store.write_text(
            "reports/next_action_plan.md",
            "# Next Action Plan\n\n" + "\n".join(f"- {item}" for item in payload["actions"] if isinstance(item, str)) + "\n",
        )

    def _write_next_options(
        self,
        store: RunStore,
        *,
        stage: str,
        completion_quality: str,
        summary: str,
        weak_points: list[str],
        research_contract: dict[str, object] | None = None,
        branch_refs: dict[str, Path] | None = None,
    ) -> Path:
        payload = _next_options_payload(
            store,
            stage=stage,
            completion_quality=completion_quality,
            summary=summary,
            weak_points=weak_points,
            research_contract=research_contract,
            branch_refs=branch_refs,
        )
        validated = validate_json(payload, SCHEMAS["next_options"])
        store.write_json("reports/next_options.json", validated)
        return store.write_text("reports/next_options.md", _render_next_options(validated))

    def _resolve_run_id(self, run_id: str) -> str:
        if run_id != "latest":
            return run_id
        runs_dir = self.root / ".data" / "runs"
        if not runs_dir.exists():
            raise ValueError("No nexus runs found")
        runs = [path for path in runs_dir.iterdir() if path.is_dir()]
        if not runs:
            raise ValueError("No nexus runs found")
        return max(runs, key=lambda path: path.stat().st_mtime).name

    def _resolve_resumable_run_id(self, run_id: str) -> str:
        if run_id != "latest":
            return run_id
        runs_dir = self.root / ".data" / "runs"
        if not runs_dir.exists():
            raise ValueError("No nexus runs found")
        runs = [path for path in runs_dir.iterdir() if path.is_dir() and (path / "input.json").exists()]
        if not runs:
            raise ValueError("No resumable nexus runs found")
        return max(runs, key=lambda path: path.stat().st_mtime).name

    def _online_search_approved(self, store: RunStore) -> bool:
        return store.path("approvals", "APPROVED_online-search.json").exists()

    def _write_external_prompt(
        self,
        store: RunStore,
        idea: str,
        research_plan: dict[str, object],
        statuses: list[SourceStatus],
    ) -> Path:
        lines = [
            "# External GPT Research Prompt",
            "",
            "请作为中文互联网优先的调研助手，补充检索以下需求。",
            "",
            f"用户需求：{idea}",
            "",
            "## 当前 research plan",
            "```json",
            json.dumps(research_plan, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 当前 source status",
            "```json",
            json.dumps([status.to_dict() for status in statuses], ensure_ascii=False, indent=2),
            "```",
            "",
            "请输出候选项目/工具/文档、来源链接、中文互联网可用性、风险和结论可信度。",
        ]
        return store.write_text("reports/external_gpt_research_prompt.md", "\n".join(lines) + "\n")


def _next_options_payload(
    store: RunStore,
    *,
    stage: str,
    completion_quality: str,
    summary: str,
    weak_points: list[str],
    research_contract: dict[str, object] | None = None,
    branch_refs: dict[str, Path] | None = None,
) -> dict[str, object]:
    refs = branch_refs or {}
    step_output: dict[str, object] = {
        "report_ref": str(store.path("reports", "final_report.md")),
        "source_status_ref": str(store.path("tool_results", "source_status.json")),
        "candidate_refs": [
            str(store.path("candidates", "ranked_candidates.json")),
            str(store.path("candidates", "candidates.jsonl")),
        ],
    }
    if research_contract and bool(research_contract.get("requires_branch_reports")):
        step_output.update(
            {
                "research_contract_ref": str(store.path("reports", "research_contract.md")),
                "branch_report_refs": [
                    str(refs.get("existing_wheel_build", store.path("reports", "branch_existing_wheel.md"))),
                    str(refs.get("subproject_wheel_research", store.path("reports", "branch_subproject_wheels.md"))),
                    str(refs.get("from_scratch_build", store.path("reports", "branch_from_scratch.md"))),
                ],
                "decision_matrix_ref": str(refs.get("decision_matrix_md", store.path("reports", "decision_matrix.md"))),
            }
        )
    options = _build_decision_next_options(store) if research_contract and bool(research_contract.get("requires_branch_reports")) else _general_next_options(store)
    return {
        "schema": "nexus.next_options.v1",
        "current_state": {
            "run_id": store.run_id,
            "stage": stage,
            "completion_quality": completion_quality,
            "summary": summary,
            "weak_points": weak_points,
        },
        "step_output": step_output,
        "next_options": options,
    }


def _implementation_plan_gate(store: RunStore) -> dict[str, object] | None:
    contract = _read_json_if_exists(store.path("reports", "research_contract.json"))
    if not bool(contract.get("requires_branch_reports")):
        return None
    selected = _read_json_if_exists(store.path("reports", "selected_research_branch.json"))
    branch_id = str(selected.get("branch_id") or "")
    if selected_branch_is_valid(branch_id):
        branch_path = _branch_report_path(store, branch_id)
        if not branch_path.exists():
            return {
                "schema": "nexus.implementation_plan_gate.v1",
                "blocked_reason": "selected_branch_report_missing",
                "message": f"已选择调研分支 {branch_id}，但对应分支报告不存在：{branch_path}；不能生成 implementation_plan。",
                "required_artifacts": [str(branch_path)],
                "next_prompt": f"python -m nexus.cli continue {store.run_id} \"重新调研：补充分支报告\"",
            }
        return None
    return {
        "schema": "nexus.implementation_plan_gate.v1",
        "blocked_reason": "research_branch_selection_required",
        "message": "本轮是搭建决策型调研，必须先在已有轮子、分块继续调研、从零搭建三个方向中选择一个分支；未选择分支前不能生成 implementation_plan。",
        "required_artifacts": [
            str(store.path("reports", "branch_existing_wheel.md")),
            str(store.path("reports", "branch_subproject_wheels.md")),
            str(store.path("reports", "branch_from_scratch.md")),
            str(store.path("reports", "decision_matrix.md")),
        ],
        "next_prompt": f"python -m nexus.cli continue {store.run_id} \"从零搭建方案\"",
    }


def _branch_report_path(store: RunStore, branch_id: str) -> Path:
    mapping = {
        "existing_wheel_build": store.path("reports", "branch_existing_wheel.md"),
        "subproject_wheel_research": store.path("reports", "branch_subproject_wheels.md"),
        "from_scratch_build": store.path("reports", "branch_from_scratch.md"),
    }
    return mapping.get(branch_id, store.path("reports", "final_report.md"))


def _existing_branch_refs(store: RunStore) -> dict[str, Path]:
    refs = {
        "existing_wheel_build": store.path("reports", "branch_existing_wheel.md"),
        "subproject_wheel_research": store.path("reports", "branch_subproject_wheels.md"),
        "from_scratch_build": store.path("reports", "branch_from_scratch.md"),
        "decision_matrix": store.path("reports", "decision_matrix.json"),
        "decision_matrix_md": store.path("reports", "decision_matrix.md"),
    }
    return {key: path for key, path in refs.items() if path.exists()}


def _discovery_next_prompt(store: RunStore, research_contract: dict[str, object]) -> str:
    if bool(research_contract.get("requires_branch_reports")):
        return _multi_option_prompt(
            [
                f"选项 1：查看报告 python -m nexus.cli report {store.run_id}",
                f"选项 2：选择已有轮子方案 python -m nexus.cli continue {store.run_id} \"选择已有轮子方案：<candidate-id>\"",
                f"选项 3：拆成多个小项目继续调研 python -m nexus.cli continue {store.run_id} \"分块调研：<module list>\"",
                f"选项 4：选择从零搭建方案 python -m nexus.cli continue {store.run_id} \"从零搭建方案\"",
                f"选项 5：重新调研 python -m nexus.cli continue {store.run_id} \"重新调研：<缺口或网站>\"",
            ]
        )
    return _multi_option_prompt(
        [
            f"选项 1：查看报告 python -m nexus.cli report {store.run_id}",
            f"选项 2：生成项目计划 python -m nexus.cli continue {store.run_id} \"生成项目计划\"",
            f"选项 3：局部调研 python -m nexus.cli continue {store.run_id} \"局部调研：provider 层\"",
            f"选项 4：重新调研 python -m nexus.cli continue {store.run_id} \"重新调研：重点看中文互联网现成项目\"",
        ]
    )


def _general_next_options(store: RunStore) -> list[dict[str, object]]:
    return [
        {
            "id": "rerun_research",
            "label": "重新调研",
            "intent_examples": ["重新调研", "换关键词再查一次", "重点看中文互联网现成项目"],
            "command": f"python -m nexus.cli continue {store.run_id} \"重新调研：<重点>\"",
            "requires_model": True,
            "requires_approval": False,
        },
        {
            "id": "local_research",
            "label": "局部调研",
            "intent_examples": ["局部调研 provider 层", "只看检索模块", "只分析 skill 触发层"],
            "command": f"python -m nexus.cli continue {store.run_id} \"局部调研：<范围>\"",
            "requires_model": True,
            "requires_approval": False,
        },
        {
            "id": "chunked_research",
            "label": "分块调研",
            "intent_examples": ["分块调研", "拆成 provider/search/runner 调研", "分多个部分继续"],
            "command": f"python -m nexus.cli continue {store.run_id} \"分块调研：<分块要求>\"",
            "requires_model": True,
            "requires_approval": False,
        },
        {
            "id": "update_intent",
            "label": "更新项目需求意图",
            "intent_examples": ["更新需求", "项目目标改成...", "意图更新为..."],
            "command": f"python -m nexus.cli continue {store.run_id} \"更新项目意图：<新意图>\"",
            "requires_model": True,
            "requires_approval": False,
        },
        {
            "id": "implementation_plan",
            "label": "生成项目计划",
            "intent_examples": ["生成项目计划", "进入 implementation plan", "按报告给我实施计划"],
            "command": f"python -m nexus.cli continue {store.run_id} \"生成项目计划\"",
            "requires_model": True,
            "requires_approval": True,
        },
    ]


def _build_decision_next_options(store: RunStore) -> list[dict[str, object]]:
    return [
        {
            "id": "branch_existing_wheel",
            "label": "选择已有轮子方案",
            "intent_examples": ["选择已有轮子方案", "基于现成轮子搭建", "采用候选项目作为基础"],
            "command": f"python -m nexus.cli continue {store.run_id} \"选择已有轮子方案：<candidate-id>\"",
            "requires_model": True,
            "requires_approval": False,
        },
        {
            "id": "branch_subproject_wheels",
            "label": "拆成多个小项目继续调研",
            "intent_examples": ["分块调研", "拆成多个小项目继续找轮子", "按模块继续调研"],
            "command": f"python -m nexus.cli continue {store.run_id} \"分块调研：<module list>\"",
            "requires_model": True,
            "requires_approval": False,
        },
        {
            "id": "branch_from_scratch",
            "label": "选择从零搭建方案",
            "intent_examples": ["从零搭建方案", "没有合适轮子，直接组织项目搭建"],
            "command": f"python -m nexus.cli continue {store.run_id} \"从零搭建方案\"",
            "requires_model": True,
            "requires_approval": False,
        },
        {
            "id": "rerun_research",
            "label": "重新调研",
            "intent_examples": ["重新调研", "补充指定网站", "补证据缺口"],
            "command": f"python -m nexus.cli continue {store.run_id} \"重新调研：<缺口或网站>\"",
            "requires_model": True,
            "requires_approval": False,
        },
        {
            "id": "update_intent",
            "label": "更新项目需求意图",
            "intent_examples": ["更新需求", "补充约束", "修改调研目标"],
            "command": f"python -m nexus.cli continue {store.run_id} \"更新项目意图：<新意图>\"",
            "requires_model": True,
            "requires_approval": False,
        },
    ]


def _status_from_dict(item: dict[str, Any]) -> SourceStatus:
    allowed = SourceStatus.__dataclass_fields__.keys()
    payload = {key: value for key, value in item.items() if key in allowed}
    return SourceStatus(**payload)


def _candidate_from_dict(item: dict[str, Any]) -> CandidateRecord:
    allowed = CandidateRecord.__dataclass_fields__.keys()
    payload = {key: value for key, value in item.items() if key in allowed}
    return CandidateRecord(**payload)


def _conversation_text(messages: list[dict[str, object]]) -> str:
    chunks = []
    for message in messages:
        chunks.append(
            f"message_id: {message.get('message_id')}\n"
            f"role: {message.get('role')}\n"
            f"text:\n{message.get('text')}\n"
        )
    return "\n---\n".join(chunks)


def _skill_draft_from_conversation(result: dict[str, object]) -> str:
    draft = str(result.get("skill_or_workflow_draft") or "").strip()
    if draft.startswith("---") and "name:" in draft.split("---", 2)[1]:
        return draft.rstrip() + "\n"
    name = _safe_skill_name(str(result.get("generalized_project_type") or "generated-workflow"))
    description = str(result.get("source_summary") or "Generated workflow skill from conversation transcript.").replace("\n", " ")
    lines = [
        "---",
        f"name: {name}",
        f"description: {description[:220]}",
        "---",
        "",
        f"# {name}",
        "",
        "This skill was generated from a real conversation transcript by nexus.",
        "",
        "## Workflow",
    ]
    lines.extend(f"- {item}" for item in result.get("workflow_blueprint", []) if isinstance(item, str))
    lines.extend(["", "## Draft", draft or "No draft body returned.", "", "## Safety"])
    lines.extend(f"- {item}" for item in result.get("safety_notes", []) if isinstance(item, str))
    return "\n".join(lines).rstrip() + "\n"


def _safe_skill_name(text: str) -> str:
    import re

    lowered = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    if not lowered:
        lowered = "generated-workflow"
    if not lowered[0].isalnum():
        lowered = "skill-" + lowered
    return lowered[:48]


def _summary_payload_from_workflow(result: dict[str, object]) -> dict[str, object]:
    return {
        "background": result.get("source_summary", ""),
        "user_goal": result.get("generalized_project_type", ""),
        "final_conclusion": "该对话已被抽象为可复用 workflow/skill/prompt 候选。",
        "project_conclusions": [
            {"conclusion": item, "module": "conversation-manager", "evidence": "conversation_workflow model node", "urgent": "yes"}
            for item in result.get("workflow_blueprint", [])
            if isinstance(item, str)
        ],
        "completed_actions": ["生成 summary/workflow/prompt/skill candidate。"],
        "todos": [item for item in result.get("next_options", []) if isinstance(item, str)],
        "risks": [item for item in result.get("safety_notes", []) if isinstance(item, str)],
    }


def _primary_invoke_directive(text: str) -> str:
    stripped = text.strip()
    for marker in ["\n\n全局 Nexus 问题验收", "\n\n全局问题验收", "\n\n验收要求"]:
        if marker in stripped:
            return stripped.split(marker, 1)[0].strip()
    if stripped.startswith("$nexus-workflow") and "\n\n" in stripped:
        return stripped.split("\n\n", 1)[0].strip()
    return stripped


def _is_nexus_workflow_skill_check(text: str) -> bool:
    lowered = text.lower()
    return (
        "检查当前 nexus workflow 是否安装并可用" in text
        or "nexus workflow 是否安装" in text
        or "nexus-workflow 是否安装" in lowered
        or ("skill" in lowered and "是否可用" in text and "nexus" in lowered)
    )


def _is_model_status_request(text: str) -> bool:
    return "查看当前可用的基座模型" in text or ("provider 状态" in text and ("基座模型" in text or "模型" in text))


def _approval_stage_from_text(text: str) -> str:
    lowered = text.lower()
    for stage in [
        "implementation-plan",
        "project-root",
        "online-search",
        "conversation-session-read",
        "code-change",
        "apply",
        "git-baseline",
        "skill-install",
        "recovery-playbook",
    ]:
        if stage in lowered:
            return stage
    stage_aliases = {
        "项目根目录": "project-root",
        "在线检索": "online-search",
        "代码修改": "code-change",
        "应用补丁": "apply",
        "恢复 playbook": "recovery-playbook",
    }
    for marker, stage in stage_aliases.items():
        if marker in text:
            return stage
    return ""


def _is_init_project_request(text: str) -> bool:
    return "初始化项目" in text or "init-project" in text.lower()


def _parent_from_init_directive(text: str) -> str:
    return _path_after_marker(text, ["父目录为", "父目录", "parent-dir", "parent"])


def _idea_from_init_directive(text: str) -> str:
    cleaned = re.sub(r"^\s*\$?nexus-workflow\s*", "", text).strip()
    cleaned = re.sub(r"^(初始化项目|init-project)\s*[：:]*\s*", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or text


def _is_research_request(text: str) -> bool:
    lowered = text.lower()
    return "调研当前项目" in text or "调研项目" in text or lowered.startswith("research ")


def _research_project_from_directive(text: str) -> str:
    match = re.search(r"调研项目\s+(/Users/[^\s：:，。；]+|~/[^\s：:，。；]+|\.{1,2}/[^\s：:，。；]+)", text)
    if match:
        return match.group(1)
    return _path_after_marker(text, ["project-root", "project-path", "项目路径"])


def _research_idea_from_directive(text: str) -> str:
    cleaned = re.sub(r"^\s*\$?nexus-workflow\s*", "", text).strip()
    cleaned = re.sub(r"^调研当前项目\s*[：:]*\s*", "", cleaned).strip()
    cleaned = re.sub(r"^调研项目\s+(/Users/[^\s：:，。；]+|~/[^\s：:，。；]+|\.{1,2}/[^\s：:，。；]+)\s*[：:]*\s*", "", cleaned).strip()
    cleaned = re.sub(r"^research\s*", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or text


def _approves_online_search(text: str) -> bool:
    lowered = text.lower()
    return "approve-online-search" in lowered or ("online-search" in lowered and ("审批" in text or "允许" in text))


def _runner_model_status_summary(payload: dict[str, object]) -> str:
    intensity = payload.get("intensity") if isinstance(payload.get("intensity"), dict) else {}
    low_api = str(intensity.get("low_api_profile") or "未配置")
    high_api = str(intensity.get("high_api_profile") or "未配置")
    return (
        f"低强度 API 槽位：{low_api}；低强度实际顺序：API {low_api} -> codex-cli gpt-5.4 -> codex-mcp；"
        f"高强度 API fallback 槽位：{high_api}；高强度实际顺序：codex-cli gpt-5.4 -> API {high_api} -> codex-mcp；"
        f"当前默认模型：{payload.get('current')}；"
        f"可直接使用/已配置：{', '.join(str(item) for item in payload.get('available_or_builtin', []) if item)}；"
        f"需要配置：{', '.join(str(item) for item in payload.get('needs_config', []) if item)}；"
        f"暂未提供：{', '.join(str(item) for item in payload.get('unsupported', []) if item)}。"
    )


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _path_after(text: str, markers: list[str]) -> str:
    import re

    matches = re.findall(r"(/Users/[^\s，。；]+|~/[^\s，。；]+|\.{1,2}/[^\s，。；]+)", text)
    if matches:
        return matches[0]
    for marker in markers:
        if marker in text:
            tail = text.split(marker, 1)[1].strip(" ：:")
            if tail:
                return tail.split()[0].strip("，。；")
    return ""


def _text_after_colon(text: str) -> str:
    for marker in ["：", ":"]:
        if marker in text:
            return text.split(marker, 1)[1].strip()
    return ""


def _text_after_markers(text: str, markers: list[str]) -> str:
    for marker in markers:
        if marker not in text:
            continue
        tail = text.split(marker, 1)[1].strip(" ：:=，")
        if tail:
            return tail
    return ""


def _board_summary(project: Path, board: dict[str, object]) -> str:
    points = board.get("points")
    recent: list[str] = []
    if isinstance(points, list):
        for item in points[:3]:
            if isinstance(item, dict):
                text = str(item.get("text") or "")
                if text:
                    recent.append(text)
    recent_text = "；最近记录：" + " / ".join(recent) if recent else "；最近记录：无"
    return f"项目：{project}；当前状态：{board.get('current_status', '未记录')}{recent_text}"


def _path_after_marker(text: str, markers: list[str]) -> str:
    import re

    for marker in markers:
        if marker not in text:
            continue
        tail = text.split(marker, 1)[1].strip(" ：:=，")
        match = re.search(r"(/Users/[^\s，。；]+|~/[^\s，。；]+|\.{1,2}/[^\s，。；]+)", tail)
        if match:
            return match.group(1)
        if tail:
            return tail.split()[0].strip("，。；")
    return ""


def _value_after_marker(text: str, markers: list[str]) -> str:
    import re

    for marker in markers:
        if marker not in text:
            continue
        tail = text.split(marker, 1)[1].strip(" ：:=，")
        quoted = _quoted_text(tail)
        if quoted:
            return quoted
        match = re.match(r"([A-Za-z0-9_\\-]+)", tail)
        return match.group(1) if match else ""
    return ""


def _selector_from_text(text: str) -> str:
    for marker in ["selector:", "ref:", "问题"]:
        if marker in text:
            return text[text.index(marker) :].strip()
    return ""


def _quoted_text(text: str) -> str:
    import re

    match = re.search(r"[“\"]([^”\"]+)[”\"]", text)
    return match.group(1).strip() if match else ""


def _task_name_from_text(text: str) -> str:
    for marker in ["任务名称", "任务名", "另一个任务的对话窗口"]:
        if marker in text:
            tail = text.split(marker, 1)[1].strip(" ：:")
            quoted = _quoted_text(tail)
            return quoted or tail.split("，", 1)[0].split("。", 1)[0].strip()
    return ""


def _wants_feishu_autowrite(text: str) -> bool:
    return "飞书" in text and any(marker in text for marker in ["在线文档记录", "写入飞书", "飞书记录", "启用飞书"])


def _wants_skip_github_sync(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ["no-github-sync", "skip github sync", "without github sync", "do not sync github"]) or any(marker in text for marker in ["不同步 GitHub", "不同步github", "跳过 GitHub 同步", "跳过github同步", "不要同步 GitHub", "不要同步github"])


def _github_repos_from_text(text: str) -> tuple[str, str]:
    private_repo = _repo_after_markers(text, ["private-repo", "private repo", "私有仓库", "private仓库"])
    public_repo = _repo_after_markers(text, ["public-repo", "public repo", "公开仓库", "public仓库"])
    return private_repo, public_repo


def _repo_after_markers(text: str, markers: list[str]) -> str:
    import re

    for marker in markers:
        if marker not in text:
            continue
        tail = text.split(marker, 1)[1].strip(" ：:=，")
        match = re.search(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", tail)
        if match:
            return match.group(1)
    return ""


def _project_kind(project: Path) -> str:
    if project.name == "nexus":
        return "nexus"
    if project.name == "verix":
        return "verix"
    return "user_project"


def _default_github_repos(project: Path) -> tuple[str, str]:
    if project.name == "nexus":
        return "<PRIVATE_REPO>", "YaofeiHe/nexus-public"
    if project.name == "verix":
        return "YaofeiHe/verix", "YaofeiHe/verix-public"
    return "", ""


def _default_github_repos_for_new_project(project: Path) -> tuple[str, str]:
    owner = os.environ.get("NEXUS_DEFAULT_GITHUB_OWNER", "YaofeiHe").strip() or "YaofeiHe"
    safe_name = "".join(char if char.isalnum() or char in "._-" else "-" for char in project.name).strip(".-") or "nexus-project"
    return f"{owner}/{safe_name}", f"{owner}/{safe_name}-public"


def _write_and_load_github_config(project: Path, private_repo: str, public_repo: str) -> dict[str, object]:
    write_github_sync_config(project, private_repo, public_repo, project_kind=_project_kind(project))
    return load_github_sync_config(project) or {}


def _github_auth_output(auth: dict[str, object]) -> str:
    state = str(auth.get("state") or "LOGIN_INCOMPLETE")
    instruction = str(auth.get("current_instruction") or "")
    device_url = str(auth.get("device_url") or "")
    device_code = str(auth.get("device_code") or "")
    if device_code:
        return f"GitHub CLI 未认证，已触发登录流程但仍需用户完成授权。state={state}；打开 {device_url} 输入校验码 {device_code}，完成密码/2FA/CAPTCHA/授权后重试同步。"
    return f"GitHub CLI 未认证，已触发登录流程。state={state}；{instruction}"


def _read_json_if_exists(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_debug_worklog_entries(store: RunStore) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for relative in ["worklogs/debug_summary.json", "worklogs/debug_worklog.json"]:
        payload = _read_json_if_exists(store.path(relative))
        raw_entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("handoff_id") or ""),
                str(item.get("kind") or ""),
                str(item.get("summary") or ""),
                str(item.get("command") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            entries.append(item)
    return entries


def _public_result_from_interaction(interaction: dict[str, object], result_path: Path | None) -> dict[str, object]:
    if result_path is not None and result_path.exists():
        payload = _read_json_if_exists(result_path)
        if payload:
            return payload
    status = "completed" if interaction.get("previous_task_status") == "completed" else "blocked"
    return {
        "schema": "nexus.github_public_sync.v1",
        "status": status,
        "reason": "pushed_public" if status == "completed" else str(interaction.get("blocked_reason") or "github_public_sync_blocked"),
        "interaction_run_id": str(interaction.get("run_id") or ""),
    }


def _github_result_from_interaction(interaction: dict[str, object], result_path: Path | None) -> dict[str, object]:
    if result_path is not None and result_path.exists():
        payload = _read_json_if_exists(result_path)
        if payload:
            return payload
    status = "completed" if interaction.get("previous_task_status") == "completed" else "blocked"
    return {
        "schema": "nexus.github_bootstrap.v1",
        "status": status,
        "reason": "bootstrapped_and_pushed_private" if status == "completed" else str(interaction.get("blocked_reason") or "github_bootstrap_blocked"),
        "interaction_run_id": str(interaction.get("run_id") or ""),
    }


def _recovery_kwargs_from_interaction(interaction: dict[str, object] | None) -> dict[str, object]:
    if not interaction:
        return {}
    keys = [
        "lifecycle_status",
        "pending_actions",
        "continuation",
        "auto_resume_supported",
        "recovery_mode",
        "recovery_state",
        "recovery_kind",
        "safe_next_actions",
        "recommended_executor",
    ]
    return {key: interaction[key] for key in keys if key in interaction and interaction[key]}


@contextmanager
def _temporary_env(values: dict[str, str]):
    previous: dict[str, str | None] = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value == "":
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _github_result_contains_api_eof(result: object) -> bool:
    if isinstance(result, dict):
        for key, value in result.items():
            if key in {"stdout", "stderr", "safe_error", "output", "reason"} and _github_result_contains_api_eof(value):
                return True
            if isinstance(value, (dict, list)) and _github_result_contains_api_eof(value):
                return True
        return False
    if isinstance(result, list):
        return any(_github_result_contains_api_eof(item) for item in result)
    text = str(result or "").lower()
    if "eof" not in text:
        return False
    return "github.com" in text or "api.github.com" in text or "graphql" in text or "login/device/code" in text


def _self_project_path_from_text(text: str) -> str:
    forge = Path("<FORGE_ROOT>")
    if "nexus" in text.lower() and "自身" in text:
        return str(forge / "nexus")
    if "verix" in text.lower() and "自身" in text:
        return str(forge / "verix")
    return ""


def _render_next_options(payload: dict[str, object]) -> str:
    state = payload.get("current_state") if isinstance(payload.get("current_state"), dict) else {}
    output = payload.get("step_output") if isinstance(payload.get("step_output"), dict) else {}
    lines = [
        "# Nexus Next Options",
        "",
        f"- run_id: `{state.get('run_id', '')}`",
        f"- stage: `{state.get('stage', '')}`",
        f"- completion_quality: `{state.get('completion_quality', '')}`",
        "",
        "## 当前状态",
        str(state.get("summary") or ""),
        "",
        "## 输出链接",
        f"- final_report: `{output.get('report_ref', '')}`",
    ]
    if output.get("research_contract_ref"):
        lines.append(f"- research_contract: `{output.get('research_contract_ref')}`")
    for ref in output.get("branch_report_refs", []) if isinstance(output.get("branch_report_refs"), list) else []:
        if isinstance(ref, str):
            lines.append(f"- branch_report: `{ref}`")
    if output.get("decision_matrix_ref"):
        lines.append(f"- decision_matrix: `{output.get('decision_matrix_ref')}`")
    lines.extend(["", "## 弱点"])
    lines.extend(f"- {item}" for item in state.get("weak_points", []) if isinstance(item, str))
    lines.extend(["", "## 下一步选项"])
    for option in payload.get("next_options", []) if isinstance(payload.get("next_options"), list) else []:
        if not isinstance(option, dict):
            continue
        lines.append(f"- {option.get('label')}: `{normalize_next_prompt(option.get('command', ''))}`")
    return "\n".join(lines).rstrip() + "\n"


def _feishu_setup_output(result: dict[str, object]) -> str:
    checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
    doctor = result.get("doctor") if isinstance(result.get("doctor"), dict) else {}
    doctor_checks = doctor.get("checks") if isinstance(doctor.get("checks"), dict) else {}
    token_success = doctor_checks.get("token_request_success", "not_run")
    expire = doctor_checks.get("token_expire_seconds", 0)
    return (
        f"飞书配置状态：{result.get('status')}；"
        f"project_config={result.get('project_config', 'missing')}；"
        f"app_id loaded: {'yes' if checks.get('app_id_loaded') else 'no'}；"
        f"app_secret loaded: {'yes' if checks.get('app_secret_loaded') else 'no'}；"
        f"folder_token loaded: {'yes' if checks.get('folder_token_loaded') else 'no'}；"
        f"token request success: {token_success}；"
        f"token expire seconds: {expire}。"
    )


def _feishu_setup_next_prompt(project: Path, result: dict[str, object]) -> str:
    if result.get("status") == "completed":
        return f"下一步可输入“使用 $nexus-workflow 进行飞书记录：记录 {project.name} 当前状态”，或运行 python -m nexus.cli feishu doctor --project-path {project}。"
    return f"请按 tool_results/feishu_setup_guide.json 完成飞书开放平台配置；完成后运行 python -m nexus.cli feishu setup --project-path {project} --app-id-path <app_id_file> --app-secret-path <app_secret_file> --folder-token-path <folder_token_file>。"


def _feishu_doctor_output(result: dict[str, object]) -> str:
    checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
    return (
        f"飞书诊断状态：{result.get('status')}；"
        f"app_id loaded: {'yes' if checks.get('app_id_loaded') else 'no'}；"
        f"app_secret loaded: {'yes' if checks.get('app_secret_loaded') else 'no'}；"
        f"folder_token loaded: {'yes' if checks.get('folder_token_loaded') else 'no'}；"
        f"token request success: {checks.get('token_request_success', 'not_run')}；"
        f"token expire seconds: {checks.get('token_expire_seconds', 0)}。"
    )


def _feishu_doctor_next_prompt(project: Path, result: dict[str, object]) -> str:
    if result.get("status") == "completed":
        return f"诊断通过；下一步可运行 python -m nexus.cli feishu record --project-path {project} --title \"Nexus 记录\" --content \"<内容>\"。"
    return f"诊断未通过；先运行 python -m nexus.cli feishu setup --project-path {project} --guide-only 查看配置向导。"


def _feishu_record_output(result: dict[str, object], content: dict[str, object]) -> str:
    raw = result.get("result") if isinstance(result.get("result"), dict) else {}
    document = raw.get("document") if isinstance(raw.get("document"), dict) else {}
    append = raw.get("append") if isinstance(raw.get("append"), dict) else {}
    document_id = document.get("document_id") or append.get("document_id") or ""
    url = document.get("url") or append.get("url") or ""
    return f"飞书记录状态：{result.get('status')}；标题：{content.get('title', '')}；document_id={document_id or 'none'}；url={url or 'none'}。"


def _feishu_record_next_prompt(project: Path, result: dict[str, object]) -> str:
    if result.get("status") == "completed":
        return f"记录完成；下一步可继续输入“使用 $nexus-workflow 进行飞书记录：<新的记录>”，或发布系统架构到飞书。"
    if result.get("reason") == "feishu_target_missing":
        return f"请给项目配置 folder_token_path 或 doc_token_path：python -m nexus.cli feishu setup --project-path {project} --folder-token-path <folder_token_file>。"
    return f"飞书记录未完成；运行 python -m nexus.cli feishu doctor --project-path {project} 查看诊断。"


def _feishu_record_prompt(project: Path, title: str, user_content: str, board: dict[str, object], repo_scan: dict[str, object]) -> str:
    return f"""
你是 nexus 的飞书记录整理节点。请把用户输入整理成适合写入飞书新版云文档的中文 markdown。

要求：
- 不编造 secret/token/path 内容。
- 标题简短清楚。
- markdown 至少包含“当前状态”“本步输出”“下一步提示”三个部分。
- 如果用户只是说记录当前项目状态，结合 board 和 repo_scan 摘要生成真实但克制的记录。

项目路径：{project}
用户标题：{title}
用户输入：{user_content}

项目记录板：
{json.dumps(board, ensure_ascii=False)[:6000]}

repo 扫描摘要：
{json.dumps(repo_scan, ensure_ascii=False)[:6000]}
""".strip()


def _feishu_docs_summary_prompt(candidates: list[CandidateRecord], statuses: list[SourceStatus]) -> str:
    payload = {
        "candidates": [candidate.to_dict() for candidate in candidates[:8]],
        "statuses": [status.to_dict() for status in statuses],
    }
    return f"""
请基于真实检索结果，总结 nexus 飞书配置向导需要遵守的端到端实现要点。

输出必须覆盖：
- 企业自建应用配置步骤；
- app_id/app_secret 获取位置；
- tenant_access_token；
- docx 创建/写入；
- 权限、发布、管理员审核和资源授权风险；
- 来源引用 refs。

真实检索结果：
{json.dumps(payload, ensure_ascii=False)[:18000]}
""".strip()


def _continue_route_prompt(user_request: str, store: RunStore) -> str:
    state = store.read_json("state.json") if store.path("state.json").exists() else {}
    input_payload = store.read_json("input.json") if store.path("input.json").exists() else {}
    next_options = store.read_json("reports/next_options.json") if store.path("reports", "next_options.json").exists() else {}
    report_text = store.path("reports", "final_report.md").read_text(encoding="utf-8")[:10000] if store.path("reports", "final_report.md").exists() else ""
    return (
        load_core_rules()
        + "\n\n请作为 nexus continue intent router，根据用户一句话选择下一节点。"
        + "只能从 rerun_research、local_research、chunked_research、update_intent、implementation_plan、select_existing_wheel、select_subproject_wheels、select_from_scratch_build 中选择 route。"
        + "如果用户说选择已有轮子/基于现成实现，route 必须是 select_existing_wheel。"
        + "如果用户说分块/拆成多个小项目继续调研，route 必须是 select_subproject_wheels 或 chunked_research；若 next_options 中有 branch_subproject_wheels，优先 select_subproject_wheels。"
        + "如果用户说从零搭建方案/没有轮子直接组织搭建，route 必须是 select_from_scratch_build。"
        + "如果用户说生成项目计划/implementation plan/实施计划，route 必须是 implementation_plan；但是否可执行由 implementation_plan gate 决定。"
        + "如果用户说重新查/重新调研/换关键词，route 是 rerun_research。"
        + "如果用户说局部/只看某层/只分析某模块，route 是 local_research。"
        + "如果用户说分块/拆成多个部分，route 是 chunked_research。"
        + "如果用户说更新需求/目标改成/意图更新，route 是 update_intent。"
        + f"\n用户继续输入：{user_request}"
        + f"\n原始输入：{json.dumps(input_payload, ensure_ascii=False)}"
        + f"\n当前 state：{json.dumps(state, ensure_ascii=False)}"
        + f"\nnext_options：{json.dumps(next_options, ensure_ascii=False)}"
        + f"\n报告摘要：{report_text}"
    )


def _continuation_idea(prefix: str, store: RunStore, route: dict[str, object]) -> str:
    report_text = store.path("reports", "final_report.md").read_text(encoding="utf-8")[:6000] if store.path("reports", "final_report.md").exists() else ""
    return (
        f"{prefix}\n"
        f"parent_run_id: {store.run_id}\n"
        f"scope: {route.get('scope', '')}\n"
        f"updated_idea: {route.get('updated_idea', '')}\n"
        "请基于 parent run 的调研结果继续，但本轮仍要真实调用模型节点，并重新产出 artifacts。\n"
        f"parent_report_excerpt:\n{report_text}"
    )


def _chunked_research_prompt(route: dict[str, object], store: RunStore) -> str:
    report_text = store.path("reports", "final_report.md").read_text(encoding="utf-8")[:10000] if store.path("reports", "final_report.md").exists() else ""
    return (
        load_core_rules()
        + "\n\n请把上一份调研报告拆成可继续执行的多个局部调研 chunk。"
        + "每个 chunk 要有稳定 id、中文标签、scope、拆分理由和 pending 状态。"
        + f"\nroute：{json.dumps(route, ensure_ascii=False)}"
        + f"\nparent_run_id：{store.run_id}"
        + f"\n报告：{report_text}"
    )


def _updated_intent_prompt(user_request: str, route: dict[str, object], store: RunStore) -> str:
    input_payload = store.read_json("input.json") if store.path("input.json").exists() else {}
    report_text = store.path("reports", "final_report.md").read_text(encoding="utf-8")[:8000] if store.path("reports", "final_report.md").exists() else ""
    return (
        load_core_rules()
        + "\n\n请根据用户继续输入更新项目需求意图。不要把用户举例收缩为唯一场景；要区分新目标、约束和下一步选项。"
        + f"\n用户继续输入：{user_request}"
        + f"\nroute：{json.dumps(route, ensure_ascii=False)}"
        + f"\n原始 input：{json.dumps(input_payload, ensure_ascii=False)}"
        + f"\n上一份报告：{report_text}"
    )


def _render_chunked_research_plan(plan: dict[str, object], run_id: str) -> str:
    lines = ["# Chunked Research Plan", "", str(plan.get("execution_note") or ""), "", "## Chunks"]
    for chunk in plan.get("chunks", []) if isinstance(plan.get("chunks"), list) else []:
        if isinstance(chunk, dict):
            lines.append(f"- `{chunk.get('id')}` {chunk.get('label')}: {chunk.get('scope')} ({chunk.get('reason')})")
            lines.append(f"  - continue: `python -m nexus.cli continue {run_id} \"局部调研：{chunk.get('id')}\"`")
    return "\n".join(lines).rstrip() + "\n"


def _weak_points_from_statuses(statuses: list[SourceStatus]) -> list[str]:
    weak: list[str] = []
    for status in statuses:
        if status.status in {"blocked", "approval_required", "auth_required", "rate_limited", "failed", "partial", "skipped"}:
            weak.append(f"{status.source}: {status.status}/{status.issue_type} - {status.reason}")
    return weak[:10]


def _safe_node_suffix(value: str) -> str:
    import re

    suffix = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())[:40].strip("_")
    return suffix or "request"


def _provider_setup_summary(provider_status: dict[str, object], *, requested: str = "") -> str:
    profiles = provider_status.get("model_profiles") if isinstance(provider_status, dict) else {}
    if not isinstance(profiles, dict):
        return ""
    needs = []
    if requested and requested in profiles and isinstance(profiles[requested], dict):
        raw = profiles[requested]
        if raw.get("status") == "needs_config":
            needs.append(f"{requested}: {raw.get('reason')}")
    for name, raw in profiles.items():
        if isinstance(raw, dict) and raw.get("status") == "needs_config":
            if any(str(item).startswith(f"{name}:") for item in needs):
                continue
            needs.append(f"{name}: {raw.get('reason')}")
    if not needs:
        return ""
    tail = "；等。" if len(needs) > 4 else "。"
    return " 需要配置：" + "；".join(str(item) for item in needs[:4]) + tail


def _task_prompt(idea: str, repo_scan: dict[str, object]) -> str:
    compact_scan = {
        "project_path": repo_scan.get("project_path"),
        "top_dirs": repo_scan.get("top_dirs"),
        "package_files": repo_scan.get("package_files"),
        "file_sample_count": repo_scan.get("file_sample_count"),
        "skipped_secret_like_count": len(repo_scan.get("skipped_secret_like", []))
        if isinstance(repo_scan.get("skipped_secret_like"), list)
        else 0,
    }
    return f"请基于用户输入生成中文 task block。用户输入：{idea}\n只读 repo scan 摘要：{json.dumps(compact_scan, ensure_ascii=False)}"


def _intent_prompt(idea: str, repo_scan: dict[str, object]) -> str:
    return (
        load_core_rules()
        + "\n\n请作为 nexus Intent Router 思考用户真实意图。"
        + "判断用户是否要完整项目调研、局部调研、多部分调研、外部 GPT prompt、项目初始化、意图更新或实现计划。"
        + "如果用户举了具体例子，请判断它是否只是例子，是否需要泛化成通用工具。"
        + f"\n用户输入：{idea}\nrepo_scan 摘要：{json.dumps({'top_dirs': repo_scan.get('top_dirs'), 'package_files': repo_scan.get('package_files')}, ensure_ascii=False)}"
    )


def _research_prompt(idea: str, task_block: dict[str, object], research_contract: dict[str, object]) -> str:
    return (
        load_core_rules()
        + "\n\n请基于 research_contract 生成调研计划。"
        + "如果 mode=build_decision，调研计划必须覆盖三方向：1. 完整现成轮子；2. 拆成多个子项目继续找轮子；3. 从零搭建方案。"
        + "中文关键词和英文关键词都可以使用，但不要把中文公网可见性/Gitee 可见性当作默认主评分目标，除非 contract/source_policy 要求。"
        + "必须优先覆盖用户显式给出的本地目录、文档、网站、项目和 required_sources_from_user。"
        + f"\n用户输入：{idea}\ntask_block：{json.dumps(task_block, ensure_ascii=False)}"
        + f"\nresearch_contract：{json.dumps(research_contract, ensure_ascii=False)[:18000]}"
    )


def _search_plan_prompt(idea: str, research_plan: dict[str, object], research_contract: dict[str, object], round_no: int, previous_round_summary: str) -> str:
    if bool(research_contract.get("requires_branch_reports")):
        round_focus = {
            1: "完整现成轮子：搜索能整体承载用户意图的已有项目/工具/SaaS/CLI/MCP。",
            2: "子项目轮子：按 subproject_modules 拆分搜索每个能力的现成轮子和黑盒集成点。",
            3: "从零搭建：补齐官方库/API/框架/参考项目和风险证据，形成搭建方案依据。",
        }.get(round_no, "补充上一轮缺口。")
    else:
        round_focus = "按 research_plan 补充覆盖缺口。"
    return (
        load_core_rules()
        + "\n\n请生成本轮显式检索计划。工具层会按 source_plan 执行。"
        + f"本轮 focus：{round_focus}"
        + "如果需要 GitHub/MCP/Gitee/中文 web/官方文档等联网来源，请设置 requires_online=true。"
        + "本地来源 source 可用 local_inventory/local_content/local_skill；在线来源可用 github_repo/gitee_repo/mcp_registry/chinese_web/official_docs/github_skill/openai_skills；"
        + "external_prompt 只能作为自动检索不足后的补充提示，不代表真实检索完成。"
        + f"\nround_no: {round_no}\n用户输入：{idea}\nresearch_plan：{json.dumps(research_plan, ensure_ascii=False)}"
        + f"\nresearch_contract：{json.dumps(research_contract, ensure_ascii=False)[:14000]}"
        + f"\nprevious_round_summary：{previous_round_summary[:6000]}"
    )


def _coverage_prompt(round_no: int, candidates: list[CandidateRecord], statuses: list[SourceStatus], research_contract: dict[str, object]) -> str:
    return (
        load_core_rules()
        + "\n\n请评估本轮检索覆盖度、候选质量和缺口；不要把 blocked/failed source 当作完成。"
        + "如果 research_contract.mode=build_decision，必须按 existing_wheel_build / subproject_wheel_research / from_scratch_build 分别判断覆盖缺口。"
        + f"\nround_no: {round_no}\n候选：{json.dumps([candidate.to_dict() for candidate in candidates[:20]], ensure_ascii=False)}"
        + f"\nsource_status：{json.dumps([status.to_dict() for status in statuses], ensure_ascii=False)}"
        + f"\nresearch_contract：{json.dumps(research_contract, ensure_ascii=False)[:12000]}"
    )


def _stop_prompt(round_no: int, coverage: dict[str, object], candidates: list[CandidateRecord], statuses: list[SourceStatus], research_contract: dict[str, object]) -> str:
    return (
        load_core_rules()
        + "\n\n请判断是否继续下一轮检索。必须考虑覆盖缺口、候选数量、source blocked/failed 和 max_rounds。"
        + "如果是 build_decision 调研，前三轮分别承担完整轮子、子项目轮子、从零搭建依据；不能在三方向证据明显缺失时提前宣称完成。"
        + f"\nround_no: {round_no}\ncoverage：{json.dumps(coverage, ensure_ascii=False)}"
        + f"\n候选数量：{len(candidates)}\nsource_status：{json.dumps([status.to_dict() for status in statuses], ensure_ascii=False)}"
        + f"\nresearch_contract：{json.dumps(research_contract, ensure_ascii=False)[:10000]}"
    )


def _localization_prompt(idea: str, candidates: list[dict[str, object]], statuses: list[SourceStatus]) -> str:
    return (
        load_core_rules()
        + "\n\n请基于候选来源、URL 可达性和 source_status，判断每个候选在中文互联网环境下的可用性。"
        + "不要把 external_prompt 当成真实检索结果；blocked/failed/auth_required/rate_limited 要明确写入风险。"
        + f"\n用户输入：{idea}\n候选：{json.dumps(candidates, ensure_ascii=False)}"
        + f"\nsource_status：{json.dumps([status.to_dict() for status in statuses], ensure_ascii=False)}"
    )


def _review_prompt(idea: str, candidates: list[dict[str, object]], statuses: list[SourceStatus], research_contract: dict[str, object]) -> str:
    return (
        load_core_rules()
        + "\n\n请评价候选对用户真实意图的复用价值。主评分维度是：意图覆盖、集成方式、成熟度、许可证/合规、维护风险、输入输出契约和端到端可验收性。"
        "中文互联网可用性、Gitee 和中文文档只能作为辅助信号，除非 research_contract/source_policy 明确要求公开中文平台复用。"
        "如果候选来自 external_prompt，只能作为补充调研提示，不能作为已验证候选。"
        f"\n用户输入：{idea}\n候选：{json.dumps(candidates, ensure_ascii=False)}"
        f"\nsource_status：{json.dumps([status.to_dict() for status in statuses], ensure_ascii=False)}"
        f"\nresearch_contract：{json.dumps(research_contract, ensure_ascii=False)[:14000]}"
    )


def _risk_prompt(idea: str, ranked: list[dict[str, object]], research_contract: dict[str, object]) -> str:
    return (
        "请分析继续采用这些候选的风险，默认只读，写代码前必须审批。"
        "如果是 build_decision 调研，请分别指出已有轮子、子项目继续调研、从零搭建的风险。"
        f"用户输入：{idea}\n候选排行：{json.dumps(ranked, ensure_ascii=False)}"
        f"\nresearch_contract：{json.dumps(research_contract, ensure_ascii=False)[:12000]}"
    )


def _report_prompt(idea: str, ranked: list[dict[str, object]], risk: dict[str, object], research_contract: dict[str, object]) -> str:
    return (
        load_core_rules()
        + "\n\n请生成中文最终报告，包含结论、发现和下一步计划。"
        + "如果 research_contract.mode=build_decision，报告必须说明三方向都会由工具层落盘为 branch reports，并且未选择方向前不得进入 implementation_plan。"
        + "不要把项目治理/README/Gitee 可见性误当成业务工具搭建结论。"
        + f"\n用户输入：{idea}\n候选排行：{json.dumps(ranked, ensure_ascii=False)}"
        + f"\n风险：{json.dumps(risk, ensure_ascii=False)}"
        + f"\nresearch_contract：{json.dumps(research_contract, ensure_ascii=False)[:14000]}"
    )


def _implementation_prompt(selected: dict[str, object], report_text: str, branch_report_text: str, decision_matrix_text: str) -> str:
    return (
        load_core_rules()
        + "\n\n请基于 discovery 报告和已选择的 research branch 生成代码实施计划，但不要执行代码修改。"
        + "如果 selected_research_branch 为空或 branch_report 与 final_report 冲突，必须在 risks 中说明，不要假装已经选择方向。"
        + f"\nselected_research_branch：{json.dumps(selected, ensure_ascii=False)}"
        + "\n\nDiscovery 报告：\n"
        + report_text[:10000]
        + "\n\nSelected Branch Report：\n"
        + branch_report_text[:8000]
        + "\n\nDecision Matrix：\n"
        + decision_matrix_text[:6000]
    )


def _validate_project_name_candidates(payload: dict[str, Any]) -> dict[str, Any]:
    recommended = str(payload.get("recommended") or "").strip().lower()
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    names: set[str] = set()
    failures: list[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            failures.append("candidate_not_object")
            continue
        name = str(item.get("name") or "").strip().lower()
        item["name"] = name
        names.add(name)
        if not re.fullmatch(r"[a-z]{5}", name):
            failures.append(f"{name or '<empty>'}: not_lowercase_five_letters")
        elif name not in REAL_FIVE_LETTER_PROJECT_WORDS:
            failures.append(f"{name}: not_in_real_five_letter_project_word_list")
        if not str(item.get("functional_link") or "").strip():
            failures.append(f"{name}: missing_functional_link")
        if not str(item.get("metaphor") or "").strip():
            failures.append(f"{name}: missing_metaphor")
        if not _has_real_project_word_validation(str(item.get("word_validation") or "")):
            failures.append(f"{name}: missing_real_word_validation")
    if recommended not in names:
        failures.append(f"{recommended or '<empty>'}: recommended_not_in_candidates")
    if recommended not in REAL_FIVE_LETTER_PROJECT_WORDS:
        failures.append(f"{recommended or '<empty>'}: recommended_not_real_five_letter_word")
    if failures:
        raise ValueError("project_name_candidates_invalid: " + "; ".join(failures))
    payload["recommended"] = recommended
    return payload


def _select_non_colliding_project_name(candidates_payload: dict[str, Any], model_recommended: str, parent: Path) -> tuple[str, list[str]]:
    candidates = candidates_payload.get("candidates") if isinstance(candidates_payload.get("candidates"), list) else []
    ordered: list[str] = []

    def add_name(value: object) -> None:
        name = str(value or "").strip().lower()
        if name and name not in ordered:
            ordered.append(name)

    add_name(model_recommended)
    for item in candidates:
        if isinstance(item, dict):
            add_name(item.get("name"))

    collisions: list[str] = []
    parent_name = parent.name.strip().lower()
    for name in ordered:
        if name == parent_name or (parent / name).exists():
            collisions.append(name)
            continue
        return name, collisions
    return "", collisions


def _has_real_project_word_validation(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    negative_markers = [
        "不是真实",
        "不是一个真实",
        "非真实",
        "伪造",
        "是造词",
        "属于造词",
        "人工造词",
        "拼组词",
        "not real",
        "not a real",
        "invented",
        "coined",
        "pseudo",
    ]
    if any(marker in normalized for marker in negative_markers):
        return False
    positive_markers = [
        "真实",
        "real",
        "合法",
        "标准",
        "收录",
        "认证",
        "通用",
        "基础英语",
        "基础五字母",
        "英文单词",
        "词典",
        "规范",
        "dictionary",
        "oxford",
        "cambridge",
        "collins",
        "merriam",
    ]
    return any(marker in normalized for marker in positive_markers)


def _read_provider_attempts(store: RunStore) -> dict[str, Any]:
    payload = _read_json_if_exists(store.path("tool_results", "provider_attempts.json"))
    attempts = payload.get("attempts") if isinstance(payload.get("attempts"), list) else []
    selected = payload.get("selected_by_intensity") if isinstance(payload.get("selected_by_intensity"), dict) else {}
    return {"schema": "nexus.provider_attempts.v1", "attempts": attempts, "selected_by_intensity": selected}


def _append_provider_attempt(store: RunStore, payload: dict[str, Any], *, provider: str, status: str, reason: str, stage: str, intensity: str, issue_type: str = "") -> None:
    attempts = payload.get("attempts") if isinstance(payload.get("attempts"), list) else []
    attempts.append(
        {
            "provider": provider,
            "status": status,
            "reason": reason,
            "stage": stage,
            "intensity": intensity,
            "issue_type": issue_type or _provider_issue_type(reason),
        }
    )
    payload["attempts"] = attempts
    payload.setdefault("selected_by_intensity", {})
    store.write_json("tool_results/provider_attempts.json", payload)


def _record_preflight_attempt(provider: HostModelProvider, status: ProviderStatus, attempts: list[dict[str, object]] | None, *, intensity: str) -> None:
    if attempts is None:
        return
    item = {
        "provider": provider.name,
        "status": status.status,
        "reason": status.reason,
        "stage": "preflight",
        "intensity": intensity,
        "issue_type": "" if status.status == "available" else _provider_issue_type(status.reason),
    }
    details = getattr(provider, "last_smoke_details", None)
    if isinstance(details, dict):
        item["auto_repair"] = details
    attempts.append(item)


def _write_provider_recovery(store: RunStore, project: Path, attempts_payload: dict[str, Any], *, failed_provider: str, intensity: str, exhausted: bool = False) -> None:
    attempts = attempts_payload.get("attempts") if isinstance(attempts_payload.get("attempts"), list) else []
    context = build_recovery_context(
        store=store,
        project=project,
        module="provider",
        node="provider_preflight",
        status="blocked",
        reason="provider_preflight_failed",
        result={"failed_provider": failed_provider, "intensity": intensity, "attempts": attempts},
    )
    guidance = {
        "schema": "nexus.failure_recovery_guidance.v1",
        "summary": f"{failed_provider} preflight 失败，需要用户选择修复当前 provider 或跳过它继续 fallback。",
        "probable_root_cause": _provider_issue_type(str((attempts[-1] if attempts else {}).get("reason") or "")) or "provider_preflight_failed",
        "safe_next_attempts": [f"重试 {failed_provider} preflight", f"显式跳过 {failed_provider} 并继续 fallback"],
        "manual_user_actions": [],
        "stop_conditions": ["没有可用 fallback provider 时停止并要求配置真实模型。"],
        "recommended_actions": [
            {"action_id": "retry_current_provider", "rationale": f"修复 {failed_provider} 后重试原 run。", "requires_escalation": True},
            {"action_id": "switch_to_next_real_provider", "rationale": f"用户批准后跳过 {failed_provider} 并继续下一个真实 provider。", "requires_escalation": False},
        ],
    }
    result = build_recovery_result(context, guidance, status="blocked", attempts=attempts)
    payload = {**result, "actions_applied": ["retry_same_provider_preflight"] if exhausted else [], "actions_recommended": ["switch_to_next_real_provider"], "requires_user_approval": True, "exhausted": exhausted}
    store.write_json("tool_results/provider_recovery.json", payload)
    store.write_json("tool_results/recovery_result.json", payload)


def _provider_recovery_exhausted(attempts: list[dict[str, object]]) -> bool:
    del attempts
    return False


def _has_fallback_provider(root: Path, project: Path, failed_provider: str, *, intensity: str) -> bool:
    for provider in iter_real_provider_candidates(root, cwd=project, intensity=intensity):
        if provider.name == failed_provider:
            continue
        status = provider.status()
        if isinstance(status, ProviderStatus) and status.status == "available":
            return True
    return False


def _provider_preflight_pending_actions(run_id: str, provider: str) -> list[dict[str, object]]:
    return [
        {
            "action_id": "retry_current_provider",
            "kind": "provider_retry",
            "provider": provider,
            "requires_host_permission": True,
            "command": f"python -m nexus.cli recover {run_id} \"修复 {provider} preflight 权限问题并继续\"",
        },
        {
            "action_id": "skip_current_provider_and_continue",
            "kind": "provider_fallback_approval",
            "skip_provider": provider,
            "requires_host_permission": False,
            "command": f"python -m nexus.cli recover {run_id} \"跳过 {provider} fallback 继续\"",
        },
    ]


def _provider_issue_type(reason: str) -> str:
    lowered = reason.lower()
    if "readonly" in lowered or "readonly database" in lowered:
        return "codex_state_db_readonly"
    if "permission" in lowered or "operation not permitted" in lowered:
        return "codex_state_db_permission_denied"
    if "usage limit" in lowered or "quota" in lowered:
        return "usage_limit"
    if "401" in lowered or "unauthorized" in lowered or "auth" in lowered:
        return "auth_failed"
    if "model_not_found" in lowered or "404" in lowered:
        return "model_not_found"
    if "timeout" in lowered:
        return "timeout"
    return "provider_failure" if reason else ""


def _skip_provider_from_recovery_request(request: str) -> str:
    match = re.search(r"跳过\s+([A-Za-z0-9_.:-]+)\s+fallback", request or "", flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _target_provider_from_recovery_request(request: str) -> str:
    match = re.search(r"fallback\s+到\s+([A-Za-z0-9_.:-]+)\s+并继续", request or "", flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _run_id_from_text(text: str) -> str:
    match = re.search(r"run-[0-9TZ-]+-[a-f0-9]+", text or "")
    return match.group(0) if match else ""


def _env_skip_providers() -> list[str]:
    return [item for item in (os.environ.get("NEXUS_AUTO_SKIP_PROVIDERS", "") or "").split(",") if item]


def _merged_skip_providers(provider: str, previous: list[str] | None = None) -> str:
    existing = [item for item in (os.environ.get("NEXUS_AUTO_SKIP_PROVIDERS", "") or "").split(",") if item]
    for item in previous or []:
        if item not in existing:
            existing.append(item)
    if provider not in existing:
        existing.append(provider)
    return ",".join(existing)


def _fallback_chain_skipped_providers(store: RunStore) -> list[str]:
    payload = _read_json_if_exists(store.path("tool_results", "provider_fallback_chain.json"))
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    skipped: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        provider = str(event.get("skip_provider") or "").strip()
        if provider and provider not in skipped:
            skipped.append(provider)
    return skipped


def _append_provider_fallback_chain(store: RunStore, event: dict[str, object]) -> Path:
    payload = _read_json_if_exists(store.path("tool_results", "provider_fallback_chain.json"))
    if payload.get("schema") != "nexus.provider_fallback_chain.v1":
        payload = {"schema": "nexus.provider_fallback_chain.v1", "run_id": store.run_id, "events": []}
    events = payload.get("events")
    if not isinstance(events, list):
        events = []
        payload["events"] = events
    normalized = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    events.append(normalized)
    payload["run_id"] = store.run_id
    payload["updated_at"] = normalized["timestamp"]
    payload["skipped_providers"] = _fallback_chain_skipped_providers_from_events(events)
    return store.write_json("tool_results/provider_fallback_chain.json", payload)


def _fallback_chain_skipped_providers_from_events(events: list[object]) -> list[str]:
    skipped: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        provider = str(event.get("skip_provider") or "").strip()
        if provider and provider not in skipped:
            skipped.append(provider)
    return skipped


def _next_fallback_provider_name(root: Path, project: Path, current: str, *, intensity: str) -> str:
    skipped = set(filter(None, (os.environ.get("NEXUS_AUTO_SKIP_PROVIDERS", "") or "").split(",")))
    skipped.add(current)
    for provider in iter_real_provider_candidates(root, cwd=project, intensity=intensity):
        if provider.name in skipped:
            continue
        status = provider.status()
        if isinstance(status, ProviderStatus) and status.status == "available":
            return provider.name
    return ""


def _write_wait_observation(store: RunStore, *, node_id: str, decision: str, wait_seconds: int, runtime_status_path: Path | None = None) -> Path:
    return store.write_json(
        f"tool_results/wait_observation_{_safe_node_suffix(node_id)}.json",
        {
            "schema": "nexus.wait_observation.v1",
            "node_id": node_id,
            "decision": decision,
            "wait_policy_seconds": wait_seconds,
            "artifact_state_first": True,
            "terminal_idle_is_not_failure": True,
            "external_login_wait_seconds": 600,
            "github_push_wait_seconds": 180,
            "feishu_import_wait_seconds": 600,
            "runtime_status_path": str(runtime_status_path) if runtime_status_path is not None else "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )


RECOVERY_PROJECT_PATH_KEYS = {
    "project_path",
    "project_root",
    "target_path",
    "target_project",
    "target_project_path",
    "final_project_path",
    "resolved_project_path",
    "created_path",
    "playbook_path",
    "records_path",
    "config_path",
    "path",
}


def _write_recovery_playbook_approval_if_completed(store: RunStore, input_payload: dict[str, object]) -> Path | None:
    result = _read_json_if_exists(store.path("tool_results/recovery_result.json"))
    if not result:
        return
    if str(result.get("status") or "") not in {"completed", "done"}:
        return
    project, project_source = _resolve_recovery_project_path(store, input_payload=input_payload, result=result)
    if project is None:
        store.write_json(
            "tool_results/recovery_playbook_pending_project.json",
            {
                "schema": "nexus.recovery_playbook_pending_project.v1",
                "status": "blocked",
                "project_path": "",
                "project_path_source": project_source,
                "reason": "project_path_unresolved",
                "recovery_result_path": str(store.path("tool_results", "recovery_result.json")),
            },
        )
        return
    if not project.exists() or not project.is_dir():
        store.write_json(
            "tool_results/recovery_playbook_pending_project.json",
            {
                "schema": "nexus.recovery_playbook_pending_project.v1",
                "status": "blocked",
                "project_path": str(project),
                "project_path_source": project_source,
                "reason": "project_path_not_ready",
                "recovery_result_path": str(store.path("tool_results", "recovery_result.json")),
            },
        )
        return
    approval = build_playbook_write_approval(store, project, result)
    approval["project_path_source"] = project_source
    return store.write_json("approvals/recovery_playbook_write_required.json", approval)


def _record_successful_recovery_outcome(
    store: RunStore,
    interaction: dict[str, object],
    *,
    recovered_by: str,
    action_applied: str,
) -> None:
    if str(interaction.get("previous_task_status") or "") not in {"completed", "done"}:
        return
    result = _read_json_if_exists(store.path("tool_results/recovery_result.json"))
    if not result:
        return
    input_payload = _read_json_if_exists(store.path("input.json"))
    project, project_source = _resolve_recovery_project_path(store, input_payload=input_payload, result=result, interaction=interaction)
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    context = dict(context)
    context["final_status"] = str(interaction.get("previous_task_status") or "")
    if project is not None:
        context["final_project_path"] = str(project)
        context["project_path_source"] = project_source
    actions = [str(item) for item in (result.get("actions_applied") if isinstance(result.get("actions_applied"), list) else []) if str(item)]
    if action_applied and action_applied not in actions:
        actions.append(action_applied)
    result.update(
        {
            "status": "completed",
            "final_status": str(interaction.get("previous_task_status") or ""),
            "final_project_path": str(project) if project is not None else "",
            "project_path_source": project_source,
            "recovered_by": recovered_by,
            "action_applied": action_applied,
            "actions_applied": actions,
            "context": context,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    store.write_json("tool_results/recovery_result.json", result)
    if store.path("tool_results/provider_recovery.json").exists():
        provider_payload = _read_json_if_exists(store.path("tool_results/provider_recovery.json"))
        if provider_payload:
            provider_payload.update({key: result[key] for key in ["status", "final_status", "final_project_path", "project_path_source", "recovered_by", "action_applied", "actions_applied", "updated_at"]})
            provider_payload["context"] = context
            store.write_json("tool_results/provider_recovery.json", provider_payload)


def _resolve_recovery_project_path(
    store: RunStore,
    *,
    input_payload: dict[str, object] | None = None,
    result: dict[str, object] | None = None,
    interaction: dict[str, object] | None = None,
) -> tuple[Path | None, str]:
    input_payload = input_payload or {}
    result = result or {}
    interaction = interaction or _read_json_if_exists(store.path("interaction.json"))
    state = _read_json_if_exists(store.path("state.json"))
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    recovery_context = _read_json_if_exists(store.path("tool_results/recovery_context.json"))
    candidates: list[tuple[str, object]] = [
        ("approved_project_root.created_path", _read_json_if_exists(store.path("approvals/APPROVED_project-root.json")).get("created_path")),
        ("project_root_approval.target_path", _read_json_if_exists(store.path("approvals/project_root_required.json")).get("target_path")),
        ("input.project_path", input_payload.get("project_path")),
        ("state.target_path", state.get("target_path")),
        ("state.final_project_path", state.get("final_project_path")),
        ("recovery_result.final_project_path", result.get("final_project_path")),
        ("recovery_context.final_project_path", context.get("final_project_path")),
        ("interaction.final_project_path", interaction.get("final_project_path") if isinstance(interaction, dict) else ""),
        ("state.project_path", state.get("project_path")),
        ("recovery_context.project_path", context.get("project_path")),
        ("stored_recovery_context.project_path", recovery_context.get("project_path")),
    ]
    approval = interaction.get("approval_request") if isinstance(interaction.get("approval_request"), dict) else {}
    candidates.append(("interaction.approval_request.target_path", approval.get("target_path")))
    for source, value in _iter_run_project_path_values(store, result=result, interaction=interaction):
        candidates.append((source, value))

    seen: set[str] = set()
    fallback: tuple[Path, str] | None = None
    for source, value in candidates:
        for candidate in _path_candidates_from_value(value):
            resolved = candidate.expanduser().resolve()
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            if _looks_like_recovery_project(resolved, store.root):
                return resolved, source
            if fallback is None and source == "input.project_path" and resolved.exists() and resolved.is_dir():
                fallback = (resolved, source)
    if fallback is not None:
        return fallback
    return None, "unresolved"


def _iter_run_project_path_values(
    store: RunStore,
    *,
    result: dict[str, object],
    interaction: dict[str, object],
) -> list[tuple[str, object]]:
    values: list[tuple[str, object]] = []
    for ref in _artifact_refs_from_payload(result) + _artifact_refs_from_payload(interaction):
        values.append(("artifact_ref", ref))
    for path in sorted(store.path("tool_results").glob("*.json")) if store.path("tool_results").exists() else []:
        payload = _read_json_if_exists(path)
        for key, value in _deep_path_items(payload):
            if key in RECOVERY_PROJECT_PATH_KEYS or key.endswith("_path") or key.endswith("_root"):
                values.append((f"tool_results/{path.name}:{key}", value))
        for ref in _artifact_refs_from_payload(payload):
            values.append((f"tool_results/{path.name}:artifact_ref", ref))
    return values


def _deep_path_items(value: object) -> list[tuple[str, object]]:
    items: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str):
                items.append((key, nested))
            items.extend(_deep_path_items(nested))
    elif isinstance(value, list):
        for nested in value:
            items.extend(_deep_path_items(nested))
    return items


def _artifact_refs_from_payload(payload: dict[str, object]) -> list[str]:
    refs = payload.get("artifact_refs") if isinstance(payload, dict) else []
    if not isinstance(refs, list):
        return []
    return [str(item) for item in refs if str(item)]


def _path_candidates_from_value(value: object) -> list[Path]:
    text = str(value or "").strip()
    if not text:
        return []
    path = Path(text)
    candidates = [path]
    parts = path.parts
    if ".nexus" in parts:
        candidates.append(Path(*parts[: parts.index(".nexus")]))
    if "docs" in parts:
        docs_index = parts.index("docs")
        if docs_index > 0:
            candidates.append(Path(*parts[:docs_index]))
    return candidates


def _looks_like_recovery_project(path: Path, nexus_root: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    if path == nexus_root.expanduser().resolve():
        return False
    markers = [
        path / ".nexus" / "project-intent.json",
        path / ".nexus" / "board.md",
        path / ".nexus" / "recovery-playbook.json",
        path / "docs" / "intent" / "original-requirement.md",
        path / "docs" / "intent" / "normalized-requirement.md",
        path / "docs" / "project-overview.md",
        path / "docs" / "recovery-records.md",
    ]
    return any(marker.exists() for marker in markers)


def _write_debug_recovery_result_if_possible(
    store: RunStore,
    handoff: dict[str, object],
    debug_summary: dict[str, object],
    result: dict[str, object],
    *,
    rebind_path: Path,
) -> dict[str, object]:
    input_payload = _read_json_if_exists(store.path("input.json"))
    project = _debug_recovery_project_path(store, input_payload, result)
    project_source = "resolved_project"
    if project is None:
        project_source = "input.parent_or_runner_root"
        project = Path(str(input_payload.get("parent") or input_payload.get("project_path") or store.root)).expanduser().resolve()

    source_state = handoff.get("source_state") if isinstance(handoff.get("source_state"), dict) else {}
    source_interaction = handoff.get("source_interaction") if isinstance(handoff.get("source_interaction"), dict) else {}
    source_node = str(source_state.get("current_node") or handoff.get("source_node") or "debug_handoff")
    reason = str(source_state.get("blocked_reason") or source_interaction.get("blocked_reason") or handoff.get("reason") or "debug_handoff")
    entries = [item for item in (debug_summary.get("entries") if isinstance(debug_summary.get("entries"), list) else []) if isinstance(item, dict)]
    artifact_refs = [
        str(store.path("worklogs", "debug_worklog.json")),
        str(store.path("worklogs", "debug_summary.json")),
        str(rebind_path),
    ]
    context = build_recovery_context(
        store=store,
        project=project,
        module=_debug_recovery_module(input_payload),
        node=source_node,
        status=str(source_state.get("status") or "blocked"),
        reason=reason,
        result={
            "schema": "nexus.debug_rebind_observed_result.v1",
            "handoff_id": str(handoff.get("handoff_id") or ""),
            "rebind_status": str(result.get("previous_task_status") or ""),
            "rebind_blocked_reason": str(result.get("blocked_reason") or ""),
            "rebind_output": str(result.get("previous_task_output") or ""),
        },
        artifact_refs=artifact_refs,
        retry_prompt=f"python -m nexus.cli handoff-for-debug {store.run_id} \"<debug 原因>\"",
    )
    guidance = _debug_recovery_guidance(reason=reason, entries=entries)
    attempts = _debug_recovery_attempts(entries)
    recovery_result = build_recovery_result(
        context,
        guidance,
        status="completed",
        attempts=attempts,
        recovered_by="debug_rebind",
    )
    paths = write_recovery_artifacts(store, recovery_result)
    debug_result_path = store.write_json("tool_results/debug_recovery_result.json", recovery_result)
    approval_path = _write_recovery_playbook_approval_if_completed(store, {"project_path": str(project) if project_source == "resolved_project" else ""})
    refs = [str(item) for item in paths.values()]
    refs.append(str(debug_result_path))
    if approval_path is not None:
        refs.append(str(approval_path))
    elif project_source != "resolved_project":
        pending_path = store.write_json(
            "tool_results/recovery_playbook_pending_project.json",
            {
                "schema": "nexus.recovery_playbook_pending_project.v1",
                "status": "blocked",
                "project_path": str(project),
                "project_path_source": project_source,
                "reason": "project_path_not_ready",
                "recovery_result_path": str(store.path("tool_results", "recovery_result.json")),
            },
        )
        refs.append(str(pending_path))
    return {
        "status": "completed",
        "project_path": str(project),
        "approval_path": str(approval_path) if approval_path is not None else "",
        "artifact_refs": refs,
    }


def _debug_recovery_project_path(store: RunStore, input_payload: dict[str, object], result: dict[str, object]) -> Path | None:
    project, _source = _resolve_recovery_project_path(store, input_payload=input_payload, result=result)
    return project


def _debug_recovery_module(input_payload: dict[str, object]) -> str:
    schema = str(input_payload.get("schema") or "")
    if schema == "nexus.project_init_input.v1":
        return "init_project"
    if schema == "nexus.input.v1":
        return "workflow"
    if schema:
        return schema.removeprefix("nexus.").removesuffix("_input.v1")
    return "debug_rebind"


def _debug_recovery_guidance(*, reason: str, entries: list[dict[str, object]]) -> dict[str, object]:
    diagnose = [str(item.get("summary") or "") for item in entries if str(item.get("kind") or "") == "diagnose" and str(item.get("summary") or "")]
    edits = [str(item.get("summary") or "") for item in entries if str(item.get("kind") or "") == "edit" and str(item.get("summary") or "")]
    tests = [str(item.get("summary") or "") for item in entries if str(item.get("kind") or "") == "test" and str(item.get("summary") or "")]
    summary = diagnose[0] if diagnose else f"debug rebind recovered from {reason}"
    return {
        "schema": "nexus.failure_recovery_guidance.v1",
        "summary": summary,
        "probable_root_cause": summary,
        "safe_next_attempts": edits[:5],
        "manual_user_actions": [],
        "stop_conditions": ["如果 rebind 后原 workflow 没有前进，保留 debug handoff 并继续追加 diagnose/edit/test worklog。"],
        "recommended_actions": [
            {
                "action_id": "use_debug_handoff_and_rebind",
                "kind": "debug_rebind",
                "rationale": "当普通 recover 无法安全继续且需要本地修复时，先登记 debug handoff，记录 diagnose/edit/test 证据，再通过 rebind-and-continue 回到原 run。",
                "requires_approval": False,
                "requires_escalation": False,
                "command": "python -m nexus.cli handoff-for-debug <run_id> \"<debug 原因>\" && python -m nexus.cli rebind-and-continue <run_id> --handoff-id <handoff_id>",
            }
        ],
        "debug_test_evidence": tests[:5],
    }


def _debug_recovery_attempts(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    attempts: list[dict[str, object]] = []
    for index, item in enumerate(entries, start=1):
        kind = str(item.get("kind") or "debug")
        attempts.append(
            {
                "schema": "nexus.recovery_attempt.v1",
                "action_id": f"debug_{kind}_{index}",
                "kind": kind,
                "status": "completed",
                "summary": str(item.get("summary") or ""),
                "command": str(item.get("command") or ""),
                "paths": item.get("paths") if isinstance(item.get("paths"), list) else [],
                "created_at": str(item.get("created_at") or ""),
            }
        )
    return attempts


def _with_recovery_playbook_prompt(store: RunStore, interaction: dict[str, object], memory: dict[str, object]) -> dict[str, object]:
    approval_path = str(memory.get("approval_path") or "")
    if not approval_path or str(interaction.get("previous_task_status") or "") != "completed":
        return interaction
    updated = dict(interaction)
    refs = [str(item) for item in (updated.get("artifact_refs") if isinstance(updated.get("artifact_refs"), list) else []) if str(item)]
    for ref in memory.get("artifact_refs") if isinstance(memory.get("artifact_refs"), list) else []:
        text = str(ref)
        if text and text not in refs:
            refs.append(text)
    updated["artifact_refs"] = refs
    updated["next_task_prompt"] = f"$nexus-workflow 审批 {store.run_id} 的 recovery-playbook"
    store.write_json("interaction.json", updated)
    return updated


def _project_name_context_from_approval(approval: dict[str, object], target: Path) -> dict[str, object]:
    recommended = str(approval.get("recommended") or target.name).strip().lower()
    model_recommended = str(approval.get("model_recommended") or recommended).strip().lower()
    explicit_name = str(approval.get("explicit_user_name") or "").strip().lower()
    name_source = str(approval.get("name_source") or ("user_raw" if explicit_name else "model_recommended")).strip()
    selected = target.name
    candidates = approval.get("candidates") if isinstance(approval.get("candidates"), list) else []
    candidate = next((item for item in candidates if isinstance(item, dict) and str(item.get("name") or "").strip().lower() == recommended), {})
    if not candidate:
        candidate = next((item for item in candidates if isinstance(item, dict) and str(item.get("name") or "").strip().lower() == model_recommended), {})
    word_validation = str(candidate.get("word_validation") or "")
    if explicit_name and not word_validation:
        word_validation = "用户显式指定的项目 slug，已通过路径安全校验；不适用真实五字母英文词规则。"
    return {
        "source": "explicit_user_name" if explicit_name else "model_recommended",
        "name_source": name_source,
        "selected_name": selected,
        "model_recommended": model_recommended,
        "meaning": str(candidate.get("meaning") or ""),
        "memory_hook": str(candidate.get("memory_hook") or ""),
        "rationale": str(candidate.get("rationale") or ""),
        "functional_link": str(candidate.get("functional_link") or ""),
        "metaphor": str(candidate.get("metaphor") or ""),
        "word_validation": word_validation,
        "candidate_count": len([item for item in candidates if isinstance(item, dict)]),
    }


def _explicit_project_name_from_idea(idea: str) -> str:
    text = idea or ""
    patterns = [
        r"名为\s*([^\s，。；,;]+)",
        r"初始化项目\s+([A-Za-z][A-Za-z0-9_-]{1,40})\s*[：:]",
        r"(?:项目名|项目名称|目录名|名称|命名)\s*(?:固定为|指定为|为|=|:|：)\s*([^\s，。；,;]+)",
        r"^\s*([A-Za-z][A-Za-z0-9_-]{1,40})\s*[：:]",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _validate_explicit_project_name(match.group(1))
    return ""


def _validate_explicit_project_name(name: str) -> str:
    normalized = (name or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,40}", normalized):
        raise ValueError(f"explicit_project_name_invalid: {name}: expected safe slug matching [a-z][a-z0-9_-]{{1,40}}")
    if normalized in {".", ".."}:
        raise ValueError(f"explicit_project_name_invalid: {name}: reserved_path_segment")
    return normalized


def _project_name_prompt(idea: str, parent: Path) -> str:
    allowed_words = ", ".join(sorted(REAL_FIVE_LETTER_PROJECT_WORDS))
    return (
        load_core_rules()
        + "\n\n请为新项目生成 3-5 个五字母英文目录名候选。"
        + "硬性规则：名称必须是真实英文五字母单词，不能是首字母拼组、伪词、任意缩写、删字母造词或简单单词拼接。"
        + f"只能从 Nexus 本地允许词表中选择 name 和 recommended：{allowed_words}。"
        + "每个候选必须和功能抽象显式相关，或用形象隐喻代表需求/功能，例如 nexus 表示纽带连接 idea 与信息，forge 表示车间沉淀小项目。"
        + "必须解释 meaning、memory_hook、rationale、functional_link、metaphor，并在 word_validation 中说明它是真实英文五字母单词。"
        + f"\n父目录：{parent}\n项目 idea：{idea}"
    )


def _conversation_prompt(text: str, selector: str = "") -> str:
    return (
        load_core_rules()
        + "\n\n请从导出的项目对话历史中提取可复用 workflow/skill 方法论。"
        + "不要把具体例子收缩成唯一场景，要泛化成可处理一类项目的流程。"
        + "必须判断应该生成 skill、workflow、纳入已有工具还是只生成 prompt pack，并在 draft 中给出可安装的 SKILL.md 内容。"
        + f"\n选择范围/selector：{selector or 'all'}"
        + "\n对话历史：\n"
        + text
    )


def _render_implementation_plan(plan: dict[str, object]) -> str:
    lines = ["# Implementation Plan", "", "## Summary", str(plan.get("summary") or ""), "", "## Phases"]
    lines.extend(f"- {item}" for item in plan.get("phases", []) if isinstance(item, str))
    lines.extend(["", "## Files To Touch"])
    lines.extend(f"- {item}" for item in plan.get("files_to_touch", []) if isinstance(item, str))
    lines.extend(["", "## Tests"])
    lines.extend(f"- {item}" for item in plan.get("tests", []) if isinstance(item, str))
    lines.extend(["", "## Risks"])
    lines.extend(f"- {item}" for item in plan.get("risks", []) if isinstance(item, str))
    return "\n".join(lines).rstrip() + "\n"


def _render_conversation_workflow(result: dict[str, object]) -> str:
    lines = [
        "# Generalized Workflow From Conversation",
        "",
        "## Source Summary",
        str(result.get("source_summary") or ""),
        "",
        "## Generalized Project Type",
        str(result.get("generalized_project_type") or ""),
        "",
        "## Workflow Blueprint",
    ]
    lines.extend(f"- {item}" for item in result.get("workflow_blueprint", []) if isinstance(item, str))
    lines.extend(["", "## Draft", str(result.get("skill_or_workflow_draft") or ""), "", "## Safety Notes"])
    lines.extend(f"- {item}" for item in result.get("safety_notes", []) if isinstance(item, str))
    return "\n".join(lines).rstrip() + "\n"


def _multi_option_prompt(options: list[str]) -> str:
    return "\n".join(options)


def _rank_candidates(candidates: list[dict[str, object]], review: dict[str, object]) -> list[dict[str, object]]:
    by_id = {str(item.get("candidate_id")): item for item in review.get("reviews", []) if isinstance(item, dict)}
    ranked: list[dict[str, object]] = []
    for candidate in candidates:
        item = dict(candidate)
        judgment = by_id.get(str(candidate.get("id")), {})
        item["score"] = float(judgment.get("score") or 0.0)
        item["reason"] = str(judgment.get("reason") or "")
        item["risks"] = judgment.get("risks") if isinstance(judgment.get("risks"), list) else []
        item["recommended_use"] = str(judgment.get("recommended_use") or "unknown")
        ranked.append(item)
    return sorted(ranked, key=lambda item: float(item.get("score") or 0.0), reverse=True)


def _dedupe_candidate_records(candidates: list[CandidateRecord]) -> list[CandidateRecord]:
    seen: dict[str, CandidateRecord] = {}
    for candidate in candidates:
        key = candidate.url or candidate.id
        existing = seen.get(key)
        if existing is None:
            seen[key] = candidate
            continue
        for query in candidate.matched_queries:
            existing.merge_query(query)
        for evidence in candidate.evidence:
            if evidence not in existing.evidence:
                existing.evidence.append(evidence)
    return list(seen.values())


def _merge_localization(candidates: list[dict[str, object]], review: dict[str, object]) -> list[dict[str, object]]:
    by_id = {str(item.get("candidate_id")): item for item in review.get("reviews", []) if isinstance(item, dict)}
    merged: list[dict[str, object]] = []
    for candidate in candidates:
        item = dict(candidate)
        localization = by_id.get(str(candidate.get("id")), {})
        if localization:
            item["localization"] = localization
        merged.append(item)
    return merged


def _ensure_online_sources(search_plan: dict[str, object], research_plan: dict[str, object], research_contract: dict[str, object] | None = None) -> dict[str, object]:
    source_plan = search_plan.get("source_plan")
    if not isinstance(source_plan, list):
        source_plan = []
    existing = {str(item.get("source")) for item in source_plan if isinstance(item, dict)}
    queries = [str(item) for item in research_plan.get("queries", []) if isinstance(item, str)] if isinstance(research_plan.get("queries"), list) else []
    if not queries:
        contract = research_contract or {}
        if bool(contract.get("requires_branch_reports")):
            modules = [str(item).replace("_", " ") for item in contract.get("subproject_modules", []) if isinstance(item, str)]
            queries = [
                "existing open source job search resume interview assistant",
                "AI resume interview knowledge remediation project generator",
                "career assistant agent workflow open source",
                *modules[:3],
            ]
        else:
            queries = ["workflow kernel", "agent orchestrator", "MCP server workflow"]
    mode = str((research_contract or {}).get("mode") or "")
    source_policy = (research_contract or {}).get("source_policy") if isinstance((research_contract or {}).get("source_policy"), dict) else {}
    required_sources = source_policy.get("required_sources_from_user") if isinstance(source_policy.get("required_sources_from_user"), list) else []
    build_decision = mode == "build_decision"
    additions: list[dict[str, object]] = []
    if "github_repo" not in existing:
        additions.append(
            {
                "source": "github_repo",
                "priority": "high",
                "queries": queries[:3],
                "reason": "online-search 已审批，覆盖可复用开源实现和库。"
                if build_decision
                else "online-search 已审批，强制覆盖 GitHub 开源实现来源。",
            }
        )
    if "gitee_repo" not in existing:
        additions.append(
            {
                "source": "gitee_repo",
                "priority": "medium" if build_decision else "high",
                "queries": queries[:3],
                "reason": "online-search 已审批，作为中文开源平台补充来源；不作为默认主评分目标。"
                if build_decision
                else "online-search 已审批，覆盖中文开源平台 Gitee。",
            }
        )
    if "chinese_web" not in existing:
        additions.append(
            {
                "source": "chinese_web",
                "priority": "medium" if build_decision else "high",
                "queries": queries[:3],
                "reason": "online-search 已审批，补充中文资料和用户指定来源线索；不等同于公开可见性验收。"
                if build_decision
                else "online-search 已审批，覆盖中文互联网资料和中文技术博客。",
            }
        )
    if "official_docs" not in existing:
        additions.append(
            {
                "source": "official_docs",
                "priority": "medium",
                "queries": [*queries[:2], *[str(item) for item in required_sources[:2]]],
                "reason": "online-search 已审批，检查官方文档/API/服务边界和用户显式来源。",
            }
        )
    if "mcp_registry" not in existing:
        additions.append(
            {
                "source": "mcp_registry",
                "priority": "medium",
                "queries": queries[:3],
                "reason": "online-search 已审批，强制覆盖 MCP registry 来源。",
            }
        )
    if "github_skill" not in existing:
        additions.append(
            {
                "source": "github_skill",
                "priority": "medium",
                "queries": queries[:2],
                "reason": "online-search 已审批，搜索 GitHub 上可能存在的 Codex SKILL.md 实现。",
            }
        )
    if additions:
        updated = dict(search_plan)
        updated["source_plan"] = [*source_plan, *additions]
        updated["requires_online"] = True
        return updated
    return search_plan


def _git_root(path: Path) -> Path | None:
    completed = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip()).resolve()


def _git(path: Path, args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=check)


def _code_change_prompt(idea: str, discovery_report: str, implementation_plan: str) -> str:
    return (
        load_core_rules()
        + "\n\n你正在隔离 git worktree 中执行代码修改。必须只实现 implementation_plan 中必要的改动。"
        + "不要读取密钥、不要登录、不要安装依赖、不要提交、不要 push、不要创建 PR、不要发送消息。"
        + "完成后不要解释太多，真实改文件即可；nexus 会生成 diff 供用户二次确认。"
        + "\n\n用户原始需求：\n"
        + idea
        + "\n\nDiscovery 报告：\n"
        + discovery_report[:12000]
        + "\n\nImplementation Plan：\n"
        + implementation_plan[:12000]
    )


def _boundary_check(diff_text: str) -> dict[str, object]:
    forbidden_fragments = [
        "/.env",
        ".ssh/",
        "/.git/",
        "id_rsa",
        "token",
        "cookie",
        "node_modules/",
        ".venv/",
        "dist/",
        "build/",
    ]
    allowed_prefixes = (
        "src/",
        "tests/",
        "docs/",
        "README",
        "pyproject.toml",
        "package.json",
        "nexus/",
        "skills/",
    )
    files: list[str] = []
    blocked: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        target = parts[3][2:] if parts[3].startswith("b/") else parts[3]
        files.append(target)
        lowered = target.lower()
        if any(fragment in lowered for fragment in forbidden_fragments):
            blocked.append(target)
            continue
        if not target.startswith(allowed_prefixes):
            blocked.append(target)
    return {
        "schema": "nexus.boundary_check.v1",
        "allowed": not blocked,
        "files": files,
        "blocked_files": blocked,
        "rule": "Only common source/test/docs/package metadata paths are allowed by default.",
    }


def _default_test_command(project: Path) -> str:
    if (project / "pyproject.toml").exists():
        return "python -m pytest -q"
    if (project / "package.json").exists():
        return "npm test"
    return ""


def _short_text(value: str, limit: int = 1200) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[:500] + " ... " + text[-600:]
