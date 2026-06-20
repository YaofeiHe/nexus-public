# 检索记录

## 实际读取结果

- `reference_pack/strategy_notes.md`：提取到 incident intake、root-cause notes、owner assignment、remediation verification、weekly leadership summary。
- `reference_pack/prior_chat_excerpt.md`：提取到用户规则：示例不是范围边界，跳过来源要写原因，不能只用 completed 表示检索。
- `reference_pack/existing_files_index.md`：提取到初始化时应先列出已知文件和读取状态。

## 未读取或跳过

- 真实现场照片、日志、设备编号、人员访谈：样例未提供，不编造。
- 组织正式严重度标准：样例未提供，保留为未确认问题。
- 法务或合规审查结论：样例不替代正式调查。

## 回写位置

- 业务对象写入 `docs/intent/normalized-requirement.md`。
- 用户纠正写入 `docs/requirement-trace.md` 和 validator 检查。
- 来源读取状态写入 `docs/source-material-index.md`。
