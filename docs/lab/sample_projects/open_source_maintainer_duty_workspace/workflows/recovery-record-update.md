# Workflow: 恢复记录更新

## 触发条件

测试、发布、回归或维护脚本失败，并且维护者完成诊断和修复。

## 步骤

1. 记录 `failure_signature`，说明失败如何识别。
2. 记录 diagnosis，不只写“已修复”。
3. 记录 fix summary 和修改范围。
4. 记录 test evidence，说明修复如何验证。
5. 写明 reuse condition：什么情况下下一次可以先复用这条经验。
6. 运行 `python3 scripts/validate_project.py`。

## 验收

- 恢复记录能被下一次相似失败检索。
- 诊断、修改和测试都有证据。
- 复用条件不是盲目套用旧修复。
