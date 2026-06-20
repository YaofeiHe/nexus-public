# 需求追踪

| ID | 用户原始要求 | 规范化理解 | 落地文件 |
|---|---|---|---|
| OSM-R01 | 开源维护者值班工作区 | 工作区围绕值班 issue、release、测试、交接和恢复组织 | `docs/project-overview.md`, `indexes/duty-board.json` |
| OSM-R02 | issue 分级 | incoming issue 需要分为 now/next/later/blocker/security-sensitive | `workflows/issue-triage.md`, `schemas/maintainer-duty.schema.json` |
| OSM-R03 | release blocker | 阻断项必须有关联 issue、解除条件和验证证据 | `workflows/release-blocker-review.md`, `indexes/duty-board.json` |
| OSM-R04 | 回归测试证据 | 记录命令、结果、环境和证据链接，不能只写测试通过 | `workflows/regression-evidence-capture.md`, `indexes/duty-board.json` |
| OSM-R05 | 维护者交接 | 交接包必须说明当前风险、下一步和下一位维护者先读什么 | `workflows/maintainer-handoff.md`, `indexes/duty-board.json` |
| OSM-R06 | 公共 README 发布检查 | public 发布前检查敏感内容和可读性 | `workflows/public-readme-release-check.md`, `indexes/public-private-boundary.json` |
| OSM-R07 | 事故恢复记录 | 恢复记录必须包含失败签名、诊断、修复、测试和复用条件 | `workflows/recovery-record-update.md`, `indexes/duty-board.json` |
| OSM-R08 | 区分 private 和 public | private notes 不得进入 public release material | `indexes/public-private-boundary.json`, `scripts/validate_project.py` |

## 追踪口径

开源维护值班的核心不是“有一张 issue 列表”，而是每个 issue、blocker、测试证据和交接结论都能说明公开状态、责任人和下一步动作。
