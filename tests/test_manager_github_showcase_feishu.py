from __future__ import annotations

import json
from pathlib import Path
import subprocess

from nexus import feishu_setup
from nexus import github_sync
from nexus import runner as nexus_runner
from nexus.artifacts import RunStore
from nexus.github_sync import (
    assert_public_release_tree_clean,
    auto_private_sync,
    discover_public_source_roots,
    load_config as load_github_sync_config,
    prepare_public_staging,
    scan_private_worktree,
    scan_public_staging,
    sync_private,
    validate_public_fresh_clone,
    validate_public_staging,
    write_config as write_github_sync_config,
)
from nexus.project_docs import write_project_docs
from nexus.runner import Runner


def _public_readme(repo: str, *, command: str = "codpm --help") -> str:
    return "\n".join(
        [
            "# demo",
            "",
            "<!-- nexus:public-install -->",
            "## Public Install",
            "",
            "```bash",
            f"python -m pip install git+https://github.com/{repo}.git",
            command,
            "```",
            "",
        ]
    )


def test_conversation_manager_init_and_ingest(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    transcript = tmp_path / "talk.md"
    transcript.write_text("api_key=sk-abcdefghijklmnopqrstuvwxyz\n用户想沉淀 workflow。", encoding="utf-8")
    runner = Runner(tmp_path)

    init = runner.conversation_manager_init(project)
    assert init["previous_task_status"] == "completed"
    ingest = runner.conversation_manager_ingest(project, transcript)

    assert ingest["previous_task_status"] == "completed"
    assert (project / "docs" / "ai-conversations" / "index.yaml").exists()
    session_files = list((project / "docs" / "ai-conversations" / "sessions").glob("*.md"))
    assert session_files
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in session_files[0].read_text(encoding="utf-8")


def test_system_showcase_generate_and_explain(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text(_public_readme("owner/public"), encoding="utf-8")
    runner = Runner(tmp_path)

    generated = runner.system_showcase_generate(project)
    assert generated["previous_task_status"] == "completed"
    assert (project / "docs" / "system" / "architecture.md").exists()
    explained = runner.system_showcase_explain(project, "runner")
    assert explained["previous_task_status"] == "completed"


def test_system_showcase_uses_project_intent_for_job_workflow(tmp_path: Path) -> None:
    project = tmp_path / "wljob"
    (project / "docs" / "intent").mkdir(parents=True)
    (project / "docs" / "intent" / "normalized-requirement.md").write_text(
        "\n".join(
            [
                "# wljob 规范化意图需求",
                "",
                "wljob 是通用中文互联网求职、网申流程、岗位信息整理和自动化执行前安全规划 workflow kernel。",
                "必备能力包括意图 intake、岗位来源规划、岗位卡片标准化、解释性评分排序、proposal/confirmation card、审批门禁和审计 artifact。",
                "硬安全边界：不读取 cookie/token/browser profile/SSH key/.env/密码文件，不绕过 CAPTCHA/403/WAF，不自动提交网申。",
                "默认 GitHub private 同步，public 发布必须 secret/private metadata scan 后显式确认，并同步飞书长期文档。",
            ]
        ),
        encoding="utf-8",
    )
    (project / "docs" / "project-overview.md").write_text("## 项目定位\n中文互联网求职/网申 workflow kernel。\n", encoding="utf-8")
    runner = Runner(tmp_path)

    generated = runner.system_showcase_generate(project)

    assert generated["previous_task_status"] == "completed"
    markdown = (project / "docs" / "system" / "architecture.md").read_text(encoding="utf-8")
    graph = json.loads((project / "docs" / "system" / "architecture.json").read_text(encoding="utf-8"))
    node_ids = {node["id"] for node in graph["nodes"]}
    assert graph["domain"] == "chinese_job_application_workflow"
    assert {"intake", "source_planning", "job_cards", "proposal_gate", "execution_guard", "records_sync"}.issubset(node_ids)
    assert "岗位卡片与索引" in markdown
    assert "不自动提交网申" in markdown
    assert "docs/intent/normalized-requirement.md" in graph["source_files"]
    assert not any(str(path).startswith(".git/") for path in graph["source_files"])
    assert ".nexus/feishu.json" not in graph["source_files"]

    explained = runner.system_showcase_explain(project, "job_cards")
    assert explained["previous_task_status"] == "completed"


def test_github_sync_blocks_without_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).github_sync_private(project)
    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "github_sync_config_missing"


def test_github_sync_configure_writes_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).github_sync_configure(project, private_repo="owner/private", public_repo="owner/public")
    assert interaction["previous_task_status"] == "completed"
    assert (project / ".github" / "nexus-sync.json").exists()


def test_github_sync_private_uses_current_state_auto_sync(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_github_sync_config(project, private_repo="owner/private", public_repo="owner/public")
    config_path = project / ".github" / "nexus-sync.json"
    config = load_github_sync_config(project) or {}
    config["default_private_sync"] = False
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_auto_private_sync(project_path: Path, config: dict[str, object], *, commit_message: str = "") -> dict[str, object]:
        calls.append({"project": project_path, "config": dict(config), "commit_message": commit_message})
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "completed", "reason": "private_synced"}

    monkeypatch.setattr(nexus_runner, "auto_private_sync", fake_auto_private_sync)

    interaction = Runner(tmp_path).github_sync_private(project)

    assert interaction["previous_task_status"] == "completed"
    assert interaction["previous_task_output"] == "GitHub private 同步结果：private_synced"
    assert calls
    assert calls[0]["project"] == project
    assert calls[0]["config"]["default_private_sync"] is True
    assert calls[0]["commit_message"] == "sync project to GitHub private"


def test_github_status_retries_without_proxy_http2(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(argv, *, capture_output, text, check, env=None):
        calls.append({"argv": argv, "env": env})
        if env and env.get("GODEBUG") == "http2client=0" and "HTTPS_PROXY" not in env:
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="keyring token invalid")

    monkeypatch.setattr(github_sync.shutil, "which", lambda name: "gh")
    monkeypatch.setattr(github_sync.subprocess, "run", fake_run)

    status = github_sync.gh_status()

    assert status["status"] == "ok"
    assert status["retry_without_proxy_http2"] is True
    assert len(calls) == 2


def test_private_sync_non_fast_forward_pushes_fallback_branch(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run_github_command(argv):
        calls.append(list(argv))
        if argv[-1] == "HEAD:main":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Updates were rejected because the remote contains work that you do not have locally. fetch first")
        return subprocess.CompletedProcess(argv, 0, stdout="pushed fallback", stderr="")

    monkeypatch.setattr(github_sync, "gh_status", lambda: {"status": "ok", "reason": "authenticated"})
    monkeypatch.setattr(github_sync, "_gh_setup_git", lambda: {"schema": "nexus.gh_git_auth_setup.v1", "status": "completed", "reason": "git_credential_helper_ready"})
    monkeypatch.setattr(github_sync, "_ensure_single_github_repository", lambda *args, **kwargs: {"schema": "nexus.github_single_repo_setup.v1", "status": "completed", "reason": "repo_exists"})
    monkeypatch.setattr(github_sync, "_ensure_remote", lambda *args, **kwargs: None)
    monkeypatch.setattr(github_sync, "_private_fallback_branch", lambda project: "nexus-private-sync/test-branch")
    monkeypatch.setattr(github_sync, "_run_github_command", fake_run_github_command)

    result = sync_private(tmp_path, {"private_repo": "owner/private", "private_remote": "private"})

    assert result["status"] == "completed"
    assert result["reason"] == "pushed_private_fallback_branch"
    assert result["fallback_branch"] == "nexus-private-sync/test-branch"
    assert result["main_push"]["reason"] == "non_fast_forward_remote_main_preserved"
    assert calls[-1][-1] == "HEAD:nexus-private-sync/test-branch"


def test_github_sync_guide_writes_local_doc(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_github_sync_config(project, private_repo="owner/private", public_repo="owner/public")

    interaction = Runner(tmp_path).github_sync_guide(project)

    assert interaction["previous_task_status"] == "completed"
    guide = project / "docs" / "github-sync-guide.md"
    assert guide.exists()
    text = guide.read_text(encoding="utf-8")
    assert "owner/private" in text
    assert "gh auth login --web" in text
    assert "密码" in text
    assert "token" in text


def test_github_sync_guide_publish_feishu_uses_autosync_not_record(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[dict[str, object]] = []

    def fake_autosync(*args, **kwargs) -> dict[str, object]:
        calls.append(kwargs)
        return {"schema": "nexus.feishu_autosync_result.v1", "status": "blocked", "reason": "feishu_config_missing"}

    monkeypatch.setattr(nexus_runner, "run_feishu_autosync", fake_autosync)

    interaction = Runner(tmp_path).github_sync_guide(project, publish_feishu=True)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "feishu_config_missing"
    assert calls
    assert calls[0]["event_type"] == "github_sync_guide_publish"
    assert calls[0]["guide_paths"] == [str(project / "docs" / "operation-guide.md")]
    assert (tmp_path / ".data" / "runs" / interaction["run_id"] / "tool_results" / "github_sync_guide_feishu_sync.json").exists()
    assert (project / "docs" / "github-sync-guide.md").exists()


def test_operation_guide_writes_local_doc(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    interaction = Runner(tmp_path).operation_guide(project, target="project")

    assert interaction["previous_task_status"] == "completed"
    assert (project / "docs" / "operation-guide.md").exists()


def test_self_sync_updates_guide_and_reports_github_success(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "nexus"
    project.mkdir()
    write_github_sync_config(project, private_repo="<PRIVATE_REPO>", public_repo="YaofeiHe/nexus-public")

    monkeypatch.setattr(
        nexus_runner,
        "auto_private_sync",
        lambda project_path, config, *, commit_message="nexus auto private sync": {"schema": "nexus.github_auto_private_sync.v1", "status": "completed", "reason": f"completed ({project_path.name})"},
    )

    interaction = Runner(tmp_path).self_sync(project, target="nexus", feishu_sync_enabled=False)

    assert interaction["previous_task_status"] == "completed"
    assert "GitHub auto-private" in interaction["previous_task_output"]
    assert (project / "docs" / "operation-guide.md").exists()


def test_self_sync_private_syncs_feishu_writeback_once(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "nexus"
    project.mkdir()
    write_github_sync_config(project, private_repo="<PRIVATE_REPO>", public_repo="YaofeiHe/nexus-public")
    calls: list[str] = []

    def fake_auto_private_sync(project_path: Path, config: dict[str, object], *, commit_message: str = "nexus auto private sync") -> dict[str, object]:
        calls.append(commit_message)
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "completed", "reason": f"completed ({commit_message})"}

    monkeypatch.setattr(nexus_runner, "auto_private_sync", fake_auto_private_sync)
    monkeypatch.setattr(
        nexus_runner,
        "run_feishu_autosync",
        lambda *args, **kwargs: {"schema": "nexus.feishu_autosync.v1", "status": "completed", "reason": "synced", "records": ["docs/feishu-records.md"]},
    )

    interaction = Runner(tmp_path).self_sync(project, target="nexus", feishu_sync_enabled=True)

    assert interaction["previous_task_status"] == "completed"
    assert calls == ["nexus self sync", "nexus self sync feishu records"]
    assert "飞书写回 private 再同步：completed" in interaction["previous_task_output"]


def test_run_tests_success_runs_post_change_autosync(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_github_sync_config(project, private_repo="owner/private", public_repo="owner/public")
    store = RunStore(tmp_path, "run-tests")
    store.ensure()
    store.write_json("input.json", {"schema": "nexus.input.v1", "project_path": str(project)})
    private_calls: list[str] = []
    feishu_calls: list[dict[str, object]] = []

    def fake_auto_private_sync(project_path: Path, config: dict[str, object], *, commit_message: str = "nexus auto private sync") -> dict[str, object]:
        private_calls.append(commit_message)
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "completed", "reason": "private_synced"}

    def fake_autosync(*args, **kwargs) -> dict[str, object]:
        feishu_calls.append(kwargs)
        return {"schema": "nexus.feishu_autosync_result.v1", "status": "completed", "reason": "feishu_autosync_completed", "setup": {"status": "completed"}}

    monkeypatch.setattr(nexus_runner, "auto_private_sync", fake_auto_private_sync)
    monkeypatch.setattr(nexus_runner, "run_feishu_autosync", fake_autosync)

    interaction = Runner(tmp_path).run_tests("run-tests", command="python -c \"print('ok')\"")

    assert interaction["previous_task_status"] == "completed"
    assert len(private_calls) == 2
    assert feishu_calls[0]["event_type"] == "code_change_test_passed"
    assert (project / "docs" / "operation-guide.md").exists()
    assert (project / ".nexus" / "project-intent.json").exists()


def test_run_tests_success_blocks_when_feishu_autosync_blocks(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_github_sync_config(project, private_repo="owner/private", public_repo="owner/public")
    store = RunStore(tmp_path, "run-feishu-blocked")
    store.ensure()
    store.write_json("input.json", {"schema": "nexus.input.v1", "project_path": str(project)})
    private_calls: list[str] = []

    monkeypatch.setattr(
        nexus_runner,
        "auto_private_sync",
        lambda project_path, config, *, commit_message="nexus auto private sync": private_calls.append(commit_message) or {"schema": "nexus.github_auto_private_sync.v1", "status": "completed", "reason": "private_synced"},
    )
    monkeypatch.setattr(
        nexus_runner,
        "run_feishu_autosync",
        lambda *args, **kwargs: {"schema": "nexus.feishu_autosync_result.v1", "status": "blocked", "reason": "feishu_config_missing"},
    )

    interaction = Runner(tmp_path).run_tests("run-feishu-blocked", command="python -c \"print('ok')\"")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "feishu_config_missing"
    assert len(private_calls) == 1
    assert (project / "docs" / "operation-guide.md").exists()


def test_run_tests_success_unknown_project_requires_github_config(tmp_path: Path) -> None:
    project = tmp_path / "ordinary"
    project.mkdir()
    store = RunStore(tmp_path, "run-no-config")
    store.ensure()
    store.write_json("input.json", {"schema": "nexus.input.v1", "project_path": str(project)})

    interaction = Runner(tmp_path).run_tests("run-no-config", command="python -c \"print('ok')\"")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "github_sync_config_missing_and_repo_target_unknown"
    assert (project / "docs" / "operation-guide.md").exists()


def test_supplemental_init_preserves_business_files_and_adds_docs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    business = project / "app.py"
    business.write_text("print('keep')\n", encoding="utf-8")

    interaction = Runner(tmp_path).supplemental_init(project, github_private_enabled=False, feishu_sync_enabled=False)

    assert interaction["previous_task_status"] == "completed"
    assert business.read_text(encoding="utf-8") == "print('keep')\n"
    assert (project / "docs" / "operation-guide.md").exists()
    assert (project / ".nexus" / "project-intent.json").exists()


def test_self_sync_enters_external_codex_recovery_and_continues_after_github_login(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "nexus"
    project.mkdir()
    write_github_sync_config(project, private_repo="<PRIVATE_REPO>", public_repo="YaofeiHe/nexus-public")
    calls = {"sync": 0}

    def fake_auto_private_sync(project_path: Path, config: dict[str, object], *, commit_message: str = "") -> dict[str, object]:
        calls["sync"] += 1
        if calls["sync"] == 1:
            return {"schema": "nexus.github_auto_private_sync.v1", "status": "blocked", "reason": "gh_auth_required"}
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "completed", "reason": "private_synced"}

    monkeypatch.setattr(nexus_runner, "auto_private_sync", fake_auto_private_sync)
    monkeypatch.setattr(
        nexus_runner,
        "run_github_auth_login",
        lambda project_path, *, browser_mode="native": {
            "schema": "nexus.github_auth_status.v1",
            "state": "LOGIN_INCOMPLETE",
            "device_url": "https://github.com/login/device",
            "device_code": "ABCD-1234",
            "current_instruction": "manual auth required",
            "request_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "status_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "log_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "host_capability_request_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
        },
    )

    interaction = Runner(tmp_path).self_sync(project, target="nexus", feishu_sync_enabled=False)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["lifecycle_status"] == "awaiting_external_user"
    assert interaction["pending_actions"][0]["action_id"] == "github_cli_device_login"
    assert interaction["pending_actions"][0]["device_code"] == "ABCD-1234"
    assert interaction["continuation"]["operation"] == "self_sync"

    continued = Runner(tmp_path).continue_after_input(interaction["run_id"], note="github login done")

    assert continued["previous_task_status"] == "completed"
    assert "GitHub auto-private 同步结果：private_synced" in continued["previous_task_output"]


def test_github_api_eof_recovery_continues_original_auto_private_run(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "nexus"
    project.mkdir()
    write_github_sync_config(project, private_repo="<PRIVATE_REPO>", public_repo="YaofeiHe/nexus-public")
    calls = {"sync": 0}

    def fake_auto_private_sync(project_path: Path, config: dict[str, object], *, commit_message: str = "") -> dict[str, object]:
        calls["sync"] += 1
        if calls["sync"] == 1:
            return {
                "schema": "nexus.github_auto_private_sync.v1",
                "status": "blocked",
                "reason": "github_repo_create_failed",
                "push": {"repo_setup": {"create": {"stderr": 'Get "https://api.github.com/users/YaofeiHe": EOF'}}},
            }
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "completed", "reason": "private_synced"}

    monkeypatch.setattr(nexus_runner, "auto_private_sync", fake_auto_private_sync)

    interaction = Runner(tmp_path).github_sync_auto_private(project)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "github_api_eof"
    assert interaction["pending_actions"][0]["action_id"] == "retry_github_api_without_proxy_http2"
    assert interaction["continuation"]["operation"] == "github_auto_private"

    continued = Runner(tmp_path).continue_after_input(interaction["run_id"], note="retry EOF")

    assert continued["previous_task_status"] == "completed"
    assert continued["previous_task_output"] == "GitHub auto-private 同步结果：private_synced"


def test_github_api_eof_recovery_continues_original_public_run(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "nexus"
    project.mkdir()
    write_github_sync_config(project, private_repo="<PRIVATE_REPO>", public_repo="YaofeiHe/nexus-public")
    config_path = project / ".github" / "nexus-sync.json"
    config = load_github_sync_config(project) or {}
    config["public_allowlist"] = ["README.md"]
    config["public_required_paths"] = ["README.md"]
    config["public_validation"] = {"enabled": False}
    config_path.write_text(__import__("json").dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (project / "README.md").write_text(_public_readme("YaofeiHe/nexus-public", command="nexus --help"), encoding="utf-8")
    calls = {"sync": 0}

    def fake_sync_public(project_path: Path, config: dict[str, object], staging: Path) -> dict[str, object]:
        calls["sync"] += 1
        if calls["sync"] == 1:
            return {"schema": "nexus.github_public_sync.v1", "status": "blocked", "reason": "git_push_failed", "stderr": 'Get "https://api.github.com/users/YaofeiHe": EOF'}
        return {"schema": "nexus.github_public_sync.v1", "status": "completed", "reason": "pushed_public"}

    monkeypatch.setattr(nexus_runner, "sync_public", fake_sync_public)

    interaction = Runner(tmp_path).github_sync_public(project, confirm=True)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "github_api_eof"
    assert interaction["run_id"]
    assert interaction["continuation"]["operation"] == "github_public"
    assert interaction["continuation"]["confirm"] is True

    continued = Runner(tmp_path).continue_after_input(interaction["run_id"], note="retry EOF")

    assert continued["previous_task_status"] == "completed"
    assert continued["previous_task_output"] == "GitHub public 同步结果：pushed_public"
    assert continued["run_id"] == interaction["run_id"]


def test_github_private_eof_recovery_continues_original_private_run(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_github_sync_config(project, private_repo="owner/private", public_repo="owner/public")
    calls = {"sync": 0}

    def fake_auto_private_sync(project_path: Path, config: dict[str, object], *, commit_message: str = "") -> dict[str, object]:
        calls["sync"] += 1
        if calls["sync"] == 1:
            return {
                "schema": "nexus.github_auto_private_sync.v1",
                "status": "blocked",
                "reason": "git_push_failed",
                "push": {"stderr": 'Get "https://api.github.com/repos/owner/private": EOF'},
            }
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "completed", "reason": "private_synced"}

    monkeypatch.setattr(nexus_runner, "auto_private_sync", fake_auto_private_sync)

    interaction = Runner(tmp_path).github_sync_private(project)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "github_api_eof"
    assert interaction["continuation"]["operation"] == "github_private"

    continued = Runner(tmp_path).continue_after_input(interaction["run_id"], note="retry EOF")

    assert continued["previous_task_status"] == "completed"
    assert continued["previous_task_output"] == "GitHub private 同步结果：private_synced"


def test_github_bootstrap_auth_recovery_continues_bootstrap(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls = {"bootstrap": 0}

    def fake_bootstrap(project_path: Path, config: dict[str, object], *, create_remote_repos: bool = True, commit_message: str = "") -> dict[str, object]:
        calls["bootstrap"] += 1
        if calls["bootstrap"] == 1:
            return {"schema": "nexus.github_bootstrap.v1", "status": "blocked", "reason": "gh_auth_required"}
        return {"schema": "nexus.github_bootstrap.v1", "status": "completed", "reason": "bootstrapped_and_pushed_private"}

    monkeypatch.setattr(nexus_runner, "bootstrap_project", fake_bootstrap)
    monkeypatch.setattr(
        nexus_runner,
        "run_github_auth_login",
        lambda project_path, *, browser_mode="native": {
            "schema": "nexus.github_auth_status.v1",
            "state": "LOGIN_INCOMPLETE",
            "device_url": "https://github.com/login/device",
            "device_code": "BOOT-1234",
            "current_instruction": "manual auth required",
            "request_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "status_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "log_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "host_capability_request_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
        },
    )

    interaction = Runner(tmp_path).github_sync_bootstrap(project, private_repo="owner/private", public_repo="owner/public")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["continuation"]["operation"] == "github_bootstrap"
    assert interaction["continuation"]["private_repo"] == "owner/private"

    continued = Runner(tmp_path).continue_after_input(interaction["run_id"], note="github login done")

    assert continued["previous_task_status"] == "completed"
    assert calls["bootstrap"] == 2


def test_github_auto_private_blocks_on_sensitive_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_github_sync_config(project, private_repo="owner/private", public_repo="owner/public")
    (project / ".env").write_text("API_KEY=secret-value\n", encoding="utf-8")

    result = auto_private_sync(project, load_github_sync_config(project) or {})

    assert result["status"] == "blocked"
    assert result["reason"] == "private_secret_scan_failed"


def test_github_auto_private_commits_dirty_worktree_before_push(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_github_sync_config(project, private_repo="owner/private", public_repo="owner/public")
    config = load_github_sync_config(project) or {}
    (project / "notes.md").write_text("dirty state must be committed before push\n", encoding="utf-8")

    def fake_sync_private(project_path: Path, config: dict[str, object]) -> dict[str, object]:
        status = subprocess.run(["git", "-C", str(project_path), "status", "--porcelain=v1"], capture_output=True, text=True, check=False)
        return {
            "schema": "nexus.github_private_sync.v1",
            "status": "completed",
            "reason": "pushed_private",
            "status_before_push": status.stdout,
        }

    monkeypatch.setattr(github_sync, "sync_private", fake_sync_private)

    result = auto_private_sync(project, config, commit_message="sync current project state")

    assert result["status"] == "completed"
    assert result["reason"] == "private_synced"
    assert result["commit"]["reason"] == "committed"
    assert result["push"]["status_before_push"].strip() == ""


def test_github_private_scan_skips_runtime_data_but_keeps_env_block(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = write_github_sync_config(project, private_repo="owner/private", public_repo="owner/public")
    config = load_github_sync_config(project) or {}
    (project / ".data" / "runs").mkdir(parents=True)
    (project / ".data" / "runs" / "state.json").write_text('{"token":"local-runtime-only"}', encoding="utf-8")
    (project / "src").mkdir()
    (project / "src" / "settings.py").write_text('api_key = "sk-abcdefghijklmnopqrstuvwxyz"\n', encoding="utf-8")

    scan = scan_private_worktree(project, config)

    assert scan["findings"] == []
    assert any(item["file"] == "<NEXUS_ARTIFACT_PATH>" for item in scan["skipped"])
    assert config_path.exists()


def test_github_private_scan_blocks_real_assignment_secret(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_github_sync_config(project, private_repo="owner/private", public_repo="owner/public")
    secret_value = "realSecretValue-" + "ABC1234567890xyz"
    (project / "settings.py").write_text(f'api_key = "{secret_value}"\n', encoding="utf-8")

    scan = scan_private_worktree(project, load_github_sync_config(project) or {})

    assert scan["findings"]
    assert scan["findings"][0]["pattern"] == "api_key_assignment"


def test_project_docs_add_private_readme_install_for_managed_project(tmp_path: Path) -> None:
    project = tmp_path / "forge-manager"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "forge-manager"',
                "[project.scripts]",
                'forge-manager = "forge_manager.cli:main"',
            ]
        ),
        encoding="utf-8",
    )
    (project / "README.md").write_text("# Existing\n\nKeep this.\n", encoding="utf-8")

    result = write_project_docs(project, "补充 private 安装说明", private_repo="YaofeiHe/forge-manager", public_repo="YaofeiHe/forge-manager-public", github_sync_enabled=True, feishu_sync_enabled=False, run_id="run-test")
    readme = (project / "README.md").read_text(encoding="utf-8")

    assert result["doc_actions"]["README.md"] == "supplemented_private_install"
    assert "# Existing" in readme
    assert "python -m pip install git+https://github.com/YaofeiHe/forge-manager.git" in readme
    assert "python -m pip install git+https://github.com/YaofeiHe/forge-manager-public.git" not in readme
    assert "forge-manager --help" in readme


def test_project_docs_replaces_old_public_install_with_private_install(tmp_path: Path) -> None:
    project = tmp_path / "codpm"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "codpm"',
                "[project.scripts]",
                'codpm = "codpm.cli:main"',
            ]
        ),
        encoding="utf-8",
    )
    (project / "README.md").write_text(
        "# codpm\n\nKeep this.\n\n<!-- nexus:public-install -->\n## Public Install\n\n```bash\npython -m pip install git+https://github.com/YaofeiHe/codpm-public.git\n```\n",
        encoding="utf-8",
    )
    skill = project / "skills" / "codpm-workflow" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: codpm-workflow\ndescription: Manage Codex personalization.\n---\nUse private checkout.\n", encoding="utf-8")

    result = write_project_docs(project, "迁移 private README", private_repo="YaofeiHe/codpm", public_repo="YaofeiHe/codpm-public", github_sync_enabled=True, feishu_sync_enabled=False, run_id="run-test")
    readme = (project / "README.md").read_text(encoding="utf-8")

    assert result["doc_actions"]["README.md"] == "replaced_nexus_install"
    assert "# codpm" in readme
    assert "Keep this." in readme
    assert "<!-- nexus:private-install -->" in readme
    assert "## Private Install" in readme
    assert "python -m pip install git+https://github.com/YaofeiHe/codpm.git" in readme
    assert "Codex workflow/skill install:" in readme
    assert "git clone --depth 1 https://github.com/YaofeiHe/codpm.git" in readme
    assert "for skill in skills/codpm-workflow; do" in readme
    assert 'cp -R "$tmp/repo/$skill" "$HOME/.agents/skills/"' in readme
    assert "codex plugin marketplace add" not in readme
    assert "codex plugin add" not in readme
    assert "codex plugin install" not in readme
    assert "YaofeiHe/codpm-public" not in readme


def test_public_staging_keeps_public_repo_install_command(tmp_path: Path) -> None:
    project = tmp_path / "forge-manager"
    project.mkdir()
    (project / "README.md").write_text(_public_readme("YaofeiHe/forge-manager-public", command="forge-manager --help"), encoding="utf-8")
    config = {
        "private_repo": "YaofeiHe/forge-manager",
        "public_repo": "YaofeiHe/forge-manager-public",
        "public_allowlist": ["README.md"],
        "public_required_paths": ["README.md"],
        "public_validation": {"enabled": False},
    }

    staged = prepare_public_staging(project, config, tmp_path / "staging")
    readme = (tmp_path / "staging" / "README.md").read_text(encoding="utf-8")

    assert staged["status"] == "completed"
    assert "git+https://github.com/YaofeiHe/forge-manager-public.git" in readme
    assert "<PRIVATE_REPO>-public" not in readme


def test_public_staging_replaces_private_readme_install_with_public_install(tmp_path: Path) -> None:
    project = tmp_path / "codpm"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "codpm"',
                "[project.scripts]",
                'codpm = "codpm.cli:main"',
            ]
        ),
        encoding="utf-8",
    )
    (project / "README.md").write_text(
        "# codpm\n\nKeep this.\n\n<!-- nexus:private-install -->\n## Private Install\n\n```bash\npython -m pip install git+https://github.com/YaofeiHe/codpm.git\n```\n\n<!-- nexus:private-install -->\n## Private Install\n\n```bash\npython -m pip install git+https://github.com/YaofeiHe/codpm.git\n```\n",
        encoding="utf-8",
    )
    config = {
        "private_repo": "YaofeiHe/codpm",
        "public_repo": "YaofeiHe/codpm-public",
        "public_allowlist": ["README.md", "pyproject.toml"],
        "public_required_paths": ["README.md", "pyproject.toml"],
        "public_validation": {"enabled": False},
    }

    staged = prepare_public_staging(project, config, tmp_path / "staging")
    readme = (tmp_path / "staging" / "README.md").read_text(encoding="utf-8")

    assert staged["status"] == "completed"
    assert staged["readme_action"] == "replaced_nexus_install"
    assert "Keep this." in readme
    assert "<!-- nexus:public-install -->" in readme
    assert readme.count("<!-- nexus:public-install -->") == 1
    assert "<!-- nexus:private-install -->" not in readme
    assert "## Public Install" in readme
    assert "python -m pip install git+https://github.com/YaofeiHe/codpm-public.git" in readme
    assert "YaofeiHe/codpm.git" not in readme
    assert "<PRIVATE_REPO>" not in readme


def test_public_staging_generates_workflow_assets_only_in_staging(tmp_path: Path) -> None:
    project = tmp_path / "forge-manager"
    project.mkdir()
    (project / "README.md").write_text(_public_readme("YaofeiHe/forge-manager-public", command="forge-manager --help"), encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "forge-manager"',
                "[project.scripts]",
                'forge-manager = "forge_manager.cli:main"',
            ]
        ),
        encoding="utf-8",
    )
    skill = project / "skills" / "forge-manager-workflow" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: forge-manager-workflow\ndescription: Manage Forge projects.\n---\nUse private checkout.\n", encoding="utf-8")
    config = {
        "private_repo": "YaofeiHe/forge-manager",
        "public_repo": "YaofeiHe/forge-manager-public",
        "public_allowlist": ["README.md", "pyproject.toml", "skills/"],
        "public_required_paths": ["README.md", "pyproject.toml", "skills/"],
        "public_validation": {"enabled": False},
    }

    staged = prepare_public_staging(project, config, tmp_path / "staging")
    validation = validate_public_staging(tmp_path / "staging", {**config, "_public_discovery": staged["discovery"]})
    public_skill = tmp_path / "staging" / ".agents" / "skills" / "forge-manager-workflow" / "SKILL.md"

    assert staged["status"] == "completed"
    assert public_skill.exists()
    assert "forge-manager --help" in public_skill.read_text(encoding="utf-8")
    assert (tmp_path / "staging" / "plugins" / "forge-manager-workflow" / ".codex-plugin" / "plugin.json").exists()
    readme = (tmp_path / "staging" / "README.md").read_text(encoding="utf-8")
    assert "Codex workflow/skill install:" in readme
    assert "git clone --depth 1 https://github.com/YaofeiHe/forge-manager-public.git" in readme
    assert "for skill in skills/forge-manager-workflow; do" in readme
    assert 'cp -R "$tmp/repo/$skill" "$HOME/.agents/skills/"' in readme
    assert "codex plugin marketplace add" not in readme
    assert "codex plugin add" not in readme
    assert "codex plugin install" not in readme
    assert validation["status"] == "skipped"
    assert validation["workflow_assets"]["status"] == "completed"
    assert not (project / ".agents").exists()
    assert not (project / "plugins").exists()


def test_public_validation_blocks_missing_readme(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()

    validation = validate_public_staging(staging, {"public_required_paths": [], "public_validation": {"enabled": False}})

    assert validation["status"] == "blocked"
    assert validation["blocked_reason"] == "public_required_path_missing"
    assert validation["missing_paths"] == ["README.md"]


def test_public_validation_blocks_non_copyable_readme(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "README.md").write_text("Install from <PRIVATE_REPO>-public and see <LOCAL_PATH_REDACTED>", encoding="utf-8")

    validation = validate_public_staging(
        staging,
        {
            "private_repo": "owner/private",
            "public_repo": "owner/private-public",
            "public_required_paths": ["README.md"],
            "public_validation": {"enabled": False},
        },
    )

    assert validation["status"] == "blocked"
    assert validation["blocked_reason"] == "public_readme_invalid"


def test_public_source_discovery_finds_flat_package_from_pyproject(tmp_path: Path) -> None:
    project = tmp_path / "codpm"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "codpm"',
                "[project.scripts]",
                'codpm = "codpm.cli:main"',
                "[tool.pytest.ini_options]",
                'testpaths = ["tests"]',
            ]
        ),
        encoding="utf-8",
    )
    (project / "codpm").mkdir()
    (project / "codpm" / "__init__.py").write_text("", encoding="utf-8")
    (project / "codpm" / "cli.py").write_text("def main(): pass\n", encoding="utf-8")
    (project / "tests").mkdir()

    discovery = discover_public_source_roots(project, {"public_source_roots": "auto"})

    assert "codpm/" in discovery["source_roots"]
    assert "tests/" in discovery["source_roots"]
    assert "codpm.cli" in discovery["import_modules"]


def test_public_staging_validation_blocks_missing_required_path(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "README.md").write_text("# demo\n", encoding="utf-8")

    validation = validate_public_staging(
        staging,
        {
            "public_required_paths": ["README.md", "codpm/"],
            "public_validation": {"enabled": True, "mode": "real", "install_command": "", "smoke_commands": [], "test_commands": []},
        },
    )

    assert validation["status"] == "blocked"
    assert validation["blocked_reason"] == "public_required_path_missing"
    assert validation["missing_paths"] == ["codpm/"]


def test_public_validation_failure_prevents_push(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_github_sync_config(project, private_repo="owner/private", public_repo="owner/public")
    config_path = project / ".github" / "nexus-sync.json"
    config = load_github_sync_config(project) or {}
    config["public_required_paths"] = ["README.md", "codpm/"]
    config["public_validation"] = {"enabled": True, "mode": "real", "install_command": "", "smoke_commands": [], "test_commands": []}
    config_path.write_text(__import__("json").dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (project / "README.md").write_text(_public_readme("owner/public"), encoding="utf-8")
    pushed = {"called": False}

    def fake_sync_public(*args, **kwargs):
        pushed["called"] = True
        return {"schema": "nexus.github_public_sync.v1", "status": "completed", "reason": "pushed_public"}

    monkeypatch.setattr(nexus_runner, "sync_public", fake_sync_public)

    interaction = Runner(tmp_path).github_sync_public(project, confirm=True)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "public_required_path_missing"
    assert pushed["called"] is False


def test_public_validation_default_install_allows_build_backend_isolation(monkeypatch, tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "scripts" / "lab").mkdir(parents=True)
    (staging / "README.md").write_text(_public_readme("owner/public", command="python -m demo --help"), encoding="utf-8")
    (staging / "pyproject.toml").write_text(
        "\n".join(
            [
                "[build-system]",
                'requires = ["hatchling"]',
                'build-backend = "hatchling.build"',
                "[project]",
                'name = "demo"',
                'version = "0.1.0"',
            ]
        ),
        encoding="utf-8",
    )
    commands: list[str] = []
    command_envs: list[dict[str, str]] = []

    monkeypatch.setenv("NEXUS_TAVILY_KEY_FILE", "/tmp/secret-tavily-key")
    monkeypatch.setenv("SERPAPI_API_KEY", "secret-serpapi-key")
    monkeypatch.setenv("NEXUS_CHINESE_WEB_PROVIDERS", "tavily,serpapi_baidu")
    monkeypatch.setenv("NEXUS_PUBLIC_VALIDATION_MARKER", "kept")
    monkeypatch.setenv("PYTHONPATH", "/private/source/checkout")
    monkeypatch.setattr(github_sync, "_create_validation_venv", lambda path: {"schema": "nexus.public_validation_venv.v1", "status": "completed", "path": str(path)})
    monkeypatch.setattr(github_sync, "_venv_bin", lambda path: str(path / "bin"))

    def fake_run_validation_command(command: str, *, cwd: Path, env: dict[str, str]) -> dict[str, object]:
        commands.append(command)
        command_envs.append(dict(env))
        return {"command": command, "cwd": str(cwd), "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(github_sync, "_run_validation_command", fake_run_validation_command)

    validation = validate_public_staging(
        staging,
        {
            "public_repo": "owner/public",
            "public_required_paths": ["README.md", "scripts/lab/"],
            "_public_discovery": {"required_paths": ["scripts/lab/"]},
            "public_validation": {"enabled": True, "mode": "real", "allow_network": True, "smoke_commands": [], "test_commands": []},
        },
    )

    assert validation["status"] == "completed"
    assert commands[0] == "python -m pip install --no-deps ."
    assert "--no-build-isolation" not in commands[0]
    assert "NEXUS_TAVILY_KEY_FILE" not in command_envs[0]
    assert "SERPAPI_API_KEY" not in command_envs[0]
    assert "PYTHONPATH" not in command_envs[0]
    assert command_envs[0]["NEXUS_CHINESE_WEB_PROVIDERS"] == "tavily,serpapi_baidu"
    assert command_envs[0]["NEXUS_PUBLIC_VALIDATION_MARKER"] == "kept"


def test_public_validation_command_not_found_is_structured(tmp_path: Path) -> None:
    result = github_sync._run_validation_command("definitely-missing-nexus-command --help", cwd=tmp_path, env={"PATH": ""})

    assert result["returncode"] == 127
    assert result["command"] == "definitely-missing-nexus-command --help"
    assert "No such file" in result["stderr_tail"] or "not found" in result["stderr_tail"]


def test_public_staging_validation_imports_flat_package(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text(_public_readme("owner/public"), encoding="utf-8")
    (project / "codpm").mkdir()
    (project / "codpm" / "__init__.py").write_text("", encoding="utf-8")
    (project / "codpm" / "cli.py").write_text("def main(): pass\n", encoding="utf-8")
    config = {
        "public_allowlist": ["README.md", "codpm/"],
        "public_required_paths": ["README.md", "codpm/"],
        "public_validation": {
            "enabled": True,
            "mode": "real",
            "install_command": "",
            "import_modules": ["codpm", "codpm.cli"],
            "smoke_commands": [],
            "test_commands": [],
        },
    }
    staging = tmp_path / "staging"
    staged = prepare_public_staging(project, config, staging)
    validation = validate_public_staging(staging, {**config, "_public_discovery": staged["discovery"]})

    assert staged["status"] == "completed"
    assert validation["status"] == "completed"


def test_public_validation_does_not_pollute_release_staging(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# demo\n\n<!-- nexus:public-install -->\n## Public Install\n\npython -m codpm --help\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "\n".join(
            [
                "[build-system]",
                'requires = ["setuptools>=68"]',
                'build-backend = "setuptools.build_meta"',
                "[project]",
                'name = "codpm"',
                'version = "0.1.0"',
                "[tool.setuptools.packages.find]",
                'include = ["codpm*"]',
            ]
        ),
        encoding="utf-8",
    )
    (project / "codpm").mkdir()
    (project / "codpm" / "__init__.py").write_text("", encoding="utf-8")
    config = {
        "public_allowlist": ["README.md", "pyproject.toml", "codpm/"],
        "public_required_paths": ["README.md", "pyproject.toml", "codpm/"],
        "public_validation": {
            "enabled": True,
            "mode": "real",
            "allow_network": False,
            "install_command": "python -m pip install --no-deps --no-build-isolation .",
            "import_modules": ["codpm"],
            "smoke_commands": [],
            "test_commands": [],
        },
    }

    staging = tmp_path / "staging"
    staged = prepare_public_staging(project, config, staging)
    validation = validate_public_staging(staging, {**config, "_public_discovery": staged["discovery"]})

    assert validation["status"] == "completed"
    assert not (staging / "build").exists()
    assert not list(staging.glob("*.egg-info"))
    assert (tmp_path / "staging-validation-workspace" / "build").exists()
    assert list((tmp_path / "staging-validation-workspace").glob("*.egg-info"))
    assert assert_public_release_tree_clean(staging, config)["status"] == "completed"


def test_public_release_tree_blocks_generated_artifacts(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "README.md").write_text("# demo\n", encoding="utf-8")
    (staging / "build" / "lib").mkdir(parents=True)
    (staging / "build" / "lib" / "demo.py").write_text("x = 1\n", encoding="utf-8")
    (staging / "codpm.egg-info").mkdir()
    (staging / "codpm.egg-info" / "PKG-INFO").write_text("Metadata-Version: 2.1\n", encoding="utf-8")

    result = assert_public_release_tree_clean(staging, {})

    assert result["status"] == "blocked"
    assert result["reason"] == "public_generated_artifact_found"
    assert "build/lib/demo.py" in result["generated_paths"]
    assert "codpm.egg-info/PKG-INFO" in result["generated_paths"]


def test_public_sync_blocks_dirty_release_tree_before_push(monkeypatch, tmp_path: Path) -> None:
    from nexus.github_sync import sync_public

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "README.md").write_text("# demo\n", encoding="utf-8")
    (staging / "__pycache__").mkdir()
    (staging / "__pycache__" / "demo.pyc").write_bytes(b"cache")
    calls = {"push": False}

    monkeypatch.setattr("nexus.github_sync.gh_status", lambda: {"status": "ok"})
    monkeypatch.setattr("nexus.github_sync._gh_setup_git", lambda: {"schema": "nexus.gh_git_auth_setup.v1", "status": "completed"})
    monkeypatch.setattr("nexus.github_sync._ensure_single_github_repository", lambda *args, **kwargs: {"schema": "nexus.github_single_repo_setup.v1", "status": "completed", "reason": "repo_exists"})

    def fake_run(args, **kwargs):
        if "push" in args:
            calls["push"] = True
        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""
        return Completed()

    monkeypatch.setattr(nexus_runner.subprocess, "run", fake_run)

    result = sync_public(tmp_path / "project", {"public_repo": "owner/public"}, staging)

    assert result["status"] == "blocked"
    assert result["reason"] == "public_generated_artifact_found"
    assert calls["push"] is False


def test_public_staging_sanitizes_private_metadata_before_scan(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text(
        "\n".join(
            [
                "private repo owner/private",
                "local path <LOCAL_PATH_REDACTED>",
                "feishu <FEISHU_URL_REDACTED>",
                "run <NEXUS_RUN_ID>",
            ]
        ),
        encoding="utf-8",
    )
    config = {
        "private_repo": "owner/private",
        "public_repo": "owner/public",
        "public_allowlist": ["README.md"],
        "public_required_paths": ["README.md"],
        "public_validation": {"enabled": False},
    }

    staged = prepare_public_staging(project, config, tmp_path / "staging")
    readme = (tmp_path / "staging" / "README.md").read_text(encoding="utf-8")

    assert staged["status"] == "completed"
    assert "owner/private" not in readme
    assert "<LOCAL_PATH_REDACTED>" not in readme
    assert "feishu.cn" not in readme
    assert "<NEXUS_RUN_ID>" not in readme
    assert staged["sanitization"]["replacement_count"] >= 4


def test_public_metadata_scan_blocks_when_sanitization_disabled(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "README.md").write_text(
        "See <LOCAL_PATH_REDACTED> and <FEISHU_URL_REDACTED> for context.\n",
        encoding="utf-8",
    )

    scan = scan_public_staging(staging, {"public_release": {"sanitize": False, "metadata_policy": "block"}})

    assert scan["findings"]
    assert {finding["pattern"] for finding in scan["metadata_findings"]} == {"local_absolute_path", "feishu_url"}


def test_public_fresh_clone_validation_rechecks_downloadable_artifact(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "README.md").write_text("# demo\n", encoding="utf-8")
    config = {
        "public_required_paths": ["README.md", "codpm/"],
        "public_validation": {"enabled": True, "mode": "real", "install_command": "", "smoke_commands": [], "test_commands": []},
    }

    validation = validate_public_fresh_clone(staging, config, tmp_path / "fresh")

    assert validation["schema"] == "nexus.github_public_fresh_clone_validation.v1"
    assert validation["status"] == "blocked"
    assert validation["blocked_reason"] == "public_required_path_missing"
    assert validation["fresh_clone"].endswith("fresh")


def test_public_fresh_clone_failure_prevents_push(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_github_sync_config(project, private_repo="owner/private", public_repo="owner/public")
    config_path = project / ".github" / "nexus-sync.json"
    config = load_github_sync_config(project) or {}
    config["public_allowlist"] = ["README.md"]
    config["public_required_paths"] = ["README.md"]
    config["public_validation"] = {"enabled": False}
    config_path.write_text(__import__("json").dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (project / "README.md").write_text(_public_readme("owner/public"), encoding="utf-8")
    pushed = {"called": False}

    monkeypatch.setattr(
        nexus_runner,
        "validate_public_fresh_clone",
        lambda staging, config, clone_dir: {"schema": "nexus.github_public_fresh_clone_validation.v1", "status": "blocked", "blocked_reason": "fresh_clone_failed"},
    )

    def fake_sync_public(*args, **kwargs):
        pushed["called"] = True
        return {"schema": "nexus.github_public_sync.v1", "status": "completed", "reason": "pushed_public"}

    monkeypatch.setattr(nexus_runner, "sync_public", fake_sync_public)

    interaction = Runner(tmp_path).github_sync_public(project, confirm=True)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "fresh_clone_failed"
    assert pushed["called"] is False


def test_github_bootstrap_without_config_blocks(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    interaction = Runner(tmp_path).github_sync_bootstrap(project)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "github_sync_config_missing_and_repo_target_unknown"


def test_github_auto_private_bootstraps_known_self_project(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "nexus"
    project.mkdir()

    def fake_bootstrap(project_path: Path, config: dict[str, object], *, create_remote_repos: bool = True, commit_message: str = "") -> dict[str, object]:
        assert config["private_repo"] == "<PRIVATE_REPO>"
        assert config["public_repo"] == "YaofeiHe/nexus-public"
        return {"schema": "nexus.github_bootstrap.v1", "status": "completed", "reason": "bootstrapped_and_pushed_private"}

    monkeypatch.setattr(nexus_runner, "bootstrap_project", fake_bootstrap)
    interaction = Runner(tmp_path).github_sync_auto_private(project)

    assert interaction["previous_task_status"] == "completed"
    assert (project / ".github" / "nexus-sync.json").exists()


def test_github_auto_private_unknown_project_requires_repo_targets(tmp_path: Path) -> None:
    project = tmp_path / "ordinary"
    project.mkdir()

    interaction = Runner(tmp_path).github_sync_auto_private(project)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "github_sync_config_missing_and_repo_target_unknown"


def test_github_auto_private_enters_auth_flow_when_gh_auth_required(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "nexus"
    project.mkdir()

    monkeypatch.setattr(nexus_runner, "bootstrap_project", lambda *args, **kwargs: {"schema": "nexus.github_bootstrap.v1", "status": "blocked", "reason": "gh_auth_required"})
    monkeypatch.setattr(
        nexus_runner,
        "run_github_auth_login",
        lambda project_path, *, browser_mode="native": {
            "schema": "nexus.github_auth_status.v1",
            "state": "LOGIN_INCOMPLETE",
            "device_url": "https://github.com/login/device",
            "device_code": "ABCD-1234",
            "current_instruction": "manual auth required",
            "request_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "status_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "log_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "host_capability_request_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
        },
    )

    interaction = Runner(tmp_path).github_sync_auto_private(project)

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "LOGIN_INCOMPLETE"
    assert "ABCD-1234" in interaction["previous_task_output"]


def test_github_auto_private_missing_self_config_recovers_as_bootstrap(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "nexus"
    project.mkdir()
    calls = {"bootstrap": 0, "auto_private": 0}

    def fake_bootstrap(project_path: Path, config: dict[str, object], *, create_remote_repos: bool = True, commit_message: str = "") -> dict[str, object]:
        calls["bootstrap"] += 1
        if calls["bootstrap"] == 1:
            return {"schema": "nexus.github_bootstrap.v1", "status": "blocked", "reason": "gh_auth_required"}
        return {"schema": "nexus.github_bootstrap.v1", "status": "completed", "reason": "bootstrapped_and_pushed_private"}

    def fake_auto_private_sync(*args, **kwargs) -> dict[str, object]:
        calls["auto_private"] += 1
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "completed", "reason": "private_synced"}

    monkeypatch.setattr(nexus_runner, "bootstrap_project", fake_bootstrap)
    monkeypatch.setattr(nexus_runner, "auto_private_sync", fake_auto_private_sync)
    monkeypatch.setattr(
        nexus_runner,
        "run_github_auth_login",
        lambda project_path, *, browser_mode="native": {
            "schema": "nexus.github_auth_status.v1",
            "state": "LOGIN_INCOMPLETE",
            "device_url": "https://github.com/login/device",
            "device_code": "ABCD-1234",
            "current_instruction": "manual auth required",
            "request_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "status_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "log_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
            "host_capability_request_path": str(project / "<NEXUS_ARTIFACT_PATH>"),
        },
    )

    interaction = Runner(tmp_path).github_sync_auto_private(project)
    continued = Runner(tmp_path).continue_after_input(interaction["run_id"], note="github login done")

    assert continued["previous_task_status"] == "completed"
    assert calls["bootstrap"] == 2
    assert calls["auto_private"] == 0


def test_system_showcase_publish_feishu_uses_autosync(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[dict[str, object]] = []

    def fake_autosync(*args, **kwargs) -> dict[str, object]:
        calls.append(kwargs)
        return {"schema": "nexus.feishu_autosync_result.v1", "status": "blocked", "reason": "feishu_config_missing"}

    monkeypatch.setattr(nexus_runner, "run_feishu_autosync", fake_autosync)

    interaction = Runner(tmp_path).system_showcase_publish_feishu(project)
    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "feishu_publish_confirmation_required"

    confirmed = Runner(tmp_path).system_showcase_publish_feishu(project, confirm=True)

    assert confirmed["previous_task_status"] == "blocked"
    assert confirmed["blocked_reason"] == "feishu_config_missing"
    assert calls
    assert calls[0]["event_type"] == "system_showcase_sync"
    assert calls[0]["guide_paths"] == [project / "docs" / "system" / "architecture.md"]
    assert (project / "docs" / "system" / "architecture.md").exists()


def test_feishu_configure_requires_no_secret_value(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    app_id = tmp_path / "feishu_appid"
    app_secret = tmp_path / "feishu_appsecret"
    app_id.write_text("cli_xxx", encoding="utf-8")
    app_secret.write_text("very-secret-value", encoding="utf-8")
    monkeypatch.setattr(feishu_setup, "run_doctor", lambda project_path, *, config=None, no_network=False: {"schema": "nexus.feishu_doctor.v1", "status": "completed", "checks": {"app_id_loaded": True, "app_secret_loaded": True, "token_request_success": True, "token_expire_seconds": 7200}})
    interaction = Runner(tmp_path).feishu_configure(project, app_id_path=str(app_id), app_secret_path=str(app_secret), folder_token="fld")
    assert interaction["previous_task_status"] == "completed"
    text = (project / ".nexus" / "feishu.json").read_text(encoding="utf-8")
    assert str(app_id) in text
    assert str(app_secret) in text
    assert "very-secret-value" not in text


def test_feishu_setup_guide_without_credentials(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    interaction = Runner(tmp_path).feishu_setup_flow(project, app_id_path=str(tmp_path / "missing_id"), app_secret_path=str(tmp_path / "missing_secret"))

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "feishu_credentials_missing"
    refs = "\n".join(str(ref) for ref in interaction["artifact_refs"])
    assert "feishu_setup_guide.json" in refs


def test_feishu_setup_writes_config_and_redacts_secret(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    app_id = tmp_path / "feishu_appid"
    app_secret = tmp_path / "feishu_appsecret"
    app_id.write_text("cli_xxx", encoding="utf-8")
    app_secret.write_text("very-secret-value", encoding="utf-8")

    def fake_doctor(project_path: Path, *, config=None, no_network: bool = False) -> dict[str, object]:
        return {
            "schema": "nexus.feishu_doctor.v1",
            "status": "completed",
            "checks": {
                "app_id_loaded": True,
                "app_secret_loaded": True,
                "token_request_success": True,
                "token_expire_seconds": 7200,
            },
        }

    monkeypatch.setattr(feishu_setup, "run_doctor", fake_doctor)
    interaction = Runner(tmp_path).feishu_setup_flow(project, app_id_path=str(app_id), app_secret_path=str(app_secret), no_network=True)

    assert interaction["previous_task_status"] == "completed"
    config_text = (project / ".nexus" / "feishu.json").read_text(encoding="utf-8")
    assert "very-secret-value" not in config_text
    run_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in (tmp_path / ".data" / "runs").glob("*/tool_results/*.json"))
    assert "very-secret-value" not in run_text


def test_feishu_setup_supports_folder_token_path_url(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    app_id = tmp_path / "feishu_appid"
    app_secret = tmp_path / "feishu_appsecret"
    folder_token = tmp_path / "folder_token"
    app_id.write_text("cli_xxx", encoding="utf-8")
    app_secret.write_text("very-secret-value", encoding="utf-8")
    folder_token.write_text("<FEISHU_URL_REDACTED>", encoding="utf-8")

    monkeypatch.setattr(feishu_setup, "run_doctor", lambda project_path, *, config=None, no_network=False: {"schema": "nexus.feishu_doctor.v1", "status": "completed", "checks": {"app_id_loaded": True, "app_secret_loaded": True, "folder_token_loaded": True, "token_request_success": "not_run"}})
    interaction = Runner(tmp_path).feishu_setup_flow(project, app_id_path=str(app_id), app_secret_path=str(app_secret), folder_token_path=str(folder_token), no_network=True)

    assert interaction["previous_task_status"] == "completed"
    config_text = (project / ".nexus" / "feishu.json").read_text(encoding="utf-8")
    assert str(folder_token) in config_text
    assert "fldabcdef123" not in config_text


def test_feishu_doctor_no_network(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    app_id = tmp_path / "feishu_appid"
    app_secret = tmp_path / "feishu_appsecret"
    app_id.write_text("cli_xxx", encoding="utf-8")
    app_secret.write_text("very-secret-value", encoding="utf-8")
    Runner(tmp_path).feishu_setup_flow(project, app_id_path=str(app_id), app_secret_path=str(app_secret), no_network=True)

    interaction = Runner(tmp_path).feishu_doctor_flow(project, no_network=True)

    assert interaction["previous_task_status"] == "completed"
    assert "not_run" in str(interaction["previous_task_output"])


def test_feishu_record_writes_local_markdown_then_autosync(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[dict[str, object]] = []

    def fake_autosync(*args, **kwargs) -> dict[str, object]:
        calls.append(kwargs)
        return {"schema": "nexus.feishu_autosync_result.v1", "status": "blocked", "reason": "feishu_config_missing"}

    monkeypatch.setattr(nexus_runner, "run_feishu_autosync", fake_autosync)

    interaction = Runner(tmp_path).feishu_record_flow(project, provider_name="mock", content="记录当前状态")

    assert interaction["previous_task_status"] == "blocked"
    assert interaction["blocked_reason"] == "feishu_config_missing"
    assert (project / "docs" / "feishu-records.md").exists()
    assert "记录当前状态" in (project / "docs" / "feishu-records.md").read_text(encoding="utf-8")
    assert calls
    assert calls[0]["event_type"] == "manual_record"


def test_feishu_invoke_routes_setup_and_record(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    setup = Runner(tmp_path).invoke(f"使用 $nexus-workflow 初始化飞书配置 项目路径 {project}")
    assert setup["previous_task_status"] in {"blocked", "completed"}
    if setup["previous_task_status"] == "blocked":
        assert setup["blocked_reason"] in {"feishu_credentials_missing", "real_model_provider_not_configured", "network_error", "feishu_auth_failed"}

    record = Runner(tmp_path).invoke(f"使用 $nexus-workflow 进行飞书记录 项目路径 {project}：记录当前状态")
    assert record["previous_task_status"] in {"blocked", "completed"}
    if record["previous_task_status"] == "blocked":
        assert record["blocked_reason"] in {"feishu_config_missing", "real_model_provider_not_configured"}
