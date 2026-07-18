#!/usr/bin/env python3
"""Dependency-free MCP lifecycle bridge for native Claude and Codex workers.

The server speaks newline-delimited MCP JSON-RPC over stdio. Worker state is
kept outside the server process so a later client can list and manage jobs.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
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


class RequestCancelled(LifecycleError):
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


def process_start_identity(pid: int) -> str | None:
    """Return Linux's immutable process start tick for a PID, if it exists."""

    try:
        # comm may contain spaces or parentheses, so split after its final ')'.
        fields = (
            Path(f"/proc/{pid}/stat")
            .read_text(encoding="utf-8")
            .rsplit(")", 1)[1]
            .split()
        )
        return fields[19]  # field 22 overall; fields starts at proc field 3
    except (FileNotFoundError, PermissionError, IndexError, OSError):
        return None


def process_matches(pid: int, start_identity: str | None) -> bool:
    return bool(start_identity) and process_start_identity(pid) == start_identity


def sanitized_subscription_env(provider: str) -> dict[str, str]:
    env = dict(os.environ)
    names = (
        (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
        )
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
        self.runners_lock = threading.Lock()
        self.script = Path(__file__).resolve()
        self.claude_worker = Path(
            os.environ.get(
                "OVERMIND_CLAUDE_WORKER",
                self.script.parent.parent / "bin" / "claude-worker.sh",
            )
        )
        self.claude_bin = os.environ.get(
            "OVERMIND_CLAUDE_BIN", os.environ.get("CLAUDE_BIN", "claude")
        )
        self.codex_bin = os.environ.get("OVERMIND_CODEX_BIN", "codex")

    def job_dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{12}", job_id):
            raise LifecycleError(f"invalid Overmind job ID: {job_id}")
        return self.jobs / job_id

    @contextlib.contextmanager
    def job_lock(self, job_id: str) -> Any:
        directory = self.job_dir(job_id)
        if not directory.is_dir():
            raise LifecycleError(f"job not found: {job_id}")
        lock_path = directory / ".lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _load_unlocked(self, job_id: str) -> dict[str, Any]:
        path = self.job_dir(job_id) / "job.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise LifecycleError(f"job not found: {job_id}") from error

    def _save_unlocked(self, job: dict[str, Any]) -> None:
        job["revision"] = int(job.get("revision", 0)) + 1
        job["updated_at"] = now_iso()
        atomic_json(self.job_dir(job["job_id"]) / "job.json", job)

    def load(self, job_id: str) -> dict[str, Any]:
        with self.job_lock(job_id):
            return self._load_unlocked(job_id)

    def save(self, job: dict[str, Any]) -> None:
        with self.job_lock(job["job_id"]):
            self._save_unlocked(job)

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
            "runner_start_identity": None,
            "exit_code": None,
            "detail": None,
            "revision": 0,
        }
        self.job_dir(job_id).mkdir(parents=True)
        self.job_dir(job_id).chmod(0o700)
        with self.job_lock(job_id):
            self._save_unlocked(job)
        return job

    def public(self, job: dict[str, Any]) -> dict[str, Any]:
        private = {"runner_pid", "runner_start_identity"}
        return {key: value for key, value in job.items() if key not in private}

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
        if provider == "claude":
            self._verify_claude_subscription()
        else:
            self._verify_codex_subscription()

        job = self.new_job(provider, cwd, label)
        if provider == "claude":
            self._spawn_claude(job, brief, model or "")
        else:
            self._spawn_codex(job, brief, model=model)
        return self.public(self.load(job["job_id"]))

    def _spawn_claude(self, job: dict[str, Any], brief: str, model: str) -> None:
        completed = self._claude_command(
            "run",
            "--subscription",
            "-C",
            job["cwd"],
            "-m",
            model,
            "--name",
            job["label"],
            brief,
        )
        if completed.returncode:
            with self.job_lock(job["job_id"]):
                current = self._load_unlocked(job["job_id"])
                if current["state"] in {"interrupting", "interrupted"}:
                    current.update(
                        state="interrupted",
                        detail=completed.stderr.strip()
                        or "Claude launch failed after interrupt",
                    )
                else:
                    current.update(
                        state="failed",
                        exit_code=completed.returncode,
                        detail=completed.stderr.strip(),
                    )
                self._save_unlocked(current)
            raise LifecycleError(
                completed.stderr.strip() or "Claude worker launch failed"
            )
        match = re.search(r"^JOB=([^\s]+)$", completed.stdout, re.MULTILINE)
        if not match:
            with self.job_lock(job["job_id"]):
                current = self._load_unlocked(job["job_id"])
                if current["state"] in {"interrupting", "interrupted"}:
                    current.update(
                        state="interrupted",
                        detail="Claude returned no job ID after interrupt",
                    )
                else:
                    current.update(
                        state="failed", detail="Claude wrapper returned no job ID"
                    )
                self._save_unlocked(current)
            raise LifecycleError("Claude wrapper returned no job ID")
        provider_id = match.group(1)
        stop_after_launch = False
        with self.job_lock(job["job_id"]):
            current = self._load_unlocked(job["job_id"])
            current["provider_job_id"] = provider_id
            if current["state"] in {"interrupting", "interrupted"}:
                current["state"] = "interrupting"
                stop_after_launch = True
            else:
                current["state"] = "running"
            self._save_unlocked(current)
        if stop_after_launch:
            stopped = self._claude_command("stop", provider_id)
            with self.job_lock(job["job_id"]):
                current = self._load_unlocked(job["job_id"])
                current["state"] = (
                    "interrupted" if stopped.returncode == 0 else "unknown"
                )
                current["detail"] = (
                    "stopped after launch-time interrupt"
                    if stopped.returncode == 0
                    else stopped.stderr.strip() or "Claude stop status is unknown"
                )
                self._save_unlocked(current)

    def _verify_codex_subscription(self) -> None:
        completed = subprocess.run(
            [self.codex_bin, "login", "status"],
            text=True,
            capture_output=True,
            env=sanitized_subscription_env("codex"),
            check=False,
        )
        status = "\n".join((completed.stdout, completed.stderr)).strip()
        if completed.returncode != 0 or not re.search(
            r"\bLogged in using ChatGPT\b", status, re.IGNORECASE
        ):
            raise LifecycleError(
                "Codex subscription preflight failed: expected `Logged in using ChatGPT`; "
                f"got {status or 'no login status'}"
            )

    def _verify_claude_subscription(self) -> None:
        env = sanitized_subscription_env("claude")
        env["CLAUDE_BIN"] = self.claude_bin
        completed = subprocess.run(
            [self.claude_bin, "auth", "status", "--json"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        try:
            status = json.loads(completed.stdout)
        except ValueError:
            status = {}
        if (
            completed.returncode != 0
            or status.get("loggedIn") is not True
            or status.get("authMethod") != "claude.ai"
            or status.get("apiProvider") != "firstParty"
            or not status.get("subscriptionType")
        ):
            detail = (
                completed.stderr.strip() or completed.stdout.strip() or "no auth status"
            )
            raise LifecycleError(
                "Claude subscription preflight failed: expected a logged-in claude.ai "
                f"subscription; got {detail}"
            )

    def _validate_codex_thread_id(self, value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", value
        ):
            raise LifecycleError(
                "Codex thread ID is missing or invalid; no follow-up job was created"
            )
        return value

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
        # Hold the job lock through runner creation and identity publication.
        # Interrupt therefore linearizes either before launch (no child) or
        # after a fully identifiable runner exists.
        with self.job_lock(job["job_id"]):
            current = self._load_unlocked(job["job_id"])
            if current["state"] in {"interrupting", "interrupted"}:
                return
            current.update(
                state="launching", runner_pid=None, runner_start_identity=None
            )
            self._save_unlocked(current)
            try:
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
            except OSError as error:
                current.update(
                    state="failed", detail=f"Codex runner launch failed: {error}"
                )
                self._save_unlocked(current)
                raise LifecycleError(current["detail"]) from error
            start_identity = process_start_identity(runner.pid)
            current["runner_pid"] = runner.pid
            current["runner_start_identity"] = start_identity
            current["state"] = "running"
            if resume_thread and not current.get("provider_thread_id"):
                current["provider_thread_id"] = resume_thread
            if not start_identity:
                current.update(
                    state="failed",
                    detail="could not record Codex runner process identity",
                )
            self._save_unlocked(current)
        self._own_runner(job["job_id"], runner)

    def _own_runner(self, job_id: str, runner: subprocess.Popen[bytes]) -> None:
        """Keep an owning wait callback for every local outer runner."""

        with self.runners_lock:
            self.runners[job_id] = runner

        def reap() -> None:
            runner.wait()
            with self.runners_lock:
                if self.runners.get(job_id) is runner:
                    self.runners.pop(job_id, None)

        threading.Thread(
            target=reap,
            name=f"overmind-reaper-{job_id}",
            daemon=True,
        ).start()

    def _claude_command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        env = sanitized_subscription_env("claude")
        env["CLAUDE_BIN"] = self.claude_bin
        return subprocess.run(
            [str(self.claude_worker), *arguments],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def sync(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = job["job_id"]
        claude_snapshot: dict[str, Any] | None = None
        with self.job_lock(job_id):
            current = self._load_unlocked(job_id)
            if current["state"] not in TERMINAL_STATES:
                if current["provider"] == "claude":
                    provider_id = current.get("provider_job_id")
                    if provider_id and current["state"] != "interrupting":
                        claude_snapshot = dict(current)
                elif current["state"] != "launching":
                    pid = current.get("runner_pid")
                    identity = current.get("runner_start_identity")
                    if not pid or not identity:
                        current.update(
                            state="failed",
                            detail="Codex runner process identity is missing",
                        )
                        self._save_unlocked(current)
                    elif not process_matches(int(pid), str(identity)):
                        current["state"] = (
                            "interrupted"
                            if current["state"] == "interrupting"
                            else "failed"
                        )
                        current["detail"] = (
                            "Codex runner exited or its PID was reused; no signal was sent"
                        )
                        self._save_unlocked(current)
        if claude_snapshot is not None:
            provider_id = claude_snapshot["provider_job_id"]
            completed = self._claude_command("status", provider_id)
            with self.job_lock(job_id):
                current = self._load_unlocked(job_id)
                if (
                    current.get("revision") == claude_snapshot.get("revision")
                    and current["state"] not in TERMINAL_STATES
                    and current.get("provider_job_id") == provider_id
                ):
                    if completed.returncode:
                        current.update(
                            state="unknown",
                            detail=completed.stderr.strip()
                            or "Claude status temporarily unavailable",
                        )
                    else:
                        fields = dict(
                            line.split("=", 1)
                            for line in completed.stdout.splitlines()
                            if "=" in line
                        )
                        raw_state = fields.get("STATE", "unknown").lower()
                        if raw_state in CLAUDE_STATE_MAP:
                            current["state"] = CLAUDE_STATE_MAP[raw_state]
                        elif raw_state in {
                            "working",
                            "running",
                            "starting",
                            "queued",
                            "waiting",
                            "idle",
                            "blocked",
                        }:
                            current["state"] = "running"
                        else:
                            current["state"] = "unknown"
                        current["provider_thread_id"] = fields.get(
                            "SESSION"
                        ) or current.get("provider_thread_id")
                        current["detail"] = fields.get("DETAIL")
                        if current["state"] == "succeeded":
                            current["exit_code"] = 0
                    self._save_unlocked(current)
        return current

    def status(self, job_id: str) -> dict[str, Any]:
        return self.public(self.sync(self.load(job_id)))

    def list(self, provider: str | None = None) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for path in sorted(self.jobs.glob("*/job.json")):
            try:
                job = self.load(path.parent.name)
                if provider and job["provider"] != provider:
                    continue
                found.append(self.public(self.sync(job)))
            except (LifecycleError, OSError, ValueError, KeyError):
                continue
        return sorted(found, key=lambda item: item["created_at"])

    def wait(
        self,
        job_id: str,
        timeout_seconds: float = 30,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0, min(timeout_seconds, 3600))
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RequestCancelled("wait request cancelled")
            job = self.sync(self.load(job_id))
            if job["state"] in TERMINAL_STATES or time.monotonic() >= deadline:
                return self.public(job)
            if cancel_event is not None:
                cancel_event.wait(0.25)
            else:
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
        if parent["provider"] == "claude":
            self._verify_claude_subscription()
            thread_id = None
        else:
            self._verify_codex_subscription()
            thread_id = self._validate_codex_thread_id(parent.get("provider_thread_id"))
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
            provider_id = match.group(1) if match else None
            stop_after_launch = False
            with self.job_lock(child["job_id"]):
                current = self._load_unlocked(child["job_id"])
                if completed.returncode or not provider_id:
                    if current["state"] in {"interrupting", "interrupted"}:
                        current.update(
                            state="interrupted",
                            detail=completed.stderr.strip()
                            or "Claude continuation failed after interrupt",
                        )
                    else:
                        current.update(
                            state="failed",
                            exit_code=completed.returncode,
                            detail=completed.stderr.strip()
                            or "Claude continuation returned no job ID",
                        )
                else:
                    current.update(
                        provider_job_id=provider_id,
                        provider_thread_id=parent.get("provider_thread_id"),
                    )
                    if current["state"] in {"interrupting", "interrupted"}:
                        current["state"] = "interrupting"
                        stop_after_launch = True
                    else:
                        current["state"] = "running"
                self._save_unlocked(current)
            if stop_after_launch and provider_id:
                stopped = self._claude_command("stop", provider_id)
                with self.job_lock(child["job_id"]):
                    current = self._load_unlocked(child["job_id"])
                    current["state"] = (
                        "interrupted" if stopped.returncode == 0 else "unknown"
                    )
                    current["detail"] = (
                        "stopped after continuation-time interrupt"
                        if stopped.returncode == 0
                        else stopped.stderr.strip() or "Claude stop status is unknown"
                    )
                    self._save_unlocked(current)
        else:
            assert thread_id is not None
            self._spawn_codex(child, prompt, resume_thread=thread_id)
        return self.public(self.load(child["job_id"]))

    def interrupt(self, job_id: str) -> dict[str, Any]:
        pidfd: int | None = None
        claude_provider_id: str | None = None
        with self.job_lock(job_id):
            job = self._load_unlocked(job_id)
            if job["state"] in TERMINAL_STATES:
                return self.public(job)
            if job["provider"] == "claude":
                provider_id = job.get("provider_job_id")
                if not provider_id:
                    job.update(
                        state="interrupting", detail="interrupt requested during launch"
                    )
                    self._save_unlocked(job)
                    return self.public(job)
                job.update(state="interrupting", detail="interrupt requested")
                self._save_unlocked(job)
                claude_provider_id = provider_id

            else:
                if job["state"] in {"starting", "launching"} and not job.get(
                    "runner_pid"
                ):
                    job.update(
                        state="interrupted", detail="interrupted before Codex launch"
                    )
                    self._save_unlocked(job)
                    return self.public(job)
                pid = job.get("runner_pid")
                identity = job.get("runner_start_identity")
                if (
                    not pid
                    or not identity
                    or not process_matches(int(pid), str(identity))
                ):
                    job.update(
                        state="failed",
                        detail="Codex runner PID is stale, reused, or unverifiable; no signal was sent",
                    )
                    self._save_unlocked(job)
                    return self.public(job)
                try:
                    pidfd = os.pidfd_open(int(pid))
                except (ProcessLookupError, PermissionError, OSError):
                    job.update(
                        state="failed",
                        detail="Codex runner exited before a verified interrupt handle could be opened",
                    )
                    self._save_unlocked(job)
                    return self.public(job)
                if not process_matches(int(pid), str(identity)):
                    os.close(pidfd)
                    pidfd = None
                    job.update(
                        state="failed",
                        detail="Codex runner PID changed identity before interrupt; no signal was sent",
                    )
                    self._save_unlocked(job)
                    return self.public(job)
                job.update(state="interrupting", detail="interrupt requested")
                self._save_unlocked(job)

        if claude_provider_id is not None:
            completed = self._claude_command("stop", claude_provider_id)
            with self.job_lock(job_id):
                job = self._load_unlocked(job_id)
                if (
                    job["state"] not in TERMINAL_STATES
                    and job.get("provider_job_id") == claude_provider_id
                ):
                    if completed.returncode:
                        job.update(
                            state="unknown",
                            detail=completed.stderr.strip()
                            or "Claude stop status is unknown",
                        )
                    else:
                        job.update(state="interrupted", detail="stopped by Overmind")
                    self._save_unlocked(job)
            if completed.returncode:
                raise LifecycleError(job["detail"])
            return self.public(job)

        assert pidfd is not None
        try:
            signal.pidfd_send_signal(pidfd, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as error:
            with self.job_lock(job_id):
                job = self._load_unlocked(job_id)
                if job["state"] == "interrupting":
                    job.update(
                        state="unknown",
                        detail=f"Codex interrupt outcome unknown: {error}",
                    )
                    self._save_unlocked(job)
            raise LifecycleError(f"Codex interrupt outcome unknown: {error}") from error
        finally:
            os.close(pidfd)
        return self.wait(job_id, 2)

    def cleanup(
        self, job_id: str, delete_provider_state: bool = False
    ) -> dict[str, Any]:
        job = self.sync(self.load(job_id))
        if job["state"] not in TERMINAL_STATES:
            raise LifecycleError("cleanup requires a terminal job; interrupt it first")
        with self.job_lock(job_id):
            job = self._load_unlocked(job_id)
            if job["state"] not in TERMINAL_STATES:
                raise LifecycleError("job changed state before cleanup; retry")
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
        with registry.job_lock(args.job_id):
            job = registry._load_unlocked(args.job_id)
        if job.get("runner_pid") == os.getpid() and job.get(
            "runner_start_identity"
        ) == process_start_identity(os.getpid()):
            break
        if time.monotonic() >= deadline:
            with registry.job_lock(args.job_id):
                job = registry._load_unlocked(args.job_id)
                if job["state"] not in TERMINAL_STATES:
                    job.update(
                        state="failed",
                        exit_code=1,
                        detail="runner process identity publication timed out",
                    )
                    registry._save_unlocked(job)
            return 1
        time.sleep(0.01)
    if job["state"] in {"interrupting", "interrupted"}:
        with registry.job_lock(args.job_id):
            job = registry._load_unlocked(args.job_id)
            job.update(
                state="interrupted", detail=job.get("detail") or "interrupt requested"
            )
            registry._save_unlocked(job)
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
            "--ignore-user-config",
            "-c",
            'model_provider="openai"',
            args.resume,
            "--json",
            "--skip-git-repo-check",
            "-",
        ]
    else:
        command = [
            args.codex_bin,
            "exec",
            "--ignore-user-config",
            "-c",
            'model_provider="openai"',
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
        child: subprocess.Popen[str] | None = None
        child_start_identity: str | None = None
        interrupted = False

        def stop_child(_signal_number: int, _frame: Any) -> None:
            nonlocal interrupted
            interrupted = True
            if child is None or child.poll() is not None:
                return
            if child_start_identity and process_matches(
                child.pid, child_start_identity
            ):
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

        previous_handler = signal.signal(signal.SIGTERM, stop_child)
        with (
            os.fdopen(event_fd, "w", encoding="utf-8") as events,
            os.fdopen(error_fd, "w", encoding="utf-8") as errors,
        ):
            if interrupted:
                return_code = -signal.SIGTERM
            else:
                child = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    text=True,
                    stdout=events,
                    stderr=errors,
                    cwd=job["cwd"],
                    env=sanitized_subscription_env("codex"),
                    start_new_session=True,
                )
                child_start_identity = process_start_identity(child.pid)
                if interrupted:
                    stop_child(signal.SIGTERM, None)
                child.communicate(prompt)
                return_code = child.returncode
        signal.signal(signal.SIGTERM, previous_handler)
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
        with registry.job_lock(args.job_id):
            job = registry._load_unlocked(args.job_id)
            if job["state"] in {"interrupting", "interrupted"} or interrupted:
                job.update(
                    state="interrupted",
                    detail=job.get("detail") or "interrupt requested",
                )
            else:
                job.update(
                    state="succeeded" if return_code == 0 else "failed",
                    exit_code=return_code,
                    provider_thread_id=thread_id,
                    detail=None
                    if return_code == 0
                    else error_path.read_text(encoding="utf-8")[-4000:],
                )
            registry._save_unlocked(job)
        return return_code
    except Exception as error:  # runner must leave a durable terminal record
        with registry.job_lock(args.job_id):
            job = registry._load_unlocked(args.job_id)
            if job["state"] in {"interrupting", "interrupted"}:
                job.update(state="interrupted")
            else:
                job.update(state="failed", exit_code=1, detail=str(error))
            registry._save_unlocked(job)
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
        "description": "Start a subscription-verified Claude or Codex worker and return a durable job ID.",
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
        "billing": {
            "claude": "Requires a logged-in first-party claude.ai subscription and forces that verified CLI into the wrapper after removing direct/external-provider overrides.",
            "codex": "Requires ChatGPT login, ignores custom user config, and pins the built-in OpenAI provider.",
        },
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


REPLY_LOCK = threading.Lock()


def reply(
    request_id: Any, result: Any = None, error: dict[str, Any] | None = None
) -> None:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is None:
        message["result"] = result
    else:
        message["error"] = error
    with REPLY_LOCK:
        sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def handle_tool_call(
    registry: Registry,
    request_id: Any,
    params: dict[str, Any],
    cancel_event: threading.Event | None = None,
) -> None:
    try:
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        if name == "overmind_wait":
            value = registry.wait(
                arguments["job_id"],
                arguments.get("timeout_seconds", 30),
                cancel_event,
            )
        else:
            value = dispatch(registry, name, arguments)
        reply(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(value, indent=2)}],
                "structuredContent": value,
                "isError": False,
            },
        )
    except RequestCancelled as error:
        reply(request_id, error={"code": -32800, "message": str(error)})
    except Exception as error:
        reply(
            request_id,
            {
                "content": [{"type": "text", "text": str(error)}],
                "isError": True,
            },
        )


def serve() -> int:
    registry = Registry()
    pending_waits: dict[
        Any, tuple[concurrent.futures.Future[None], threading.Event]
    ] = {}
    pending_lock = threading.Lock()

    def forget_wait(request_id: Any, future: concurrent.futures.Future[None]) -> None:
        with pending_lock:
            current = pending_waits.get(request_id)
            if current and current[0] is future:
                pending_waits.pop(request_id, None)

    with (
        concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="overmind-control"
        ) as control_executor,
        concurrent.futures.ThreadPoolExecutor(
            max_workers=32, thread_name_prefix="overmind-wait"
        ) as wait_executor,
    ):
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
                    if params.get("name") == "overmind_wait":
                        cancel_event = threading.Event()
                        future = wait_executor.submit(
                            handle_tool_call,
                            registry,
                            request_id,
                            params,
                            cancel_event,
                        )
                        with pending_lock:
                            pending_waits[request_id] = (future, cancel_event)
                        future.add_done_callback(
                            lambda completed, rid=request_id: forget_wait(
                                rid, completed
                            )
                        )
                    else:
                        control_executor.submit(
                            handle_tool_call,
                            registry,
                            request_id,
                            params,
                        )
                elif method == "notifications/cancelled":
                    cancelled_id = request.get("params", {}).get("requestId")
                    with pending_lock:
                        pending = pending_waits.get(cancelled_id)
                    if pending:
                        pending[1].set()
                elif request_id is not None:
                    reply(
                        request_id,
                        error={
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
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
