# Workflow: 申请包构建

## 触发条件

用户决定推进某个资助方申请，或已有草稿、预算、证据需要合并成可审查申请包。

## 步骤

1. 读取目标资助方在 `indexes/grant-pipeline.json` 中的 `required_materials`。
2. 为每项材料补充 `evidence_links`、`budget_links`、`draft_status` 和 `owner`。
3. 对预算条目记录金额、费用类别、来源依据和公开等级。
4. 对受益人证据记录授权状态和引用位置，未授权内容不得进入公开材料。
5. 在提交前运行 `python3 scripts/validate_project.py`。

## 验收

- 每个 required material 至少有 `ready`、`gap` 或 `blocked` 状态。
- 每个预算数字都能回到来源说明。
- 每个证据条目都标明是否可公开。
- 申请包缺口能直接转成下一步 action。
