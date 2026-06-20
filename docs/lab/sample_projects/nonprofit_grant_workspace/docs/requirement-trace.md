# 需求追踪

| ID | 用户原始要求 | 规范化理解 | 落地文件 |
|---|---|---|---|
| NPG-R01 | 面向非营利组织项目申请的工作区 | 工作区围绕资助方、申请包、预算、证据和复盘组织 | `docs/project-overview.md`, `indexes/grant-pipeline.json` |
| NPG-R02 | 比较不同资助方的要求 | 资助方 intake 必须拆解资格、材料、金额范围、截止日期和风险 | `workflows/funder-intake.md`, `schemas/grant-record.schema.json` |
| NPG-R03 | 知道每份材料还缺什么证据 | 申请包构建时维护 requirement-to-evidence 映射和缺口状态 | `workflows/application-package-build.md`, `indexes/grant-pipeline.json` |
| NPG-R04 | 预算数字来自哪里 | 预算条目必须记录金额、费用类别、来源依据和公开等级 | `schemas/grant-record.schema.json`, `indexes/grant-pipeline.json` |
| NPG-R05 | 截止日期前谁要做什么 | 每个资助方记录需要 owner、deadline、next_actions 和 blocker | `workflows/deadline-feedback-review.md`, `indexes/grant-pipeline.json` |
| NPG-R06 | 提交后记录反馈、拒绝理由和下一次窗口 | 复盘记录成为申请包状态的一部分，不另散在聊天记录里 | `workflows/deadline-feedback-review.md`, `indexes/grant-pipeline.json` |
| NPG-R07 | 保留证据来源 | source index 记录材料来源、读取状态和影响范围 | `docs/source-material-index.md` |
| NPG-R08 | 本地验证脚本检查关键文件 | validator 检查文档、schema、索引、workflow 和敏感边界 | `scripts/validate_project.py` |

## 追踪口径

本追踪表要求每个需求至少落到一个人读文件和一个可执行或可校验位置。后续如果新增资助方或复盘流程，应新增 `NPG-Rxx` 记录，而不是只修改 overview。
