from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.feishu_sync import DEFAULT_APP_ID_PATH, DEFAULT_APP_SECRET_PATH, DEFAULT_FOLDER_TOKEN_PATH, FeishuConfig, import_markdown_file, load_config, smoke_test, write_config
from nexus.user_prompts import workflow_prompt


SENSITIVE_KEYS = {"app_secret", "tenant_access_token", "app_access_token", "user_access_token", "access_token", "authorization", "token", "secret"}


def build_setup_guide() -> dict[str, object]:
    return {
        "schema": "nexus.feishu_setup_guide.v1",
        "type": "feishu_setup_guide",
        "status": "needs_user_action",
        "open_url": "<FEISHU_URL_REDACTED>",
        "manual_steps": [
            {
                "title": "登录并创建或选择企业自建应用",
                "steps": [
                    "打开 <FEISHU_URL_REDACTED>。",
                    "登录飞书开放平台开发者后台。",
                    "进入目标企业，选择已有企业自建应用，或点击创建企业自建应用。",
                ],
            },
            {
                "title": "获取 App ID 和 App Secret",
                "steps": [
                    "进入应用详情页。",
                    "左侧菜单点击「凭证与基础信息」。",
                    "在「应用凭证」区域复制 App ID，单独保存到本地 app_id 文件。",
                    "复制 App Secret，单独保存到本地 app_secret 文件；文件内只放 secret 本身。",
                ],
            },
            {
                "title": "开通云文档权限",
                "steps": [
                    "左侧菜单点击「开发配置 > 权限管理」。",
                    "点击「开通权限」。",
                    "优先开通 docx:document:create、docx:document:write_only、docx:document:readonly。",
                    "如需把本地 Markdown 文件保真导入为飞书文档，还需要开通 docs:document.media:upload、docs:document:import；备用云空间上传链路还需要 drive:file:upload 或 drive:drive。",
                    "如需读取文件夹或文档元信息，再开通 drive:drive.metadata:readonly。",
                ],
            },
            {
                "title": "发布应用版本",
                "steps": [
                    "左侧菜单点击「应用发布 > 版本管理与发布」。",
                    "点击「创建版本」。",
                    "确认新增权限后提交发布。",
                    "等待企业管理员审核通过；权限未生效前 token 可能成功但文档创建/写入会失败。",
                ],
            },
            {
                "title": "获取 folder_token 或 document_id",
                "steps": [
                    "打开目标飞书文件夹，URL 通常形如 <FEISHU_URL_REDACTED><folder_token>。",
                    "如果要追加已有新版文档，URL 通常形如 <FEISHU_URL_REDACTED><document_id>。",
                    "把应用加入目标文件夹或文档协作者，确保应用身份拥有资源权限。",
                ],
            },
        ],
        "default_paths": {
            "app_id_path": str(DEFAULT_APP_ID_PATH),
            "app_secret_path": str(DEFAULT_APP_SECRET_PATH),
            "folder_token_path": str(DEFAULT_FOLDER_TOKEN_PATH),
        },
        "next_command_template": "$nexus-workflow 初始化飞书配置，项目路径 <project>，并提供 app_id/app_secret/folder_token 文件路径",
    }


def build_publish_required_approval() -> dict[str, object]:
    return {
        "schema": "nexus.manual_approval_required.v1",
        "type": "manual_approval_required",
        "stage": "feishu_publish",
        "reason": "Feishu custom app permission changes require browser setup, version publishing, and administrator review.",
        "manual_steps": [
            "进入飞书开放平台开发者后台。",
            "打开目标企业自建应用。",
            "进入「开发配置 > 权限管理」开通 docx/drive 权限。",
            "如需 Markdown 文件导入，额外开通 docs:document.media:upload、docs:document:import；备用云空间上传链路还需要 drive:file:upload 或 drive:drive。",
            "进入「应用发布 > 版本管理与发布」创建版本并提交审核。",
            "把应用加入目标文件夹或文档权限范围。",
        ],
        "automation_boundary": {
            "can_automate": [
                "检查本地凭证文件是否存在且非空。",
                "写入 .nexus/feishu.json，优先保存凭证文件路径和 folder/doc token 文件路径。",
                "真实请求 tenant_access_token。",
                "在显式 record/create-doc 时创建或追加飞书新版文档。",
            ],
            "must_be_manual": [
                "创建企业自建应用。",
                "查看或复制 App Secret。",
                "开通权限后的管理员审核。",
                "发布应用版本。",
                "授予目标云文档资源权限。",
            ],
        },
    }


def run_setup(
    project: Path,
    *,
    app_id_path: str = "",
    app_secret_path: str = "",
    folder_token: str = "",
    folder_token_path: str = "",
    doc_token: str = "",
    doc_token_path: str = "",
    doc_base_url: str = "",
    guide_only: bool = False,
    run_smoke: bool = True,
    no_network: bool = False,
) -> dict[str, object]:
    project = project.expanduser().resolve()
    existing = load_config(project) or {}
    config = _resolve_config(existing, app_id_path, app_secret_path, folder_token, folder_token_path, doc_token, doc_token_path, doc_base_url)
    checks = _local_checks(project, config, bool(existing))
    guide = build_setup_guide()
    approval = build_publish_required_approval()

    if guide_only:
        return _redact(
            {
                "schema": "nexus.feishu_setup.v1",
                "status": "needs_user_action",
                "reason": "feishu_app_configuration_required",
                "project_path": str(project),
                "checks": checks,
                "guide": guide,
                "approval": approval,
                "next_actions": _next_actions(project, config, configured=False),
            }
        )

    if not checks["app_id_loaded"] or not checks["app_secret_loaded"]:
        return _redact(
            {
                "schema": "nexus.feishu_setup.v1",
                "status": "needs_user_action",
                "reason": "feishu_credentials_missing",
                "project_path": str(project),
                "checks": checks,
                "guide": guide,
                "approval": approval,
                "next_actions": _next_actions(project, config, configured=False),
            }
        )

    config_path = write_config(
        project,
        app_id_path=config.app_id_path,
        app_secret_path=config.app_secret_path,
        folder_token=config.folder_token,
        folder_token_path=config.folder_token_path,
        doc_token=config.doc_token,
        doc_token_path=config.doc_token_path,
        doc_base_url=config.doc_base_url,
    )
    doctor = run_doctor(project, config=config, no_network=no_network or not run_smoke)
    status = "completed" if doctor.get("status") == "completed" else "blocked"
    return _redact(
        {
            "schema": "nexus.feishu_setup.v1",
            "status": status,
            "reason": "" if status == "completed" else doctor.get("reason", "feishu_smoke_failed"),
            "project_path": str(project),
            "project_config": str(config_path),
            "checks": checks,
            "doctor": doctor,
            "guide": guide if status != "completed" else None,
            "approval": approval,
            "next_actions": _next_actions(project, config, configured=True),
        }
    )


def run_doctor(project: Path, *, config: FeishuConfig | None = None, no_network: bool = False) -> dict[str, object]:
    project = project.expanduser().resolve()
    existing = load_config(project) or {}
    config = config or FeishuConfig.from_env_and_mapping(existing)
    checks = _local_checks(project, config, bool(existing))
    if not checks["app_id_loaded"] or not checks["app_secret_loaded"]:
        return _redact(
            {
                "schema": "nexus.feishu_doctor.v1",
                "status": "needs_user_action",
                "reason": "feishu_credentials_missing",
                "checks": checks,
                "diagnostics": _diagnostics("feishu_credentials_missing"),
                "next_actions": _next_actions(project, config, configured=False),
            }
        )
    if no_network:
        return _redact(
            {
                "schema": "nexus.feishu_doctor.v1",
                "status": "completed",
                "reason": "local_checks_only",
                "checks": {**checks, "token_request_success": "not_run"},
                "diagnostics": [],
                "next_actions": _next_actions(project, config, configured=bool(existing)),
            }
        )
    smoke = smoke_test(
        app_id_path=config.app_id_path,
        app_secret_path=config.app_secret_path,
        folder_token=config.folder_token,
        document_id=config.doc_token,
        doc_base_url=config.doc_base_url,
    )
    token = smoke.get("token") if isinstance(smoke.get("token"), dict) else {}
    error = smoke.get("error") if isinstance(smoke.get("error"), dict) else {}
    completed = smoke.get("status") == "completed"
    return _redact(
        {
            "schema": "nexus.feishu_doctor.v1",
            "status": "completed" if completed else "blocked",
            "reason": "" if completed else str(error.get("reason") or "feishu_token_smoke_failed"),
            "checks": {
                **checks,
                "token_request_success": bool(completed),
                "token_expire_seconds": token.get("expire_seconds", 0) if completed else 0,
            },
            "smoke": smoke,
            "diagnostics": [] if completed else _diagnostics(str(error.get("reason") or "feishu_token_smoke_failed")),
            "next_actions": _next_actions(project, config, configured=bool(existing)),
        }
    )


def run_record(
    project: Path,
    *,
    title: str,
    content: str,
    folder_token: str = "",
    folder_token_path: str = "",
    doc_token: str = "",
    doc_token_path: str = "",
    doc_base_url: str = "",
    no_network: bool = False,
) -> dict[str, object]:
    project = project.expanduser().resolve()
    existing = load_config(project)
    if existing is None:
        return _redact(
            {
                "schema": "nexus.feishu_record.v1",
                "status": "blocked",
                "reason": "feishu_config_missing",
                "message": "未检测到 .nexus/feishu.json，无法写入飞书。",
                "guide": build_setup_guide(),
                "next_actions": [workflow_prompt(f"初始化飞书配置，项目路径 {project}")],
            }
        )
    config = FeishuConfig.from_env_and_mapping(existing)
    if folder_token:
        config.folder_token = folder_token
    if folder_token_path:
        config.folder_token_path = Path(folder_token_path)
    if doc_token:
        config.doc_token = doc_token
    if doc_token_path:
        config.doc_token_path = Path(doc_token_path)
    if doc_base_url:
        config.doc_base_url = doc_base_url
    checks = _local_checks(project, config, True)
    if not checks["app_id_loaded"] or not checks["app_secret_loaded"]:
        return _redact(
            {
                "schema": "nexus.feishu_record.v1",
                "status": "blocked",
                "reason": "feishu_credentials_missing",
                "checks": checks,
                "guide": build_setup_guide(),
                "next_actions": _next_actions(project, config, configured=False),
            }
        )
    resolved_folder_token = config.resolved_folder_token()
    resolved_doc_token = config.resolved_doc_token()
    if not resolved_folder_token and not resolved_doc_token:
        return _redact(
            {
                "schema": "nexus.feishu_record.v1",
                "status": "blocked",
                "reason": "feishu_target_missing",
                "message": "飞书记录需要 folder_token 创建新文档，或 doc_token/document_id 追加已有文档。",
                "checks": checks,
                "next_actions": _next_actions(project, config, configured=True),
            }
        )
    if no_network:
        return _redact(
            {
                "schema": "nexus.feishu_record.v1",
                "status": "blocked",
                "reason": "network_disabled",
                "message": "已完成本地配置检查，但 --no-network 禁止真实飞书写入。",
                "checks": checks,
            }
        )
    result = smoke_test(
        app_id_path=config.app_id_path,
        app_secret_path=config.app_secret_path,
        create_doc=not bool(resolved_doc_token),
        title=title or "Nexus 记录",
        folder_token=resolved_folder_token,
        folder_token_path=None,
        document_id=resolved_doc_token,
        document_id_path=None,
        content=content,
        doc_base_url=config.doc_base_url,
    )
    status = "completed" if result.get("status") == "completed" else "blocked"
    error = result.get("error") if isinstance(result.get("error"), dict) else {}
    return _redact(
        {
            "schema": "nexus.feishu_record.v1",
            "status": status,
            "reason": "" if status == "completed" else str(error.get("reason") or "feishu_record_failed"),
            "title": title,
            "result": result,
            "checks": checks,
            "next_actions": _next_actions(project, config, configured=True),
        }
    )


def run_markdown_import(
    project: Path,
    *,
    title: str,
    markdown_path: Path,
    folder_token: str = "",
    folder_token_path: str = "",
    doc_base_url: str = "",
    no_network: bool = False,
) -> dict[str, object]:
    project = project.expanduser().resolve()
    markdown_path = markdown_path.expanduser().resolve()
    existing = load_config(project)
    if existing is None:
        return _redact(
            {
                "schema": "nexus.feishu_markdown_import_record.v1",
                "status": "blocked",
                "reason": "feishu_config_missing",
                "message": "未检测到 .nexus/feishu.json，无法导入 Markdown 到飞书。",
                "markdown_path": str(markdown_path),
                "guide": build_setup_guide(),
                "next_actions": [workflow_prompt(f"初始化飞书配置，项目路径 {project}")],
            }
        )
    config = FeishuConfig.from_env_and_mapping(existing)
    if folder_token:
        config.folder_token = folder_token
    if folder_token_path:
        config.folder_token_path = Path(folder_token_path)
    if doc_base_url:
        config.doc_base_url = doc_base_url
    checks = _local_checks(project, config, True)
    if not checks["app_id_loaded"] or not checks["app_secret_loaded"]:
        return _redact(
            {
                "schema": "nexus.feishu_markdown_import_record.v1",
                "status": "blocked",
                "reason": "feishu_credentials_missing",
                "markdown_path": str(markdown_path),
                "checks": checks,
                "guide": build_setup_guide(),
                "next_actions": _next_actions(project, config, configured=False),
            }
        )
    if no_network:
        return _redact(
            {
                "schema": "nexus.feishu_markdown_import_record.v1",
                "status": "blocked",
                "reason": "network_disabled",
                "message": "已完成本地配置检查，但 --no-network 禁止真实飞书 Markdown 导入。",
                "markdown_path": str(markdown_path),
                "checks": checks,
            }
        )
    config_payload = config.to_project_config()
    result = import_markdown_file(project, markdown_path, config_payload, title=title or markdown_path.stem)
    status = "completed" if result.get("status") == "completed" else "blocked"
    return _redact(
        {
            "schema": "nexus.feishu_markdown_import_record.v1",
            "status": status,
            "reason": str(result.get("reason") or "") if status == "completed" else str(result.get("reason") or "feishu_markdown_import_failed"),
            "title": title,
            "markdown_path": str(markdown_path),
            "result": result,
            "checks": checks,
            "next_actions": _next_actions(project, config, configured=True),
        }
    )


def _resolve_config(
    existing: dict[str, object],
    app_id_path: str,
    app_secret_path: str,
    folder_token: str,
    folder_token_path: str,
    doc_token: str,
    doc_token_path: str,
    doc_base_url: str,
) -> FeishuConfig:
    return FeishuConfig.from_paths(
        app_id_path or str(existing.get("app_id_path") or DEFAULT_APP_ID_PATH),
        app_secret_path or str(existing.get("app_secret_path") or DEFAULT_APP_SECRET_PATH),
        folder_token=folder_token or str(existing.get("folder_token") or ""),
        folder_token_path=folder_token_path or str(existing.get("folder_token_path") or DEFAULT_FOLDER_TOKEN_PATH),
        doc_token=doc_token or str(existing.get("doc_token") or ""),
        doc_token_path=doc_token_path or str(existing.get("doc_token_path") or ""),
        doc_base_url=doc_base_url or str(existing.get("doc_base_url") or ""),
    )


def _local_checks(project: Path, config: FeishuConfig, project_config_exists: bool) -> dict[str, object]:
    return {
        "schema": "nexus.feishu_local_checks.v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "project_path": str(project),
        "project_config_exists": project_config_exists,
        "project_config_path": str(project / ".nexus" / "feishu.json"),
        "app_id_path": str(config.app_id_path),
        "app_secret_path": str(config.app_secret_path),
        "app_id_path_exists": config.app_id_path.expanduser().exists(),
        "app_secret_path_exists": config.app_secret_path.expanduser().exists(),
        "app_id_loaded": _file_nonempty(config.app_id_path),
        "app_secret_loaded": _file_nonempty(config.app_secret_path),
        "folder_token_path": str(config.folder_token_path or ""),
        "doc_token_path": str(config.doc_token_path or ""),
        "folder_token_path_exists": bool(config.folder_token_path and config.folder_token_path.expanduser().exists()),
        "doc_token_path_exists": bool(config.doc_token_path and config.doc_token_path.expanduser().exists()),
        "folder_token_loaded": bool(config.resolved_folder_token()),
        "doc_token_loaded": bool(config.resolved_doc_token()),
        "folder_token_configured": bool(config.resolved_folder_token()),
        "doc_token_configured": bool(config.resolved_doc_token()),
        "doc_base_url_configured": bool(config.doc_base_url),
    }


def _file_nonempty(path: Path) -> bool:
    try:
        path = path.expanduser()
        return path.exists() and bool(path.read_text(encoding="utf-8", errors="ignore").strip())
    except OSError:
        return False


def _diagnostics(reason: str) -> list[str]:
    base = [
        "确认 app_id/app_secret 来自同一个飞书企业自建应用。",
        "确认应用已开通 docx/drive 权限并发布版本。",
        "确认企业管理员审核已通过。",
        "确认应用已加入目标文件夹或文档的资源权限范围。",
    ]
    if reason in {"feishu_credentials_missing", "app_id_file_missing", "app_secret_file_missing", "app_id_empty", "app_secret_empty"}:
        return [
            "检查 app_id/app_secret 文件路径是否正确。",
            "确认文件内容只包含对应字段本身，没有说明文字或空格。",
            *base,
        ]
    if reason == "network_error":
        return ["检查当前环境是否允许访问 https://open.feishu.cn。", *base]
    return base


def _next_actions(project: Path, config: FeishuConfig, *, configured: bool) -> list[str]:
    if not configured:
        return [
            "飞书开放平台\n-> 你的企业自建应用\n-> 应用能力 / 添加应用能力\n-> 确认已经添加「机器人」\n-> 权限管理\n-> 开通 docx/drive 相关权限\n-> 应用发布\n-> 版本管理与发布\n-> 确认已发布",
            workflow_prompt(f"初始化飞书配置，项目路径 {project}，并提供 app_id/app_secret/folder_token 文件路径"),
        ]
    actions = [
        workflow_prompt(f"诊断飞书配置，项目路径 {project}"),
        workflow_prompt(f"进行飞书记录，项目路径 {project}：<记录内容>"),
    ]
    if not config.resolved_folder_token() and not config.resolved_doc_token():
        actions.append("本地文件系统\n-> 准备 folder_token 文件或 doc_token 文件\n-> 文件只保存 token/id 本身\n-> 不在对话窗口粘贴 token 内容\n-> 完成后重新输入飞书初始化配置指令")
    return actions


def _redact(payload: object) -> object:
    if isinstance(payload, dict):
        redacted: dict[str, object] = {}
        for key, value in payload.items():
            lowered = key.lower()
            safe_path_or_status = lowered.endswith("_path") or lowered.endswith("_path_exists") or lowered.endswith("_loaded") or lowered.endswith("_configured") or lowered.endswith("_present")
            safe_token_status = lowered in {"token_request_success", "token_expire_seconds", "expire_seconds", "raw_code"}
            if not safe_path_or_status and not safe_token_status and (lowered in SENSITIVE_KEYS or any(part in lowered for part in ["secret", "token", "authorization"])):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact(value)
        return redacted
    if isinstance(payload, list):
        return [_redact(item) for item in payload]
    return payload
