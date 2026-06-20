# Nexus Core Rules

These rules are part of the runtime contract. New nexus modules must follow them.

1. Model thinking is explicit: any node that performs intent understanding, generalization, naming, planning, scoring, risk analysis, summarization, route selection, or next-option synthesis must call `HostModelProvider`.
2. A skill is only a trigger and relay layer; `SKILL.md` must not be treated as a model RPC endpoint.
3. A Python subprocess cannot implicitly call back into the currently visible Codex conversation; model calls must go through `CodexMcpProvider`, `CodexCliProvider`, or `ApiProvider`.
4. Do not downgrade a requested end-to-end module into a mock-only or placeholder implementation; if a capability is blocked by environment, provider, permission, or missing external integration, write a blocked artifact and explain the missing condition.
5. `MockProvider` is for tests or explicit `--provider mock`; normal runs must try real providers first and must not silently mock.
6. User examples are evidence for intent, not the final scope; when a user gives a concrete scenario, the intent router must decide whether to generalize beyond the example.
7. Project directory naming, project intent routing, implementation planning, candidate review, and next-action options are model nodes, not local heuristics.
8. Project-root creation and target-project writes require explicit approval artifacts before filesystem writes.
9. Every new workflow node must declare: model nodes, tool nodes, approval nodes, artifact outputs, interaction output, and failure behavior.
10. Interaction replies must report completion quality: current status, what is weak or incomplete, and multiple next options when the next step is ambiguous.
11. External prompts are not retrieval results; they are only blocked/partial fallback artifacts and must never be reported as completed online research.
12. Code changes must use the approval chain: implementation plan, code-change approval, isolated worktree, diff preview, apply approval, and tests.
13. Chinese web search must not call OpenAI web_search by default; OpenAI web_search is allowed only when `NEXUS_ENABLE_OPENAI_WEB_SEARCH=1`.
14. Chinese web search defaults to real non-OpenAI providers in this order: Tavily, then Brave; Baidu SERP providers require `NEXUS_ENABLE_BAIDU_SERP=1`.
15. If a web-search provider API key is missing, write `auth_required` / blocked artifacts and do not fabricate candidates.
16. CandidateRecord objects from online search must come from real URL-bearing provider responses; model guesses, summaries, or external prompts must not become candidates.
17. Every attempted online provider request must write a redacted raw artifact; API keys, Authorization, X-Subscription-Token, `api_key`, Cookie, and Set-Cookie must never be written.
18. Model selection is an explicit workflow input: configured API profiles are the default first choice, `codex-cli` is the second choice, `codex-mcp` is the standby fallback, and user-selected profiles must drive every model node in the run.
19. `使用 $nexus-workflow 更换模型` must enter a real model configuration/status flow and report available, needs-config, and unsupported providers with standardized interaction output.
20. API keys must be referenced by environment variable or key-file path only; nexus artifacts must record redacted configuration and key presence, never secret values.
21. Candidate ranking must be hybrid: tools canonicalize URLs, merge evidence, and compute mechanical features before the model reviews compact candidate evidence.
22. Code-change on a non-git target must not proceed; nexus should first offer an approval-gated git baseline flow unless the user explicitly asks for a future non-git patch mode.
23. Conversation-to-skill/workflow must use a real transcript source; if current Codex history is not programmatically readable, block and ask for an exported markdown/json/ChatGPT zip transcript.
24. Resume must be node-based: completed nodes reuse validated artifacts, failed/interrupted nodes rerun from their checkpoint, and approval nodes continue only after approval markers exist.
25. Reading local Codex session history requires explicit conversation-session-read approval and secret redaction before any model call.
26. CAPTCHA/WAF conditions must be recorded as `captcha_or_waf`, not as `no_results`, and must not be bypassed.
