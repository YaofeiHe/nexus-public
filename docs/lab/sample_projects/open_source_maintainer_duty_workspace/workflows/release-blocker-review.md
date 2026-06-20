# Workflow: Release Blocker 审查

## 触发条件

准备 release、出现高优 issue、测试失败或维护者要求判断是否阻断发布。

## 步骤

1. 读取 `indexes/duty-board.json` 中 `release_blockers`。
2. 确认每个 blocker 的关联 issue、阻断原因、解除条件和 owner。
3. 检查是否已有 regression evidence。
4. 没有解除条件或证据时，不能把 blocker 标记为 cleared。
5. public release material 只保留已确认、可公开的信息。
6. 运行 `python3 scripts/validate_project.py`。

## 验收

- 每个 blocker 有明确解除条件。
- cleared blocker 有测试证据。
- public 摘要不包含 private maintainer notes。
