# 项目说明

`field_incident_review_workspace` 是现场事故复盘样例工作区。它强调事实记录、证据来源、根因假设、整改验证和领导摘要之间的追踪关系。

## 当前可用状态

- 已保存原始需求和规范化需求。
- 已记录来源材料索引、检索计划和检索结果。
- 已定义四条 workflow：事故 intake、根因复盘、整改验证、领导周报。
- 已定义事故复盘 schema，并提供一条可校验事故记录。
- 已提供本地验证入口：`python3 scripts/validate_project.py`。

## 模块协作方式

- `docs/intent/` 保存用户原话和需求理解。
- `docs/source-material-index.md` 记录每份来源材料如何影响工作区。
- `workflows/incident-intake.md` 负责事实和初始影响。
- `workflows/root-cause-review.md` 负责假设、证据、反证和未确认点。
- `workflows/remediation-verification.md` 负责行动项关闭标准。
- `workflows/weekly-leadership-summary.md` 负责向领导汇报状态，不泄漏内部调查细节。
- `schemas/incident-review.schema.json` 与 `indexes/incident-register.json` 让事故记录可解析、可检查。

## 公开和私有边界

公开摘要只包含已确认事实、总体影响、已完成或正在进行的措施和下一次更新时间。内部记录可以包含访谈摘录、未验证根因、责任分派、敏感设备信息和证据来源。
