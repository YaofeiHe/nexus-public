# nexus Recovery Records

## github / login-and-sync-history

- time: `2026-06-03`
- source: `<FORGE_ROOT>/github_login_sync_history_20260603.md`
- scope: GitHub CLI 登录、bootstrap、private/public sync。

### 已沉淀的错误类型

- `LOGIN_START_FAILED` + `Post "https://github.com/login/device/code": EOF`：按 device-code POST EOF 处理，先复查 `gh auth status --hostname github.com`，再考虑去代理 + `GH_DEBUG=api` 重试官方 web/device 登录。
- `LOGIN_START_FAILED` + proxy / `127.0.0.1` / `operation not permitted` / `dial tcp`：优先使用 `retry_without_proxy_and_debug_api` 方向。
- `github_repo_create_failed`：优先判断是否为同名仓库已存在；若 `gh repo view` 可访问，则复用现有 private/public 仓库并补齐 remote。
- `gh_git_auth_setup_failed`：先确认 GitHub CLI 已登录，再运行 `gh auth setup-git --hostname github.com`。
- `git_push_failed` 或 push 看似卡住：先检查本地 commit、remote、repo 可访问性和 gh git 凭证桥接，再重试原 sync。

### 操作边界

- GitHub 登录流程只启动 GitHub CLI 官方 web/device 授权，用户本人完成密码、2FA、CAPTCHA 和网页授权。
- 不读取 token、cookie、浏览器 profile、SSH key、`.env`、密码文件或 2FA/CAPTCHA 内容。
- 需要访问 GitHub、配置 credential helper、设置 remote 或 push 时，恢复模块必须说明动作、服务、路径和风险，并通过提权/审批边界执行。

### 复用策略

- GitHub 链路失败时先查项目 `.nexus/recovery-playbook.json` 和内置经验。
- 命中经验后先按对应动作尝试或发起提权。
- 经验动作仍失败或没有匹配项时，再调用高精度恢复模块生成新的恢复计划。
- 成功恢复后生成 `recovery_playbook_write_required` 审批；审批后写入项目 playbook，避免类似问题反复调用模型。
