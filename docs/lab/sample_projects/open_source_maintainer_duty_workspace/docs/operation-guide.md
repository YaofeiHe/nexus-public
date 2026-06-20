# 操作指南

## 常用指令

```bash
python3 scripts/validate_project.py
```

该命令检查维护值班样例的文档、workflow、schema、索引、公私边界和敏感内容。

## 工作流入口

- Issue 分级：使用 `workflows/issue-triage.md` 更新 `indexes/duty-board.json`。
- Release blocker 审查：使用 `workflows/release-blocker-review.md` 确认阻断项和解除条件。
- 回归证据记录：使用 `workflows/regression-evidence-capture.md` 记录命令、环境、结果和证据。
- 维护者交接：使用 `workflows/maintainer-handoff.md` 生成下一班入口。
- Public README 发布检查：使用 `workflows/public-readme-release-check.md`，并对照 `indexes/public-private-boundary.json`。
- 恢复记录更新：使用 `workflows/recovery-record-update.md` 沉淀失败签名、诊断、修复和复用条件。

## 更新规则

1. 新材料先写入 `docs/reference-materials.md` 或 `docs/search-log.md`。
2. 结构化值班数据写入 `indexes/duty-board.json`。
3. public/private 规则写入 `indexes/public-private-boundary.json`。
4. 如果新增用户要求，更新 `docs/requirement-trace.md`。
5. 每次更新后运行 `python3 scripts/validate_project.py`。

## 发布边界

Public 发布必须显式确认。发布前检查 README/changelog 是否包含 token、私有路径、private repo 名称、未确认漏洞细节、维护者内部讨论或未公开 exploit。私有同步可以保留完整值班记录。

## 失败恢复

如果发布、测试或修复失败，先写 `recovery_record`，包含 failure_signature、diagnosis、fix_summary、test_evidence 和 reuse_condition。下一次相似失败应先查这些记录，再决定是否需要新的外部 debug。
