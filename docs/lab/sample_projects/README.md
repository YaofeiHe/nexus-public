# Codex 手工高质量样例项目

本目录保存人工编写的样例项目，用于后续抽取 Nexus 初始化、调研、项目构建、验证、同步和恢复的通用质量基准。这些样例不是 Nexus 自动输出，也不是要求 Nexus 按固定项目名生成的模板。

## 使用边界

- 样例只能作为质量参照：评测应抽取通用能力，例如原始意图保存、需求规范化、材料读取记录、workflow、schema、验证脚本、公开/私有边界和恢复记录。
- 禁止在 Nexus 实现中按 `nonprofit_grant_workspace`、`field_incident_review_workspace`、`open_source_maintainer_duty_workspace` 或这些样例的固定文件内容硬编码分支。
- 后续测试必须替换项目名、领域、输入材料、路径和失败条件，确认 Nexus 能理解新的用户意图，而不是复制本目录结构。
- 样例中的 validator 只验证该样例自身质量，不代表 Nexus 运行时可以只检查文件存在。

## 样例清单

| 样例 | 领域 | 质量重点 |
|---|---|---|
| `nonprofit_grant_workspace` | 非营利组织资助申请 | 资助方要求比较、预算和证据追踪、截止日期、提交后复盘 |
| `field_incident_review_workspace` | 现场事故复盘 | 事故 intake、根因分析、整改验证、负责人和领导周报 |
| `open_source_maintainer_duty_workspace` | 开源维护者值班 | issue 分级、release blocker、回归证据、交接、公私发布边界 |

## 评测建议

评测 agent 使用这些样例时，应先读每个样例的 `docs/intent/original-requirement.md`、`docs/intent/normalized-requirement.md`、`docs/requirement-trace.md`、`docs/source-material-index.md` 或 `docs/reference-materials.md`、`docs/operation-guide.md` 和 `scripts/validate_project.py`。通过标准不是目录形状相似，而是新项目能把本轮用户输入映射到本轮产物，并用本轮 validator 或等价校验证明可用。
