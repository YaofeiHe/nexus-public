# nexus 整体操作指南

目标类型：`nexus`

## 定位

Nexus 是 Codex-first workflow kernel，负责调研、初始化项目、维护记录板、接入 GitHub private/public 同步和 Feishu 操作指南发布。

## 项目意图说明

- 原始意图需求固定存放于 `docs/intent/original-requirement.md`。
- 完整规范化需求主文档固定存放于 `docs/intent/normalized-requirement.md`。
- 项目说明文档固定存放于 `docs/project-overview.md`。
- 整体操作指南固定存放于 `docs/operation-guide.md`。
- 机器可读索引固定存放于 `.nexus/project-intent.json`。

### 规范化意图摘录

# Nexus Normalized Requirement

Nexus must deliver real end-to-end workflow results, not mock kernels, offline demos, or surface-level substitutes. If a requested workflow cannot be completed because of permissions, credentials, external services, security boundaries, or architecture limits, Nexus must block with the concrete reason and next decision point.

For GitHub public sync, the output must be a reusable public artifact. The public repository must contain the required runtime code and public-safe data, exclude private/runtime/secrets paths, and pass real staging validation before push. Public staging must sanitize private metadata such as local absolute paths, private repo names, Feishu document URLs, Nexus run ids, and internal artifact paths, then block remaining secrets or private metadata by default. Validation includes required path checks, install/import checks, CLI smoke checks, tests configured for the public artifact, and the same checks against a fresh copied download.

For self-sync, Nexus must avoid leaving Feishu autosync writeback only on disk. After Feishu successfully updates local records or document bindings, Nexus must run one additional GitHub private sync without recursively triggering Feishu again.

For supplemental initialization, Nexus must preserve already meaningful project documentation. It must inspect intent docs, project overview, operation guide, and `.nexus/project-intent.json` before writing. Complete existing documents are left unchanged, incomplete non-empty documents are supplemented without deleting user text, and only missing, em

...（已截断，完整内容见对应意图文档）

### 项目说明摘录

# Nexus Project Overview

Nexus is a Codex-first workflow kernel for project research, initialization, GitHub private/public synchronization, Feishu guide publishing, recovery, and reusable workflow extraction.

## Public Sync Responsibility

Nexus public sync produces a public artifact that is safe to publish and usable by downstream users. It is not only a document snapshot. A public sync must generate staging, enforce denylist and sanitization, block secrets and private metadata by default, validate required runtime paths, and prove that both the staged artifact and a fresh copied download can be installed, imported, smoke-tested, and tested before public push.

Public push remains explicitly confirmed by the user, but confirmation is only one gate. Validation failure, missing runtime code, private-only data requirements, GitHub auth failures, repo failures, or push failures must block publication with structured artifacts and concrete next steps.

Self-sync closes the private audit trail after Feishu writes local records: GitHub private sync runs before Feishu and once again after successful Feishu autosync, without recursively invoking Feishu during that second private sync.

Supplemental initialization organizes an existing project; it does not reset the project to an empty initial state. Existing complete intent, overview, and operation-guide documents must be preserved. Incomplete non-empty documents may receive appended supplemental sections, while missing or placeholder documents may be created from current project intent.

## Recovery Responsibility

When Nexus l

...（已截断，完整内容见对应意图文档）

## Skill 入口

```text
$nexus-workflow 生成 Nexus 整体操作指南，项目路径 <PROJECT_ROOT>
$nexus-workflow 同步 Nexus 整体操作指南到飞书，项目路径 <PROJECT_ROOT>
$nexus-workflow 执行 Nexus 自同步：生成并同步 Nexus 整体操作指南到飞书，项目路径 <PROJECT_ROOT>
$nexus-workflow 同步 Nexus 到 GitHub public，项目路径 <PROJECT_ROOT>，确认 public 发布
```

## 初始化与日常更新

- 新项目初始化应创建真实项目目录，并在默认情况下初始化 git。
- GitHub private 同步是默认能力；只有指令显式写“不同步 GitHub”、“跳过 GitHub 同步”、`no-github-sync`，或 CLI 使用 `--no-github-sync` 时才跳过。
- Feishu 是操作指南、说明文档和初始化/更新记录的发布通道；初始化项目时默认写入 `docs/feishu-records.md` 并同步到飞书，只有指令显式写“不同步飞书”、“跳过飞书”、`no-feishu-sync`，或 CLI 使用 `--no-feishu-sync` 时才跳过。
- 激活 `$nexus-workflow` 后，每次对项目内容产生更新，都应默认触发飞书自动同步：优先更新已有线上文档，没有对应绑定时才新建，不能把同一份说明拆成多份重复文件。
- 如果飞书配置不可用，应 blocked 到 setup/doctor，不应假装写入成功；本地记录和本地指南仍需保留为 artifact。
- GitHub public 发布永远需要显式确认，不能跟随 private 自动发布。

## GitHub 同步

private 仓库：`<PRIVATE_REPO>`

public 仓库：`YaofeiHe/nexus-public`

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
{
  "schema": "nexus.operation_guide_context.v1",
  "project": "nexus",
  "target": "nexus",
  "private_repo": "<PRIVATE_REPO>",
  "public_repo": "YaofeiHe/nexus-public",
  "updated_at": "2026-06-20T14:39:50.106210+00:00"
}
```
