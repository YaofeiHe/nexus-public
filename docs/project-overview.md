# Nexus Project Overview

Nexus is a Codex-first workflow kernel for project research, initialization, GitHub private/public synchronization, Feishu guide publishing, recovery, and reusable workflow extraction.

## Public Sync Responsibility

Nexus public sync produces a public artifact that is safe to publish and usable by downstream users. It is not only a document snapshot. A public sync must generate staging, enforce denylist and sanitization, block secrets and private metadata by default, validate required runtime paths, and prove that both the staged artifact and a fresh copied download can be installed, imported, smoke-tested, and tested before public push.

Public push remains explicitly confirmed by the user, but confirmation is only one gate. Validation failure, missing runtime code, private-only data requirements, GitHub auth failures, repo failures, or push failures must block publication with structured artifacts and concrete next steps.

Self-sync closes the private audit trail after Feishu writes local records: GitHub private sync runs before Feishu and once again after successful Feishu autosync, without recursively invoking Feishu during that second private sync.

Supplemental initialization organizes an existing project; it does not reset the project to an empty initial state. Existing complete intent, overview, and operation-guide documents must be preserved. Incomplete non-empty documents may receive appended supplemental sections, while missing or placeholder documents may be created from current project intent.

## Recovery Responsibility

When Nexus leaves the normal workflow for host auth, CAPTCHA/2FA, network retry, or local Codex debugging, the run must preserve a continuation and return to the original `run_id`. Successful recovery should be written to the project recovery playbook after approval so similar future failures can reuse known safe actions before invoking high-intensity recovery planning.
