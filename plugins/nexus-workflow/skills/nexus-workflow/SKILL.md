---
name: nexus-workflow
description: Use this skill when the user says "使用 nexus", "nexus 调研", "用 nexus 调研当前项目", "调研某个项目是否有完整现成实现", "Codex-first workflow kernel", or asks Codex to run a local Nexus research / workflow orchestrator. This skill must call the local nexus CLI with a real provider and must not use mock unless the user explicitly asks for mock/test mode.
---

# nexus-workflow

This public workflow skill is for a fresh public repository install.

Install the package first:

```bash
python -m pip install git+https://github.com/YaofeiHe/nexus-public.git
```

Use the installed CLI or module entrypoint; do not call a private local checkout path.

```bash
nexus --help
```

Do not read local credentials, private runtime directories, `.env`, tokens, cookies, browser profiles, or host-specific paths.
