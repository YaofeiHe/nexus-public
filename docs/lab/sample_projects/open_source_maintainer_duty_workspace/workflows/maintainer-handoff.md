# Workflow: 维护者交接

## 触发条件

值班结束、release 前切换负责人、阻断项需要跨班继续处理。

## 步骤

1. 汇总 now/blocker/security-sensitive issue。
2. 汇总未解除 release blocker 和缺失的 regression evidence。
3. 写明下一位维护者先读哪些记录。
4. 标出 public 可说内容和 private-only notes。
5. 更新 `indexes/duty-board.json` 中的 `handoff_packet`。
6. 运行 `python3 scripts/validate_project.py`。

## 验收

- 下一位维护者能从交接包恢复上下文。
- 每个未完成项有 owner 或下一步动作。
- private notes 不进入 public handoff summary。
