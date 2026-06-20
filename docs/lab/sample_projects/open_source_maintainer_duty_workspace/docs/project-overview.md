# 项目说明

`open_source_maintainer_duty_workspace` 是一个开源维护者值班样例工作区。它把值班期间的 issue、release blocker、回归证据、交接、公开发布检查和恢复经验放在同一套可追踪结构中。

## 当前可用状态

- 已保存原始需求和规范化需求。
- 已建立需求追踪和 reference materials。
- 已有五条维护 workflow 和两个结构化索引。
- 已有本地验证入口：`python3 scripts/validate_project.py`。

## 模块协作方式

- `docs/intent/` 保存用户意图和需求理解。
- `docs/requirement-trace.md` 把用户原话映射到 workflow、schema 和 index。
- `docs/reference-materials.md` 记录初始化时使用或应读取的维护材料。
- `workflows/issue-triage.md` 和 `workflows/release-blocker-review.md` 负责值班判断。
- `workflows/regression-evidence-capture.md` 负责测试证据。
- `workflows/maintainer-handoff.md` 负责交接。
- `workflows/public-readme-release-check.md` 负责公开发布边界。
- `workflows/recovery-record-update.md` 负责恢复经验沉淀。

## 公开和私有边界

公开材料可以包含已确认 issue 状态、已发布修复、公开测试命令、README/changelog 摘要和无敏感细节的恢复说明。私有记录包含未确认漏洞细节、维护者内部讨论、private repo 路径、token、未公开 exploit、未验证回归失败和人员排班细节。
