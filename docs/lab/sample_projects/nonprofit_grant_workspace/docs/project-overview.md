# 项目说明

`nonprofit_grant_workspace` 是一个资助申请协作样例工作区。它把用户的初始想法拆成三条可执行链路：资助方 intake、申请包构建、提交后复盘。

## 当前可用状态

- 已有人读需求说明：原始需求、规范化需求、需求追踪和材料索引。
- 已有三条 workflow：`workflows/funder-intake.md`、`workflows/application-package-build.md`、`workflows/deadline-feedback-review.md`。
- 已有结构化数据定义：`schemas/grant-record.schema.json`。
- 已有样例索引：`indexes/grant-pipeline.json`。
- 已有本地验证入口：`python3 scripts/validate_project.py`。

## 模块协作方式

- `docs/intent/` 保存用户输入和 Codex 对需求的理解。
- `docs/requirement-trace.md` 说明每条用户要求落到哪些文件、workflow 或字段。
- `docs/source-material-index.md` 记录样例初始化时使用或应使用的来源材料。
- `workflows/` 面向实际执行者，说明每次更新要改哪些文件、谁负责、如何验收。
- `schemas/` 定义结构化字段，避免每个资助方记录随意命名。
- `indexes/` 保存可校验的样例记录，展示预算、证据和复盘如何连接。

## 公开和私有边界

公开材料可以包含项目使命、申请摘要、公开资助方名称、通用预算类别和已授权受益人统计。内部材料应保留在私有工作区，包括联系人、未授权受益人故事、精细预算来源、本地路径、token、私有仓库地址和未确认反馈。
