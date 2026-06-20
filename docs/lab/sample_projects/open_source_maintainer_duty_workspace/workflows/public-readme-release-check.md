# Workflow: Public README 发布检查

## 触发条件

准备发布 README、changelog、release notes 或 public 安装说明。

## 步骤

1. 对照 `indexes/public-private-boundary.json` 检查 public allowed 和 private only 规则。
2. 扫描 token、私有路径、private repo 名称、未确认漏洞细节和内部讨论。
3. 确认 README 命令可读、可复制，并标注未验证的安装步骤。
4. 如果 public 内容引用 regression evidence，确认该证据可公开。
5. 运行 `python3 scripts/validate_project.py`。

## 验收

- public material 不含 private-only 内容。
- 发布前有 scan result 或等价记录。
- 不把 private maintainer notes 当成 changelog。
