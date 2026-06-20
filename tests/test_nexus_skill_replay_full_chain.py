from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_REPLAY = REPO_ROOT / "scripts" / "lab" / "run_nexus_skill_replay.py"


def evaluate_response(prompt: str, response: str) -> dict[str, object]:
    spec = importlib.util.spec_from_file_location("run_nexus_skill_replay", SKILL_REPLAY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.evaluate_response(prompt, response)


def test_skill_replay_includes_full_wljob_command_file() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--case-id",
            "real_07_wljob_heat_local_sequence",
            "--include-wljob-command-file",
            "--dry-run-plan",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    wljob_steps = [step for step in plan["steps"] if step["case_id"] == "wljob_full_skill_chain"]
    source_steps = {step["source_step"] for step in wljob_steps}
    assert plan["wljob_command_file_step_count"] >= 35
    assert len(wljob_steps) == plan["wljob_command_file_step_count"]
    assert {str(index) for index in range(35)}.issubset(source_steps)
    assert "11A" in source_steps
    assert all(step["prompt"].startswith("$nexus-workflow") for step in wljob_steps)
    assert any(step["requires_external_side_effect"] for step in wljob_steps)
    assert any(not step["queued"] and step["skip_reason"] == "external_side_effect_refused_by_default" for step in wljob_steps)
    by_source = {step["source_step"]: step for step in wljob_steps}
    for source_step in ("21", "27", "28", "30", "34"):
        assert by_source[source_step]["requires_external_side_effect"] is True
        assert by_source[source_step]["queued"] is False
        assert by_source[source_step]["dispatch_status"] == "gated"
    assert plan["replay_execution_model"] == "multi_agent_v1_thread"
    assert plan["replay_execution_models"] == ["queue-only", "codex_exec_subprocess", "multi_agent_v1_thread"]
    assert plan["requires_real_thread"] is True
    assert all(step["replay_execution_model"] == "queue-only" for step in wljob_steps)
    assert all(len(step["problem_axes"]) == 16 for step in wljob_steps)


def test_skill_replay_check_skill_uses_direct_skill_doctor_and_keeps_audit_suffix() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--case-id",
            "real_07_wljob_heat_local_sequence",
            "--dry-run-plan",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    check_skill = next(step for step in plan["steps"] if step["step_id"] == "check_skill")
    assert check_skill["expected_cli_command"] == "skill doctor"
    assert check_skill["safe_replay_command"] is True
    assert "全局 Nexus 问题验收" in check_skill["prompt"]
    assert check_skill["prompt"].startswith("$nexus-workflow 检查当前 nexus workflow 是否安装并可用")
    approve_online = next(step for step in plan["steps"] if step["step_id"] == "approve_online_search_continue")
    assert approve_online["expected_cli_command"] == "approve-and-continue <run_id> online-search"
    assert approve_online["safe_replay_command"] is True
    view_report = next(step for step in plan["steps"] if step["step_id"] == "view_final_report")
    assert view_report["expected_cli_command"] == "report <run_id>"
    assert view_report["safe_replay_command"] is True


def test_skill_replay_send_queue_preserves_gated_command_file_steps(tmp_path: Path) -> None:
    init = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--case-id",
            "real_07_wljob_heat_local_sequence",
            "--include-wljob-command-file",
            "--e2e-root",
            str(tmp_path),
            "--init-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert init.returncode == 0, init.stderr
    replay_dir = Path(init.stdout.strip()).parent
    queue = [
        json.loads(line)
        for line in (replay_dir / "send_queue.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    command_file_rows = [row for row in queue if row["case_id"] == "wljob_full_skill_chain"]
    assert {str(index) for index in range(35)}.issubset({row["source_step"] for row in command_file_rows})
    assert all(row["replay_execution_model"] == "queue-only" for row in command_file_rows)
    assert all(row["target_replay_execution_model"] == "multi_agent_v1_thread" for row in command_file_rows)
    gated = [row for row in command_file_rows if row["queued"] is False]
    assert gated
    assert all(row["dispatch_status"] == "gated" for row in gated)
    assert any(row["source_step"] == "22" and row["skip_reason"] == "external_side_effect_refused_by_default" for row in gated)
    prompt_turns = [
        json.loads(line)
        for line in (replay_dir / "prompt_turns.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(prompt_turns) == len(queue)
    assert {row["replay_execution_model"] for row in prompt_turns} == {"queue-only"}


def test_codex_exec_record_does_not_satisfy_real_thread_replay_contract(tmp_path: Path) -> None:
    init = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--case-id",
            "real_07_wljob_heat_local_sequence",
            "--e2e-root",
            str(tmp_path),
            "--init-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert init.returncode == 0, init.stderr
    replay_state = Path(init.stdout.strip())
    replay_dir = replay_state.parent
    prompt = tmp_path / "prompt.txt"
    response = tmp_path / "response.txt"
    prompt.write_text("$nexus-workflow 查看状态\n", encoding="utf-8")
    response.write_text("状态: completed\n输出: run_id abc\n下一步: $nexus-workflow 查看 abc 的状态\n", encoding="utf-8")

    record = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--record-turn",
            "--replay-dir",
            str(replay_dir),
            "--thread-id",
            "codex-exec-1",
            "--case-id-record",
            "case",
            "--step-id",
            "step",
            "--prompt-file",
            str(prompt),
            "--response-file",
            str(response),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert record.returncode == 0, record.stderr
    turn = json.loads(Path(record.stdout.strip()).read_text(encoding="utf-8"))
    assert turn["record_source"] == "codex_exec_subprocess"
    assert turn["replay_execution_model"] == "codex_exec_subprocess"
    assert turn["satisfies_real_thread_replay_contract"] is False

    evaluate = subprocess.run(
        [sys.executable, str(SKILL_REPLAY), "--evaluate-run", "--replay-dir", str(replay_dir)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert evaluate.returncode == 0, evaluate.stderr
    evaluation = json.loads(Path(evaluate.stdout.strip()).read_text(encoding="utf-8"))
    assert evaluation["satisfies_skill_output_contract"] is True
    assert evaluation["satisfies_real_thread_replay_contract"] is False
    assert evaluation["record_sources"] == ["codex_exec_subprocess"]
    assert evaluation["execution_model_counts"]["queue-only"] > 0
    assert evaluation["execution_model_counts"]["codex_exec_subprocess"] == 1
    assert "multi_agent_v1_thread" not in evaluation["execution_model_counts"]


def test_multi_agent_v1_record_satisfies_real_thread_replay_contract(tmp_path: Path) -> None:
    init = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--case-id",
            "real_07_wljob_heat_local_sequence",
            "--e2e-root",
            str(tmp_path),
            "--init-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert init.returncode == 0, init.stderr
    replay_dir = Path(init.stdout.strip()).parent
    prompt = tmp_path / "prompt.txt"
    response = tmp_path / "response.txt"
    prompt.write_text("$nexus-workflow 查看状态\n", encoding="utf-8")
    response.write_text("状态: completed\n输出: run_id abc\n下一步: $nexus-workflow 查看 abc 的状态\n", encoding="utf-8")

    record = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--record-turn",
            "--replay-dir",
            str(replay_dir),
            "--thread-id",
            "019multiagentexample",
            "--submission-id",
            "submission-1",
            "--completion-id",
            "completion-1",
            "--final-status",
            "done",
            "--case-id-record",
            "case",
            "--step-id",
            "step",
            "--prompt-file",
            str(prompt),
            "--response-file",
            str(response),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert record.returncode == 0, record.stderr
    turn = json.loads(Path(record.stdout.strip()).read_text(encoding="utf-8"))
    assert turn["record_source"] == "multi_agent_v1_thread"
    assert turn["replay_execution_model"] == "multi_agent_v1_thread"
    assert turn["submission_id"] == "submission-1"
    assert turn["completion_id"] == "completion-1"
    assert turn["status_id"] == ""
    assert turn["final_status"] == "done"
    assert turn["legacy_message_id_fields_are_contract_fields"] is False
    assert turn["satisfies_real_thread_replay_contract"] is True
    assert turn["prompt_sha256"]
    assert turn["response_sha256"]
    assert turn["input_output_sha256"]

    evaluate = subprocess.run(
        [sys.executable, str(SKILL_REPLAY), "--evaluate-run", "--replay-dir", str(replay_dir)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert evaluate.returncode == 0, evaluate.stderr
    evaluation = json.loads(Path(evaluate.stdout.strip()).read_text(encoding="utf-8"))
    assert evaluation["satisfies_skill_output_contract"] is True
    assert evaluation["satisfies_real_thread_replay_contract"] is True
    assert evaluation["record_sources"] == ["multi_agent_v1_thread"]
    assert evaluation["execution_model_counts"]["queue-only"] > 0
    assert evaluation["execution_model_counts"]["multi_agent_v1_thread"] == 1
    assert evaluation["multi_agent_v1_thread_turn_count"] == 1
    assert evaluation["satisfies_traceability_contract"] is True
    traceability = json.loads(Path(evaluation["traceability_report_path"]).read_text(encoding="utf-8"))
    assert traceability["status"] == "pass"
    assert traceability["skill_replay_turns"][0]["prompt_sha256"] == turn["prompt_sha256"]


def test_multi_agent_v1_record_accepts_status_id_without_completion_id(tmp_path: Path) -> None:
    init = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--case-id",
            "real_07_wljob_heat_local_sequence",
            "--e2e-root",
            str(tmp_path),
            "--init-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert init.returncode == 0, init.stderr
    replay_dir = Path(init.stdout.strip()).parent
    prompt = tmp_path / "prompt.txt"
    response = tmp_path / "response.txt"
    prompt.write_text("$nexus-workflow 查看状态\n", encoding="utf-8")
    response.write_text("状态: completed\n输出: run_id abc\n下一步: $nexus-workflow 查看 abc 的状态\n", encoding="utf-8")

    record = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--record-turn",
            "--replay-dir",
            str(replay_dir),
            "--thread-id",
            "019multiagentexample",
            "--record-source",
            "multi_agent_v1_thread",
            "--submission-id",
            "submission-1",
            "--status-id",
            "status-1",
            "--final-status",
            "done",
            "--case-id-record",
            "case",
            "--step-id",
            "step",
            "--prompt-file",
            str(prompt),
            "--response-file",
            str(response),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert record.returncode == 0, record.stderr
    turn = json.loads(Path(record.stdout.strip()).read_text(encoding="utf-8"))
    assert turn["completion_id"] == ""
    assert turn["status_id"] == "status-1"
    assert turn["satisfies_real_thread_replay_contract"] is True


def test_skill_check_prompt_fails_when_routed_to_github_public(tmp_path: Path) -> None:
    init = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--case-id",
            "real_07_wljob_heat_local_sequence",
            "--e2e-root",
            str(tmp_path),
            "--init-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert init.returncode == 0, init.stderr
    replay_dir = Path(init.stdout.strip()).parent
    prompt = tmp_path / "prompt.txt"
    response = tmp_path / "response.txt"
    prompt.write_text("$nexus-workflow 检查当前 nexus workflow 是否安装并可用\n", encoding="utf-8")
    response.write_text(
        "上一任务状态：blocked\n上一任务输出：缺少 `.github/nexus-sync.json`，current_node=github_sync_public。\n下一任务提示：配置 GitHub sync。\n",
        encoding="utf-8",
    )

    record = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--record-turn",
            "--replay-dir",
            str(replay_dir),
            "--thread-id",
            "019multiagentexample",
            "--record-source",
            "multi_agent_v1_thread",
            "--submission-id",
            "submission-1",
            "--completion-id",
            "completion-1",
            "--final-status",
            "blocked",
            "--case-id-record",
            "case",
            "--step-id",
            "check_skill",
            "--prompt-file",
            str(prompt),
            "--response-file",
            str(response),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert record.returncode == 1
    turn = json.loads(Path(record.stdout.strip()).read_text(encoding="utf-8"))
    assert "unexpected_github_public_route_for_skill_check" in turn["verdict"]["missing"]


def test_replay_accepts_nexus_run_id_as_real_invocation_evidence(tmp_path: Path) -> None:
    init = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--case-id",
            "real_07_wljob_heat_local_sequence",
            "--e2e-root",
            str(tmp_path),
            "--init-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert init.returncode == 0, init.stderr
    replay_dir = Path(init.stdout.strip()).parent
    prompt = tmp_path / "prompt.txt"
    response = tmp_path / "response.txt"
    prompt.write_text("$nexus-workflow 继续 <NEXUS_RUN_ID>\n", encoding="utf-8")
    response.write_text(
        "上一任务状态：completed\n上一任务输出：报告已生成。\n下一任务提示：$nexus-workflow 查看 <NEXUS_RUN_ID> 的报告\n",
        encoding="utf-8",
    )

    record = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--record-turn",
            "--replay-dir",
            str(replay_dir),
            "--thread-id",
            "019multiagentexample",
            "--record-source",
            "multi_agent_v1_thread",
            "--submission-id",
            "submission-1",
            "--completion-id",
            "completion-1",
            "--final-status",
            "done",
            "--case-id-record",
            "case",
            "--step-id",
            "continue_after_online_search",
            "--prompt-file",
            str(prompt),
            "--response-file",
            str(response),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert record.returncode == 0, record.stderr
    turn = json.loads(Path(record.stdout.strip()).read_text(encoding="utf-8"))
    assert turn["verdict"]["status"] == "pass"


def test_diagnostic_skill_response_without_run_id_satisfies_output_contract() -> None:
    verdict = evaluate_response(
        "$nexus-workflow 查看当前可用的基座模型和 provider 状态\n",
        (
            "上一任务状态：completed\n"
            "上一任务输出：低强度 API 槽位：qwen-plus；高强度顺序：codex-cli -> API fallback。\n"
            "下一任务提示：$nexus-workflow 配置模型 openai\n"
        ),
    )

    assert verdict["status"] == "pass"
    assert "missing_evidence_of_real_nexus_invocation" not in verdict["missing"]
    assert verdict["checks"]["non_run_skill_response_without_mock"] is True


def test_artifact_only_next_step_does_not_require_skill_prompt() -> None:
    verdict = evaluate_response(
        "$nexus-workflow 候选去重/重排/重新排名\n",
        (
            "上一任务状态：completed\n"
            "上一任务输出：本地 Nexus CLI 已完成候选重排，"
            "python -m nexus.cli rerank-candidates latest 写出 ranked_candidates artifact。\n"
            "下一任务提示：查看候选排行：/private/tmp/nexus-replay/ranked_candidates.json\n"
        ),
    )

    assert verdict["status"] == "pass"
    assert "missing_next_skill_prompt" not in verdict["missing"]
    assert verdict["checks"]["has_artifact_next_action"] is True


def test_blocked_recovery_response_still_requires_skill_next_prompt() -> None:
    verdict = evaluate_response(
        "$nexus-workflow 恢复上一个中断 run\n",
        (
            "上一任务状态：blocked\n"
            "上一任务输出：provider preflight failed；run_id：<NEXUS_RUN_ID>。\n"
            "下一任务提示：修复 provider 权限后继续。\n"
        ),
    )

    assert verdict["status"] == "fail"
    assert "missing_next_skill_prompt" in verdict["missing"]
    assert verdict["checks"]["requires_workflow_next_prompt"] is True


def test_evaluate_run_writes_verix_traceability_index(tmp_path: Path) -> None:
    init = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--case-id",
            "real_07_wljob_heat_local_sequence",
            "--e2e-root",
            str(tmp_path),
            "--init-run",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert init.returncode == 0, init.stderr
    replay_dir = Path(init.stdout.strip()).parent
    prompt = tmp_path / "prompt.txt"
    response = tmp_path / "response.txt"
    prompt.write_text("$nexus-workflow 查看状态\n", encoding="utf-8")
    response.write_text("状态: completed\n输出: run_id abc\n下一步: $nexus-workflow 查看 abc 的状态\n", encoding="utf-8")
    record = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--record-turn",
            "--replay-dir",
            str(replay_dir),
            "--thread-id",
            "019multiagentexample",
            "--record-source",
            "multi_agent_v1_thread",
            "--submission-id",
            "submission-1",
            "--status-id",
            "status-1",
            "--final-status",
            "done",
            "--case-id-record",
            "case",
            "--step-id",
            "step",
            "--prompt-file",
            str(prompt),
            "--response-file",
            str(response),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert record.returncode == 0, record.stderr
    audit_dir = tmp_path / "verix_audit"
    (audit_dir / "checkpoints").mkdir(parents=True)
    (audit_dir / "model_requests").mkdir()
    output = audit_dir / "model_responses" / "intent_block_validated.json"
    output.parent.mkdir()
    output.write_text('{"schema":"verix.intent_block.v1"}', encoding="utf-8")
    (audit_dir / "checkpoints" / "intent_block.json").write_text(
        json.dumps(
            {
                "schema": "verix.checkpoint.v1",
                "node_id": "intent_block",
                "kind": "model",
                "status": "completed",
                "input_hash": "abc123",
                "output_refs": [str(output)],
            }
        ),
        encoding="utf-8",
    )
    (audit_dir / "model_requests" / "intent_block.json").write_text(
        json.dumps({"node_id": "intent_block", "prompt": "审计 prompt"}),
        encoding="utf-8",
    )

    evaluate = subprocess.run(
        [
            sys.executable,
            str(SKILL_REPLAY),
            "--evaluate-run",
            "--replay-dir",
            str(replay_dir),
            "--verix-audit-dir",
            str(audit_dir),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert evaluate.returncode == 0, evaluate.stderr
    evaluation = json.loads(Path(evaluate.stdout.strip()).read_text(encoding="utf-8"))
    traceability = json.loads(Path(evaluation["traceability_report_path"]).read_text(encoding="utf-8"))
    assert traceability["status"] == "pass"
    assert traceability["verix_audit"]["checkpoint_count"] == 1
    assert traceability["verix_audit"]["model_request_count"] == 1
    assert traceability["verix_audit"]["nodes"][0]["input_hash"] == "abc123"
