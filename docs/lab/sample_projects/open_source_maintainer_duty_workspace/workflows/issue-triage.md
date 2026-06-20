# Workflow: Issue 分级

## 触发条件

出现新的 issue、PR、用户报告或维护者手动补充一条待处理事项。

## 步骤

1. 在 `docs/reference-materials.md` 或 `docs/search-log.md` 记录来源。
2. 在 `indexes/duty-board.json` 新增或更新 issue item。
3. 填写 `issue_id`、`title`、`classification`、`severity`、`public_status`、`owner` 和 `next_action`。
4. 如果涉及未确认漏洞，`public_status` 必须为 `private-sensitive` 或 `withheld`。
5. 关联 release blocker 或 regression evidence。
6. 运行 `python3 scripts/validate_project.py`。

## 验收

- 每条 issue 都有分类、owner 和下一步。
- security-sensitive issue 不进入 public material。
- blocker issue 能被 release 审查 workflow 找到。
