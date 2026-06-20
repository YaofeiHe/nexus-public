from __future__ import annotations

import json
from pathlib import Path

import nexus.cli as nexus_cli
from nexus.cli import main


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_status_and_report_commands(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()
    assert main(["--root", str(tmp_path), "run", "中文 idea", "--project-path", str(project), "--provider", "mock"]) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    assert main(["--root", str(tmp_path), "status", run_id]) == 0
    assert '"status": "completed"' in capsys.readouterr().out
    assert main(["--root", str(tmp_path), "report", run_id]) == 0
    assert "Nexus Discovery Report" in capsys.readouterr().out


def test_approve_command(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()
    assert main(["--root", str(tmp_path), "run", "中文 idea", "--project-path", str(project), "--provider", "mock"]) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    state_path = tmp_path / ".data" / "runs" / run_id / "state.json"
    import json

    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "blocked"
    state["blocked_reason"] = "approval_required"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    assert main(["--root", str(tmp_path), "approve", run_id, "implementation-plan"]) == 0
    assert "已记录 implementation-plan 审批" in capsys.readouterr().out


def test_approve_and_continue_command_routes_to_runner(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(
        nexus_cli.Runner,
        "approve_and_continue",
        lambda self, run_id, stage: {
            "previous_task_status": "completed",
            "previous_task_output": f"continued: {run_id}:{stage}",
            "next_task_prompt": "done",
            "run_id": run_id,
        },
    )

    assert main(["--root", str(tmp_path), "approve-and-continue", "run_test", "online-search"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：completed" in out
    assert "continued: run_test:online-search" in out


def test_continue_after_input_command_routes_to_runner(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(
        nexus_cli.Runner,
        "continue_after_input",
        lambda self, run_id, note="": {
            "previous_task_status": "completed",
            "previous_task_output": f"continued after input: {run_id}:{note}",
            "next_task_prompt": "done",
            "run_id": run_id,
        },
    )

    assert main(["--root", str(tmp_path), "continue-after-input", "run_test", "--note", "done"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：completed" in out
    assert "continued after input: run_test:done" in out


def test_recover_command_routes_to_runner(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(
        nexus_cli.Runner,
        "recover",
        lambda self, run_id="latest", request="": {
            "previous_task_status": "completed",
            "previous_task_output": f"recovered: {run_id}:{request}",
            "next_task_prompt": "done",
            "run_id": run_id,
        },
    )

    assert main(["--root", str(tmp_path), "recover", "latest", "恢复 GitHub 初始化"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：completed" in out
    assert "recovered: latest:恢复 GitHub 初始化" in out


def test_handoff_for_debug_command_routes_to_runner(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(
        nexus_cli.Runner,
        "handoff_for_debug",
        lambda self, run_id="latest", reason="": {
            "previous_task_status": "blocked",
            "previous_task_output": f"handoff: {run_id}:{reason}",
            "next_task_prompt": "done",
            "run_id": run_id,
        },
    )

    assert main(["--root", str(tmp_path), "handoff-for-debug", "latest", "修复 provider preflight"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：blocked" in out
    assert "handoff: latest:修复 provider preflight" in out


def test_append_debug_worklog_command_routes_to_runner(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(
        nexus_cli.Runner,
        "append_debug_worklog",
        lambda self, run_id="latest", handoff_id="", kind="diagnose", summary="", result="", command="", paths=None: {
            "previous_task_status": "blocked",
            "previous_task_output": f"worklog: {run_id}:{handoff_id}:{kind}:{summary}:{command}:{paths}",
            "next_task_prompt": "done",
            "run_id": run_id,
        },
    )

    assert main(
        [
            "--root",
            str(tmp_path),
            "append-debug-worklog",
            "latest",
            "--handoff-id",
            "debug-1",
            "--kind",
            "test",
            "--summary",
            "pytest ok",
            "--command",
            "python -m pytest -q",
            "--path",
            "nexus/runner.py",
        ]
    ) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：blocked" in out
    assert "worklog: latest:debug-1:test:pytest ok:python -m pytest -q:['nexus/runner.py']" in out


def test_rebind_and_continue_command_routes_to_runner(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(
        nexus_cli.Runner,
        "rebind_and_continue",
        lambda self, run_id="latest", handoff_id="": {
            "previous_task_status": "completed",
            "previous_task_output": f"rebound: {run_id}:{handoff_id}",
            "next_task_prompt": "done",
            "run_id": run_id,
        },
    )

    assert main(["--root", str(tmp_path), "rebind-and-continue", "latest", "--handoff-id", "debug-1"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：completed" in out
    assert "rebound: latest:debug-1" in out


def test_nexus_verix_monitor_cli_dry_run_declares_monitor_multi_agent(tmp_path: Path, capsys) -> None:
    assert main(
        [
            "--root",
            str(REPO_ROOT),
            "nexus-verix-monitor",
            "--dry-run-plan",
            "--e2e-root",
            str(tmp_path),
            "--run-id",
            "cli-dry-run",
        ]
    ) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["schema"] == "nexus.lab.monitor_multi_agent_plan.v1"
    assert plan["execution_model"] == "monitor_multi_agent_v1"
    assert plan["spawn_source"] == "current_codex_conversation_multi_agent_v1"
    assert plan["python_embeds_subagents"] is False
    assert plan["operator_command"] == "启动运行"
    assert "nexus_execution" in plan["agents"]
    assert "skill_replay" in plan["agents"]


def test_nexus_verix_monitor_cli_init_run_writes_launch_packet(tmp_path: Path, capsys) -> None:
    assert main(
        [
            "--root",
            str(REPO_ROOT),
            "nexus-verix-monitor",
            "--init-run",
            "--e2e-root",
            str(tmp_path),
            "--run-id",
            "cli-init",
        ]
    ) == 0
    packet = json.loads(capsys.readouterr().out)
    state_path = Path(packet["state_path"])
    assert packet["schema"] == "nexus.lab.monitor_multi_agent_launch_packet.v1"
    assert packet["spawn_source"] == "current_codex_conversation_only"
    assert packet["spawn_order"] == [
        "nexus_execution",
        "skill_replay",
        "verix_audit",
        "nexus_modification",
        "state_audit",
    ]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["execution_model"] == "monitor_multi_agent_v1"
    assert state["phase"] == "awaiting_monitor_spawn"
    assert state["main_flow"]["next_monitor_action"] == "spawn_agents"


def test_debug_status_command_routes_to_runner(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(
        nexus_cli.Runner,
        "debug_status",
        lambda self, run_id="latest": {
            "previous_task_status": "blocked",
            "previous_task_output": f"debug status: {run_id}",
            "next_task_prompt": "done",
            "run_id": run_id,
        },
    )

    assert main(["--root", str(tmp_path), "debug-status", "latest"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：blocked" in out
    assert "debug status: latest" in out


def test_configure_writes_local_config_only(tmp_path: Path, capsys) -> None:
    assert main(["--root", str(tmp_path), "configure"]) == 0
    assert (tmp_path / ".data" / "config" / "provider.json").exists()
    out = capsys.readouterr().out
    assert "未读取密钥" in out


def test_model_intent_status_prompt(tmp_path: Path, capsys) -> None:
    assert main(["--root", str(tmp_path), "model", "intent", "使用 $nexus-workflow 更换模型"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：blocked" in out
    assert "低强度实际顺序：API" in out
    assert "codex-cli gpt-5.4 -> codex-mcp" in out
    assert "暂未提供" in out
    assert "配置 qwen 模型" in out


def test_model_intent_use_codexcli_sets_default(tmp_path: Path, capsys) -> None:
    assert main(["--root", str(tmp_path), "model", "intent", "使用 $nexus-workflow 使用 codexcli 模型"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：completed" in out
    assert "codex-cli" in out
    assert (tmp_path / ".data" / "session" / "current_model_profile.json").exists()


def test_model_intent_use_qwen_prompts_for_config(tmp_path: Path, capsys) -> None:
    assert main(["--root", str(tmp_path), "model", "intent", "使用 $nexus-workflow 使用 qwen 模型"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：blocked" in out
    assert "qwen 尚未配置" in out
    assert "apikey 文件" in out


def test_model_intent_configures_qwen_key_file(tmp_path: Path, capsys) -> None:
    key_path = tmp_path / "qwen-key"
    key_path.write_text("secret-value", encoding="utf-8")
    request = f"使用 $nexus-workflow 配置 qwen 模型，模型名 qwen-plus，base_url https://dashscope.aliyuncs.com/compatible-mode/v1，apikey 文件 {key_path}"
    assert main(["--root", str(tmp_path), "model", "intent", request]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：completed" in out
    assert "qwen-plus" in out
    assert "secret-value" not in out
    profiles_path = tmp_path / ".data" / "config" / "models" / "profiles.json"
    assert profiles_path.exists()
    payload = profiles_path.read_text(encoding="utf-8")
    assert str(key_path) not in payload
    assert ".data/config/models/secrets/qwen-plus.api_key" in payload
    assert "secret-value" not in payload


def test_model_intent_unsupported_provider(tmp_path: Path, capsys) -> None:
    assert main(["--root", str(tmp_path), "model", "intent", "使用 $nexus-workflow 使用 groq 模型"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：blocked" in out
    assert "暂未提供 adapter" in out
    assert "codex-cli gpt-5.4" in out


def test_model_intent_configures_intensity_slots(tmp_path: Path, capsys) -> None:
    key_path = tmp_path / "qwen-key"
    key_path.write_text("secret-value", encoding="utf-8")
    request = f"使用 $nexus-workflow 配置模型：低强度 API 使用 qwen-plus，高强度 API 使用 qwen3.7max，apikey 来自 {key_path}，链接是 https://dashscope.aliyuncs.com/compatible-mode/v1。"
    assert main(["--root", str(tmp_path), "model", "intent", request]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：completed" in out
    assert "低强度 API 槽位：qwen-plus" in out
    assert "高强度 API fallback 槽位：qwen3.7max" in out
    assert "codex-cli gpt-5.4" in out
    assert "secret-value" not in out
    profiles_path = tmp_path / ".data" / "config" / "models" / "profiles.json"
    intensity_path = tmp_path / ".data" / "config" / "models" / "intensity.json"
    assert profiles_path.exists()
    assert intensity_path.exists()
    profiles_payload = profiles_path.read_text(encoding="utf-8")
    assert str(key_path) not in profiles_payload
    assert ".data/config/models/secrets/qwen-plus.api_key" in profiles_payload
    assert ".data/config/models/secrets/qwen3.7max.api_key" in profiles_payload
    profiles = json.loads(profiles_payload)["profiles"]
    assert profiles["qwen-plus"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert profiles["qwen3.7max"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    intensity = json.loads(intensity_path.read_text(encoding="utf-8"))
    assert intensity["low_api_profile"] == "qwen-plus"
    assert intensity["high_api_profile"] == "qwen3.7max"


def test_model_status_cli_writes_standard_run_artifacts(tmp_path: Path, capsys) -> None:
    assert main(["--root", str(tmp_path), "model", "status"]) == 0

    out = capsys.readouterr().out
    assert "上一任务状态：completed" in out
    assert "run_id：" in out
    runs = sorted((tmp_path / ".data" / "runs").iterdir())
    run_dir = runs[-1]
    assert (run_dir / "tool_results" / "model_status.json").exists()
    assert (run_dir / "tool_results" / "provider_status.json").exists()


def test_board_cli_outputs_standard_interaction(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".nexus").mkdir()
    (project / ".nexus" / "feishu-autosync.json").write_text('{"schema":"nexus.feishu_autosync.v1","enabled":false}\n', encoding="utf-8")

    assert main(["--root", str(tmp_path), "board", "point", "--project-path", str(project), "之后要做前端"]) == 0
    out = capsys.readouterr().out

    assert "上一任务状态：completed" in out
    assert "上一任务输出：" in out
    assert "下一任务提示：" in out
    assert "$nexus-workflow 查看项目" in out
    assert "之后要做前端" in (project / ".nexus" / "board.md").read_text(encoding="utf-8")


def test_guide_generate_cli_writes_operation_guide(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()

    assert main(["--root", str(tmp_path), "guide", "generate", "--project-path", str(project), "--target", "project", "--no-feishu-sync"]) == 0
    out = capsys.readouterr().out

    assert "上一任务状态：completed" in out
    assert "整体操作指南" in out
    assert (project / "docs" / "operation-guide.md").exists()


def test_system_showcase_generate_cli_writes_architecture(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()

    assert main(["--root", str(tmp_path), "system-showcase", "generate", "--project-path", str(project), "--no-feishu-sync"]) == 0
    out = capsys.readouterr().out

    assert "上一任务状态：completed" in out
    assert "已生成系统架构展示" in out
    assert (project / "docs" / "system" / "architecture.md").exists()


def test_self_sync_cli_routes_to_runner(monkeypatch, tmp_path: Path, capsys) -> None:
    project = tmp_path / "nexus"
    project.mkdir()

    monkeypatch.setattr(
        nexus_cli.Runner,
        "self_sync",
        lambda self, project_path, *, target="auto", feishu_sync_enabled=True: {
            "previous_task_status": "completed",
            "previous_task_output": f"self sync ok: {project_path}",
            "next_task_prompt": "done",
            "run_id": "run_test",
        },
    )

    assert main(["--root", str(tmp_path), "self-sync", "--project-path", str(project), "--target", "nexus"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：completed" in out
    assert "self sync ok" in out


def test_supplement_init_cli_routes_to_runner(monkeypatch, tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setattr(
        nexus_cli.Runner,
        "supplemental_init",
        lambda self, project_path, *, idea="补充初始化", target="auto", github_private_enabled=True, feishu_sync_enabled=True: {
            "previous_task_status": "completed",
            "previous_task_output": f"supplement ok: {project_path}:{idea}:{target}:{github_private_enabled}:{feishu_sync_enabled}",
            "next_task_prompt": "done",
            "run_id": "run_test",
        },
    )

    assert main(["--root", str(tmp_path), "supplement-init", "--project-path", str(project), "--idea", "补充初始化", "--target", "project", "--no-github-sync", "--no-feishu-sync"]) == 0
    out = capsys.readouterr().out
    assert "上一任务状态：completed" in out
    assert "supplement ok" in out
