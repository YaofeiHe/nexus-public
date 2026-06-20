from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from typing import Callable, Iterable

from nexus.project_docs import direct_skill_install_command, write_public_readme_for_staging


CONFIG_REL = Path(".github/nexus-sync.json")
DEFAULT_DENYLIST = [".env", ".env.*", ".data/", ".github/nexus-auth/", ".nexus/private/", ".verix/private/", "docs/codex-personalization-registry.md", "secrets/", "*.key", "*token*", "*cookie*", "*apikey*", ".codex/", ".agents/"]
MANDATORY_PUBLIC_DENYLIST = [".nexus/"]
MANDATORY_PUBLIC_EXCLUDES = [
    ".git/",
    "build/",
    "dist/",
    "*.egg-info/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".tox/",
    ".venv/",
    "venv/",
    ".coverage",
    "coverage.xml",
    "htmlcov/",
    "*.pyc",
    "*.pyo",
]
DEFAULT_ALLOWLIST = [".gitignore", "README.md", "docs/", "src/", "tests/", "pyproject.toml", "package.json", "nexus/", "verix/", "skills/", ".github/skills/", ".github/prompts/"]
DEFAULT_PRIVATE_GITIGNORE = [
    ".env",
    ".env.*",
    ".data/",
    ".github/nexus-auth/",
    ".pytest_cache/",
    "__pycache__/",
    ".nexus/private/",
    ".verix/private/",
    "secrets/",
    "*.key",
    "*token*",
    "*cookie*",
    "*apikey*",
    ".codex/",
    ".agents/",
]
PRIVATE_SKIP_DENYLIST = {".data/", ".github/nexus-auth/", ".nexus/private/", ".verix/private/", ".codex/", ".agents/"}
SECRET_ASSIGNMENT_RE = re.compile(r"(?i)\b(api[_-]?key|secret|token|cookie|password|authorization)\b\s*[:=]\s*['\"]?([^'\"\s,;)}\]]+)")
OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}\b")
GITHUB_TOKEN_RE = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{30,}\b")
LOCAL_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w<])(?:/Users/[^`'\"\s),;\]]+|/home/[^`'\"\s),;\]]+|[A-Za-z]:\\Users\\[^`'\"\s),;\]]+)")
FEISHU_URL_RE = re.compile(r"https://[A-Za-z0-9.-]*feishu\.cn/[A-Za-z0-9/_?=&.%-]+")
NEXUS_RUN_ID_RE = re.compile(r"\brun-\d{8}T\d{6}Z-[A-Za-z0-9]{6,}\b")
NEXUS_ARTIFACT_PATH_RE = re.compile(r"(?<![\w/])(?:\.data/runs|\.nexus/runtime|\.github/nexus-auth)/[^`'\"\s),;\]]+")
PUBLIC_PLACEHOLDERS = {"<PROJECT_ROOT>", "<FORGE_ROOT>", "<PRIVATE_REPO>", "<FEISHU_URL_REDACTED>", "<LOCAL_PATH_REDACTED>", "<NEXUS_RUN_ID>", "<NEXUS_ARTIFACT_PATH>"}
PUBLIC_WORKFLOW_ASSET_PATHS = [
    ".agents/skills/*/SKILL.md",
    ".agents/plugins/marketplace.json",
    "plugins/*/.codex-plugin/plugin.json",
    "plugins/*/skills/*/SKILL.md",
]
README_PUBLIC_MARKER = "<!-- nexus:public-install -->"


@dataclass(slots=True)
class GithubSyncConfig:
    private_repo: str
    public_repo: str
    private_remote: str = "private"
    public_remote: str = "public"
    project_kind: str = "user_project"
    default_private_sync: bool = True
    public_sync_requires_confirm: bool = True
    public_allowlist: list[str] | None = None
    public_denylist: list[str] | None = None
    private_denylist: list[str] | None = None
    public_source_roots: str | list[str] = "auto"
    public_required_paths: list[str] | None = None
    public_validation: dict[str, object] | None = None
    public_release: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "nexus.github_sync_config.v3",
            "project_kind": self.project_kind,
            "private_repo": self.private_repo,
            "public_repo": self.public_repo,
            "private_remote": self.private_remote,
            "public_remote": self.public_remote,
            "default_private_sync": self.default_private_sync,
            "public_sync_requires_confirm": self.public_sync_requires_confirm,
            "public_allowlist": self.public_allowlist or list(DEFAULT_ALLOWLIST),
            "public_denylist": self.public_denylist or [*DEFAULT_DENYLIST, *MANDATORY_PUBLIC_DENYLIST],
            "private_denylist": self.private_denylist or list(DEFAULT_DENYLIST),
            "public_source_roots": self.public_source_roots,
            "public_required_paths": self.public_required_paths or [],
            "public_validation": self.public_validation
            or {
                "enabled": True,
                "mode": "real",
                "allow_network": False,
            },
            "public_release": self.public_release
            or {
                "sanitize": True,
                "metadata_policy": "block",
                "fresh_clone_validation": True,
            },
        }


def write_config(project: Path, private_repo: str, public_repo: str, *, project_kind: str = "user_project") -> Path:
    target = project / CONFIG_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = GithubSyncConfig(private_repo=private_repo, public_repo=public_repo, project_kind=project_kind).to_dict()
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def load_config(project: Path) -> dict[str, object] | None:
    path = project / CONFIG_REL
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def gh_status() -> dict[str, object]:
    gh = shutil.which("gh")
    if not gh:
        return {"status": "blocked", "reason": "gh_cli_not_found"}
    completed = _run_github_command([gh, "auth", "status"])
    return {
        "status": "ok" if completed.returncode == 0 else "blocked",
        "reason": "authenticated" if completed.returncode == 0 else "gh_auth_required",
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
        "retry_without_proxy_http2": getattr(completed, "nexus_retry_without_proxy_http2", False),
    }


def bootstrap_project(
    project: Path,
    config: dict[str, object],
    *,
    create_remote_repos: bool = True,
    commit_message: str = "bootstrap project sync",
) -> dict[str, object]:
    project.mkdir(parents=True, exist_ok=True)
    git_result = ensure_git_repo(project)
    ignore_result = ensure_private_gitignore(project)
    private_repo = str(config.get("private_repo") or "")
    public_repo = str(config.get("public_repo") or "")
    repo_result = ensure_github_repositories(private_repo, public_repo, create=create_remote_repos)
    if repo_result["status"] != "completed":
        return {
            "schema": "nexus.github_bootstrap.v1",
            "status": "blocked",
            "reason": repo_result.get("reason", "github_repo_setup_blocked"),
            "git": git_result,
            "gitignore": ignore_result,
            "repositories": repo_result,
        }
    private = auto_private_sync(project, config, commit_message=commit_message)
    return {
        "schema": "nexus.github_bootstrap.v1",
        "status": "completed" if private.get("status") == "completed" else "blocked",
        "reason": "bootstrapped_and_pushed_private" if private.get("status") == "completed" else private.get("reason", "private_sync_blocked"),
        "git": git_result,
        "gitignore": ignore_result,
        "repositories": repo_result,
        "private_sync": private,
    }


def ensure_git_repo(project: Path) -> dict[str, object]:
    existing = subprocess.run(["git", "-C", str(project), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    if existing.returncode == 0:
        return {"schema": "nexus.git_repo_setup.v1", "status": "completed", "reason": "git_repo_exists", "root": existing.stdout.strip()}
    init = subprocess.run(["git", "-C", str(project), "init"], capture_output=True, text=True, check=False)
    return {
        "schema": "nexus.git_repo_setup.v1",
        "status": "completed" if init.returncode == 0 else "blocked",
        "reason": "git_repo_initialized" if init.returncode == 0 else "git_init_failed",
        "stdout": init.stdout[-2000:],
        "stderr": init.stderr[-2000:],
    }


def ensure_private_gitignore(project: Path) -> dict[str, object]:
    path = project / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines()
    changed = False
    for pattern in DEFAULT_PRIVATE_GITIGNORE:
        if pattern not in lines:
            lines.append(pattern)
            changed = True
    if changed:
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {"schema": "nexus.private_gitignore.v1", "status": "completed", "path": str(path), "changed": changed}


def ensure_github_repositories(private_repo: str, public_repo: str = "", *, create: bool = True) -> dict[str, object]:
    if not private_repo:
        return {"schema": "nexus.github_repo_setup.v1", "status": "blocked", "reason": "private_repo_missing"}
    status = gh_status()
    if status["status"] != "ok":
        return {"schema": "nexus.github_repo_setup.v1", **status}
    repos: list[dict[str, object]] = []
    for repo, visibility in [(private_repo, "private"), (public_repo, "public")]:
        if not repo:
            continue
        viewed = _gh_repo_view(repo)
        if viewed["status"] == "completed":
            repos.append({"repo": repo, "visibility": visibility, "status": "exists"})
            continue
        if not create:
            return {"schema": "nexus.github_repo_setup.v1", "status": "blocked", "reason": "github_repo_missing", "repo": repo, "view": viewed}
        created = _gh_repo_create(repo, visibility=visibility)
        if _repo_already_exists(created):
            reviewed = _gh_repo_view(repo)
            repos.append({"repo": repo, "visibility": visibility, "status": "exists_after_create_conflict", "view": reviewed, "create": created})
            continue
        if created["status"] != "completed":
            return {"schema": "nexus.github_repo_setup.v1", "status": "blocked", "reason": "github_repo_create_failed", "repo": repo, "create": created}
        repos.append({"repo": repo, "visibility": visibility, "status": "created"})
    return {"schema": "nexus.github_repo_setup.v1", "status": "completed", "reason": "github_repositories_ready", "repositories": repos}


def auto_private_sync(project: Path, config: dict[str, object], *, commit_message: str = "nexus auto private sync") -> dict[str, object]:
    if config.get("default_private_sync") is False:
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "skipped", "reason": "default_private_sync_disabled"}
    if not project.exists():
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "blocked", "reason": "project_path_not_found", "project": str(project)}
    git_setup = ensure_git_repo(project)
    if git_setup["status"] != "completed":
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "blocked", "reason": git_setup.get("reason", "git_setup_failed"), "git": git_setup}
    ensure_private_gitignore(project)
    scan = scan_private_worktree(project, config)
    if scan["findings"]:
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "blocked", "reason": "private_secret_scan_failed", "scan": scan}
    commit = commit_all(project, commit_message)
    if commit["status"] == "blocked":
        return {"schema": "nexus.github_auto_private_sync.v1", "status": "blocked", "reason": commit.get("reason", "commit_blocked"), "scan": scan, "commit": commit}
    push = sync_private(project, config)
    return {
        "schema": "nexus.github_auto_private_sync.v1",
        "status": "completed" if push.get("status") == "completed" else "blocked",
        "reason": "private_synced" if push.get("status") == "completed" else push.get("reason", "git_push_failed"),
        "scan": scan,
        "commit": commit,
        "push": push,
    }


def commit_all(project: Path, message: str) -> dict[str, object]:
    status = subprocess.run(["git", "-C", str(project), "status", "--porcelain=v1"], capture_output=True, text=True, check=False)
    if status.returncode != 0:
        return {"schema": "nexus.git_commit_all.v1", "status": "blocked", "reason": "git_status_failed", "stderr": status.stderr[-2000:]}
    if not status.stdout.strip():
        return {"schema": "nexus.git_commit_all.v1", "status": "completed", "reason": "nothing_to_commit"}
    add = subprocess.run(["git", "-C", str(project), "add", "-A"], capture_output=True, text=True, check=False)
    if add.returncode != 0:
        return {"schema": "nexus.git_commit_all.v1", "status": "blocked", "reason": "git_add_failed", "stderr": add.stderr[-2000:]}
    commit = subprocess.run(["git", "-C", str(project), "-c", "user.name=Nexus", "-c", "user.email=nexus@example.invalid", "commit", "-m", message], capture_output=True, text=True, check=False)
    if commit.returncode != 0:
        return {"schema": "nexus.git_commit_all.v1", "status": "blocked", "reason": "git_commit_failed", "stdout": commit.stdout[-4000:], "stderr": commit.stderr[-4000:]}
    rev = subprocess.run(["git", "-C", str(project), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False)
    return {"schema": "nexus.git_commit_all.v1", "status": "completed", "reason": "committed", "commit": rev.stdout.strip(), "stdout": commit.stdout[-2000:]}


def scan_private_worktree(project: Path, config: dict[str, object]) -> dict[str, object]:
    deny = [str(item) for item in config.get("private_denylist", DEFAULT_DENYLIST) if isinstance(item, str)]
    findings: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for path in _iter_project_files(project):
        rel = str(path.relative_to(project))
        matched = _matching_pattern(rel, deny)
        if matched:
            if _skip_private_denied_path(matched):
                skipped.append({"file": rel, "reason": "ignored_private_runtime_path", "pattern": matched})
                continue
            findings.append({"file": rel, "type": "denylist_path", "pattern": matched})
            continue
        if path.stat().st_size > 2_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        findings.extend({"file": rel, **finding} for finding in _scan_text_for_secrets(text))
    return {"schema": "nexus.private_secret_scan.v1", "findings": findings, "skipped": skipped, "scanned_at": datetime.now(timezone.utc).isoformat()}


def sync_private(project: Path, config: dict[str, object]) -> dict[str, object]:
    status = gh_status()
    if status["status"] != "ok":
        return {"schema": "nexus.github_private_sync.v1", **status}
    git_auth = _gh_setup_git()
    if git_auth["status"] != "completed":
        return {"schema": "nexus.github_private_sync.v1", "status": "blocked", "reason": "gh_git_auth_setup_failed", "git_auth": git_auth}
    repo = str(config.get("private_repo") or "")
    remote = str(config.get("private_remote") or "private")
    if not repo:
        return {"schema": "nexus.github_private_sync.v1", "status": "blocked", "reason": "private_repo_missing"}
    repo_setup = _ensure_single_github_repository(repo, visibility="private", create=True)
    if repo_setup["status"] != "completed":
        return {"schema": "nexus.github_private_sync.v1", "status": "blocked", "reason": repo_setup.get("reason", "github_repo_setup_blocked"), "repo_setup": repo_setup}
    _ensure_remote(project, remote, repo)
    push = _run_github_command(["git", "-C", str(project), "push", "-u", remote, "HEAD:main"])
    if push.returncode != 0 and _is_non_fast_forward_push_rejection(push.stderr):
        branch = _private_fallback_branch(project)
        branch_push = _run_github_command(["git", "-C", str(project), "push", "-u", remote, f"HEAD:{branch}"])
        return {
            "schema": "nexus.github_private_sync.v1",
            "status": "completed" if branch_push.returncode == 0 else "blocked",
            "reason": "pushed_private_fallback_branch" if branch_push.returncode == 0 else "git_push_failed_non_fast_forward_fallback_failed",
            "remote": remote,
            "repo": repo,
            "repo_setup": repo_setup,
            "git_auth": git_auth,
            "main_push": {
                "status": "blocked",
                "reason": "non_fast_forward_remote_main_preserved",
                "stdout": push.stdout[-4000:],
                "stderr": push.stderr[-4000:],
            },
            "fallback_branch": branch,
            "fallback_push": {
                "status": "completed" if branch_push.returncode == 0 else "blocked",
                "stdout": branch_push.stdout[-4000:],
                "stderr": branch_push.stderr[-4000:],
                "retry_without_proxy_http2": getattr(branch_push, "nexus_retry_without_proxy_http2", False),
            },
        }
    return {
        "schema": "nexus.github_private_sync.v1",
        "status": "completed" if push.returncode == 0 else "blocked",
        "reason": "pushed_private" if push.returncode == 0 else "git_push_failed",
        "remote": remote,
        "repo": repo,
        "repo_setup": repo_setup,
        "git_auth": git_auth,
        "stdout": push.stdout[-4000:],
        "stderr": push.stderr[-4000:],
        "retry_without_proxy_http2": getattr(push, "nexus_retry_without_proxy_http2", False),
    }


def prepare_public_staging(project: Path, config: dict[str, object], staging: Path) -> dict[str, object]:
    discovery = discover_public_source_roots(project, config)
    allow = [str(item) for item in config.get("public_allowlist", DEFAULT_ALLOWLIST) if isinstance(item, str)]
    allow.extend(str(item) for item in discovery.get("source_roots", []) if isinstance(item, str))
    deny = _public_denylist(config)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    copied: list[str] = []
    blocked: list[str] = []
    for pattern in allow:
        for source in _expand(project, pattern):
            rel = source.relative_to(project)
            if _denied(str(rel), deny):
                blocked.append(str(rel))
                continue
            if source.is_dir():
                copied.extend(_copy_public_dir(project, source, staging, deny, blocked, config))
            elif source.is_file():
                target = staging / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.append(str(rel))
    readme_action = write_public_readme_for_staging(staging, project, public_repo=str(config.get("public_repo") or ""))
    workflow_assets = write_public_workflow_assets(project, staging, config)
    copied.extend(str(item) for item in workflow_assets.get("written", []) if isinstance(item, str))
    sanitization = sanitize_public_staging(staging, project, config)
    scan = scan_public_staging(staging, config)
    return {
        "schema": "nexus.github_public_staging.v1",
        "status": "completed" if not scan["findings"] else "blocked",
        "copied": sorted(set(copied)),
        "blocked": sorted(set(blocked)),
        "discovery": discovery,
        "readme_action": readme_action,
        "workflow_assets": workflow_assets,
        "sanitization": sanitization,
        "scan": scan,
        "staging": str(staging),
    }


def write_public_workflow_assets(project: Path, staging: Path, config: dict[str, object]) -> dict[str, object]:
    skills = _discover_workflow_skills(project)
    written: list[str] = []
    marketplace: list[dict[str, object]] = []
    for skill in skills:
        name = str(skill["name"])
        description = _public_skill_description(str(skill["description"] or ""), name)
        content = _public_skill_markdown(project, config, name=name, description=description)
        targets = [
            staging / ".agents" / "skills" / name / "SKILL.md",
            staging / "plugins" / name / "skills" / name / "SKILL.md",
        ]
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(str(target.relative_to(staging)))
        manifest = staging / "plugins" / name / ".codex-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "v1",
                    "name": name,
                    "version": "0.1.0",
                    "description": description,
                    "skills": [f"skills/{name}/SKILL.md"],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        written.append(str(manifest.relative_to(staging)))
        marketplace.append({"name": name, "path": f"plugins/{name}", "description": description})
    if marketplace:
        marketplace_path = staging / ".agents" / "plugins" / "marketplace.json"
        marketplace_path.parent.mkdir(parents=True, exist_ok=True)
        marketplace_path.write_text(json.dumps({"plugins": marketplace}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(str(marketplace_path.relative_to(staging)))
    return {"schema": "nexus.public_workflow_assets.v1", "status": "completed", "skills": skills, "written": sorted(set(written))}


def discover_public_source_roots(project: Path, config: dict[str, object]) -> dict[str, object]:
    configured = config.get("public_source_roots", "auto")
    if isinstance(configured, list):
        roots = [str(item).rstrip("/") + ("/" if (project / str(item).rstrip("/")).is_dir() else "") for item in configured if isinstance(item, str)]
        return {
            "schema": "nexus.public_source_discovery.v1",
            "mode": "configured",
            "source_roots": sorted(set(roots)),
            "required_paths": _required_paths_from_config_or_roots(config, roots),
            "import_modules": _validation_import_modules(project, config, roots),
            "smoke_commands": _validation_smoke_commands(project, config),
            "test_commands": _validation_test_commands(project, config),
        }

    pyproject = _read_pyproject(project)
    modules = _modules_from_pyproject(pyproject)
    roots: list[str] = []
    for module in modules:
        root_name = module.split(".", 1)[0].replace("-", "_")
        for candidate in (Path("src") / root_name, Path(root_name)):
            if (project / candidate).is_dir():
                roots.append(str(candidate).rstrip("/") + "/")
    for path in _pytest_paths(pyproject):
        if (project / path).exists():
            roots.append(str(path).rstrip("/") + ("/" if (project / path).is_dir() else ""))
    for default in ("README.md", "pyproject.toml"):
        if (project / default).exists():
            roots.append(default)
    roots = sorted(set(roots))
    return {
        "schema": "nexus.public_source_discovery.v1",
        "mode": "auto",
        "source_roots": roots,
        "required_paths": _required_paths_from_config_or_roots(config, roots),
        "import_modules": _validation_import_modules(project, config, roots, pyproject=pyproject),
        "smoke_commands": _validation_smoke_commands(project, config, pyproject=pyproject),
        "test_commands": _validation_test_commands(project, config, pyproject=pyproject),
    }


def sanitize_public_staging(staging: Path, project: Path, config: dict[str, object]) -> dict[str, object]:
    release = _public_release_config(config)
    if not release["sanitize"]:
        return {"schema": "nexus.github_public_sanitization.v1", "status": "skipped", "reason": "public_sanitization_disabled", "changed_files": []}

    replacements = _public_sanitization_replacements(project, config)
    changed_files: list[dict[str, object]] = []
    for path in staging.rglob("*"):
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        updated = original
        applied: dict[str, int] = {}
        for label, old, new in replacements:
            if old and old in updated:
                if label == "private_repo":
                    updated, count = _replace_private_repo_refs(updated, old, new)
                else:
                    count = updated.count(old)
                    updated = updated.replace(old, new)
                applied[label] = applied.get(label, 0) + count
        for label, pattern, repl in _public_sanitization_regexes():
            updated, count = pattern.subn(repl, updated)
            if count:
                applied[label] = applied.get(label, 0) + count
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed_files.append({"file": str(path.relative_to(staging)), "replacements": applied})

    return {
        "schema": "nexus.github_public_sanitization.v1",
        "status": "completed",
        "changed_files": changed_files,
        "replacement_count": sum(sum(int(count) for count in item["replacements"].values()) for item in changed_files),
    }


def validate_public_staging(staging: Path, config: dict[str, object]) -> dict[str, object]:
    validation_dir = staging.parent / f"{staging.name}-validation-workspace"
    result = _validate_public_tree(staging, config, schema="nexus.github_public_validation.v1", validation_dir=validation_dir)
    result["release_tree"] = assert_public_release_tree_clean(staging, config)
    if result.get("status") == "completed" and result["release_tree"].get("status") == "blocked":
        result["status"] = "blocked"
        result["blocked_reason"] = str(result["release_tree"].get("reason") or "public_release_tree_dirty")
    return result


def _validate_public_tree(source: Path, config: dict[str, object], *, schema: str, validation_dir: Path) -> dict[str, object]:
    validation = _public_validation_config(config)
    if not str(validation.get("install_command") or "").strip() and (source / "pyproject.toml").exists():
        validation["install_command"] = "python -m pip install --no-deps ."
    required_paths = [str(item).rstrip("/") + ("/" if str(item).endswith("/") else "") for item in validation["required_paths"]]
    missing = [item for item in required_paths if not (source / item.rstrip("/")).exists()]
    readme_check = validate_public_readme(source, config)
    workflow_check = validate_public_workflow_assets(source, config)
    result: dict[str, object] = {
        "schema": schema,
        "status": "completed",
        "source": str(source),
        "validation_workspace": str(validation_dir),
        "required_paths": required_paths,
        "missing_paths": missing,
        "readme": readme_check,
        "workflow_assets": workflow_check,
        "install": {},
        "imports": [],
        "smoke_commands": [],
        "tests": [],
        "blocked_reason": None,
    }
    if missing:
        result["status"] = "blocked"
        result["blocked_reason"] = "public_required_path_missing"
        return result
    if readme_check["status"] == "blocked":
        result["status"] = "blocked"
        result["blocked_reason"] = "public_readme_invalid"
        return result
    if workflow_check["status"] == "blocked":
        result["status"] = "blocked"
        result["blocked_reason"] = "public_workflow_assets_invalid"
        return result
    if not validation["enabled"]:
        result["status"] = "skipped"
        result["reason"] = "public_validation_disabled"
        return result
    if validation["mode"] != "real":
        return {"schema": schema, "status": "blocked", "blocked_reason": "public_validation_mode_not_real", "mode": validation["mode"], "readme": readme_check, "workflow_assets": workflow_check}

    if validation_dir.exists():
        shutil.rmtree(validation_dir)
    shutil.copytree(source, validation_dir, ignore=shutil.ignore_patterns(".git"))

    env = _public_validation_env()
    env["PYTHONNOUSERSITE"] = "1"
    if not validation["allow_network"]:
        env["PIP_NO_INDEX"] = "1"
    venv_dir = validation_dir.parent / f"{validation_dir.name}-venv"
    venv_result = _create_validation_venv(venv_dir)
    result["venv"] = venv_result
    if venv_result["status"] != "completed":
        result["status"] = "blocked"
        result["blocked_reason"] = "public_validation_venv_failed"
        return result
    env["PATH"] = f"{_venv_bin(venv_dir)}{os.pathsep}{env.get('PATH', '')}"
    env["VIRTUAL_ENV"] = str(venv_dir)

    install_command = str(validation["install_command"] or "").strip()
    if install_command:
        install = _run_validation_command(install_command, cwd=validation_dir, env=env)
        result["install"] = install
        if install["returncode"] != 0:
            result["status"] = "blocked"
            result["blocked_reason"] = "public_install_failed"
            return result

    imports: list[dict[str, object]] = []
    for module in validation["import_modules"]:
        command = f'python -c "import {module}"'
        imports.append({"module": module, **_run_validation_command(command, cwd=validation_dir, env=env)})
    result["imports"] = imports
    if any(item["returncode"] != 0 for item in imports):
        result["status"] = "blocked"
        result["blocked_reason"] = "public_import_failed"
        return result

    smoke = [_run_validation_command(str(command), cwd=validation_dir, env=env) for command in validation["smoke_commands"]]
    result["smoke_commands"] = smoke
    if any(item["returncode"] != 0 for item in smoke):
        result["status"] = "blocked"
        result["blocked_reason"] = "public_smoke_command_failed"
        return result

    tests = [_run_validation_command(str(command), cwd=validation_dir, env=env) for command in validation["test_commands"]]
    result["tests"] = tests
    if any(item["returncode"] != 0 for item in tests):
        result["status"] = "blocked"
        result["blocked_reason"] = "public_tests_failed"
        return result

    return result


def validate_public_fresh_clone(staging: Path, config: dict[str, object], clone_dir: Path) -> dict[str, object]:
    release = _public_release_config(config)
    if not release["fresh_clone_validation"]:
        return {"schema": "nexus.github_public_fresh_clone_validation.v1", "status": "skipped", "reason": "public_fresh_clone_validation_disabled"}
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    shutil.copytree(staging, clone_dir, ignore=shutil.ignore_patterns(".git"))
    validation_dir = clone_dir.parent / f"{clone_dir.name}-validation-workspace"
    result = _validate_public_tree(clone_dir, config, schema="nexus.github_public_fresh_clone_validation.v1", validation_dir=validation_dir)
    result["fresh_clone"] = str(clone_dir)
    result["release_tree"] = assert_public_release_tree_clean(clone_dir, config)
    if result.get("status") == "completed" and result["release_tree"].get("status") == "blocked":
        result["status"] = "blocked"
        result["blocked_reason"] = str(result["release_tree"].get("reason") or "public_release_tree_dirty")
    return result


def _public_validation_config(config: dict[str, object]) -> dict[str, object]:
    discovery = config.get("_public_discovery") if isinstance(config.get("_public_discovery"), dict) else {}
    raw = config.get("public_validation") if isinstance(config.get("public_validation"), dict) else {}
    enabled = bool(raw.get("enabled", True))
    mode = str(raw.get("mode") or "real")
    allow_network = bool(raw.get("allow_network", False))
    install_command = str(raw.get("install_command") or "")
    if not install_command and "pyproject.toml" in discovery.get("required_paths", []):
        install_command = "python -m pip install --no-deps ."
    required_paths = _strings(raw.get("required_paths")) or _strings(config.get("public_required_paths")) or _strings(discovery.get("required_paths"))
    if "README.md" not in required_paths:
        required_paths = ["README.md", *required_paths]
    return {
        "enabled": enabled,
        "mode": mode,
        "allow_network": allow_network,
        "install_command": install_command,
        "required_paths": required_paths,
        "import_modules": _strings(raw.get("import_modules")) or _strings(discovery.get("import_modules")),
        "smoke_commands": _strings(raw.get("smoke_commands")) or _strings(discovery.get("smoke_commands")),
        "test_commands": _strings(raw.get("test_commands")) or _strings(discovery.get("test_commands")),
    }


def _public_validation_env() -> dict[str, str]:
    sensitive_name_parts = ("KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE", "AUTH")
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(part in key.upper() for part in sensitive_name_parts)
    }
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def _public_release_config(config: dict[str, object]) -> dict[str, object]:
    raw = config.get("public_release") if isinstance(config.get("public_release"), dict) else {}
    metadata_policy = str(raw.get("metadata_policy") or config.get("public_metadata_policy") or "block")
    return {
        "sanitize": bool(raw.get("sanitize", True)),
        "metadata_policy": metadata_policy if metadata_policy in {"block", "warn"} else "block",
        "metadata_allow_patterns": _strings(raw.get("metadata_allow_patterns")) or _strings(config.get("public_metadata_allow_patterns")),
        "fresh_clone_validation": bool(raw.get("fresh_clone_validation", True)),
        "exclude_generated": bool(raw.get("exclude_generated", True)),
        "generated_exclude_patterns": _strings(raw.get("generated_exclude_patterns")),
    }


def _public_sanitization_replacements(project: Path, config: dict[str, object]) -> list[tuple[str, str, str]]:
    replacements = [
        ("project_root", str(project), "<PROJECT_ROOT>"),
        ("project_root_posix", project.as_posix(), "<PROJECT_ROOT>"),
        ("forge_root", str(project.parent), "<FORGE_ROOT>"),
        ("forge_root_posix", project.parent.as_posix(), "<FORGE_ROOT>"),
    ]
    private_repo = str(config.get("private_repo") or "")
    public_repo = str(config.get("public_repo") or "")
    if private_repo and private_repo != public_repo:
        replacements.append(("private_repo", private_repo, "<PRIVATE_REPO>"))
    return replacements


def _replace_private_repo_refs(text: str, private_repo: str, replacement: str) -> tuple[str, int]:
    pattern = re.compile(re.escape(private_repo) + r"(?![-A-Za-z0-9_])")
    return pattern.subn(replacement, text)


def _private_repo_ref_count(text: str, private_repo: str) -> int:
    return len(re.findall(re.escape(private_repo) + r"(?![-A-Za-z0-9_])", text))


def _public_sanitization_regexes() -> list[tuple[str, re.Pattern[str], str | Callable[[re.Match[str]], str]]]:
    return [
        ("feishu_url", FEISHU_URL_RE, "<FEISHU_URL_REDACTED>"),
        ("local_absolute_path", LOCAL_ABSOLUTE_PATH_RE, _redact_local_path_match),
        ("nexus_run_id", NEXUS_RUN_ID_RE, "<NEXUS_RUN_ID>"),
        ("nexus_artifact_path", NEXUS_ARTIFACT_PATH_RE, "<NEXUS_ARTIFACT_PATH>"),
    ]


def _redact_local_path_match(match: re.Match[str]) -> str:
    value = match.group(0)
    if _looks_like_regex_path_pattern(value):
        return value
    return "<LOCAL_PATH_REDACTED>"


def _looks_like_regex_path_pattern(value: str) -> bool:
    return "[^" in value or "\\s" in value or "\\\\" in value


def _copy_public_dir(project: Path, source: Path, staging: Path, deny: list[str], blocked: list[str], config: dict[str, object]) -> list[str]:
    copied: list[str] = []
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(project)
        rel_text = str(rel)
        if _denied(rel_text, deny):
            blocked.append(rel_text)
            continue
        if _public_release_path_excluded(rel_text, config):
            continue
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied.append(rel_text)
    if not copied and not blocked:
        rel = source.relative_to(project)
        (staging / rel).mkdir(parents=True, exist_ok=True)
    return copied


def _read_pyproject(project: Path) -> dict[str, object]:
    path = project / "pyproject.toml"
    if not path.exists():
        return {}
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _discover_workflow_skills(project: Path) -> list[dict[str, str]]:
    skills: dict[str, dict[str, str]] = {}
    for root in [project / "skills", project / ".github" / "skills"]:
        for path in sorted(root.glob("*/SKILL.md")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            name = _frontmatter_value(text, "name") or path.parent.name
            if not name:
                continue
            skills[name] = {
                "name": name,
                "description": _frontmatter_value(text, "description") or f"Run {name}.",
                "source": str(path.relative_to(project)),
            }
    return [skills[name] for name in sorted(skills)]


def _public_skill_markdown(project: Path, config: dict[str, object], *, name: str, description: str) -> str:
    public_repo = str(config.get("public_repo") or "OWNER/project-public")
    commands = _public_cli_commands(project)
    if not commands:
        modules = _modules_from_pyproject(_read_pyproject(project))
        commands = [f"python -m {modules[0]} --help"] if modules else ["python -m <module> --help"]
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "---",
        "",
        f"# {name}",
        "",
        "This public workflow skill is for a fresh public repository install.",
        "",
        "Install the package first:",
        "",
        "```bash",
        _public_install_command(public_repo),
        "```",
        "",
        "Use the installed CLI or module entrypoint; do not call a private local checkout path.",
        "",
        "```bash",
        *commands,
        "```",
        "",
        "Do not read local credentials, private runtime directories, `.env`, tokens, cookies, browser profiles, or host-specific paths.",
        "",
    ]
    return "\n".join(lines)


def _public_skill_description(description: str, name: str) -> str:
    text = description.strip() or f"Run {name} from a public package install."
    if "<PROJECT_ROOT>" in text or LOCAL_ABSOLUTE_PATH_RE.search(text):
        return f"Run {name} from the installed public package CLI."
    return text


def _frontmatter_value(text: str, key: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        if name.strip() == key:
            return value.strip().strip("'\"")
    return ""


def _modules_from_pyproject(pyproject: dict[str, object]) -> list[str]:
    modules: list[str] = []
    project = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
    name = str(project.get("name") or "").replace("-", "_")
    if name:
        modules.append(name)
    scripts = project.get("scripts") if isinstance(project.get("scripts"), dict) else {}
    for target in scripts.values():
        if not isinstance(target, str):
            continue
        module = target.split(":", 1)[0].strip()
        if module:
            modules.append(module)
            modules.append(module.split(".", 1)[0])
    return sorted(set(modules))


def _pytest_paths(pyproject: dict[str, object]) -> list[Path]:
    tool = pyproject.get("tool") if isinstance(pyproject.get("tool"), dict) else {}
    pytest_cfg = tool.get("pytest") if isinstance(tool.get("pytest"), dict) else {}
    options = pytest_cfg.get("ini_options") if isinstance(pytest_cfg.get("ini_options"), dict) else {}
    paths: list[Path] = []
    value = options.get("testpaths")
    if isinstance(value, str):
        paths.append(Path(value))
    elif isinstance(value, list):
        paths.extend(Path(str(item)) for item in value if isinstance(item, str))
    return paths


def _validation_import_modules(project: Path, config: dict[str, object], roots: list[str], *, pyproject: dict[str, object] | None = None) -> list[str]:
    raw = config.get("public_validation") if isinstance(config.get("public_validation"), dict) else {}
    configured = _strings(raw.get("import_modules"))
    if configured:
        return configured
    modules = _modules_from_pyproject(pyproject if pyproject is not None else _read_pyproject(project))
    for root in roots:
        clean = root.rstrip("/")
        if "/" not in clean and (project / clean / "__init__.py").exists():
            modules.append(clean)
        if clean.startswith("src/"):
            pkg = clean.split("/", 1)[1]
            if pkg and (project / clean / "__init__.py").exists():
                modules.append(pkg)
    return sorted(set(module for module in modules if module))


def _validation_smoke_commands(project: Path, config: dict[str, object], *, pyproject: dict[str, object] | None = None) -> list[str]:
    raw = config.get("public_validation") if isinstance(config.get("public_validation"), dict) else {}
    configured = _strings(raw.get("smoke_commands"))
    if configured:
        return configured
    pyproject = pyproject if pyproject is not None else _read_pyproject(project)
    project_section = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
    scripts = project_section.get("scripts") if isinstance(project_section.get("scripts"), dict) else {}
    return [f"{name} --help" for name in scripts if isinstance(name, str)]


def _validation_test_commands(project: Path, config: dict[str, object], *, pyproject: dict[str, object] | None = None) -> list[str]:
    raw = config.get("public_validation") if isinstance(config.get("public_validation"), dict) else {}
    configured = _strings(raw.get("test_commands"))
    if configured:
        return configured
    pyproject = pyproject if pyproject is not None else _read_pyproject(project)
    if any((project / path).exists() for path in _pytest_paths(pyproject)) or (project / "tests").is_dir():
        return ["python -m pytest -q"]
    return []


def validate_public_readme(source: Path, config: dict[str, object]) -> dict[str, object]:
    path = source / "README.md"
    findings: list[dict[str, object]] = []
    if not path.exists():
        return {"schema": "nexus.public_readme_validation.v1", "status": "blocked", "findings": [{"file": "README.md", "reason": "public_readme_missing"}]}
    text = path.read_text(encoding="utf-8", errors="ignore")
    public_repo = str(config.get("public_repo") or "")
    private_repo = str(config.get("private_repo") or "")
    if public_repo:
        install = _public_install_command(public_repo)
        if install not in text:
            findings.append({"file": "README.md", "reason": "public_install_command_missing", "expected": install})
    if private_repo and private_repo != public_repo and _private_repo_ref_count(text, private_repo):
        findings.append({"file": "README.md", "reason": "private_repo_leaked"})
    for label, pattern in [("local_absolute_path", LOCAL_ABSOLUTE_PATH_RE), ("feishu_url", FEISHU_URL_RE), ("nexus_run_id", NEXUS_RUN_ID_RE)]:
        if pattern.search(text):
            findings.append({"file": "README.md", "reason": label})
    if "<PRIVATE_REPO>" in text or "<PROJECT_ROOT>" in text:
        findings.append({"file": "README.md", "reason": "non_copyable_placeholder_found"})
    commands = _public_cli_commands(source)
    if commands and not any(command in text for command in commands):
        findings.append({"file": "README.md", "reason": "public_smoke_command_missing", "expected_any": commands})
    if _staged_workflow_skill_names(source) and public_repo:
        skill_paths = _staged_repo_skill_paths(source)
        if not skill_paths:
            findings.append({"file": "README.md", "reason": "public_skill_source_missing"})
        else:
            install_command = direct_skill_install_command(public_repo, skill_paths)
            if install_command not in text:
                findings.append({"file": "README.md", "reason": "codex_skill_install_command_missing", "expected": install_command})
    return {"schema": "nexus.public_readme_validation.v1", "status": "blocked" if findings else "completed", "findings": findings}


def validate_public_workflow_assets(source: Path, config: dict[str, object]) -> dict[str, object]:
    skill_names = _staged_workflow_skill_names(source)
    findings: list[dict[str, object]] = []
    if not skill_names:
        return {"schema": "nexus.public_workflow_assets_validation.v1", "status": "completed", "skills": [], "findings": []}
    if not (source / ".agents" / "plugins" / "marketplace.json").exists():
        findings.append({"file": ".agents/plugins/marketplace.json", "reason": "workflow_asset_required_path_missing"})
    private_repo = str(config.get("private_repo") or "")
    commands = _public_cli_commands(source)
    for name in skill_names:
        required = [
            source / ".agents" / "skills" / name / "SKILL.md",
            source / "plugins" / name / ".codex-plugin" / "plugin.json",
            source / "plugins" / name / "skills" / name / "SKILL.md",
        ]
        for path in required:
            if not path.exists():
                findings.append({"file": str(path.relative_to(source)), "reason": "workflow_asset_required_path_missing"})
                continue
            if path.suffix == ".json":
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    findings.append({"file": str(path.relative_to(source)), "reason": "workflow_plugin_manifest_invalid"})
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "<PROJECT_ROOT>" in text or LOCAL_ABSOLUTE_PATH_RE.search(text):
                findings.append({"file": str(path.relative_to(source)), "reason": "workflow_skill_contains_local_path"})
            if private_repo and _private_repo_ref_count(text, private_repo):
                findings.append({"file": str(path.relative_to(source)), "reason": "workflow_skill_contains_private_repo"})
            if commands and not any(command in text for command in commands):
                findings.append({"file": str(path.relative_to(source)), "reason": "workflow_skill_public_command_missing", "expected_any": commands})
    return {"schema": "nexus.public_workflow_assets_validation.v1", "status": "blocked" if findings else "completed", "skills": skill_names, "findings": findings}


def _public_cli_commands(project: Path) -> list[str]:
    pyproject = _read_pyproject(project)
    project_section = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
    scripts = project_section.get("scripts") if isinstance(project_section.get("scripts"), dict) else {}
    commands = [f"{name} --help" for name in scripts if isinstance(name, str)]
    if commands:
        return commands
    modules = _modules_from_pyproject(pyproject)
    return [f"python -m {module} --help" for module in modules[:1]]


def _public_install_command(public_repo: str) -> str:
    return f"python -m pip install git+https://github.com/{public_repo}.git"


def _staged_workflow_skill_names(source: Path) -> list[str]:
    names = {path.parent.name for root in [source / "skills", source / ".github" / "skills", source / ".agents" / "skills"] for path in root.glob("*/SKILL.md")}
    return sorted(name for name in names if name)


def _staged_repo_skill_paths(source: Path) -> list[str]:
    paths = {
        str(path.relative_to(source))
        for root in [source / "skills", source / ".github" / "skills"]
        for path in root.glob("*/SKILL.md")
    }
    return sorted(path for path in paths if path)


def _required_paths_from_config_or_roots(config: dict[str, object], roots: list[str]) -> list[str]:
    configured = _strings(config.get("public_required_paths"))
    if configured:
        return configured
    return sorted(set(roots))


def _create_validation_venv(path: Path) -> dict[str, object]:
    if path.exists():
        shutil.rmtree(path)
    completed = subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(path)], capture_output=True, text=True, check=False)
    return {
        "schema": "nexus.public_validation_venv.v1",
        "status": "completed" if completed.returncode == 0 else "blocked",
        "returncode": completed.returncode,
        "path": str(path),
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def _venv_bin(path: Path) -> Path:
    return path / ("Scripts" if os.name == "nt" else "bin")


def _run_validation_command(command: str, *, cwd: Path, env: dict[str, str]) -> dict[str, object]:
    try:
        args = shlex.split(command)
    except ValueError as exc:
        return {"command": command, "cwd": str(cwd), "returncode": 2, "stdout_tail": "", "stderr_tail": str(exc)}
    try:
        completed = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return {"command": command, "cwd": str(cwd), "returncode": 127, "stdout_tail": "", "stderr_tail": str(exc)}
    return {
        "command": command,
        "cwd": str(cwd),
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def _strings(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) and str(item).strip()]
    return []


def _public_denylist(config: dict[str, object]) -> list[str]:
    deny = [str(item) for item in config.get("public_denylist", DEFAULT_DENYLIST) if isinstance(item, str)]
    for pattern in MANDATORY_PUBLIC_DENYLIST:
        if pattern not in deny:
            deny.append(pattern)
    return deny


def assert_public_release_tree_clean(staging: Path, config: dict[str, object]) -> dict[str, object]:
    deny = _public_denylist(config)
    generated: list[str] = []
    denied: list[str] = []
    file_count = 0
    for path in staging.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(staging))
        file_count += 1
        if _denied(rel, deny, allow_public_workflow_assets=True):
            denied.append(rel)
        if _public_release_path_excluded(rel, config):
            generated.append(rel)
    scan = scan_public_staging(staging, config)
    status = "completed" if not denied and not generated and not scan.get("findings") else "blocked"
    reason = ""
    if denied:
        reason = "public_denied_path_found"
    elif generated:
        reason = "public_generated_artifact_found"
    elif scan.get("findings"):
        reason = "public_secret_scan_failed"
    return {
        "schema": "nexus.public_release_tree.v1",
        "status": status,
        "reason": reason,
        "file_count": file_count,
        "denied_paths": sorted(denied),
        "generated_paths": sorted(generated),
        "scan": scan,
    }


def _public_release_path_excluded(rel: str, config: dict[str, object]) -> bool:
    release = _public_release_config(config)
    if not release["exclude_generated"]:
        return False
    patterns = [*MANDATORY_PUBLIC_EXCLUDES, *release["generated_exclude_patterns"]]
    return any(_path_matches_public_pattern(rel, pattern) for pattern in patterns)


def _path_matches_public_pattern(rel: str, pattern: str) -> bool:
    normalized = pattern.strip()
    if not normalized:
        return False
    rel = rel.strip("/")
    parts = rel.split("/")
    if normalized.endswith("/"):
        prefix = normalized.strip("/")
        return rel == prefix or rel.startswith(prefix + "/") or any(fnmatch.fnmatch(part, prefix) for part in parts)
    return fnmatch.fnmatch(rel, normalized) or any(fnmatch.fnmatch(part, normalized) for part in parts)


def sync_public(project: Path, config: dict[str, object], staging: Path) -> dict[str, object]:
    status = gh_status()
    if status["status"] != "ok":
        return {"schema": "nexus.github_public_sync.v1", **status}
    git_auth = _gh_setup_git()
    if git_auth["status"] != "completed":
        return {"schema": "nexus.github_public_sync.v1", "status": "blocked", "reason": "gh_git_auth_setup_failed", "git_auth": git_auth}
    repo = str(config.get("public_repo") or "")
    if not repo:
        return {"schema": "nexus.github_public_sync.v1", "status": "blocked", "reason": "public_repo_missing"}
    repo_setup = _ensure_single_github_repository(repo, visibility="public", create=True)
    if repo_setup["status"] != "completed":
        return {"schema": "nexus.github_public_sync.v1", "status": "blocked", "reason": repo_setup.get("reason", "github_repo_setup_blocked"), "repo_setup": repo_setup}
    release_tree = assert_public_release_tree_clean(staging, config)
    if release_tree.get("status") == "blocked":
        return {"schema": "nexus.github_public_sync.v1", "status": "blocked", "reason": str(release_tree.get("reason") or "public_release_tree_dirty"), "repo": repo, "repo_setup": repo_setup, "git_auth": git_auth, "release_tree": release_tree}
    subprocess.run(["git", "-C", str(staging), "init"], capture_output=True, text=True, check=False)
    subprocess.run(["git", "-C", str(staging), "-c", "user.name=Nexus", "-c", "user.email=nexus@example.invalid", "add", "-A"], capture_output=True, text=True, check=False)
    subprocess.run(["git", "-C", str(staging), "-c", "user.name=Nexus", "-c", "user.email=nexus@example.invalid", "commit", "-m", "public sync"], capture_output=True, text=True, check=False)
    _ensure_remote(staging, "public", repo)
    push = _run_github_command(["git", "-C", str(staging), "push", "-f", "public", "HEAD:main"])
    return {"schema": "nexus.github_public_sync.v1", "status": "completed" if push.returncode == 0 else "blocked", "reason": "pushed_public" if push.returncode == 0 else "git_push_failed", "repo": repo, "repo_setup": repo_setup, "git_auth": git_auth, "release_tree": release_tree, "stdout": push.stdout[-4000:], "stderr": push.stderr[-4000:], "retry_without_proxy_http2": getattr(push, "nexus_retry_without_proxy_http2", False)}


def scan_public_staging(staging: Path, config: dict[str, object] | None = None) -> dict[str, object]:
    config = config or {}
    release = _public_release_config(config)
    findings = []
    warnings = []
    critical_findings = []
    metadata_findings = []
    for path in staging.rglob("*"):
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = str(path.relative_to(staging))
        critical_findings.extend({"file": rel, "severity": "critical", **finding} for finding in _scan_text_for_secrets(text))
        metadata_findings.extend(_scan_text_for_public_metadata(text, rel, config))
    findings.extend(critical_findings)
    if release["metadata_policy"] == "block":
        findings.extend(metadata_findings)
    else:
        warnings.extend(metadata_findings)
    return {
        "schema": "nexus.public_secret_scan.v2",
        "findings": findings,
        "warnings": warnings,
        "critical_findings": critical_findings,
        "metadata_findings": metadata_findings,
        "metadata_policy": release["metadata_policy"],
    }


def _ensure_remote(project: Path, remote: str, repo: str) -> None:
    url = f"https://github.com/{repo}.git" if not repo.startswith(("git@", "https://")) else repo
    remotes = subprocess.run(["git", "-C", str(project), "remote"], capture_output=True, text=True, check=False).stdout.split()
    if remote in remotes:
        subprocess.run(["git", "-C", str(project), "remote", "set-url", remote, url], capture_output=True, text=True, check=False)
    else:
        subprocess.run(["git", "-C", str(project), "remote", "add", remote, url], capture_output=True, text=True, check=False)


def _github_no_proxy_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ["HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"]:
        env.pop(key, None)
    existing = env.get("GODEBUG", "")
    if "http2client=0" not in existing:
        env["GODEBUG"] = "http2client=0" if not existing else f"{existing},http2client=0"
    return env


def _run_github_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    completed.nexus_retry_without_proxy_http2 = False  # type: ignore[attr-defined]
    if completed.returncode == 0:
        return completed
    retried = subprocess.run(argv, capture_output=True, text=True, check=False, env=_github_no_proxy_env())
    retried.nexus_retry_without_proxy_http2 = True  # type: ignore[attr-defined]
    if retried.returncode == 0:
        return retried
    if _github_command_error_score(retried) >= _github_command_error_score(completed):
        return retried
    return completed


def _github_command_error_score(completed: subprocess.CompletedProcess[str]) -> int:
    text = f"{completed.stdout}\n{completed.stderr}".lower()
    score = 0
    if "eof" in text or "http2" in text or "proxy" in text:
        score += 3
    if "not logged in" in text or "authentication" in text or "keyring" in text:
        score += 2
    return score


def _is_non_fast_forward_push_rejection(stderr: str) -> bool:
    lowered = stderr.lower()
    return (
        "fetch first" in lowered
        or "non-fast-forward" in lowered
        or "updates were rejected because the remote contains work" in lowered
    )


def _private_fallback_branch(project: Path) -> str:
    rev = subprocess.run(["git", "-C", str(project), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False)
    short = rev.stdout.strip() or "unknown"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", project.name).strip("-") or "project"
    return f"nexus-private-sync/{timestamp}-{name}-{short}"


def _gh_repo_view(repo: str) -> dict[str, object]:
    completed = _run_github_command(["gh", "repo", "view", repo, "--json", "nameWithOwner,visibility,url"])
    return {
        "schema": "nexus.gh_repo_view.v1",
        "status": "completed" if completed.returncode == 0 else "blocked",
        "reason": "repo_exists" if completed.returncode == 0 else "repo_view_failed",
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
        "retry_without_proxy_http2": getattr(completed, "nexus_retry_without_proxy_http2", False),
    }


def _gh_repo_create(repo: str, *, visibility: str) -> dict[str, object]:
    flag = "--public" if visibility == "public" else "--private"
    completed = _run_github_command(["gh", "repo", "create", repo, flag, "--confirm"])
    return {
        "schema": "nexus.gh_repo_create.v1",
        "status": "completed" if completed.returncode == 0 else "blocked",
        "reason": "repo_created" if completed.returncode == 0 else "repo_create_failed",
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
        "retry_without_proxy_http2": getattr(completed, "nexus_retry_without_proxy_http2", False),
    }


def _ensure_single_github_repository(repo: str, *, visibility: str, create: bool) -> dict[str, object]:
    viewed = _gh_repo_view(repo)
    if viewed["status"] == "completed":
        return {"schema": "nexus.github_single_repo_setup.v1", "status": "completed", "reason": "repo_exists", "repo": repo, "visibility": visibility, "view": viewed}
    if not create:
        return {"schema": "nexus.github_single_repo_setup.v1", "status": "blocked", "reason": "github_repo_missing", "repo": repo, "visibility": visibility, "view": viewed}
    created = _gh_repo_create(repo, visibility=visibility)
    if _repo_already_exists(created):
        return {"schema": "nexus.github_single_repo_setup.v1", "status": "completed", "reason": "repo_exists_after_create_conflict", "repo": repo, "visibility": visibility, "view": viewed, "create": created}
    if created["status"] != "completed":
        return {"schema": "nexus.github_single_repo_setup.v1", "status": "blocked", "reason": "github_repo_create_failed", "repo": repo, "visibility": visibility, "view": viewed, "create": created}
    return {"schema": "nexus.github_single_repo_setup.v1", "status": "completed", "reason": "repo_created", "repo": repo, "visibility": visibility, "view": viewed, "create": created}


def _gh_setup_git() -> dict[str, object]:
    completed = _run_github_command(["gh", "auth", "setup-git", "--hostname", "github.com"])
    return {
        "schema": "nexus.gh_git_auth_setup.v1",
        "status": "completed" if completed.returncode == 0 else "blocked",
        "reason": "git_credential_helper_ready" if completed.returncode == 0 else "gh_auth_setup_git_failed",
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
        "retry_without_proxy_http2": getattr(completed, "nexus_retry_without_proxy_http2", False),
    }


def _repo_already_exists(result: dict[str, object]) -> bool:
    text = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}".lower()
    return "name already exists" in text or "already exists on this account" in text


def _iter_project_files(project: Path) -> Iterable[Path]:
    for path in project.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(project)
        parts = set(rel.parts)
        if ".git" in parts or "__pycache__" in parts or ".pytest_cache" in parts:
            continue
        yield path


def _matching_pattern(rel: str, deny: list[str]) -> str:
    parts = rel.split("/")
    for pattern in deny:
        normalized = pattern.rstrip("/")
        if fnmatch.fnmatch(rel, normalized) or any(fnmatch.fnmatch(part, normalized) for part in parts):
            return pattern
        if pattern.endswith("/") and rel.startswith(pattern):
            return pattern
    return ""


def _expand(project: Path, pattern: str) -> list[Path]:
    path = project / pattern
    if path.exists():
        return [path]
    return [item for item in project.glob(pattern) if item.exists()]


def _denied(rel: str, deny: list[str], *, allow_public_workflow_assets: bool = False) -> bool:
    if allow_public_workflow_assets and _public_workflow_asset_allowed(rel):
        return False
    parts = rel.split("/")
    for pattern in deny:
        normalized = pattern.rstrip("/")
        if fnmatch.fnmatch(rel, normalized) or any(fnmatch.fnmatch(part, normalized) for part in parts):
            return True
        if pattern.endswith("/") and rel.startswith(pattern):
            return True
    return False


def _public_workflow_asset_allowed(rel: str) -> bool:
    rel = rel.strip("/")
    return any(_path_matches_public_pattern(rel, pattern) for pattern in PUBLIC_WORKFLOW_ASSET_PATHS)


def _skip_private_denied_path(pattern: str) -> bool:
    return pattern in PRIVATE_SKIP_DENYLIST


def _scan_text_for_secrets(text: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for name, pattern in [
        ("ssh_private_key", PRIVATE_KEY_RE),
        ("openai_key", OPENAI_KEY_RE),
        ("github_token", GITHUB_TOKEN_RE),
        ("bearer_token", BEARER_RE),
    ]:
        count = len(list(pattern.finditer(text)))
        if count:
            findings.append({"pattern": name, "count": count})
    assignment_count = 0
    for match in SECRET_ASSIGNMENT_RE.finditer(text):
        if _looks_like_secret_value(match.group(2)):
            assignment_count += 1
    if assignment_count:
        findings.append({"pattern": "api_key_assignment", "count": assignment_count})
    return findings


def _scan_text_for_public_metadata(text: str, rel: str, config: dict[str, object]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for name, pattern in [
        ("local_absolute_path", LOCAL_ABSOLUTE_PATH_RE),
        ("feishu_url", FEISHU_URL_RE),
        ("nexus_run_id", NEXUS_RUN_ID_RE),
        ("nexus_artifact_path", NEXUS_ARTIFACT_PATH_RE),
    ]:
        matches = [match.group(0).strip() for match in pattern.finditer(text)]
        if name == "local_absolute_path":
            matches = [match for match in matches if not _looks_like_regex_path_pattern(match)]
        matches = [match for match in matches if match not in PUBLIC_PLACEHOLDERS and not _public_metadata_allowed(rel, name, match, config)]
        if matches:
            findings.append({"file": rel, "severity": "metadata", "pattern": name, "count": len(matches), "examples": sorted(set(matches))[:3]})
    private_repo = str(config.get("private_repo") or "")
    public_repo = str(config.get("public_repo") or "")
    private_count = _private_repo_ref_count(text, private_repo) if private_repo and private_repo != public_repo else 0
    if private_count and not _public_metadata_allowed(rel, "private_repo", private_repo, config):
        findings.append({"file": rel, "severity": "metadata", "pattern": "private_repo", "count": private_count, "examples": [private_repo]})
    return findings


def _public_metadata_allowed(rel: str, pattern: str, value: str, config: dict[str, object]) -> bool:
    release = _public_release_config(config)
    target = f"{rel}:{pattern}:{value}"
    return any(fnmatch.fnmatch(target, allow) or fnmatch.fnmatch(value, allow) or fnmatch.fnmatch(rel, allow) for allow in release["metadata_allow_patterns"])


def _looks_like_secret_value(value: str) -> bool:
    cleaned = value.strip().strip("'\"")
    lowered = cleaned.lower()
    if lowered in {
        "secret-value",
        "test-secret",
        "test-token",
        "dummy-token",
        "dummy-secret",
        "example-token",
        "example-secret",
        "placeholder",
        "changeme",
        "redacted",
    }:
        return False
    if lowered.startswith(("your_", "your-", "example_", "example-", "dummy_", "dummy-", "test_", "test-", "<")):
        return False
    if "abcdefghijklmnopqrstuvwxyz" in lowered:
        return False
    if "(" in cleaned or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", cleaned):
        return False
    if len(cleaned) < 20:
        return False
    if OPENAI_KEY_RE.fullmatch(cleaned) or GITHUB_TOKEN_RE.fullmatch(cleaned):
        return True
    distinct = len(set(cleaned))
    has_mixed_classes = sum(
        [
            any(char.islower() for char in cleaned),
            any(char.isupper() for char in cleaned),
            any(char.isdigit() for char in cleaned),
            any(char in "_-./+=" for char in cleaned),
        ]
    )
    return distinct >= 10 and has_mixed_classes >= 2
