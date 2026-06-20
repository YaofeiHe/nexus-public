from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from nexus.artifacts import RunStore
from nexus.interaction import write_interaction
from nexus.providers.mock import MockProvider
from nexus.providers.base import ModelRequest, ModelResponse, ProviderExecutionError, ProviderStatus
from nexus.providers.codex_cli import CodexCliProvider
from nexus.research_contract import branch_id_from_route
from nexus.runner import Runner


class NamedMockProvider(MockProvider):
    def __init__(self, name: str) -> None:
        self.name = name


class NamedAvailableProvider(MockProvider):
    def __init__(self, name: str) -> None:
        self.name = name


class FailingProvider(MockProvider):
    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        raise ProviderExecutionError(self.reason)


def _write_build_decision_intent(project: Path) -> None:
    (project / ".nexus").mkdir(parents=True, exist_ok=True)
    (project / "docs" / "intent").mkdir(parents=True, exist_ok=True)
    normalized = (
        "# probe 规范化意图需求\n\n"
        "需要一个体系化的求职工具：修改简历、辅助面试、总结记录面试结果、迭代简历、"
        "提供基础知识补习、生成训练用小项目，并且所有模块互通动态反馈。"
        "需要访问 forge 系列、ifome、Python 随机森林和 C++ 数值模拟项目说明文档，"
        "并开放在线搜索接口。"
    )
    (project / "docs" / "intent" / "normalized-requirement.md").write_text(normalized, encoding="utf-8")
    (project / ".nexus" / "project-intent.json").write_text(
        json.dumps(
            {
                "schema": "nexus.project_intent.v2",
                "project": project.name,
                "normalized_requirement_path": str(project / "docs" / "intent" / "normalized-requirement.md"),
                "project_path": str(project),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_runner_end_to_end_mock(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    interaction = Runner(tmp_path).run(
        "调研当前项目是否有现成 workflow/kernel 可以复用",
        project_path=project,
        provider_name="mock",
    )
    assert interaction["previous_task_status"] == "completed"
    run_dir = next((tmp_path / ".data" / "runs").iterdir())
    assert (run_dir / "input.json").exists()
    assert (run_dir / "state.json").exists()
    assert (run_dir / "interaction.json").exists()
    assert (run_dir / "model_requests" / "task_block" / "model_request.json").exists()
    assert (run_dir / "model_requests" / "intent_route" / "model_request.json").exists()
    assert (run_dir / "model_responses" / "final_report" / "validated_response.json").exists()
    assert (run_dir / "candidates" / "candidates.jsonl").exists()
    assert "中文互联网" in (run_dir / "reports" / "final_report.md").read_text(encoding="utf-8")


def test_research_architecture_first_search_and_final_report_use_high_provider(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    low = NamedMockProvider("low-provider")
    high = NamedMockProvider("high-provider")

    monkeypatch.setattr("nexus.runner.iter_real_provider_candidates", lambda root, cwd=None, intensity="low": [high] if intensity == "high" else [low])
    interaction = Runner(tmp_path).run("中文 idea", project_path=project)

    assert interaction["previous_task_status"] == "completed"
    run_dir = next((tmp_path / ".data" / "runs").iterdir())
    high_nodes = ["intent_route", "task_block", "research_plan", "search_plan_round_1", "final_report"]
    for node in high_nodes:
        status = json.loads((run_dir / "nodes" / node / "status.json").read_text(encoding="utf-8"))
        assert status["provider"] == "high-provider"
    low_nodes = ["coverage_review_round_1", "stop_decision_round_1", "candidate_review", "risk_analysis"]
    for node in low_nodes:
        status = json.loads((run_dir / "nodes" / node / "status.json").read_text(encoding="utf-8"))
        assert status["provider"] == "low-provider"


def test_interaction_relay_shape(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).run("中文 idea", project_path=project, provider_name="mock")
    assert set(interaction) >= {
        "previous_task_status",
        "previous_task_output",
        "next_task_prompt",
        "blocked_reason",
        "approval_request",
        "artifact_refs",
        "lifecycle_status",
        "pending_actions",
        "continuation",
        "auto_resume_supported",
        "recovery_mode",
        "recovery_state",
        "recovery_kind",
        "safe_next_actions",
        "recommended_executor",
        "debug_handoff",
        "debug_session",
        "rebind_requirements",
        "rebind_command",
        "terminal",
    }


def test_pending_actions_render_specific_prompts_not_status_fallback(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.ensure()

    interaction = write_interaction(
        store,
        status="blocked",
        output="provider runtime failed",
        next_prompt="恢复动作待审批。",
        blocked_reason="provider_runtime_failed",
        pending_actions=[
            {
                "action_id": "skip_current_provider_and_continue",
                "kind": "provider_fallback_approval",
                "provider": "codex-cli",
                "skip_provider": "codex-cli",
                "target_provider": "qwen3.7max",
                "command": f'python -m nexus.cli recover {store.run_id} "跳过 codex-cli fallback 到 qwen3.7max 并继续"',
            }
        ],
    )

    assert "跳过 codex-cli fallback 到 qwen3.7max 并继续" in interaction["next_task_prompt"]
    assert "查看" not in interaction["next_task_prompt"]
    assert "python -m nexus.cli" not in interaction["next_task_prompt"]


def test_status_reads_existing_run_without_creating_new_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.ensure()
    write_interaction(
        store,
        status="blocked",
        output="provider preflight failed",
        next_prompt="恢复动作待审批。",
        blocked_reason="provider_preflight_failed",
        pending_actions=[
            {
                "action_id": "skip_current_provider_and_continue",
                "kind": "provider_fallback_approval",
                "provider": "codex-cli",
                "skip_provider": "codex-cli",
                "command": f'python -m nexus.cli recover {store.run_id} "跳过 codex-cli fallback 继续"',
            }
        ],
    )

    interaction = Runner(tmp_path).invoke(f"$nexus-workflow 查看 {store.run_id} 的状态")

    assert interaction["run_id"] == store.run_id
    assert "跳过 codex-cli fallback 继续" in interaction["next_task_prompt"]
    assert len(list((tmp_path / ".data" / "runs").iterdir())) == 1


def test_codex_cli_runtime_status_records_heartbeat_and_completion(tmp_path: Path) -> None:
    provider = CodexCliProvider(cwd=tmp_path, timeout_seconds=5)
    provider.status_interval_seconds = 0.1
    provider.runtime_status_path = tmp_path / "runtime-status.json"

    completed = provider._run_codex_exec(
        [sys.executable, "-c", "import sys, time; sys.stdin.read(); time.sleep(0.25); print('done')"],
        "prompt",
    )

    status = json.loads(provider.runtime_status_path.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert status["schema"] == "nexus.provider_runtime_status.v1"
    assert status["provider"] == "codex-cli"
    assert status["status"] == "completed"
    assert status["heartbeat_count"] >= 1
    assert status["timeout_seconds"] == 5
    assert status["stdout_tail"] == "done"


def test_codex_cli_runtime_status_records_timeout(tmp_path: Path) -> None:
    provider = CodexCliProvider(cwd=tmp_path, timeout_seconds=0.2)
    provider.status_interval_seconds = 0.1
    provider.runtime_status_path = tmp_path / "runtime-status.json"

    try:
        provider._run_codex_exec(
            [sys.executable, "-c", "import sys, time; sys.stdin.read(); time.sleep(2)"],
            "prompt",
        )
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("expected timeout")

    status = json.loads(provider.runtime_status_path.read_text(encoding="utf-8"))
    assert status["schema"] == "nexus.provider_runtime_status.v1"
    assert status["status"] == "timeout"
    assert status["heartbeat_count"] >= 1
    assert status["timeout_seconds"] == 0.2


def test_codex_cli_preflight_gets_runtime_status_path(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = RunStore(tmp_path)
    store.ensure()
    captured: dict[str, str] = {}

    def fake_smoke(self: CodexCliProvider) -> ProviderStatus:
        assert self.runtime_status_path is not None
        captured["path"] = str(self.runtime_status_path)
        self.runtime_status_path.write_text(
            json.dumps({"schema": "nexus.provider_runtime_status.v1", "status": "completed"}, ensure_ascii=False),
            encoding="utf-8",
        )
        return ProviderStatus("codex-cli", "available", "smoke ready")

    provider = CodexCliProvider(cwd=project)
    monkeypatch.setattr(CodexCliProvider, "smoke_status", fake_smoke)

    assert Runner(tmp_path)._preflight_provider(store, provider, attempts=[], intensity="high") is True
    assert captured["path"].endswith("provider_runtime_status_preflight_codex_cli_high.json")
    assert Path(captured["path"]).exists()
    assert provider.runtime_status_path is None


def test_recover_executes_pending_runner_command(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).prepare_project(project)
    run_id = interaction["run_id"]

    monkeypatch.setattr(
        Runner,
        "approve_and_continue",
        lambda self, actual_run_id, stage: {
            "previous_task_status": "completed",
            "previous_task_output": f"approved via recover: {actual_run_id}:{stage}",
            "next_task_prompt": "done",
            "run_id": actual_run_id,
        },
    )

    recovered = Runner(tmp_path).recover(run_id, "恢复 git baseline")
    assert recovered["previous_task_status"] == "completed"
    assert recovered["previous_task_output"] == f"approved via recover: {run_id}:git-baseline"


def test_invoke_routes_standard_provider_recovery_prompts_to_original_run(monkeypatch, tmp_path: Path) -> None:
    captured: list[tuple[str, str]] = []

    def fake_recover(self, run_id: str = "latest", request: str = "") -> dict[str, object]:
        captured.append((run_id, request))
        return {
            "previous_task_status": "completed",
            "previous_task_output": f"recovered {run_id}",
            "next_task_prompt": "$nexus-workflow 查看状态",
            "run_id": run_id,
        }

    monkeypatch.setattr(Runner, "recover", fake_recover)

    run_id = "<NEXUS_RUN_ID>"
    fallback = Runner(tmp_path).invoke(f"$nexus-workflow 跳过 codex-cli fallback 继续 {run_id}")
    repair = Runner(tmp_path).invoke(f"$nexus-workflow 修复 codex-cli preflight 权限问题并继续 {run_id}")

    assert fallback["previous_task_status"] == "completed"
    assert repair["previous_task_status"] == "completed"
    assert captured == [
        (run_id, f"$nexus-workflow 跳过 codex-cli fallback 继续 {run_id}"),
        (run_id, f"$nexus-workflow 修复 codex-cli preflight 权限问题并继续 {run_id}"),
    ]


def test_invoke_routes_debug_handoff_and_rebind_prompts_with_run_id(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def fake_handoff(self, run_id: str = "latest", *, reason: str = "") -> dict[str, object]:
        captured["handoff_run_id"] = run_id
        captured["handoff_reason"] = reason
        return {"previous_task_status": "blocked", "previous_task_output": "handoff", "next_task_prompt": "$nexus-workflow 回跳", "run_id": run_id}

    def fake_rebind(self, run_id: str = "latest", *, handoff_id: str = "") -> dict[str, object]:
        captured["rebind_run_id"] = run_id
        return {"previous_task_status": "completed", "previous_task_output": "rebound", "next_task_prompt": "$nexus-workflow 查看", "run_id": run_id}

    monkeypatch.setattr(Runner, "handoff_for_debug", fake_handoff)
    monkeypatch.setattr(Runner, "rebind_and_continue", fake_rebind)

    run_id = "<NEXUS_RUN_ID>"
    Runner(tmp_path).invoke(f"$nexus-workflow 脱离 workflow 进行 debug 并登记 {run_id}")
    Runner(tmp_path).invoke(f"$nexus-workflow 回跳到刚才的 workflow，run_id {run_id}")

    assert captured["handoff_run_id"] == run_id
    assert captured["rebind_run_id"] == run_id


def test_invoke_routes_skill_check_before_global_problem_suffix(tmp_path: Path) -> None:
    request = (
        "$nexus-workflow 检查当前 nexus workflow 是否安装并可用\n\n"
        "全局 Nexus 问题验收：所有样例都必须检查 GitHub public 同步、Feishu 和恢复链路。"
    )

    interaction = Runner(tmp_path).invoke(request)

    assert interaction["previous_task_status"] == "completed"
    assert "skill" in interaction["previous_task_output"]
    assert "github_sync" not in interaction["previous_task_output"]
    run_dir = tmp_path / ".data" / "runs" / str(interaction["run_id"])
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["current_node"] == "skill_doctor"
    assert (run_dir / "tool_results" / "skill_doctor.json").exists()


def test_invoke_routes_model_status_prompt(tmp_path: Path) -> None:
    interaction = Runner(tmp_path).invoke("$nexus-workflow 查看当前可用的基座模型和 provider 状态")

    assert interaction["previous_task_status"] == "completed"
    assert "低强度" in interaction["previous_task_output"]
    run_dir = tmp_path / ".data" / "runs" / str(interaction["run_id"])
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["current_node"] == "model_status"
    assert (run_dir / "tool_results" / "model_status.json").exists()
    assert (run_dir / "tool_results" / "provider_status.json").exists()


def test_model_node_failure_updates_state_to_current_node(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.ensure()
    runner = Runner(tmp_path)

    with pytest.raises(ProviderExecutionError):
        runner._model_node(store, FailingProvider("bad-provider", "boom"), "intent_route", "prompt")

    state = json.loads((store.run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["current_node"] == "intent_route"
    assert state["provider"] == "bad-provider"
    assert state["model_node_status"] == "failed"


def test_resume_blocks_when_same_run_lock_is_active(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = RunStore(tmp_path)
    store.ensure()
    store.write_json("input.json", {"schema": "nexus.input.v1", "idea": "demo", "project_path": str(project), "provider": "mock", "max_candidates": 8})
    store.write_json("state.json", {"schema": "nexus.state.v1", "run_id": store.run_id, "status": "blocked", "current_node": "online_search_approval"})
    store.write_json("workflow_resume.lock", {"pid": os.getpid(), "created_at": "now"})
    monkeypatch.setattr(Runner, "_select_provider", lambda self, run_store, provider_name, project_path, intensity="low": MockProvider())

    interaction = Runner(tmp_path).resume(store.run_id)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "workflow_resume_already_running"
    assert str(store.path("workflow_resume.lock")) in interaction["artifact_refs"]


def test_recover_host_action_keeps_original_run_and_continuation(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.ensure()
    store.write_json("input.json", {"schema": "nexus.input.v1", "project_path": str(tmp_path), "provider": "auto", "idea": "host action"})
    continuation = {"kind": "runner_call", "method": "resume", "kwargs": {}}
    write_interaction(
        store,
        status="blocked",
        output="GitHub 登录启动失败。",
        next_prompt=f"$nexus-workflow 恢复 {store.run_id}，并说明已完成",
        blocked_reason="LOGIN_START_FAILED",
        lifecycle_status="awaiting_approval",
        pending_actions=[
            {
                "action_id": "host_terminal_github_login",
                "kind": "shell_escalation",
                "command": "gh auth login --web --clipboard --skip-ssh-key --git-protocol https --hostname github.com",
                "rationale": "需要宿主环境打开 GitHub 登录。",
                "requires_host_permission": True,
            }
        ],
        continuation=continuation,
        auto_resume_supported=True,
    )

    recovered = Runner(tmp_path).recover(store.run_id, "执行 GitHub 登录恢复")

    assert recovered["previous_task_status"] == "blocked"
    assert recovered["run_id"] == store.run_id
    assert recovered["blocked_reason"] == "host_action_required"
    assert recovered["continuation"] == continuation
    assert recovered["pending_actions"][0]["action_id"] == "host_terminal_github_login"


def test_handoff_for_debug_registers_rebindable_pause(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).run("中文 idea", project_path=project, provider_name="mock")
    run_id = interaction["run_id"]

    handoff = Runner(tmp_path).handoff_for_debug(run_id, reason="修复 runner bug")

    assert handoff["previous_task_status"] == "blocked"
    assert handoff["lifecycle_status"] == "awaiting_debug_rebind"
    assert handoff["recovery_state"] == "recoverable_via_debug_rebind"
    assert handoff["debug_handoff"]["handoff_id"]
    assert handoff["debug_handoff"]["source_run_id"] == run_id
    assert handoff["debug_handoff"]["source_node"]
    run_dir = tmp_path / ".data" / "runs" / run_id
    assert (run_dir / "handoffs" / "debug_handoff.json").exists()
    assert (run_dir / "handoffs" / "debug_session.json").exists()


def test_append_debug_worklog_requires_registered_handoff(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).run("中文 idea", project_path=project, provider_name="mock")
    run_id = interaction["run_id"]

    blocked = Runner(tmp_path).append_debug_worklog(run_id, summary="no handoff yet")

    assert blocked["previous_task_status"] == "blocked"
    assert blocked["blocked_reason"] == "debug_handoff_missing"


def test_rebind_and_continue_resumes_from_registered_debug_handoff(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).prepare_project(project)
    run_id = interaction["run_id"]
    handoff = Runner(tmp_path).handoff_for_debug(run_id, reason="修复 git baseline flow")
    handoff_id = handoff["debug_handoff"]["handoff_id"]
    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="diagnose", summary="found baseline approval route")
    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="edit", summary="patched runner", paths=[str(project / "README.md")])
    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="test", summary="pytest ok", result="passed")

    monkeypatch.setattr(
        Runner,
        "approve_and_continue",
        lambda self, actual_run_id, stage: {
            "previous_task_status": "completed",
            "previous_task_output": f"rebound via pending command: {actual_run_id}:{stage}",
            "next_task_prompt": "done",
            "run_id": actual_run_id,
        },
    )

    rebound = Runner(tmp_path).rebind_and_continue(run_id, handoff_id=handoff_id)

    assert rebound["previous_task_status"] == "completed"
    assert rebound["previous_task_output"] == f"rebound via pending command: {run_id}:git-baseline"
    run_dir = tmp_path / ".data" / "runs" / run_id
    assert (run_dir / "worklogs" / "debug_summary.json").exists()
    assert (run_dir / "rebind" / "rebind_result.json").exists()


def test_debug_worklog_append_recovers_entries_from_summary(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).prepare_project(project)
    run_id = interaction["run_id"]
    handoff = Runner(tmp_path).handoff_for_debug(run_id, reason="修复 git baseline flow")
    handoff_id = handoff["debug_handoff"]["handoff_id"]
    run_dir = tmp_path / ".data" / "runs" / run_id
    worklog_dir = run_dir / "worklogs"
    worklog_dir.mkdir(parents=True, exist_ok=True)
    diagnose = {"schema": "nexus.debug_worklog_entry.v1", "handoff_id": handoff_id, "kind": "diagnose", "summary": "diagnosed lost write", "result": "", "command": "inspect", "paths": []}
    previous_test = {"schema": "nexus.debug_worklog_entry.v1", "handoff_id": handoff_id, "kind": "test", "summary": "previous test survived", "result": "passed", "command": "pytest", "paths": []}
    (worklog_dir / "debug_summary.json").write_text(json.dumps({"schema": "nexus.debug_summary.v1", "handoff_id": handoff_id, "entries": [diagnose]}, ensure_ascii=False), encoding="utf-8")
    (worklog_dir / "debug_worklog.json").write_text(json.dumps({"schema": "nexus.debug_worklog.v1", "entries": [previous_test]}, ensure_ascii=False), encoding="utf-8")

    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="edit", summary="patched append merge", paths=[str(project / "README.md")])

    merged = json.loads((worklog_dir / "debug_worklog.json").read_text(encoding="utf-8"))
    assert {entry["kind"] for entry in merged["entries"]} == {"diagnose", "edit", "test"}

    monkeypatch.setattr(
        Runner,
        "approve_and_continue",
        lambda self, actual_run_id, stage: {
            "previous_task_status": "completed",
            "previous_task_output": f"rebound via pending command: {actual_run_id}:{stage}",
            "next_task_prompt": "done",
            "run_id": actual_run_id,
        },
    )

    rebound = Runner(tmp_path).rebind_and_continue(run_id, handoff_id=handoff_id)

    assert rebound["previous_task_status"] == "completed"


def test_rebind_completed_run_without_pending_command_stays_completed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).prepare_project(project)
    run_id = interaction["run_id"]
    handoff = Runner(tmp_path).handoff_for_debug(run_id, reason="manual fix completed run")
    handoff_id = handoff["debug_handoff"]["handoff_id"]
    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="diagnose", summary="diagnosed completed state")
    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="edit", summary="patched behavior")
    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="test", summary="tests passed")
    run_dir = tmp_path / ".data" / "runs" / run_id
    (run_dir / "state.json").write_text(json.dumps({"schema": "nexus.state.v1", "status": "completed", "current_node": "github_sync_bootstrap"}, ensure_ascii=False), encoding="utf-8")

    rebound = Runner(tmp_path).rebind_and_continue(run_id, handoff_id=handoff_id)

    assert rebound["previous_task_status"] == "completed"
    assert "已回绑" in rebound["previous_task_output"]
    assert (run_dir / "rebind" / "rebind_result.json").exists()


def test_debug_rebind_writes_recovery_playbook_approval(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).prepare_project(project)
    run_id = interaction["run_id"]
    handoff = Runner(tmp_path).handoff_for_debug(run_id, reason="修复 git baseline flow")
    handoff_id = handoff["debug_handoff"]["handoff_id"]
    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="diagnose", summary="found baseline approval route")
    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="edit", summary="patched runner", paths=[str(project / "README.md")])
    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="test", summary="pytest ok", result="passed")

    monkeypatch.setattr(
        Runner,
        "approve_and_continue",
        lambda self, actual_run_id, stage: {
            "previous_task_status": "completed",
            "previous_task_output": f"rebound via pending command: {actual_run_id}:{stage}",
            "next_task_prompt": "done",
            "run_id": actual_run_id,
        },
    )

    rebound = Runner(tmp_path).rebind_and_continue(run_id, handoff_id=handoff_id)

    assert rebound["previous_task_status"] == "completed"
    assert "recovery-playbook" in rebound["next_task_prompt"]
    run_dir = tmp_path / ".data" / "runs" / run_id
    recovery = json.loads((run_dir / "tool_results" / "recovery_result.json").read_text(encoding="utf-8"))
    assert recovery["recovered_by"] == "debug_rebind"
    assert recovery["context"]["project_path"] == str(project.resolve())
    assert (run_dir / "tool_results" / "debug_recovery_result.json").exists()
    assert (run_dir / "approvals" / "recovery_playbook_write_required.json").exists()

    approved = Runner(tmp_path).approve(run_id, "recovery-playbook")

    assert approved["previous_task_status"] == "completed"
    assert (project / ".nexus" / "recovery-playbook.json").exists()
    assert (project / "docs" / "recovery-records.md").exists()


def test_rebind_requires_debug_diagnose_entry(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).prepare_project(project)
    run_id = interaction["run_id"]
    handoff = Runner(tmp_path).handoff_for_debug(run_id, reason="修复 git baseline flow")
    handoff_id = handoff["debug_handoff"]["handoff_id"]
    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="edit", summary="patched runner", paths=[str(project / "README.md")])
    Runner(tmp_path).append_debug_worklog(run_id, handoff_id=handoff_id, kind="test", summary="pytest ok", result="passed")

    blocked = Runner(tmp_path).rebind_and_continue(run_id, handoff_id=handoff_id)

    assert blocked["previous_task_status"] == "blocked"
    assert blocked["blocked_reason"] == "debug_diagnose_missing"


def test_prepare_project_approval_exposes_pending_runner_command(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    interaction = Runner(tmp_path).prepare_project(project)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "git_baseline_approval_required"
    assert interaction["lifecycle_status"] == "awaiting_approval"
    assert interaction["pending_actions"]
    assert interaction["pending_actions"][0]["kind"] == "runner_command"
    assert "approve-and-continue" in interaction["pending_actions"][0]["command"]


def test_resume_completed_run_noop(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    Runner(tmp_path).run("中文 idea", project_path=project, provider_name="mock")
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    interaction = Runner(tmp_path).resume(run_id)
    assert interaction["previous_task_status"] == "completed"
    assert "已完成" in interaction["previous_task_output"]


def test_default_run_blocks_without_real_provider(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr("nexus.runner.iter_real_provider_candidates", lambda root, cwd=None, intensity="low": [])
    interaction = Runner(tmp_path).run("中文 idea", project_path=project)
    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "real_model_provider_not_configured"
    run_dir = next((tmp_path / ".data" / "runs").iterdir())
    setup = json.loads((run_dir / "approvals" / "provider_setup_required.json").read_text(encoding="utf-8"))
    assert setup["message"] == "未发现可用真实模型 provider；未使用 MockProvider。"


def test_provider_recovery_requires_explicit_fallback_when_codex_cli_smoke_fails(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    fallback = NamedMockProvider("api-fallback")
    high = NamedMockProvider("high-provider")

    monkeypatch.setattr(
        "nexus.providers.codex_cli.CodexCliProvider.status",
        lambda self: ProviderStatus("codex-cli", "available", "cli ready"),
    )

    def fake_smoke(self) -> ProviderStatus:
        self.last_smoke_details = {
            "repair_module": "codex_cli_auto_repair_v2",
            "repair_attempted": True,
            "repair_succeeded": False,
            "repair_strategy": "isolated_workspace_codex_home",
            "effective_codex_home": str((tmp_path / "project" / ".nexus" / "runtime" / "codex-home").resolve()),
            "effective_extra_args": ["--ignore-user-config"],
            "attempts": [
                {
                    "strategy": "workspace_local_codex_home",
                    "status": "needs_config",
                    "reason": "still failed",
                },
                {
                    "strategy": "isolated_workspace_codex_home",
                    "status": "needs_config",
                    "reason": "still failed",
                },
            ],
        }
        return ProviderStatus("codex-cli", "needs_config", "codex exec smoke test failed: attempt to write a readonly database at state_5.sqlite Operation not permitted")

    monkeypatch.setattr("nexus.providers.codex_cli.CodexCliProvider.smoke_status", fake_smoke)

    def candidates(root, cwd=None, intensity="low"):
        if intensity == "high":
            return [high]
        skipped = set(filter(None, (os.environ.get("NEXUS_AUTO_SKIP_PROVIDERS", "") or "").split(",")))
        providers = [CodexCliProvider(cwd=cwd), fallback]
        return [provider for provider in providers if provider.name not in skipped]

    monkeypatch.setattr("nexus.runner.iter_real_provider_candidates", candidates)

    interaction = Runner(tmp_path).run("中文 idea", project_path=project)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "provider_preflight_failed"
    assert "跳过 codex-cli fallback" in interaction["next_task_prompt"]
    assert interaction["pending_actions"][0]["action_id"] == "retry_current_provider"
    assert interaction["pending_actions"][0]["kind"] == "provider_retry"
    assert interaction["pending_actions"][0]["provider"] == "codex-cli"
    assert interaction["pending_actions"][0]["requires_host_permission"] is True
    assert interaction["pending_actions"][1]["action_id"] == "skip_current_provider_and_continue"
    assert interaction["pending_actions"][1]["kind"] == "provider_fallback_approval"
    assert interaction["pending_actions"][1]["skip_provider"] == "codex-cli"
    run_dir = next((tmp_path / ".data" / "runs").iterdir())
    attempts = json.loads((run_dir / "tool_results" / "provider_attempts.json").read_text(encoding="utf-8"))
    assert any(item["provider"] == "codex-cli" and item["issue_type"] == "codex_state_db_readonly" for item in attempts["attempts"])
    assert any(item["provider"] == "codex-cli" and item.get("auto_repair", {}).get("repair_attempted") is True for item in attempts["attempts"])
    assert "low" not in attempts["selected_by_intensity"]
    recovery = json.loads((run_dir / "tool_results" / "provider_recovery.json").read_text(encoding="utf-8"))
    assert recovery["actions_applied"] == []
    assert recovery["actions_recommended"] == ["switch_to_next_real_provider"]
    assert recovery["requires_user_approval"] is True
    assert recovery["exhausted"] is False

    recovered = Runner(tmp_path).recover(interaction["run_id"], "跳过 codex-cli fallback 继续")

    assert recovered["previous_task_status"] == "completed"
    attempts = json.loads((run_dir / "tool_results" / "provider_attempts.json").read_text(encoding="utf-8"))
    assert attempts["selected_by_intensity"]["low"] == "api-fallback"
    recovery = json.loads((run_dir / "tool_results" / "recovery_result.json").read_text(encoding="utf-8"))
    assert recovery["status"] == "completed"
    assert recovery["recovered_by"] == "provider_fallback"
    assert recovery["action_applied"] == "skip_current_provider_and_continue"
    assert recovery["actions_applied"] == ["skip_current_provider_and_continue"]
    assert recovery["final_project_path"] == str(project.resolve())
    assert (run_dir / "approvals" / "recovery_playbook_write_required.json").exists()


def test_recovery_playbook_approval_writes_project_memory(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    fallback = NamedMockProvider("api-fallback")
    high = NamedMockProvider("high-provider")

    monkeypatch.setattr(
        "nexus.providers.codex_cli.CodexCliProvider.status",
        lambda self: ProviderStatus("codex-cli", "available", "cli ready"),
    )
    monkeypatch.setattr(
        "nexus.providers.codex_cli.CodexCliProvider.smoke_status",
        lambda self: ProviderStatus("codex-cli", "needs_config", "codex exec smoke test failed: attempt to write a readonly database at state_5.sqlite Operation not permitted"),
    )
    def candidates(root, cwd=None, intensity="low"):
        if intensity == "high":
            return [high]
        skipped = set(filter(None, (os.environ.get("NEXUS_AUTO_SKIP_PROVIDERS", "") or "").split(",")))
        providers = [CodexCliProvider(cwd=cwd), fallback]
        return [provider for provider in providers if provider.name not in skipped]

    monkeypatch.setattr("nexus.runner.iter_real_provider_candidates", candidates)

    interaction = Runner(tmp_path).run("中文 idea", project_path=project)
    assert interaction["previous_task_status"] == "blocked"
    run_id = interaction["run_id"]
    recovered = Runner(tmp_path).recover(run_id, "跳过 codex-cli fallback 继续")
    assert recovered["previous_task_status"] == "completed"

    approved = Runner(tmp_path).approve(run_id, "recovery-playbook")

    assert approved["previous_task_status"] == "completed"
    playbook = json.loads((project / ".nexus" / "recovery-playbook.json").read_text(encoding="utf-8"))
    assert playbook["entries"]
    assert (project / "docs" / "recovery-records.md").exists()


def test_recovery_playbook_approval_uses_created_project_not_runner_root(tmp_path: Path) -> None:
    root = tmp_path / "nexus-root"
    root.mkdir()
    project = tmp_path / "projects" / "target"
    (project / ".nexus").mkdir(parents=True)
    (project / ".nexus" / "board.md").write_text("# board\n", encoding="utf-8")
    store = RunStore(root, "run-test")
    store.ensure()
    store.write_json("input.json", {"schema": "nexus.project_init_input.v1", "parent": str(project.parent)})
    store.write_json("approvals/APPROVED_project-root.json", {"schema": "nexus.approval_marker.v1", "created_path": str(project)})
    store.write_json("state.json", {"schema": "nexus.run_state.v1", "status": "completed", "project_path": str(root), "target_path": str(project)})
    store.write_json(
        "tool_results/recovery_result.json",
        {
            "schema": "nexus.recovery_result.v1",
            "status": "completed",
            "context": {
                "run_id": store.run_id,
                "project_path": str(project.parent),
                "module": "provider",
                "node": "provider_preflight",
                "reason": "provider_preflight_failed",
                "failure_signature": "sig",
            },
            "guidance": {"schema": "nexus.failure_recovery_guidance.v1", "summary": "Recovered", "recommended_actions": []},
            "attempts": [],
        },
    )

    approved = Runner(root).approve(store.run_id, "recovery-playbook")

    assert approved["previous_task_status"] == "completed"
    assert (project / ".nexus" / "recovery-playbook.json").exists()
    assert not (root / ".nexus" / "recovery-playbook.json").exists()
    result = json.loads((store.path("tool_results/recovery_playbook_write_result.json")).read_text(encoding="utf-8"))
    assert result["playbook_path"] == str(project / ".nexus" / "recovery-playbook.json")


def test_recovery_playbook_approval_blocks_when_project_unresolved(tmp_path: Path) -> None:
    root = tmp_path / "nexus-root"
    root.mkdir()
    store = RunStore(root, "run-test")
    store.ensure()
    store.write_json("input.json", {"schema": "nexus.project_init_input.v1", "parent": str(tmp_path / "projects")})
    store.write_json("state.json", {"schema": "nexus.run_state.v1", "status": "completed", "project_path": str(root)})
    store.write_json(
        "tool_results/recovery_result.json",
        {
            "schema": "nexus.recovery_result.v1",
            "status": "completed",
            "context": {"run_id": store.run_id, "module": "provider", "node": "provider_preflight", "reason": "provider_preflight_failed"},
            "guidance": {"schema": "nexus.failure_recovery_guidance.v1", "summary": "Recovered", "recommended_actions": []},
            "attempts": [],
        },
    )

    approved = Runner(root).approve(store.run_id, "recovery-playbook")

    assert approved["previous_task_status"] == "blocked"
    assert approved["blocked_reason"] == "recovery_playbook_project_path_unresolved"
    assert not (root / ".nexus" / "recovery-playbook.json").exists()


def test_recovery_model_receives_related_experience_without_being_constrained(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".nexus").mkdir()
    (project / ".nexus" / "recovery-playbook.json").write_text(
        json.dumps(
            {
                "schema": "nexus.recovery_playbook.v1",
                "entries": [
                    {
                        "schema": "nexus.recovery_playbook_entry.v1",
                        "failure_signature": "old-github-eof",
                        "module": "github",
                        "node": "github_auth",
                        "reason": "LOGIN_START_FAILED",
                        "summary": "GitHub device-code EOF 后先复查 gh auth status，再考虑去代理重试。",
                        "probable_root_cause": "github_device_login_post_eof_or_proxy_interrupted",
                        "recommended_actions": [{"action_id": "retry_without_proxy_and_debug_api"}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = RunStore(tmp_path)
    store.ensure()
    context = {
        "schema": "nexus.recovery_context.v1",
        "module": "github",
        "node": "github_private_sync",
        "reason": "unexpected_network_eof",
        "result": {"status": "blocked", "reason": "unexpected_network_eof", "stderr": "EOF while contacting github.com"},
        "project_path": str(project),
    }
    captured: dict[str, str] = {}

    monkeypatch.setattr(Runner, "_select_provider", lambda self, run_store, provider_name, project_path, *, intensity="low", allow_recovery=True: MockProvider())

    def fake_model_node(self, run_store, provider, node_id, schema_key, prompt):
        captured["prompt"] = prompt
        return {
            "schema": "nexus.failure_recovery_guidance.v1",
            "summary": "先判断旧 GitHub EOF 经验是否适用；若不适用则改走网络诊断。",
            "probable_root_cause": "network_eof",
            "safe_next_attempts": ["对照经验后自主判断。"],
            "manual_user_actions": [],
            "stop_conditions": [],
            "recommended_actions": [],
        }

    monkeypatch.setattr(Runner, "_model_node_with_schema", fake_model_node)

    guidance = Runner(tmp_path)._recovery_guidance(store, project, context, {})

    assert guidance["summary"].startswith("先判断旧 GitHub EOF 经验")
    assert (store.run_dir / "tool_results" / "related_recovery_experience.json").exists()
    assert "related_recovery_experience" in captured["prompt"]
    assert "GitHub device-code EOF" in captured["prompt"]
    assert "不能被经验牵着鼻子走" in captured["prompt"]


def test_legacy_existing_run_rebinds_stored_provider_to_auto(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    store.ensure()
    payload = {
        "schema": "nexus.input.v1",
        "project_path": str(tmp_path),
        "provider": "codex-cli",
        "requested_provider": "codex-cli",
        "idea": "legacy",
    }

    selected = Runner(tmp_path)._effective_provider_for_existing_run(store, payload, source="resume")

    assert selected == "auto"
    rebind = json.loads((store.run_dir / "resume" / "provider_rebind.json").read_text(encoding="utf-8"))
    assert rebind["stored_provider"] == "codex-cli"
    assert rebind["effective_provider"] == "auto"


def test_explicit_codex_mcp_requires_smoke_success(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setattr(
        "nexus.providers.codex_mcp.CodexMcpProvider.status",
        lambda self: ProviderStatus("codex-mcp", "available", "command found"),
    )
    monkeypatch.setattr(
        "nexus.providers.codex_mcp.CodexMcpProvider.smoke_status",
        lambda self: ProviderStatus("codex-mcp", "needs_config", "smoke failed"),
    )

    interaction = Runner(tmp_path).run("中文 idea", project_path=project, provider_name="codex-mcp")
    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "provider_preflight_failed"


def test_provider_preflight_recovery_blocks_before_next_candidate_until_approved(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    broken = NamedAvailableProvider("broken-provider")
    fallback = NamedAvailableProvider("api-fallback")
    high = NamedMockProvider("high-provider")

    def candidates(root, cwd=None, intensity="low"):
        if intensity == "high":
            return [high]
        skipped = set(filter(None, (os.environ.get("NEXUS_AUTO_SKIP_PROVIDERS", "") or "").split(",")))
        providers = [broken, fallback]
        return [provider for provider in providers if provider.name not in skipped]

    monkeypatch.setattr("nexus.runner.iter_real_provider_candidates", candidates)

    def fake_preflight(self, store, provider, *, attempts=None, intensity="low"):
        available = provider.name != "broken-provider"
        status = ProviderStatus(provider.name, "available" if available else "needs_config", "smoke ready" if available else "temporary preflight failure")
        if attempts is not None:
            attempts.append(
                {
                    "provider": provider.name,
                    "status": status.status,
                    "reason": status.reason,
                    "stage": "preflight",
                    "intensity": intensity,
                    "issue_type": "" if available else "temporary_preflight_failure",
                }
            )
        store.write_json(
            "tool_results/provider_preflight.json",
            {"schema": "nexus.provider_preflight.v1", "status": {"provider": provider.name, "status": status.status, "reason": status.reason}},
        )
        return available

    monkeypatch.setattr(Runner, "_preflight_provider", fake_preflight)

    interaction = Runner(tmp_path).run("中文 idea", project_path=project)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "provider_preflight_failed"
    assert "跳过 broken-provider fallback" in interaction["next_task_prompt"]
    assert interaction["pending_actions"][0]["action_id"] == "retry_current_provider"
    assert interaction["pending_actions"][0]["provider"] == "broken-provider"
    assert interaction["pending_actions"][1]["action_id"] == "skip_current_provider_and_continue"
    assert interaction["pending_actions"][1]["skip_provider"] == "broken-provider"
    run_dir = next((tmp_path / ".data" / "runs").iterdir())
    attempts = json.loads((run_dir / "tool_results" / "provider_attempts.json").read_text(encoding="utf-8"))
    assert "low" not in attempts["selected_by_intensity"]
    recovery = json.loads((run_dir / "tool_results" / "provider_recovery.json").read_text(encoding="utf-8"))
    assert recovery["actions_applied"] == []
    assert recovery["actions_recommended"] == ["switch_to_next_real_provider"]
    assert recovery["requires_user_approval"] is True
    assert recovery["exhausted"] is False
    assert recovery["guidance"]["summary"]

    recovered = Runner(tmp_path).recover(interaction["run_id"], "跳过 broken-provider fallback 继续")

    assert recovered["previous_task_status"] == "completed"
    attempts = json.loads((run_dir / "tool_results" / "provider_attempts.json").read_text(encoding="utf-8"))
    assert attempts["selected_by_intensity"]["low"] == "api-fallback"


def test_provider_preflight_recovery_blocks_with_standardized_output_after_attempts_exhaust(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    broken = NamedAvailableProvider("broken-provider")
    high = NamedMockProvider("high-provider")

    def candidates(root, cwd=None, intensity="low"):
        return [high] if intensity == "high" else [broken]

    monkeypatch.setattr("nexus.runner.iter_real_provider_candidates", candidates)

    def fake_preflight(self, store, provider, *, attempts=None, intensity="low"):
        available = provider.name == "high-provider"
        status = ProviderStatus(provider.name, "available" if available else "needs_config", "smoke ready" if available else "persistent preflight failure")
        if attempts is not None:
            attempts.append(
                {
                    "provider": provider.name,
                    "status": status.status,
                    "reason": status.reason,
                    "stage": "preflight",
                    "intensity": intensity,
                    "issue_type": "" if available else "persistent_preflight_failure",
                }
            )
        store.write_json(
            "tool_results/provider_preflight.json",
            {"schema": "nexus.provider_preflight.v1", "status": {"provider": provider.name, "status": status.status, "reason": status.reason}},
        )
        return available

    monkeypatch.setattr(Runner, "_preflight_provider", fake_preflight)

    interaction = Runner(tmp_path).run("中文 idea", project_path=project)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "provider_preflight_failed"
    assert "Nexus 已进入通用恢复链路" in interaction["previous_task_output"]
    assert "高强度模型指导" in interaction["previous_task_output"]
    assert "已达到当前恢复链路的自动尝试上限" in interaction["previous_task_output"]
    run_dir = next((tmp_path / ".data" / "runs").iterdir())
    recovery = json.loads((run_dir / "tool_results" / "provider_recovery.json").read_text(encoding="utf-8"))
    assert recovery["actions_applied"] == ["retry_same_provider_preflight"]
    assert recovery["exhausted"] is True


def test_init_project_provider_fallback_recovers_same_run(monkeypatch, tmp_path: Path) -> None:
    broken = NamedAvailableProvider("broken-provider")
    fallback = NamedAvailableProvider("api-fallback")

    def candidates(root, cwd=None, intensity="low"):
        skipped = set(filter(None, (os.environ.get("NEXUS_AUTO_SKIP_PROVIDERS", "") or "").split(",")))
        providers = [broken, fallback]
        return [provider for provider in providers if provider.name not in skipped]

    monkeypatch.setattr("nexus.runner.iter_real_provider_candidates", candidates)

    def fake_preflight(self, store, provider, *, attempts=None, intensity="low"):
        available = provider.name != "broken-provider"
        status = ProviderStatus(provider.name, "available" if available else "needs_config", "smoke ready" if available else "preflight failed")
        if attempts is not None:
            attempts.append(
                {
                    "provider": provider.name,
                    "status": status.status,
                    "reason": status.reason,
                    "stage": "preflight",
                    "intensity": intensity,
                    "issue_type": "" if available else "temporary_preflight_failure",
                }
            )
        return available

    monkeypatch.setattr(Runner, "_preflight_provider", fake_preflight)

    first = Runner(tmp_path).init_project("从零新建一个名为 wljob 的项目", parent=tmp_path, github_sync=False, feishu_sync=False)
    run_id = first["run_id"]

    assert first["previous_task_status"] == "blocked"
    assert first["blocked_reason"] == "provider_preflight_failed"
    assert not (tmp_path / "wljob").exists()

    recovered = Runner(tmp_path).recover(run_id, "跳过 broken-provider fallback 继续")

    assert recovered["run_id"] == run_id
    assert recovered["previous_task_status"] == "blocked"
    assert recovered["blocked_reason"] == "project_root_approval_required"
    assert not (tmp_path / "wljob").exists()

    approved = Runner(tmp_path).approve(run_id, "project-root")

    assert approved["previous_task_status"] == "completed"
    assert (tmp_path / "wljob").exists()
    assert len(list((tmp_path / ".data" / "runs").iterdir())) == 1
    state = json.loads((tmp_path / ".data" / "runs" / run_id / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["current_node"] == "project_root_created"


def test_runtime_provider_usage_limit_blocks_with_fallback_approval_and_recovers_same_run(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    low = NamedAvailableProvider("low-provider")
    broken_high = FailingProvider("codex-cli", "ERROR: You've hit your usage limit. try again later")
    fallback_high = NamedAvailableProvider("qwen3.7max")

    def candidates(root, cwd=None, intensity="low"):
        skipped = set(filter(None, (os.environ.get("NEXUS_AUTO_SKIP_PROVIDERS", "") or "").split(",")))
        if intensity == "high":
            return [provider for provider in [broken_high, fallback_high] if provider.name not in skipped]
        return [low]

    monkeypatch.setattr("nexus.runner.iter_real_provider_candidates", candidates)

    interaction = Runner(tmp_path).run("中文 idea", project_path=project)
    run_id = interaction["run_id"]

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "provider_runtime_failed"
    assert interaction["pending_actions"][0]["kind"] == "provider_fallback_approval"
    assert interaction["pending_actions"][0]["skip_provider"] == "codex-cli"
    assert interaction["pending_actions"][0]["target_provider"] == "qwen3.7max"
    assert "跳过 codex-cli fallback 到 qwen3.7max 并继续" in interaction["next_task_prompt"]

    recovered = Runner(tmp_path).recover(run_id, "跳过 codex-cli fallback 到 qwen3.7max 并继续")

    assert recovered["run_id"] == run_id
    assert recovered["previous_task_status"] == "completed"
    assert len(list((tmp_path / ".data" / "runs").iterdir())) == 1
    chain = json.loads((tmp_path / ".data" / "runs" / run_id / "tool_results" / "provider_fallback_chain.json").read_text(encoding="utf-8"))
    assert chain["schema"] == "nexus.provider_fallback_chain.v1"
    assert chain["skipped_providers"] == ["codex-cli"]
    assert [event["event"] for event in chain["events"]] == [
        "provider_runtime_failed",
        "provider_fallback_approved",
        "provider_fallback_completed",
    ]
    assert chain["events"][0]["failed_provider"] == "codex-cli"
    assert chain["events"][0]["target_provider"] == "qwen3.7max"
    assert chain["events"][-1]["final_provider"] == "qwen3.7max"


def test_multi_step_provider_fallback_chain_preserves_previous_skips(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    low = NamedAvailableProvider("low-provider")
    broken_codex = FailingProvider("codex-cli", "ERROR: You've hit your usage limit. try again later")
    missing_qwen = FailingProvider("qwen3.7max", "HTTP 404: model_not_found")
    good_qwen = NamedAvailableProvider("qwen")

    def candidates(root, cwd=None, intensity="low"):
        skipped = set(filter(None, (os.environ.get("NEXUS_AUTO_SKIP_PROVIDERS", "") or "").split(",")))
        if intensity == "high":
            return [provider for provider in [broken_codex, missing_qwen, good_qwen] if provider.name not in skipped]
        return [low]

    monkeypatch.setattr("nexus.runner.iter_real_provider_candidates", candidates)

    interaction = Runner(tmp_path).run("中文 idea", project_path=project)
    run_id = interaction["run_id"]
    assert interaction["previous_task_status"] == "blocked"
    assert interaction["pending_actions"][0]["skip_provider"] == "codex-cli"
    assert interaction["pending_actions"][0]["target_provider"] == "qwen3.7max"

    first_recovery = Runner(tmp_path).recover(run_id, "跳过 codex-cli fallback 到 qwen3.7max 并继续")
    assert first_recovery["previous_task_status"] == "blocked"
    assert first_recovery["pending_actions"][0]["skip_provider"] == "qwen3.7max"
    assert first_recovery["pending_actions"][0]["target_provider"] == "qwen"

    second_recovery = Runner(tmp_path).recover(run_id, "跳过 qwen3.7max fallback 到 qwen 并继续")
    assert second_recovery["run_id"] == run_id
    assert second_recovery["previous_task_status"] == "completed"
    assert len(list((tmp_path / ".data" / "runs").iterdir())) == 1

    chain = json.loads((tmp_path / ".data" / "runs" / run_id / "tool_results" / "provider_fallback_chain.json").read_text(encoding="utf-8"))
    assert chain["skipped_providers"] == ["codex-cli", "qwen3.7max"]
    events = chain["events"]
    assert [event["event"] for event in events] == [
        "provider_runtime_failed",
        "provider_fallback_approved",
        "provider_runtime_failed",
        "provider_fallback_blocked",
        "provider_fallback_approved",
        "provider_fallback_completed",
    ]
    assert events[2]["failed_provider"] == "qwen3.7max"
    assert events[2]["target_provider"] == "qwen"
    assert events[-1]["final_provider"] == "qwen"


def test_init_project_model_failure_blocks_without_traceback(monkeypatch, tmp_path: Path) -> None:
    failing = FailingProvider("qwen3.7max", "qwen3.7max HTTP 404: model_not_found")
    monkeypatch.setattr(Runner, "_select_provider", lambda self, store, provider_name, project, *, intensity="low", allow_recovery=True: failing)
    monkeypatch.setattr(
        Runner,
        "_recovery_guidance",
        lambda self, store, project, context, playbook_match: {
            "schema": "nexus.failure_recovery_guidance.v1",
            "summary": "模型不存在，需要重新配置 provider。",
            "probable_root_cause": "model_not_found",
            "safe_next_attempts": [],
            "manual_user_actions": [],
            "stop_conditions": [],
            "recommended_actions": [],
        },
    )

    interaction = Runner(tmp_path).init_project("从零新建一个中文互联网 workflow 项目", parent=tmp_path, github_sync=False, feishu_sync=False)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "project_name_model_failed"
    assert "model_not_found" in interaction["previous_task_output"]
    assert "$nexus-workflow" in interaction["next_task_prompt"]
    run_dir = tmp_path / ".data" / "runs" / interaction["run_id"]
    assert (run_dir / "tool_results" / "project_name_model_failure.json").exists()


def test_feishu_doctor_failure_uses_global_recovery_module(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    high = NamedMockProvider("high-provider")

    monkeypatch.setattr("nexus.runner.iter_real_provider_candidates", lambda root, cwd=None, intensity="low": [high])
    monkeypatch.setattr(
        "nexus.runner.run_feishu_doctor",
        lambda project_path, no_network=False: {
            "schema": "nexus.feishu_doctor.v1",
            "status": "blocked",
            "reason": "feishu_credentials_missing",
            "checks": {"app_id_loaded": False, "app_secret_loaded": False},
            "diagnostics": ["missing credentials"],
        },
    )

    interaction = Runner(tmp_path).feishu_doctor_flow(project)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "feishu_credentials_missing"
    assert "Nexus 已进入全局恢复模块" in interaction["previous_task_output"]
    run_dir = next((tmp_path / ".data" / "runs").iterdir())
    recovery = json.loads((run_dir / "tool_results" / "recovery_result.json").read_text(encoding="utf-8"))
    assert recovery["context"]["module"] == "feishu"
    assert recovery["context"]["node"] == "feishu_doctor"


def test_online_search_blocks_without_approval(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    def fake_model_node(self, store, provider, node_id, schema_key, prompt):
        if schema_key == "intent_route":
            return {
                "schema": "nexus.intent_route.v1",
                "resolved_route": "whole_project_discovery",
                "confidence": 0.9,
                "reason": "test",
                "project_mode": "existing",
                "discovery_target": "whole-project",
                "generalization_note": "test",
                "completion_assessment": {"state": "planned", "weak_points": []},
                "next_options": [{"id": "continue", "label": "继续", "description": "继续"}],
            }
        if schema_key == "task_block":
            return {"schema": "nexus.task_block.v1", "goal": "g", "constraints": [], "target_project": "p", "success_criteria": []}
        if schema_key == "research_plan":
            return {"schema": "nexus.research_plan.v1", "source_families": ["github_repo"], "queries": ["workflow kernel"], "coverage_gates": [], "stop_conditions": []}
        if schema_key == "search_plan":
            return {
                "schema": "nexus.search_plan.v1",
                "round_no": 1,
                "source_plan": [{"source": "github_repo", "priority": "high", "queries": ["workflow kernel"], "reason": "online"}],
                "requires_online": True,
                "coverage_gates": ["github"],
                "stop_conditions": [],
            }
        if schema_key == "coverage_review":
            return {
                "schema": "nexus.coverage_review.v1",
                "round_no": 1,
                "coverage_state": "partial",
                "covered_facets": [],
                "missing_facets": ["github"],
                "source_failures": [],
                "quality_notes": [],
                "should_continue": True,
                "recommended_next_sources": ["github_repo"],
                "recommended_next_queries": ["workflow kernel"],
            }
        if schema_key == "stop_decision":
            return {"schema": "nexus.stop_decision.v1", "round_no": 1, "should_continue": True, "reason": "need online", "confidence": "medium", "next_action": "continue_search"}
        raise AssertionError(schema_key)

    monkeypatch.setattr("nexus.runner.Runner._model_node_with_schema", fake_model_node)

    interaction = Runner(tmp_path).run("需要在线检索", project_path=project, provider_name="mock")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "online_search_approval_required"
    run_dir = next((tmp_path / ".data" / "runs").iterdir())
    assert (run_dir / "approvals" / "online_search_required.json").exists()
    assert (run_dir / "search_rounds" / "round_1" / "source_status.json").exists()


def test_approve_online_search_then_resume_executes_adapter(monkeypatch, tmp_path: Path) -> None:
    from nexus.tools.search_models import CandidateRecord

    project = tmp_path / "project"
    project.mkdir()

    def fake_model_node(self, store, provider, node_id, schema_key, prompt):
        approved = store.path("approvals", "APPROVED_online-search.json").exists()
        if schema_key == "intent_route":
            return {
                "schema": "nexus.intent_route.v1",
                "resolved_route": "whole_project_discovery",
                "confidence": 0.9,
                "reason": "test",
                "project_mode": "existing",
                "discovery_target": "whole-project",
                "generalization_note": "test",
                "completion_assessment": {"state": "planned", "weak_points": []},
                "next_options": [{"id": "continue", "label": "继续", "description": "继续"}],
            }
        if schema_key == "task_block":
            return {"schema": "nexus.task_block.v1", "goal": "g", "constraints": [], "target_project": "p", "success_criteria": []}
        if schema_key == "research_plan":
            return {"schema": "nexus.research_plan.v1", "source_families": ["github_repo"], "queries": ["workflow kernel"], "coverage_gates": [], "stop_conditions": []}
        if schema_key == "search_plan":
            return {
                "schema": "nexus.search_plan.v1",
                "round_no": 1,
                "source_plan": [{"source": "github_repo", "priority": "high", "queries": ["workflow kernel"], "reason": "online"}],
                "requires_online": True,
                "coverage_gates": ["github"],
                "stop_conditions": [],
            }
        if schema_key == "coverage_review":
            return {
                "schema": "nexus.coverage_review.v1",
                "round_no": 1,
                "coverage_state": "enough" if approved else "partial",
                "covered_facets": ["github"] if approved else [],
                "missing_facets": [] if approved else ["github"],
                "source_failures": [],
                "quality_notes": [],
                "should_continue": not approved,
                "recommended_next_sources": [],
                "recommended_next_queries": [],
            }
        if schema_key == "stop_decision":
            return {"schema": "nexus.stop_decision.v1", "round_no": 1, "should_continue": False, "reason": "done", "confidence": "high", "next_action": "final_review"}
        if schema_key == "candidate_review":
            return {
                "schema": "nexus.candidate_review.v1",
                "reviews": [{"candidate_id": "cand-test", "score": 0.9, "reason": "ok", "risks": [], "recommended_use": "reference"}],
            }
        if schema_key == "candidate_localization_review":
            return {
                "schema": "nexus.candidate_localization_review.v1",
                "reviews": [
                    {
                        "candidate_id": "cand-test",
                        "chinese_web_access": "good",
                        "documentation_language": ["en"],
                        "domestic_platform_presence": {
                            "gitee": False,
                            "official_zh_docs": False,
                            "cn_blog_evidence": False,
                            "package_mirror": "unknown",
                        },
                        "availability_summary": "reachable",
                        "risk_level": "low",
                        "recommendation": "accept",
                        "reason": "ok",
                        "evidence_refs": [],
                    }
                ],
            }
        if schema_key == "risk_analysis":
            return {"schema": "nexus.risk_analysis.v1", "risks": [], "blocked_actions": [], "approval_required": False}
        if schema_key == "final_report":
            return {"schema": "nexus.final_report.v1", "summary": "done", "findings": ["github done"], "next_action_plan": ["review"]}
        raise AssertionError(schema_key)

    def fake_gh(self, queries, raw_dir=None):
        del raw_dir
        record = CandidateRecord(
            id="cand-test",
            title="example/workflow-kernel",
            summary="workflow kernel",
            source="github_repo",
            url="https://github.com/example/workflow-kernel",
            retrieval_mode="online-readonly",
        )
        record.merge_query(queries[0])
        return [record]

    monkeypatch.setattr("nexus.runner.Runner._model_node_with_schema", fake_model_node)
    monkeypatch.setattr("nexus.tools.search_adapters.GithubRepoAdapter._search_gh", fake_gh)

    runner = Runner(tmp_path)
    first = runner.run("需要在线检索", project_path=project, provider_name="mock")
    run_id = first["run_id"]
    assert first["blocked_reason"] == "online_search_approval_required"
    approved = runner.approve(str(run_id), "online-search")
    assert approved["blocked_reason"] == "resume_required_after_online_search_approval"
    resumed = runner.resume(str(run_id))

    assert resumed["previous_task_status"] == "completed"
    run_dir = tmp_path / ".data" / "runs" / str(run_id)
    assert "github_repo" in (run_dir / "tool_results" / "source_status.json").read_text(encoding="utf-8")
    assert "example/workflow-kernel" in (run_dir / "candidates" / "all_candidates.jsonl").read_text(encoding="utf-8")


def test_approve_closes_implementation_plan_block(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = Runner(tmp_path)
    runner.run("请调研后生成 implementation plan", project_path=project, provider_name="mock")
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    run_dir = tmp_path / ".data" / "runs" / run_id
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    # MockProvider normally does not request approval; force the approval state to test the command path.
    state["status"] = "blocked"
    state["blocked_reason"] = "approval_required"
    state["provider"] = "mock"
    (run_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    interaction = runner.approve(run_id, "implementation-plan")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "code_change_approval_required"
    assert (run_dir / "approvals" / "APPROVED_implementation-plan.json").exists()
    assert (run_dir / "reports" / "implementation_plan.md").exists()


def test_approve_generates_implementation_plan_from_completed_run(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = Runner(tmp_path)
    runner.run("中文 idea", project_path=project, provider_name="mock")
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name

    interaction = runner.approve(run_id, "implementation-plan")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "code_change_approval_required"
    assert (tmp_path / ".data" / "runs" / run_id / "reports" / "implementation_plan.md").exists()


def test_next_options_written_after_discovery(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = Runner(tmp_path)
    runner.run("中文 idea", project_path=project, provider_name="mock")
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    run_dir = tmp_path / ".data" / "runs" / run_id

    payload = json.loads((run_dir / "reports" / "next_options.json").read_text(encoding="utf-8"))

    assert payload["schema"] == "nexus.next_options.v1"
    assert payload["current_state"]["stage"] == "discovery_completed"
    assert {item["id"] for item in payload["next_options"]} >= {"implementation_plan", "local_research", "rerun_research", "update_intent", "chunked_research"}


def test_build_decision_research_writes_branch_reports_and_workflow_options(tmp_path: Path) -> None:
    project = tmp_path / "probe"
    project.mkdir()
    _write_build_decision_intent(project)
    runner = Runner(tmp_path)

    interaction = runner.run("调研项目初始化方案", project_path=project, provider_name="mock")

    assert interaction["previous_task_status"] == "completed"
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    run_dir = tmp_path / ".data" / "runs" / run_id
    contract = json.loads((run_dir / "reports" / "research_contract.json").read_text(encoding="utf-8"))
    next_options = json.loads((run_dir / "reports" / "next_options.json").read_text(encoding="utf-8"))
    final_report = (run_dir / "reports" / "final_report.md").read_text(encoding="utf-8")
    next_options_md = (run_dir / "reports" / "next_options.md").read_text(encoding="utf-8")

    assert contract["mode"] == "build_decision"
    assert contract["requires_branch_reports"] is True
    assert (run_dir / "reports" / "branch_existing_wheel.md").exists()
    assert (run_dir / "reports" / "branch_subproject_wheels.md").exists()
    assert (run_dir / "reports" / "branch_from_scratch.md").exists()
    assert (run_dir / "reports" / "decision_matrix.md").exists()
    assert "branch_existing_wheel.md" in final_report
    assert "branch_subproject_wheels.md" in final_report
    assert "branch_from_scratch.md" in final_report
    assert {item["id"] for item in next_options["next_options"]} >= {"branch_existing_wheel", "branch_subproject_wheels", "branch_from_scratch", "rerun_research", "update_intent"}
    assert "implementation_plan" not in {item["id"] for item in next_options["next_options"]}
    assert "$nexus-workflow" in next_options_md


def test_subproject_route_takes_precedence_over_existing_wheel_keywords() -> None:
    branch_id = branch_id_from_route(
        "select_subproject_wheels",
        "分块现成轮子调研：resume_revision, interview_assistant",
        "分块调研：resume_revision, interview_assistant",
    )

    assert branch_id == "subproject_wheel_research"


def test_build_decision_blocks_implementation_until_branch_selected(tmp_path: Path) -> None:
    project = tmp_path / "probe"
    project.mkdir()
    _write_build_decision_intent(project)
    runner = Runner(tmp_path)
    runner.run("调研项目初始化方案", project_path=project, provider_name="mock")
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    run_dir = tmp_path / ".data" / "runs" / run_id

    blocked = runner.continue_run(run_id, "生成项目计划", provider_name="mock")

    assert blocked["previous_task_status"] == "blocked"
    assert blocked["blocked_reason"] == "research_branch_selection_required"
    assert (run_dir / "reports" / "implementation_plan_blocked.json").exists()
    assert not (run_dir / "reports" / "implementation_plan.md").exists()

    selected = runner.continue_run(run_id, "从零搭建方案", provider_name="mock")

    assert selected["previous_task_status"] == "blocked"
    assert selected["blocked_reason"] == "implementation_plan_required"
    selection = json.loads((run_dir / "reports" / "selected_research_branch.json").read_text(encoding="utf-8"))
    assert selection["branch_id"] == "from_scratch_build"

    planned = runner.continue_run(run_id, "生成项目计划", provider_name="mock")

    assert planned["previous_task_status"] == "blocked"
    assert planned["blocked_reason"] == "code_change_approval_required"
    assert (run_dir / "reports" / "implementation_plan.md").exists()


def test_build_decision_high_risk_does_not_skip_branch_selection(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "probe"
    project.mkdir()
    _write_build_decision_intent(project)
    monkeypatch.setattr("nexus.runner.detect_high_risk_actions", lambda text: ["submit_form"])

    interaction = Runner(tmp_path).run("调研项目初始化方案", project_path=project, provider_name="mock")

    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    run_dir = tmp_path / ".data" / "runs" / run_id
    assert interaction["previous_task_status"] == "completed"
    assert "选择已有轮子方案" in interaction["next_task_prompt"]
    assert not (run_dir / "approvals" / "implementation_plan_required.json").exists()
    assert (run_dir / "reports" / "branch_existing_wheel.md").exists()


def test_continue_routes_to_implementation_plan(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = Runner(tmp_path)
    runner.run("中文 idea", project_path=project, provider_name="mock")
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name

    interaction = runner.continue_run(run_id, "生成项目计划", provider_name="mock")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "code_change_approval_required"
    assert (tmp_path / ".data" / "runs" / run_id / "reports" / "implementation_plan.md").exists()
    assert (tmp_path / ".data" / "runs" / run_id / "reports" / "continue_intent_route.json").exists()


def test_execute_code_change_uses_codex_profile_model(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# project\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "initial"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )

    runner = Runner(tmp_path)
    runner.run("中文 idea", project_path=project, provider_name="mock")
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    runner.continue_run(run_id, "生成项目计划", provider_name="mock")
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True, text=True)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=project, check=False)
    if staged.returncode != 0:
        subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "nexus planning"],
            cwd=project,
            check=True,
            capture_output=True,
            text=True,
        )

    captured: dict[str, list[str]] = {}
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "/usr/bin/codex-fake":
            captured["cmd"] = list(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr("nexus.runner.shutil.which", lambda name: "/usr/bin/codex-fake" if name == "codex" else None)
    monkeypatch.setattr("nexus.runner.subprocess.run", fake_run)

    runner.approve(run_id, "code-change")
    interaction = runner.execute_code_change(run_id, provider_name="codex-cli")

    model_index = captured["cmd"].index("--model") + 1
    assert captured["cmd"][model_index] == "gpt-5.4"
    assert interaction["blocked_reason"] == "empty_diff"


def test_continue_routes_to_update_intent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = Runner(tmp_path)
    runner.run("中文 idea", project_path=project, provider_name="mock")
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name

    interaction = runner.continue_run(run_id, "更新项目意图：目标改成中文互联网优先 workflow kernel", provider_name="mock")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "next_step_required"
    assert (tmp_path / ".data" / "runs" / run_id / "reports" / "updated_intent.json").exists()


def test_continue_intent_routing_and_update_use_high_provider(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    low = NamedMockProvider("low-provider")
    high = NamedMockProvider("high-provider")

    monkeypatch.setattr("nexus.runner.iter_real_provider_candidates", lambda root, cwd=None, intensity="low": [high] if intensity == "high" else [low])

    runner = Runner(tmp_path)
    runner.run("中文 idea", project_path=project)
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name

    interaction = runner.continue_run(run_id, "更新项目意图：目标改成中文互联网优先 workflow kernel")

    assert interaction["previous_task_status"] == "blocked"
    run_dir = tmp_path / ".data" / "runs" / run_id
    route_nodes = list((run_dir / "nodes").glob("continue_intent_route_*"))
    assert route_nodes
    route_status = json.loads((route_nodes[0] / "status.json").read_text(encoding="utf-8"))
    updated_status = json.loads((run_dir / "nodes" / "updated_intent" / "status.json").read_text(encoding="utf-8"))
    assert route_status["provider"] == "high-provider"
    assert updated_status["provider"] == "high-provider"


def test_continue_routes_to_chunked_research(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = Runner(tmp_path)
    runner.run("中文 idea", project_path=project, provider_name="mock")
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name

    interaction = runner.continue_run(run_id, "分块调研：provider/search/runner", provider_name="mock")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "chunk_selection_required"
    assert (tmp_path / ".data" / "runs" / run_id / "reports" / "chunked_research_plan.md").exists()


def test_continue_latest_run(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = Runner(tmp_path)
    runner.run("中文 idea", project_path=project, provider_name="mock")

    interaction = runner.continue_run("latest", "生成项目计划", provider_name="mock")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "code_change_approval_required"


def test_board_show_uses_active_project_context(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = Runner(tmp_path)
    runner.run("中文 idea", project_path=project, provider_name="mock")

    interaction = runner.board_show()

    assert interaction["previous_task_status"] == "completed"
    assert "当前状态" in interaction["previous_task_output"]
    assert "$nexus-workflow" in interaction["next_task_prompt"]
    assert "python -m nexus.cli" not in interaction["next_task_prompt"]


def test_board_point_keeps_recent_five_points(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = Runner(tmp_path)

    for index in range(7):
        runner.board_point(f"point-{index}", project_path=project)

    board = json.loads((project / ".nexus" / "board.json").read_text(encoding="utf-8"))
    assert [item["text"] for item in board["points"]] == ["point-6", "point-5", "point-4", "point-3", "point-2"]


def test_board_invoke_routes_point_without_project_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    runner = Runner(tmp_path)
    runner.run("中文 idea", project_path=project, provider_name="mock")

    interaction = runner.invoke("记一个记录点：之后要做前端")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"]
    assert "之后要做前端" in (project / ".nexus" / "board.md").read_text(encoding="utf-8")


def test_board_blocks_without_project_context(tmp_path: Path) -> None:
    interaction = Runner(tmp_path).board_show()

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "active_project_not_found"
