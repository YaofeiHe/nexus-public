# nexus

`nexus` is a Codex-first workflow kernel / orchestrator. The verified end-to-end route is:

```text
$nexus-workflow -> nexus research -> codex-cli -> codex exec --output-schema
```

If no real provider is usable, `nexus research` stops in a configuration flow instead of silently using a mock model. The mock provider is only for tests or explicit local workflow checks.

The default locale is Chinese:

```text
locale: zh-CN
market_context: chinese_internet
query_language_priority: zh first, en as supplement
```

Chinese web search is real-provider only:

```text
default: Tavily -> Brave
optional: SerpApi Baidu / SearchApi Baidu with NEXUS_ENABLE_BAIDU_SERP=1
disabled by default: OpenAI web_search
```

Useful environment variables:

```bash
export TAVILY_API_KEY=...
export BRAVE_SEARCH_API_KEY=...
export NEXUS_TAVILY_KEY_FILE=<LOCAL_PATH_REDACTED>
export NEXUS_SERPAPI_KEY_FILE=<LOCAL_PATH_REDACTED>
export NEXUS_CHINESE_WEB_PROVIDERS=tavily,brave
export NEXUS_ENABLE_BAIDU_SERP=1
export SERPAPI_API_KEY=...
export SEARCHAPI_API_KEY=...
```

Without one of these real search keys, online Chinese web search returns `auth_required` and writes redacted blocked artifacts; it does not fabricate URLs or candidates.

Useful commands:

```bash
python -m nexus.cli doctor
python -m nexus.cli configure
python -m nexus.cli research "调研当前项目是否有现成 workflow/kernel 可以复用" --project-root .. --provider codex-cli --approve-online-search
python -m nexus.cli resume <run_id> --approve online-search
python -m nexus.cli status <run_id>
python -m nexus.cli report <run_id>
python -m nexus.cli continue <run_id|latest> "生成项目计划"
python -m nexus.cli continue <run_id|latest> "局部调研：provider 层"
python -m nexus.cli continue <run_id|latest> "分块调研：provider/search/runner"
python -m nexus.cli continue <run_id|latest> "更新项目意图：目标改成中文互联网优先 workflow kernel"
python -m nexus.cli plan-implementation <run_id>
python -m nexus.cli approve <run_id> code-change
python -m nexus.cli execute-code-change <run_id> --provider codex-cli
python -m nexus.cli diff <run_id>
python -m nexus.cli approve <run_id> apply
python -m nexus.cli apply <run_id>
python -m nexus.cli test <run_id> --cmd "python -m pytest -q"
python -m nexus.cli init-project "我要做一个中文互联网调研 workflow kernel" --parent ..
python -m nexus.cli board show --project-path ../some-project
python -m nexus.cli conversation-from-file exported-conversation.md
```

Core runtime rules live in `nexus/core_rules.md`; they explicitly forbid mock-only downgrades and require model calls for intent, naming, planning, scoring, localization review, and next-option synthesis.

<!-- nexus:public-install -->
## Public Install

Install the public package from GitHub:

```bash
python -m pip install git+https://github.com/YaofeiHe/nexus-public.git
```

Smoke test the installed command:

```bash
nexus --help
```

Codex workflow/skill install:

```bash
tmp="$(mktemp -d)" && git clone --depth 1 https://github.com/YaofeiHe/nexus-public.git "$tmp/repo" && mkdir -p "$HOME/.agents/skills" && for skill in skills/nexus-workflow; do cp -R "$tmp/repo/$skill" "$HOME/.agents/skills/"; done
```

This installs the workflow skill directly from the repository files into `$HOME/.agents/skills`.

Private runtime files, credentials, `.env`, tokens, cookies, browser profiles, `.data/`, `.nexus/private/`, and local host paths are not part of the public release.
