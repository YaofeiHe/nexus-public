#!/usr/bin/env python3
"""Structured evaluator for Nexus lab E2E case executions.

This file/content based evaluator is the local approval-agent input for the
Nexus lab. It reads an execution.json file, or a case directory
containing one, then inspects the target project and Nexus run artifacts.

It is intentionally not a full intelligent review. A passing result means the
required evidence surfaces were found and matched basic content heuristics; the
skill replay and modification loop still need to validate deeper intent quality
when a full lab run is started.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROBLEM_MATRIX_FILE = Path(__file__).with_name("nexus_problem_matrix.json")

DIMENSION_ORDER = [
    "intent_capture",
    "intent_understanding",
    "reference_reading_record",
    "search_plan_log",
    "domain_workflows",
    "validation_script",
    "sync_artifacts",
    "recovery_rebind",
    "playbook_persistence",
    "monitor_next_prompt",
    "anti_hardcoding",
]

SURFACE_BY_DIMENSION = {
    "intent_capture": "init_project",
    "intent_understanding": "init_project",
    "reference_reading_record": "research",
    "search_plan_log": "search",
    "domain_workflows": "project_workspace",
    "validation_script": "test",
    "sync_artifacts": "sync",
    "recovery_rebind": "recovery",
    "playbook_persistence": "recovery",
    "monitor_next_prompt": "monitor",
    "anti_hardcoding": "generalization",
}

REQUIRED_CHANGE_BY_DIMENSION = {
    "intent_capture": "Preserve the raw prompt and source path in init_project/write_project_docs artifacts.",
    "intent_understanding": "Expand init_project/write_project_docs so normalized requirements map intent to concrete project capabilities.",
    "reference_reading_record": "Make source reading/search records explicit and link them back to project requirements.",
    "search_plan_log": "Emit a readable search plan and search log from the research/search path.",
    "domain_workflows": "Generate domain-specific workflows from normalized intent instead of only governance wrappers.",
    "validation_script": "Create or expose a project validation entry that checks generated project surfaces.",
    "sync_artifacts": "Route the tested workflow through the existing private/Feishu/public sync artifact path.",
    "recovery_rebind": "Use the existing debug handoff/worklog/rebind flow and keep the original run_id.",
    "playbook_persistence": "Approve successful recovery into the project recovery playbook and look it up on later failures.",
    "monitor_next_prompt": "Write concrete continuation/approval/recovery prompts into interaction or monitor artifacts.",
    "anti_hardcoding": "Move fixes into general Nexus mechanisms and remove sample-name or sample-domain branching.",
}

PROJECT_PATH_KEYS = {
    "project_path",
    "project_root",
    "target_project",
    "target_project_path",
    "target_project_root",
    "workspace_project_path",
    "output_project_path",
    "resolved_project_path",
    "final_project_path",
    "target_path",
    "created_path",
}
RUN_ID_KEYS = {"run_id", "nexus_run_id", "latest_run_id"}
RUN_DIR_KEYS = {"run_dir", "run_path", "nexus_run_dir", "run_artifact_dir"}
CASE_ID_KEYS = {"case_id", "test_case_id", "id", "case"}
RAW_INTENT_KEYS = {
    "instruction",
    "prompt",
    "raw_prompt",
    "raw_intent",
    "raw_user_request",
    "user_instruction",
    "initial_instruction",
}
EXPECTED_KEYS = {"expected", "expected_behavior", "expected_files", "pass_requirements"}

TEXT_SUFFIXES = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".py"}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
PLACEHOLDER_RE = re.compile(
    r"\b(TODO|TBD|FIXME|placeholder|lorem ipsum)\b|待补充|占位|示例待填写|稍后补充",
    re.IGNORECASE,
)

STOP_TERMS = {
    "$nexus-workflow",
    "nexus",
    "workflow",
    "project",
    "projects",
    "parent",
    "case",
    "run",
    "root",
    "path",
    "docs",
    "scripts",
    "json",
    "python",
    "local",
    "init",
    "初始化",
    "项目",
    "工作区",
    "要求",
    "记录",
    "生成",
    "说明",
    "根据",
    "父目录",
    "必须",
    "最后",
    "本地",
    "检查",
    "关键文件",
    "是否",
    "齐全",
    "保存",
    "使用",
    "当前",
}

SAMPLE_DOMAIN_TERMS = {
    "feeler": ["feeler"],
    "probe": ["probe"],
    "resume_interview": ["resume", "interview", "简历", "面试"],
    "grant_workspace": ["资助方", "项目预算", "受益人证据", "申请材料", "提交截止日期"],
    "incident_workspace": ["现场事故", "事故复盘", "root-cause", "根因", "owner assignment"],
    "museum_workspace": ["博物馆", "策展", "藏品", "展陈", "观众反馈"],
    "open_source_maintainer": ["开源维护者", "issue 分级", "release blocker", "维护者交接"],
    "language_learning": ["语言学习", "错题", "模拟测评", "课程迭代"],
    "robotics_competition": ["机器人比赛", "零件采购", "机械/电控/软件", "测试场次"],
}


@dataclass
class Evidence:
    id: str
    path: str
    note: str
    exists: bool
    line_start: int | None = None
    line_end: int | None = None
    excerpt: str | None = None


@dataclass
class DimensionResult:
    name: str
    surface: str
    status: str
    expected: str
    observed: str
    evidence_refs: list[str]
    required_nexus_change: str | None = None


@dataclass
class EvalContext:
    input_path: Path
    case_dir: Path
    execution_path: Path | None
    execution: dict[str, Any]
    case_id: str
    run_id: str | None
    nexus_root: Path
    project_path: Path | None
    project_path_source: str
    run_dir: Path | None
    raw_intent: str
    expected_text: str
    problem_matrix: dict[str, Any]


class EvidenceBag:
    def __init__(self, base_paths: list[Path]) -> None:
        self._base_paths = [path.resolve() for path in base_paths if path]
        self._items: list[Evidence] = []

    @property
    def items(self) -> list[Evidence]:
        return self._items

    def add(
        self,
        path: Path,
        note: str,
        *,
        exists: bool | None = None,
        pattern: str | None = None,
        excerpt: str | None = None,
    ) -> str:
        exists_value = path.exists() if exists is None else exists
        line_start = None
        line_end = None
        if exists_value and excerpt is None:
            line_start, line_end, excerpt = find_excerpt(path, pattern=pattern)
        evidence_id = f"ev{len(self._items) + 1}"
        self._items.append(
            Evidence(
                id=evidence_id,
                path=self._rel(path),
                note=note,
                exists=exists_value,
                line_start=line_start,
                line_end=line_end,
                excerpt=excerpt,
            )
        )
        return evidence_id

    def _rel(self, path: Path) -> str:
        resolved = path.resolve() if path.exists() else path.absolute()
        for base in self._base_paths:
            try:
                return str(resolved.relative_to(base))
            except ValueError:
                continue
        return str(path)


def read_text(path: Path, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if limit is not None and len(text) > limit:
        return text[:limit]
    return text


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_problem_matrix(path: Path) -> dict[str, Any]:
    payload = load_json(path.expanduser())
    if not isinstance(payload, dict):
        return {"schema": "nexus.lab.problem_matrix.missing", "source": str(path), "problems": []}
    problems = payload.get("problems")
    if not isinstance(problems, list):
        payload["problems"] = []
    return payload


def find_excerpt(path: Path, *, pattern: str | None = None, max_chars: int = 500) -> tuple[int | None, int | None, str | None]:
    text = read_text(path, limit=250_000)
    if not text:
        return None, None, None
    lines = text.splitlines()
    lowered_pattern = pattern.lower() if pattern else None
    chosen_index = None
    if lowered_pattern:
        for index, line in enumerate(lines):
            if lowered_pattern in line.lower():
                chosen_index = index
                break
    if chosen_index is None:
        for index, line in enumerate(lines):
            if line.strip():
                chosen_index = index
                break
    if chosen_index is None:
        return None, None, None
    start = max(0, chosen_index - 1)
    end = min(len(lines), chosen_index + 2)
    excerpt = "\n".join(lines[start:end]).strip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[: max_chars - 3].rstrip() + "..."
    return start + 1, end, excerpt


def iter_files(root: Path, *, max_files: int = 800) -> list[Path]:
    if not root or not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if len(files) >= max_files:
            break
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and (path.suffix.lower() in TEXT_SUFFIXES or path.name in {"README", "Makefile"}):
            files.append(path)
    return files


def find_files(root: Path | None, patterns: list[str]) -> list[Path]:
    if root is None or not root.exists():
        return []
    found: list[Path] = []
    for pattern in patterns:
        found.extend(path for path in root.glob(pattern) if path.is_file())
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in found:
        if path not in seen:
            unique.append(path)
            seen.add(path)
    return unique


def deep_find_values(data: Any, keys: set[str]) -> list[Any]:
    values: list[Any] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys:
                values.append(value)
            values.extend(deep_find_values(value, keys))
    elif isinstance(data, list):
        for item in data:
            values.extend(deep_find_values(item, keys))
    return values


def deep_json_text(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(data)


def first_string(data: Any, keys: set[str]) -> str | None:
    for value in deep_find_values(data, keys):
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None


def combined_strings(data: Any, keys: set[str]) -> str:
    parts: list[str] = []
    for value in deep_find_values(data, keys):
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, (list, dict)):
            parts.append(deep_json_text(value))
    return "\n".join(parts)


def resolve_path(value: Any, *, base: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith("codex://"):
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    candidate = base / path
    if candidate.exists():
        return candidate.resolve()
    return (Path.cwd() / path).resolve()


def discover_execution(input_path: Path) -> tuple[Path | None, dict[str, Any]]:
    if input_path.is_file():
        data = load_json(input_path)
        return input_path, data if isinstance(data, dict) else {}
    candidates = [input_path / "execution.json"]
    if input_path.exists():
        candidates.extend(sorted(input_path.rglob("execution.json")))
    for candidate in candidates:
        if candidate.is_file():
            data = load_json(candidate)
            if isinstance(data, dict):
                return candidate, data
    return None, {}


def discover_context(args: argparse.Namespace) -> EvalContext:
    input_path = Path(args.case).expanduser().resolve()
    execution_path, execution = discover_execution(input_path)
    case_dir = input_path.parent if input_path.is_file() else input_path
    if execution_path:
        case_dir = execution_path.parent

    nexus_root = Path(args.nexus_root).expanduser().resolve() if args.nexus_root else None
    if nexus_root is None:
        root_text = first_string(execution, {"nexus_root", "repo_root"})
        nexus_root = resolve_path(root_text, base=case_dir) if root_text else Path.cwd().resolve()

    run_id = args.run_id or first_string(execution, RUN_ID_KEYS)
    run_dir = resolve_path(args.run_dir, base=case_dir) if args.run_dir else None
    if run_dir is None:
        run_dir_text = first_string(execution, RUN_DIR_KEYS)
        run_dir = resolve_path(run_dir_text, base=case_dir) if run_dir_text else None
    if run_dir is None and run_id:
        run_dir = nexus_root / ".data" / "runs" / run_id
    if run_id is None and run_dir is not None:
        run_id = run_dir.name

    project_path = resolve_path(args.project_path, base=case_dir) if args.project_path else None
    project_path_source = "cli.project_path" if project_path is not None else ""
    if project_path is None:
        project_path, project_path_source = resolve_evaluation_project_path(execution, run_dir=run_dir, nexus_root=nexus_root, case_dir=case_dir)

    case_id = args.case_id or first_string(execution, CASE_ID_KEYS)
    if not case_id:
        case_id = case_dir.name or "unknown_case"

    raw_intent = args.raw_intent or combined_strings(execution, RAW_INTENT_KEYS)
    expected_text = combined_strings(execution, EXPECTED_KEYS)
    matrix_path = Path(args.problem_matrix).expanduser().resolve()
    problem_matrix = load_problem_matrix(matrix_path)

    return EvalContext(
        input_path=input_path,
        case_dir=case_dir,
        execution_path=execution_path,
        execution=execution,
        case_id=case_id,
        run_id=run_id,
        nexus_root=nexus_root,
        project_path=project_path,
        project_path_source=project_path_source,
        run_dir=run_dir,
        raw_intent=raw_intent,
        expected_text=expected_text,
        problem_matrix=problem_matrix,
    )


def resolve_evaluation_project_path(
    execution: dict[str, Any],
    *,
    run_dir: Path | None,
    nexus_root: Path,
    case_dir: Path,
) -> tuple[Path | None, str]:
    candidates: list[tuple[str, Any]] = []
    candidates.extend(
        [
            ("execution.resolved_project_path", execution.get("resolved_project_path")),
            ("execution.final_project_path", execution.get("final_project_path")),
        ]
    )
    context_updates = execution.get("context_updates") if isinstance(execution.get("context_updates"), dict) else {}
    candidates.extend(
        [
            ("execution.context_updates.RESOLVED_PROJECT_PATH", context_updates.get("RESOLVED_PROJECT_PATH")),
            ("execution.context_updates.PROJECT_PATH", context_updates.get("PROJECT_PATH")),
        ]
    )
    if run_dir is not None:
        for rel, keys in [
            ("approvals/APPROVED_project-root.json", {"created_path"}),
            ("approvals/project_root_required.json", {"target_path", "project_path"}),
            ("state.json", {"target_path", "final_project_path", "project_path"}),
            ("interaction.json", {"final_project_path", "resolved_project_path", "project_path"}),
            ("tool_results/recovery_result.json", {"final_project_path", "project_path"}),
            ("input.json", {"project_path", "target_project_path"}),
        ]:
            payload = load_json(run_dir / rel)
            for key, value in deep_path_items(payload):
                if key in keys:
                    candidates.append((f"run.{rel}:{key}", value))
        for ref in artifact_refs_from_payload(load_json(run_dir / "interaction.json")):
            candidates.append(("run.interaction.artifact_ref", ref))
    for value in deep_find_values(execution, PROJECT_PATH_KEYS):
        candidates.append(("execution.deep_project_path", value))

    seen: set[str] = set()
    first_named: tuple[Path, str] | None = None
    for source, value in candidates:
        for candidate in project_path_candidates(value, base=case_dir):
            resolved = candidate.expanduser().resolve()
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            if looks_like_target_project(resolved, nexus_root):
                return resolved, source
            if first_named is None and candidate.name:
                first_named = (resolved, source)
    return first_named if first_named is not None else (None, "unresolved")


def deep_path_items(value: Any) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str):
                items.append((key, nested))
            items.extend(deep_path_items(nested))
    elif isinstance(value, list):
        for nested in value:
            items.extend(deep_path_items(nested))
    return items


def artifact_refs_from_payload(payload: Any) -> list[str]:
    refs = payload.get("artifact_refs") if isinstance(payload, dict) else []
    if not isinstance(refs, list):
        return []
    return [str(item) for item in refs if str(item)]


def project_path_candidates(value: Any, *, base: Path) -> list[Path]:
    path = resolve_path(value, base=base)
    if path is None:
        return []
    candidates = [path]
    parts = path.parts
    if ".nexus" in parts:
        candidates.append(Path(*parts[: parts.index(".nexus")]))
    if "docs" in parts:
        docs_index = parts.index("docs")
        if docs_index > 0:
            candidates.append(Path(*parts[:docs_index]))
    return candidates


def looks_like_target_project(path: Path, nexus_root: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    if path == nexus_root.expanduser().resolve():
        return False
    markers = [
        path / ".nexus" / "project-intent.json",
        path / ".nexus" / "board.md",
        path / ".nexus" / "recovery-playbook.json",
        path / "docs" / "intent" / "original-requirement.md",
        path / "docs" / "intent" / "normalized-requirement.md",
        path / "docs" / "project-overview.md",
        path / "docs" / "recovery-records.md",
    ]
    return any(marker.exists() for marker in markers)


def extract_terms(text: str, *, limit: int = 32) -> list[str]:
    cleaned = re.sub(r"\{[^}]+\}", " ", text)
    cleaned = cleaned.replace("$nexus-workflow", " ")
    pieces = re.split(r"[\s,，、。；;：:()（）\[\]【】{}<>\"'`]+", cleaned)
    terms: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        term = piece.strip("._/-")
        if not term or term in STOP_TERMS:
            continue
        lowered = term.lower()
        if lowered in STOP_TERMS or lowered.isdigit():
            continue
        if re.fullmatch(r"[A-Za-z0-9_.-]+", term):
            if len(term) < 4 or lowered in STOP_TERMS:
                continue
            normalized = lowered
        else:
            if len(term) < 2:
                continue
            normalized = term[:24]
        if normalized not in seen:
            terms.append(normalized)
            seen.add(normalized)
        if len(terms) >= limit:
            break
    return terms


def count_term_hits(text: str, terms: list[str]) -> tuple[int, list[str]]:
    lowered = text.lower()
    hits = [term for term in terms if term.lower() in lowered]
    return len(hits), hits


def is_non_placeholder(text: str, *, min_chars: int = 80) -> bool:
    stripped = text.strip()
    if len(stripped) < min_chars:
        return False
    return not PLACEHOLDER_RE.search(stripped)


def expected_by_keyword(ctx: EvalContext, keywords: list[str]) -> bool:
    haystack = f"{ctx.case_id}\n{ctx.raw_intent}\n{ctx.expected_text}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def wants_explicit_public_sync(ctx: EvalContext) -> bool:
    haystack = f"{ctx.case_id}\n{ctx.raw_intent}\n{ctx.expected_text}".lower()
    strong_positive = [
        "github-sync public",
        "public --confirm",
        "sync public",
        "public sync",
        "public staging",
        "public validation",
        "fresh clone",
        "公开同步",
        "公开发布",
        "发布 public",
        "同步 public",
        "同步公开",
        "公开仓库同步",
    ]
    if not any(marker in haystack for marker in strong_positive):
        return False
    negative = [
        "--no-github-sync",
        "no-github-sync",
        "不要同步 github",
        "不要同步github",
        "不做 github 同步",
        "不要公开同步",
        "不公开发布",
        "不要公开发布",
        "no public sync",
        "do not sync public",
    ]
    if any(marker in haystack for marker in negative):
        return False
    return True


def reference_paths_from_intent(text: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"((?:\{[^}]+\}|/|\.?/)?[\w.\-/]+?\.(?:md|txt|json|jsonl|yaml|yml|csv|py))", text):
        value = match.group(1)
        if value.startswith("docs/intent") or value.startswith(".data/"):
            continue
        paths.append(value)
    return sorted(set(paths))


def project_text(ctx: EvalContext, rel_paths: list[str] | None = None) -> str:
    if ctx.project_path is None or not ctx.project_path.exists():
        return ""
    if rel_paths is None:
        files = iter_files(ctx.project_path)
    else:
        files = [ctx.project_path / rel for rel in rel_paths if (ctx.project_path / rel).is_file()]
    return "\n".join(read_text(path, limit=100_000) for path in files)


def run_text(ctx: EvalContext, patterns: list[str] | None = None) -> str:
    if ctx.run_dir is None or not ctx.run_dir.exists():
        return ""
    if patterns is None:
        files = iter_files(ctx.run_dir)
    else:
        files: list[Path] = []
        for pattern in patterns:
            files.extend(ctx.run_dir.glob(pattern))
        files = [path for path in files if path.is_file()]
    return "\n".join(read_text(path, limit=100_000) for path in files)


def blocked_interaction_reason(ctx: EvalContext) -> str | None:
    if terminal_without_pending_actions(ctx):
        return None
    interaction = load_json(ctx.run_dir / "interaction.json") if ctx.run_dir else None
    state = load_json(ctx.run_dir / "state.json") if ctx.run_dir else None
    pending = interaction.get("pending_actions") if isinstance(interaction, dict) and isinstance(interaction.get("pending_actions"), list) else []
    if pending:
        return "interaction/state reports active pending actions"
    blocked_reason = ""
    if isinstance(interaction, dict):
        blocked_reason = str(interaction.get("blocked_reason") or "")
    if not blocked_reason and isinstance(state, dict):
        blocked_reason = str(state.get("blocked_reason") or "")
    status_text = " ".join(
        [
            str(interaction.get("previous_task_status") or interaction.get("lifecycle_status") or "") if isinstance(interaction, dict) else "",
            str(state.get("status") or "") if isinstance(state, dict) else "",
            blocked_reason,
        ]
    ).lower()
    if "blocked" in status_text or blocked_reason:
        return "interaction/state reports pending or blocked setup/auth work"
    return None


def terminal_without_pending_actions(ctx: EvalContext) -> bool:
    if ctx.run_dir is None or not ctx.run_dir.exists():
        return False
    interaction = load_json(ctx.run_dir / "interaction.json")
    state = load_json(ctx.run_dir / "state.json")
    pending = interaction.get("pending_actions") if isinstance(interaction, dict) and isinstance(interaction.get("pending_actions"), list) else []
    if pending:
        return False
    lifecycle = str(interaction.get("lifecycle_status") or "") if isinstance(interaction, dict) else ""
    previous = str(interaction.get("previous_task_status") or "") if isinstance(interaction, dict) else ""
    state_status = str(state.get("status") or "") if isinstance(state, dict) else ""
    terminal_statuses = {"completed", "done", "pass"}
    return lifecycle in terminal_statuses or previous in terminal_statuses or state_status in terminal_statuses


def no_external_sync_requested(ctx: EvalContext) -> bool:
    haystack = f"{ctx.raw_intent}\n{ctx.expected_text}".lower()
    markers = [
        "--no-github-sync",
        "--no-feishu-sync",
        "不要同步 github",
        "不要同步github",
        "不要同步飞书",
        "不同步 github",
        "不同步github",
        "不同步飞书",
        "no github sync",
        "no feishu sync",
    ]
    return any(marker in haystack for marker in markers)


def wants_explicit_recovery_rebind(ctx: EvalContext) -> bool:
    haystack = f"{ctx.case_id}\n{ctx.raw_intent}\n{ctx.expected_text}".lower()
    markers = [
        "handoff",
        "debug worklog",
        "rebind",
        "continue-after-input",
        "rebind-and-continue",
        "外部 codex",
        "外部codex",
        "回到原",
        "原 nexus 暂停点",
        "原nexus暂停点",
        "回跳",
        "恢复回绑",
    ]
    if not any(marker in haystack for marker in markers):
        return False
    local_safe_negatives = ["--no-github-sync", "--no-feishu-sync", "不要同步 github", "不要同步飞书", "不同步 github", "不同步飞书"]
    if ctx.case_id.startswith("real_0") and any(marker in haystack for marker in local_safe_negatives) and "debug" not in haystack and "handoff" not in haystack:
        return False
    return True


def add_existing_files(evidence: EvidenceBag, files: list[Path], note: str, *, pattern: str | None = None) -> list[str]:
    return [evidence.add(path, note, pattern=pattern) for path in files]


def missing_project_result(name: str) -> DimensionResult:
    return DimensionResult(
        name=name,
        surface=SURFACE_BY_DIMENSION[name],
        status="blocked",
        expected="Target project path is available for inspection.",
        observed="No usable target project path was found in execution.json or CLI overrides.",
        evidence_refs=[],
        required_nexus_change=None,
    )


def missing_run_result(name: str) -> DimensionResult:
    return DimensionResult(
        name=name,
        surface=SURFACE_BY_DIMENSION[name],
        status="blocked",
        expected="Nexus run artifact directory is available for inspection.",
        observed="No usable <NEXUS_ARTIFACT_PATH> directory was found in execution.json or CLI overrides.",
        evidence_refs=[],
        required_nexus_change=None,
    )


def evaluate_intent_capture(ctx: EvalContext, evidence: EvidenceBag) -> DimensionResult:
    name = "intent_capture"
    if ctx.project_path is None or not ctx.project_path.exists():
        return missing_project_result(name)
    original = ctx.project_path / "docs" / "intent" / "original-requirement.md"
    project_intent = ctx.project_path / ".nexus" / "project-intent.json"
    evidence_refs = [
        evidence.add(original, "raw intent document"),
        evidence.add(project_intent, "machine-readable project intent index"),
    ]
    original_text = read_text(original)
    terms = extract_terms(ctx.raw_intent)
    hits, matched = count_term_hits(original_text, terms)
    expected = "Raw user instruction is preserved in docs/intent/original-requirement.md and indexed by .nexus/project-intent.json."
    if not original.exists():
        status = "fail"
        observed = "docs/intent/original-requirement.md is missing."
    elif not is_non_placeholder(original_text):
        status = "fail"
        observed = "Raw intent document exists but is empty, too short, or placeholder-like."
    elif terms and hits < min(3, max(1, len(terms) // 4)):
        status = "fail"
        observed = f"Raw intent document has weak overlap with the case instruction; matched terms: {matched}."
    elif not project_intent.exists():
        status = "fail"
        observed = ".nexus/project-intent.json is missing, so the raw intent is not machine-indexed."
    else:
        status = "pass"
        observed = f"Raw intent file and project intent index exist; matched instruction terms include {matched[:8]}."
    return dimension(name, status, expected, observed, evidence_refs)


def evaluate_intent_understanding(ctx: EvalContext, evidence: EvidenceBag) -> DimensionResult:
    name = "intent_understanding"
    if ctx.project_path is None or not ctx.project_path.exists():
        return missing_project_result(name)
    normalized = ctx.project_path / "docs" / "intent" / "normalized-requirement.md"
    trace_files = find_files(
        ctx.project_path,
        [
            "docs/requirement-trace.md",
            "docs/intent/project-understanding.md",
            "docs/intent/*understanding*.md",
            "docs/intent/*trace*.md",
        ],
    )
    evidence_refs = [evidence.add(normalized, "normalized requirement document")]
    evidence_refs.extend(add_existing_files(evidence, trace_files[:4], "intent trace or understanding document"))
    normalized_text = read_text(normalized)
    terms = extract_terms(ctx.raw_intent)
    hits, matched = count_term_hits(normalized_text, terms)
    structure_markers = ["目标", "workflow", "流程", "验收", "验证", "边界", "未确认", "风险", "用户", "数据"]
    marker_hits = [marker for marker in structure_markers if marker.lower() in normalized_text.lower()]
    expected = "Normalized requirements explain goals, workflows, data/objects, validation, boundaries, and uncertain points."
    if not normalized.exists():
        status = "fail"
        observed = "docs/intent/normalized-requirement.md is missing."
    elif not is_non_placeholder(normalized_text, min_chars=180):
        status = "fail"
        observed = "Normalized requirement document is too short or placeholder-like."
    elif terms and hits < min(3, max(1, len(terms) // 5)):
        status = "fail"
        observed = f"Normalized requirement has weak domain overlap with the instruction; matched terms: {matched}."
    elif len(marker_hits) < 3:
        status = "fail"
        observed = f"Normalized requirement lacks enough understanding markers; found {marker_hits}."
    elif not trace_files:
        status = "fail"
        observed = "No requirement trace or project-understanding document was found."
    else:
        status = "pass"
        observed = f"Normalized requirement and understanding/trace surfaces exist; markers include {marker_hits[:6]}."
    return dimension(name, status, expected, observed, evidence_refs)


def evaluate_reference_reading_record(ctx: EvalContext, evidence: EvidenceBag) -> DimensionResult:
    name = "reference_reading_record"
    expected_refs = reference_paths_from_intent(ctx.raw_intent)
    applicable = bool(expected_refs) or expected_by_keyword(
        ctx,
        ["reference", "source", "fixture", "history", "读取", "检索", "参考", "历史", "材料来源", "source-material"],
    )
    project_files = find_files(
        ctx.project_path,
        [
            "docs/intent/source-material-index.md",
            "docs/reference-materials.md",
            "docs/*source*material*.md",
            "docs/*reference*.md",
            "docs/search-log.md",
        ],
    )
    run_files = find_files(
        ctx.run_dir,
        [
            "reports/source_reading_plan.md",
            "reports/source_reading_log.md",
            "tool_results/source_status.json",
            "search_rounds/*/source_status.json",
            "search_rounds/*/candidates.jsonl",
        ],
    )
    evidence_refs = add_existing_files(evidence, project_files[:6], "project source/reference reading record")
    evidence_refs.extend(add_existing_files(evidence, run_files[:8], "run source/search status artifact"))
    expected = "When references or source materials are requested, records list what was planned, read, skipped, and extracted."
    if not applicable:
        observed = "No explicit reference/source-reading requirement detected; dimension recorded as not applicable for this case."
        return dimension(name, "pass", expected, observed, evidence_refs)
    if not project_files and not run_files:
        if ctx.project_path is None and ctx.run_dir is None:
            return DimensionResult(
                name=name,
                surface=SURFACE_BY_DIMENSION[name],
                status="blocked",
                expected=expected,
                observed="No project path or run artifact directory is available to inspect reference-reading records.",
                evidence_refs=evidence_refs,
                required_nexus_change=None,
            )
        return dimension(name, "fail", expected, "No source/reference reading record artifact was found.", evidence_refs)
    combined = "\n".join(read_text(path, limit=120_000) for path in project_files + run_files)
    missing_refs = [ref for ref in expected_refs if Path(ref).name and Path(ref).name not in combined]
    extraction_markers = ["提取", "需求", "extracted", "requirement", "read_status", "skipped", "未读", "失败", "unavailable", "source"]
    markers = [marker for marker in extraction_markers if marker.lower() in combined.lower()]
    if missing_refs:
        observed = f"Reading records exist but do not mention required reference file(s): {missing_refs}."
        return dimension(name, "fail", expected, observed, evidence_refs)
    if len(markers) < 2:
        observed = f"Reading records exist but do not show extracted requirements or skipped/unavailable states; markers found: {markers}."
        return dimension(name, "fail", expected, observed, evidence_refs)
    observed = f"Reference/source records exist and mention requested files; reading markers include {markers[:6]}."
    return dimension(name, "pass", expected, observed, evidence_refs)


def evaluate_search_plan_log(ctx: EvalContext, evidence: EvidenceBag) -> DimensionResult:
    name = "search_plan_log"
    applicable = expected_by_keyword(
        ctx,
        ["search", "research", "检索", "搜索", "调研", "读取", "source", "reference", "history", "材料"],
    )
    plan_files = find_files(
        ctx.run_dir,
        ["search_rounds/*/search_plan.json", "reports/research_contract.json", "reports/research_contract.md"],
    ) + find_files(ctx.project_path, ["docs/search-plan.md", "docs/*search*plan*.md"])
    log_files = find_files(
        ctx.run_dir,
        [
            "search_rounds/*/source_status.json",
            "search_rounds/*/coverage_review.json",
            "search_rounds/*/stop_decision.json",
            "search_rounds/*/candidates.jsonl",
            "tool_results/source_status.json",
        ],
    ) + find_files(ctx.project_path, ["docs/search-log.md", "docs/*search*log*.md"])
    evidence_refs = add_existing_files(evidence, plan_files[:8], "search/research plan artifact")
    evidence_refs.extend(add_existing_files(evidence, log_files[:8], "search/research log artifact"))
    expected = "Cases that require reading, research, or search leave both a plan and an actual log/result record."
    if not applicable and not plan_files and not log_files:
        observed = "No search/research requirement or artifacts detected; dimension recorded as not applicable for this case."
        return dimension(name, "pass", expected, observed, evidence_refs)
    if not plan_files and not log_files and ctx.run_dir is None:
        return missing_run_result(name)
    if not plan_files:
        return dimension(name, "fail", expected, "Search/research log surfaces may exist, but no plan artifact was found.", evidence_refs)
    if not log_files:
        return dimension(name, "fail", expected, "Search/research plan surfaces exist, but no actual log/result artifact was found.", evidence_refs)
    return dimension(name, "pass", expected, f"Found {len(plan_files)} plan artifact(s) and {len(log_files)} log/result artifact(s).", evidence_refs)


def evaluate_domain_workflows(ctx: EvalContext, evidence: EvidenceBag) -> DimensionResult:
    name = "domain_workflows"
    if ctx.project_path is None or not ctx.project_path.exists():
        return missing_project_result(name)
    workflow_files = find_files(
        ctx.project_path,
        ["workflows/*.md", "workflows/*.json", "docs/workflows/*.md", "docs/*workflow*.md", "docs/*流程*.md"],
    )
    operation_guide = ctx.project_path / "docs" / "operation-guide.md"
    overview = ctx.project_path / "docs" / "project-overview.md"
    evidence_refs = add_existing_files(evidence, workflow_files[:8], "domain workflow artifact")
    evidence_refs.append(evidence.add(operation_guide, "operation guide"))
    evidence_refs.append(evidence.add(overview, "project overview"))
    combined = "\n".join(read_text(path, limit=120_000) for path in workflow_files + [operation_guide, overview])
    terms = extract_terms(ctx.raw_intent, limit=40)
    hits, matched = count_term_hits(combined, terms)
    workflow_markers = ["workflow", "流程", "步骤", "执行", "更新", "验证", "复盘", "提交", "发布"]
    marker_hits = [marker for marker in workflow_markers if marker.lower() in combined.lower()]
    expected = "Project contains domain-specific workflows or operation-guide sections derived from the case intent."
    if not workflow_files and not operation_guide.exists():
        return dimension(name, "fail", expected, "No workflow files or docs/operation-guide.md were found.", evidence_refs)
    if not is_non_placeholder(combined, min_chars=240):
        return dimension(name, "fail", expected, "Workflow/operation-guide content is too short or placeholder-like.", evidence_refs)
    if terms and hits < min(4, max(2, len(terms) // 6)):
        return dimension(name, "fail", expected, f"Workflow surfaces have weak overlap with domain terms; matched {matched}.", evidence_refs)
    if len(marker_hits) < 2:
        return dimension(name, "fail", expected, f"Workflow surfaces lack process markers; found {marker_hits}.", evidence_refs)
    observed = f"Workflow/operation-guide content exists with domain terms {matched[:8]} and markers {marker_hits[:6]}."
    return dimension(name, "pass", expected, observed, evidence_refs)


def evaluate_validation_script(ctx: EvalContext, evidence: EvidenceBag) -> DimensionResult:
    name = "validation_script"
    if ctx.project_path is None or not ctx.project_path.exists():
        return missing_project_result(name)
    validation_files = find_files(ctx.project_path, ["scripts/validate_project.py", "scripts/*validate*.py", "validate_project.py"])
    guide = ctx.project_path / "docs" / "operation-guide.md"
    evidence_refs = add_existing_files(evidence, validation_files[:4], "project validation script")
    evidence_refs.append(evidence.add(guide, "operation guide validation command reference", pattern="validate"))
    expected = "A local project validation entry exists and is referenced by the user-facing operation guide."
    if not validation_files:
        return dimension(name, "fail", expected, "No scripts/validate_project.py or equivalent validation script was found.", evidence_refs)
    text = "\n".join(read_text(path, limit=100_000) for path in validation_files)
    if not is_non_placeholder(text, min_chars=180):
        return dimension(name, "fail", expected, "Validation script exists but appears empty, tiny, or placeholder-like.", evidence_refs)
    script_markers = ["Path", "exists", "json", "sys.exit", "main", "missing", "validate", "检查", "缺失", "失败"]
    markers = [marker for marker in script_markers if marker.lower() in text.lower()]
    if len(markers) < 3:
        return dimension(name, "fail", expected, f"Validation script lacks obvious structural checks; markers found: {markers}.", evidence_refs)
    guide_text = read_text(guide)
    if guide.exists() and "validate" not in guide_text.lower() and "验证" not in guide_text:
        return dimension(name, "fail", expected, "Validation script exists, but operation guide does not explain how to run validation.", evidence_refs)
    observed = f"Validation script exists with structural markers {markers[:7]}."
    return dimension(name, "pass", expected, observed, evidence_refs)


def evaluate_sync_artifacts(ctx: EvalContext, evidence: EvidenceBag) -> DimensionResult:
    name = "sync_artifacts"
    applicable = expected_by_keyword(ctx, ["sync", "github", "feishu", "同步", "发布", "飞书"])
    sync_files = find_files(
        ctx.run_dir,
        [
            "tool_results/github_*.json",
            "tool_results/self_sync_*.json",
            "tool_results/post_change_*.json",
            "tool_results/*feishu*.json",
            "recovery/continuation.json",
        ],
    )
    evidence_refs = add_existing_files(evidence, sync_files[:12], "sync artifact")
    expected = "Sync cases produce auditable private/Feishu/public artifacts, or block with a concrete setup/auth reason."
    if not applicable and not sync_files:
        observed = "No sync/public/private/Feishu requirement detected; dimension recorded as not applicable for this case."
        return dimension(name, "pass", expected, observed, evidence_refs)
    if no_external_sync_requested(ctx) and not sync_files:
        observed = "Case explicitly disables GitHub/Feishu sync; sync boundary is satisfied by not producing external sync artifacts."
        return dimension(name, "pass", expected, observed, evidence_refs)
    if ctx.run_dir is None or not ctx.run_dir.exists():
        return missing_run_result(name)
    block_reason = blocked_interaction_reason(ctx)
    if not sync_files:
        if block_reason:
            return dimension(name, "blocked", expected, f"No sync artifact found because {block_reason}.", evidence_refs)
        return dimension(name, "fail", expected, "No sync artifact was found under the run tool_results/recovery surfaces.", evidence_refs)
    file_names = [path.name for path in sync_files]
    wants_public = wants_explicit_public_sync(ctx)
    if wants_public:
        has_staging = any("public_staging" in name for name in file_names)
        has_validation = any("public_validation" in name or "fresh_clone" in name for name in file_names)
        if not (has_staging and has_validation):
            return dimension(
                name,
                "fail",
                expected,
                f"Public sync was requested but staging/validation evidence is incomplete; files: {file_names}.",
                evidence_refs,
            )
    observed = f"Found sync artifact(s): {file_names[:10]}."
    return dimension(name, "pass", expected, observed, evidence_refs)


def evaluate_recovery_rebind(ctx: EvalContext, evidence: EvidenceBag) -> DimensionResult:
    name = "recovery_rebind"
    applicable = wants_explicit_recovery_rebind(ctx)
    handoff = ctx.run_dir / "handoffs" / "debug_handoff.json" if ctx.run_dir else Path("handoffs/debug_handoff.json")
    worklog = ctx.run_dir / "worklogs" / "debug_worklog.json" if ctx.run_dir else Path("worklogs/debug_worklog.json")
    rebind = ctx.run_dir / "rebind" / "rebind_result.json" if ctx.run_dir else Path("rebind/rebind_result.json")
    evidence_refs = [
        evidence.add(handoff, "debug handoff artifact"),
        evidence.add(worklog, "debug worklog artifact"),
        evidence.add(rebind, "same-run rebind result"),
    ]
    expected = "Recovery/debug cases keep the original run_id and include handoff, diagnose/edit/test worklog, and rebind result."
    if not applicable:
        observed = "No recovery/debug/rebind requirement detected; dimension recorded as not applicable for this case."
        return dimension(name, "pass", expected, observed, evidence_refs)
    if ctx.run_dir is None or not ctx.run_dir.exists():
        return missing_run_result(name)
    if terminal_without_pending_actions(ctx) and not handoff.exists() and not worklog.exists() and not rebind.exists():
        return dimension(name, "pass", expected, "Run is terminal completed with no pending_actions; archived recovery words in next_task_prompt are not treated as an active rebind requirement.", evidence_refs)
    block_reason = blocked_interaction_reason(ctx)
    if not handoff.exists() or not worklog.exists() or not rebind.exists():
        if block_reason:
            return dimension(name, "blocked", expected, f"Recovery artifacts are incomplete because {block_reason}.", evidence_refs)
        missing = [str(path.relative_to(ctx.run_dir)) for path in [handoff, worklog, rebind] if not path.exists()]
        return dimension(name, "fail", expected, f"Missing recovery/rebind artifact(s): {missing}.", evidence_refs)
    worklog_text = read_text(worklog)
    kind_hits = [kind for kind in ["diagnose", "edit", "test"] if kind in worklog_text.lower()]
    if len(kind_hits) < 3:
        return dimension(name, "fail", expected, f"Debug worklog exists but lacks diagnose/edit/test evidence; found {kind_hits}.", evidence_refs)
    run_texts = "\n".join(read_text(path, limit=80_000) for path in [handoff, worklog, rebind])
    if ctx.run_id and ctx.run_id not in run_texts:
        return dimension(name, "fail", expected, "Recovery artifacts exist but do not mention the original run_id.", evidence_refs)
    observed = "Debug handoff, diagnose/edit/test worklog, and rebind result exist for the original run."
    return dimension(name, "pass", expected, observed, evidence_refs)


def evaluate_playbook_persistence(ctx: EvalContext, evidence: EvidenceBag) -> DimensionResult:
    name = "playbook_persistence"
    applicable = expected_by_keyword(ctx, ["playbook", "recovery", "恢复记录", "恢复经验", "沉淀", "复用"])
    if ctx.project_path is None or not ctx.project_path.exists():
        if applicable:
            return missing_project_result(name)
        return dimension(
            name,
            "pass",
            "Recovery playbook is inspected when the case asks for persistence or reuse.",
            "No playbook persistence requirement detected; dimension recorded as not applicable for this case.",
            [],
        )
    playbook = ctx.project_path / ".nexus" / "recovery-playbook.json"
    recovery_doc = ctx.project_path / "docs" / "recovery-records.md"
    related_files = find_files(
        ctx.run_dir,
        [
            "tool_results/related_recovery_experience.json",
            "tool_results/recovery_context.json",
            "approvals/recovery_playbook_write_required.json",
            "tool_results/recovery_result.json",
            "tool_results/recovery_playbook_write_result.json",
        ],
    )
    evidence_refs = [
        evidence.add(playbook, "project recovery playbook"),
        evidence.add(recovery_doc, "human recovery records document"),
    ]
    evidence_refs.extend(add_existing_files(evidence, related_files[:6], "run recovery context/playbook lookup artifact"))
    expected = "Successful recovery is approved into .nexus/recovery-playbook.json and later runs record related-experience lookup."
    if not applicable:
        observed = "No playbook persistence/reuse requirement detected; dimension recorded as not applicable for this case."
        return dimension(name, "pass", expected, observed, evidence_refs)
    if not playbook.exists():
        return dimension(name, "fail", expected, ".nexus/recovery-playbook.json is missing.", evidence_refs)
    data = load_json(playbook)
    if not data:
        return dimension(name, "fail", expected, ".nexus/recovery-playbook.json exists but is empty or invalid JSON.", evidence_refs)
    entries = data.get("entries") if isinstance(data, dict) and isinstance(data.get("entries"), list) else []
    recovery_result = load_json(ctx.run_dir / "tool_results" / "recovery_result.json") if ctx.run_dir else None
    recovery_completed = (
        bool(ctx.run_dir and (ctx.run_dir / "tool_results" / "recovery_playbook_write_result.json").exists())
        or bool(ctx.run_dir and (ctx.run_dir / "approvals" / "recovery_playbook_write_required.json").exists())
        or (isinstance(recovery_result, dict) and str(recovery_result.get("status") or "") in {"completed", "done"})
    )
    if recovery_completed and not entries:
        return dimension(name, "fail", expected, "Recovery was attempted or approved, but the target project recovery playbook has no entries.", evidence_refs)
    if wants_explicit_recovery_rebind(ctx) and expected_by_keyword(ctx, ["again", "再次", "reuse", "复用", "相似"]) and not related_files:
        return dimension(name, "fail", expected, "Case asks for recovery reuse, but no related recovery experience lookup artifact was found.", evidence_refs)
    observed = "Project recovery playbook exists and recovery context/lookup artifacts were inspected where applicable."
    return dimension(name, "pass", expected, observed, evidence_refs)


def evaluate_monitor_next_prompt(ctx: EvalContext, evidence: EvidenceBag) -> DimensionResult:
    name = "monitor_next_prompt"
    if ctx.run_dir is None or not ctx.run_dir.exists():
        return missing_run_result(name)
    interaction = ctx.run_dir / "interaction.json"
    state = ctx.run_dir / "state.json"
    evidence_refs = [
        evidence.add(interaction, "run interaction artifact", pattern="next"),
        evidence.add(state, "run state artifact", pattern="status"),
    ]
    expected = "interaction/state artifacts expose a concrete next prompt, approval, recovery, stop, or blocked reason for the monitor."
    if not interaction.exists():
        return dimension(name, "fail", expected, "interaction.json is missing.", evidence_refs)
    if terminal_without_pending_actions(ctx):
        return dimension(name, "pass", expected, "Run is terminal completed with no pending_actions; next_task_prompt text is treated as archival guidance, not an active blocked/recovery prompt.", evidence_refs)
    text = read_text(interaction) + "\n" + read_text(state)
    lowered = text.lower()
    next_markers = [
        "$nexus-workflow",
        "next_prompt",
        "next prompt",
        "approve-and-continue",
        "continue-after-input",
        "recover",
        "handoff",
        "rebind",
        "blocked",
        "pending_actions",
        "下一条",
        "继续",
        "审批",
        "阻断",
    ]
    markers = [marker for marker in next_markers if marker.lower() in lowered]
    if not markers:
        return dimension(name, "fail", expected, "interaction/state artifacts do not contain a concrete next prompt or blocked/approval marker.", evidence_refs)
    observed = f"Monitor prompt/status markers found: {markers[:8]}."
    return dimension(name, "pass", expected, observed, evidence_refs)


def evaluate_anti_hardcoding(ctx: EvalContext, evidence: EvidenceBag) -> DimensionResult:
    name = "anti_hardcoding"
    if ctx.project_path is None or not ctx.project_path.exists():
        return missing_project_result(name)
    intent_text = f"{ctx.raw_intent}\n{ctx.expected_text}".lower()
    project_files = iter_files(ctx.project_path, max_files=400)
    archive_rels = {
        "docs/intent/original-requirement.md",
        "docs/intent/source-material-index.md",
        "docs/reference-materials.md",
        "docs/search-log.md",
    }
    selected_files = [
        path
        for path in project_files
        if str(path.relative_to(ctx.project_path)) not in archive_rels
        and "runtime" not in path.relative_to(ctx.project_path).parts
        and (
            any(part in {"docs", "workflows", "schemas", "scripts", ".nexus"} for part in path.relative_to(ctx.project_path).parts)
            or path.name.lower().startswith("readme")
        )
    ]
    combined = "\n".join(read_text(path, limit=60_000) for path in selected_files)
    evidence_refs: list[str] = []
    leaked: list[str] = []
    for group, terms in SAMPLE_DOMAIN_TERMS.items():
        group_allowed = any(term.lower() in intent_text for term in terms)
        if group_allowed:
            continue
        for term in terms:
            if term.lower() in combined.lower():
                leaked.append(term)
                leak_file = next((path for path in selected_files if term.lower() in read_text(path, limit=120_000).lower()), None)
                if leak_file:
                    evidence_refs.append(evidence.add(leak_file, f"possible sample leakage term: {term}", pattern=term))
                break
    terms = extract_terms(ctx.raw_intent, limit=40)
    hits, matched = count_term_hits(combined, terms)
    overview = ctx.project_path / "docs" / "project-overview.md"
    evidence_refs.append(evidence.add(overview, "project overview checked for domain specificity"))
    expected = "Project artifacts derive from the current prompt and do not leak sample-only domains or names."
    if leaked:
        return dimension(name, "fail", expected, f"Found sample/domain terms not present in the current intent: {leaked}.", evidence_refs)
    if terms and hits < min(4, max(2, len(terms) // 6)):
        return dimension(name, "fail", expected, f"Project artifacts have weak overlap with current prompt terms; matched {matched}.", evidence_refs)
    observed = f"No sample-only leakage terms found; current-prompt term matches include {matched[:10]}."
    return dimension(name, "pass", expected, observed, evidence_refs)


def dimension(
    name: str,
    status: str,
    expected: str,
    observed: str,
    evidence_refs: list[str],
) -> DimensionResult:
    return DimensionResult(
        name=name,
        surface=SURFACE_BY_DIMENSION[name],
        status=status,
        expected=expected,
        observed=observed,
        evidence_refs=evidence_refs,
        required_nexus_change=REQUIRED_CHANGE_BY_DIMENSION[name] if status == "fail" else None,
    )


EVALUATORS = {
    "intent_capture": evaluate_intent_capture,
    "intent_understanding": evaluate_intent_understanding,
    "reference_reading_record": evaluate_reference_reading_record,
    "search_plan_log": evaluate_search_plan_log,
    "domain_workflows": evaluate_domain_workflows,
    "validation_script": evaluate_validation_script,
    "sync_artifacts": evaluate_sync_artifacts,
    "recovery_rebind": evaluate_recovery_rebind,
    "playbook_persistence": evaluate_playbook_persistence,
    "monitor_next_prompt": evaluate_monitor_next_prompt,
    "anti_hardcoding": evaluate_anti_hardcoding,
}


def build_verdict(ctx: EvalContext) -> dict[str, Any]:
    evidence = EvidenceBag([ctx.case_dir, ctx.nexus_root, ctx.project_path or ctx.case_dir, ctx.run_dir or ctx.case_dir])
    if ctx.execution.get("status") == "refused_external_side_effect" and not ctx.execution.get("allow_external_side_effects"):
        ref = evidence.add(ctx.execution_path or ctx.input_path, "external side-effect refusal artifact", pattern="refused_external_side_effect")
        dimensions = [
            DimensionResult(
                name=name,
                surface=SURFACE_BY_DIMENSION[name],
                status="pass",
                expected="External side-effect cases must refuse execution unless explicit allowance is provided.",
                observed="Harness refused external side-effect commands before running GitHub/Feishu/public sync steps.",
                evidence_refs=[ref],
                required_nexus_change=None,
            )
            for name in DIMENSION_ORDER
        ]
    else:
        dimensions = [EVALUATORS[name](ctx, evidence) for name in DIMENSION_ORDER]
    problem_axis_results = build_problem_axis_results(ctx, dimensions)

    failed = [item for item in dimensions if item.status == "fail"]
    blocked = [item for item in dimensions if item.status == "blocked"]
    problem_failed = [item for item in problem_axis_results if item["status"] == "fail"]
    problem_blocked = [item for item in problem_axis_results if item["status"] == "blocked"]
    if failed:
        verdict = "fail"
    elif blocked:
        verdict = "blocked"
    elif problem_failed:
        verdict = "fail"
    elif problem_blocked:
        verdict = "blocked"
    else:
        verdict = "pass"

    expected = {item.name: item.expected for item in dimensions}
    observed = {item.name: item.observed for item in dimensions}
    failed_surface = sorted({item.surface for item in failed + blocked})
    failed_surface.extend(surface for surface in problem_failed_surfaces(problem_axis_results) if surface not in failed_surface)
    required_changes = [
        {
            "dimension": item.name,
            "surface": item.surface,
            "recommendation": item.required_nexus_change or "Provide missing evaluator evidence or rerun the case with complete artifacts.",
            "reason": item.observed,
        }
        for item in failed
    ]
    required_changes.extend(problem_required_changes(problem_axis_results))
    generalization = next(item for item in dimensions if item.name == "anti_hardcoding")

    return {
        "case_id": ctx.case_id,
        "run_id": ctx.run_id,
        "verdict": verdict,
        "failed_surface": failed_surface,
        "expected": expected,
        "observed": observed,
        "evidence_refs": [evidence_to_dict(item) for item in evidence.items],
        "generalization_check": {
            "status": generalization.status,
            "observed": generalization.observed,
            "evidence_refs": generalization.evidence_refs,
        },
        "problem_matrix": {
            "schema": ctx.problem_matrix.get("schema"),
            "source": ctx.problem_matrix.get("source"),
            "coverage_policy": ctx.problem_matrix.get("coverage_policy", {}),
            "problem_count": len(ctx.problem_matrix.get("problems", [])),
        },
        "problem_axis_results": problem_axis_results,
        "required_nexus_change": required_changes,
        "dimensions": [dimension_to_dict(item) for item in dimensions],
        "input": {
            "case_path": str(ctx.input_path),
            "execution_path": str(ctx.execution_path) if ctx.execution_path else None,
            "nexus_root": str(ctx.nexus_root),
            "project_path": str(ctx.project_path) if ctx.project_path else None,
            "project_path_source": ctx.project_path_source,
            "run_dir": str(ctx.run_dir) if ctx.run_dir else None,
        },
        "heuristic_review_notice": (
            "This evaluator uses local file/content heuristics only. "
            "It is not a complete intelligent review of intent satisfaction, but its evidence_refs and problem_axis_results are auditable."
        ),
    }


def build_problem_axis_results(ctx: EvalContext, dimensions: list[DimensionResult]) -> list[dict[str, Any]]:
    by_dimension = {item.name: item for item in dimensions}
    results: list[dict[str, Any]] = []
    problems = ctx.problem_matrix.get("problems", [])
    if not isinstance(problems, list):
        return results
    for problem in problems:
        if not isinstance(problem, dict):
            continue
        dimension_names = [str(name) for name in problem.get("evaluator_dimensions", []) if str(name)]
        mapped = [by_dimension[name] for name in dimension_names if name in by_dimension]
        missing_dimensions = [name for name in dimension_names if name not in by_dimension]
        if not mapped:
            status = "blocked"
            observed = "No evaluator dimension is mapped for this Nexus problem axis."
        else:
            statuses = [item.status for item in mapped]
            if "fail" in statuses:
                status = "fail"
            elif "blocked" in statuses:
                status = "blocked"
            else:
                status = "pass"
            observed = "; ".join(f"{item.name}: {item.observed}" for item in mapped)
            if missing_dimensions:
                observed += f"; missing evaluator dimensions: {missing_dimensions}"
        results.append(
            {
                "id": str(problem.get("id", "")),
                "title": str(problem.get("title", "")),
                "status": status,
                "expected": str(problem.get("user_problem", "")),
                "observed": observed,
                "dimensions": dimension_names,
                "surfaces": [str(surface) for surface in problem.get("nexus_surfaces", [])],
                "required_evidence": [str(item) for item in problem.get("required_evidence", [])],
                "applies_to_every_case": bool(problem.get("applies_to_every_case")),
                "evidence_refs": sorted({ref for item in mapped for ref in item.evidence_refs}),
                "required_nexus_change": problem_axis_change(problem, mapped, status),
            }
        )
    return results


def problem_axis_change(problem: dict[str, Any], mapped: list[DimensionResult], status: str) -> str | None:
    if status == "pass":
        return None
    failed_recommendations = [item.required_nexus_change for item in mapped if item.required_nexus_change]
    if failed_recommendations:
        return " ".join(failed_recommendations)
    surfaces = ", ".join(str(surface) for surface in problem.get("nexus_surfaces", [])[:4])
    return f"Add auditable evidence for problem axis {problem.get('id', '')} through Nexus surface(s): {surfaces}."


def problem_failed_surfaces(problem_axis_results: list[dict[str, Any]]) -> list[str]:
    surfaces: list[str] = []
    for item in problem_axis_results:
        if item.get("status") not in {"fail", "blocked"}:
            continue
        for surface in item.get("surfaces", []):
            if surface not in surfaces:
                surfaces.append(str(surface))
    return surfaces


def problem_required_changes(problem_axis_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for item in problem_axis_results:
        if item.get("status") != "fail":
            continue
        recommendation = item.get("required_nexus_change")
        if not recommendation:
            continue
        changes.append(
            {
                "dimension": str(item.get("id", "")),
                "surface": ", ".join(str(surface) for surface in item.get("surfaces", [])[:4]),
                "recommendation": str(recommendation),
                "reason": str(item.get("observed", "")),
            }
        )
    return changes


def evidence_to_dict(item: Evidence) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": item.id,
        "path": item.path,
        "note": item.note,
        "exists": item.exists,
    }
    if item.line_start is not None:
        data["line_start"] = item.line_start
    if item.line_end is not None:
        data["line_end"] = item.line_end
    if item.excerpt:
        data["excerpt"] = item.excerpt
    return data


def dimension_to_dict(item: DimensionResult) -> dict[str, Any]:
    return {
        "name": item.name,
        "surface": item.surface,
        "status": item.status,
        "expected": item.expected,
        "observed": item.observed,
        "evidence_refs": item.evidence_refs,
        "required_nexus_change": item.required_nexus_change,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate a Nexus lab case execution.json or case directory and print verdict JSON."
    )
    parser.add_argument("case", help="Path to execution.json or a case directory containing execution.json.")
    parser.add_argument("--output", help="Optional path to write verdict JSON. Default: stdout.")
    parser.add_argument("--nexus-root", help="Override Nexus repository root. Default: execution.json or current cwd.")
    parser.add_argument("--project-path", help="Override target project path.")
    parser.add_argument("--run-id", help="Override Nexus run id.")
    parser.add_argument("--run-dir", help="Override <NEXUS_ARTIFACT_PATH> artifact directory.")
    parser.add_argument("--case-id", help="Override case id.")
    parser.add_argument("--raw-intent", help="Override raw user intent/instruction text.")
    parser.add_argument("--problem-matrix", default=str(DEFAULT_PROBLEM_MATRIX_FILE), help="Problem matrix JSON used to aggregate the 16 Nexus issue axes.")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON instead of indented JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    ctx = discover_context(args)
    result = build_verdict(ctx)
    indent = None if args.compact else 2
    text = json.dumps(result, ensure_ascii=False, indent=indent, sort_keys=False)
    if args.output:
        Path(args.output).expanduser().write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
