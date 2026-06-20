#!/usr/bin/env python3
"""Prepare and evaluate Codex-thread skill replay runs for Nexus lab cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CASES_FILE = SCRIPT_DIR / "nexus_real_project_cases.json"
DEFAULT_PROBLEM_MATRIX_FILE = SCRIPT_DIR / "nexus_problem_matrix.json"
DEFAULT_WLJOB_COMMAND_FILE = Path("<LOCAL_PATH_REDACTED>")
PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
NEXUS_RUN_ID_RE = re.compile(r"run-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}")
ARTIFACT_PATH_RE = re.compile(r"(?:/|~/)[^\s`'\"，。；：:]+[.](?:json|md|txt|csv|html|log|yaml|yml|pdf)\b")
NEXT_STEP_MARKERS = ("下一步", "下一任务提示", "next_prompt", "next prompt", "next step")
REAL_INVOCATION_MARKERS = ("python -m nexus.cli", "nexus.cli", "schema`: `nexus.", '"schema": "nexus.', "'schema': 'nexus.")
WORKFLOW_NEXT_REQUIRED_TERMS = (
    "awaiting_approval",
    "awaiting_external_user",
    "blocked",
    "code-change",
    "code change",
    "continue-after-input",
    "handoff-for-debug",
    "pending_actions",
    "provider preflight",
    "rebind-and-continue",
    "recover",
    "recovery",
    "审批",
    "恢复",
    "代码修改",
    "阻断",
)
SAFE_REPLAY_COMMANDS = {"invoke", "skill", "model", "init-project", "approve-and-continue", "research", "report", "board"}
EXTERNAL_COMMANDS = {"self-sync", "github-sync", "guide", "feishu", "system-showcase"}
GIT_REMOTE_WORDS = ("github", "gitlab", "gitee", "远程", "仓库")
WRITE_OR_SYNC_WORDS = ("同步", "发布", "push", "commit", "提交", "bootstrap", "自动同步", "双仓")
FEISHU_WORDS = ("飞书", "feishu")
FEISHU_WRITE_WORDS = ("初始化", "配置", "诊断", "记录", "发布", "同步", "写入", "自动同步", "setup")
AUTH_OR_SECRET_WORDS = ("登录", "授权", "auth", "login", "apikey", "api key", "token", "密钥", "权限")
READONLY_RESEARCH_WORDS = ("调研", "检查", "查看", "评估", "搜索")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create and evaluate a Nexus skill replay plan for prompts sent as real Codex thread messages."
    )
    parser.add_argument("--cases-file", type=Path, default=DEFAULT_CASES_FILE)
    parser.add_argument("--problem-matrix", type=Path, default=DEFAULT_PROBLEM_MATRIX_FILE)
    parser.add_argument("--case-id", action="append", default=[], help="case id to include; repeat to select multiple")
    parser.add_argument("--e2e-root", type=Path, default=Path("/tmp/nexus-skill-replay-lab"))
    parser.add_argument("--nexus-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--include-wljob-command-file", action="store_true", help="append the full 0-34 wljob $nexus-workflow command-file replay plan")
    parser.add_argument("--wljob-command-file", type=Path, default=DEFAULT_WLJOB_COMMAND_FILE)
    parser.add_argument("--dry-run-plan", action="store_true", help="print a replay plan without writing files")
    parser.add_argument("--init-run", action="store_true", help="write replay_plan.json, send_queue.jsonl, and replay_state.json")
    parser.add_argument("--allow-external-side-effects", action="store_true", help="include self-sync/public/Feishu-style prompts in the send queue")
    parser.add_argument("--replay-dir", type=Path, help="existing replay directory for record/evaluate")
    parser.add_argument("--record-turn", action="store_true", help="record one Codex thread response into the replay run")
    parser.add_argument("--thread-id", help="Codex thread id used for the replay turn")
    parser.add_argument("--submission-id", default="", help="multi_agent_v1 send_input submission id for monitor-thread replay")
    parser.add_argument("--completion-id", default="", help="multi_agent_v1 completion id for monitor-thread replay")
    parser.add_argument("--status-id", default="", help="multi_agent_v1 status/wait id for monitor-thread replay")
    parser.add_argument("--final-status", default="", help="final multi_agent_v1 status observed by the monitor")
    parser.add_argument("--user-message-id", default="", help="legacy alias for --submission-id; not required by the real contract")
    parser.add_argument("--assistant-message-id", default="", help="legacy alias for --completion-id; not required by the real contract")
    parser.add_argument(
        "--record-source",
        default="",
        choices=["", "in_app_codex_thread", "multi_agent_v1_thread", "codex_exec_subprocess"],
        help="source used to capture this turn",
    )
    parser.add_argument("--case-id-record", help="case id for --record-turn")
    parser.add_argument("--step-id", help="step id for --record-turn")
    parser.add_argument("--prompt-file", type=Path, help="file containing the exact prompt sent to Codex for --record-turn")
    parser.add_argument("--response-file", type=Path, help="file containing the Codex assistant response for --record-turn")
    parser.add_argument("--verix-audit-dir", type=Path, help="optional Verix audit artifact directory to include in traceability evaluation")
    parser.add_argument("--evaluate-run", action="store_true", help="evaluate all recorded turns in --replay-dir")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    modes = [args.dry_run_plan, args.init_run, args.record_turn, args.evaluate_run]
    if sum(1 for mode in modes if mode) != 1:
        raise SystemExit("choose exactly one mode: --dry-run-plan, --init-run, --record-turn, or --evaluate-run")

    if args.record_turn:
        return record_turn(args)
    if args.evaluate_run:
        return evaluate_run(args)

    cases_payload = load_json(args.cases_file.expanduser())
    problem_matrix = load_json(args.problem_matrix.expanduser())
    context = build_context(args)
    cases = select_cases(cases_payload, args.case_id)
    plan = build_replay_plan(
        cases,
        context,
        problem_matrix,
        cases_file=args.cases_file.expanduser().resolve(),
        problem_matrix_file=args.problem_matrix.expanduser().resolve(),
        allow_external_side_effects=args.allow_external_side_effects,
        wljob_command_file=args.wljob_command_file.expanduser().resolve() if args.include_wljob_command_file else None,
    )
    if args.dry_run_plan:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return init_run(args, plan)


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return payload


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def select_cases(payload: dict[str, Any], case_ids: list[str]) -> list[dict[str, Any]]:
    cases = [case for case in payload.get("cases", []) if isinstance(case, dict)]
    if not case_ids:
        return cases
    by_id = {str(case.get("id", "")): case for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise SystemExit(f"unknown case id(s): {', '.join(missing)}")
    return [by_id[case_id] for case_id in case_ids]


def build_context(args: argparse.Namespace) -> dict[str, str]:
    e2e_root = args.e2e_root.expanduser().resolve()
    nexus_root = args.nexus_root.expanduser().resolve()
    return {
        "E2E_ROOT": str(e2e_root),
        "NEXUS_ROOT": str(nexus_root),
        "PROJECT_PATH": str(e2e_root / "projects" / "project-under-test"),
        "FIXTURE_DIR": str(e2e_root / "fixtures"),
    }


def build_replay_plan(
    cases: list[dict[str, Any]],
    context: dict[str, str],
    problem_matrix: dict[str, Any],
    *,
    cases_file: Path,
    problem_matrix_file: Path,
    allow_external_side_effects: bool,
    wljob_command_file: Path | None = None,
) -> dict[str, Any]:
    suffix = str(problem_matrix.get("common_prompt_suffix", "")).strip()
    problem_ids = [str(problem.get("id", "")) for problem in problem_matrix.get("problems", []) if isinstance(problem, dict)]
    replay_steps: list[dict[str, Any]] = []
    for case in cases:
        scoped_context = dict(context)
        project_path = str(case.get("project_path", ""))
        if project_path:
            scoped_context["PROJECT_PATH"] = substitute(project_path, scoped_context)
        for step in case.get("steps", []):
            if not isinstance(step, dict):
                continue
            prompt = substitute(str(step.get("prompt", "")), scoped_context)
            if should_add_problem_suffix(step, suffix):
                prompt = append_problem_suffix(prompt, suffix)
            nexus_args = [substitute(str(item), scoped_context) for item in step.get("nexus_args", [])]
            command = nexus_args[0] if nexus_args else ""
            external_reasons = external_replay_reasons(case, step, command, prompt=prompt)
            queued = not external_reasons or allow_external_side_effects
            replay_steps.append(
                {
                    "case_id": str(case.get("id", "")),
                    "case_title": str(case.get("title", "")),
                    "step_id": str(step.get("id", "")),
                    "prompt": prompt,
                    "expected_cli_command": expected_cli_command(nexus_args),
                    "expected_cli_args": nexus_args,
                    "safe_replay_command": command in SAFE_REPLAY_COMMANDS,
                    "requires_external_side_effect": bool(external_reasons),
                    "external_side_effect_reasons": external_reasons,
                    "queued": queued,
                    "queue_execution_model": "queue-only",
                    "target_replay_execution_model": "multi_agent_v1_thread",
                    "recorded_execution_model": "",
                    "replay_execution_model": "queue-only",
                    "skip_reason": "" if queued else "external_side_effect_refused_by_default",
                    "problem_axes": problem_ids,
                }
            )
    wljob_steps = build_wljob_command_file_steps(
        wljob_command_file,
        problem_ids=problem_ids,
        allow_external_side_effects=allow_external_side_effects,
    )
    replay_steps.extend(wljob_steps)
    return {
        "schema": "nexus.lab.skill_replay_plan.v1",
        "generated_at": utc_now(),
        "nexus_root": context["NEXUS_ROOT"],
        "e2e_root": context["E2E_ROOT"],
        "cases_file": str(cases_file),
        "problem_matrix_file": str(problem_matrix_file),
        "problem_matrix_source": problem_matrix.get("source", ""),
        "coverage_policy": problem_matrix.get("coverage_policy", {}),
        "allow_external_side_effects": allow_external_side_effects,
        "replay_surface": "codex_thread_message",
        "replay_execution_model": "multi_agent_v1_thread",
        "replay_execution_models": ["queue-only", "codex_exec_subprocess", "multi_agent_v1_thread"],
        "queue_execution_model": "queue-only",
        "real_thread_replay_execution_model": "multi_agent_v1_thread",
        "requires_real_thread": True,
        "subprocess_replay_model": "codex_exec_subprocess",
        "wljob_command_file": str(wljob_command_file) if wljob_command_file else "",
        "wljob_command_file_step_count": len(wljob_steps),
        "operator_contract": (
            "Send each queued prompt through the monitor-controlled multi_agent_v1 thread path, then record the assistant "
            "response with --record-turn and submission/completion/status ids. Queue-only and codex_exec_subprocess "
            "records are evidence, but they do not satisfy the real thread replay contract."
        ),
        "source_consumption": build_source_consumption(
            cases_file=cases_file,
            problem_matrix_file=problem_matrix_file,
            problem_matrix=problem_matrix,
            wljob_command_file=wljob_command_file,
            wljob_step_count=len(wljob_steps),
        ),
        "steps": replay_steps,
    }


def build_wljob_command_file_steps(
    path: Path | None,
    *,
    problem_ids: list[str],
    allow_external_side_effects: bool,
) -> list[dict[str, Any]]:
    if not path:
        return []
    if not path.exists():
        return [
            {
                "case_id": "wljob_full_skill_chain",
                "case_title": "Full wljob 0-34 $nexus-workflow command-file replay",
                "step_id": "command_file_missing",
                "prompt": f"$nexus-workflow blocked: wljob command file missing at {path}",
                "expected_cli_command": "",
                "safe_replay_command": False,
                "requires_external_side_effect": False,
                "external_side_effect_reasons": [],
                "queued": True,
                "queue_execution_model": "queue-only",
                "target_replay_execution_model": "multi_agent_v1_thread",
                "recorded_execution_model": "",
                "replay_execution_model": "queue-only",
                "skip_reason": "",
                "problem_axes": problem_ids,
            }
        ]
    text = path.read_text(encoding="utf-8")
    steps: list[dict[str, Any]] = []
    current_id = ""
    current_title = ""
    in_input = False
    in_code = False
    prompt_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        heading = re.match(r"^##\s+([0-9]+[A-Z]?)\.\s*(.+)$", line)
        if heading:
            if prompt_lines:
                steps.append(make_wljob_step(current_id, current_title, "\n".join(prompt_lines).strip(), problem_ids, allow_external_side_effects))
                prompt_lines = []
            current_id = heading.group(1)
            current_title = heading.group(2).strip()
            in_input = False
            in_code = False
            continue
        if line.startswith("### 输入指令"):
            in_input = True
            continue
        if not in_input:
            continue
        if line.strip().startswith("```"):
            in_code = not in_code
            if not in_code and prompt_lines:
                steps.append(make_wljob_step(current_id, current_title, "\n".join(prompt_lines).strip(), problem_ids, allow_external_side_effects))
                prompt_lines = []
                in_input = False
            continue
        if in_code and line.strip():
            prompt_lines.append(line)
    if prompt_lines:
        steps.append(make_wljob_step(current_id, current_title, "\n".join(prompt_lines).strip(), problem_ids, allow_external_side_effects))
    return [step for step in steps if step["prompt"].startswith("$nexus-workflow")]


def make_wljob_step(
    step_no: str,
    title: str,
    prompt: str,
    problem_ids: list[str],
    allow_external_side_effects: bool,
) -> dict[str, Any]:
    step_id = "wljob_" + re.sub(r"[^0-9A-Za-z]+", "_", step_no).strip("_").lower()
    reasons = prompt_side_effect_reasons(prompt + "\n" + title)
    queued = not reasons or allow_external_side_effects
    return {
        "case_id": "wljob_full_skill_chain",
        "case_title": "Full wljob 0-34 $nexus-workflow command-file replay",
        "step_id": step_id,
        "source_step": step_no,
        "source_title": title,
        "prompt": prompt,
        "expected_cli_command": "skill_surface_from_command_file",
        "expected_cli_args": [],
        "safe_replay_command": not reasons,
        "requires_external_side_effect": bool(reasons),
        "external_side_effect_reasons": reasons,
        "queued": queued,
        "queue_execution_model": "queue-only",
        "target_replay_execution_model": "multi_agent_v1_thread",
        "recorded_execution_model": "",
        "replay_execution_model": "queue-only",
        "dispatch_status": "ready_to_send" if queued else "gated",
        "skip_reason": "" if queued else "external_side_effect_refused_by_default",
        "problem_axes": problem_ids,
    }


def init_run(args: argparse.Namespace, plan: dict[str, Any]) -> int:
    replay_dir = args.e2e_root.expanduser().resolve() / "skill-replays" / timestamp_slug()
    replay_dir.mkdir(parents=True, exist_ok=True)
    plan_path = replay_dir / "replay_plan.json"
    queue_path = replay_dir / "send_queue.jsonl"
    state_path = replay_dir / "replay_state.json"
    prompt_turns_path = replay_dir / "prompt_turns.jsonl"
    source_consumption_path = replay_dir / "source_consumption.json"
    save_json(plan_path, plan)
    save_json(source_consumption_path, plan.get("source_consumption", {}))
    with queue_path.open("w", encoding="utf-8") as handle:
        for step in plan["steps"]:
            queued = bool(step.get("queued"))
            queue_record = dict(step)
            queue_record["dispatch_status"] = "ready_to_send" if queued else "gated"
            queue_record["replay_execution_model"] = "queue-only"
            handle.write(json.dumps(queue_record, ensure_ascii=False, sort_keys=True) + "\n")
    write_queue_only_prompt_turns(prompt_turns_path, plan)
    state = {
        "schema": "nexus.lab.skill_replay_state.v1",
        "created_at": utc_now(),
        "status": "waiting_for_multi_agent_v1_thread_replay",
        "replay_execution_model": "multi_agent_v1_thread",
        "replay_execution_models": ["queue-only", "codex_exec_subprocess", "multi_agent_v1_thread"],
        "queue_execution_model": "queue-only",
        "requires_real_thread": True,
        "satisfies_real_thread_replay_contract": False,
        "replay_dir": str(replay_dir),
        "replay_plan": str(plan_path),
        "send_queue": str(queue_path),
        "prompt_turns": str(prompt_turns_path),
        "source_consumption_path": str(source_consumption_path),
        "source_consumption": plan.get("source_consumption", {}),
        "recorded_turns": [],
        "evaluation_path": "",
        "note": (
            "Use monitor-controlled multi_agent_v1 send/read tools to execute queued prompts, then record submission_id "
            "and completion/status evidence. Queue-only and codex_exec_subprocess are not real thread replay."
        ),
    }
    save_json(state_path, state)
    print(str(state_path))
    return 0


def record_turn(args: argparse.Namespace) -> int:
    if not args.replay_dir:
        raise SystemExit("--record-turn requires --replay-dir")
    if not args.thread_id or not args.case_id_record or not args.step_id or not args.prompt_file or not args.response_file:
        raise SystemExit("--record-turn requires --thread-id, --case-id-record, --step-id, --prompt-file, and --response-file")
    replay_dir = args.replay_dir.expanduser().resolve()
    state_path = replay_dir / "replay_state.json"
    state = load_json(state_path)
    prompt = args.prompt_file.expanduser().read_text(encoding="utf-8", errors="replace")
    response = args.response_file.expanduser().read_text(encoding="utf-8", errors="replace")
    verdict = evaluate_response(prompt, response)
    record_source = args.record_source or infer_record_source(args.thread_id)
    ids = normalize_record_ids(args)
    replay_execution_model = execution_model_for_record_source(record_source)
    real_thread_contract = satisfies_real_thread_contract(
        record_source=record_source,
        thread_id=args.thread_id,
        submission_id=ids["submission_id"],
        completion_id=ids["completion_id"],
        status_id=ids["status_id"],
        final_status=ids["final_status"],
    )
    turn_dir = replay_dir / "turns" / args.case_id_record / args.step_id
    turn_path = turn_dir / "turn.json"
    record = {
        "schema": "nexus.lab.skill_replay_turn.v1",
        "recorded_at": utc_now(),
        "thread_id": args.thread_id,
        "actual_thread_id": args.thread_id if record_source in {"in_app_codex_thread", "multi_agent_v1_thread"} else "",
        "submission_id": ids["submission_id"],
        "completion_id": ids["completion_id"],
        "status_id": ids["status_id"],
        "final_status": ids["final_status"],
        "user_message_id": args.user_message_id,
        "assistant_message_id": args.assistant_message_id,
        "legacy_message_id_fields_are_contract_fields": False,
        "sent_by_monitor_thread_id": args.thread_id if record_source in {"in_app_codex_thread", "multi_agent_v1_thread"} else "",
        "record_source": record_source,
        "replay_execution_model": replay_execution_model,
        "requires_real_thread": True,
        "satisfies_real_thread_replay_contract": real_thread_contract,
        "contract_evidence": {
            "requires_submission_id": record_source == "multi_agent_v1_thread",
            "requires_completion_or_status_id": record_source == "multi_agent_v1_thread",
            "has_thread_id": bool(args.thread_id.strip()),
            "has_submission_id": bool(ids["submission_id"]),
            "has_completion_id": bool(ids["completion_id"]),
            "has_status_id": bool(ids["status_id"]),
            "has_final_status": bool(ids["final_status"]),
            "queue_only_is_not_real_thread": replay_execution_model == "queue-only",
            "codex_exec_subprocess_is_not_real_thread": replay_execution_model == "codex_exec_subprocess",
        },
        "source_consumption": state.get("source_consumption", {}),
        "case_id": args.case_id_record,
        "step_id": args.step_id,
        "prompt_sha256": text_sha256(prompt),
        "response_sha256": text_sha256(response),
        "input_output_sha256": stable_hash({"prompt": prompt, "response": response}),
        "prompt": prompt,
        "response": response,
        "verdict": verdict,
    }
    save_json(turn_path, record)
    recorded = [item for item in state.get("recorded_turns", []) if isinstance(item, dict)]
    recorded.append(
        {
            "case_id": args.case_id_record,
            "step_id": args.step_id,
            "thread_id": args.thread_id,
            "record_source": record_source,
            "replay_execution_model": replay_execution_model,
            "submission_id": ids["submission_id"],
            "completion_id": ids["completion_id"],
            "status_id": ids["status_id"],
            "final_status": ids["final_status"],
            "satisfies_real_thread_replay_contract": real_thread_contract,
            "turn_path": str(turn_path),
            "prompt_sha256": record["prompt_sha256"],
            "response_sha256": record["response_sha256"],
            "input_output_sha256": record["input_output_sha256"],
            "status": verdict["status"],
        }
    )
    state["recorded_turns"] = recorded
    state["status"] = "turns_recorded"
    state["satisfies_real_thread_replay_contract"] = bool(recorded) and all(
        bool(item.get("satisfies_real_thread_replay_contract")) for item in recorded
    )
    save_json(state_path, state)
    append_prompt_turn(replay_dir, record)
    print(str(turn_path))
    return 0 if verdict["status"] == "pass" else 1


def evaluate_run(args: argparse.Namespace) -> int:
    if not args.replay_dir:
        raise SystemExit("--evaluate-run requires --replay-dir")
    replay_dir = args.replay_dir.expanduser().resolve()
    state_path = replay_dir / "replay_state.json"
    state = load_json(state_path)
    turn_paths = [Path(item["turn_path"]) for item in state.get("recorded_turns", []) if isinstance(item, dict) and item.get("turn_path")]
    turns = [load_json(path) for path in turn_paths if path.exists()]
    prompt_turn_records = load_prompt_turns(replay_dir / "prompt_turns.jsonl")
    for path, turn in zip(turn_paths, turns):
        verdict = evaluate_response(str(turn.get("prompt", "")), str(turn.get("response", "")))
        turn["verdict"] = verdict
        turn["satisfies_skill_output_contract"] = verdict["status"] == "pass"
        save_json(path, turn)
    execution_records = prompt_turn_records if prompt_turn_records else turns
    real_thread_contract = bool(turns) and all(bool(turn.get("satisfies_real_thread_replay_contract")) for turn in turns)
    failures = [
        {
            "case_id": turn.get("case_id", ""),
            "step_id": turn.get("step_id", ""),
            "status": turn.get("verdict", {}).get("status", "missing"),
            "missing": turn.get("verdict", {}).get("missing", []),
            "turn_path": str(path),
        }
        for path, turn in zip(turn_paths, turns)
        if turn.get("verdict", {}).get("status") != "pass"
    ]
    result = {
        "schema": "nexus.lab.skill_replay_evaluation.v1",
        "evaluated_at": utc_now(),
        "status": "pass" if turns and not failures else "fail",
        "satisfies_skill_output_contract": bool(turns) and not failures,
        "satisfies_real_thread_replay_contract": real_thread_contract,
        "replay_execution_models": sorted(
            {
                str(item.get("replay_execution_model", ""))
                for item in execution_records
                if item.get("replay_execution_model")
            }
        ),
        "execution_model_counts": execution_model_counts(execution_records),
        "record_sources": sorted({str(turn.get("record_source", "")) for turn in turns if turn.get("record_source")}),
        "turn_count": len(turns),
        "queue_only_prompt_count": sum(1 for item in prompt_turn_records if item.get("replay_execution_model") == "queue-only"),
        "codex_exec_subprocess_turn_count": sum(1 for turn in turns if turn.get("replay_execution_model") == "codex_exec_subprocess"),
        "multi_agent_v1_thread_turn_count": sum(1 for turn in turns if turn.get("replay_execution_model") == "multi_agent_v1_thread"),
        "prompt_turns_path": str(replay_dir / "prompt_turns.jsonl"),
        "source_consumption": state.get("source_consumption", {}),
        "failures": failures,
        "required_nexus_change": build_required_change(failures),
    }
    traceability = build_traceability_report(
        replay_dir=replay_dir,
        turns=turns,
        prompt_turn_records=prompt_turn_records,
        verix_audit_dir=args.verix_audit_dir.expanduser().resolve() if args.verix_audit_dir else None,
    )
    traceability_path = replay_dir / "traceability_report.json"
    save_json(traceability_path, traceability)
    result["traceability_report_path"] = str(traceability_path)
    result["satisfies_traceability_contract"] = traceability["status"] == "pass"
    output = replay_dir / "skill_replay_evaluation.json"
    save_json(output, result)
    state["evaluation_path"] = str(output)
    state["status"] = result["status"]
    state["satisfies_real_thread_replay_contract"] = real_thread_contract
    save_json(state_path, state)
    print(str(output))
    return 0 if result["status"] == "pass" else 1


def build_source_consumption(
    *,
    cases_file: Path,
    problem_matrix_file: Path,
    problem_matrix: dict[str, Any],
    wljob_command_file: Path | None,
    wljob_step_count: int,
) -> dict[str, Any]:
    sources = [
        {
            "path": str(cases_file),
            "status": "read",
            "used_for": "case prompts and expected Nexus CLI routing",
        },
        {
            "path": str(problem_matrix_file),
            "status": "read",
            "used_for": "16-axis replay coverage and common prompt suffix",
            "problem_axis_count": len([item for item in problem_matrix.get("problems", []) if isinstance(item, dict)]),
            "problem_matrix_source": problem_matrix.get("source", ""),
        },
    ]
    if wljob_command_file:
        sources.append(
            {
                "path": str(wljob_command_file),
                "status": "read" if wljob_command_file.exists() else "missing",
                "used_for": "wljob 0-34 command-file replay coverage",
                "step_count": wljob_step_count,
            }
        )
    return {
        "schema": "nexus.lab.skill_replay_source_consumption.v1",
        "generated_at": utc_now(),
        "sources": sources,
    }


def write_queue_only_prompt_turns(path: Path, plan: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for index, step in enumerate(plan.get("steps", []), start=1):
            if not isinstance(step, dict):
                continue
            queued = bool(step.get("queued"))
            record = {
                "schema": "nexus.lab.skill_replay_prompt_turn.v1",
                "recorded_at": utc_now(),
                "case_id": step.get("case_id", ""),
                "step_id": step.get("step_id", ""),
                "source_step": step.get("source_step", ""),
                "prompt_index": index,
                "prompt": step.get("prompt", ""),
                "record_source": "queue-only",
                "replay_execution_model": "queue-only",
                "dispatch_status": "ready_to_send" if queued else "gated",
                "queued": queued,
                "requires_external_side_effect": bool(step.get("requires_external_side_effect")),
                "external_side_effect_reasons": step.get("external_side_effect_reasons", []),
                "satisfies_real_thread_replay_contract": False,
                "target_replay_execution_model": "multi_agent_v1_thread",
            }
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def normalize_record_ids(args: argparse.Namespace) -> dict[str, str]:
    submission_id = (args.submission_id or args.user_message_id or "").strip()
    completion_id = (args.completion_id or args.assistant_message_id or "").strip()
    return {
        "submission_id": submission_id,
        "completion_id": completion_id,
        "status_id": (args.status_id or "").strip(),
        "final_status": (args.final_status or "").strip(),
    }


def execution_model_for_record_source(record_source: str) -> str:
    if record_source == "multi_agent_v1_thread":
        return "multi_agent_v1_thread"
    if record_source == "codex_exec_subprocess":
        return "codex_exec_subprocess"
    if record_source == "in_app_codex_thread":
        return "in_app_codex_thread"
    if record_source == "queue-only":
        return "queue-only"
    return record_source or "unknown"


def satisfies_real_thread_contract(
    *,
    record_source: str,
    thread_id: str,
    submission_id: str,
    completion_id: str,
    status_id: str,
    final_status: str,
) -> bool:
    if record_source != "multi_agent_v1_thread":
        return False
    return bool(thread_id.strip()) and bool(submission_id) and bool(completion_id or status_id) and bool(final_status)


def append_prompt_turn(replay_dir: Path, record: dict[str, Any]) -> None:
    prompt_turns_path = replay_dir / "prompt_turns.jsonl"
    prompt_record = {
        "schema": "nexus.lab.skill_replay_prompt_turn.v1",
        "recorded_at": record.get("recorded_at", utc_now()),
        "case_id": record.get("case_id", ""),
        "step_id": record.get("step_id", ""),
        "prompt": record.get("prompt", ""),
        "record_source": record.get("record_source", ""),
        "replay_execution_model": record.get("replay_execution_model", ""),
        "dispatch_status": "recorded",
        "thread_id": record.get("thread_id", ""),
        "submission_id": record.get("submission_id", ""),
        "completion_id": record.get("completion_id", ""),
        "status_id": record.get("status_id", ""),
        "final_status": record.get("final_status", ""),
        "satisfies_real_thread_replay_contract": bool(record.get("satisfies_real_thread_replay_contract")),
        "turn_path": str(replay_dir / "turns" / str(record.get("case_id", "")) / str(record.get("step_id", "")) / "turn.json"),
        "prompt_sha256": record.get("prompt_sha256", ""),
        "response_sha256": record.get("response_sha256", ""),
        "input_output_sha256": record.get("input_output_sha256", ""),
        "verdict_status": record.get("verdict", {}).get("status", ""),
    }
    with prompt_turns_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(prompt_record, ensure_ascii=False, sort_keys=True) + "\n")


def load_prompt_turns(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def execution_model_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        model = str(record.get("replay_execution_model", "")).strip()
        if not model:
            continue
        counts[model] = counts.get(model, 0) + 1
    return dict(sorted(counts.items()))


def build_traceability_report(
    *,
    replay_dir: Path,
    turns: list[dict[str, Any]],
    prompt_turn_records: list[dict[str, Any]],
    verix_audit_dir: Path | None,
) -> dict[str, Any]:
    normalized_turns = []
    for turn in turns:
        prompt = str(turn.get("prompt") or "")
        response = str(turn.get("response") or "")
        prompt_hash = str(turn.get("prompt_sha256") or text_sha256(prompt))
        response_hash = str(turn.get("response_sha256") or text_sha256(response))
        normalized_turns.append(
            {
                "case_id": turn.get("case_id", ""),
                "step_id": turn.get("step_id", ""),
                "record_source": turn.get("record_source", ""),
                "replay_execution_model": turn.get("replay_execution_model", ""),
                "thread_id": turn.get("thread_id", ""),
                "submission_id": turn.get("submission_id", ""),
                "completion_id": turn.get("completion_id", ""),
                "status_id": turn.get("status_id", ""),
                "final_status": turn.get("final_status", ""),
                "satisfies_real_thread_replay_contract": bool(turn.get("satisfies_real_thread_replay_contract")),
                "prompt_sha256": prompt_hash,
                "response_sha256": response_hash,
                "input_output_sha256": str(turn.get("input_output_sha256") or stable_hash({"prompt": prompt, "response": response})),
                "turn_path": str(replay_dir / "turns" / str(turn.get("case_id", "")) / str(turn.get("step_id", "")) / "turn.json"),
            }
        )
    verix_trace = collect_verix_traceability(verix_audit_dir) if verix_audit_dir else {
        "audit_dir": "",
        "checkpoint_count": 0,
        "model_request_count": 0,
        "nodes": [],
        "status": "not_provided",
    }
    status = "pass" if normalized_turns and all(item["prompt_sha256"] and item["response_sha256"] for item in normalized_turns) else "fail"
    if verix_audit_dir and not verix_trace["nodes"]:
        status = "fail"
    return {
        "schema": "nexus.verix.traceability_report.v1",
        "generated_at": utc_now(),
        "status": status,
        "replay_dir": str(replay_dir),
        "prompt_turns_path": str(replay_dir / "prompt_turns.jsonl"),
        "queue_only_prompt_count": sum(1 for item in prompt_turn_records if item.get("replay_execution_model") == "queue-only"),
        "skill_replay_turn_count": len(normalized_turns),
        "multi_agent_v1_thread_turn_count": sum(1 for item in normalized_turns if item["replay_execution_model"] == "multi_agent_v1_thread"),
        "skill_replay_turns": normalized_turns,
        "verix_audit": verix_trace,
        "contract": {
            "skill_replay_records_exact_prompt_response_hashes": bool(normalized_turns),
            "verix_audit_records_checkpoint_input_hashes": bool(verix_trace["nodes"]) if verix_audit_dir else False,
            "queue_only_is_not_real_thread_replay": True,
        },
    }


def collect_verix_traceability(audit_dir: Path) -> dict[str, Any]:
    nodes = []
    checkpoints_dir = audit_dir / "checkpoints"
    if checkpoints_dir.exists():
        for checkpoint_path in sorted(checkpoints_dir.glob("*.json")):
            checkpoint = load_json(checkpoint_path)
            output_refs = [Path(str(ref)) for ref in checkpoint.get("output_refs", []) if str(ref)]
            nodes.append(
                {
                    "node_id": checkpoint.get("node_id", checkpoint_path.stem),
                    "kind": checkpoint.get("kind", ""),
                    "status": checkpoint.get("status", ""),
                    "input_hash": checkpoint.get("input_hash", ""),
                    "checkpoint_path": str(checkpoint_path),
                    "checkpoint_sha256": file_sha256(checkpoint_path),
                    "output_refs": [
                        {
                            "path": str(ref),
                            "exists": ref.exists(),
                            "sha256": file_sha256(ref) if ref.exists() and ref.is_file() else "",
                        }
                        for ref in output_refs
                    ],
                }
            )
    request_dir = audit_dir / "model_requests"
    model_requests = []
    if request_dir.exists():
        for request_path in sorted(request_dir.glob("*.json")):
            request = load_json(request_path)
            model_requests.append(
                {
                    "node_id": request.get("node_id", request_path.stem),
                    "path": str(request_path),
                    "sha256": file_sha256(request_path),
                    "prompt_sha256": text_sha256(str(request.get("prompt") or "")),
                }
            )
    return {
        "audit_dir": str(audit_dir),
        "status": "present" if nodes or model_requests else "missing_artifacts",
        "checkpoint_count": len(nodes),
        "model_request_count": len(model_requests),
        "nodes": nodes,
        "model_requests": model_requests,
    }


def stable_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate_response(prompt: str, response: str) -> dict[str, Any]:
    response_lower = response.lower()
    missing: list[str] = []
    has_status = response_has_status(response)
    has_output = response_has_output(response)
    mock_used = response_uses_mock(response_lower)
    workflow_next_required = requires_workflow_next_prompt(prompt, response)
    has_next_action = response_has_next_action(response)
    has_skill_next_prompt = "$nexus-workflow" in response
    has_real_invocation_evidence = response_has_real_invocation_evidence(
        prompt=prompt,
        response=response,
        response_lower=response_lower,
        has_status=has_status,
        has_output=has_output,
        mock_used=mock_used,
    )
    if "$nexus-workflow" not in prompt:
        missing.append("prompt_is_not_skill_style")
    if not has_status:
        missing.append("missing_status_section_or_field")
    if not has_output:
        missing.append("missing_output_section_or_field")
    if workflow_next_required:
        if not has_skill_next_prompt:
            missing.append("missing_next_skill_prompt")
    elif not has_next_action:
        missing.append("missing_next_skill_prompt")
    if not has_real_invocation_evidence:
        missing.append("missing_evidence_of_real_nexus_invocation")
    if mock_used:
        missing.append("mock_provider_used")
    if is_skill_check_prompt(prompt) and routes_to_unrelated_github_public(response):
        missing.append("unexpected_github_public_route_for_skill_check")
    return {
        "status": "pass" if not missing else "fail",
        "missing": missing,
        "checks": {
            "skill_prompt_sent": "$nexus-workflow" in prompt,
            "has_status": "missing_status_section_or_field" not in missing,
            "has_output": "missing_output_section_or_field" not in missing,
            "has_next_prompt": "missing_next_skill_prompt" not in missing,
            "requires_workflow_next_prompt": workflow_next_required,
            "has_artifact_next_action": response_has_artifact_reference(response),
            "has_real_nexus_invocation_evidence": "missing_evidence_of_real_nexus_invocation" not in missing,
            "non_run_skill_response_without_mock": is_non_run_skill_response(prompt) and has_status and has_output and not mock_used,
            "mock_provider_absent": "mock_provider_used" not in missing,
            "skill_check_route_consistent": "unexpected_github_public_route_for_skill_check" not in missing,
        },
    }


def response_has_status(response: str) -> bool:
    return any(marker in response for marker in ["状态", "status", "Status"])


def response_has_output(response: str) -> bool:
    return any(marker in response for marker in ["输出", "output", "Output"])


def response_uses_mock(response_lower: str) -> bool:
    return any(term in response_lower for term in ["mock provider", "--provider mock", "provider mock"])


def response_has_next_action(response: str) -> bool:
    response_lower = response.lower()
    return "$nexus-workflow" in response or any(marker in response_lower or marker in response for marker in NEXT_STEP_MARKERS) or response_has_artifact_reference(response)


def response_has_artifact_reference(response: str) -> bool:
    return bool(ARTIFACT_PATH_RE.search(response))


def response_has_real_invocation_evidence(
    *,
    prompt: str,
    response: str,
    response_lower: str,
    has_status: bool,
    has_output: bool,
    mock_used: bool,
) -> bool:
    if any(marker in response_lower for marker in REAL_INVOCATION_MARKERS):
        return True
    if "run_id" in response_lower or NEXUS_RUN_ID_RE.search(response):
        return True
    if response_has_artifact_reference(response) and has_status and has_output and not mock_used:
        return True
    if is_non_run_skill_response(prompt) and has_status and has_output and not mock_used:
        return True
    return False


def requires_workflow_next_prompt(prompt: str, response: str) -> bool:
    combined = (prompt + "\n" + response).lower()
    if any(term in combined for term in WORKFLOW_NEXT_REQUIRED_TERMS):
        return True
    return any(term in prompt for term in ["审批", "恢复", "继续", "代码修改"]) and not response_has_artifact_reference(response)


def is_non_run_skill_response(prompt: str) -> bool:
    directive = skill_prompt_directive(prompt)
    directive_lower = directive.lower()
    if not directive or NEXUS_RUN_ID_RE.search(directive) or "run_id" in directive_lower:
        return False
    diagnostic_terms = (
        "skill doctor",
        "model configure",
        "model intent",
        "model set",
        "model status",
        "provider 状态",
        "workflow 是否安装",
        "查看当前可用的基座模型",
        "检查当前 nexus workflow 是否安装并可用",
        "配置模型",
        "诊断飞书配置",
        "查看记录板",
        "查看当前状态",
    )
    return any(term in directive_lower or term in directive for term in diagnostic_terms)


def skill_prompt_directive(prompt: str) -> str:
    head = prompt.split("\n\n全局 Nexus 问题验收：", 1)[0].strip()
    for line in head.splitlines():
        stripped = line.strip()
        if stripped.startswith("$nexus-workflow"):
            return stripped.removeprefix("$nexus-workflow").strip()
    return ""


def build_required_change(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for failure in failures:
        changes.append(
            {
                "case_id": failure.get("case_id", ""),
                "step_id": failure.get("step_id", ""),
                "surface": "nexus-workflow skill output contract",
                "recommendation": (
                    "The Codex skill response must expose status and output, avoid mock provider use, require a "
                    "$nexus-workflow next prompt only when the workflow must continue, and allow non-run diagnostic "
                    "or artifact-only responses to satisfy the evidence contract without a run_id."
                ),
                "reason": ", ".join(str(item) for item in failure.get("missing", [])),
            }
        )
    return changes


def infer_record_source(thread_id: str) -> str:
    if thread_id.startswith("codex-exec-"):
        return "codex_exec_subprocess"
    if thread_id.startswith("019"):
        return "multi_agent_v1_thread"
    return "in_app_codex_thread"


def is_skill_check_prompt(prompt: str) -> bool:
    return "检查当前 nexus workflow 是否安装并可用" in prompt or "skill 是否可用" in prompt


def expected_cli_command(nexus_args: list[str]) -> str:
    if not nexus_args:
        return ""
    command = nexus_args[0]
    if command in {"skill", "model", "board"} and len(nexus_args) > 1:
        return " ".join(nexus_args[:2])
    if command == "report" and len(nexus_args) > 1:
        return "report <run_id>"
    if command == "approve-and-continue" and len(nexus_args) > 2:
        return f"{command} <run_id> {nexus_args[2]}"
    return command


def routes_to_unrelated_github_public(response: str) -> bool:
    return "github_sync_public" in response or "github-sync public" in response or ".github/nexus-sync.json" in response


def should_add_problem_suffix(step: dict[str, Any], suffix: str) -> bool:
    if not suffix or step.get("apply_global_problem_suffix") is False:
        return False
    args = step.get("nexus_args", [])
    command = args[0] if isinstance(args, list) and args else ""
    return command in {"init-project", "supplement-init", "research", "invoke", "skill"}


def append_problem_suffix(prompt: str, suffix: str) -> str:
    if suffix in prompt:
        return prompt
    return prompt.rstrip() + "\n\n全局 Nexus 问题验收： " + suffix


def external_replay_reasons(case: dict[str, Any], step: dict[str, Any], command: str, *, prompt: str = "") -> list[str]:
    reasons: list[str] = []
    if case.get("requires_external_side_effect"):
        reasons.append("case_requires_external_side_effect")
    if step.get("requires_external_side_effect"):
        reasons.append("step_requires_external_side_effect")
    if command in EXTERNAL_COMMANDS:
        reasons.append(command)
    reasons.extend(prompt_side_effect_reasons(prompt))
    return sorted(set(reasons))


def prompt_side_effect_reasons(prompt: str) -> list[str]:
    text = prompt.split("\n\n全局 Nexus 问题验收：", 1)[0].strip()
    lower = text.lower()
    reasons: list[str] = []
    readonly = readonly_research_prompt(lower)
    github_disabled = "不同步 github" in lower or "--no-github" in lower or "跳过 github" in lower
    feishu_disabled = "不同步飞书" in text or "--no-feishu" in lower or "跳过飞书" in text

    if not readonly and not github_disabled and any(word in lower for word in GIT_REMOTE_WORDS) and any(word in lower for word in WRITE_OR_SYNC_WORDS):
        reasons.append("git_remote_sync_or_publish")
    if "public" in lower and any(word in lower for word in ("发布", "同步", "publish", "confirm", "确认")):
        reasons.append("public_publish_or_sync")
    if "private" in lower and "自动同步" in lower:
        reasons.append("private_auto_sync")
    if "self-sync" in lower or "自同步" in text:
        reasons.append("self_sync")

    has_feishu = any(word in lower for word in FEISHU_WORDS)
    if has_feishu and not feishu_disabled and any(word in lower for word in FEISHU_WRITE_WORDS):
        if not readonly:
            reasons.append("feishu_write_or_setup")
    if "在可用时同步" in text and has_feishu:
        reasons.append("feishu_write_or_setup")

    if any(word in lower for word in AUTH_OR_SECRET_WORDS):
        reasons.append("auth_or_secret_setup")
    if "skill" in lower and ("安装" in text or "install" in lower) and "是否安装" not in text:
        reasons.append("skill_install_global_state")
    if "配置模型" in text or "apikey" in lower or "api key" in lower:
        reasons.append("model_or_secret_config")
    if "纳入 git" in text or "建立 baseline" in text:
        reasons.append("local_git_state_write")
    if "初始化项目" in text and "/users/" in lower and "/private/tmp/" not in lower and "/tmp/" not in lower:
        reasons.append("host_project_creation")

    return sorted(set(reasons))


def readonly_research_prompt(lower_prompt: str) -> bool:
    return any(word in lower_prompt for word in READONLY_RESEARCH_WORDS) and not any(
        word in lower_prompt for word in ("写入", "发布", "push", "commit", "安装", "配置模型", "在可用时同步")
    )


def substitute(value: str, context: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return context.get(match.group(1), match.group(0))

    return PLACEHOLDER_RE.sub(replace, value)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
