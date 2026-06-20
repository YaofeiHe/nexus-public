from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from nexus.board import load_board, update_board
from nexus.cli import main
from nexus import runner as nexus_runner
from nexus.project_docs import write_project_docs
from nexus.runner import Runner, _validate_project_name_candidates


def test_board_keeps_five_recent_points(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    update_board(project, status="开发中")
    for index in range(7):
        update_board(project, point=f"point-{index}")
    board = load_board(project)
    assert board["current_status"] == "开发中"
    assert len(board["points"]) == 5
    assert board["points"][0]["text"] == "point-6"
    assert (project / ".nexus" / "board.md").exists()


def test_init_project_requires_approval_then_creates_dir(tmp_path: Path, capsys) -> None:
    assert main(["--root", str(tmp_path), "init-project", "创建一个中文互联网 workflow kernel，不同步 GitHub", "--parent", str(tmp_path), "--provider", "mock"]) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    approval = json.loads((tmp_path / ".data" / "runs" / run_id / "approvals" / "project_root_required.json").read_text(encoding="utf-8"))
    target = Path(approval["target_path"])
    if target.exists():
        # The mock recommendation is stable; keep the test isolated if another test left it around.
        import shutil

        shutil.rmtree(target)
    assert main(["--root", str(tmp_path), "approve", run_id, "project-root"]) == 0
    assert target.exists()
    assert (target / ".nexus" / "board.json").exists()
    assert "已创建项目目录" in capsys.readouterr().out


def test_init_project_defaults_to_github_private_and_public_staging_only(monkeypatch, tmp_path: Path, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_bootstrap(project: Path, config: dict[str, object], *, create_remote_repos: bool = True, commit_message: str = "") -> dict[str, object]:
        captured["project"] = project
        captured["config"] = config
        return {"schema": "nexus.github_bootstrap.v1", "status": "completed", "reason": "bootstrapped_and_pushed_private"}

    def fake_prepare_public(project: Path, config: dict[str, object], staging: Path) -> dict[str, object]:
        captured["public_staging"] = staging
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "README.md").write_text(
            "\n".join(
                [
                    "# public",
                    "",
                    "<!-- nexus:public-install -->",
                    "## Public Install",
                    "",
                    f"python -m pip install git+https://github.com/{config['public_repo']}.git",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return {"schema": "nexus.github_public_staging.v1", "status": "completed", "staging": str(staging), "scan": {"findings": []}, "copied": ["docs/"], "blocked": []}

    def fake_sync_public(project: Path, config: dict[str, object], staging: Path) -> dict[str, object]:
        raise AssertionError("project-root approval must not authorize public push")

    monkeypatch.setattr(nexus_runner, "bootstrap_project", fake_bootstrap)
    monkeypatch.setattr(nexus_runner, "prepare_public_staging", fake_prepare_public)
    monkeypatch.setattr(nexus_runner, "sync_public", fake_sync_public)
    assert main(["--root", str(tmp_path), "init-project", "创建一个中文互联网 workflow kernel", "--parent", str(tmp_path), "--provider", "mock"]) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    approval = json.loads((tmp_path / ".data" / "runs" / run_id / "approvals" / "project_root_required.json").read_text(encoding="utf-8"))
    target = Path(approval["target_path"])
    if target.exists():
        import shutil

        shutil.rmtree(target)

    assert approval["github_sync"] is True
    assert approval["initial_public_sync"] is False
    assert approval["initial_public_staging"] is True
    assert approval["public_sync_requires_project_root_approval"] is False
    assert approval["public_sync_requires_explicit_confirmation"] is True
    assert "不授权 public push" in approval["public_risk_summary"]
    assert main(["--root", str(tmp_path), "approve", run_id, "project-root"]) == 0
    config = captured["config"]
    assert isinstance(config, dict)
    assert config["private_repo"] == f"YaofeiHe/{target.name}"
    assert config["public_repo"] == f"YaofeiHe/{target.name}-public"
    assert "public_sync" not in captured
    intent = json.loads((target / ".nexus" / "project-intent.json").read_text(encoding="utf-8"))
    assert intent["project_name_context"]["selected_name"] == target.name
    assert intent["project_name_context"]["meaning"]
    assert "真实英文五字母单词" in intent["project_name_context"]["word_validation"]
    normalized = (target / "docs" / "intent" / "normalized-requirement.md").read_text(encoding="utf-8")
    assert "## 项目命名说明" in normalized
    assert "真实单词校验" in normalized
    output = capsys.readouterr().out
    assert "GitHub public 首次同步：blocked" in output
    assert "$nexus-workflow 确认将项目" in output
    assert "GitHub public" in output


def test_init_project_initial_public_staging_reuses_public_flow(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_bootstrap(project: Path, config: dict[str, object], *, create_remote_repos: bool = True, commit_message: str = "") -> dict[str, object]:
        return {"schema": "nexus.github_bootstrap.v1", "status": "completed", "reason": "bootstrapped_and_pushed_private"}

    def fake_public_flow(self: Runner, store, project: Path, *, confirm: bool = False, continuation_note: str = "") -> dict[str, object]:
        captured["project"] = project
        captured["confirm"] = confirm
        captured["continuation_note"] = continuation_note
        artifact = store.write_json("tool_results/github_public_sync.json", {"schema": "nexus.github_public_sync.v1", "status": "completed", "reason": "pushed_public"})
        return {
            "schema": "nexus.interaction.v1",
            "run_id": store.run_id,
            "previous_task_status": "completed",
            "previous_task_output": "GitHub public 同步结果：pushed_public",
            "next_task_prompt": "查看 tool_results/github_public_sync.json。",
            "blocked_reason": "",
            "artifact_refs": [str(artifact)],
        }

    monkeypatch.setattr(nexus_runner, "bootstrap_project", fake_bootstrap)
    monkeypatch.setattr(Runner, "_github_public_flow", fake_public_flow)

    assert main(["--root", str(tmp_path), "init-project", "创建一个中文互联网 workflow kernel", "--parent", str(tmp_path), "--provider", "mock"]) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    approval = json.loads((tmp_path / ".data" / "runs" / run_id / "approvals" / "project_root_required.json").read_text(encoding="utf-8"))
    target = Path(approval["target_path"])
    if target.exists():
        import shutil

        shutil.rmtree(target)

    assert main(["--root", str(tmp_path), "approve", run_id, "project-root"]) == 0

    assert captured["project"] == target
    assert captured["confirm"] is False
    assert captured["continuation_note"] == "project_init"


def test_init_project_public_flow_recovery_metadata_survives_outer_result(monkeypatch, tmp_path: Path) -> None:
    def fake_bootstrap(project: Path, config: dict[str, object], *, create_remote_repos: bool = True, commit_message: str = "") -> dict[str, object]:
        return {"schema": "nexus.github_bootstrap.v1", "status": "completed", "reason": "bootstrapped_and_pushed_private"}

    def fake_public_flow(self: Runner, store, project: Path, *, confirm: bool = False, continuation_note: str = "") -> dict[str, object]:
        return {
            "schema": "nexus.interaction.v1",
            "run_id": store.run_id,
            "previous_task_status": "blocked",
            "previous_task_output": "GitHub CLI 未认证，已触发登录流程。",
            "next_task_prompt": f"完成 GitHub 登录后运行 python -m nexus.cli continue-after-input {store.run_id} --note \"用户已完成 GitHub 登录授权\"。",
            "blocked_reason": "LOGIN_INCOMPLETE",
            "artifact_refs": [],
            "lifecycle_status": "awaiting_external_user",
            "pending_actions": [{"action_id": "github_cli_device_login", "command": f"python -m nexus.cli continue-after-input {store.run_id} --note done"}],
            "continuation": {"operation": "github_public", "project_path": str(project), "confirm": True},
            "auto_resume_supported": True,
            "recovery_mode": True,
            "recovery_state": "recoverable_via_continue_after_input",
            "recovery_kind": "external_user_action",
            "recommended_executor": "outer_codex",
        }

    monkeypatch.setattr(nexus_runner, "bootstrap_project", fake_bootstrap)
    monkeypatch.setattr(Runner, "_github_public_flow", fake_public_flow)

    assert main(["--root", str(tmp_path), "init-project", "创建一个中文互联网 workflow kernel，跳过飞书", "--parent", str(tmp_path), "--provider", "mock"]) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    approval = json.loads((tmp_path / ".data" / "runs" / run_id / "approvals" / "project_root_required.json").read_text(encoding="utf-8"))
    target = Path(approval["target_path"])
    if target.exists():
        import shutil

        shutil.rmtree(target)

    assert main(["--root", str(tmp_path), "approve", run_id, "project-root"]) == 0
    interaction = json.loads((tmp_path / ".data" / "runs" / run_id / "interaction.json").read_text(encoding="utf-8"))

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "LOGIN_INCOMPLETE"
    assert interaction["lifecycle_status"] == "awaiting_external_user"
    assert interaction["pending_actions"][0]["action_id"] == "github_cli_device_login"
    assert interaction["continuation"]["operation"] == "github_public"
    assert "$nexus-workflow 继续" in interaction["next_task_prompt"]


def test_init_project_feishu_guide_sync_uses_autosync_not_record(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_autosync(*args, **kwargs) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "schema": "nexus.feishu_autosync_result.v1",
            "status": "completed",
            "reason": "feishu_autosync_completed",
            "setup": {"status": "completed", "reason": "local_checks_only"},
        }

    monkeypatch.setattr(nexus_runner, "run_feishu_autosync", fake_autosync)
    monkeypatch.setattr(nexus_runner, "run_feishu_setup", lambda *args, **kwargs: {"status": "completed", "reason": "local_checks_only"})

    assert main(["--root", str(tmp_path), "init-project", "创建一个中文互联网 workflow kernel，不同步 GitHub", "--parent", str(tmp_path), "--provider", "mock"]) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    approval = json.loads((tmp_path / ".data" / "runs" / run_id / "approvals" / "project_root_required.json").read_text(encoding="utf-8"))
    target = Path(approval["target_path"])
    if target.exists():
        import shutil

        shutil.rmtree(target)

    assert main(["--root", str(tmp_path), "approve", run_id, "project-root"]) == 0

    assert calls
    assert calls[0]["event_type"] == "project_init"
    assert calls[0]["guide_paths"] == [str(target / "docs" / "operation-guide.md")]


def test_init_project_feishu_autowrite_lands_markdown_before_autosync(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_autosync(*args, **kwargs) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "schema": "nexus.feishu_autosync_result.v1",
            "status": "completed",
            "reason": "feishu_autosync_completed",
            "setup": {"status": "completed", "reason": "local_checks_only"},
        }

    monkeypatch.setattr(nexus_runner, "run_feishu_autosync", fake_autosync)
    monkeypatch.setattr(nexus_runner, "run_feishu_setup", lambda *args, **kwargs: {"status": "completed", "reason": "local_checks_only"})
    original_model_node = Runner._model_node_with_schema

    def fake_model_node(self, store, provider, node_id, schema_key, prompt):
        if node_id == "feishu_project_init_record":
            return {
                "schema": "nexus.feishu_record_content.v1",
                "title": "初始化记录",
                "markdown": "项目已初始化，等待 autosync 同步。",
            }
        return original_model_node(self, store, provider, node_id, schema_key, prompt)

    monkeypatch.setattr(Runner, "_model_node_with_schema", fake_model_node)

    assert main(["--root", str(tmp_path), "init-project", "创建一个中文互联网 workflow kernel，同步飞书并启用飞书自动记录，不同步 GitHub", "--parent", str(tmp_path), "--provider", "mock", "--enable-feishu-autowrite"]) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    approval = json.loads((tmp_path / ".data" / "runs" / run_id / "approvals" / "project_root_required.json").read_text(encoding="utf-8"))
    target = Path(approval["target_path"])
    if target.exists():
        import shutil

        shutil.rmtree(target)

    assert main(["--root", str(tmp_path), "approve", run_id, "project-root"]) == 0

    record = target / "docs" / "feishu-records.md"
    assert record.exists()
    assert "project_init_autowrite" in record.read_text(encoding="utf-8")
    assert calls
    assert calls[0]["event_type"] == "project_init"
    assert str(record) in calls[0]["changed_paths"]


def test_project_name_validation_rejects_pseudo_compound_names() -> None:
    payload = {
        "schema": "nexus.project_name_candidates.v1",
        "recommended": "weamr",
        "candidates": [
            {
                "name": "weamr",
                "meaning": "WeChat + Email + Report",
                "memory_hook": "拼组词",
                "rationale": "五个字母但不是真实单词",
                "functional_link": "消息聚合",
                "metaphor": "信息入口",
                "word_validation": "不是一个真实英文单词",
            }
        ],
    }
    try:
        _validate_project_name_candidates(payload)
    except ValueError as exc:
        assert "not_in_real_five_letter_project_word_list" in str(exc)
    else:
        raise AssertionError("pseudo compound project name should be rejected")


def test_project_name_validation_accepts_common_real_words_with_dictionary_proof() -> None:
    payload = {
        "schema": "nexus.project_name_candidates.v1",
        "recommended": "vista",
        "candidates": [
            {
                "name": "vista",
                "meaning": "远景、视野",
                "memory_hook": "career vista",
                "rationale": "适合表达求职系统的全局视野",
                "functional_link": "连接简历、面试、知识补习和训练项目",
                "metaphor": "职业路径全景图",
                "word_validation": "vista 是 Oxford 收录的合法英文五字母名词。",
            },
            {
                "name": "pivot",
                "meaning": "支点、转向",
                "memory_hook": "feedback pivot",
                "rationale": "适合表达面试反馈驱动迭代",
                "functional_link": "将面试薄弱点映射到知识补习和简历修改",
                "metaphor": "能力迭代支点",
                "word_validation": "pivot 是 Collins 认证的标准英文五字母实词。",
            },
            {
                "name": "weave",
                "meaning": "编织",
                "memory_hook": "career weave",
                "rationale": "适合表达跨文档能力网络",
                "functional_link": "把简历、面试、知识和训练项目编织成动态闭环",
                "metaphor": "经纬线交织成布",
                "word_validation": "weave 是基础五字母动词，牛津词典列为及物/不及物动词，拼写规范。",
            },
            {
                "name": "nexus",
                "meaning": "连接点",
                "memory_hook": "next us",
                "rationale": "适合表达多模块动态联动",
                "functional_link": "同步简历、面试、知识和项目状态",
                "metaphor": "神经突触",
                "word_validation": "nexus 是牛津词典收录的五字母英文名词，非缩写、非造词、非拼接。",
            },
        ],
    }

    validated = _validate_project_name_candidates(payload)

    assert validated["recommended"] == "vista"


def test_init_project_explicit_name_overrides_invalid_model_candidates(monkeypatch, tmp_path: Path) -> None:
    def fail_project_name_node(self, store, provider, node_id, prompt):
        assert node_id == "project_name_candidates"
        raise ValueError("project_name_candidates_invalid: vista: not_in_real_five_letter_project_word_list")

    monkeypatch.setattr(Runner, "_model_node", fail_project_name_node)

    assert main(["--root", str(tmp_path), "init-project", "wljob：从零新建项目；项目名固定为 wljob；不同步 GitHub，跳过飞书", "--parent", str(tmp_path), "--provider", "mock"]) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    run_dir = tmp_path / ".data" / "runs" / run_id
    approval = json.loads((run_dir / "approvals" / "project_root_required.json").read_text(encoding="utf-8"))

    assert approval["recommended"] == "wljob"
    assert approval["explicit_user_name"] == "wljob"
    assert approval["naming_policy"] == "explicit_user_name_overrides_model_candidates"
    assert (run_dir / "tool_results" / "project_name_model_warning.json").exists()

    assert main(["--root", str(tmp_path), "approve", run_id, "project-root"]) == 0
    target = tmp_path / "wljob"
    assert target.exists()
    intent = json.loads((target / ".nexus" / "project-intent.json").read_text(encoding="utf-8"))
    assert intent["project_name_context"]["source"] == "explicit_user_name"
    assert "路径安全校验" in intent["project_name_context"]["word_validation"]
    normalized = (target / "docs" / "intent" / "normalized-requirement.md").read_text(encoding="utf-8")
    assert "名称校验" in normalized
    assert "真实单词校验" not in normalized


def test_init_project_name_failure_recover_retries_to_project_root_approval(monkeypatch, tmp_path: Path) -> None:
    calls = 0

    def flaky_project_name_node(self, store, provider, node_id, prompt):
        nonlocal calls
        assert node_id == "project_name_candidates"
        calls += 1
        if calls == 1:
            raise ValueError("project_name_candidates_invalid: vista: not_in_real_five_letter_project_word_list")
        return {
            "schema": "nexus.project_name_candidates.v1",
            "recommended": "vista",
            "candidates": [
                {
                    "name": "vista",
                    "meaning": "远景、视野",
                    "memory_hook": "career vista",
                    "rationale": "适合表达求职系统的全局视野",
                    "functional_link": "连接简历、面试、知识补习和训练项目",
                    "metaphor": "职业路径全景图",
                    "word_validation": "vista 是 Oxford 收录的合法英文五字母名词。",
                }
            ],
        }

    monkeypatch.setattr(Runner, "_model_node", flaky_project_name_node)

    runner = Runner(tmp_path)
    first = runner.init_project("创建一个中文互联网 workflow 项目", parent=tmp_path, provider_name="mock", github_sync=False, feishu_sync=False)
    assert first["blocked_reason"] == "project_name_model_failed"

    recovered = runner.recover(str(first["run_id"]), "恢复项目命名")

    assert recovered["blocked_reason"] == "project_root_approval_required"
    approval = json.loads((tmp_path / ".data" / "runs" / str(first["run_id"]) / "approvals" / "project_root_required.json").read_text(encoding="utf-8"))
    assert approval["recommended"] == "vista"
    assert not Path(approval["target_path"]).exists()


def test_init_project_debug_rebind_sinks_recovery_after_project_root_approval(monkeypatch, tmp_path: Path) -> None:
    calls = 0

    def flaky_project_name_node(self, store, provider, node_id, prompt):
        nonlocal calls
        assert node_id == "project_name_candidates"
        calls += 1
        if calls == 1:
            raise ValueError("project_name_candidates_invalid: vista: not_in_real_five_letter_project_word_list")
        return {
            "schema": "nexus.project_name_candidates.v1",
            "recommended": "vista",
            "candidates": [
                {
                    "name": "vista",
                    "meaning": "远景、视野",
                    "memory_hook": "career vista",
                    "rationale": "适合表达求职系统的全局视野",
                    "functional_link": "连接简历、面试、知识补习和训练项目",
                    "metaphor": "职业路径全景图",
                    "word_validation": "vista 是 Oxford 收录的合法英文五字母名词。",
                }
            ],
        }

    monkeypatch.setattr(Runner, "_model_node", flaky_project_name_node)

    runner = Runner(tmp_path)
    first = runner.init_project("创建一个中文互联网 workflow 项目", parent=tmp_path, provider_name="mock", github_sync=False, feishu_sync=False)
    run_id = str(first["run_id"])
    assert first["blocked_reason"] == "project_name_model_failed"

    handoff = runner.handoff_for_debug(run_id, reason="修复项目命名候选失败")
    handoff_id = str(handoff["debug_handoff"]["handoff_id"])
    runner.append_debug_worklog(run_id, handoff_id=handoff_id, kind="diagnose", summary="project name candidate validation failed before project-root approval")
    runner.append_debug_worklog(run_id, handoff_id=handoff_id, kind="edit", summary="patched project-name retry path")
    runner.append_debug_worklog(run_id, handoff_id=handoff_id, kind="test", summary="pytest targeted init retry ok", result="passed")

    rebound = runner.rebind_and_continue(run_id, handoff_id=handoff_id)

    run_dir = tmp_path / ".data" / "runs" / run_id
    assert rebound["blocked_reason"] == "project_root_approval_required"
    assert (run_dir / "tool_results" / "recovery_result.json").exists()
    assert (run_dir / "tool_results" / "debug_recovery_result.json").exists()
    assert (run_dir / "tool_results" / "recovery_playbook_pending_project.json").exists()
    assert not (run_dir / "approvals" / "recovery_playbook_write_required.json").exists()

    approved_project = runner.approve(run_id, "project-root")

    assert approved_project["previous_task_status"] == "completed"
    assert "recovery-playbook" in approved_project["next_task_prompt"]
    playbook_approval = run_dir / "approvals" / "recovery_playbook_write_required.json"
    assert playbook_approval.exists()
    approval = json.loads((run_dir / "approvals" / "project_root_required.json").read_text(encoding="utf-8"))
    target = Path(approval["target_path"])

    approved_memory = runner.approve(run_id, "recovery-playbook")

    assert approved_memory["previous_task_status"] == "completed"
    assert (target / ".nexus" / "recovery-playbook.json").exists()
    assert (target / "docs" / "recovery-records.md").exists()


def test_init_project_model_name_collision_uses_next_candidate(monkeypatch, tmp_path: Path) -> None:
    parent = tmp_path / "forge"
    parent.mkdir()
    (parent / "nexus").mkdir()

    def project_name_node(self, store, provider, node_id, prompt):
        assert node_id == "project_name_candidates"
        return {
            "schema": "nexus.project_name_candidates.v1",
            "recommended": "nexus",
            "candidates": [
                {
                    "name": "nexus",
                    "meaning": "连接点",
                    "memory_hook": "next us",
                    "rationale": "表达多模块联动",
                    "functional_link": "同步简历、面试、知识和项目状态",
                    "metaphor": "神经突触",
                    "word_validation": "nexus 是牛津词典收录的合法英文五字母名词，非造词。",
                },
                {
                    "name": "forge",
                    "meaning": "锻造",
                    "memory_hook": "forge skills",
                    "rationale": "表达能力锻造",
                    "functional_link": "沉淀训练项目",
                    "metaphor": "工坊",
                    "word_validation": "forge 是 Collins 认证的真实五字母英文单词。",
                },
                {
                    "name": "probe",
                    "meaning": "探测器",
                    "memory_hook": "probe gaps",
                    "rationale": "表达能力诊断",
                    "functional_link": "识别薄弱点并触发补习任务",
                    "metaphor": "能力扫描仪",
                    "word_validation": "probe 是朗文词典收录的真实五字母英文单词。",
                },
            ],
        }

    monkeypatch.setattr(Runner, "_model_node", project_name_node)

    interaction = Runner(tmp_path).init_project("创建一个求职闭环工具", parent=parent, provider_name="mock", github_sync=False, feishu_sync=False)

    assert interaction["blocked_reason"] == "project_root_approval_required"
    approval = interaction["approval_request"]
    assert approval["model_recommended"] == "nexus"
    assert approval["recommended"] == "probe"
    assert approval["name_collision_skipped"] == ["nexus", "forge"]
    assert approval["target_path"] == str(parent / "probe")


def test_init_project_blocks_agent_injected_explicit_name(tmp_path: Path) -> None:
    assert main(
        [
            "--root",
            str(tmp_path),
            "init-project",
            "项目名固定为 career-forge。从零新建项目",
            "--raw-user-request",
            "从零新建项目",
            "--parent",
            str(tmp_path),
            "--provider",
            "mock",
        ]
    ) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    run_dir = tmp_path / ".data" / "runs" / run_id
    interaction = json.loads((run_dir / "interaction.json").read_text(encoding="utf-8"))

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "explicit_project_name_agent_injected"
    assert (run_dir / "tool_results" / "agent_injected_project_name_failure.json").exists()
    assert not (tmp_path / "career-forge").exists()


def test_init_project_accepts_skill_prompt_explicit_name_in_raw_request(tmp_path: Path) -> None:
    assert main(
        [
            "--root",
            str(tmp_path),
            "init-project",
            "wljob_no_feishu：参考需求文件创建求职网申 workflow 测试项目；不同步飞书",
            "--raw-user-request",
            "$nexus-workflow 初始化项目 wljob_no_feishu：参考需求文件创建求职网申 workflow 测试项目；父目录为 /tmp；不同步飞书",
            "--parent",
            str(tmp_path),
            "--provider",
            "mock",
            "--no-github-sync",
            "--no-feishu-sync",
        ]
    ) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    run_dir = tmp_path / ".data" / "runs" / run_id
    interaction = json.loads((run_dir / "interaction.json").read_text(encoding="utf-8"))

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "project_root_approval_required"
    assert interaction["approval_request"]["name_source"] == "user_raw"
    assert interaction["approval_request"]["target_path"] == str(tmp_path / "wljob_no_feishu")


def test_init_project_original_doc_uses_raw_user_request(tmp_path: Path) -> None:
    assert main(
        [
            "--root",
            str(tmp_path),
            "init-project",
            "项目名固定为 wljob。外层运行补充说明。",
            "--raw-user-request",
            "项目名固定为 wljob。用户原始需求。",
            "--normalized-request",
            "项目名固定为 wljob。规范化需求。",
            "--parent",
            str(tmp_path),
            "--provider",
            "mock",
            "--no-github-sync",
            "--no-feishu-sync",
        ]
    ) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    assert main(["--root", str(tmp_path), "approve", run_id, "project-root"]) == 0

    original = (tmp_path / "wljob" / "docs" / "intent" / "original-requirement.md").read_text(encoding="utf-8")
    normalized = (tmp_path / "wljob" / "docs" / "intent" / "normalized-requirement.md").read_text(encoding="utf-8")
    intent = json.loads((tmp_path / "wljob" / ".nexus" / "project-intent.json").read_text(encoding="utf-8"))

    assert "用户原始需求" in original
    assert "外层运行补充说明" not in original
    assert "## 规范化输入" in normalized
    assert "规范化需求" in normalized
    assert intent["project_name_context"]["name_source"] == "user_raw"
    assert intent["input_trace"]["operational_idea_differs_from_raw"] is True


def test_init_project_blocks_unsafe_explicit_name(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path), "init-project", "从零新建一个名为 ../secret 的项目，不同步 GitHub，跳过飞书", "--parent", str(tmp_path), "--provider", "mock"]) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    run_dir = tmp_path / ".data" / "runs" / run_id
    interaction = json.loads((run_dir / "interaction.json").read_text(encoding="utf-8"))

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "explicit_project_name_invalid"
    assert not (tmp_path / "secret").exists()
    assert (run_dir / "tool_results" / "explicit_project_name_failure.json").exists()


def test_init_project_blocks_when_initial_public_secret_scan_fails(monkeypatch, tmp_path: Path) -> None:
    def fake_bootstrap(project: Path, config: dict[str, object], *, create_remote_repos: bool = True, commit_message: str = "") -> dict[str, object]:
        return {"schema": "nexus.github_bootstrap.v1", "status": "completed", "reason": "bootstrapped_and_pushed_private"}

    def fake_prepare_public(project: Path, config: dict[str, object], staging: Path) -> dict[str, object]:
        return {
            "schema": "nexus.github_public_staging.v1",
            "status": "blocked",
            "staging": str(staging),
            "scan": {"findings": [{"file": "docs/intent/original-requirement.md", "type": "secret_assignment"}]},
            "copied": ["docs/"],
            "blocked": [],
        }

    monkeypatch.setattr(nexus_runner, "bootstrap_project", fake_bootstrap)
    monkeypatch.setattr(nexus_runner, "prepare_public_staging", fake_prepare_public)
    assert main(["--root", str(tmp_path), "init-project", "创建一个中文互联网 workflow kernel", "--parent", str(tmp_path), "--provider", "mock"]) == 0
    run_id = next((tmp_path / ".data" / "runs").iterdir()).name
    approval = json.loads((tmp_path / ".data" / "runs" / run_id / "approvals" / "project_root_required.json").read_text(encoding="utf-8"))
    target = Path(approval["target_path"])
    assert main(["--root", str(tmp_path), "approve", run_id, "project-root"]) == 0
    interaction = json.loads((tmp_path / ".data" / "runs" / run_id / "interaction.json").read_text(encoding="utf-8"))
    state = json.loads((tmp_path / ".data" / "runs" / run_id / "state.json").read_text(encoding="utf-8"))
    assert target.exists()
    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "public_secret_scan_failed"
    assert state["github_public_init_status"] == "blocked"


def test_project_docs_preserve_existing_valid_docs_during_supplemental_init(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "docs" / "intent").mkdir(parents=True)
    normalized = project / "docs" / "intent" / "normalized-requirement.md"
    overview = project / "docs" / "project-overview.md"
    original = project / "docs" / "intent" / "original-requirement.md"
    original.write_text("# original\n\n## 原始输入\n\n用户已经写好的原始意图。\n", encoding="utf-8")
    normalized.write_text("# normalized\n\n## 文档职责\n\n保留。\n\n## 规范化目标\n\n保留。\n\n## 默认更新约束\n\n保留。\n", encoding="utf-8")
    overview.write_text("# overview\n\n## 项目定位\n\n保留。\n\n## 文档体系\n\n保留。\n\n## 关键运行与同步约束\n\n保留。\n", encoding="utf-8")
    before = {path: path.read_text(encoding="utf-8") for path in [original, normalized, overview]}

    payload = write_project_docs(project, "补充初始化", private_repo="", public_repo="", github_sync_enabled=False, feishu_sync_enabled=False, run_id="run-test")

    assert payload["doc_actions"]["docs/intent/original-requirement.md"] == "preserved_existing_valid"
    assert payload["doc_actions"]["docs/intent/normalized-requirement.md"] == "preserved_existing_valid"
    assert payload["doc_actions"]["docs/project-overview.md"] == "preserved_existing_valid"
    assert {path: path.read_text(encoding="utf-8") for path in [original, normalized, overview]} == before
    assert (project / ".nexus" / "project-intent.json").exists()


def test_project_docs_rebuild_empty_and_append_incomplete_without_deleting_user_text(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "docs" / "intent").mkdir(parents=True)
    normalized = project / "docs" / "intent" / "normalized-requirement.md"
    overview = project / "docs" / "project-overview.md"
    normalized.write_text("用户已经写好的背景，不能删除。\n", encoding="utf-8")
    overview.write_text("TODO\n", encoding="utf-8")

    payload = write_project_docs(project, "补充初始化", private_repo="", public_repo="", github_sync_enabled=False, feishu_sync_enabled=False, run_id="run-test")

    normalized_text = normalized.read_text(encoding="utf-8")
    overview_text = overview.read_text(encoding="utf-8")
    assert payload["doc_actions"]["docs/intent/normalized-requirement.md"] == "supplemented_missing_sections"
    assert payload["doc_actions"]["docs/project-overview.md"] == "rebuilt_from_empty_or_placeholder"
    assert "用户已经写好的背景，不能删除。" in normalized_text
    assert "## Nexus 补充初始化记录" in normalized_text
    assert "TODO" not in overview_text
    assert "## 项目定位" in overview_text


def test_project_docs_create_intent_source_search_validation_and_recovery_surfaces(tmp_path: Path) -> None:
    project = tmp_path / "codpm_workflow_rebuild"
    project.mkdir()
    source = tmp_path / "codpm-history.md"
    source.write_text(
        "codpm 必须管理 rule/skill/hook/config/MCP/memory，并区分 source-of-truth 和 registry 元数据。\n"
        "需要 list/show/check/render/watch workflow，不能修改参考项目。\n",
        encoding="utf-8",
    )

    payload = write_project_docs(
        project,
        f"参考 {source} 从零构建 codpm workflow 项目，必须理解 Codex 个性化治理和同步边界。",
        private_repo="",
        public_repo="",
        github_sync_enabled=False,
        feishu_sync_enabled=False,
        run_id="run-test",
    )

    required = [
        "docs/intent/project-understanding.md",
        "docs/intent/source-material-index.md",
        "docs/requirement-trace.md",
        "docs/reference-materials.md",
        "docs/search-plan.md",
        "docs/search-log.md",
        "docs/recovery-records.md",
        "scripts/validate_project.py",
        ".nexus/recovery-playbook.json",
    ]
    for rel in required:
        assert (project / rel).exists(), rel
        assert rel in payload["doc_actions"]

    understanding = (project / "docs" / "intent" / "project-understanding.md").read_text(encoding="utf-8")
    source_index = (project / "docs" / "intent" / "source-material-index.md").read_text(encoding="utf-8")
    trace = (project / "docs" / "requirement-trace.md").read_text(encoding="utf-8")
    assert "codpm" in understanding
    assert "rule/skill/hook/config/MCP/memory" in understanding
    assert "read_status: `read`" in source_index
    assert str(source) in source_index
    assert "R01" in trace

    playbook = json.loads((project / ".nexus" / "recovery-playbook.json").read_text(encoding="utf-8"))
    assert playbook["schema"] == "nexus.recovery_playbook.v1"
    assert playbook["entries"] == []

    (project / "docs" / "operation-guide.md").write_text("## 验证\n\n运行 `python scripts/validate_project.py`。\n", encoding="utf-8")
    result = subprocess.run([sys.executable, str(project / "scripts" / "validate_project.py")], cwd=project, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_project_docs_do_not_leak_resume_domain_into_unrelated_projects(tmp_path: Path) -> None:
    project = tmp_path / "forge_manager_workflow_rebuild"
    project.mkdir()

    write_project_docs(
        project,
        "构建 forge-manager 类项目管理 workflow，读取本地项目、任务、状态、activity、深链和 dashboard，不要同步 GitHub。",
        private_repo="",
        public_repo="",
        github_sync_enabled=False,
        feishu_sync_enabled=False,
        run_id="run-test",
    )

    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            project / "docs" / "intent" / "normalized-requirement.md",
            project / "docs" / "project-overview.md",
            project / "docs" / "intent" / "project-understanding.md",
        ]
    )
    assert "简历" not in combined
    assert "求职" not in combined
    assert "网申" not in combined
    assert "forge-manager" in combined
