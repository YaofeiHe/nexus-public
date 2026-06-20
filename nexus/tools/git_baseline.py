from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".envrc",
    "id_rsa",
    "id_ed25519",
    "cookies.txt",
    "token",
    "secret",
}

DEFAULT_GITIGNORE_LINES = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*token*",
    "*cookie*",
    "__pycache__/",
    ".DS_Store",
    ".pytest_cache/",
    ".venv/",
    "node_modules/",
]


@dataclass(slots=True)
class GitBaselinePlan:
    project_path: Path
    is_git_repo: bool
    has_commits: bool
    file_count: int
    sensitive_hits: list[str]
    suggested_gitignore: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "nexus.git_baseline_plan.v1",
            "project_path": str(self.project_path),
            "is_git_repo": self.is_git_repo,
            "has_commits": self.has_commits,
            "file_count": self.file_count,
            "sensitive_hits": self.sensitive_hits,
            "suggested_gitignore": self.suggested_gitignore,
        }


def inspect_git_baseline(project: Path) -> GitBaselinePlan:
    project = project.expanduser().resolve()
    is_repo = git_root(project) is not None
    has_commits = False
    if is_repo:
        completed = subprocess.run(["git", "-C", str(project), "rev-parse", "--verify", "HEAD"], capture_output=True, text=True, check=False)
        has_commits = completed.returncode == 0
    files = [path for path in project.rglob("*") if path.is_file() and ".git" not in path.parts]
    sensitive_hits = []
    for path in files:
        lowered = path.name.lower()
        if lowered in SENSITIVE_NAMES or any(fragment in lowered for fragment in ("secret", "token", "cookie")):
            sensitive_hits.append(str(path.relative_to(project)))
    return GitBaselinePlan(
        project_path=project,
        is_git_repo=is_repo,
        has_commits=has_commits,
        file_count=len(files),
        sensitive_hits=sorted(sensitive_hits)[:50],
        suggested_gitignore=list(DEFAULT_GITIGNORE_LINES),
    )


def create_git_baseline(project: Path, *, message: str = "nexus baseline") -> dict[str, object]:
    project = project.expanduser().resolve()
    if not project.exists() or not project.is_dir():
        raise ValueError(f"project path is not a directory: {project}")
    before = inspect_git_baseline(project)
    if before.sensitive_hits:
        return {
            "schema": "nexus.git_baseline_result.v1",
            "status": "blocked",
            "reason": "sensitive_files_detected",
            "sensitive_hits": before.sensitive_hits,
            "project_path": str(project),
        }
    if not before.is_git_repo:
        _run_git(project, ["init"], check=True)
    gitignore = project / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    additions = [line for line in DEFAULT_GITIGNORE_LINES if line not in existing.splitlines()]
    if additions:
        gitignore.write_text((existing.rstrip() + "\n" if existing.strip() else "") + "\n".join(additions) + "\n", encoding="utf-8")
    _run_git(project, ["add", "-A"], check=True)
    status = _run_git(project, ["status", "--porcelain=v1"], check=False).stdout.strip()
    if not status:
        return {
            "schema": "nexus.git_baseline_result.v1",
            "status": "completed",
            "reason": "already_clean",
            "project_path": str(project),
            "commit": current_commit(project),
        }
    commit = _run_git(project, ["-c", "user.name=Nexus", "-c", "user.email=nexus@example.invalid", "commit", "-m", message], check=False)
    if commit.returncode != 0:
        return {
            "schema": "nexus.git_baseline_result.v1",
            "status": "failed",
            "reason": "git_commit_failed",
            "project_path": str(project),
            "stdout": commit.stdout[-2000:],
            "stderr": commit.stderr[-2000:],
        }
    return {
        "schema": "nexus.git_baseline_result.v1",
        "status": "completed",
        "reason": "baseline_commit_created",
        "project_path": str(project),
        "commit": current_commit(project),
    }


def git_root(path: Path) -> Path | None:
    completed = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip()).resolve()


def current_commit(project: Path) -> str:
    completed = _run_git(project, ["rev-parse", "--short", "HEAD"], check=False)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _run_git(project: Path, args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=check)
