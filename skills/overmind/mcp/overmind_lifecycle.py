#!/usr/bin/env python3
"""Dependency-free MCP lifecycle bridge for native Claude and Codex workers.

The server speaks newline-delimited MCP JSON-RPC over stdio. Worker state is
kept outside the server process so a later client can list and manage jobs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = "2025-03-26"
TERMINAL_STATES = {"succeeded", "failed", "interrupted"}
CLAUDE_STATE_MAP = {
    "done": "succeeded",
    "complete": "succeeded",
    "completed": "succeeded",
    "failed": "failed",
    "error": "failed",
    "stopped": "interrupted",
    "cancelled": "interrupted",
    "canceled": "interrupted",
    "killed": "interrupted",
}


class LifecycleError(RuntimeError):
    pass


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def sanitized_subscription_env(provider: str) -> dict[str, str]:
    env = dict(os.environ)
    names = (
        ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
        if provider == "claude"
        else (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
        )
    )
    for name in names:
        env.pop(name, None)
    return env


class Registry:
    def __init__(self, root: Path | None = None) -> None:
        configured = os.environ.get("OVERMIND_STATE_DIR")
        self.root = root or Path(
            configured or Path.home() / ".cache/overmind-lifecycle"
        )
        self.jobs = self.root / "jobs"
        self.jobs.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.jobs.chmod(0o700)
        self.runners: dict[str, subprocess.Popen[bytes]] = {}
        self.script = Path(__file__).resolve()
        self.claude_worker = Path(
            os.environ.get(
                "OVERMIND_CLAUDE_WORKER",
                self.script.parent.parent / "bin" / "claude-worker.sh",
            )
        )
        self.codex_bin = os.environ.get("OVERMIND_CODEX_BIN", "codex")

    def job_dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{12}", job_id):
            raise LifecycleError(f"invalid Overmind job ID: {job_id}")
        return self.jobs / job_id

    def load(self, job_id: str) -> dict[str, Any]:
        path = self.job_dir(job_id) / "job.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise LifecycleError(f"job not found: {job_id}") from error

    def save(self, job: dict[str, Any]) -> None:
        job["updated_at"] = now_iso()
        atomic_json(self.job_dir(job["job_id"]) / "job.json", job)

    def new_job(
        self,
        provider: str,
        cwd: str,
        label: str,
        *,
        parent_job_id: str | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "provider": provider,
            "label": label,
            "cwd": str(Path(cwd).resolve()),
            "state": "starting",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "parent_job_id": parent_job_id,
            "provider_job_id": None,
            "provider_thread_id": None,
            "runner_pid": None,
            "exit_code": None,
            "detail": None,
        }
        self.job_dir(job_id).mkdir(parents=True)
        self.job_dir(job_id).chmod(0o700)
        self.save(job)
        return job

    def public(self, job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items() if key != "runner_pid"}

    def spawn(
        self,
        provider: str,
        brief: str,
        cwd: str,
        label: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        if provider not in {"claude", "codex"}:
            raise LifecycleError("provider must be 'claude' or 'codex'")
        if not brief.strip():
            raise LifecycleError("brief must not be empty")
        if not Path(cwd).is_dir():
            raise LifecycleError(f"working directory does not exist: {cwd}")
        if provider == "claude" and not model:
            raise LifecycleError("Claude spawn requires an explicit model")

        job = self.new_job(provider, cwd, label)
        if provider == "claude":
            self._spawn_claude(job, brief, model or "")
        else:
            self._spawn_codex(job, brief, model=model)
        return self.public(self.load(job["job_id"]))

    def _spawn_claude(self, job: dict[str, Any], brief: str, model: str) -> None:
        command = [
            str(self.claude_worker),
            "run",
            "--subscription",
            "-C",
            job["cwd"],
            "-m",
            model,
            "--name",
            job["label"],
            brief,
        ]
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            env=sanitized_subscription_env("claude"),
            check=False,
        )
        if completed.returncode:
            job.update(
                state="failed",
                exit_code=completed.returncode,
                detail=completed.stderr.strip(),
            )
            self.save(job)
            raise LifecycleError(job["detail"] or "Claude worker launch failed")
        match = re.search(r"^JOB=([^\s]+)$", completed.stdout, re.MULTILINE)
        if not match:
            job.update(state="failed", detail="Claude wrapper returned no job ID")
            self.save(job)
            raise LifecycleError(job["detail"])
        job.update(state="running", provider_job_id=match.group(1))
        self.save(job)

    def _spawn_codex(
        self,
        job: dict[str, Any],
        brief: str,
        *,
        model: str | None = None,
        resume_thread: str | None = None,
    ) -> None:
        directory = self.job_dir(job["job_id"])
        prompt_path = directory / "prompt.txt"
        prompt_path.write_text(brief, encoding="utf-8")
        prompt_path.chmod(0o600)
        # Publish the running state before the child can publish a terminal
        # state; the later PID update reloads instead of overwriting it.
        job.update(state="running", runner_pid=None)
        self.save(job)
        runner = subprocess.Popen(
            [
                sys.executable,
                str(self.script),
                "_run_codex",
                "--state-dir",
                str(self.root),
                "--job-id",
                job["job_id"],
                "--codex-bin",
                self.codex_bin,
                *(["--model", model] if model else []),
                *(["--resume", resume_thread] if resume_thread else []),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=sanitized_subscription_env("codex"),
        )
        self.runners[job["job_id"]] = runner
        current = self.load(job["job_id"])
        current["runner_pid"] = runner.pid
        if resume_thread and not current.get("provider_thread_id"):
            current["provider_thread_id"] = resume_thread
        self.save(current)

    def _claude_command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.claude_worker), *arguments],
            text=True,
            capture_output=True,
            env=sanitized_subscription_env("claude"),
            check=False,
        )

    def sync(self, job: dict[str, Any]) -> dict[str, Any]:
        runner = self.runners.get(job["job_id"])
        if runner and job["state"] in TERMINAL_STATES:
            try:
                runner.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            if runner.poll() is not None:
                self.runners.pop(job["job_id"], None)
        if job["state"] in TERMINAL_STATES:
            return job
        if job["provider"] == "claude":
            provider_id = job.get("provider_job_id")
            if not provider_id:
                return job
            completed = self._claude_command("status", provider_id)
            if completed.returncode:
                job.update(
                    state="failed",
                    exit_code=completed.returncode,
                    detail=completed.stderr.strip(),
                )
            else:
                fields = dict(
                    line.split("=", 1)
                    for line in completed.stdout.splitlines()
                    if "=" in line
                )
                raw_state = fields.get("STATE", "unknown").lower()
                job["state"] = CLAUDE_STATE_MAP.get(raw_state, "running")
                job["provider_thread_id"] = fields.get("SESSION") or job.get(
                    "provider_thread_id"
                )
                job["detail"] = fields.get("DETAIL") or job.get("detail")
                if job["state"] == "succeeded":
                    job["exit_code"] = 0
            self.save(job)
            return job

        pid = job.get("runner_pid")
        if pid and not process_alive(int(pid)):
            # The durable runner normally writes the terminal state. If it was
            # killed before doing so, preserve an explicit interrupt/failure.
            refreshed = self.load(job["job_id"])
            if refreshed["state"] not in TERMINAL_STATES:
                refreshed["state"] = (
                    "interrupted" if refreshed["state"] == "interrupting" else "failed"
                )
                refreshed["detail"] = (
                    refreshed.get("detail") or "Codex runner exited unexpectedly"
                )
                self.save(refreshed)
            return refreshed
        return job

    def status(self, job_id: str) -> dict[str, Any]:
        return self.public(self.sync(self.load(job_id)))

    def list(self, provider: str | None = None) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for path in sorted(self.jobs.glob("*/job.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if provider and job["provider"] != provider:
                    continue
                found.append(self.public(self.sync(job)))
            except (OSError, ValueError, KeyError):
                continue
        return sorted(found, key=lambda item: item["created_at"])

    def wait(self, job_id: str, timeout_seconds: float = 30) -> dict[str, Any]:
        deadline = time.monotonic() + max(0, min(timeout_seconds, 3600))
        while True:
            job = self.sync(self.load(job_id))
            if job["state"] in TERMINAL_STATES or time.monotonic() >= deadline:
                return self.public(job)
            time.sleep(0.25)

    def result(self, job_id: str) -> dict[str, Any]:
        job = self.sync(self.load(job_id))
        if job["provider"] == "claude":
            if job["state"] not in TERMINAL_STATES:
                raise LifecycleError(f"job is not finished: {job_id}")
            completed = self._claude_command("last", job["provider_job_id"])
            result = completed.stdout
            if completed.returncode and not result:
                raise LifecycleError(
                    completed.stderr.strip() or "Claude result unavailable"
                )
        else:
            path = self.job_dir(job_id) / "result.md"
            if not path.exists():
                raise LifecycleError(f"result is not available for job: {job_id}")
            result = path.read_text(encoding="utf-8")
        return {"job": self.public(job), "result": result}

    def followup(
        self, job_id: str, prompt: str, label: str | None = None
    ) -> dict[str, Any]:
        parent = self.sync(self.load(job_id))
        if parent["state"] not in TERMINAL_STATES:
            raise LifecycleError("follow-up requires a terminal parent job")
        if not prompt.strip():
            raise LifecycleError("follow-up prompt must not be empty")
        child = self.new_job(
            parent["provider"],
            parent["cwd"],
            label or f"{parent['label']}-followup",
            parent_job_id=job_id,
        )
        if parent["provider"] == "claude":
            completed = self._claude_command(
                "cont", "--subscription", parent["provider_job_id"], prompt
            )
            match = re.search(r"^JOB=([^\s]+)$", completed.stdout, re.MULTILINE)
            if completed.returncode or not match:
                child.update(
                    state="failed",
                    exit_code=completed.returncode,
                    detail=completed.stderr.strip()
                    or "Claude continuation returned no job ID",
                )
            else:
                child.update(
                    state="running",
                    provider_job_id=match.group(1),
                    provider_thread_id=parent.get("provider_thread_id"),
                )
            self.save(child)
        else:
            thread_id = parent.get("provider_thread_id")
            if not thread_id:
                raise LifecycleError(
                    "Codex thread ID is unavailable; inspect the parent result"
                )
            self._spawn_codex(child, prompt, resume_thread=thread_id)
        return self.public(self.load(child["job_id"]))

    def interrupt(self, job_id: str) -> dict[str, Any]:
        job = self.sync(self.load(job_id))
        if job["state"] in TERMINAL_STATES:
            return self.public(job)
        if job["provider"] == "claude":
            completed = self._claude_command("stop", job["provider_job_id"])
            if completed.returncode:
                raise LifecycleError(completed.stderr.strip() or "Claude stop failed")
            job.update(state="interrupted", detail="stopped by Overmind")
        else:
            pid = job.get("runner_pid")
            job.update(state="interrupting", detail="interrupt requested")
            self.save(job)
            if pid and process_alive(int(pid)):
                try:
                    os.killpg(int(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            job.update(state="interrupted")
        self.save(job)
        runner = self.runners.pop(job_id, None)
        if runner:
            try:
                runner.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        return self.public(job)

    def cleanup(
        self, job_id: str, delete_provider_state: bool = False
    ) -> dict[str, Any]:
        job = self.sync(self.load(job_id))
        if job["state"] not in TERMINAL_STATES:
            raise LifecycleError("cleanup requires a terminal job; interrupt it first")
        provider_state_deleted = False
        if (
            delete_provider_state
            and job["provider"] == "claude"
            and job.get("provider_job_id")
        ):
            completed = self._claude_command("rm", job["provider_job_id"])
            if completed.returncode:
                raise LifecycleError(
                    completed.stderr.strip() or "Claude provider cleanup failed"
                )
            provider_state_deleted = True
        shutil.rmtree(self.job_dir(job_id))
        return {
            "job_id": job_id,
            "cleaned": True,
            "provider_state_deleted": provider_state_deleted,
        }


def run_codex_runner(args: argparse.Namespace) -> int:
    registry = Registry(Path(args.state_dir))
    # Do not run Codex until the parent has durably published this runner's
    # PID. This handshake prevents a very fast terminal write from racing a
    # stale parent update that would move the job back to "running".
    deadline = time.monotonic() + 5
    while True:
        job = registry.load(args.job_id)
        if job.get("runner_pid") == os.getpid():
            break
        if time.monotonic() >= deadline:
            job.update(
                state="failed", exit_code=1, detail="runner PID publication timed out"
            )
            registry.save(job)
            return 1
        time.sleep(0.01)
    if job["state"] in {"interrupting", "interrupted"}:
        job.update(
            state="interrupted", detail=job.get("detail") or "interrupt requested"
        )
        registry.save(job)
        return 0
    directory = registry.job_dir(args.job_id)
    prompt = (directory / "prompt.txt").read_text(encoding="utf-8")
    event_path = directory / "events.jsonl"
    error_path = directory / "stderr.log"
    if args.resume:
        command = [
            args.codex_bin,
            "exec",
            "resume",
            args.resume,
            "--json",
            "--skip-git-repo-check",
            "-",
        ]
    else:
        command = [
            args.codex_bin,
            "exec",
            "-C",
            job["cwd"],
            "--skip-git-repo-check",
            "--json",
            *(["-m", args.model] if args.model else []),
            "-",
        ]
    try:
        event_fd = os.open(event_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        error_fd = os.open(error_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with (
            os.fdopen(event_fd, "w", encoding="utf-8") as events,
            os.fdopen(error_fd, "w", encoding="utf-8") as errors,
        ):
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                stdout=events,
                stderr=errors,
                cwd=job["cwd"],
                env=sanitized_subscription_env("codex"),
                check=False,
            )
        messages: list[str] = []
        thread_id = job.get("provider_thread_id")
        for line in event_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id") or thread_id
            item = event.get("item") or {}
            if (
                event.get("type") == "item.completed"
                and item.get("type") == "agent_message"
            ):
                messages.append(str(item.get("text", "")))
        result = messages[-1] if messages else ""
        result_path = directory / "result.md"
        result_path.write_text(result, encoding="utf-8")
        result_path.chmod(0o600)
        job = registry.load(args.job_id)
        if job["state"] in {"interrupting", "interrupted"}:
            job.update(
                state="interrupted", detail=job.get("detail") or "interrupt requested"
            )
        else:
            job.update(
                state="succeeded" if completed.returncode == 0 else "failed",
                exit_code=completed.returncode,
                provider_thread_id=thread_id,
                detail=None
                if completed.returncode == 0
                else error_path.read_text(encoding="utf-8")[-4000:],
            )
        registry.save(job)
        return completed.returncode
    except Exception as error:  # runner must leave a durable terminal record
        job = registry.load(args.job_id)
        job.update(state="failed", exit_code=1, detail=str(error))
        registry.save(job)
        return 1


TOOLS: list[dict[str, Any]] = [
    {
        "name": "overmind_capabilities",
        "description": "Describe lifecycle support and provider-specific limitations.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "overmind_spawn",
        "description": "Start a subscription-authenticated Claude or Codex worker and return a durable job ID.",
        "inputSchema": {
            "type": "object",
            "required": ["provider", "brief", "cwd", "label"],
            "properties": {
                "provider": {"enum": ["claude", "codex"]},
                "brief": {"type": "string"},
                "cwd": {"type": "string"},
                "label": {"type": "string"},
                "model": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "overmind_list",
        "description": "List durable Overmind jobs, optionally filtered by provider.",
        "inputSchema": {
            "type": "object",
            "properties": {"provider": {"enum": ["claude", "codex"]}},
            "additionalProperties": False,
        },
    },
    *[
        {
            "name": f"overmind_{name}",
            "description": description,
            "inputSchema": {
                "type": "object",
                "required": ["job_id"],
                "properties": {"job_id": {"type": "string"}},
                "additionalProperties": False,
            },
        }
        for name, description in (
            ("status", "Refresh and return one job's lifecycle state."),
            ("result", "Return a terminal job's final worker message."),
            ("interrupt", "Stop a running worker while retaining its state."),
        )
    ],
    {
        "name": "overmind_wait",
        "description": "Wait up to a bounded timeout for a job to reach a terminal state.",
        "inputSchema": {
            "type": "object",
            "required": ["job_id"],
            "properties": {
                "job_id": {"type": "string"},
                "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 3600},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "overmind_followup",
        "description": "Continue a terminal worker's provider conversation as a new durable job.",
        "inputSchema": {
            "type": "object",
            "required": ["job_id", "prompt"],
            "properties": {
                "job_id": {"type": "string"},
                "prompt": {"type": "string"},
                "label": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "overmind_cleanup",
        "description": "Delete a terminal job's adapter record; optionally delete Claude's provider record too.",
        "inputSchema": {
            "type": "object",
            "required": ["job_id"],
            "properties": {
                "job_id": {"type": "string"},
                "delete_provider_state": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
]


def capabilities() -> dict[str, Any]:
    return {
        "vocabulary": [
            "spawn",
            "list",
            "status",
            "wait",
            "result",
            "followup",
            "interrupt",
            "cleanup",
        ],
        "claude": {
            "transport": "claude --bg via claude-worker.sh",
            "continuation": True,
            "interrupt": True,
            "fork": False,
            "limitation": "Cross-harness jobs appear in Claude's daemon registry, not the Codex native registry.",
        },
        "codex": {
            "transport": "codex exec --json / codex exec resume",
            "continuation": True,
            "interrupt": True,
            "fork": False,
            "limitation": "The exec protocol has no live turn steering or fork; follow-up starts after a terminal turn. Use native Codex collaboration for those capabilities.",
        },
        "billing": "Native CLI subscription authentication only; provider API environment overrides are removed.",
    }


def dispatch(registry: Registry, name: str, arguments: dict[str, Any]) -> Any:
    if name == "overmind_capabilities":
        return capabilities()
    if name == "overmind_spawn":
        return registry.spawn(**arguments)
    if name == "overmind_list":
        return {"jobs": registry.list(arguments.get("provider"))}
    if name == "overmind_status":
        return registry.status(arguments["job_id"])
    if name == "overmind_wait":
        return registry.wait(arguments["job_id"], arguments.get("timeout_seconds", 30))
    if name == "overmind_result":
        return registry.result(arguments["job_id"])
    if name == "overmind_followup":
        return registry.followup(
            arguments["job_id"], arguments["prompt"], arguments.get("label")
        )
    if name == "overmind_interrupt":
        return registry.interrupt(arguments["job_id"])
    if name == "overmind_cleanup":
        return registry.cleanup(
            arguments["job_id"], arguments.get("delete_provider_state", False)
        )
    raise LifecycleError(f"unknown tool: {name}")


def reply(
    request_id: Any, result: Any = None, error: dict[str, Any] | None = None
) -> None:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is None:
        message["result"] = result
    else:
        message["error"] = error
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def serve() -> int:
    registry = Registry()
    for raw in sys.stdin:
        request_id = None
        try:
            request = json.loads(raw)
            method = request.get("method")
            request_id = request.get("id")
            if method == "initialize":
                reply(
                    request_id,
                    {
                        "protocolVersion": request.get("params", {}).get(
                            "protocolVersion", PROTOCOL_VERSION
                        ),
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {
                            "name": "overmind-lifecycle",
                            "version": "0.1.0",
                        },
                    },
                )
            elif method == "ping":
                reply(request_id, {})
            elif method == "tools/list":
                reply(request_id, {"tools": TOOLS})
            elif method == "tools/call":
                params = request.get("params", {})
                value = dispatch(
                    registry, params.get("name", ""), params.get("arguments", {})
                )
                reply(
                    request_id,
                    {
                        "content": [
                            {"type": "text", "text": json.dumps(value, indent=2)}
                        ],
                        "structuredContent": value,
                        "isError": False,
                    },
                )
            elif request_id is not None:
                reply(
                    request_id,
                    error={"code": -32601, "message": f"Method not found: {method}"},
                )
        except Exception as error:
            if request_id is not None:
                reply(
                    request_id,
                    {
                        "content": [{"type": "text", "text": str(error)}],
                        "isError": True,
                    },
                )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    runner = subparsers.add_parser("_run_codex", help=argparse.SUPPRESS)
    runner.add_argument("--state-dir", required=True)
    runner.add_argument("--job-id", required=True)
    runner.add_argument("--codex-bin", required=True)
    runner.add_argument("--model")
    runner.add_argument("--resume")
    args = parser.parse_args()
    if args.command == "_run_codex":
        return run_codex_runner(args)
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
