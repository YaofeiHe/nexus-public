from __future__ import annotations

from pathlib import Path
import json


SYSTEM_DIR = Path("docs/system")
CONTEXT_FILE_CANDIDATES = [
    "docs/intent/normalized-requirement.md",
    "docs/intent/original-requirement.md",
    "docs/project-overview.md",
    "docs/operation-guide.md",
    "docs/feishu-records.md",
    ".nexus/project-intent.json",
    ".nexus/board.md",
    "README.md",
]
PROJECT_DOC_MARKERS = {
    "docs/intent/normalized-requirement.md",
    "docs/project-overview.md",
    "docs/operation-guide.md",
    ".nexus/project-intent.json",
}
MAX_CONTEXT_CHARS_PER_FILE = 8000


def generate_showcase(project: Path, repo_scan: dict[str, object], model_graph: dict[str, object] | None = None) -> dict[str, object]:
    root = project / SYSTEM_DIR
    root.mkdir(parents=True, exist_ok=True)
    context = _load_project_context(project)
    graph = model_graph or _fallback_graph(repo_scan, context=context)
    graph.setdefault("project_name", project.name)
    architecture_json = root / "architecture.json"
    architecture_md = root / "architecture.md"
    architecture_mmd = root / "architecture.mmd"
    modules_yaml = root / "modules.yaml"
    architecture_json.write_text(json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    architecture_md.write_text(_render_markdown(graph), encoding="utf-8")
    architecture_mmd.write_text(_render_mermaid(graph), encoding="utf-8")
    modules_yaml.write_text(_render_modules_yaml(graph), encoding="utf-8")
    return {
        "schema": "nexus.system_showcase.v1",
        "status": "completed",
        "architecture_json": str(architecture_json),
        "architecture_md": str(architecture_md),
        "architecture_mmd": str(architecture_mmd),
        "modules_yaml": str(modules_yaml),
    }


def explain_node(project: Path, node_id: str) -> dict[str, object]:
    graph_path = project / SYSTEM_DIR / "architecture.json"
    if not graph_path.exists():
        return {"schema": "nexus.system_node_explain.v1", "status": "blocked", "reason": "architecture_not_generated"}
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    for node in graph.get("nodes", []):
        if isinstance(node, dict) and str(node.get("id")) == node_id:
            return {"schema": "nexus.system_node_explain.v1", "status": "completed", "node": node}
    return {"schema": "nexus.system_node_explain.v1", "status": "blocked", "reason": "node_not_found", "node_id": node_id}


def _load_project_context(project: Path) -> dict[str, object]:
    documents: dict[str, str] = {}
    for rel in CONTEXT_FILE_CANDIDATES:
        path = project / rel
        if not path.is_file():
            continue
        try:
            documents[rel] = path.read_text(encoding="utf-8", errors="replace")[:MAX_CONTEXT_CHARS_PER_FILE]
        except OSError:
            continue
    return {
        "project_name": project.name,
        "documents": documents,
        "text": "\n\n".join(documents.values()),
        "source_files": list(documents),
    }


def _fallback_graph(repo_scan: dict[str, object], *, context: dict[str, object] | None = None) -> dict[str, object]:
    context = context or {}
    if _has_project_docs(context):
        return _contextual_project_graph(repo_scan, context)

    packages = _list_value(repo_scan, "package_files") + _list_value(repo_scan, "top_dirs")
    files = _list_value(repo_scan, "file_samples")
    nodes = [
        {"id": "entry", "label": "入口层", "one_liner_zh": "接收一句话请求并路由到对应 workflow。", "details": "CLI、skill 和 invoke 入口。"},
        {"id": "runner", "label": "编排层", "one_liner_zh": "管理状态、审批、artifact 和模型节点。", "details": "负责端到端 workflow。"},
        {"id": "tools", "label": "工具层", "one_liner_zh": "执行文件、检索、GitHub、飞书等机械操作。", "details": "不做模型判断。"},
    ]
    for idx, package in enumerate(packages[:8]):
        nodes.append({"id": f"pkg_{idx}", "label": str(package), "one_liner_zh": f"{package} 模块。", "details": f"由 repo scan 发现：{package}"})
    return {
        "schema": "nexus.system_architecture.v1",
        "summary": "系统架构由 nexus repo scan 和模型/工具节点生成。",
        "nodes": nodes,
        "edges": [
            {"from": "entry", "to": "runner", "label": "dispatch"},
            {"from": "runner", "to": "tools", "label": "execute"},
        ],
        "source_files": files[:20],
    }


def _has_project_docs(context: dict[str, object]) -> bool:
    documents = context.get("documents")
    if not isinstance(documents, dict):
        return False
    return any(rel in documents for rel in PROJECT_DOC_MARKERS)


def _contextual_project_graph(repo_scan: dict[str, object], context: dict[str, object]) -> dict[str, object]:
    text = str(context.get("text") or "")
    project_name = str(context.get("project_name") or "project")
    source_files = [str(path) for path in context.get("source_files", []) if isinstance(path, str)]
    job_domain = _contains_any(text, ["求职", "网申", "岗位", "简历", "投递", "招聘", "ATS"])
    nodes = _job_workflow_nodes(project_name) if job_domain else _generic_project_nodes(project_name)
    nodes.extend(_shared_governance_nodes(project_name))
    edges = _job_workflow_edges() if job_domain else _generic_project_edges()
    edge_set = {(str(edge.get("from")), str(edge.get("to")), str(edge.get("label"))) for edge in edges}
    for edge in _shared_governance_edges(job_domain):
        key = (str(edge.get("from")), str(edge.get("to")), str(edge.get("label")))
        if key not in edge_set:
            edges.append(edge)
            edge_set.add(key)
    return {
        "schema": "nexus.system_architecture.v1",
        "project_name": project_name,
        "summary": f"{project_name} 系统架构展示：基于项目意图、长期文档和 repo scan 生成，覆盖领域 workflow、schema/index、审批门禁、审计 artifact 与同步边界。",
        "domain": "chinese_job_application_workflow" if job_domain else "documented_project_workflow",
        "nodes": nodes,
        "edges": edges,
        "boundaries": _safety_boundaries(text, job_domain),
        "source_files": _dedupe(source_files + _safe_source_files(_list_value(repo_scan, "file_samples")))[:40],
        "repo_scan": {
            "top_dirs": [item for item in _list_value(repo_scan, "top_dirs") if item != ".git"],
            "package_files": _safe_source_files(_list_value(repo_scan, "package_files")),
            "skipped_secret_like": _list_value(repo_scan, "skipped_secret_like"),
        },
    }


def _job_workflow_nodes(project_name: str) -> list[dict[str, object]]:
    return [
        {
            "id": "intent_source",
            "label": "意图与需求源",
            "one_liner_zh": f"沉淀 {project_name} 的原始需求、规范化需求和历史参考边界。",
            "details": "读取 docs/intent 与项目概览，明确 wljob 是通用中文互联网求职/网申 workflow kernel，而不是单次求职脚本。",
        },
        {
            "id": "intake",
            "label": "求职意图 Intake",
            "one_liner_zh": "解析用户的求职方向、公司/岗位/链接输入、地点和偏好约束。",
            "details": "输出可审计的 intent artifact，作为后续路线选择和岗位整理的输入。",
        },
        {
            "id": "source_planning",
            "label": "岗位来源规划",
            "one_liner_zh": "在官网招聘、官方 ATS、招聘平台、用户链接和本地数据之间选择受控路线。",
            "details": "只生成来源计划和只读检索边界；没有真实来源或审批时不得编造岗位 URL。",
        },
        {
            "id": "job_cards",
            "label": "岗位卡片与索引",
            "one_liner_zh": "把已批准来源整理为岗位卡片、状态索引和可比较表格。",
            "details": "字段包括公司、岗位、地点、薪资、技能、链接、申请状态、风险提示、证据来源和更新时间。",
        },
        {
            "id": "scoring",
            "label": "解释性评分排序",
            "one_liner_zh": "结合默认画像与用户偏好，对岗位匹配度做可解释排序。",
            "details": "面向后端、工程型算法、数据分析、AI 工程化、AI Infra、数据评测和智能驾驶等方向。",
        },
        {
            "id": "proposal_gate",
            "label": "Proposal 与审批门禁",
            "one_liner_zh": "在真实浏览器、外部写入、附件上传、投递或沟通前生成审批卡。",
            "details": "敏感动作必须等待显式确认；审批卡和 decision artifact 进入审计链路。",
        },
        {
            "id": "execution_guard",
            "label": "安全执行边界",
            "one_liner_zh": "遇到登录、验证码、风控、凭据请求或敏感页面时停止并写 blocked artifact。",
            "details": "不读取 cookie/token/browser profile/SSH key/.env/密码，不绕过 CAPTCHA/403/WAF，不自动提交网申或发送消息。",
        },
    ]


def _generic_project_nodes(project_name: str) -> list[dict[str, object]]:
    return [
        {
            "id": "intent_source",
            "label": "意图与需求源",
            "one_liner_zh": f"沉淀 {project_name} 的原始需求、规范化需求和项目边界。",
            "details": "以 docs/intent、project-overview 和 operation-guide 作为长期项目说明来源。",
        },
        {
            "id": "workflow_kernel",
            "label": "Workflow Kernel",
            "one_liner_zh": "把自然语言请求转成可审计的本地 workflow、artifact 和下一步提示。",
            "details": "模型负责判断和规划，本地工具负责受控执行、状态记录和边界检查。",
        },
        {
            "id": "schema_index",
            "label": "Schema / Index",
            "one_liner_zh": "维护结构化数据、索引、运行状态和可追溯记录。",
            "details": "为项目数据、操作记录、审批状态和输出文档提供稳定契约。",
        },
        {
            "id": "proposal_gate",
            "label": "Proposal 与审批门禁",
            "one_liner_zh": "对外部副作用、发布、同步或敏感动作进行显式审批。",
            "details": "审批结果进入 artifacts，后续恢复和审计可复核。",
        },
        {
            "id": "execution_guard",
            "label": "安全执行边界",
            "one_liner_zh": "缺权限、缺配置或触发敏感边界时停止并输出 blocked。",
            "details": "不把不可执行步骤包装成已完成结果。",
        },
    ]


def _shared_governance_nodes(project_name: str) -> list[dict[str, object]]:
    return [
        {
            "id": "artifacts",
            "label": "状态与审计 Artifacts",
            "one_liner_zh": "统一记录 state、interaction、proposal、approval、audit 和 blocked 结果。",
            "details": "用于恢复回绑、Verix 审计、人工复核和后续前端展示。",
        },
        {
            "id": "records_sync",
            "label": "项目记录与同步",
            "one_liner_zh": "维护项目说明、操作指南、飞书记录、GitHub private/public 同步边界和记录板。",
            "details": "默认 GitHub private 与飞书长期文档同步；public 发布必须先做 secret/private metadata scan 并显式确认。",
        },
        {
            "id": "operation_surface",
            "label": "操作入口与前端展示",
            "one_liner_zh": f"为 {project_name} 提供 CLI/Codex workflow 入口，并为后续前端实现暴露稳定结构。",
            "details": "architecture.json、architecture.mmd 和 modules.yaml 可作为系统架构展示、文档同步和前端渲染输入。",
        },
    ]


def _job_workflow_edges() -> list[dict[str, str]]:
    return [
        {"from": "intent_source", "to": "intake", "label": "scope"},
        {"from": "intake", "to": "source_planning", "label": "route selection"},
        {"from": "source_planning", "to": "job_cards", "label": "approved readonly sources"},
        {"from": "job_cards", "to": "scoring", "label": "normalize + compare"},
        {"from": "scoring", "to": "proposal_gate", "label": "next action proposal"},
        {"from": "proposal_gate", "to": "execution_guard", "label": "sensitive action check"},
    ]


def _generic_project_edges() -> list[dict[str, str]]:
    return [
        {"from": "intent_source", "to": "workflow_kernel", "label": "scope"},
        {"from": "workflow_kernel", "to": "schema_index", "label": "structured state"},
        {"from": "schema_index", "to": "proposal_gate", "label": "candidate action"},
        {"from": "proposal_gate", "to": "execution_guard", "label": "permission check"},
    ]


def _shared_governance_edges(job_domain: bool) -> list[dict[str, str]]:
    upstream = "execution_guard"
    if not job_domain:
        upstream = "schema_index"
    return [
        {"from": "execution_guard", "to": "artifacts", "label": "write audit trail"},
        {"from": upstream, "to": "records_sync", "label": "document result"},
        {"from": "artifacts", "to": "operation_surface", "label": "renderable state"},
        {"from": "records_sync", "to": "operation_surface", "label": "docs + board"},
    ]


def _safety_boundaries(text: str, job_domain: bool) -> list[str]:
    defaults = [
        "不读取 cookie、token、浏览器 profile、SSH key、.env、密码文件或其他凭据。",
        "不绕过登录、CAPTCHA、403、WAF、平台风控或反爬系统。",
        "外部写入、发布、安装依赖、真实浏览器访问和外部 LLM 调用必须显式审批。",
        "GitHub public 发布必须先完成 secret/private metadata scan，并需要显式确认。",
    ]
    if job_domain:
        defaults.extend(
            [
                "不自动输入账号密码，不自动提交网申，不自动发送消息或联系招聘方。",
                "简历上传、附件上传、最终投递点击和发送沟通内容必须单独审批。",
            ]
        )
    if "飞书" in text:
        defaults.append("飞书同步必须使用真实 Open Platform 配置；缺配置或权限时输出 blocked。")
    return defaults


def _render_markdown(graph: dict[str, object]) -> str:
    project_name = str(graph.get("project_name") or "").strip()
    title = f"# {project_name} System Architecture" if project_name else "# System Architecture"
    lines = [title, "", str(graph.get("summary") or ""), "", "## Nodes"]
    for node in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
        if isinstance(node, dict):
            lines.append(f"- `{node.get('id')}` {node.get('label')}: {node.get('one_liner_zh')}")
            lines.append(f"  - details: {node.get('details', '')}")
    lines.extend(["", "## Edges"])
    for edge in graph.get("edges", []) if isinstance(graph.get("edges"), list) else []:
        if isinstance(edge, dict):
            lines.append(f"- `{edge.get('from')}` -> `{edge.get('to')}`: {edge.get('label', '')}")
    boundaries = graph.get("boundaries")
    if isinstance(boundaries, list) and boundaries:
        lines.extend(["", "## Safety Boundaries"])
        for boundary in boundaries:
            lines.append(f"- {boundary}")
    sources = graph.get("source_files")
    if isinstance(sources, list) and sources:
        lines.extend(["", "## Evidence Sources"])
        for source in sources[:20]:
            lines.append(f"- `{source}`")
    return "\n".join(lines).rstrip() + "\n"


def _render_mermaid(graph: dict[str, object]) -> str:
    lines = ["flowchart TD"]
    for node in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
        if isinstance(node, dict):
            lines.append(f"  {node.get('id')}[{str(node.get('label', node.get('id'))).replace('[', '(').replace(']', ')')}]")
    for edge in graph.get("edges", []) if isinstance(graph.get("edges"), list) else []:
        if isinstance(edge, dict):
            lines.append(f"  {edge.get('from')} -->|{edge.get('label', '')}| {edge.get('to')}")
    return "\n".join(lines).rstrip() + "\n"


def _render_modules_yaml(graph: dict[str, object]) -> str:
    lines = ["schema: nexus.system.modules.v1", "modules:"]
    for node in graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []:
        if isinstance(node, dict):
            lines.extend(
                [
                    "  -",
                    f"    id: {_yaml_scalar(node.get('id'))}",
                    f"    name: {_yaml_scalar(node.get('label'))}",
                    f"    one_liner_zh: {_yaml_scalar(node.get('one_liner_zh'))}",
                    f"    details: {_yaml_scalar(str(node.get('details', '')).replace(chr(10), ' '))}",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _list_value(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _contains_any(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _safe_source_files(paths: list[str]) -> list[str]:
    allowed_exact = {
        "README.md",
        ".github/nexus-sync.json",
        ".nexus/board.md",
        ".nexus/project-intent.json",
    }
    safe: list[str] = []
    for path in paths:
        if path in allowed_exact or path.startswith("docs/"):
            safe.append(path)
    return safe


def _yaml_scalar(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)
