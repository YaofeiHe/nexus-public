# 检索记录

## 本轮实际读取

- `docs/intent/original-requirement.md`：读取用户对开源维护值班的原始目标。
- `docs/intent/normalized-requirement.md`：读取字段、workflow 和验收标准。
- `docs/reference-materials.md`：确认未提供真实 issue、release checklist、CI 命令和 security policy。

## 提取结果

- 值班工作区必须同时覆盖 issue triage、release blocker、回归证据、交接和恢复记录。
- public README 发布检查是显式需求，不是普通文档更新。
- private notes 和 public material 必须分离。
- 恢复记录应支持下一次相似失败前先查历史经验。

## 未读取或跳过

- GitHub API：样例不联网，也不改真实项目。
- 真实 README/changelog：未提供，不能编造发布材料。
- 未确认漏洞细节：不应出现在样例公开内容中。
