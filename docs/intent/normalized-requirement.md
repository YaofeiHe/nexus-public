# Nexus Normalized Requirement

Nexus must deliver real end-to-end workflow results, not mock kernels, offline demos, or surface-level substitutes. If a requested workflow cannot be completed because of permissions, credentials, external services, security boundaries, or architecture limits, Nexus must block with the concrete reason and next decision point.

For GitHub public sync, the output must be a reusable public artifact. The public repository must contain the required runtime code and public-safe data, exclude private/runtime/secrets paths, and pass real staging validation before push. Public staging must sanitize private metadata such as local absolute paths, private repo names, Feishu document URLs, Nexus run ids, and internal artifact paths, then block remaining secrets or private metadata by default. Validation includes required path checks, install/import checks, CLI smoke checks, tests configured for the public artifact, and the same checks against a fresh copied download.

For self-sync, Nexus must avoid leaving Feishu autosync writeback only on disk. After Feishu successfully updates local records or document bindings, Nexus must run one additional GitHub private sync without recursively triggering Feishu again.

For supplemental initialization, Nexus must preserve already meaningful project documentation. It must inspect intent docs, project overview, operation guide, and `.nexus/project-intent.json` before writing. Complete existing documents are left unchanged, incomplete non-empty documents are supplemented without deleting user text, and only missing, empty, or placeholder documents are rebuilt.

For recovery, Nexus must preserve the original run state. Host login, CAPTCHA/2FA, GitHub API EOF retry, provider recovery, and local Codex debug handoff must continue through `pending_actions`, `continue-after-input`, or `rebind-and-continue` on the same `run_id`. Successful recovery experience should be approved into `.nexus/recovery-playbook.json` and reused for similar future failures before invoking high-intensity recovery planning.
