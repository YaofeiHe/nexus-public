from __future__ import annotations

from .base import ModelRequest, ModelResponse, ProviderStatus


class MockProvider:
    name = "mock"

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.name, "available", "Explicit mock provider for tests only.")

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        node = request.node_id
        if node == "intent_route":
            data = {
                "schema": "nexus.intent_route.v1",
                "resolved_route": "whole_project_discovery",
                "confidence": 0.78,
                "reason": "用户请求调研项目是否有现成 workflow/kernel，默认进入完整项目调研。",
                "project_mode": "existing",
                "discovery_target": "whole-project",
                "generalization_note": "用户例子只作为意图证据，不收缩为单一场景。",
                "completion_assessment": {"state": "planned", "weak_points": ["mock provider 仅用于测试"]},
                "next_options": [
                    {"id": "continue", "label": "继续完整项目调研", "description": "进入 discovery workflow。"},
                    {"id": "multi_part", "label": "拆分局部调研", "description": "把目标拆成多个组件分别调研。"},
                    {"id": "external_prompt", "label": "生成外部 GPT prompt", "description": "生成可复制到外部模型的调研 prompt。"},
                ],
            }
        elif node == "project_name_candidates":
            data = {
                "schema": "nexus.project_name_candidates.v1",
                "recommended": "nexus",
                "candidates": [
                    {
                        "name": "nexus",
                        "meaning": "连接模型、工具和 workflow 的中心枢纽。",
                        "memory_hook": "next + us 的谐音联想，表示把下一步接起来。",
                        "rationale": "五个字母，和 orchestrator 功能相关。",
                        "functional_link": "抽象对应 workflow kernel 把需求、工具、模型和项目经验连接起来。",
                        "metaphor": "纽带和枢纽，把分散信息接成可执行链路。",
                        "word_validation": "nexus 是真实英文五字母单词。",
                    },
                    {
                        "name": "forge",
                        "meaning": "锻造和沉淀小项目的车间。",
                        "memory_hook": "在 forge 里把 idea 锻造成项目。",
                        "rationale": "五个字母，是真实英文单词，适合表达从想法到产物的构建过程。",
                        "functional_link": "抽象对应项目初始化、同步和经验沉淀能力。",
                        "metaphor": "车间，把原始需求锻造成可维护项目。",
                        "word_validation": "forge 是真实英文五字母单词。",
                    },
                ],
            }
        elif node == "implementation_plan":
            data = {
                "schema": "nexus.implementation_plan.v1",
                "summary": "基于 discovery 报告生成实现计划，但不直接修改目标项目。",
                "phases": ["确认目标范围", "实现适配模块", "补充测试", "运行验证"],
                "files_to_touch": ["<target project files after approval>"],
                "tests": ["python -m pytest -q"],
                "risks": ["真实写入前需要二次确认"],
                "requires_code_change_approval": True,
            }
        elif node == "conversation_workflow":
            data = {
                "schema": "nexus.conversation_workflow.v1",
                "source_summary": "已从导出的对话历史中提取项目方法。",
                "generalized_project_type": "可复用 workflow/skill 构建项目",
                "workflow_blueprint": ["提取目标", "抽象场景", "定义工具边界", "生成安全审查", "输出 workflow 草案"],
                "skill_or_workflow_draft": "# Draft\n\n从具体项目对话泛化出的 workflow 草案。",
                "safety_notes": ["不要读取隐私凭据", "草案不自动安装或发布"],
                "next_options": ["审阅草案", "生成更正式 skill", "进入安全审查"],
            }
        elif node == "task_block":
            data = {
                "schema": "nexus.task_block.v1",
                "goal": "调研当前项目是否有可复用的 workflow/kernel 能力。",
                "constraints": ["默认只读", "中文互联网优先", "高风险动作需要确认"],
                "target_project": "当前项目",
                "success_criteria": ["产出候选列表", "说明可复用性", "给出下一步计划"],
            }
        elif node == "research_plan":
            data = {
                "schema": "nexus.research_plan.v1",
                "source_families": ["official_cn_docs", "chinese_tech_blogs", "github", "mcp_registry", "official_en_docs"],
                "queries": ["工作流 编排 kernel", "Codex workflow skill", "MCP 工作流 编排", "agent workflow orchestrator"],
                "coverage_gates": ["中文资料", "开源实现", "官方文档", "MCP/工具协议"],
                "stop_conditions": ["登录", "验证码", "读取凭据", "外部副作用"],
            }
        elif node.startswith("search_plan"):
            round_no = _round_no_from_prompt(request.prompt)
            data = {
                "schema": "nexus.search_plan.v1",
                "round_no": round_no,
                "source_plan": [
                    {
                        "source": "local_inventory",
                        "priority": "high",
                        "queries": ["workflow kernel", "中文互联网 workflow"],
                        "reason": "先读取本地项目证据。",
                    },
                    {
                        "source": "local_content",
                        "priority": "high",
                        "queries": ["runner orchestrator workflow kernel"],
                        "reason": "扫描本地文本内容。",
                    },
                    {
                        "source": "external_prompt",
                        "priority": "medium",
                        "queries": ["workflow kernel orchestrator", "codex workflow"],
                        "reason": "生成外部补充调研 prompt，不执行联网。",
                    },
                ],
                "requires_online": False,
                "coverage_gates": ["本地项目", "GitHub 开源实现", "MCP 工具生态", "中文资料"],
                "stop_conditions": ["候选足够", "核心来源失败需用户选择", "达到 max_rounds"],
            }
        elif node.startswith("coverage_review"):
            round_no = _round_no_from_prompt(request.prompt)
            data = {
                "schema": "nexus.coverage_review.v1",
                "round_no": round_no,
                "coverage_state": "partial" if round_no == 1 else "enough",
                "covered_facets": ["本地项目结构", "本地文本证据"],
                "missing_facets": ["真实在线来源"] if round_no == 1 else [],
                "source_failures": [],
                "quality_notes": ["mock provider 用于测试 search loop，不代表真实调研结论。"],
                "should_continue": round_no == 1,
                "recommended_next_sources": ["github_repo", "mcp_registry"] if round_no == 1 else [],
                "recommended_next_queries": ["workflow kernel", "MCP workflow server"] if round_no == 1 else [],
            }
        elif node.startswith("stop_decision"):
            round_no = _round_no_from_prompt(request.prompt)
            data = {
                "schema": "nexus.stop_decision.v1",
                "round_no": round_no,
                "should_continue": round_no == 1,
                "reason": "第一轮后继续一次以验证迭代；第二轮停止。",
                "confidence": "medium",
                "next_action": "continue_search" if round_no == 1 else "final_review",
            }
        elif node.startswith("candidate_review"):
            data = {
                "schema": "nexus.candidate_review.v1",
                "reviews": [
                    {
                        "candidate_id": "local-project",
                        "score": 0.72,
                        "reason": "本地项目结构可作为 workflow kernel 参考，但需要显式模型 provider。",
                        "risks": ["不能把 Python 子进程当成当前 Codex 对话回调"],
                        "recommended_use": "reference",
                    },
                    {
                        "candidate_id": "mcp-pattern",
                        "score": 0.66,
                        "reason": "MCP 适合作为长期模型/工具集成边界，但需要实际配置验证。",
                        "risks": ["MCP 客户端未配置时不能假装可用"],
                        "recommended_use": "future_provider",
                    },
                ],
            }
        elif node == "candidate_localization_review":
            data = {
                "schema": "nexus.candidate_localization_review.v1",
                "reviews": [
                    {
                        "candidate_id": "local-project",
                        "chinese_web_access": "unknown",
                        "documentation_language": ["zh-CN", "en"],
                        "domestic_platform_presence": {
                            "gitee": False,
                            "official_zh_docs": False,
                            "cn_blog_evidence": False,
                            "package_mirror": "unknown",
                        },
                        "availability_summary": "mock 只用于测试，不代表真实可达性。",
                        "risk_level": "medium",
                        "recommendation": "manual_check_required",
                        "reason": "MockProvider 不执行真实中文互联网可用性判断。",
                        "evidence_refs": [],
                    }
                ],
            }
        elif node.startswith("continue_intent_route"):
            user_text = _line_after(request.prompt, "用户继续输入：")
            lowered = user_text.lower()
            if "已有轮子" in user_text or "现成" in user_text or "existing" in lowered:
                route = "select_existing_wheel"
                scope = "existing_wheel_build"
            elif "从零" in user_text or "from scratch" in lowered:
                route = "select_from_scratch_build"
                scope = "from_scratch_build"
            elif "多个小项目" in user_text:
                route = "select_subproject_wheels"
                scope = "subproject_wheel_research"
            elif "局部" in user_text or "local" in lowered:
                route = "local_research"
                scope = "provider 层"
            elif "分块" in user_text or "拆" in user_text or "chunk" in lowered:
                route = "select_subproject_wheels" if '"id": "branch_subproject_wheels"' in request.prompt else "chunked_research"
                scope = "subproject_wheel_research" if route == "select_subproject_wheels" else "provider/search/runner"
            elif "更新" in user_text or "意图" in user_text:
                route = "update_intent"
                scope = "项目需求意图"
            elif "重新" in user_text or "rerun" in lowered:
                route = "rerun_research"
                scope = "完整调研"
            else:
                route = "implementation_plan"
                scope = "基于调研报告生成项目计划"
            data = {
                "schema": "nexus.continue_intent_route.v1",
                "route": route,
                "confidence": 0.84,
                "reason": "MockProvider 根据测试输入返回可预测 route。",
                "scope": scope,
                "updated_idea": "更新后的测试意图",
                "requires_approval": route == "implementation_plan",
                "next_prompt": "继续执行对应 nexus 节点。",
            }
        elif node == "updated_intent":
            data = {
                "schema": "nexus.updated_intent.v1",
                "updated_idea": "更新后的测试意图",
                "reason": "根据用户继续输入更新项目意图。",
                "constraints": ["继续保持只读，写入前审批"],
                "next_options": ["重新调研", "局部调研", "生成项目计划"],
            }
        elif node == "chunked_research_plan":
            data = {
                "schema": "nexus.chunked_research_plan.v1",
                "execution_note": "已把后续调研拆成多个块；默认先生成计划，不自动启动多个联网 run。",
                "chunks": [
                    {"id": "provider", "label": "Provider 层", "scope": "模型 provider 与调用链路", "reason": "验证真实模型调用", "status": "pending"},
                    {"id": "search", "label": "Search 层", "scope": "检索 adapter 与 source_status", "reason": "验证真实检索", "status": "pending"},
                ],
            }
        elif node == "risk_analysis":
            data = {
                "schema": "nexus.risk_analysis.v1",
                "risks": ["真实模型 provider 未配置时必须 blocked", "写目标项目必须审批"],
                "blocked_actions": ["install", "login", "read_secret", "submit_form", "push"],
                "approval_required": False,
            }
        elif node == "github_auth_failure_guidance":
            data = {
                "schema": "nexus.github_auth_failure_guidance.v1",
                "summary": "GitHub 登录流程在拿到授权结果前就中断，更像代理或本地网络链路不稳定，而不是账号口令错误。",
                "probable_root_cause": "代理链路、设备码请求或本地终端浏览器唤起能力异常。",
                "safe_next_attempts": [
                    "在保留安全边界下复试 gh auth login --web 并记录 device-code 阶段的输出。",
                    "如果当前 shell 带代理变量，尝试在不读取任何凭据的前提下去代理复试。",
                    "开启 GH_DEBUG=api 收集更明确的网络/设备码请求错误。",
                ],
                "manual_user_actions": [
                    "如果已经拿到 device code，在 GitHub 登录页完成密码、2FA、CAPTCHA 和授权。",
                    "如果仍然拿不到 device code，优先检查本机代理、VPN、防火墙和浏览器唤起能力。",
                ],
                "stop_conditions": [
                    "不要读取 token、cookie、浏览器 profile、SSH key 或 .env。",
                    "不要自动输入密码、2FA 或绕过 CAPTCHA。",
                ],
                "recommended_actions": [
                    {
                        "action_id": "retry_without_proxy_and_debug_api",
                        "rationale": "同时绕开本地代理并开启 API 级调试，适合 EOF / 代理链路异常场景。",
                        "requires_escalation": True,
                        "risk_summary": "会在宿主环境重新启动 GitHub CLI 登录流程，但不会输入密码、2FA 或读取凭据。",
                        "command": "gh auth login --web --clipboard --skip-ssh-key --git-protocol https --hostname github.com",
                        "service": "github.com",
                        "paths": [],
                    },
                    {
                        "action_id": "retry_default_login_after_diagnostics",
                        "rationale": "在记录更多诊断后复试标准登录流程，验证是否为瞬时网络故障。",
                        "requires_escalation": True,
                        "risk_summary": "会再次请求 GitHub device login；用户仍需自行完成网页授权。",
                        "command": "gh auth login --web --clipboard --skip-ssh-key --git-protocol https --hostname github.com",
                        "service": "github.com",
                        "paths": [],
                    },
                ],
            }
        elif node == "provider_preflight_failure_guidance":
            data = {
                "schema": "nexus.failure_recovery_guidance.v1",
                "summary": "当前 provider 通过状态检查但预检失败，更像本地运行时、权限或临时链路问题，而不是工作流本身逻辑错误。",
                "probable_root_cause": "provider 预检环境异常、宿主权限限制，或当前 provider 的本地依赖链路暂时不可用。",
                "safe_next_attempts": [
                    "在不读取任何凭据的前提下重试一次同 provider preflight，确认是否为瞬时失败。",
                    "如果有其他真实 provider 候选，切换到下一个候选继续 workflow，避免当前 run 直接停止。",
                ],
                "manual_user_actions": [
                    "如果所有候选都失败，检查当前 provider 的本地配置、网络、权限和 CLI 安装状态。",
                ],
                "stop_conditions": [
                    "未获用户授权时不执行需要权限的动作。",
                    "达到最大尝试次数后输出标准化 blocked 状态。",
                ],
                "recommended_actions": [
                    {
                        "action_id": "switch_to_next_real_provider",
                        "rationale": "当前 provider 预检失败且仍有候选时，优先继续下一个真实 provider，减少无谓阻塞。",
                        "requires_escalation": False,
                        "risk_summary": "会改用下一个已配置真实 provider 继续本次 workflow。",
                        "command": "",
                        "service": "model-provider",
                        "paths": [],
                    },
                    {
                        "action_id": "retry_same_provider_preflight",
                        "rationale": "在有限次数内复试同 provider，可区分瞬时失败和稳定性故障。",
                        "requires_escalation": False,
                        "risk_summary": "会再次执行当前 provider smoke/preflight，不读取密钥内容。",
                        "command": "",
                        "service": "model-provider",
                        "paths": [],
                    },
                ],
            }
        elif node.startswith("recovery_plan"):
            data = {
                "schema": "nexus.failure_recovery_guidance.v1",
                "summary": "当前链路失败后已进入高精度恢复规划；优先复用经验，必要时请求用户授权后继续尝试。",
                "probable_root_cause": "外部服务配置、宿主权限、网络链路或 provider/同步工具运行条件暂时不满足。",
                "safe_next_attempts": [
                    "记录失败上下文和可尝试动作。",
                    "如果动作需要登录、发布、push、外部写入或宿主权限，则明确说明风险并请求用户授权。",
                ],
                "manual_user_actions": [
                    "审阅恢复动作权限请求；批准后由 Codex/Nexus 执行对应动作。",
                ],
                "stop_conditions": [
                    "未获用户授权时不执行需要权限的动作。",
                    "达到最大尝试次数后输出标准化 blocked 状态。",
                ],
                "recommended_actions": [
                    {
                        "action_id": "request_user_permission_for_recovery_action",
                        "rationale": "需要由用户确认后执行下一步恢复动作，提权请求本身就是边界。",
                        "requires_escalation": True,
                        "risk_summary": "会在用户批准后执行恢复动作，可能访问外部服务或宿主环境；执行前必须说明命令、路径和服务。",
                        "command": "",
                        "service": "nexus-recovery",
                        "paths": [],
                    },
                    {
                        "action_id": "record_recovery_context",
                        "rationale": "先保存本次失败上下文和恢复计划，便于后续沉淀为项目 playbook。",
                        "requires_escalation": False,
                        "risk_summary": "只写当前 run artifact，不修改目标项目文件。",
                        "command": "",
                        "service": "local-artifacts",
                        "paths": [],
                    },
                ],
            }
        else:
            data = {
                "schema": "nexus.final_report.v1",
                "summary": "已完成只读 discovery 流程；结果面向中文互联网环境，候选已按可复用性排序。",
                "findings": ["nexus 应显式调用 HostModelProvider", "mock 只用于测试", "MCP/CLI/API 需要 doctor 验证"],
                "next_action_plan": ["配置真实 provider", "接入更多中文互联网 source adapter", "在审批后进入实现计划"],
            }
        return ModelResponse(provider=self.name, raw_text=str(data), json_data=data, diagnostics={"mock": True})


def _round_no_from_prompt(prompt: str) -> int:
    import re

    match = re.search(r"round_no[:：]\s*(\d+)", prompt)
    return int(match.group(1)) if match else 1


def _line_after(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.split(prefix, 1)[1].strip()
    return text
