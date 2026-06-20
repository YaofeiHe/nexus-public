from __future__ import annotations

from nexus.user_prompts import normalize_next_prompt


def test_nexus_next_prompt_defaults_to_codex_workflow(monkeypatch) -> None:
    monkeypatch.delenv("NEXUS_NEXT_PROMPT_MODE", raising=False)
    monkeypatch.delenv("NEXUS_WORKFLOW_SURFACE", raising=False)

    prompt = normalize_next_prompt('运行 python -m nexus.cli continue run_123 "生成项目计划"')

    assert prompt == "$nexus-workflow 对 run_123 继续：生成项目计划"
    assert "python -m nexus.cli" not in prompt


def test_nexus_next_prompt_supports_copilot_surface(monkeypatch) -> None:
    monkeypatch.setenv("NEXUS_WORKFLOW_SURFACE", "copilot")

    prompt = normalize_next_prompt("确认后运行 python -m nexus.cli approve run_123 online-search")

    assert "/nexus-workflow 审批 run_123 的 online-search" in prompt
    assert "$nexus-workflow" not in prompt


def test_nexus_cli_prompt_mode_keeps_internal_command(monkeypatch) -> None:
    monkeypatch.setenv("NEXUS_NEXT_PROMPT_MODE", "cli")
    raw = "运行 python -m nexus.cli resume run_123"

    assert normalize_next_prompt(raw) == raw


def test_nexus_manual_prompt_uses_arrow_steps(monkeypatch) -> None:
    monkeypatch.delenv("NEXUS_NEXT_PROMPT_MODE", raising=False)
    prompt = normalize_next_prompt("请按 tool_results/feishu_setup_guide.json 完成飞书开放平台配置；完成后运行 python -m nexus.cli feishu setup --project-path /tmp/demo")

    assert "飞书开放平台" in prompt
    assert "-> 应用能力 / 添加应用能力" in prompt
    assert "$nexus-workflow 初始化飞书配置" in prompt


def test_nexus_board_prompt_normalizes_to_workflow(monkeypatch) -> None:
    monkeypatch.delenv("NEXUS_NEXT_PROMPT_MODE", raising=False)
    prompt = normalize_next_prompt('运行 python -m nexus.cli board point --project-path /tmp/demo "之后要做前端"')

    assert prompt == "$nexus-workflow 为项目 /tmp/demo 记一个记录点：之后要做前端"


def test_nexus_run_prompt_with_project_path_normalizes_to_workflow(monkeypatch) -> None:
    monkeypatch.delenv("NEXUS_NEXT_PROMPT_MODE", raising=False)
    prompt = normalize_next_prompt('项目已创建；下一步可以运行 python -m nexus.cli run "调研项目初始化方案" --project-path /tmp/demo')

    assert "$nexus-workflow 调研项目 /tmp/demo：调研项目初始化方案" in prompt
    assert "python -m nexus.cli" not in prompt


def test_numbered_options_keep_choose_word(monkeypatch) -> None:
    monkeypatch.delenv("NEXUS_NEXT_PROMPT_MODE", raising=False)
    raw = "\n".join(
        [
            '选项 1：查看报告 python -m nexus.cli report run_123',
            '选项 2：选择已有轮子方案 python -m nexus.cli continue run_123 "选择已有轮子方案：<candidate-id>"',
            '选项 3：选择从零搭建方案 python -m nexus.cli continue run_123 "从零搭建方案"',
        ]
    )

    prompt = normalize_next_prompt(raw)

    assert "选择已有轮子方案 $nexus-workflow 对 run_123 继续：选择已有轮子方案：<candidate-id>" in prompt
    assert "选择从零搭建方案 $nexus-workflow 对 run_123 继续：从零搭建方案" in prompt
