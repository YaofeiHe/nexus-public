# Workflow: 事故 Intake

## 触发条件

现场出现异常、服务中断、安全事件、质量事故或用户要求记录一类同类事件。

## 步骤

1. 在 `docs/source-material-index.md` 登记新增来源，例如现场记录、工单、日志或访谈。
2. 在 `indexes/incident-register.json` 新增 incident 记录。
3. 填写 `incident_id`、`reported_at`、`site`、`severity`、`initial_impact` 和 `current_status`。
4. 只记录事实，不在 intake 阶段写最终根因。
5. 标出 immediate actions 和需要升级的风险。
6. 运行 `python3 scripts/validate_project.py`。

## 验收

- 事故能被唯一识别。
- 初始影响和当前状态清楚。
- 未确认事实标注为 `pending` 或写入 open questions。
