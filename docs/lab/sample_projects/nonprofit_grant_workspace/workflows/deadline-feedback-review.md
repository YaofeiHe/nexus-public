# Workflow: 截止日期与反馈复盘

## 触发条件

进入提交倒计时、完成提交、收到资助方反馈，或需要为下一轮申请做复盘。

## 步骤

1. 检查 `deadline`、`submission_status`、`owner` 和 `next_actions`。
2. 如果提交前仍有 `blocked` 材料，写明阻断原因和负责人。
3. 提交后记录 `submitted_at`、`decision_status`、`feedback_summary` 和 `next_window`。
4. 如果被拒，记录拒绝理由、需补充证据和下一次申请窗口。
5. 更新 `docs/search-log.md`，说明反馈来自哪里。
6. 运行 `python3 scripts/validate_project.py`。

## 验收

- 用户能从一条记录看到申请前状态和申请后复盘。
- 下一轮行动有负责人和时间窗口。
- 未确认反馈不进入公开材料。
