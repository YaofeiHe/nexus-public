# Feishu Docs API

Nexus can publish generated project documentation to Feishu Docs through the Feishu Open Platform API.

## Credential Files

Default credential file paths:

```text
<LOCAL_PATH_REDACTED>
<LOCAL_PATH_REDACTED>
```

Each file must contain only the corresponding value. Do not copy these files into the project.

Environment overrides:

```bash
export FEISHU_APP_ID_PATH=/path/to/feishu_appid
export FEISHU_APP_SECRET_PATH=/path/to/feishu_appsecret
```

## Smoke Test

Token-only check:

```bash
python scripts/feishu_smoke_test.py
```

Create a test doc only when explicitly requested:

```bash
python scripts/feishu_smoke_test.py \
  --create-doc \
  --title "Feishu API smoke test" \
  --folder-token "<FOLDER_TOKEN>"
```

The smoke test prints whether credentials loaded, whether the token request succeeded, token expiry, and document ids/URLs. It never prints app secret or token values.

## Nexus Config

First-time setup guide and token-only verification:

```bash
python -m nexus.cli feishu setup --project-path <target-project> --research-docs --approve-online-search
```

Register local credential file paths and optionally a target folder:

```bash
python -m nexus.cli feishu setup \
  --project-path <target-project> \
  --app-id-path <LOCAL_PATH_REDACTED> \
  --app-secret-path <LOCAL_PATH_REDACTED> \
  --folder-token-path <LOCAL_PATH_REDACTED>
```

Run diagnostics:

```bash
python -m nexus.cli feishu doctor --project-path <target-project>
```

Write a record to Feishu. This calls a real model provider to format the content, then calls the real Feishu API:

```bash
python -m nexus.cli feishu record \
  --project-path <target-project> \
  --title "Nexus record" \
  --content "Record the current project state."
```

Then:

```bash
python -m nexus.cli system-showcase generate --project-path <target-project>
python -m nexus.cli system-showcase publish-feishu --project-path <target-project> --confirm
```

The setup workflow cannot automate Feishu browser-only work such as creating the custom app, copying App Secret, publishing a version, administrator approval, or granting document/folder resource permissions. When those are missing, Nexus writes a setup guide artifact and returns a standard `上一任务状态 / 上一任务输出 / 下一任务提示` response.
