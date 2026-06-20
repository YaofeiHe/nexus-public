from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from nexus.config import write_default_config
from nexus.interaction import render_cli_interaction
from nexus.model_profiles import (
    DASHSCOPE_BASE_URL,
    IntensityModelConfig,
    ModelProfile,
    import_api_key_file,
    load_intensity_config,
    load_profiles,
    load_session_model,
    profile_status,
    resolve_api_key,
    resolve_profile,
    save_intensity_config,
    save_profile,
    set_session_model,
    unsupported_profiles,
)
from nexus.providers.registry import doctor
from nexus.runner import Runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexus")
    parser.add_argument("--root", default=".", help="nexus project root, defaults to current directory")
    parser.add_argument("--next-prompt-mode", default="", choices=["", "workflow", "cli"], help="render next_task_prompt as workflow prompt by default, or raw CLI command when set to cli")
    parser.add_argument("--workflow-surface", default="", choices=["", "codex", "copilot"], help="render workflow prompts for Codex ($skill) or Copilot (/skill)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run.add_argument("idea")
    run.add_argument("--project-path", default=".")
    run.add_argument("--provider", default="auto")
    run.add_argument("--model", default="")
    run.add_argument("--max-candidates", type=int, default=8)

    research = sub.add_parser("research")
    research.add_argument("idea")
    research.add_argument("--project-root", "--project-path", dest="project_path", default=".")
    research.add_argument("--provider", default="auto")
    research.add_argument("--model", default="")
    research.add_argument("--approval-policy", default="ask", choices=["ask", "never"])
    research.add_argument("--max-candidates", type=int, default=8)
    research.add_argument("--approve-online-search", action="store_true", help="approve readonly public online search for this run")

    resume = sub.add_parser("resume")
    resume.add_argument("run_id", help="run id or latest")
    resume.add_argument("--approve", choices=["online-search"])
    resume.add_argument("--from-node", default="")
    resume.add_argument("--force-node", default="")

    status = sub.add_parser("status")
    status.add_argument("run_id", nargs="?", default="latest", help="run id or latest")

    approve = sub.add_parser("approve")
    approve.add_argument("run_id")
    approve.add_argument("stage", choices=["implementation-plan", "project-root", "online-search", "conversation-session-read", "code-change", "apply", "git-baseline", "skill-install", "recovery-playbook"])

    approve_and_continue = sub.add_parser("approve-and-continue")
    approve_and_continue.add_argument("run_id")
    approve_and_continue.add_argument("stage", choices=["implementation-plan", "project-root", "online-search", "conversation-session-read", "code-change", "apply", "git-baseline", "skill-install", "recovery-playbook"])

    plan_impl = sub.add_parser("plan-implementation")
    plan_impl.add_argument("run_id")

    execute = sub.add_parser("execute-code-change")
    execute.add_argument("run_id")
    execute.add_argument("--provider", default="codex-cli-gpt5.4high")

    diff = sub.add_parser("diff")
    diff.add_argument("run_id")

    apply = sub.add_parser("apply")
    apply.add_argument("run_id")

    test = sub.add_parser("test")
    test.add_argument("run_id")
    test.add_argument("--cmd", default="")

    continue_run = sub.add_parser("continue")
    continue_run.add_argument("run_id", help="run id or latest")
    continue_run.add_argument("request")
    continue_run.add_argument("--provider", default="auto")

    continue_after_input = sub.add_parser("continue-after-input")
    continue_after_input.add_argument("run_id", help="run id or latest")
    continue_after_input.add_argument("--note", default="")

    recover = sub.add_parser("recover")
    recover.add_argument("run_id", nargs="?", default="latest", help="run id or latest")
    recover.add_argument("request", nargs="?", default="", help="optional recovery instruction or completion note")

    handoff_debug = sub.add_parser("handoff-for-debug")
    handoff_debug.add_argument("run_id", nargs="?", default="latest", help="run id or latest")
    handoff_debug.add_argument("reason", nargs="?", default="", help="debug handoff reason")

    append_debug = sub.add_parser("append-debug-worklog")
    append_debug.add_argument("run_id", nargs="?", default="latest", help="run id or latest")
    append_debug.add_argument("--handoff-id", default="")
    append_debug.add_argument("--kind", default="diagnose")
    append_debug.add_argument("--summary", default="")
    append_debug.add_argument("--result", default="")
    append_debug.add_argument("--command", dest="debug_command", default="")
    append_debug.add_argument("--path", action="append", dest="paths")

    rebind = sub.add_parser("rebind-and-continue")
    rebind.add_argument("run_id", nargs="?", default="latest", help="run id or latest")
    rebind.add_argument("--handoff-id", default="")

    debug_status = sub.add_parser("debug-status")
    debug_status.add_argument("run_id", nargs="?", default="latest", help="run id or latest")

    init_project = sub.add_parser("init-project")
    init_project.add_argument("idea")
    init_project.add_argument("--parent", default=".")
    init_project.add_argument("--provider", default="auto")
    init_project.add_argument("--enable-feishu-autowrite", action="store_true")
    init_project.add_argument("--private-repo", default="")
    init_project.add_argument("--public-repo", default="")
    init_project.add_argument("--no-github-sync", action="store_true")
    init_project.add_argument("--no-feishu-sync", action="store_true")
    init_project.add_argument("--raw-user-request", default="", help="unmodified user request; used for original intent and explicit-name source checks")
    init_project.add_argument("--normalized-request", default="", help="optional normalized requirement text for generated intent docs")

    prepare_project = sub.add_parser("prepare-project")
    prepare_project.add_argument("project_path")

    rerank = sub.add_parser("rerank-candidates")
    rerank.add_argument("run_id", help="run id or latest")
    rerank.add_argument("--provider", default="auto")

    cm = sub.add_parser("conversation-manager")
    cm_sub = cm.add_subparsers(dest="cm_command", required=True)
    cm_init = cm_sub.add_parser("init")
    cm_init.add_argument("--project-path", default=".")
    cm_ingest = cm_sub.add_parser("ingest")
    cm_ingest.add_argument("--project-path", default=".")
    cm_ingest.add_argument("--file", required=True)
    cm_ingest.add_argument("--source-agent", default="codex")
    cm_promote = cm_sub.add_parser("promote")
    cm_promote.add_argument("--project-path", default=".")
    cm_promote.add_argument("--session-file", required=True)
    cm_promote.add_argument("--to", default="auto", choices=["auto", "skill", "workflow", "poc", "prompt"])
    cm_promote.add_argument("--provider", default="auto")

    gh_sync = sub.add_parser("github-sync")
    gh_sub = gh_sync.add_subparsers(dest="github_command", required=True)
    gh_config = gh_sub.add_parser("configure")
    gh_config.add_argument("--project-path", default=".")
    gh_config.add_argument("--private-repo", required=True)
    gh_config.add_argument("--public-repo", default="")
    gh_bootstrap = gh_sub.add_parser("bootstrap")
    gh_bootstrap.add_argument("--project-path", default=".")
    gh_bootstrap.add_argument("--private-repo", default="")
    gh_bootstrap.add_argument("--public-repo", default="")
    gh_bootstrap.add_argument("--no-create-remote-repos", action="store_true")
    gh_private = gh_sub.add_parser("private")
    gh_private.add_argument("--project-path", default=".")
    gh_auto_private = gh_sub.add_parser("auto-private")
    gh_auto_private.add_argument("--project-path", default=".")
    gh_auto_private.add_argument("--message", default="nexus auto private sync")
    gh_public = gh_sub.add_parser("public")
    gh_public.add_argument("--project-path", default=".")
    gh_public.add_argument("--confirm", action="store_true")
    gh_guide = gh_sub.add_parser("guide")
    gh_guide.add_argument("--project-path", default=".")
    gh_publish_guide = gh_sub.add_parser("publish-guide-feishu")
    gh_publish_guide.add_argument("--project-path", default=".")

    guide = sub.add_parser("guide")
    guide_sub = guide.add_subparsers(dest="guide_command", required=True)
    guide_generate = guide_sub.add_parser("generate")
    guide_generate.add_argument("--project-path", default=".")
    guide_generate.add_argument("--target", default="auto", choices=["auto", "nexus", "verix", "project"])
    guide_generate.add_argument("--no-feishu-sync", action="store_true")
    guide_publish = guide_sub.add_parser("publish-feishu")
    guide_publish.add_argument("--project-path", default=".")
    guide_publish.add_argument("--target", default="auto", choices=["auto", "nexus", "verix", "project"])
    guide_sync = guide_sub.add_parser("sync")
    guide_sync.add_argument("--project-path", default=".")
    guide_sync.add_argument("--target", default="auto", choices=["auto", "nexus", "verix", "project"])

    self_sync = sub.add_parser("self-sync")
    self_sync.add_argument("--project-path", default=".")
    self_sync.add_argument("--target", default="auto", choices=["auto", "nexus", "verix", "project"])
    self_sync.add_argument("--no-feishu-sync", action="store_true")

    supplement_init = sub.add_parser("supplement-init")
    supplement_init.add_argument("--project-path", default=".")
    supplement_init.add_argument("--idea", default="补充初始化")
    supplement_init.add_argument("--target", default="auto", choices=["auto", "nexus", "verix", "project"])
    supplement_init.add_argument("--no-github-sync", action="store_true")
    supplement_init.add_argument("--no-feishu-sync", action="store_true")

    showcase = sub.add_parser("system-showcase")
    showcase_sub = showcase.add_subparsers(dest="showcase_command", required=True)
    showcase_generate = showcase_sub.add_parser("generate")
    showcase_generate.add_argument("--project-path", default=".")
    showcase_generate.add_argument("--provider", default="auto")
    showcase_generate.add_argument("--no-feishu-sync", action="store_true")
    showcase_explain = showcase_sub.add_parser("explain")
    showcase_explain.add_argument("node_id")
    showcase_explain.add_argument("--project-path", default=".")
    showcase_publish = showcase_sub.add_parser("publish-feishu")
    showcase_publish.add_argument("--project-path", default=".")
    showcase_publish.add_argument("--confirm", action="store_true")

    feishu = sub.add_parser("feishu")
    feishu_sub = feishu.add_subparsers(dest="feishu_command", required=True)
    feishu_config = feishu_sub.add_parser("configure")
    feishu_config.add_argument("--project-path", default=".")
    feishu_config.add_argument("--app-id", default="")
    feishu_config.add_argument("--app-secret-env", default="FEISHU_APP_SECRET")
    feishu_config.add_argument("--app-id-path", default="<LOCAL_PATH_REDACTED>")
    feishu_config.add_argument("--app-secret-path", default="<LOCAL_PATH_REDACTED>")
    feishu_config.add_argument("--folder-token", default="")
    feishu_config.add_argument("--folder-token-path", default="")
    feishu_config.add_argument("--doc-token", default="")
    feishu_config.add_argument("--doc-token-path", default="")
    feishu_config.add_argument("--doc-base-url", default="")
    feishu_setup = feishu_sub.add_parser("setup")
    feishu_setup.add_argument("--project-path", default=".")
    feishu_setup.add_argument("--app-id-path", default="")
    feishu_setup.add_argument("--app-secret-path", default="")
    feishu_setup.add_argument("--folder-token", default="")
    feishu_setup.add_argument("--folder-token-path", default="")
    feishu_setup.add_argument("--doc-token", default="")
    feishu_setup.add_argument("--doc-token-path", default="")
    feishu_setup.add_argument("--doc-base-url", default="")
    feishu_setup.add_argument("--guide-only", action="store_true")
    feishu_setup.add_argument("--no-network", action="store_true")
    feishu_setup.add_argument("--research-docs", action="store_true")
    feishu_setup.add_argument("--approve-online-search", action="store_true")
    feishu_setup.add_argument("--provider", default="auto")
    feishu_doctor = feishu_sub.add_parser("doctor")
    feishu_doctor.add_argument("--project-path", default=".")
    feishu_doctor.add_argument("--no-network", action="store_true")
    feishu_doctor.add_argument("--create-doc", action="store_true")
    feishu_doctor.add_argument("--title", default="Nexus Feishu smoke test")
    feishu_doctor.add_argument("--folder-token", default="")
    feishu_doctor.add_argument("--folder-token-path", default="")
    feishu_record = feishu_sub.add_parser("record")
    feishu_record.add_argument("--project-path", default=".")
    feishu_record.add_argument("--title", default="")
    feishu_record.add_argument("--content", default="")
    feishu_record.add_argument("--content-file", default="")
    feishu_record.add_argument("--folder-token", default="")
    feishu_record.add_argument("--folder-token-path", default="")
    feishu_record.add_argument("--doc-token", default="")
    feishu_record.add_argument("--doc-token-path", default="")
    feishu_record.add_argument("--doc-base-url", default="")
    feishu_record.add_argument("--provider", default="auto")
    feishu_record.add_argument("--no-network", action="store_true")
    feishu_sub.add_parser("login")

    board = sub.add_parser("board")
    board_sub = board.add_subparsers(dest="board_command", required=True)
    board_show = board_sub.add_parser("show")
    board_show.add_argument("--project-path", default="")
    board_update = board_sub.add_parser("update")
    board_update.add_argument("--project-path", default="")
    board_update.add_argument("--status", required=True)
    board_point = board_sub.add_parser("point")
    board_point.add_argument("--project-path", default="")
    board_point.add_argument("point")

    conv = sub.add_parser("conversation-from-file")
    conv.add_argument("transcript_path")
    conv.add_argument("--provider", default="auto")
    conv.add_argument("--selector", default="")

    conversation = sub.add_parser("conversation")
    conversation_sub = conversation.add_subparsers(dest="conversation_command", required=True)
    conversation_sub.add_parser("sessions")
    extract = conversation_sub.add_parser("extract")
    extract.add_argument("--current", action="store_true")
    extract.add_argument("--all", action="store_true", dest="all_history")
    extract.add_argument("--match", default="")
    extract.add_argument("--task", default="")
    extract.add_argument("--session-id", default="")
    extract.add_argument("--file", default="")
    extract.add_argument("--selector", default="")
    extract.add_argument("--provider", default="auto")
    to_workflow = sub.add_parser("conversation-to-workflow")
    to_workflow.add_argument("--current", action="store_true")
    to_workflow.add_argument("--all", action="store_true", dest="all_history")
    to_workflow.add_argument("--match", default="")
    to_workflow.add_argument("--task", default="")
    to_workflow.add_argument("--session-id", default="")
    to_workflow.add_argument("--file", default="")
    to_workflow.add_argument("--selector", default="")
    to_workflow.add_argument("--provider", default="auto")

    install_skill = sub.add_parser("install-generated-skill")
    install_skill.add_argument("run_id", nargs="?", default="latest")
    install_skill.add_argument("--confirm", action="store_true")

    invoke = sub.add_parser("invoke")
    invoke.add_argument("request")

    model = sub.add_parser("model")
    model_sub = model.add_subparsers(dest="model_command", required=True)
    model_sub.add_parser("status")
    model_sub.add_parser("list")
    model_intent = model_sub.add_parser("intent")
    model_intent.add_argument("request")
    model_set = model_sub.add_parser("set")
    model_set.add_argument("profile")
    model_configure = model_sub.add_parser("configure")
    model_configure.add_argument("--provider", default="")
    model_configure.add_argument("--profile", default="")
    model_configure.add_argument("--model", default="")
    model_configure.add_argument("--base-url", default="")
    model_configure.add_argument("--api-key-env", default="")
    model_configure.add_argument("--api-key-file", default="")
    model_configure.add_argument("--adapter", default="openai-compatible")

    skill = sub.add_parser("skill")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)
    skill_sub.add_parser("doctor")

    report = sub.add_parser("report")
    report.add_argument("run_id")

    nxv = sub.add_parser("nexus-verix-loop")
    nxv.add_argument("--cases-file", default="")
    nxv.add_argument("--problem-matrix", default="")
    nxv.add_argument("--case-id", action="append", default=[])
    nxv.add_argument("--e2e-root", default="/tmp/nexus-verix-orchestrator")
    nxv.add_argument("--verix-root", default="")
    nxv.add_argument("--max-iterations", type=int, default=3)
    nxv.add_argument("--dry-run-plan", action="store_true")
    nxv.add_argument("--execute", action="store_true")
    nxv.add_argument("--allow-external-side-effects", action="store_true")
    nxv.add_argument("--no-create-branch", action="store_true")
    nxv.add_argument("--skill-replay-mode", default="auto", choices=["auto", "queue-only", "off"])
    nxv.add_argument("--skill-replay-step-timeout", type=int, default=600)
    nxv.add_argument("--skill-replay-status-interval", type=int, default=15)
    nxv.add_argument("--verix-mode", default="auto", choices=["auto", "off"])
    nxv.add_argument("--patch-mode", default="auto", choices=["auto", "command", "off"])
    nxv.add_argument("--patch-command", default="")
    nxv.add_argument("--regression-mode", default="full", choices=["full", "build-check"])

    nxvm = sub.add_parser("nexus-verix-monitor")
    nxvm.add_argument("--dry-run-plan", action="store_true")
    nxvm.add_argument("--init-run", action="store_true")
    nxvm.add_argument("--e2e-root", default="/tmp/nexus-verix-monitor")
    nxvm.add_argument("--verix-root", default="")
    nxvm.add_argument("--history-export", default="")
    nxvm.add_argument("--max-iterations", type=int, default=3)
    nxvm.add_argument("--run-id", default="")
    nxvm.add_argument("--allow-external-side-effects", action="store_true")

    sub.add_parser("doctor")
    sub.add_parser("configure")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.next_prompt_mode:
        os.environ["NEXUS_NEXT_PROMPT_MODE"] = args.next_prompt_mode
    else:
        _load_session_next_prompt_mode(Path(args.root).expanduser().resolve())
    if args.workflow_surface:
        os.environ["NEXUS_WORKFLOW_SURFACE"] = args.workflow_surface
    root = Path(args.root).expanduser().resolve()
    runner = Runner(root)

    if args.command in {"run", "research"}:
        interaction = runner.run(
            args.idea,
            project_path=Path(args.project_path),
            provider_name=args.provider,
            model_name=getattr(args, "model", ""),
            max_candidates=args.max_candidates,
            approve_online_search=bool(getattr(args, "approve_online_search", False)),
        )
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "resume":
        if args.approve:
            runner.approve(args.run_id, args.approve)
        interaction = runner.resume(args.run_id, from_node=args.from_node, force_node=args.force_node)
        print(render_cli_interaction(interaction))
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "status":
        interaction = runner.status(args.run_id)
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        state_path = root / ".data" / "runs" / str(interaction.get("run_id") or args.run_id) / "state.json"
        if state_path.exists():
            print(state_path.read_text(encoding="utf-8").strip())
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked", "failed"} else 1

    if args.command == "approve":
        interaction = runner.approve(args.run_id, args.stage)
        print(render_cli_interaction(interaction))
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "approve-and-continue":
        interaction = runner.approve_and_continue(args.run_id, args.stage)
        print(render_cli_interaction(interaction))
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "plan-implementation":
        interaction = runner.approve(args.run_id, "implementation-plan")
        print(render_cli_interaction(interaction))
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "execute-code-change":
        interaction = runner.execute_code_change(args.run_id, provider_name=args.provider)
        print(render_cli_interaction(interaction))
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "diff":
        interaction = runner.diff(args.run_id)
        print(render_cli_interaction(interaction))
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "apply":
        interaction = runner.apply_patch_to_target(args.run_id)
        print(render_cli_interaction(interaction))
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "test":
        interaction = runner.run_tests(args.run_id, command=args.cmd)
        print(render_cli_interaction(interaction))
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "continue":
        interaction = runner.continue_run(args.run_id, args.request, provider_name=args.provider)
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "continue-after-input":
        interaction = runner.continue_after_input(args.run_id, note=args.note)
        print(render_cli_interaction(interaction))
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "recover":
        interaction = runner.recover(args.run_id, request=args.request)
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "handoff-for-debug":
        interaction = runner.handoff_for_debug(args.run_id, reason=args.reason)
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "append-debug-worklog":
        interaction = runner.append_debug_worklog(
            args.run_id,
            handoff_id=args.handoff_id,
            kind=args.kind,
            summary=args.summary,
            result=args.result,
            command=args.debug_command,
            paths=args.paths or [],
        )
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "rebind-and-continue":
        interaction = runner.rebind_and_continue(args.run_id, handoff_id=args.handoff_id)
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "debug-status":
        interaction = runner.debug_status(args.run_id)
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "init-project":
        interaction = runner.init_project(
            args.idea,
            parent=Path(args.parent),
            provider_name=args.provider,
            enable_feishu_autowrite=args.enable_feishu_autowrite,
            private_repo=args.private_repo,
            public_repo=args.public_repo,
            github_sync=not args.no_github_sync,
            feishu_sync=not args.no_feishu_sync,
            raw_user_request=args.raw_user_request,
            normalized_request=args.normalized_request,
        )
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "prepare-project":
        interaction = runner.prepare_project(Path(args.project_path))
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "rerank-candidates":
        interaction = runner.rerank_candidates(args.run_id, provider_name=args.provider)
        print(render_cli_interaction(interaction))
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "conversation-manager":
        if args.cm_command == "init":
            interaction = runner.conversation_manager_init(Path(args.project_path))
        elif args.cm_command == "ingest":
            interaction = runner.conversation_manager_ingest(Path(args.project_path), Path(args.file), source_agent=args.source_agent)
        elif args.cm_command == "promote":
            interaction = runner.conversation_manager_promote(Path(args.project_path), Path(args.session_file), target=args.to, provider_name=args.provider)
        else:
            parser.error("Unsupported conversation-manager command")
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "github-sync":
        if args.github_command == "configure":
            interaction = runner.github_sync_configure(Path(args.project_path), private_repo=args.private_repo, public_repo=args.public_repo)
        elif args.github_command == "bootstrap":
            interaction = runner.github_sync_bootstrap(Path(args.project_path), private_repo=args.private_repo, public_repo=args.public_repo, create_remote_repos=not args.no_create_remote_repos)
        elif args.github_command == "private":
            interaction = runner.github_sync_private(Path(args.project_path))
        elif args.github_command == "auto-private":
            interaction = runner.github_sync_auto_private(Path(args.project_path), commit_message=args.message)
        elif args.github_command == "public":
            interaction = runner.github_sync_public(Path(args.project_path), confirm=args.confirm)
        elif args.github_command == "guide":
            interaction = runner.github_sync_guide(Path(args.project_path))
        elif args.github_command == "publish-guide-feishu":
            interaction = runner.github_sync_guide(Path(args.project_path), publish_feishu=True)
        else:
            parser.error("Unsupported github-sync command")
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "guide":
        if args.guide_command == "generate":
            interaction = runner.operation_guide(Path(args.project_path), target=args.target, feishu_sync_enabled=not args.no_feishu_sync)
        elif args.guide_command in {"publish-feishu", "sync"}:
            interaction = runner.operation_guide(Path(args.project_path), target=args.target, publish_feishu=True)
        else:
            parser.error("Unsupported guide command")
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "self-sync":
        interaction = runner.self_sync(Path(args.project_path), target=args.target, feishu_sync_enabled=not args.no_feishu_sync)
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "supplement-init":
        interaction = runner.supplemental_init(
            Path(args.project_path),
            idea=args.idea,
            target=args.target,
            github_private_enabled=not args.no_github_sync,
            feishu_sync_enabled=not args.no_feishu_sync,
        )
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "system-showcase":
        if args.showcase_command == "generate":
            interaction = runner.system_showcase_generate(Path(args.project_path), provider_name=args.provider, feishu_sync_enabled=not args.no_feishu_sync)
        elif args.showcase_command == "explain":
            interaction = runner.system_showcase_explain(Path(args.project_path), args.node_id)
        elif args.showcase_command == "publish-feishu":
            interaction = runner.system_showcase_publish_feishu(Path(args.project_path), confirm=args.confirm)
        else:
            parser.error("Unsupported system-showcase command")
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "feishu":
        if args.feishu_command == "configure":
            interaction = runner.feishu_configure(
                Path(args.project_path),
                app_id=args.app_id,
                app_secret_env=args.app_secret_env,
                app_id_path=args.app_id_path,
                app_secret_path=args.app_secret_path,
                folder_token=args.folder_token,
                folder_token_path=args.folder_token_path,
                doc_token=args.doc_token,
                doc_token_path=args.doc_token_path,
                doc_base_url=args.doc_base_url,
            )
        elif args.feishu_command == "setup":
            interaction = runner.feishu_setup_flow(
                Path(args.project_path),
                app_id_path=args.app_id_path,
                app_secret_path=args.app_secret_path,
                folder_token=args.folder_token,
                folder_token_path=args.folder_token_path,
                doc_token=args.doc_token,
                doc_token_path=args.doc_token_path,
                doc_base_url=args.doc_base_url,
                guide_only=args.guide_only,
                no_network=args.no_network,
                research_docs=args.research_docs,
                approve_online_search=args.approve_online_search,
                provider_name=args.provider,
            )
        elif args.feishu_command == "doctor":
            interaction = runner.feishu_doctor_flow(
                Path(args.project_path),
                no_network=args.no_network,
                create_doc=args.create_doc,
                title=args.title,
                folder_token=args.folder_token,
                folder_token_path=args.folder_token_path,
            )
        elif args.feishu_command == "record":
            content = args.content
            if args.content_file:
                content = Path(args.content_file).expanduser().read_text(encoding="utf-8", errors="ignore")
            interaction = runner.feishu_record_flow(
                Path(args.project_path),
                title=args.title,
                content=content,
                folder_token=args.folder_token,
                folder_token_path=args.folder_token_path,
                doc_token=args.doc_token,
                doc_token_path=args.doc_token_path,
                doc_base_url=args.doc_base_url,
                provider_name=args.provider,
                no_network=args.no_network,
            )
        elif args.feishu_command == "login":
            interaction = runner.feishu_login_flow()
        else:
            parser.error("Unsupported feishu command")
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "board":
        if args.board_command == "show":
            interaction = runner.board_show(Path(args.project_path) if args.project_path else None)
        elif args.board_command == "update":
            interaction = runner.board_update(args.status, project_path=Path(args.project_path) if args.project_path else None)
        elif args.board_command == "point":
            interaction = runner.board_point(args.point, project_path=Path(args.project_path) if args.project_path else None)
        else:
            parser.error("Unsupported board command")
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "conversation-from-file":
        interaction = runner.conversation_from_file(Path(args.transcript_path), provider_name=args.provider, selector=args.selector)
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "conversation":
        if args.conversation_command == "sessions":
            payload = runner.conversation_sessions()
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.conversation_command == "extract":
            interaction = runner.conversation_to_workflow(
                current=args.current,
                all_history=args.all_history,
                match=args.match,
                task=args.task,
                session_id=args.session_id,
                file_path=args.file,
                selector=args.selector,
                provider_name=args.provider,
            )
            print(render_cli_interaction(interaction))
            print(f"run_id：{interaction.get('run_id', '')}")
            return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "conversation-to-workflow":
        interaction = runner.conversation_to_workflow(
            current=args.current,
            all_history=args.all_history,
            match=args.match,
            task=args.task,
            session_id=args.session_id,
            file_path=args.file,
            selector=args.selector,
            provider_name=args.provider,
        )
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "install-generated-skill":
        interaction = runner.install_generated_skill(args.run_id, confirm=args.confirm)
        print(render_cli_interaction(interaction))
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "invoke":
        if "配置模型" in args.request and "低强度" in args.request and "高强度" in args.request:
            return handle_model_intent(root, args.request)
        interaction = runner.invoke(args.request)
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0 if interaction.get("previous_task_status") in {"completed", "blocked"} else 1

    if args.command == "model":
        return handle_model_command(root, args)

    if args.command == "skill":
        if args.skill_command == "doctor":
            installed = Path.home() / ".codex" / "skills" / "nexus-workflow" / "SKILL.md"
            local = root / "skills" / "nexus-workflow" / "SKILL.md"
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
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0

    if args.command == "status":
        state_path = root / ".data" / "runs" / args.run_id / "state.json"
        print(state_path.read_text(encoding="utf-8").strip())
        return 0

    if args.command == "report":
        report_path = root / ".data" / "runs" / args.run_id / "reports" / "final_report.md"
        print(report_path.read_text(encoding="utf-8").strip())
        return 0

    if args.command == "nexus-verix-loop":
        return handle_nexus_verix_loop(args, root)

    if args.command == "nexus-verix-monitor":
        return handle_nexus_verix_monitor(args, root)

    if args.command == "doctor":
        payload = doctor(root)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "configure":
        path = write_default_config(root)
        payload = doctor(root)
        print(f"已写入 nexus 本地 provider 配置：{path}")
        print("未安装依赖，未读取密钥，未执行登录；模型 API 请使用 python -m nexus.cli model configure。")
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


def interaction_run_id(root: Path) -> str:
    runs_dir = root / ".data" / "runs"
    if not runs_dir.exists():
        return ""
    latest = max((path for path in runs_dir.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime)
    return latest.name


def _load_session_next_prompt_mode(root: Path) -> None:
    path = root / ".data" / "session" / "next_prompt_mode.json"
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    mode = str(payload.get("mode") or "").strip().lower()
    if mode in {"workflow", "cli"}:
        os.environ["NEXUS_NEXT_PROMPT_MODE"] = mode


def handle_nexus_verix_loop(args: argparse.Namespace, root: Path) -> int:
    script = Path(__file__).resolve().parents[1] / "scripts" / "lab" / "run_nexus_verix_orchestrator.py"
    argv = [
        sys.executable,
        str(script),
        "--nexus-root",
        str(root),
        "--e2e-root",
        args.e2e_root,
        "--max-iterations",
        str(args.max_iterations),
        "--skill-replay-mode",
        args.skill_replay_mode,
        "--skill-replay-step-timeout",
        str(args.skill_replay_step_timeout),
        "--skill-replay-status-interval",
        str(args.skill_replay_status_interval),
        "--verix-mode",
        args.verix_mode,
        "--patch-mode",
        args.patch_mode,
        "--regression-mode",
        args.regression_mode,
    ]
    if args.cases_file:
        argv.extend(["--cases-file", args.cases_file])
    if args.problem_matrix:
        argv.extend(["--problem-matrix", args.problem_matrix])
    if args.verix_root:
        argv.extend(["--verix-root", args.verix_root])
    for case_id in args.case_id:
        argv.extend(["--case-id", case_id])
    if args.dry_run_plan:
        argv.append("--dry-run-plan")
    if args.execute:
        argv.append("--execute")
    if args.allow_external_side_effects:
        argv.append("--allow-external-side-effects")
    if args.no_create_branch:
        argv.append("--no-create-branch")
    if args.patch_command:
        argv.extend(["--patch-command", args.patch_command])
    completed = subprocess.run(argv, cwd=root, text=True, capture_output=True, check=False)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode


def handle_nexus_verix_monitor(args: argparse.Namespace, root: Path) -> int:
    script = Path(__file__).resolve().parents[1] / "scripts" / "lab" / "init_monitor_multi_agent_run.py"
    argv = [
        sys.executable,
        str(script),
        "--nexus-root",
        str(root),
        "--e2e-root",
        args.e2e_root,
        "--max-iterations",
        str(args.max_iterations),
    ]
    if args.dry_run_plan:
        argv.append("--dry-run-plan")
    if args.init_run:
        argv.append("--init-run")
    if args.verix_root:
        argv.extend(["--verix-root", args.verix_root])
    if args.history_export:
        argv.extend(["--history-export", args.history_export])
    if args.run_id:
        argv.extend(["--run-id", args.run_id])
    if args.allow_external_side_effects:
        argv.append("--allow-external-side-effects")
    completed = subprocess.run(argv, cwd=root, text=True, capture_output=True, check=False)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode


def handle_model_command(root: Path, args: argparse.Namespace) -> int:
    if args.model_command == "intent":
        return handle_model_intent(root, args.request)
    if args.model_command in {"status", "list"}:
        interaction = Runner(root).model_status()
        print(render_cli_interaction(interaction))
        print(f"run_id：{interaction.get('run_id', '')}")
        return 0
    if args.model_command == "set":
        profile = resolve_profile(root, args.profile)
        if profile is None:
            payload = profile_status(root)
            print(render_cli_interaction({
                "previous_task_status": "blocked",
                "previous_task_output": f"未找到模型 profile：{args.profile}。",
                "next_task_prompt": "运行 python -m nexus.cli model list 查看可用/需配置/暂未提供的接口。",
                "blocked_reason": "model_profile_not_found",
            }))
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        path = set_session_model(root, profile.name)
        print(render_cli_interaction({
            "previous_task_status": "completed",
            "previous_task_output": f"已将当前 nexus 默认基座模型设置为：{profile.name}。",
            "next_task_prompt": "下一次使用 $nexus-workflow 调研时，如果没有显式指定基座模型，将使用这个默认模型。",
            "artifact_refs": [str(path)],
        }))
        return 0
    if args.model_command == "configure":
        if not args.provider and not args.profile and not args.model:
            payload = profile_status(root)
            print(render_cli_interaction({
                "previous_task_status": "blocked",
                "previous_task_output": _model_status_summary(payload),
                "next_task_prompt": "请提供 provider/model/base_url/api_key_env，例如：python -m nexus.cli model configure --provider qwen --profile qwen-plus --model qwen-plus --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --api-key-env DASHSCOPE_API_KEY。",
                "blocked_reason": "model_configuration_required",
            }))
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        provider = args.provider or args.profile
        profile_name = args.profile or args.model or provider
        existing = resolve_profile(root, profile_name) or resolve_profile(root, provider)
        profile = ModelProfile(
            name=profile_name,
            provider=provider,
            adapter=args.adapter or (existing.adapter if existing else "openai-compatible"),
            model=args.model or (existing.model if existing else ""),
            base_url=args.base_url or (existing.base_url if existing else ""),
            api_key_env=args.api_key_env or ("" if args.api_key_file else (existing.api_key_env if existing else "")),
            api_key_file=_configured_api_key_file(root, profile_name, args.api_key_file) or (existing.api_key_file if existing else ""),
            structured_output_mode=existing.structured_output_mode if existing else "strict_json_retry",
            configured=True,
            notes=f"Configured via nexus model configure for {provider}.",
        )
        path = save_profile(root, profile)
        session_path = set_session_model(root, profile.name)
        print(render_cli_interaction({
            "previous_task_status": "completed",
            "previous_task_output": f"已保存模型 profile：{profile.name}；密钥只记录 env/key-file 引用，不写入密钥值。",
            "next_task_prompt": f"现在可以运行 python -m nexus.cli research \"<需求>\" --model {profile.name}，或直接使用 $nexus-workflow 调研某个项目，基座模型使用 {profile.name}。",
            "artifact_refs": [str(path), str(session_path)],
        }))
        return 0
    raise SystemExit(f"Unsupported model command: {args.model_command}")


def handle_model_intent(root: Path, request: str) -> int:
    parsed = _parse_model_intent(root, request)
    if parsed["action"] == "configure_intensity_defaults":
        return _handle_intensity_model_config(root, parsed)
    if parsed["action"] == "status":
        payload = profile_status(root)
        print(render_cli_interaction({
            "previous_task_status": "blocked",
            "previous_task_output": _model_status_summary(payload),
            "next_task_prompt": "例如输入：使用 $nexus-workflow 配置 qwen 模型，模型名 qwen-plus，apikey 文件 /path/to/qwen-key；或输入：使用 $nexus-workflow 使用 codexcli 模型 作为次选 fallback。",
            "blocked_reason": "model_configuration_prompt",
        }))
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    provider_name = str(parsed.get("provider") or "")
    if provider_name in unsupported_profiles():
        print(render_cli_interaction({
            "previous_task_status": "blocked",
            "previous_task_output": f"{provider_name} provider 暂未提供 adapter。",
            "next_task_prompt": "当前默认优先链路：低强度 API 槽位 -> codex-cli gpt-5.4 -> codex-mcp；高强度 codex-cli gpt-5.4 -> API 槽位 -> codex-mcp；可配置链路：qwen/openai/deepseek/kimi/gemini/anthropic/zhipu/minimax/doubao/baichuan。",
            "blocked_reason": "model_provider_not_implemented",
        }))
        return 0

    if parsed["action"] == "use":
        profile = resolve_profile(root, provider_name)
        if profile is None:
            print(render_cli_interaction({
                "previous_task_status": "blocked",
                "previous_task_output": f"未找到模型 profile：{provider_name}。",
                "next_task_prompt": "输入“使用 $nexus-workflow 更换模型”查看已有接口，或输入“使用 $nexus-workflow 配置 <provider> 模型 ...”。",
                "blocked_reason": "model_profile_not_found",
            }))
            return 0
        if profile.adapter in {"codex-mcp", "codex-cli"} or resolve_api_key(profile):
            path = set_session_model(root, profile.name)
            print(render_cli_interaction({
                "previous_task_status": "completed",
                "previous_task_output": f"已将当前 nexus 默认基座模型设置为：{profile.name}。",
                "next_task_prompt": f"现在可以输入：使用 $nexus-workflow 调研某个具体项目，基座模型使用 {profile.name}。",
                "artifact_refs": [str(path)],
            }))
            return 0
        print(render_cli_interaction({
            "previous_task_status": "blocked",
            "previous_task_output": f"{profile.name} 尚未配置可用 API key；需要 model、base_url、api_key_env 或 api_key_file。",
            "next_task_prompt": _configure_prompt_for(profile),
            "blocked_reason": "model_configuration_required",
        }))
        return 0

    if parsed["action"] == "configure":
        profile = _profile_from_intent(root, parsed)
        missing = _missing_model_config(profile)
        if missing:
            print(render_cli_interaction({
                "previous_task_status": "blocked",
                "previous_task_output": f"{profile.name} 配置信息不完整，缺少：{', '.join(missing)}。",
                "next_task_prompt": _configure_prompt_for(profile),
                "blocked_reason": "model_configuration_required",
            }))
            return 0
        path = save_profile(root, profile)
        session_path = set_session_model(root, profile.name)
        print(render_cli_interaction({
            "previous_task_status": "completed",
            "previous_task_output": f"已保存模型 profile：{profile.name}；密钥只记录 env/key-file 引用，不写入密钥值。",
            "next_task_prompt": f"现在可以输入：使用 $nexus-workflow 调研某个具体项目，基座模型使用 {profile.name}。",
            "artifact_refs": [str(path), str(session_path)],
        }))
        return 0

    print(render_cli_interaction({
        "previous_task_status": "blocked",
        "previous_task_output": "没有识别出模型配置意图。",
        "next_task_prompt": "输入“使用 $nexus-workflow 更换模型”查看配置方式。",
        "blocked_reason": "model_intent_unrecognized",
    }))
    return 0


def _model_status_summary(payload: dict[str, object]) -> str:
    intensity = payload.get("intensity") if isinstance(payload.get("intensity"), dict) else {}
    low_api = str(intensity.get("low_api_profile") or "未配置")
    high_api = str(intensity.get("high_api_profile") or "未配置")
    return (
        f"低强度 API 槽位：{low_api}；低强度实际顺序：API {low_api} -> codex-cli gpt-5.4 -> codex-mcp；"
        f"高强度 API fallback 槽位：{high_api}；高强度实际顺序：codex-cli gpt-5.4 -> API {high_api} -> codex-mcp；"
        f" 当前默认模型：{payload.get('current')}; "
        f"可直接使用/已配置：{', '.join(payload.get('available_or_builtin', []))}; "
        f"需要配置：{', '.join(payload.get('needs_config', []))}; "
        f"暂未提供：{', '.join(payload.get('unsupported', []))}。"
    )


def _parse_model_intent(root: Path, request: str) -> dict[str, object]:
    normalized = request.strip()
    if "低强度" in normalized and "高强度" in normalized:
        return {
            "action": "configure_intensity_defaults",
            "base_url": _extract_base_url(normalized) or DASHSCOPE_BASE_URL,
            "api_key_file": _extract_api_key_file(normalized),
            "low_model": _extract_after_marker(normalized, "低强度 API 使用") or _extract_after_marker(normalized, "低强度使用") or "qwen-plus",
            "high_model": _extract_after_marker(normalized, "高强度 API 使用") or _extract_after_marker(normalized, "高强度使用") or "qwen3.7max",
        }
    if any(marker in normalized for marker in ["更换模型", "切换模型", "模型配置"]) and not any(marker in normalized for marker in ["使用", "配置"] if marker != "使用"):
        return {"action": "status"}
    provider = _detect_provider(root, normalized)
    fields = {
        "action": "configure" if "配置" in normalized or _extract_api_key_file(normalized) or _extract_api_key_env(normalized) or _extract_base_url(normalized) else "use",
        "provider": provider,
        "profile": _extract_profile(normalized) or _extract_model(normalized) or provider,
        "model": _extract_model(normalized),
        "base_url": _extract_base_url(normalized),
        "api_key_env": _extract_api_key_env(normalized),
        "api_key_file": _extract_api_key_file(normalized),
    }
    if not provider:
        fields["action"] = "status"
    return fields


def _detect_provider(root: Path, text: str) -> str:
    compact = text.lower().replace(" ", "").replace("_", "-")
    aliases = {
        "codexmcp": "codex-mcp",
        "codex-mcp": "codex-mcp",
        "codexcli": "codex-cli",
        "codex-cli": "codex-cli",
        "claude": "anthropic",
    }
    for alias, provider in aliases.items():
        if alias in compact:
            return provider
    for name in [*load_profiles(root).keys(), *unsupported_profiles()]:
        if name.lower().replace("-", "") in compact.replace("-", ""):
            return name
    return ""


def _extract_model(text: str) -> str:
    patterns = [
        r"(?:模型名|model(?:\s*name)?)[：:=\s]+([^，,；;\s]+)",
        r"基座模型使用[：:=\s]*([^，,；;\s]+)",
    ]
    return _first_match(text, patterns)


def _extract_profile(text: str) -> str:
    return _first_match(text, [r"(?:profile|配置名)[：:=\s]+([^，,；;\s]+)"])


def _extract_base_url(text: str) -> str:
    return _first_match(text, [r"(?:base_url|base-url|接口地址|链接)(?:是)?[：:=\s]+(https?://[^，,；;。\s]+)"])


def _extract_api_key_env(text: str) -> str:
    return _first_match(text, [r"(?:apikey|api-key|api_key|key|密钥)\s*(?:环境变量|env)[：:=\s]+([A-Za-z_][A-Za-z0-9_]*)"])


def _extract_api_key_file(text: str) -> str:
    return _first_match(text, [r"(?:apikey|api-key|api_key|key|密钥)\s*(?:来自|文件|file|路径)?[：:=\s]+([^，,；;\n]+)"])


def _first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _strip_trailing_sentence_punctuation(match.group(1).strip().strip("\"'"))
    return ""


def _strip_trailing_sentence_punctuation(value: str) -> str:
    return value.rstrip("。．.!！?？；;，,")


def _profile_from_intent(root: Path, parsed: dict[str, object]) -> ModelProfile:
    provider = str(parsed.get("provider") or "")
    profile_name = str(parsed.get("profile") or parsed.get("model") or provider)
    existing = resolve_profile(root, profile_name) or resolve_profile(root, provider)
    adapter = existing.adapter if existing else ("anthropic" if provider == "anthropic" else "openai-compatible")
    return ModelProfile(
        name=profile_name,
        provider=provider,
        adapter=adapter,
        model=str(parsed.get("model") or (existing.model if existing else "")),
        base_url=str(parsed.get("base_url") or (existing.base_url if existing else "")),
        api_key_env=str(parsed.get("api_key_env") or ("" if parsed.get("api_key_file") else (existing.api_key_env if existing else ""))),
        api_key_file=_configured_api_key_file(root, profile_name, str(parsed.get("api_key_file") or "")) or (existing.api_key_file if existing else ""),
        structured_output_mode=existing.structured_output_mode if existing else "strict_json_retry",
        configured=True,
        notes=f"Configured via nexus model intent for {provider}.",
    )


def _missing_model_config(profile: ModelProfile) -> list[str]:
    if profile.adapter in {"codex-mcp", "codex-cli"}:
        return []
    missing = []
    if not profile.model:
        missing.append("model")
    if profile.adapter != "anthropic" and not profile.base_url:
        missing.append("base_url")
    if not profile.api_key_env and not profile.api_key_file:
        missing.append("api_key_env 或 api_key_file")
    return missing


def _configure_prompt_for(profile: ModelProfile) -> str:
    if profile.adapter in {"codex-mcp", "codex-cli"}:
        return f"输入：使用 $nexus-workflow 使用 {profile.name} 模型。"
    base_url = profile.base_url or "<base_url>"
    model = profile.model or "<model>"
    provider = profile.provider or profile.name
    return f"输入：使用 $nexus-workflow 配置 {provider} 模型，模型名 {model}，base_url {base_url}，apikey 文件 <key文件路径>；也可以把最后一项换成 apikey 环境变量 <ENV_NAME>。"


def _configured_api_key_file(root: Path, profile_name: str, api_key_file: str) -> str:
    if not api_key_file:
        return ""
    return import_api_key_file(root, profile_name, api_key_file)


def _handle_intensity_model_config(root: Path, parsed: dict[str, object]) -> int:
    api_key_file = str(parsed.get("api_key_file") or "")
    base_url = str(parsed.get("base_url") or DASHSCOPE_BASE_URL)
    low_model = str(parsed.get("low_model") or "qwen-plus")
    high_model = str(parsed.get("high_model") or "qwen3.7max")
    saved = []
    profiles: list[ModelProfile] = []
    for model_name in [low_model, high_model]:
        profile_name = _profile_name_for_api_model(model_name)
        existing = resolve_profile(root, profile_name)
        imported_key_file = _configured_api_key_file(root, profile_name, api_key_file)
        profile = ModelProfile(
            name=profile_name,
            provider=existing.provider if existing else _infer_api_provider(model_name, base_url),
            adapter="openai-compatible",
            model=model_name,
            base_url=base_url,
            api_key_env="",
            api_key_file=imported_key_file or (existing.api_key_file if existing else ""),
            structured_output_mode=existing.structured_output_mode if existing else "strict_json_retry",
            configured=True,
            notes=f"Configured via nexus intensity model intent for {profile_name}.",
        )
        profiles.append(profile)
        saved.append(str(save_profile(root, profile)))
    intensity_path = save_intensity_config(root, IntensityModelConfig(low_api_profile=profiles[0].name, high_api_profile=profiles[1].name))
    session_path = set_session_model(root, profiles[0].name)
    print(render_cli_interaction({
        "previous_task_status": "completed",
        "previous_task_output": f"已保存 Nexus 强度分层模型。低强度 API 槽位：{profiles[0].name}；实际顺序：API {profiles[0].name} -> codex-cli gpt-5.4 -> codex-mcp。高强度 API fallback 槽位：{profiles[1].name}；实际顺序：codex-cli gpt-5.4 -> API {profiles[1].name} -> codex-mcp。密钥已导入 Nexus 本地模型配置目录，不直接读取原始 key 文件。",
        "next_task_prompt": "现在可以直接使用 $nexus-workflow；workflow 固定强度优先级，API 槽位由模型配置指令决定。",
        "artifact_refs": [*saved, str(intensity_path), str(session_path)],
    }))
    return 0


def _profile_name_for_api_model(model_name: str) -> str:
    return model_name.strip() or "api-profile"


def _infer_api_provider(model_name: str, base_url: str) -> str:
    lowered = f"{model_name} {base_url}".lower()
    for marker, provider in [
        ("dashscope", "qwen"),
        ("aliyuncs", "qwen"),
        ("qwen", "qwen"),
        ("deepseek", "deepseek"),
        ("moonshot", "kimi"),
        ("kimi", "kimi"),
        ("openai", "openai"),
        ("bigmodel", "zhipu"),
        ("zhipu", "zhipu"),
        ("minimax", "minimax"),
        ("volces", "doubao"),
        ("doubao", "doubao"),
        ("baichuan", "baichuan"),
        ("anthropic", "anthropic"),
    ]:
        if marker in lowered:
            return provider
    return model_name.split("-", 1)[0].split(".", 1)[0] or "api"


def _extract_after_marker(text: str, marker: str) -> str:
    index = text.find(marker)
    if index == -1:
        return ""
    tail = text[index + len(marker) :].strip(" ：:=，,。.\n\t")
    match = re.match(r"([^，,。;；\s]+)", tail)
    return _strip_trailing_sentence_punctuation(match.group(1).strip()) if match else ""


if __name__ == "__main__":
    raise SystemExit(main())
