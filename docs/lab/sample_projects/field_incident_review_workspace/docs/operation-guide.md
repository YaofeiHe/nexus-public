# 操作指南

## 常用指令

```bash
python3 scripts/validate_project.py
```

该命令检查事故复盘工作区的关键文档、workflow、schema、事故索引和公开/私有边界。

## 工作流入口

- 新增事故：使用 `workflows/incident-intake.md`，先记录事实和影响。
- 组织复盘：使用 `workflows/root-cause-review.md`，区分假设、证据、反证和不确定点。
- 关闭整改：使用 `workflows/remediation-verification.md`，只有证据满足验证标准才能关闭。
- 生成周报：使用 `workflows/weekly-leadership-summary.md`，输出领导可读摘要并保留内部风险。

## 更新规则

1. 新来源先写入 `docs/source-material-index.md`。
2. 事故记录写入 `indexes/incident-register.json`，字段遵守 `schemas/incident-review.schema.json`。
3. 新增需求或用户纠正时更新 `docs/requirement-trace.md`。
4. 周报中未验证事实必须标注为内部或待确认。
5. 每次重要更新后运行 `python3 scripts/validate_project.py`。

## 同步和恢复

私有同步可以保存完整复盘材料。公开摘要必须移除人员姓名、敏感设备、未验证根因、内部责任归属和本地路径。验证失败时先修复字段或补充来源记录，不要把 `pending` 状态改成 `verified` 来绕过检查。
