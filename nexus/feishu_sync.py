from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from typing import Any


CONFIG_REL = Path(".nexus/feishu.json")
DOC_INDEX_REL = Path(".nexus/feishu-documents.json")
BASE_URL = "<FEISHU_URL_REDACTED>"
DEFAULT_APP_ID_PATH = Path("<LOCAL_PATH_REDACTED>")
DEFAULT_APP_SECRET_PATH = Path("<LOCAL_PATH_REDACTED>")
DEFAULT_FOLDER_TOKEN_PATH = Path("<LOCAL_PATH_REDACTED>")


class FeishuError(RuntimeError):
    def __init__(self, reason: str, message: str, *, code: int | None = None, response: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.code = code
        self.response = response or {}

    def to_result(self, schema: str) -> dict[str, object]:
        return {
            "schema": schema,
            "status": "blocked",
            "reason": self.reason,
            "message": str(self),
            "code": self.code,
            "response": _redact(self.response),
            "next_checks": _next_checks(self.reason, self.code),
        }


@dataclass(slots=True)
class FeishuConfig:
    app_id_path: Path = DEFAULT_APP_ID_PATH
    app_secret_path: Path = DEFAULT_APP_SECRET_PATH
    folder_token: str = ""
    folder_token_path: Path | None = DEFAULT_FOLDER_TOKEN_PATH
    doc_token: str = ""
    doc_token_path: Path | None = None
    doc_base_url: str = ""

    @classmethod
    def from_env_and_mapping(cls, mapping: dict[str, object] | None = None) -> "FeishuConfig":
        mapping = mapping or {}
        return cls(
            app_id_path=Path(os.environ.get("FEISHU_APP_ID_PATH") or str(mapping.get("app_id_path") or DEFAULT_APP_ID_PATH)),
            app_secret_path=Path(os.environ.get("FEISHU_APP_SECRET_PATH") or str(mapping.get("app_secret_path") or DEFAULT_APP_SECRET_PATH)),
            folder_token=str(os.environ.get("FEISHU_FOLDER_TOKEN") or mapping.get("folder_token") or ""),
            folder_token_path=_optional_path(os.environ.get("FEISHU_FOLDER_TOKEN_PATH") or mapping.get("folder_token_path") or DEFAULT_FOLDER_TOKEN_PATH),
            doc_token=str(os.environ.get("FEISHU_DOC_TOKEN") or mapping.get("doc_token") or ""),
            doc_token_path=_optional_path(os.environ.get("FEISHU_DOC_TOKEN_PATH") or mapping.get("doc_token_path") or ""),
            doc_base_url=str(os.environ.get("FEISHU_DOC_BASE_URL") or mapping.get("doc_base_url") or ""),
        )

    @classmethod
    def from_project(cls, project: Path) -> "FeishuConfig":
        return cls.from_env_and_mapping(load_config(project) or {})

    @classmethod
    def from_paths(
        cls,
        app_id_path: str | Path,
        app_secret_path: str | Path,
        *,
        folder_token: str = "",
        folder_token_path: str | Path | None = DEFAULT_FOLDER_TOKEN_PATH,
        doc_token: str = "",
        doc_token_path: str | Path | None = None,
        doc_base_url: str = "",
    ) -> "FeishuConfig":
        return cls(Path(app_id_path), Path(app_secret_path), folder_token, _optional_path(folder_token_path), doc_token, _optional_path(doc_token_path), doc_base_url)

    def read_app_id(self) -> str:
        return _read_required_file(self.app_id_path, "app_id_file_missing", "app_id_empty")

    def read_app_secret(self) -> str:
        return _read_required_file(self.app_secret_path, "app_secret_file_missing", "app_secret_empty")

    def read_folder_token(self) -> str:
        return _normalize_resource_token(self.folder_token or _read_optional_file(self.folder_token_path))

    def read_doc_token(self) -> str:
        return _normalize_resource_token(self.doc_token or _read_optional_file(self.doc_token_path))

    def resolved_folder_token(self) -> str:
        return self.read_folder_token()

    def resolved_doc_token(self) -> str:
        return self.read_doc_token()

    def diagnostics(self) -> dict[str, object]:
        app_id_ok = _file_exists_and_nonempty(self.app_id_path)
        app_secret_ok = _file_exists_and_nonempty(self.app_secret_path)
        folder_value = self.resolved_folder_token()
        doc_value = self.resolved_doc_token()
        return {
            "schema": "nexus.feishu_config_diagnostics.v1",
            "app_id_path": str(self.app_id_path),
            "app_secret_path": str(self.app_secret_path),
            "app_id_loaded": bool(app_id_ok),
            "app_secret_loaded": bool(app_secret_ok),
            "folder_token_path": str(self.folder_token_path or ""),
            "doc_token_path": str(self.doc_token_path or ""),
            "folder_token_loaded": bool(folder_value),
            "doc_token_loaded": bool(doc_value),
            "doc_base_url_present": bool(self.doc_base_url),
        }

    def to_project_config(self) -> dict[str, object]:
        return {
            "schema": "nexus.feishu_config.v1",
            "app_id_path": str(self.app_id_path),
            "app_secret_path": str(self.app_secret_path),
            "folder_token": self.folder_token,
            "folder_token_path": str(self.folder_token_path or ""),
            "doc_token": self.doc_token,
            "doc_token_path": str(self.doc_token_path or ""),
            "doc_base_url": self.doc_base_url,
        }


class FeishuAuthClient:
    def __init__(self, config: FeishuConfig) -> None:
        self.config = config
        self._tenant_access_token = ""
        self._expire_at = 0

    def get_tenant_access_token(self) -> dict[str, object]:
        app_id = self.config.read_app_id()
        app_secret = self.config.read_app_secret()
        payload = {"app_id": app_id, "app_secret": app_secret}
        data = _post_json("/auth/v3/tenant_access_token/internal", payload, headers={})
        if data.get("code") != 0:
            raise FeishuError("feishu_auth_failed", "飞书 token 获取失败，请检查 app_id/app_secret、应用版本发布、企业自建应用是否正确。", code=_int_code(data), response=data)
        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise FeishuError("feishu_auth_empty_token", "飞书 token 响应缺少 tenant_access_token。", response=data)
        expire = int(data.get("expire") or 0)
        self._tenant_access_token = token
        self._expire_at = int(time.time()) + expire
        return {
            "schema": "nexus.feishu_token_check.v1",
            "status": "completed",
            "app_id_loaded": True,
            "app_secret_loaded": True,
            "token_request_success": True,
            "expire_seconds": expire,
            "expire_at": self._expire_at,
        }

    @property
    def token(self) -> str:
        if not self._tenant_access_token or self._expire_at <= int(time.time()) + 60:
            self.get_tenant_access_token()
        return self._tenant_access_token


class FeishuDocsClient:
    def __init__(self, auth: FeishuAuthClient) -> None:
        self.auth = auth

    def create_document(self, title: str, folder_token: str | None = None) -> dict[str, object]:
        payload: dict[str, object] = {"title": title}
        if folder_token:
            payload["folder_token"] = folder_token
        data = _post_json("/docx/v1/documents", payload, headers=self._headers())
        if data.get("code") != 0:
            raise FeishuError("feishu_create_doc_failed", "创建飞书新版文档失败，请检查 docx/drive 权限、应用发布状态、folder_token 权限。", code=_int_code(data), response=data)
        document = data.get("data", {}).get("document", {}) if isinstance(data.get("data"), dict) else {}
        document_id = str(document.get("document_id") or data.get("data", {}).get("document_id", ""))
        if not document_id:
            raise FeishuError("feishu_create_doc_missing_document_id", "飞书创建文档成功响应中没有 document_id。", response=data)
        return {
            "schema": "nexus.feishu_document.v1",
            "status": "completed",
            "document_id": document_id,
            "url": document_url(document_id, self.auth.config.doc_base_url),
            "raw_code": data.get("code"),
        }

    def append_text(self, document_id: str, content: str) -> dict[str, object]:
        children = _content_to_blocks(content)
        return self.append_blocks(document_id, children)

    def append_blocks(self, document_id: str, children: list[dict[str, object]]) -> dict[str, object]:
        responses: list[dict[str, object]] = []
        for chunk in _chunks(children, 50):
            payload = {"index": -1, "children": chunk}
            data = _post_json(f"/docx/v1/documents/{document_id}/blocks/{document_id}/children", payload, headers=self._headers())
            responses.append({"code": data.get("code"), "block_count": len(chunk)})
            if data.get("code") != 0:
                raise FeishuError("feishu_append_doc_failed", "向飞书新版文档写入内容失败，请检查文档权限、应用是否被加入文档协作者、docx block 权限。", code=_int_code(data), response=data)
        return {
            "schema": "nexus.feishu_append.v1",
            "status": "completed",
            "document_id": document_id,
            "url": document_url(document_id, self.auth.config.doc_base_url),
            "block_count": len(children),
            "append_batches": len(responses),
            "raw_code": data.get("code"),
        }

    def replace_with_markdown_text(self, document_id: str, markdown_path: Path, *, title: str = "") -> dict[str, object]:
        if not markdown_path.exists():
            raise FeishuError("markdown_not_found", f"Markdown 文件不存在：{markdown_path}")
        deleted = self.delete_all_root_children(document_id)
        content = markdown_path.read_text(encoding="utf-8", errors="ignore")
        blocks = _content_to_blocks(content)
        appended = self.append_blocks(document_id, blocks)
        return {
            "schema": "nexus.feishu_document_replace.v1",
            "status": "completed",
            "reason": "existing_document_updated",
            "document_id": document_id,
            "title": title or markdown_path.stem,
            "markdown_path": str(markdown_path),
            "url": document_url(document_id, self.auth.config.doc_base_url),
            "deleted": deleted,
            "append": appended,
        }

    def delete_all_root_children(self, document_id: str) -> dict[str, object]:
        batches: list[dict[str, object]] = []
        while True:
            children = self.list_child_blocks(document_id, document_id, page_size=500)
            count = len(children)
            if count <= 0:
                break
            data = self.delete_child_range(document_id, document_id, start_index=0, end_index=count)
            batches.append({"deleted_count": count, "raw_code": data.get("code")})
            time.sleep(0.4)
        return {
            "schema": "nexus.feishu_delete_children.v1",
            "status": "completed",
            "document_id": document_id,
            "deleted_batches": len(batches),
            "deleted_count": sum(int(batch.get("deleted_count") or 0) for batch in batches),
            "batches": batches,
        }

    def list_child_blocks(self, document_id: str, block_id: str, *, page_size: int = 500) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        page_token = ""
        while True:
            query = f"page_size={page_size}"
            if page_token:
                query += f"&page_token={page_token}"
            data = _get_json(f"/docx/v1/documents/{document_id}/blocks/{block_id}/children?{query}", headers=self._headers())
            if data.get("code") != 0:
                raise FeishuError("feishu_list_doc_children_failed", "获取飞书新版文档子块失败，请检查 docx 阅读权限和文档资源权限。", code=_int_code(data), response=data)
            payload = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
            batch = payload.get("items") if isinstance(payload.get("items"), list) else []
            items.extend(item for item in batch if isinstance(item, dict))
            if not payload.get("has_more"):
                break
            page_token = str(payload.get("page_token") or "")
            if not page_token:
                break
        return items

    def delete_child_range(self, document_id: str, block_id: str, *, start_index: int, end_index: int) -> dict[str, object]:
        payload = {"start_index": start_index, "end_index": end_index}
        data = _delete_json(f"/docx/v1/documents/{document_id}/blocks/{block_id}/children/batch_delete?document_revision_id=-1", payload, headers=self._headers())
        if data.get("code") != 0:
            raise FeishuError("feishu_delete_doc_children_failed", "删除飞书新版文档旧内容失败，请检查 docx 编辑权限和文档资源权限。", code=_int_code(data), response=data)
        return data

    def import_markdown(self, markdown_path: Path, *, title: str, folder_token: str) -> dict[str, object]:
        attempts: list[dict[str, object]] = []
        try:
            result = self._import_markdown_as(markdown_path, title=title, folder_token=folder_token, import_type="docx")
            result["attempts"] = [{"import_type": "docx", "status": "completed"}]
            return result
        except FeishuError as exc:
            attempts.append({"upload_mode": "ccm_import_open", "import_type": "docx", "status": "blocked", "reason": exc.reason, "error": exc.to_result("nexus.feishu_error.v1")})
            if exc.reason not in {"feishu_import_task_failed", "feishu_import_task_timeout"}:
                raise
        try:
            result = self._import_markdown_file_upload_as(markdown_path, title=title, folder_token=folder_token, import_type="docx")
            result["attempts"] = [*attempts, {"upload_mode": "drive_file", "import_type": "docx", "status": "completed"}]
            return result
        except FeishuError as exc:
            exc.response = {"attempts": attempts, "last_error": exc.response}
            raise

    def _import_markdown_as(self, markdown_path: Path, *, title: str, folder_token: str, import_type: str) -> dict[str, object]:
        file_token = self.upload_markdown_for_import(markdown_path, title=title, import_type=import_type)
        task = self.create_import_task(file_token=file_token, file_extension=_markdown_extension(markdown_path), folder_token=folder_token, import_type=import_type, file_name=_markdown_file_name(markdown_path, title))
        ticket = str(task.get("ticket") or "")
        result = self.wait_import_task(ticket)
        document_token = str(result.get("token") or result.get("file_token") or result.get("document_id") or "")
        url = str(result.get("url") or "")
        if not url and document_token:
            url = document_url(document_token, self.auth.config.doc_base_url)
        return {
            "schema": "nexus.feishu_markdown_import.v1",
            "status": "completed",
            "import_type": import_type,
            "markdown_path": str(markdown_path),
            "file_token": file_token,
            "ticket": ticket,
            "document_token": document_token,
            "url": url,
            "task": task,
            "result": result,
        }

    def _import_markdown_file_upload_as(self, markdown_path: Path, *, title: str, folder_token: str, import_type: str) -> dict[str, object]:
        file_token = self.upload_markdown_file_for_import(markdown_path, title=title, folder_token=folder_token)
        task = self.create_import_task(file_token=file_token, file_extension=_markdown_extension(markdown_path), folder_token=folder_token, import_type=import_type, file_name=_markdown_file_name(markdown_path, title))
        ticket = str(task.get("ticket") or "")
        result = self.wait_import_task(ticket)
        document_token = str(result.get("token") or result.get("file_token") or result.get("document_id") or "")
        url = str(result.get("url") or "")
        if not url and document_token:
            url = document_url(document_token, self.auth.config.doc_base_url)
        return {
            "schema": "nexus.feishu_markdown_import.v1",
            "status": "completed",
            "upload_mode": "drive_file",
            "import_type": import_type,
            "markdown_path": str(markdown_path),
            "file_token": file_token,
            "ticket": ticket,
            "document_token": document_token,
            "url": url,
            "task": task,
            "result": result,
        }

    def upload_markdown_for_import(self, markdown_path: Path, *, title: str, import_type: str = "docx") -> str:
        if not markdown_path.exists():
            raise FeishuError("markdown_not_found", f"Markdown 文件不存在：{markdown_path}")
        import_type = _import_type(import_type)
        content = markdown_path.read_bytes()
        fields: dict[str, object] = {
            "file_name": _markdown_file_name(markdown_path, title),
            "parent_type": "ccm_import_open",
            "parent_node": "/",
            "size": str(len(content)),
            "extra": json.dumps({"obj_type": import_type, "file_extension": _markdown_extension(markdown_path)}, ensure_ascii=False),
        }
        data = _post_multipart(
            "/drive/v1/medias/upload_all",
            fields=fields,
            file_field="file",
            file_name=_markdown_file_name(markdown_path, title),
            file_bytes=content,
            content_type="text/markdown; charset=utf-8",
            headers=self._headers(),
        )
        if data.get("code") != 0:
            raise FeishuError("feishu_upload_markdown_failed", "上传 Markdown 导入文件失败，请检查 drive 上传权限、应用发布状态和文件大小。", code=_int_code(data), response=data)
        payload = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
        file_token = str(payload.get("file_token") or data.get("file_token") or "")
        if not file_token:
            raise FeishuError("feishu_upload_markdown_missing_file_token", "飞书上传响应缺少 file_token。", response=data)
        return file_token

    def upload_markdown_file_for_import(self, markdown_path: Path, *, title: str, folder_token: str) -> str:
        if not markdown_path.exists():
            raise FeishuError("markdown_not_found", f"Markdown 文件不存在：{markdown_path}")
        content = markdown_path.read_bytes()
        file_name = _markdown_file_name(markdown_path, title)
        data = _post_multipart(
            "/drive/v1/files/upload_all",
            fields={"file_name": file_name, "parent_type": "explorer", "parent_node": folder_token, "size": str(len(content))},
            file_field="file",
            file_name=file_name,
            file_bytes=content,
            content_type="text/markdown; charset=utf-8",
            headers=self._headers(),
        )
        if data.get("code") != 0:
            raise FeishuError("feishu_upload_markdown_file_failed", "上传 Markdown 文件到飞书云空间失败，请检查 drive:file:upload/drive:drive 权限、folder_token 和应用资源权限。", code=_int_code(data), response=data)
        payload = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
        file_token = str(payload.get("file_token") or data.get("file_token") or "")
        if not file_token:
            raise FeishuError("feishu_upload_markdown_file_missing_file_token", "飞书云空间文件上传响应缺少 file_token。", response=data)
        return file_token

    def create_import_task(self, *, file_token: str, file_extension: str, folder_token: str, import_type: str = "docx", file_name: str = "") -> dict[str, object]:
        import_type = _import_type(import_type)
        payload: dict[str, object] = {
            "file_extension": file_extension,
            "file_name": Path(file_name).stem if file_name else "operation-guide",
            "file_token": file_token,
            "type": import_type,
            "point": {"mount_type": 1, "mount_key": folder_token},
        }
        data = _post_json("/drive/v1/import_tasks", payload, headers=self._headers())
        if data.get("code") != 0:
            raise FeishuError("feishu_create_import_task_failed", "创建飞书 Markdown 导入任务失败，请检查 drive import 权限、folder_token 和应用资源权限。", code=_int_code(data), response=data)
        payload_data = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
        ticket = str(payload_data.get("ticket") or data.get("ticket") or "")
        if not ticket:
            raise FeishuError("feishu_import_task_missing_ticket", "飞书导入任务响应缺少 ticket。", response=data)
        return {
            "schema": "nexus.feishu_import_task.v1",
            "status": "submitted",
            "import_type": import_type,
            "ticket": ticket,
            "raw_code": data.get("code"),
        }

    def wait_import_task(self, ticket: str, *, timeout_seconds: int = 60) -> dict[str, object]:
        if not ticket:
            raise FeishuError("feishu_import_task_ticket_missing", "无法查询空 ticket 的导入任务。")
        started = time.time()
        attempts: list[dict[str, object]] = []
        while time.time() - started <= timeout_seconds:
            data = _get_json(f"/drive/v1/import_tasks/{ticket}", headers=self._headers())
            if data.get("code") != 0:
                raise FeishuError("feishu_get_import_task_failed", "查询飞书 Markdown 导入任务失败。", code=_int_code(data), response=data)
            status = _import_task_status(data)
            result_payload = _import_task_result(data)
            attempts.append({"status": status, "code": data.get("code"), "job_error_msg": result_payload.get("job_error_msg") or ""})
            if status in {"success", "completed", "done", "succeeded", "0"}:
                return {"schema": "nexus.feishu_import_task_result.v1", "status": "completed", "ticket": ticket, "attempts": attempts, **result_payload}
            if result_payload.get("job_error_msg") or status in {"failed", "fail", "error", "3", "4"}:
                raise FeishuError("feishu_import_task_failed", "飞书 Markdown 导入任务失败。", response=data)
            time.sleep(2)
        raise FeishuError("feishu_import_task_timeout", "飞书 Markdown 导入任务超时，请稍后查询导入任务结果。", response={"ticket": ticket, "attempts": attempts})

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.auth.token}"}


def write_config(
    project: Path,
    *,
    app_id_path: str | Path = DEFAULT_APP_ID_PATH,
    app_secret_path: str | Path = DEFAULT_APP_SECRET_PATH,
    folder_token: str = "",
    folder_token_path: str | Path | None = DEFAULT_FOLDER_TOKEN_PATH,
    doc_token: str = "",
    doc_token_path: str | Path | None = None,
    doc_base_url: str = "",
    app_id: str = "",
    app_secret_env: str = "",
) -> Path:
    del app_id, app_secret_env
    target = project / CONFIG_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = FeishuConfig.from_paths(Path(app_id_path), Path(app_secret_path), folder_token=folder_token, folder_token_path=folder_token_path, doc_token=doc_token, doc_token_path=doc_token_path, doc_base_url=doc_base_url).to_project_config()
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def load_config(project: Path) -> dict[str, object] | None:
    path = project / CONFIG_REL
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def smoke_test(
    *,
    app_id_path: Path = DEFAULT_APP_ID_PATH,
    app_secret_path: Path = DEFAULT_APP_SECRET_PATH,
    create_doc: bool = False,
    title: str = "Feishu API smoke test",
    folder_token: str = "",
    folder_token_path: Path | None = DEFAULT_FOLDER_TOKEN_PATH,
    document_id: str = "",
    document_id_path: Path | None = None,
    content: str = "",
    doc_base_url: str = "",
) -> dict[str, object]:
    config = FeishuConfig.from_paths(app_id_path, app_secret_path, folder_token=folder_token, folder_token_path=folder_token_path, doc_token=document_id, doc_token_path=document_id_path, doc_base_url=doc_base_url)
    auth = FeishuAuthClient(config)
    diagnostics = config.diagnostics()
    try:
        token_check = auth.get_tenant_access_token()
        result: dict[str, object] = {"schema": "nexus.feishu_smoke.v1", "status": "completed", "config": diagnostics, "token": token_check, "document": None, "append": None}
        docs = FeishuDocsClient(auth)
        resolved_folder_token = config.resolved_folder_token()
        resolved_doc_token = config.resolved_doc_token()
        if create_doc:
            created = docs.create_document(title, resolved_folder_token or None)
            result["document"] = created
            document_id = str(created["document_id"])
        target_document_id = document_id or resolved_doc_token
        if target_document_id and content:
            result["append"] = docs.append_text(target_document_id, content)
        return result
    except FeishuError as exc:
        return {"schema": "nexus.feishu_smoke.v1", "status": "blocked", "config": diagnostics, "error": exc.to_result("nexus.feishu_error.v1")}
    except urllib.error.URLError as exc:
        return {"schema": "nexus.feishu_smoke.v1", "status": "blocked", "config": diagnostics, "error": {"reason": "network_error", "message": str(exc), "next_checks": _next_checks("network_error", None)}}


def publish_markdown(project: Path, markdown_path: Path, config: dict[str, object]) -> dict[str, object]:
    return publish_markdown_document(project, markdown_path, config, title=markdown_path.stem, schema="nexus.feishu_publish.v1")


def publish_markdown_document(project: Path, markdown_path: Path, config: dict[str, object], *, title: str = "", schema: str = "nexus.feishu_markdown_document_publish.v1") -> dict[str, object]:
    project = project.expanduser().resolve()
    markdown_path = markdown_path.expanduser().resolve()
    feishu_config = FeishuConfig.from_env_and_mapping(config)
    diagnostics = feishu_config.diagnostics()
    if not markdown_path.exists():
        return {"schema": schema, "status": "blocked", "reason": "markdown_not_found", "markdown_path": str(markdown_path), "config": diagnostics}
    resolved_folder_token = feishu_config.resolved_folder_token()
    resolved_doc_token = feishu_config.resolved_doc_token()
    document_key = _document_key(project, markdown_path)
    index = _load_document_index(project)
    existing = _index_document_entry(index, document_key)
    stale_ids = _stale_document_ids(index, document_key)
    historical = _find_historical_document_entry(project, document_key, markdown_path, stale_ids=stale_ids)
    existing_document_id = str(existing.get("document_id") or existing.get("document_token") or "") or str(historical.get("document_id") or "") or resolved_doc_token
    if not resolved_folder_token and not existing_document_id:
        return {
            "schema": schema,
            "status": "blocked",
            "reason": "feishu_import_requires_folder_token",
            "message": "首次发布 Markdown 指南/说明文档需要 folder_token 创建飞书文档；后续会通过 .nexus/feishu-documents.json 复用并更新同一份文档。",
            "markdown_path": str(markdown_path),
            "document_key": document_key,
            "config": diagnostics,
            "next_checks": _next_checks("feishu_import_requires_folder_token", None),
        }
    auth = FeishuAuthClient(feishu_config)
    try:
        token_check = auth.get_tenant_access_token()
        docs = FeishuDocsClient(auth)
        if existing_document_id:
            binding = existing if existing else historical
            try:
                updated = docs.replace_with_markdown_text(existing_document_id, markdown_path, title=title or markdown_path.stem)
                _upsert_document_entry(project, index, document_key, markdown_path, title=title or markdown_path.stem, document_id=existing_document_id, url=str(updated.get("url") or historical.get("url") or ""), mode="replace_blocks")
                return {
                    "schema": schema,
                    "status": "completed",
                    "reason": "existing_document_updated",
                    "markdown_path": str(markdown_path),
                    "document_key": document_key,
                    "config": diagnostics,
                    "token": token_check,
                    "document": updated,
                    "index_path": str(project / DOC_INDEX_REL),
                }
            except FeishuError as exc:
                category = classify_feishu_error(exc)
                if category["category"] in {"stale_resource", "resource_permission_missing"}:
                    stale = _mark_stale_document_entry(project, index, document_key, markdown_path, binding, exc, category)
                    if resolved_folder_token:
                        imported = docs.import_markdown(markdown_path, title=title or markdown_path.stem, folder_token=resolved_folder_token)
                        document_id, url = _imported_document_identity(imported, feishu_config)
                        _upsert_document_entry(project, index, document_key, markdown_path, title=title or markdown_path.stem, document_id=document_id, url=url, mode="import_markdown")
                        rebuild_reason = "rebuilt_after_stale_binding" if category["category"] == "stale_resource" else "rebuilt_after_resource_permission_missing"
                        return {
                            "schema": schema,
                            "status": "completed",
                            "reason": rebuild_reason,
                            "markdown_path": str(markdown_path),
                            "document_key": document_key,
                            "config": diagnostics,
                            "token": token_check,
                            "previous_binding": stale,
                            "rebuild": {
                                "schema": "nexus.feishu_document_rebuild.v1",
                                "status": "completed",
                                "mode": "import_markdown",
                                "document_id": document_id,
                                "url": url,
                                "import": imported,
                            },
                            "index_path": str(project / DOC_INDEX_REL),
                        }
                    blocked_reason = "feishu_rebuild_requires_folder_token" if category["category"] == "stale_resource" else "feishu_resource_permission_missing"
                    return {
                        "schema": schema,
                        "status": "blocked",
                        "reason": blocked_reason,
                        "markdown_path": str(markdown_path),
                        "document_key": document_key,
                        "config": diagnostics,
                        "previous_binding": stale,
                        "error": exc.to_result("nexus.feishu_error.v1"),
                        "classification": category,
                        "next_checks": _next_checks(blocked_reason, exc.code),
                    }
                raise
        imported = docs.import_markdown(markdown_path, title=title or markdown_path.stem, folder_token=resolved_folder_token)
        document_id, url = _imported_document_identity(imported, feishu_config)
        _upsert_document_entry(project, index, document_key, markdown_path, title=title or markdown_path.stem, document_id=document_id, url=url, mode="import_markdown")
        return {
            "schema": schema,
            "status": "completed",
            "reason": "new_document_imported",
            "markdown_path": str(markdown_path),
            "document_key": document_key,
            "config": diagnostics,
            "token": token_check,
            "import": imported,
            "index_path": str(project / DOC_INDEX_REL),
        }
    except FeishuError as exc:
        return {"schema": schema, "status": "blocked", "reason": exc.reason, "markdown_path": str(markdown_path), "document_key": document_key, "config": diagnostics, "error": exc.to_result("nexus.feishu_error.v1")}
    except urllib.error.URLError as exc:
        return {"schema": schema, "status": "blocked", "reason": "network_error", "markdown_path": str(markdown_path), "document_key": document_key, "config": diagnostics, "error": {"reason": "network_error", "message": str(exc), "next_checks": _next_checks("network_error", None)}}


def import_markdown_file(project: Path, markdown_path: Path, config: dict[str, object], *, title: str = "") -> dict[str, object]:
    return publish_markdown_document(project, markdown_path, config, title=title, schema="nexus.feishu_markdown_import_publish.v1")


def _load_document_index(project: Path) -> dict[str, object]:
    path = project / DOC_INDEX_REL
    if not path.exists():
        return {"schema": "nexus.feishu_documents.v1", "documents": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": "nexus.feishu_documents.v1", "documents": {}}
    if not isinstance(payload, dict):
        return {"schema": "nexus.feishu_documents.v1", "documents": {}}
    documents = payload.get("documents")
    if not isinstance(documents, dict):
        payload["documents"] = {}
    payload.setdefault("schema", "nexus.feishu_documents.v1")
    return payload


def _save_document_index(project: Path, index: dict[str, object]) -> Path:
    path = project / DOC_INDEX_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _index_document_entry(index: dict[str, object], key: str) -> dict[str, object]:
    documents = index.get("documents")
    if not isinstance(documents, dict):
        return {}
    entry = documents.get(key)
    return entry if isinstance(entry, dict) else {}


def classify_feishu_error(exc: FeishuError) -> dict[str, object]:
    response_text = json.dumps(_redact(exc.response), ensure_ascii=False).lower()
    message = str(exc).lower()
    reason = exc.reason.lower()
    code = exc.code
    deleted_markers = ["resource deleted", "deleted", "not found", "不存在", "已删除", "invalid document", "invalid token"]
    if code == 1770003 and any(marker in response_text or marker in message for marker in deleted_markers):
        category = "stale_resource"
    elif reason in {"feishu_list_doc_children_failed", "feishu_delete_doc_children_failed", "feishu_append_doc_failed"} and any(marker in response_text or marker in message for marker in deleted_markers):
        category = "stale_resource"
    elif reason in {"feishu_list_doc_children_failed", "feishu_delete_doc_children_failed", "feishu_append_doc_failed", "feishu_create_doc_failed"}:
        category = "resource_permission_missing"
    elif reason in {"feishu_upload_markdown_failed", "feishu_create_import_task_failed", "feishu_upload_markdown_file_failed", "feishu_get_import_task_failed"}:
        category = "api_scope_missing"
    elif reason in {"app_id_file_missing", "app_secret_file_missing", "app_id_empty", "app_secret_empty", "feishu_auth_failed", "feishu_auth_empty_token"}:
        category = "credential_missing"
    elif reason in {"feishu_import_requires_folder_token", "feishu_target_missing", "feishu_rebuild_requires_folder_token"}:
        category = "target_missing"
    else:
        category = "unknown"
    return {
        "schema": "nexus.feishu_error_classification.v1",
        "category": category,
        "reason": exc.reason,
        "code": code,
        "message": str(exc),
    }


def feishu_publish_next_prompt(project: Path, result: dict[str, object], *, retry_prompt: str = "") -> str:
    retry = retry_prompt or f"$nexus-workflow 同步整体操作指南到飞书，项目路径 {project}"
    reason = str(result.get("reason") or "")
    if result.get("status") == "completed":
        if reason in {"rebuilt_after_stale_binding", "rebuilt_after_resource_permission_missing"}:
            return f"飞书指南/说明文档同步已自动修复失效绑定并完成；后续修改可继续输入：{retry}"
        return f"飞书指南/说明文档同步已完成；后续修改可继续输入：{retry}"
    category = _result_category(result)
    if reason == "feishu_rebuild_requires_folder_token" or category == "target_missing":
        return "\n".join(
            [
                "旧飞书文档绑定已失效或缺少首次发布目标，需要可用 folder_token 才能自动重建线上文档。",
                "飞书云文档",
                "-> 打开目标文件夹",
                "-> 复制文件夹 URL 中 /drive/folder/ 后面的 folder_token",
                "-> 将 folder_token 保存到本地文件，不要贴到对话里",
                f"-> 输入 $nexus-workflow 初始化飞书配置，项目路径 {project}，并提供 folder_token 文件路径",
                f"完成后重试：{retry}",
            ]
        )
    if category == "api_scope_missing":
        return "\n".join(
            [
                "飞书应用缺少文档导入/上传/写入 API 权限，或权限发布版本尚未生效。",
                "飞书开放平台",
                "-> 进入目标企业自建应用",
                "-> 开通 docx 文档读写、docs Markdown 导入、drive 文件上传/导入相关权限",
                "-> 创建并发布应用版本，等待管理员审核通过",
                f"完成后重试：{retry}",
            ]
        )
    if category == "resource_permission_missing":
        return "\n".join(
            [
                "飞书应用没有目标文档或目标文件夹的资源权限，且当前自动重建未完成。",
                "飞书云文档",
                "-> 把企业自建应用加入目标文件夹或目标文档的协作者/权限范围",
                "-> 或配置一个应用可访问的 folder_token 让 Nexus 自动重建文档",
                f"完成后重试：{retry}",
            ]
        )
    if category == "credential_missing":
        return "\n".join(
            [
                "飞书应用凭证不可用。",
                "本地文件系统",
                "-> 准备 app_id 文件和 app_secret 文件，文件内只保存字段本身",
                f"-> 输入 $nexus-workflow 初始化飞书配置，项目路径 {project}，并提供 app_id/app_secret/folder_token 文件路径",
                f"完成后重试：{retry}",
            ]
        )
    if reason == "network_error":
        return f"当前环境无法访问飞书 OpenAPI；确认网络可访问 https://open.feishu.cn 后重试：{retry}"
    return f"飞书同步失败，reason={reason or 'unknown'}；查看 tool_results 中的 error/classification 后重试：{retry}"


def _result_category(result: dict[str, object]) -> str:
    classification = result.get("classification")
    if isinstance(classification, dict):
        return str(classification.get("category") or "")
    nested = result.get("result")
    if isinstance(nested, dict):
        return _result_category(nested)
    error = result.get("error")
    if isinstance(error, dict):
        reason = str(error.get("reason") or "")
        code_value = error.get("code")
        try:
            code = int(code_value) if code_value is not None else None
        except (TypeError, ValueError):
            code = None
        return str(classify_feishu_error(FeishuError(reason, str(error.get("message") or ""), code=code, response=error.get("response") if isinstance(error.get("response"), dict) else {})).get("category") or "")
    return ""


def _upsert_document_entry(project: Path, index: dict[str, object], key: str, markdown_path: Path, *, title: str, document_id: str, url: str, mode: str) -> None:
    documents = index.setdefault("documents", {})
    if not isinstance(documents, dict):
        documents = {}
        index["documents"] = documents
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    previous = documents.get(key) if isinstance(documents.get(key), dict) else {}
    created_at = str(previous.get("created_at") or now) if isinstance(previous, dict) else now
    documents[key] = {
        "schema": "nexus.feishu_document_binding.v1",
        "local_path": _relative_or_absolute(project, markdown_path),
        "title": title,
        "document_id": document_id,
        "url": url or document_url(document_id),
        "mode": mode,
        "created_at": created_at,
        "updated_at": now,
    }
    _save_document_index(project, index)


def _mark_stale_document_entry(project: Path, index: dict[str, object], key: str, markdown_path: Path, binding: dict[str, object], exc: FeishuError, classification: dict[str, object]) -> dict[str, object]:
    documents = index.get("documents")
    current = binding if binding else {}
    if isinstance(documents, dict):
        stored = documents.pop(key, None)
        if isinstance(stored, dict):
            current = {**stored, **current}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stale = {
        "schema": "nexus.feishu_stale_document_binding.v1",
        "local_path": _relative_or_absolute(project, markdown_path),
        "document_id": str(current.get("document_id") or current.get("document_token") or ""),
        "url": str(current.get("url") or ""),
        "reason": str(classification.get("category") or exc.reason),
        "feishu_reason": exc.reason,
        "code": exc.code,
        "message": str(exc),
        "staled_at": now,
    }
    stale_documents = index.setdefault("stale_documents", [])
    if not isinstance(stale_documents, list):
        stale_documents = []
        index["stale_documents"] = stale_documents
    stale_documents.append(stale)
    _save_document_index(project, index)
    return stale


def _stale_document_ids(index: dict[str, object], key: str) -> set[str]:
    stale_documents = index.get("stale_documents")
    if not isinstance(stale_documents, list):
        return set()
    ids: set[str] = set()
    for item in stale_documents:
        if not isinstance(item, dict):
            continue
        if str(item.get("local_path") or "") != key:
            continue
        document_id = str(item.get("document_id") or "")
        if document_id:
            ids.add(document_id)
    return ids


def _find_historical_document_entry(project: Path, key: str, markdown_path: Path, *, stale_ids: set[str] | None = None) -> dict[str, str]:
    stale_ids = stale_ids or set()
    runs = project / ".data" / "runs"
    if not runs.exists():
        return {}
    candidates = sorted(runs.glob("*/tool_results/*.json"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("status") != "completed":
            continue
        if not _payload_mentions_markdown(payload, key, markdown_path):
            continue
        url = _first_docx_url(payload)
        document_id = _document_id_from_url(url)
        if document_id and document_id not in stale_ids:
            return {"document_id": document_id, "url": url, "source_artifact": str(path)}
    return {}


def _payload_mentions_markdown(payload: object, key: str, markdown_path: Path) -> bool:
    expected = {key, str(markdown_path)}
    if isinstance(payload, dict):
        for item_key, value in payload.items():
            if item_key == "markdown_path" and str(value) in expected:
                return True
            if _payload_mentions_markdown(value, key, markdown_path):
                return True
    if isinstance(payload, list):
        return any(_payload_mentions_markdown(item, key, markdown_path) for item in payload)
    return False


def _first_docx_url(payload: object) -> str:
    if isinstance(payload, dict):
        for key in ["url", "document_url", "preview_url"]:
            value = str(payload.get(key) or "")
            if "/docx/" in value:
                return value
        for value in payload.values():
            found = _first_docx_url(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _first_docx_url(item)
            if found:
                return found
    return ""


def _document_id_from_url(url: str) -> str:
    match = re.search(r"/docx/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else ""


def _document_key(project: Path, markdown_path: Path) -> str:
    return _relative_or_absolute(project, markdown_path)


def _relative_or_absolute(project: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project))
    except ValueError:
        return str(path)


def _imported_document_identity(imported: dict[str, object], config: FeishuConfig) -> tuple[str, str]:
    document_id = str(imported.get("document_token") or imported.get("document_id") or "")
    url = str(imported.get("url") or "")
    result = imported.get("result")
    if isinstance(result, dict):
        document_id = document_id or str(result.get("document_id") or result.get("obj_token") or result.get("token") or "")
        url = url or str(result.get("url") or result.get("preview_url") or result.get("document_url") or "")
    if not document_id:
        raise FeishuError("feishu_import_missing_document_id", "飞书 Markdown 导入成功响应缺少 document_id/token，无法建立本地文档索引。", response=imported)
    return document_id, url or document_url(document_id, config.doc_base_url)


def document_url(document_id: str, base_url: str = "") -> str:
    if base_url:
        return f"{base_url.rstrip('/')}/docx/{document_id}"
    return f"<FEISHU_URL_REDACTED>{document_id}"


def _read_required_file(path: Path, missing_reason: str, empty_reason: str) -> str:
    path = path.expanduser()
    if not path.exists():
        raise FeishuError(missing_reason, f"凭证文件不存在：{path}")
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        raise FeishuError(empty_reason, f"凭证文件为空：{path}")
    return text


def _file_exists_and_nonempty(path: Path) -> bool:
    try:
        return path.expanduser().exists() and bool(path.expanduser().read_text(encoding="utf-8", errors="ignore").strip())
    except OSError:
        return False


def _optional_path(value: object) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    return Path(text) if text else None


def _read_optional_file(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        path = path.expanduser()
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _normalize_resource_token(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    patterns = [
        r"/drive/folder/([A-Za-z0-9_-]+)",
        r"/docx/([A-Za-z0-9_-]+)",
        r"/docs/([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return text


def _post_json(path: str, payload: dict[str, object], *, headers: dict[str, str]) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=body, method="POST", headers={"Content-Type": "application/json; charset=utf-8", **headers})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(detail)
        except json.JSONDecodeError:
            data = {"code": exc.code, "msg": detail}
        if isinstance(data, dict):
            return data
        return {"code": exc.code, "msg": detail}


def _get_json(path: str, *, headers: dict[str, str]) -> dict[str, object]:
    req = urllib.request.Request(BASE_URL + path, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(detail)
        except json.JSONDecodeError:
            data = {"code": exc.code, "msg": detail}
        if isinstance(data, dict):
            return data
        return {"code": exc.code, "msg": detail}


def _delete_json(path: str, payload: dict[str, object], *, headers: dict[str, str]) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=body, method="DELETE", headers={"Content-Type": "application/json; charset=utf-8", **headers})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(detail)
        except json.JSONDecodeError:
            data = {"code": exc.code, "msg": detail}
        if isinstance(data, dict):
            return data
        return {"code": exc.code, "msg": detail}


def _post_multipart(
    path: str,
    *,
    fields: dict[str, object],
    file_field: str,
    file_name: str,
    file_bytes: bytes,
    content_type: str,
    headers: dict[str, str],
) -> dict[str, object]:
    boundary = "----nexus-feishu-" + uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(
        BASE_URL + path,
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(detail)
        except json.JSONDecodeError:
            data = {"code": exc.code, "msg": detail}
        if isinstance(data, dict):
            return data
        return {"code": exc.code, "msg": detail}


def _markdown_extension(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return suffix if suffix in {"md", "markdown"} else "md"


def _markdown_file_name(path: Path, title: str) -> str:
    base = re.sub(r"[\\/\r\n\t]+", " ", title or path.stem).strip() or path.stem or "operation-guide"
    extension = _markdown_extension(path)
    if base.lower().endswith(f".{extension}"):
        return base
    return f"{base}.{extension}"


def _import_type(value: str) -> str:
    text = (value or "docx").strip().lower()
    if text not in {"docx", "doc"}:
        raise FeishuError("feishu_import_type_unsupported", f"不支持的飞书导入目标类型：{value}")
    return text


def _import_task_status(data: dict[str, object]) -> str:
    payload = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    for container in [payload, payload.get("result") if isinstance(payload.get("result"), dict) else {}, data]:
        if not isinstance(container, dict):
            continue
        for key in ["job_status", "status", "state", "task_status"]:
            if key in container:
                return str(container.get(key)).strip().lower()
    return "unknown"


def _import_task_result(data: dict[str, object]) -> dict[str, object]:
    payload = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    token = str(result.get("token") or result.get("file_token") or result.get("document_id") or result.get("obj_token") or "")
    url = str(result.get("url") or result.get("preview_url") or result.get("document_url") or "")
    return {
        "token": token,
        "file_token": str(result.get("file_token") or ""),
        "document_id": str(result.get("document_id") or result.get("obj_token") or ""),
        "url": url,
        "raw_result": _redact(result),
    }


def _content_to_blocks(content: str) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# "):
            blocks.append(_text_block(line[2:], block_type=3))
        elif line.startswith(("- ", "* ")):
            blocks.append(_text_block(line[2:], block_type=12))
        else:
            blocks.append(_text_block(line, block_type=2))
    if not blocks:
        blocks.append(_text_block(content.strip() or "Feishu API smoke test", block_type=2))
    return blocks


def _chunks(items: list[dict[str, object]], size: int) -> list[list[dict[str, object]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _text_block(text: str, *, block_type: int) -> dict[str, object]:
    key = {2: "text", 3: "heading1", 12: "bullet", 13: "ordered"}.get(block_type, "text")
    return {
        "block_type": block_type,
        key: {
            "elements": [
                {
                    "text_run": {
                        "content": text[:1800],
                        "text_element_style": {},
                    }
                }
            ],
            "style": {},
        },
    }


def _int_code(data: dict[str, Any]) -> int | None:
    try:
        return int(data.get("code"))
    except (TypeError, ValueError):
        return None


def _redact(payload: object) -> object:
    sensitive_keys = {"tenant_access_token", "app_access_token", "user_access_token", "access_token", "token", "authorization", "app_secret", "secret"}
    if isinstance(payload, dict):
        redacted: dict[str, object] = {}
        for key, value in payload.items():
            if key.lower() in sensitive_keys or any(part in key.lower() for part in ["secret", "token", "authorization"]):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact(value)
        return redacted
    if isinstance(payload, list):
        return [_redact(item) for item in payload]
    return payload


def _next_checks(reason: str, code: int | None) -> list[str]:
    checks = [
        "确认飞书应用已发布版本，权限变更已生效。",
        "确认开通 docx/drive/docs 相关权限。",
        "确认企业自建应用 app_id/app_secret 来自同一个应用。",
        "如果传入 folder_token/document_id，确认应用已加入目标文件夹/文档权限范围。",
    ]
    if reason in {"app_id_file_missing", "app_secret_file_missing", "app_id_empty", "app_secret_empty"}:
        checks.insert(0, "检查 FEISHU_APP_ID_PATH / FEISHU_APP_SECRET_PATH 或默认凭证文件路径。")
    if reason == "feishu_target_missing":
        checks.insert(0, "为项目配置 folder_token 或 doc_token：folder_token 用于创建新文档，doc_token/document_id 用于追加已有文档。")
    if reason == "feishu_import_requires_folder_token":
        checks.insert(0, "Markdown 文件导入需要 folder_token 作为目标文件夹；如果只有 doc_token，只能追加 blocks，不能保真导入 .md。")
    if reason == "feishu_upload_markdown_failed":
        checks.insert(0, "Markdown 导入上传需要 docs:document.media:upload，或飞书错误响应中列出的 docs:doc / drive:drive 等上传权限之一；开通权限后发布应用版本并等待生效。")
    if reason == "feishu_create_import_task_failed":
        checks.insert(0, "Markdown 创建导入任务需要 docs:document:import，或飞书错误响应中列出的 drive:drive 等导入权限之一；开通权限后发布应用版本并等待生效。")
    if reason == "feishu_upload_markdown_file_failed":
        checks.insert(0, "Markdown 云空间文件上传需要 drive:file:upload、drive:file 或 drive:drive 权限，并且应用需拥有目标 folder_token 的资源权限。")
    if reason == "feishu_rebuild_requires_folder_token":
        checks.insert(0, "旧飞书文档绑定已失效；自动重建需要可用 folder_token。")
    if reason == "feishu_resource_permission_missing":
        checks.insert(0, "飞书应用缺少目标文档/文件夹的资源权限；把应用加入资源权限范围，或配置可访问的 folder_token 让 Nexus 自动重建。")
    if reason == "feishu_api_scope_missing":
        checks.insert(0, "飞书应用缺少 API 权限或权限版本未发布；开通并发布 docx/docs/drive 相关权限后重试。")
    if code:
        checks.append(f"飞书错误码：{code}，请在飞书开放平台错误码文档中查询。")
    return checks
