# 需求追踪

| ID | 用户原始要求 | 规范化理解 | 落地文件 |
|---|---|---|---|
| FIR-R01 | 根据三份 reference pack 构建工作区 | 先记录每份材料的读取目的、状态和提取结果 | `docs/source-material-index.md`, `docs/search-plan.md`, `docs/search-log.md` |
| FIR-R02 | 事故 intake | 记录现场事实、初步影响、严重度和第一响应 | `workflows/incident-intake.md`, `indexes/incident-register.json` |
| FIR-R03 | 根因分析 | 把假设、证据、反证和未确认问题分离 | `workflows/root-cause-review.md`, `schemas/incident-review.schema.json` |
| FIR-R04 | 负责人分派 | 每个整改行动必须有 owner、截止日期和升级条件 | `workflows/remediation-verification.md`, `indexes/incident-register.json` |
| FIR-R05 | 整改验证证据 | 未验证不得关闭，验证标准必须具体 | `workflows/remediation-verification.md`, `schemas/incident-review.schema.json` |
| FIR-R06 | 每周领导摘要 | 领导摘要区分公开信息和内部风险 | `workflows/weekly-leadership-summary.md`, `indexes/incident-register.json` |
| FIR-R07 | 不要只写检索完成 | 检索计划和检索记录必须说明查了什么、未查什么、为什么 | `docs/search-plan.md`, `docs/search-log.md` |
| FIR-R08 | 例子不是范围上限 | workflow 处理同类事故，不按单一事故模板写死 | `docs/intent/normalized-requirement.md`, `scripts/validate_project.py` |

## 更新要求

新增事故类型时，必须先判断它是否复用现有 intake、根因、整改和周报流程；只有现有字段无法承载时，才扩展 schema。
