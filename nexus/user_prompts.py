from __future__ import annotations

import os
import re


def cli_prompt_mode() -> bool:
    value = os.environ.get("NEXUS_NEXT_PROMPT_MODE") or os.environ.get("WORKFLOW_NEXT_PROMPT_MODE") or ""
    return value.strip().lower() in {"cli", "command", "commands", "1", "true", "yes"}


def workflow_prefix() -> str:
    surface = os.environ.get("NEXUS_WORKFLOW_SURFACE") or os.environ.get("WORKFLOW_SURFACE") or ""
    if surface.strip().lower() in {"copilot", "slash", "/"}:
        return "/nexus-workflow"
    return "$nexus-workflow"


def normalize_next_prompt(prompt: object) -> str:
    text = str(prompt or "").strip()
    if not text or cli_prompt_mode():
        return text
    text = _normalize_existing_skill_prompt(text)
    text = _manual_prompt(text)
    text = _normalize_numbered_options(text)
    text = _replace_cli_commands(text)
    text = _cleanup(text)
    return text


def workflow_prompt(intent: str) -> str:
    return f"{workflow_prefix()} {intent}".strip()


def _normalize_existing_skill_prompt(text: str) -> str:
    prefix = workflow_prefix()
    text = re.sub(r"使用\s+\$nexus-workflow", prefix, text)
    text = re.sub(r"使用\s+/nexus-workflow", prefix, text)
    text = text.replace("$nexus-workflow", prefix).replace("/nexus-workflow", prefix)
    return text


def _manual_prompt(text: str) -> str:
    if "tool_results/feishu_setup_guide.json" in text or ("飞书开放平台" in text and "配置" in text):
        project = "<target-project>"
        match = re.search(r"--project-path\s+([^\s，。；]+)", text)
        if match:
            project = match.group(1)
        return "\n".join(
            [
                "飞书开放平台",
                "-> 你的企业自建应用",
                "-> 应用能力 / 添加应用能力",
                "-> 确认已经添加「机器人」",
                "-> 权限管理",
                "-> 开通 docx/drive 相关权限",
                "-> 应用发布",
                "-> 版本管理与发布",
                "-> 确认已发布",
                "",
                "完成后输入：",
                workflow_prompt(f"初始化飞书配置，项目路径 {project}，并提供 app_id/app_secret/folder_token 文件路径"),
            ]
        )
    if "gh auth login" in text:
        return "\n".join(
            [
                "GitHub CLI",
                "-> 打开终端",
                "-> 运行 gh auth login",
                "-> 按提示选择 GitHub.com 和目标认证方式",
                "-> 确认目标 private/public repo 权限可用",
                "",
                "完成后输入：",
                workflow_prompt("重新执行上一步 GitHub 同步"),
            ]
        )
    return text


def _normalize_numbered_options(text: str) -> str:
    if "选项 " not in text:
        return text
    matches = list(re.finditer(r"选项\s*(\d+)[:：]", text))
    if not matches:
        return text
    lines = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        number = match.group(1)
        body = text[match.end() : next_start]
        converted = _replace_cli_commands(body.strip(" ，。；"))
        lines.append(f"{number}. {converted}")
    return "\n".join(lines)


def _replace_cli_commands(text: str) -> str:
    replacements = [
        (r"python -m nexus\.cli approve ([^\s，。；]+) ([^\s，。；]+)", lambda m: workflow_prompt(f"审批 {m.group(1)} 的 {m.group(2)}")),
        (r"python -m nexus\.cli resume ([^\s，。；]+)", lambda m: workflow_prompt(f"继续 {m.group(1)}")),
        (r"python -m nexus\.cli continue ([^\s，。；]+) \"([^\"]+)\"", lambda m: workflow_prompt(f"对 {m.group(1)} 继续：{m.group(2)}")),
        (r"python -m nexus\.cli recover ([^\s，。；]+) \"([^\"]+)\"", lambda m: workflow_prompt(f"{m.group(2)} {m.group(1)}")),
        (r"python -m nexus\.cli continue-after-input ([^\s，。；]+)[^\n。；]*", lambda m: workflow_prompt(f"继续 {m.group(1)} 的外部输入恢复")),
        (r"python -m nexus\.cli report ([^\s，。；]+)", lambda m: workflow_prompt(f"查看 {m.group(1)} 的报告")),
        (r"python -m nexus\.cli status ([^\s，。；]+)", lambda m: workflow_prompt(f"查看 {m.group(1)} 的状态")),
        (r"python -m nexus\.cli model status", lambda m: workflow_prompt("查看当前可用的基座模型和 provider 状态")),
        (r"python -m nexus\.cli model list", lambda m: workflow_prompt("查看当前可用的基座模型和 provider 状态")),
        (r"python -m nexus\.cli model configure[^\n，。；]*", lambda m: workflow_prompt("配置模型 provider")),
        (r"python -m nexus\.cli model set ([^\s，。；/]+)", lambda m: workflow_prompt(f"使用 {m.group(1)} 模型")),
        (r"python -m nexus\.cli run \"([^\"]+)\" --project-path ([^\s，。；]+)", lambda m: workflow_prompt(f"调研项目 {m.group(2)}：{m.group(1)}")),
        (r"python -m nexus\.cli research \"<需求>\" --model ([^\s，。；]+)", lambda m: workflow_prompt(f"调研某个具体项目，基座模型使用 {m.group(1)}")),
        (r"python -m nexus\.cli research \"([^\"]+)\"[^\n，。；]*", lambda m: workflow_prompt(f"调研当前项目：{m.group(1)}")),
        (r"python -m nexus\.cli init-project[^\n，。；]*", lambda m: workflow_prompt("初始化项目：<项目 idea>")),
        (r"python -m nexus\.cli prepare-project ([^\s，。；]+)", lambda m: workflow_prompt(f"将项目 {m.group(1)} 纳入 git 管理并建立 baseline")),
        (r"python -m nexus\.cli execute-code-change ([^\s，。；]+)[^\n，。；]*", lambda m: workflow_prompt(f"执行 {m.group(1)} 的 code-change")),
        (r"python -m nexus\.cli diff ([^\s，。；]+)", lambda m: workflow_prompt(f"查看 {m.group(1)} 的 diff")),
        (r"python -m nexus\.cli apply ([^\s，。；]+)", lambda m: workflow_prompt(f"应用 {m.group(1)} 的 patch")),
        (r"python -m nexus\.cli test ([^\s，。；]+)[^\n。；]*", lambda m: workflow_prompt(f"运行 {m.group(1)} 的测试")),
        (r"python -m nexus\.cli board show --project-path ([^\s，。；]+)", lambda m: workflow_prompt(f"查看项目 {m.group(1)} 的记录板")),
        (r"python -m nexus\.cli board show", lambda m: workflow_prompt("查看记录板")),
        (r"python -m nexus\.cli board update --project-path ([^\s，。；]+) --status \"([^\"]+)\"", lambda m: workflow_prompt(f"更新项目 {m.group(1)} 的记录板当前状态：{m.group(2)}")),
        (r"python -m nexus\.cli board update --status \"([^\"]+)\"", lambda m: workflow_prompt(f"更新记录板当前状态：{m.group(1)}")),
        (r"python -m nexus\.cli board point --project-path ([^\s，。；]+) \"([^\"]+)\"", lambda m: workflow_prompt(f"为项目 {m.group(1)} 记一个记录点：{m.group(2)}")),
        (r"python -m nexus\.cli board point \"([^\"]+)\"", lambda m: workflow_prompt(f"记一个记录点：{m.group(1)}")),
        (r"python -m nexus\.cli feishu setup --project-path ([^\s，。；]+)[^\n。；]*", lambda m: workflow_prompt(f"初始化飞书配置，项目路径 {m.group(1)}")),
        (r"python -m nexus\.cli feishu doctor --project-path ([^\s，。；]+)", lambda m: workflow_prompt(f"诊断飞书配置，项目路径 {m.group(1)}")),
        (r"python -m nexus\.cli feishu record --project-path ([^\s，。；]+)[^\n。；]*", lambda m: workflow_prompt(f"进行飞书记录，项目路径 {m.group(1)}：<记录内容>")),
        (r"python -m nexus\.cli system-showcase publish-feishu --project-path ([^\s，。；]+)[^\n。；]*", lambda m: workflow_prompt(f"确认发布项目 {m.group(1)} 的系统架构到飞书")),
        (r"python -m nexus\.cli system-showcase generate --project-path ([^\s，。；]+)", lambda m: workflow_prompt(f"为项目 {m.group(1)} 生成系统架构展示")),
        (r"python -m nexus\.cli github-sync configure[^\n。；]*", lambda m: workflow_prompt("配置 GitHub private/public 同步目标")),
        (r"python -m nexus\.cli github-sync private --project-path ([^\s，。；]+)", lambda m: workflow_prompt(f"将项目 {m.group(1)} 同步到 GitHub private")),
        (r"python -m nexus\.cli github-sync public --project-path ([^\s，。；]+)[^\n。；]*", lambda m: workflow_prompt(f"确认将项目 {m.group(1)} 同步到 GitHub public")),
        (r"python -m nexus\.cli conversation sessions", lambda m: workflow_prompt("查看可用对话 session")),
        (r"python -m nexus\.cli conversation-from-file ([^\s，。；]+)", lambda m: workflow_prompt(f"将对话文件 {m.group(1)} 整理成 skill/workflow")),
        (r"python -m nexus\.cli conversation-to-workflow --current", lambda m: workflow_prompt("将本次对话整理成 skill/workflow")),
        (r"python -m nexus\.cli install-generated-skill ([^\s，。；]+) --confirm", lambda m: workflow_prompt(f"确认安装 {m.group(1)} 生成的 skill")),
        (r"python -m nexus\.cli doctor", lambda m: workflow_prompt("检查当前 nexus workflow 是否安装并可用")),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)
    text = text.replace("运行 ", "输入 ")
    text = text.replace("确认后输入 输入", "确认后输入")
    return text


def _cleanup(text: str) -> str:
    text = re.sub(r"输入\s*：\s*", "输入：", text)
    text = re.sub(r"^输入\s+((?:\$|/)nexus-workflow)", r"\1", text)
    text = re.sub(r"^使用\s+((?:\$|/)nexus-workflow)", r"\1", text)
    text = re.sub(r"([，。；]\s*)使用\s+((?:\$|/)nexus-workflow)", r"\1\2", text)
    text = text.replace("。。", "。")
    return text.strip()
