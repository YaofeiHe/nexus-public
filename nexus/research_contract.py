from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


BUILD_DECISION_BRANCHES: list[dict[str, str]] = [
    {
        "id": "existing_wheel_build",
        "label": "基于已有轮子搭建",
        "report": "reports/branch_existing_wheel.md",
        "question": "是否存在可直接承载主要需求的现成项目、工具、CLI、MCP、SaaS 或库组合。",
    },
    {
        "id": "subproject_wheel_research",
        "label": "拆成多个小项目继续找轮子",
        "report": "reports/branch_subproject_wheels.md",
        "question": "如果没有完整轮子，哪些子能力应拆开继续调研现成轮子或黑盒集成。",
    },
    {
        "id": "from_scratch_build",
        "label": "从零组织项目搭建",
        "report": "reports/branch_from_scratch.md",
        "question": "如果复用证据不足，应如何组织从零搭建，同时保留可复用库/API/项目作为集成点。",
    },
]

BRANCH_LABELS = {item["id"]: item["label"] for item in BUILD_DECISION_BRANCHES}


def load_project_research_context(project: Path) -> dict[str, object]:
    project = project.expanduser().resolve()
    intent = _read_json(project / ".nexus" / "project-intent.json")
    normalized = _read_text(project / "docs" / "intent" / "normalized-requirement.md", limit=12000)
    original = _read_text(project / "docs" / "intent" / "original-requirement.md", limit=6000)
    overview = _read_text(project / "docs" / "project-overview.md", limit=4000)
    return {
        "schema": "nexus.project_research_context.v1",
        "project_path": str(project),
        "project_name": project.name,
        "project_intent": intent,
        "normalized_requirement_excerpt": normalized,
        "original_requirement_excerpt": original,
        "project_overview_excerpt": overview,
    }


def build_research_contract(
    *,
    idea: str,
    project: Path,
    repo_scan: dict[str, object],
    intent_route: dict[str, object],
    task_block: dict[str, object],
) -> dict[str, object]:
    project_context = load_project_research_context(project)
    evidence_text = _contract_evidence_text(idea, project_context, intent_route, task_block)
    mode = "build_decision" if _looks_like_build_decision_research(evidence_text, idea=idea) else "general_discovery"
    modules = _infer_subproject_modules(evidence_text)
    required_sources = _extract_required_sources(evidence_text)
    return {
        "schema": "nexus.research_contract.v1",
        "mode": mode,
        "requires_branch_reports": mode == "build_decision",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_path": str(project.expanduser().resolve()),
        "normalized_user_intent": _normalized_intent_summary(idea, project_context, task_block),
        "research_goal": _research_goal(mode, idea, task_block),
        "target_project_context": project_context,
        "intent_route_ref": {
            "resolved_route": str(intent_route.get("resolved_route") or ""),
            "project_mode": str(intent_route.get("project_mode") or ""),
            "discovery_target": str(intent_route.get("discovery_target") or ""),
            "reason": str(intent_route.get("reason") or ""),
        },
        "task_block_ref": task_block,
        "repo_scan_ref": {
            "project_path": repo_scan.get("project_path"),
            "top_dirs": repo_scan.get("top_dirs"),
            "package_files": repo_scan.get("package_files"),
            "file_sample_count": repo_scan.get("file_sample_count"),
        },
        "source_policy": {
            "required_sources_from_user": required_sources,
            "priority": [
                "用户显式给出的目录、文档、网站或平台",
                "目标项目的 .nexus/project-intent.json 与 docs/intent/*",
                "可复用现成项目、CLI、MCP、库、SaaS、官方 API/文档",
                "GitHub/Gitee/中文互联网/官方文档等真实只读来源",
            ],
            "non_default_signal": "中文公网可见性/Gitee 只在用户要求公开分发或中文平台复用时作为主评分项。",
        },
        "decision_branches": BUILD_DECISION_BRANCHES if mode == "build_decision" else [],
        "subproject_modules": modules,
        "coverage_requirements": _coverage_requirements(mode, modules),
        "non_goals": [
            "不把项目治理文档完整性误当成业务工具搭建调研。",
            "不把用户举例收缩为唯一场景。",
            "不在证据不足时包装成已有轮子可直接采用。",
            "不在未选择分支前生成代码实施计划。",
        ]
        if mode == "build_decision"
        else [
            "不扩大到代码修改或外部副作用。",
            "不把 blocked/failed source 当成已经覆盖。",
        ],
    }


def render_research_contract(contract: dict[str, object]) -> str:
    lines = [
        "# Research Contract",
        "",
        f"- schema: `{contract.get('schema', '')}`",
        f"- mode: `{contract.get('mode', '')}`",
        f"- requires_branch_reports: `{contract.get('requires_branch_reports', False)}`",
        f"- project_path: `{contract.get('project_path', '')}`",
        "",
        "## Normalized User Intent",
        str(contract.get("normalized_user_intent") or ""),
        "",
        "## Research Goal",
        str(contract.get("research_goal") or ""),
        "",
        "## Decision Branches",
    ]
    for branch in contract.get("decision_branches", []) if isinstance(contract.get("decision_branches"), list) else []:
        if isinstance(branch, dict):
            lines.append(f"- `{branch.get('id')}` {branch.get('label')}: {branch.get('question')}")
    if not isinstance(contract.get("decision_branches"), list) or not contract.get("decision_branches"):
        lines.append("- 本轮不是搭建决策型调研，不要求三分支报告。")
    lines.extend(["", "## Subproject Modules"])
    lines.extend(f"- {item}" for item in contract.get("subproject_modules", []) if isinstance(item, str))
    lines.extend(["", "## Coverage Requirements"])
    lines.extend(f"- {item}" for item in contract.get("coverage_requirements", []) if isinstance(item, str))
    lines.extend(["", "## Non Goals"])
    lines.extend(f"- {item}" for item in contract.get("non_goals", []) if isinstance(item, str))
    return "\n".join(lines).rstrip() + "\n"


def build_branch_research_artifacts(
    *,
    contract: dict[str, object],
    ranked: list[dict[str, object]],
    final_report: dict[str, object],
    risk_analysis: dict[str, object],
    source_statuses: list[dict[str, object]],
) -> dict[str, object]:
    if not bool(contract.get("requires_branch_reports")):
        return {"schema": "nexus.branch_research_artifacts.v1", "enabled": False}
    candidate_evidence = _candidate_evidence(ranked)
    source_weaknesses = _source_weaknesses(source_statuses)
    reports = {
        "existing_wheel_build": _existing_wheel_report(contract, ranked, candidate_evidence, source_weaknesses),
        "subproject_wheel_research": _subproject_wheel_report(contract, ranked, candidate_evidence, source_weaknesses),
        "from_scratch_build": _from_scratch_report(contract, ranked, final_report, risk_analysis, source_weaknesses),
    }
    matrix = _decision_matrix(contract, ranked, reports, source_weaknesses)
    return {
        "schema": "nexus.branch_research_artifacts.v1",
        "enabled": True,
        "reports": reports,
        "decision_matrix": matrix,
    }


def render_branch_report(report: dict[str, object]) -> str:
    lines = [
        f"# {report.get('title', 'Branch Report')}",
        "",
        f"- branch_id: `{report.get('branch_id', '')}`",
        f"- evidence_level: `{report.get('evidence_level', '')}`",
        f"- implementation_ready: `{report.get('implementation_ready', False)}`",
        "",
        "## Conclusion",
        str(report.get("conclusion") or ""),
        "",
        "## Evidence",
    ]
    lines.extend(f"- {item}" for item in report.get("evidence", []) if isinstance(item, str))
    lines.extend(["", "## Gaps"])
    lines.extend(f"- {item}" for item in report.get("gaps", []) if isinstance(item, str))
    lines.extend(["", "## Next Workflow Prompt"])
    lines.append(str(report.get("next_workflow_prompt") or ""))
    return "\n".join(lines).rstrip() + "\n"


def render_decision_matrix(matrix: dict[str, object]) -> str:
    lines = [
        "# Research Decision Matrix",
        "",
        f"- recommended_branch: `{matrix.get('recommended_branch', '')}`",
        f"- branch_selection_required: `{matrix.get('branch_selection_required', True)}`",
        "",
        "## Branches",
    ]
    for item in matrix.get("branches", []) if isinstance(matrix.get("branches"), list) else []:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('branch_id')}` score={item.get('score')}: {item.get('rationale')}")
    lines.extend(["", "## Blocking Conditions"])
    lines.extend(f"- {item}" for item in matrix.get("blocking_conditions", []) if isinstance(item, str))
    return "\n".join(lines).rstrip() + "\n"


def branch_id_from_route(route: str, scope: str = "", user_request: str = "") -> str:
    text = f"{route} {scope} {user_request}".lower()
    if route == "select_subproject_wheels":
        return "subproject_wheel_research"
    if route == "select_existing_wheel":
        return "existing_wheel_build"
    if route == "select_from_scratch_build":
        return "from_scratch_build"
    if "subproject" in text or "分块" in text or "拆" in text or "多个小项目" in text:
        return "subproject_wheel_research"
    if "existing_wheel" in text or "已有轮子" in text or "现成轮子" in text or "已有实现" in text:
        return "existing_wheel_build"
    if "from_scratch" in text or "从零" in text or "重新搭建" in text or "直接搭建" in text:
        return "from_scratch_build"
    return ""


def selected_branch_is_valid(branch_id: str) -> bool:
    return branch_id in BRANCH_LABELS


def _contract_evidence_text(
    idea: str,
    project_context: dict[str, object],
    intent_route: dict[str, object],
    task_block: dict[str, object],
) -> str:
    parts = [
        idea,
        json.dumps(intent_route, ensure_ascii=False),
        json.dumps(task_block, ensure_ascii=False),
        str(project_context.get("normalized_requirement_excerpt") or ""),
        str(project_context.get("original_requirement_excerpt") or ""),
        str(project_context.get("project_overview_excerpt") or ""),
        json.dumps(project_context.get("project_intent") or {}, ensure_ascii=False),
    ]
    return "\n".join(parts)


def _looks_like_build_decision_research(text: str, *, idea: str) -> bool:
    lowered = text.lower()
    business_markers = [
        "体系化",
        "求职工具",
        "简历",
        "面试",
        "基础知识",
        "补习",
        "训练用小项目",
        "项目搭建",
        "互通",
        "动态",
        "agent workflow",
        "workflow kernel",
        "platform",
        "工具",
    ]
    decision_markers = [
        "轮子",
        "已有实现",
        "现成",
        "搭建",
        "初始化方案",
        "从零",
        "复用",
        "黑盒集成",
        "项目计划",
        "架构",
    ]
    governance_only = ["治理文档", "同步指南", "operation-guide", "github-sync-guide", "飞书记录", "公开可见性"]
    if any(marker in idea for marker in ["治理文档", "同步指南", "公开可见性"]) and not any(marker in text for marker in business_markers[:8]):
        return False
    return any(marker in text for marker in business_markers) and any(marker in text for marker in decision_markers) and not (
        all(marker in lowered for marker in ["readme", "github"]) and not any(marker in text for marker in business_markers[:8])
    )


def _infer_subproject_modules(text: str) -> list[str]:
    checks = [
        ("resume_revision", ["简历", "resume"]),
        ("interview_assistant", ["面试", "interview", "项目陈述", "问答"]),
        ("interview_result_recording", ["面试结果", "记录", "反馈"]),
        ("knowledge_remediation", ["基础知识", "补习", "知识点", "能力"]),
        ("practice_project_generator", ["训练用小项目", "项目练习", "prompt直接搭建", "小项目"]),
        ("project_context_access", ["项目目录", "forge", "ifome", "随机森林", "数值模拟"]),
        ("online_search_interface", ["在线搜索", "联网", "搜索接口"]),
        ("dynamic_feedback_loop", ["互通", "动态", "迭代", "反应在", "反馈闭环"]),
    ]
    modules = [module for module, markers in checks if any(marker in text for marker in markers)]
    return modules or ["core_workflow", "source_ingestion", "decision_report", "implementation_planning"]


def _extract_required_sources(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s，。；)）]+", text)
    paths = re.findall(r"/Users/[^\s，。；)）]+", text)
    result: list[str] = []
    for item in [*urls, *paths]:
        if item not in result:
            result.append(item)
    return result[:20]


def _normalized_intent_summary(idea: str, project_context: dict[str, object], task_block: dict[str, object]) -> str:
    normalized = str(project_context.get("normalized_requirement_excerpt") or "").strip()
    if normalized:
        return _compact_text(normalized, limit=900)
    goal = str(task_block.get("goal") or "").strip()
    return goal or idea


def _research_goal(mode: str, idea: str, task_block: dict[str, object]) -> str:
    if mode == "build_decision":
        return "围绕用户真实产品意图做搭建方案调研：先找完整现成轮子，再拆子能力继续找轮子，最后给出从零搭建方案和下一步工作流指令。"
    return str(task_block.get("goal") or idea or "完成只读 discovery。")


def _coverage_requirements(mode: str, modules: list[str]) -> list[str]:
    if mode != "build_decision":
        return ["覆盖用户指定范围", "记录 source_status", "输出报告和下一步选项"]
    return [
        "必须判断是否有完整现成轮子可以作为基础。",
        "必须为没有完整轮子的情况拆分子模块继续调研。",
        "必须为从零搭建给出组织方式、边界和集成点。",
        "三份分支报告必须都落盘并由 final_report/next_options 引用。",
        "未选择分支前不得进入 implementation_plan。",
        "子模块覆盖：" + ", ".join(modules),
    ]


def _candidate_evidence(ranked: list[dict[str, object]]) -> list[str]:
    evidence: list[str] = []
    for item in ranked[:8]:
        title = str(item.get("title") or item.get("id") or "candidate")
        score = item.get("score", "")
        source = str(item.get("source") or "")
        url = str(item.get("url") or "")
        reason = str(item.get("reason") or item.get("summary") or "")
        line = f"{title} source={source} score={score}"
        if url:
            line += f" url={url}"
        if reason:
            line += f" - {_compact_text(reason, limit=220)}"
        evidence.append(line)
    return evidence


def _source_weaknesses(statuses: list[dict[str, object]]) -> list[str]:
    weak: list[str] = []
    for status in statuses:
        state = str(status.get("status") or "")
        if state in {"blocked", "approval_required", "auth_required", "rate_limited", "failed", "partial", "skipped"}:
            weak.append(f"{status.get('source')}: {state}/{status.get('issue_type')} - {status.get('reason')}")
    return weak[:10]


def _existing_wheel_report(
    contract: dict[str, object],
    ranked: list[dict[str, object]],
    candidate_evidence: list[str],
    source_weaknesses: list[str],
) -> dict[str, object]:
    direct_candidates = [
        item
        for item in ranked
        if float(item.get("score") or 0) >= 0.82
        or str(item.get("recommended_use") or "").lower() in {"adopt", "direct", "base", "primary"}
    ]
    evidence_level = "strong" if direct_candidates else ("partial" if ranked else "insufficient")
    return {
        "schema": "nexus.branch_report.v1",
        "branch_id": "existing_wheel_build",
        "title": "Branch Report: 基于已有轮子搭建",
        "evidence_level": evidence_level,
        "implementation_ready": bool(direct_candidates),
        "conclusion": "存在可优先验证的完整候选，适合先做黑盒集成/二次开发评估。"
        if direct_candidates
        else "当前证据不足以证明存在能完整承载用户意图的现成轮子，不能直接进入基于单一轮子的搭建。",
        "evidence": candidate_evidence or ["未检索到可直接作为完整轮子的候选。"],
        "gaps": source_weaknesses
        or [
            "需要继续确认许可证、维护状态、数据/隐私边界和与目标项目的输入输出契约。",
            "需要端到端验证是否覆盖 normalized_user_intent 中的动态反馈闭环。",
        ],
        "next_workflow_prompt": "$nexus-workflow 对 <run_id> 继续：选择已有轮子方案：<candidate-id>",
        "contract_mode": contract.get("mode"),
    }


def _subproject_wheel_report(
    contract: dict[str, object],
    ranked: list[dict[str, object]],
    candidate_evidence: list[str],
    source_weaknesses: list[str],
) -> dict[str, object]:
    modules = [str(item) for item in contract.get("subproject_modules", []) if isinstance(item, str)]
    evidence = [f"{module}: 需要单独调研可复用项目/API/库/MCP，并定义输入输出契约。" for module in modules]
    evidence.extend(candidate_evidence[:5])
    return {
        "schema": "nexus.branch_report.v1",
        "branch_id": "subproject_wheel_research",
        "title": "Branch Report: 拆成多个小项目继续找轮子",
        "evidence_level": "partial" if ranked else "planning_needed",
        "implementation_ready": False,
        "conclusion": "如果没有完整轮子，应按能力模块继续调研；该分支是继续 discovery 的入口，不应被当成已完成实施计划。",
        "evidence": evidence,
        "gaps": source_weaknesses
        or [
            "每个模块还需要独立候选、许可证、接口契约、端到端验收方式。",
            "需要确认模块之间的反馈闭环，而不是只做彼此孤立的小工具。",
        ],
        "next_workflow_prompt": "$nexus-workflow 对 <run_id> 继续：分块调研：<module list>",
        "contract_mode": contract.get("mode"),
    }


def _from_scratch_report(
    contract: dict[str, object],
    ranked: list[dict[str, object]],
    final_report: dict[str, object],
    risk_analysis: dict[str, object],
    source_weaknesses: list[str],
) -> dict[str, object]:
    modules = [str(item) for item in contract.get("subproject_modules", []) if isinstance(item, str)]
    evidence = [
        "从零搭建仍应优先黑盒集成成熟库/API/CLI/MCP，不手写已有成熟能力。",
        "架构应以统一状态模型连接简历、面试、反馈、知识补习和训练项目。",
        "所有外部副作用继续走 approval/artifact/audit 链路。",
    ]
    evidence.extend(f"模块：{module}" for module in modules)
    evidence.extend(str(item) for item in final_report.get("findings", []) if isinstance(item, str))
    return {
        "schema": "nexus.branch_report.v1",
        "branch_id": "from_scratch_build",
        "title": "Branch Report: 从零组织项目搭建",
        "evidence_level": "available",
        "implementation_ready": True,
        "conclusion": "当完整轮子和子模块轮子证据不足时，可以进入从零搭建方案，但实施计划必须显式保留可复用集成点和端到端验收。",
        "evidence": evidence[:16],
        "gaps": source_weaknesses
        or [str(item) for item in risk_analysis.get("risks", []) if isinstance(item, str)]
        or ["需要在 implementation_plan 中把外部工具集成、状态闭环和测试验收展开。"],
        "next_workflow_prompt": "$nexus-workflow 对 <run_id> 继续：从零搭建方案",
        "contract_mode": contract.get("mode"),
    }


def _decision_matrix(
    contract: dict[str, object],
    ranked: list[dict[str, object]],
    reports: dict[str, dict[str, object]],
    source_weaknesses: list[str],
) -> dict[str, object]:
    scores = {
        "existing_wheel_build": 80 if reports["existing_wheel_build"].get("implementation_ready") else (45 if ranked else 20),
        "subproject_wheel_research": 70 if ranked else 55,
        "from_scratch_build": 65,
    }
    recommended = max(scores, key=scores.get)
    branches = [
        {
            "branch_id": branch_id,
            "label": BRANCH_LABELS[branch_id],
            "score": score,
            "report_ref": next(item["report"] for item in BUILD_DECISION_BRANCHES if item["id"] == branch_id),
            "rationale": str(reports[branch_id].get("conclusion") or ""),
        }
        for branch_id, score in scores.items()
    ]
    return {
        "schema": "nexus.research_decision_matrix.v1",
        "mode": contract.get("mode"),
        "recommended_branch": recommended,
        "branch_selection_required": True,
        "branches": branches,
        "blocking_conditions": [
            "用户或后续 workflow 尚未选择 research branch，因此不得直接进入 implementation_plan。",
            *source_weaknesses,
        ][:12],
    }


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_text(path: Path, *, limit: int) -> str:
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except OSError:
        return ""


def _compact_text(text: str, *, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."
