# Workflow: 回归证据记录

## 触发条件

修复 issue、解除 release blocker、发布前验证或恢复后回归。

## 步骤

1. 记录测试命令、执行环境、结果和证据引用。
2. 把证据连接到 issue 或 blocker。
3. 如果测试失败，记录 failure signature 并进入恢复记录 workflow。
4. 只允许公开不含敏感路径、token 和未确认漏洞细节的证据摘要。
5. 运行 `python3 scripts/validate_project.py`。

## 验收

- 回归证据不是一句“测试通过”。
- 每条证据能复查命令和结果。
- failed evidence 会触发下一步动作，而不是被隐藏。
