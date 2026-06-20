from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import json


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "lab" / "run_nexus_e2e_case.py"
LAB_LOOP = REPO_ROOT / "scripts" / "lab" / "run_nexus_lab_loop.py"
EVALUATOR = REPO_ROOT / "scripts" / "lab" / "evaluate_nexus_case.py"
PROBLEM_MATRIX = REPO_ROOT / "scripts" / "lab" / "nexus_problem_matrix.json"


def load_runner_module():
    spec = importlib.util.spec_from_file_location("run_nexus_e2e_case", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_lab_loop_module():
    spec = importlib.util.spec_from_file_location("run_nexus_lab_loop", LAB_LOOP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_evaluator_module():
    spec = importlib.util.spec_from_file_location("evaluate_nexus_case", EVALUATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_prepare_execution_directories_creates_project_parent_without_project(tmp_path: Path) -> None:
    module = load_runner_module()
    context = {
        "E2E_ROOT": str(tmp_path / "e2e"),
        "FIXTURE_DIR": str(tmp_path / "e2e" / "fixtures"),
        "PROJECT_PATH": str(tmp_path / "e2e" / "projects" / "project-under-test"),
    }
    result: dict[str, object] = {}

    module.prepare_execution_directories(context, result)

    assert (tmp_path / "e2e").is_dir()
    assert (tmp_path / "e2e" / "fixtures").is_dir()
    assert (tmp_path / "e2e" / "projects").is_dir()
    assert (tmp_path / "e2e" / "codex_home").is_dir()
    assert not (tmp_path / "e2e" / "projects" / "project-under-test").exists()
    assert str(tmp_path / "e2e" / "projects") in result["prepared_directories"]


def test_provider_config_defaults_to_current_repo_when_runtime_root_is_isolated(tmp_path: Path, monkeypatch) -> None:
    module = load_runner_module()
    current_repo = tmp_path / "current-nexus"
    runtime_root = tmp_path / "runtime-nexus"
    (current_repo / ".data" / "config" / "models").mkdir(parents=True)
    runtime_root.mkdir()
    monkeypatch.setattr(module, "REPO_ROOT", current_repo)
    args = SimpleNamespace(
        nexus_root=runtime_root,
        e2e_root=tmp_path / "e2e",
        provider_config_root=None,
        no_inherit_provider_config=False,
    )

    provider_config = module.resolve_provider_config(args)

    assert provider_config["inherit_enabled"] is True
    assert provider_config["source"] == "current_repo_default"
    assert provider_config["source_root"] == str(current_repo)
    assert provider_config["models_exists"] is True
    runtime_status = module.provider_config_runtime_status(provider_config, runtime_root)
    assert runtime_status["status"] == "ready"
    assert runtime_status["mode"] == "external_config_root"


def test_lab_environment_audits_provider_config_and_codex_mcp_skip(tmp_path: Path, monkeypatch) -> None:
    module = load_runner_module()
    monkeypatch.setenv("NEXUS_AUTO_SKIP_PROVIDERS", "codex-mcp,codexcli")
    context = {
        "E2E_ROOT": str(tmp_path / "e2e"),
        "FIXTURE_DIR": str(tmp_path / "e2e" / "fixtures"),
        "PROJECT_PATH": str(tmp_path / "e2e" / "projects" / "project-under-test"),
    }
    provider_config = {
        "inherit_enabled": True,
        "source_root": str(tmp_path / "real-nexus"),
        "models_dir": str(tmp_path / "real-nexus" / ".data" / "config" / "models"),
    }

    env, audit = module.build_lab_environment(context, provider_config=provider_config, preserve_codex_home=False)

    assert env["NEXUS_PROVIDER_CONFIG_ROOT"] == str(tmp_path / "real-nexus")
    assert audit["CODEX_HOME_mode"] == "isolated_lab"
    assert audit["codex_mcp_skip_active"] is True
    assert audit["skipped_providers"] == ["codex-cli", "codex-mcp"]


def test_resolve_final_project_path_uses_run_project_root_artifacts(tmp_path: Path) -> None:
    module = load_runner_module()
    nexus_root = tmp_path / "nexus"
    run_id = "run-test"
    run_dir = nexus_root / ".data" / "runs" / run_id
    run_dir.mkdir(parents=True)
    project = tmp_path / "e2e" / "projects" / "actual"
    (project / ".nexus").mkdir(parents=True)
    (project / ".nexus" / "board.md").write_text("# board\n", encoding="utf-8")
    (run_dir / "state.json").write_text(
        json.dumps({"status": "completed", "project_path": str(nexus_root), "target_path": str(project)}),
        encoding="utf-8",
    )
    context = {
        "E2E_ROOT": str(tmp_path / "e2e"),
        "PROJECT_PATH": str(tmp_path / "e2e" / "projects" / "project-under-test"),
    }

    resolved = module.resolve_final_project_path(nexus_root, run_id, context, [])

    assert resolved == (project.resolve(), "state.json:target_path")


def test_evaluator_uses_resolved_project_path_and_ignores_terminal_prompt_text(tmp_path: Path) -> None:
    module = load_evaluator_module()
    nexus_root = tmp_path / "nexus"
    run_id = "run-test"
    run_dir = nexus_root / ".data" / "runs" / run_id
    run_dir.mkdir(parents=True)
    project = tmp_path / "e2e" / "projects" / "actual"
    (project / ".nexus").mkdir(parents=True)
    (project / ".nexus" / "board.md").write_text("# board\n", encoding="utf-8")
    (run_dir / "interaction.json").write_text(
        json.dumps(
            {
                "previous_task_status": "completed",
                "lifecycle_status": "completed",
                "pending_actions": [],
                "next_task_prompt": "后续类似 recovery 失败会读取经验。",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    execution = {
        "schema": "nexus.lab.e2e_execution.v1",
        "case_id": "generic_case",
        "run_id": run_id,
        "nexus_root": str(nexus_root),
        "project_path": str(tmp_path / "e2e" / "projects" / "project-under-test"),
        "resolved_project_path": str(project),
        "raw_intent": "generic recovery monitor case",
    }
    execution_path = tmp_path / "execution.json"
    execution_path.write_text(json.dumps(execution), encoding="utf-8")
    args = SimpleNamespace(
        case=str(execution_path),
        nexus_root="",
        run_id="",
        run_dir="",
        project_path="",
        case_id="",
        raw_intent="",
        problem_matrix=str(PROBLEM_MATRIX),
    )

    ctx = module.discover_context(args)
    evidence = module.EvidenceBag([tmp_path, nexus_root, project, run_dir])
    result = module.evaluate_monitor_next_prompt(ctx, evidence)

    assert ctx.project_path == project
    assert ctx.project_path_source == "execution.resolved_project_path"
    assert result.status == "pass"
    assert "terminal completed" in result.observed


def test_evaluator_fails_empty_playbook_after_recovery_write(tmp_path: Path) -> None:
    module = load_evaluator_module()
    nexus_root = tmp_path / "nexus"
    run_id = "run-test"
    run_dir = nexus_root / ".data" / "runs" / run_id
    (run_dir / "tool_results").mkdir(parents=True)
    project = tmp_path / "e2e" / "projects" / "actual"
    (project / ".nexus").mkdir(parents=True)
    (project / "docs").mkdir()
    (project / ".nexus" / "recovery-playbook.json").write_text(
        json.dumps({"schema": "nexus.recovery_playbook.v1", "entries": []}),
        encoding="utf-8",
    )
    (project / "docs" / "recovery-records.md").write_text("# Recovery\n", encoding="utf-8")
    (run_dir / "tool_results" / "recovery_result.json").write_text(
        json.dumps({"schema": "nexus.recovery_result.v1", "status": "completed"}),
        encoding="utf-8",
    )
    (run_dir / "tool_results" / "recovery_playbook_write_result.json").write_text(
        json.dumps({"schema": "nexus.recovery_playbook_write_result.v1", "status": "completed"}),
        encoding="utf-8",
    )
    execution_path = tmp_path / "execution.json"
    execution_path.write_text(
        json.dumps(
            {
                "schema": "nexus.lab.e2e_execution.v1",
                "case_id": "generic_case",
                "run_id": run_id,
                "nexus_root": str(nexus_root),
                "resolved_project_path": str(project),
                "raw_intent": "recovery playbook 沉淀",
            }
        ),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        case=str(execution_path),
        nexus_root="",
        run_id="",
        run_dir="",
        project_path="",
        case_id="",
        raw_intent="",
        problem_matrix=str(PROBLEM_MATRIX),
    )

    ctx = module.discover_context(args)
    evidence = module.EvidenceBag([tmp_path, nexus_root, project, run_dir])
    result = module.evaluate_playbook_persistence(ctx, evidence)

    assert result.status == "fail"
    assert "no entries" in result.observed


def test_aborted_case_evaluation_maps_all_problem_axes() -> None:
    module = load_lab_loop_module()
    problem_matrix = {
        "schema": "nexus.lab.problem_matrix.v1",
        "source": "unit",
        "coverage_policy": {"case_policy": "all_cases_cover_all_problem_axes"},
        "problems": [
            {
                "id": "P01",
                "title": "one",
                "user_problem": "must report one",
                "evaluator_dimensions": ["monitor_next_prompt"],
                "nexus_surfaces": ["lab loop"],
                "required_evidence": ["evaluation.json"],
                "applies_to_every_case": True,
            },
            {
                "id": "P02",
                "title": "two",
                "user_problem": "must report two",
                "evaluator_dimensions": ["recovery_rebind"],
                "nexus_surfaces": ["modification_request.json"],
                "required_evidence": ["problem_axis_results"],
                "applies_to_every_case": True,
            },
        ],
    }
    case_result = {
        "case_id": "generic_case",
        "execution_path": "",
        "runner": {"returncode": 1, "stderr": "missing workspace parent"},
    }

    evaluation = module.build_aborted_case_evaluation({"id": "generic_case"}, case_result, problem_matrix)

    assert evaluation["verdict"] == "fail"
    assert [axis["id"] for axis in evaluation["problem_axis_results"]] == ["P01", "P02"]
    assert all(axis["status"] == "fail" for axis in evaluation["problem_axis_results"])
    assert "missing workspace parent" in evaluation["problem_axis_results"][0]["observed"]
