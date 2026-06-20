# Nexus 问题矩阵

日期：2026-06-18

来源：`<LOCAL_PATH_REDACTED>`

这份矩阵把用户对 Nexus/Codex 的不满转成 lab loop 的统一验收轴。它不是一组“一个问题配一个样例”的测试，而是所有样例、真实项目 case 和结构化 case 都必须共同满足的通用要求。

机器可读版本在 `scripts/lab/nexus_problem_matrix.json`。

## 覆盖规则

- 所有 case 都默认覆盖 16 个问题轴。
- `codpm`、`forge-manager`、`feeler`、`thesis`、`probe`、`orbit`、`wljob` 只提供不同来源和领域压力，不负责单独代表某一个问题。
- 结构化样例和真实项目样例使用同一问题矩阵，不能出现样例套件测 A、真实项目套件测 B 的两套口径。
- 修改请求必须指向 Nexus 通用机制，例如 `init_project`、材料读取、search trace、sync、recovery、monitor，不得指向某个样例名的特殊分支。
- 外部副作用测试默认只验证门禁和 blocked evidence；只有显式传入允许参数时才执行 GitHub、Feishu、public 发布类动作。

## 16 个问题轴

| id | 问题 | 主要证据面 |
|---|---|---|
| P01 | 外部 Codex debug 后必须回到原 Nexus 暂停点 | `handoffs/`, `worklogs/`, `rebind/`, `interaction.json` |
| P02 | 外部恢复操作必须沉淀到 recovery playbook | `.nexus/recovery-playbook.json`, `docs/recovery-records.md`, recovery artifacts |
| P03 | 用户不应反复输入同一限定语 | normalized requirement, requirement trace, monitor next prompt |
| P04 | 初始化、补充初始化、自同步、public 同步不能反复纠缠 | shared sync artifacts and continuation state |
| P05 | GitHub private 默认同步和 public 显式确认不能混淆 | private/public/Feishu artifacts and pending actions |
| P06 | 初始化不能只保存 raw input，必须理解成项目功能和结构 | intent docs, workflows, schemas, validator |
| P07 | 一两句项目说明不能算需求理解 | detailed normalized requirement and domain workflow |
| P08 | 用户指定的历史文件、目录、prompt 必须成为必读来源 | source index, reading plan/log, source status |
| P09 | 检索后不能只给运行状态 | search plan, search log, coverage review, stop reason |
| P10 | 自动输出质量必须接近手工带出的 feeler | usable workspace, guide, validator, domain workflow |
| P11 | 构建、测试、反馈、再修改必须成为闭环 | evaluation, modification request, retest result |
| P12 | Verix 式独立评测必须驱动 Nexus 修改 | independent evaluation and feedback contract |
| P13 | Monitor 必须能看到整条闭环阶段 | loop state, iteration summary, next prompt |
| P14 | 不能用专业词替代真实 artifacts | file refs, commands, state fields, validation evidence |
| P15 | 示例不限定范围，必须抽象成同类能力 | anti-hardcoding and variant coverage |
| P16 | 分散 CLI 必须组合成节省机械操作的端到端系统 | batch loop state, all-case regression, modification cycle |

## 启动后的执行形态

默认入口：

```bash
python3 scripts/lab/run_nexus_lab_loop.py \
  --cases-file scripts/lab/nexus_real_project_cases.json \
  --e2e-root /tmp/nexus-real-e2e-lab \
  --max-iterations 3 \
  --execute
```

这一命令会：

1. 记录当前 Nexus 分支、HEAD 和 dirty 状态。
2. 在每一轮中执行全部选中 case，而不是先跑一个 case 后停下来询问。
3. 对每个 case 生成 `execution.json` 和 `evaluation.json`。
4. 对每个 `evaluation.json` 生成 16 个 `problem_axis_results`。
5. 聚合整轮失败到 `iteration_summary.json`。
6. 写出 `modification_request.json`，作为修改 agent 的输入。
7. 如果传入 `--apply-modification-command`，修改后进入下一轮全量回归；否则当前对话可以读取修改请求继续扮演修改 agent。

## 退出条件

- 全部选中 case 的问题轴通过：`pass`。
- 只剩 GitHub、Feishu、public、登录或授权类外部副作用门禁：`blocked_external_side_effects`。
- 评测失败并且没有修改命令：`needs_modification_agent`，当前对话继续读取 `modification_request.json` 修改 Nexus。
- 修改命令失败：`modification_failed`。
- 达到 `--max-iterations`：`iteration_limit_reached`。

## 记忆候选

loop 会把有价值但尚未确认可复用的操作经验写入：

```text
<e2e-root>/lab-loops/<loop-id>/memory_candidates.jsonl
```

这里的内容只是候选。只有当经验已经被验证、不是样例特例、并且适合长期复用时，才应提升为 Codex memory。
