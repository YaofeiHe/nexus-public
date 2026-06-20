# Workflow: 整改验证

## 触发条件

整改 owner 报告行动项完成，或周报前需要确认阻断项状态。

## 步骤

1. 找到对应 incident 的 `remediation_actions`。
2. 检查每个 action 的 owner、due_date、verification_method 和 evidence。
3. 没有验证证据时，状态保持 `pending` 或 `blocked`。
4. 证据满足标准后才能改为 `verified`。
5. 如果整改无效，记录 reopen condition 并回到根因复盘。
6. 运行 `python3 scripts/validate_project.py`。

## 验收

- verified 行动项都有证据。
- blocked 行动项有原因和下一步。
- 验证状态能被周报引用。
