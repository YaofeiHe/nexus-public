# Workflow: 每周领导摘要

## 触发条件

每周汇总事故、整改和风险，或领导要求了解当前状态。

## 步骤

1. 读取 `indexes/incident-register.json` 中本周新增、关闭、blocked 和升级事件。
2. 汇总公开可说的事实：数量、状态、下一次更新时间。
3. 汇总内部风险：未验证根因、延期整改、需要领导决策的问题。
4. 明确哪些内容不得进入公开摘要。
5. 将摘要写回事故记录的 `leadership_summary`。
6. 运行 `python3 scripts/validate_project.py`。

## 验收

- 摘要能让领导看到当前阶段和阻断原因。
- 内部敏感信息没有进入 public summary。
- 每个 blocked 项都有 owner 和下一步动作。
