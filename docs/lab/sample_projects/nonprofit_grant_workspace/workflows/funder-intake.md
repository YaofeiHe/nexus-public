# Workflow: 资助方 Intake

## 触发条件

发现一个新的资助方计划，或用户补充了一份资助方申请指南。

## 步骤

1. 在 `docs/source-material-index.md` 记录材料来源、读取状态和提取目标。
2. 在 `indexes/grant-pipeline.json` 新增一条 funder 记录。
3. 填写 `funder_id`、`program_name`、`eligibility_rules`、`required_materials`、`deadline`、`owner` 和 `risk_notes`。
4. 将每个材料要求映射到证据或草稿位置；没有材料时写入 `gap`，不能留空。
5. 更新 `docs/requirement-trace.md`，确认新字段仍对应用户对资助方比较的要求。
6. 运行 `python3 scripts/validate_project.py`。

## 验收

- 新资助方能与已有记录比较。
- 资格条件、材料清单、截止日期和负责人都不为空。
- 未确认规则明确标注为 `待确认`，不写成事实。
