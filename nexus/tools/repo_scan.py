from __future__ import annotations

from pathlib import Path


SECRET_MARKERS = {".env", ".ssh", "id_rsa", "id_ed25519", "cookie", "token", "credentials", "keychain"}
PACKAGE_FILES = {
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "requirements.txt",
    "README.md",
}


def scan_repo(project_path: Path, *, max_files: int = 300) -> dict[str, object]:
    root = project_path.expanduser().resolve()
    files: list[str] = []
    package_files: list[str] = []
    top_dirs: set[str] = set()
    skipped_secret_like: list[str] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        rel_text = str(rel)
        if _is_secret_like(rel_text):
            skipped_secret_like.append(rel_text)
            if path.is_dir():
                continue
        if path.is_dir():
            if len(rel.parts) == 1:
                top_dirs.add(rel.parts[0])
            continue
        if len(files) < max_files:
            files.append(rel_text)
        if path.name in PACKAGE_FILES:
            package_files.append(rel_text)
    return {
        "schema": "nexus.repo_scan.v1",
        "project_path": str(root),
        "file_sample_count": len(files),
        "file_samples": files,
        "top_dirs": sorted(top_dirs),
        "package_files": sorted(package_files),
        "skipped_secret_like": sorted(skipped_secret_like)[:50],
    }


def _is_secret_like(rel_text: str) -> bool:
    lowered = rel_text.lower()
    return any(marker in lowered for marker in SECRET_MARKERS)
