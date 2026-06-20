from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from nexus.github_sync import load_config


GUIDE_REL = Path("docs/operation-guide.md")
VALID_TARGETS = {"auto", "nexus", "verix", "project"}


def write_operation_guide(project: Path, *, target: str = "auto") -> dict[str, object]:
    project = project.expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)
    normalized_target = _normalize_target(project, target)
    path = project / GUIDE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    config = load_config(project) or {}
    content = _guide_markdown(project, normalized_target, config)
    path.write_text(content, encoding="utf-8")
    return {
        "schema": "nexus.operation_guide.v1",
        "status": "completed",
        "reason": "operation_guide_written",
        "path": str(path),
        "project_path": str(project),
        "target": normalized_target,
        "private_repo": str(config.get("private_repo") or ""),
        "public_repo": str(config.get("public_repo") or ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _normalize_target(project: Path, target: str) -> str:
    value = (target or "auto").strip().lower()
    if value not in VALID_TARGETS:
        raise ValueError(f"unsupported guide target: {target}")
    if value != "auto":
        return value
    if project.name == "nexus":
        return "nexus"
    if project.name == "verix":
        return "verix"
    return "project"


def _guide_markdown(project: Path, target: str, config: dict[str, object]) -> str:
    private_repo = str(config.get("private_repo") or _default_private_repo(project, target))
    public_repo = str(config.get("public_repo") or _default_public_repo(project, target))
    payload = {
        "schema": "nexus.operation_guide_context.v1",
        "project": project.name,
        "target": target,
        "private_repo": private_repo,
        "public_repo": public_repo,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return f"""# {project.name} 整体操作指南

目标类型：`{target}`

## 定位

{_overview(project, target)}

## 项目意图说明

{_intent_section(project)}

## Skill 入口

```text
{_skill_commands(project, target)}
```

## 初始化与日常更新

- 新项目初始化应创建真实项目目录，并在默认情况下初始化 git。
- GitHub private 同步是默认能力；只有指令显式写“不同步 GitHub”、“跳过 GitHub 同步”、`no-github-sync`，或 CLI 使用 `--no-github-sync` 时才跳过。
- Feishu 是操作指南、说明文档和初始化/更新记录的发布通道；初始化项目时默认写入 `docs/feishu-records.md` 并同步到飞书，只有指令显式写“不同步飞书”、“跳过飞书”、`no-feishu-sync`，或 CLI 使用 `--no-feishu-sync` 时才跳过。
- 激活 `$nexus-workflow` 后，每次对项目内容产生更新，都应默认触发飞书自动同步：优先更新已有线上文档，没有对应绑定时才新建，不能把同一份说明拆成多份重复文件。
- 如果飞书配置不可用，应 blocked 到 setup/doctor，不应假装写入成功；本地记录和本地指南仍需保留为 artifact。
- GitHub public 发布永远需要显式确认，不能跟随 private 自动发布。

## GitHub 同步

private 仓库：`{private_repo}`

public 仓库：`{public_repo}`

GitHub CLI 未认证或 token 失效时，workflow 使用原生 GitHub CLI 浏览器登录：

```bash
gh auth login --web --clipboard --skip-ssh-key --git-protocol https --hostname github.com
```

用户手动完成邮箱、密码、2FA、CAPTCHA 和授权确认。workflow 不读取本地邮箱、密码、token、cookie、浏览器 profile、SSH key、`.env` 或 2FA/CAPTCHA 内容。

### GitHub 登录与同步经验

- 如果 `gh auth login --web` 在 `https://github.com/login/device/code` 阶段返回 EOF，不要立即让用户重复登录；workflow 应先复查 `gh auth status --hostname github.com`，因为设备授权可能已经成功写入 GitHub CLI 状态。
- 如果登录启动失败包含代理、`127.0.0.1`、`operation not permitted`、`dial tcp` 或 EOF，优先复用 recovery playbook 的 `retry_without_proxy_and_debug_api` 方向：绕开代理并开启 `GH_DEBUG=api` 后再次发起 GitHub CLI 官方 web/device 登录。
- 如果 GitHub 仓库创建失败，先检查是否为同名 private/public 仓库已存在；若 `gh repo view` 可访问，应复用现有仓库、补齐 remote，并重试原 bootstrap/sync，不要默认更换仓库名。
- 如果 `git push` 看似卡住或失败，先收口检查本地 commit、remote、GitHub 仓库可访问性和 `gh auth setup-git --hostname github.com`；确认凭证桥接可用后再重试原 private/public sync。
- GitHub 登录、建仓、凭证桥接、push、public staging/secret scan 任一环节失败时，应先查项目 `.nexus/recovery-playbook.json` 和内置经验；命中经验先按对应方向尝试或发起提权，仍失败才调用高精度恢复模块。
- 如果没有精确命中的经验，高精度恢复模块应读取相关经验库条款和历史操作结果作为参考证据，先判断经验是否与当前情景有关、是否仍有效、是否值得尝试；经验无关时应直接丢弃并自主规划新路线，不能被经验库限制住。

public 发布必须先生成 staging，并通过 secret scan。`.env`、token、key、cookie、apikey、本地运行数据、`.data`、`.codex`、`.agents` 等不能进入 public。

## Feishu 同步

- 本指南的主文件是 `docs/operation-guide.md`。
- 同步到飞书时，应优先把本地 Markdown 文件上传并通过 Drive import task 导入为飞书云文档，以保留 Markdown 标题、列表、代码块等结构。
- Markdown 导入需要 folder_token 作为目标文件夹；仅配置 doc_token 时不能保真导入 `.md`。
- 飞书同步应维护 `.nexus/feishu-documents.json`，按本地 Markdown 路径绑定线上 docx；有效绑定存在时更新同一文档，不重复创建。
- 初始化和日常更新记录统一写入 `docs/feishu-records.md`，不应为每一次记录生成一份独立飞书文档。
- 如果绑定文档已删除、失效或资源不可访问，且 folder_token 可用，workflow 应在同一条同步指令内自动标记旧绑定为 stale、重新导入新文档并更新绑定。
- 只有缺凭证、缺 folder_token、缺 API 权限、缺资源权限或网络不可用等真实外部条件时才 blocked；blocked 的下一步提示必须指向真实缺口，并回到原同步指令重试。
- 缺少 `.nexus/feishu.json`、app_id/app_secret、folder_token、`docs:document.media:upload`/Drive 导入上传权限或文件夹资源权限时，应返回明确 blocked reason。
- Feishu 配置和权限问题由 `feishu setup` / `feishu doctor` 处理，不使用 mock 替代。

## 验收边界

- GitHub private/bootstrap/auth/secret scan 已作为基础能力验证；日常变更不重复跑完整 GitHub private E2E。
- Nexus 自身指南同步飞书可以作为本机真实 Feishu 自同步测试。
- Verix 飞书同步、Nexus 初始化新项目后的飞书同步、public 发布由后续 `$` 指令验收。

## 机器可读上下文

```json
{json.dumps(payload, ensure_ascii=False, indent=2)}
```
"""


def _overview(project: Path, target: str) -> str:
    if target == "nexus":
        return "Nexus 是 Codex-first workflow kernel，负责调研、初始化项目、维护记录板、接入 GitHub private/public 同步和 Feishu 操作指南发布。"
    if target == "verix":
        return "Verix 是端到端意图审计与测试 workflow，负责审计 AI 生成项目是否满足用户意图，并维护自身 GitHub 同步和操作指南发布入口。"
    return f"`{project.name}` 是由 Nexus 管理或初始化的项目，应拥有可运行代码、git 基线、GitHub private 默认同步和整体操作指南。"


def _intent_section(project: Path) -> str:
    original = project / "docs" / "intent" / "original-requirement.md"
    normalized = project / "docs" / "intent" / "normalized-requirement.md"
    overview = project / "docs" / "project-overview.md"
    lines = [
        "- 原始意图需求固定存放于 `docs/intent/original-requirement.md`。",
        "- 完整规范化需求主文档固定存放于 `docs/intent/normalized-requirement.md`。",
        "- 项目说明文档固定存放于 `docs/project-overview.md`。",
        "- 整体操作指南固定存放于 `docs/operation-guide.md`。",
        "- 机器可读索引固定存放于 `.nexus/project-intent.json`。",
    ]
    if original.exists():
        lines.extend(["", "### 原始意图摘录", "", _read_excerpt(original)])
    if normalized.exists():
        lines.extend(["", "### 规范化意图摘录", "", _read_excerpt(normalized)])
    if overview.exists():
        lines.extend(["", "### 项目说明摘录", "", _read_excerpt(overview)])
    return "\n".join(lines)


def _read_excerpt(path: Path, *, limit: int = 1600) -> str:
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n...（已截断，完整内容见对应意图文档）"


def _skill_commands(project: Path, target: str) -> str:
    if target == "nexus":
        return "\n".join(
            [
                f"$nexus-workflow 生成 Nexus 整体操作指南，项目路径 {project}",
                f"$nexus-workflow 同步 Nexus 整体操作指南到飞书，项目路径 {project}",
                f"$nexus-workflow 执行 Nexus 自同步：生成并同步 Nexus 整体操作指南到飞书，项目路径 {project}",
                f"$nexus-workflow 同步 Nexus 到 GitHub public，项目路径 {project}，确认 public 发布",
            ]
        )
    if target == "verix":
        return "\n".join(
            [
                f"$verix-workflow 生成 Verix 整体操作指南，项目路径 {project}",
                f"$verix-workflow 同步 Verix 整体操作指南到飞书，项目路径 {project}",
                f"$verix-workflow 同步 Verix 到 GitHub private，项目路径 {project}",
                f"$verix-workflow 同步 Verix 到 GitHub public，项目路径 {project}，确认 public 发布",
            ]
        )
    return "\n".join(
        [
            f"$nexus-workflow 为项目 {project} 生成整体操作指南",
            f"$nexus-workflow 同步项目 {project} 的整体操作指南到飞书",
            f"$nexus-workflow 将项目 {project} 同步到 GitHub public，确认 public 发布",
        ]
    )


def _default_private_repo(project: Path, target: str) -> str:
    if target in {"nexus", "verix"}:
        return f"YaofeiHe/{target}"
    return "YaofeiHe/<project>"


def _default_public_repo(project: Path, target: str) -> str:
    if target in {"nexus", "verix"}:
        return f"YaofeiHe/{target}-public"
    return "YaofeiHe/<project>-public"
