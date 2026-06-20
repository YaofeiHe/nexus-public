# 操作指南

## 常用指令

```bash
python3 scripts/validate_project.py
```

运行本地验证，检查关键文档、workflow、schema、索引和敏感边界。验证脚本只读取本样例目录，不连接外部服务。

## 工作流入口

- 新增资助方：按 `workflows/funder-intake.md` 更新 `indexes/grant-pipeline.json`。
- 构建申请包：按 `workflows/application-package-build.md` 将预算、证据、草稿和材料缺口互相引用。
- 提交和复盘：按 `workflows/deadline-feedback-review.md` 更新截止日期、提交状态、反馈、下一次窗口和负责人。

## 更新规则

1. 先修改 `docs/source-material-index.md`，说明新材料来源和读取状态。
2. 再更新 `indexes/grant-pipeline.json`，保持字段符合 `schemas/grant-record.schema.json`。
3. 如果新增需求类型，更新 `docs/requirement-trace.md`。
4. 最后运行 `python3 scripts/validate_project.py`。

## 同步和公开边界

私有同步可以保存完整申请记录。公开发布必须先移除内部联系人、未授权受益人信息、token、本地绝对路径、私有仓库地址和未确认反馈。public 发布不是默认动作，必须显式确认。

## 失败恢复

如果 validator 失败，先看输出中的文件和字段名。修复后重新运行验证。如果失败来自未提供材料，不要编造数据，应在 `docs/source-material-index.md` 标注 `未提供` 或 `待确认`。
