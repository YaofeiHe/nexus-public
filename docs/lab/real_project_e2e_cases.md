# Nexus 真实项目自动化测试案例

日期：2026-06-17

本文档替代临时领域样例作为下一轮 Nexus 自动化测试主入口。测试对象仍然是 `nexus` 项目本身；`codpm`、`forge-manager`、`feeler`、`thesis`、`probe`、`orbit`、`wljob` 只是用来构造真实来源的端到端测试案例。

## 要解决的问题

这套 case 面向两类问题：

1. Codex 构建 Nexus 时反复丢失约束：把例子当边界、断章取义、用空泛术语代替产物、修一条链路又破坏另一条链路。
2. Nexus 作为项目构建 agent 没有稳定做到：接收初始意图、读取历史和参考材料、生成可用项目、测试项目、按反馈修改、记录同步/恢复/状态证据。

## 16 个问题映射

16 个问题的机器可读版本在 `scripts/lab/nexus_problem_matrix.json`，人读说明在 `docs/lab/nexus_problem_matrix.md`。

这些问题不是 case 分工表。每个真实项目 case 都必须携带同一组问题轴，评测时每个 case 都输出 16 条 `problem_axis_results`。某个项目只能提供不同压力场景，例如 `probe` 更容易暴露意图理解失败，`wljob` 更容易暴露逐条 `$nexus-workflow` 和同步边界，但它们都要接受完整问题矩阵检查。

## 项目类型

| 类型 | 项目 | 用途 |
|---|---|---|
| 成功 workflow 项目 | `codpm` | Codex 个性化治理，真实 CLI、registry、rules/skills/hooks/config/MCP/memory surface、public 验收通过 |
| 成功 workflow 项目 | `forge-manager` | Forge 项目管理，真实本地数据、project/task 状态、深链、dashboard、CLI 输出规范 |
| 成功 skill 型项目 | `feeler` | 成功版 skill/workspace，复杂历史材料转成可读文档、copy-ready、workflow、schema、validator |
| skill 型项目 | `thesis` | 论文流程 skill/workspace，官方依据、模板保格式、影子目录、文档处理边界 |
| 失败对照 | `probe` | Nexus 初始化失败样本：保留了输入，但项目意图理解和可用结构远低于 `feeler` |
| 半失败/局部改进 | `orbit` | Nexus 初始化理解偏差，但微信子项目已有较好人工构建蓝图，可测试“从失败项目吸收好子模块” |
| 端到端热测试 | `wljob` | 你写的一句话 E2E 指令链，可测试标准三段输出、provider、research、sync、Feishu、conversation、public gate |

## Case Registry

真实项目 case registry：

```bash
python3 scripts/lab/run_nexus_e2e_case.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --dry-run-plan
```

默认 case 只做计划或本地隔离执行。所有 GitHub public、Feishu、真实同步、登录、conversation-session-read 都必须显式允许或审批。

## 默认启动方式

默认不再按 case 线性推进，也不把某个 case 当成唯一入口。启动后应先跑完整 batch：

```bash
python3 scripts/lab/run_nexus_lab_loop.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --e2e-root /tmp/nexus-real-e2e-lab \
  --max-iterations 3 \
  --execute
```

这一轮会先执行全部真实项目 case，再统一评测，再聚合 `modification_request.json`。如果当前对话作为修改 agent，就读取这个修改请求，修改 Nexus 通用能力后再启动下一轮；如果传入 `--apply-modification-command`，脚本会在每轮评测后调用该命令并继续全量回归。

单个 case 只用于调试命令形状、复查某个失败证据或减少外部副作用风险，不作为默认验收方式。

## 通过标准

- 新项目输出必须有 `original-requirement`、`normalized-requirement`、`requirement-trace`、source/reference index、search plan/log、project overview、operation guide、workflow、schema/index、validator。
- 每个 case 都必须输出 16 轴 `problem_axis_results`，不能只说当前 case 负责某几个问题。
- 对成功项目的重建必须抽象通用能力，而不是复制项目名和目录。
- 对失败项目的重建必须说明原失败输出缺什么，并补出可执行项目结构。
- 对 `wljob` 必须保持 `wljob_project_intent.md` 为主，`wlzuu` 只能作为历史来源。
- 所有外部副作用必须 blocked 或进入确认链，不能自动执行。
- evaluator 的失败必须映射到 Nexus 通用入口，例如 `init_project()`、`write_project_docs()`、source reading/search trace、sync、recovery、monitor。
