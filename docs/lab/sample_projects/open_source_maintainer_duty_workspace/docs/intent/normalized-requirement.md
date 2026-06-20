# 规范化需求

## 项目目标

建立一个开源维护者值班工作区，支持维护者在值班期间进行 issue 分级、release blocker 判断、回归证据收集、交接、public README 发布检查和事故恢复记录沉淀。

## 目标用户

- 值班维护者：处理 issue、判断优先级、记录当前状态。
- release manager：确认 blocker、回归证据和公开发布材料。
- 下一班维护者：读取交接记录和未完成事项。
- 安全或项目负责人：判断未确认漏洞细节是否允许公开。

## 核心对象

- issue duty item：issue 编号、类型、严重度、public 状态、private notes、owner、下一步。
- release blocker：阻断原因、关联 issue、回归证据、解除条件。
- regression evidence：测试命令、结果、环境、证据链接、是否可公开。
- handoff packet：当前风险、待办、决策、下一班入口。
- public release check：README/changelog 是否含敏感内容、是否可安装或可读取。
- recovery record：失败签名、诊断、修复、测试、复用条件。

## 必须支持的 workflow

1. issue triage：把 incoming issue 分为 now/next/later/blocker/security-sensitive。
2. release blocker review：确认阻断项和解除条件。
3. regression evidence capture：记录回归测试证据，不能只写“测试通过”。
4. maintainer handoff：生成交接包，说明下一位维护者先读什么。
5. public README release check：公开发布前做敏感信息和可读性检查。
6. recovery record update：失败恢复后写入可复用经验。

## 验收标准

- 原始需求、规范化需求、需求追踪和 reference materials 都存在且互相引用。
- `indexes/duty-board.json` 包含 issue、blocker、regression、handoff 和 recovery 记录。
- `indexes/public-private-boundary.json` 明确定义 public allowed、private only 和 scan rules。
- `schemas/maintainer-duty.schema.json` 定义值班记录必填字段。
- `scripts/validate_project.py` 检查 workflow、schema、索引、公私边界和敏感内容。

## 明确不做

- 不调用 GitHub API。
- 不发布真实 release。
- 不生成安全公告。
- 不用单一项目名或固定 issue 样例作为 Nexus 通过条件。

## 未确认问题

- 项目是否已有安全披露政策。
- README 发布检查是否需要 fresh clone 或安装验证。
- 回归测试命令是否来自仓库脚本、CI 还是维护者手动命令。
- 恢复记录是否要同步到项目 `.nexus/recovery-playbook.json`。
