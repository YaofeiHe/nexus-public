from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .base import ModelRequest, ModelResponse, ProviderExecutionError, ProviderStatus, ProviderUnavailable


class CodexCliProvider:
    name = "codex-cli"

    def __init__(self, *, cwd: Path | None = None, timeout_seconds: int = 300, model: str = "") -> None:
        self.cwd = cwd or Path.cwd()
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.codex_home: Path | None = None
        self.exec_extra_args: list[str] = []
        self.last_smoke_details: dict[str, object] = {}
        self.runtime_status_path: Path | None = None
        self.status_interval_seconds = _status_interval_seconds()

    def status(self) -> ProviderStatus:
        codex = shutil.which("codex")
        if not codex:
            return ProviderStatus(self.name, "unavailable", "未找到 codex 命令。")
        probe = subprocess.run(
            [codex, "exec", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if probe.returncode != 0:
            return ProviderStatus(self.name, "needs_config", "codex exec 无法正常运行。", command=codex)
        help_text = probe.stdout + probe.stderr
        if "--output-schema" not in help_text:
            return ProviderStatus(self.name, "needs_config", "当前 codex exec 未暴露 --output-schema。", command=codex)
        return ProviderStatus(self.name, "available", "codex exec 支持非交互结构化输出。", command=codex)

    def smoke_status(self) -> ProviderStatus:
        status = self.status()
        if status.status != "available":
            return status
        assert status.command
        initial = self._run_smoke_with_command(status.command, codex_home=self.codex_home, extra_args=self.exec_extra_args)
        if initial.status == "available":
            self.last_smoke_details = self._repair_result(False, True, "", [], self.codex_home, self.exec_extra_args)
            return initial
        repaired = self._attempt_auto_repair(status.command, initial.reason, mode="smoke")
        self.last_smoke_details = repaired["details"]
        if repaired["status"].status == "available":
            return repaired["status"]
        return ProviderStatus(
            self.name,
            repaired["status"].status,
            f"{initial.reason} Auto-repair module attempted: {repaired['summary']}",
            command=status.command,
        )

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        status = self.status()
        if status.status != "available":
            raise ProviderUnavailable(status.reason)
        with tempfile.TemporaryDirectory(prefix="nexus-codex-") as tmp:
            tmp_dir = Path(tmp)
            schema_path = tmp_dir / "schema.json"
            output_path = tmp_dir / "last_message.json"
            schema_path.write_text(json.dumps(request.schema, ensure_ascii=False, indent=2), encoding="utf-8")
            prompt = _render_prompt(request)
            cmd = [
                status.command or "codex",
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
            if self.model:
                cmd[2:2] = ["--model", self.model]
            completed = self._run_codex_exec(cmd, prompt, codex_home=self.codex_home, extra_args=self.exec_extra_args)
            raw = output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout
            if completed.returncode != 0:
                failure_reason = completed.stderr or completed.stdout or "codex exec failed"
                repaired = self._attempt_auto_repair(status.command, failure_reason, mode="runtime", base_cmd=cmd, prompt=prompt, output_path=output_path)
                if repaired["completed"] is not None:
                    completed = repaired["completed"]
                    raw = output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout
                else:
                    reason = _short(completed.stderr or completed.stdout or "codex exec failed")
                    raise ProviderExecutionError(f"{reason} Auto-repair module attempted: {repaired['summary']}")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ProviderExecutionError(f"codex exec did not return JSON: {_short(raw)}") from exc
            if not isinstance(data, dict):
                raise ProviderExecutionError("codex exec returned JSON but not an object")
            diagnostics: dict[str, object] = {"command": " ".join(cmd[:2]), "new_codex_task": True}
            if self.model:
                diagnostics["model"] = self.model
            if self.codex_home is not None:
                diagnostics["codex_home"] = str(self.codex_home)
            if self.exec_extra_args:
                diagnostics["codex_exec_extra_args"] = list(self.exec_extra_args)
            return ModelResponse(
                provider=self.name,
                raw_text=raw,
                json_data=data,
                diagnostics=diagnostics,
            )

    def _workspace_codex_home(self) -> Path:
        override = os.environ.get("NEXUS_CODEX_HOME", "").strip()
        if override:
            return Path(override).expanduser().resolve()
        return (self.cwd / ".nexus" / "runtime" / "codex-home").resolve()

    def _exec_env(self, *, codex_home: Path | None = None) -> dict[str, str] | None:
        target_home = codex_home or self.codex_home
        if target_home is None:
            return None
        target_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["CODEX_HOME"] = str(target_home)
        return env

    def _run_smoke_with_command(self, command: str, *, codex_home: Path | None = None, extra_args: list[str] | None = None) -> ProviderStatus:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["ok", "provider"],
            "properties": {"ok": {"type": "boolean"}, "provider": {"type": "string"}},
        }
        with tempfile.TemporaryDirectory(prefix="nexus-codex-smoke-") as tmp:
            tmp_dir = Path(tmp)
            schema_path = tmp_dir / "schema.json"
            output_path = tmp_dir / "last.json"
            schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            cmd = [
                command,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
            try:
                completed = self._run_codex_exec(
                    cmd,
                    "Return JSON only: {\"ok\": true, \"provider\": \"codex-cli\"}.",
                    codex_home=codex_home,
                    extra_args=extra_args,
                )
            except subprocess.TimeoutExpired as exc:
                return ProviderStatus(self.name, "needs_config", f"codex exec smoke test timed out after {exc.timeout} seconds.", command=command)
            if completed.returncode != 0:
                return ProviderStatus(self.name, "needs_config", f"codex exec smoke test failed: {_short(completed.stderr or completed.stdout)}", command=command)
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except Exception as exc:
                return ProviderStatus(self.name, "needs_config", f"codex exec smoke test did not produce JSON: {_short(str(exc))}", command=command)
            if payload.get("ok") is True:
                return ProviderStatus(self.name, "available", "codex exec smoke test passed; real Codex model calls are available.", command=command)
            return ProviderStatus(self.name, "needs_config", "codex exec smoke test returned unexpected JSON.", command=command)

    def _run_codex_exec(self, cmd: list[str], prompt: str, *, codex_home: Path | None = None, extra_args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        run_cmd = list(cmd)
        if extra_args:
            run_cmd[2:2] = list(extra_args)
        started = time.monotonic()
        process = subprocess.Popen(
            run_cmd,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._exec_env(codex_home=codex_home),
        )
        if process.stdin is not None:
            process.stdin.write(prompt)
            process.stdin.close()
            process.stdin = None
        heartbeat_count = 0
        self._write_runtime_status(
            run_cmd,
            status="running",
            pid=process.pid,
            started=started,
            heartbeat_count=heartbeat_count,
            event="started",
        )
        while True:
            elapsed = time.monotonic() - started
            remaining = self.timeout_seconds - elapsed
            if remaining <= 0:
                process.kill()
                stdout, stderr = process.communicate()
                self._write_runtime_status(
                    run_cmd,
                    status="timeout",
                    pid=process.pid,
                    started=started,
                    heartbeat_count=heartbeat_count,
                    event="timeout",
                    returncode=process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                )
                raise subprocess.TimeoutExpired(run_cmd, self.timeout_seconds, output=stdout, stderr=stderr)
            try:
                stdout, stderr = process.communicate(timeout=min(self.status_interval_seconds, remaining))
            except subprocess.TimeoutExpired:
                heartbeat_count += 1
                self._write_runtime_status(
                    run_cmd,
                    status="running",
                    pid=process.pid,
                    started=started,
                    heartbeat_count=heartbeat_count,
                    event="heartbeat",
                )
                continue
            completed = subprocess.CompletedProcess(run_cmd, process.returncode, stdout, stderr)
            self._write_runtime_status(
                run_cmd,
                status="completed" if completed.returncode == 0 else "failed",
                pid=process.pid,
                started=started,
                heartbeat_count=heartbeat_count,
                event="completed",
                returncode=completed.returncode,
                stdout=stdout,
                stderr=stderr,
            )
            return completed

    def _write_runtime_status(
        self,
        command: list[str],
        *,
        status: str,
        pid: int,
        started: float,
        heartbeat_count: int,
        event: str,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        if self.runtime_status_path is None:
            return
        try:
            self.runtime_status_path.parent.mkdir(parents=True, exist_ok=True)
            elapsed = max(0.0, time.monotonic() - started)
            self.runtime_status_path.write_text(
                json.dumps(
                    {
                        "schema": "nexus.provider_runtime_status.v1",
                        "provider": self.name,
                        "event": event,
                        "status": status,
                        "pid": pid,
                        "elapsed_seconds": round(elapsed, 3),
                        "timeout_seconds": self.timeout_seconds,
                        "status_interval_seconds": self.status_interval_seconds,
                        "heartbeat_count": heartbeat_count,
                        "returncode": returncode,
                        "command": _redacted_command(command),
                        "terminal_idle_is_not_failure": status == "running",
                        "no_stdout_yet_is_expected": status == "running",
                        "stdout_tail": _short(stdout, limit=1000),
                        "stderr_tail": _short(stderr, limit=1000),
                        "updated_at_epoch": time.time(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            return

    def _attempt_auto_repair(
        self,
        command: str,
        failure_reason: str,
        *,
        mode: str,
        base_cmd: list[str] | None = None,
        prompt: str = "",
        output_path: Path | None = None,
    ) -> dict[str, object]:
        attempt_records: list[dict[str, object]] = []
        for strategy in self._repair_strategies(failure_reason):
            attempt_records.append(
                {
                    "strategy": strategy["name"],
                    "description": strategy["description"],
                    "codex_home": str(strategy["codex_home"]) if strategy["codex_home"] is not None else "",
                    "extra_args": list(strategy["extra_args"]),
                }
            )
            if mode == "smoke":
                status = self._run_smoke_with_command(
                    command,
                    codex_home=strategy["codex_home"],
                    extra_args=strategy["extra_args"],
                )
                attempt_records[-1]["status"] = status.status
                attempt_records[-1]["reason"] = status.reason
                if status.status == "available":
                    self.codex_home = strategy["codex_home"]
                    self.exec_extra_args = list(strategy["extra_args"])
                    details = self._repair_result(
                        True,
                        True,
                        str(strategy["name"]),
                        attempt_records,
                        self.codex_home,
                        self.exec_extra_args,
                    )
                    return {
                        "status": ProviderStatus(
                            self.name,
                            "available",
                            f"codex exec smoke test passed after auto-repair strategy {strategy['name']}",
                            command=command,
                        ),
                        "summary": _repair_attempt_summary(attempt_records),
                        "details": details,
                        "completed": None,
                    }
                continue
            assert base_cmd is not None
            assert output_path is not None
            completed = self._run_codex_exec(
                base_cmd,
                prompt,
                codex_home=strategy["codex_home"],
                extra_args=strategy["extra_args"],
            )
            attempt_records[-1]["status"] = "available" if completed.returncode == 0 else "needs_config"
            attempt_records[-1]["reason"] = _short(completed.stderr or completed.stdout or "")
            if completed.returncode == 0:
                self.codex_home = strategy["codex_home"]
                self.exec_extra_args = list(strategy["extra_args"])
                return {
                    "status": ProviderStatus(
                        self.name,
                        "available",
                        f"codex exec runtime passed after auto-repair strategy {strategy['name']}",
                        command=command,
                    ),
                    "summary": _repair_attempt_summary(attempt_records),
                    "details": self._repair_result(True, True, str(strategy["name"]), attempt_records, self.codex_home, self.exec_extra_args),
                    "completed": completed,
                }
        return {
            "status": ProviderStatus(self.name, "needs_config", _short(failure_reason), command=command),
            "summary": _repair_attempt_summary(attempt_records),
            "details": self._repair_result(bool(attempt_records), False, "", attempt_records, self.codex_home, self.exec_extra_args),
            "completed": None,
        }

    def _repair_strategies(self, failure_reason: str) -> list[dict[str, object]]:
        workspace_home = self._workspace_codex_home()
        current_home = self.codex_home.resolve() if self.codex_home is not None else None
        current_args = tuple(self.exec_extra_args)
        strategies: list[dict[str, object]] = []

        def add(name: str, *, codex_home: Path | None, extra_args: list[str], description: str) -> None:
            normalized_home = codex_home.resolve() if codex_home is not None else None
            if normalized_home == current_home and tuple(extra_args) == current_args:
                return
            if any(item["name"] == name for item in strategies):
                return
            strategies.append(
                {
                    "name": name,
                    "codex_home": normalized_home,
                    "extra_args": list(extra_args),
                    "description": description,
                }
            )

        add(
            "workspace_local_codex_home",
            codex_home=workspace_home,
            extra_args=[],
            description="Use a writable workspace-local CODEX_HOME to isolate state and credential cache writes.",
        )
        add(
            "isolated_workspace_codex_home",
            codex_home=workspace_home,
            extra_args=["--ignore-user-config"],
            description="Retry with isolated writable CODEX_HOME and ignore user config to rule out host config/state issues.",
        )
        if _looks_like_user_config_issue(failure_reason):
            add(
                "ignore_user_config_only",
                codex_home=current_home,
                extra_args=["--ignore-user-config"],
                description="Retry without loading user config when the failure hints at config parsing or config compatibility issues.",
            )
        return strategies

    def _repair_result(
        self,
        attempted: bool,
        succeeded: bool,
        strategy: str,
        attempts: list[dict[str, object]],
        codex_home: Path | None,
        extra_args: list[str],
    ) -> dict[str, object]:
        return {
            "repair_module": "codex_cli_auto_repair_v2",
            "repair_attempted": attempted,
            "repair_succeeded": succeeded,
            "repair_strategy": strategy,
            "effective_codex_home": str(codex_home) if codex_home is not None else "",
            "effective_extra_args": list(extra_args),
            "attempts": attempts,
        }


def _render_prompt(request: ModelRequest) -> str:
    return (
        "你是 nexus workflow kernel 的模型节点。\n"
        "请严格返回符合 JSON Schema 的 JSON 对象，不要输出 Markdown。\n"
        "默认环境：中文互联网，中文优先，英文作为补充。\n"
        f"节点：{request.node_id}\n"
        f"目的：{request.purpose}\n"
        f"安全边界：{json.dumps(request.safety_boundary, ensure_ascii=False)}\n"
        f"上下文引用：{json.dumps(request.context_refs, ensure_ascii=False)}\n\n"
        "必须满足下面 JSON Schema，包含所有 required 字段，不要添加 schema 不允许的字段：\n"
        f"{json.dumps(request.schema, ensure_ascii=False, indent=2)}\n\n"
        f"{request.prompt}\n"
    )


def _short(value: str, limit: int = 1200) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[:500] + " ... " + value[-700:]


def _status_interval_seconds() -> float:
    raw = os.environ.get("NEXUS_CODEX_CLI_STATUS_INTERVAL_SECONDS", "").strip()
    if not raw:
        return 60.0
    try:
        value = float(raw)
    except ValueError:
        return 60.0
    return max(1.0, value)


def _redacted_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for item in command:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        redacted.append(item)
        if item in {"--api-key", "--token", "--password"}:
            skip_next = True
    return redacted


def _needs_workspace_codex_home_repair(reason: str) -> bool:
    lowered = reason.lower()
    if "readonly database" in lowered or "read-only database" in lowered:
        return True
    return "state_5.sqlite" in lowered and ("operation not permitted" in lowered or "permission" in lowered)


def _looks_like_user_config_issue(reason: str) -> bool:
    lowered = reason.lower()
    return any(
        token in lowered
        for token in [
            "config.toml",
            "strict-config",
            "unknown field",
            "unrecognized field",
            "parse",
            "invalid toml",
            "toml",
        ]
    )


def _repair_attempt_summary(attempts: list[dict[str, object]]) -> str:
    if not attempts:
        return "no local repair strategy matched"
    parts = []
    for item in attempts:
        parts.append(f"{item.get('strategy')}={item.get('status')}:{item.get('reason')}")
    return " ; ".join(parts)
