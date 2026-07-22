"""Provider adapters for the Overmind v2 broker.

Adapters return observations; only the broker mutates SQLite. Provider-native
logs and state stay on disk and are surfaced as artifact paths.
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
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import BILLING_CLASSES, OvermindError, TERMINAL_STATES


def subscription_env(provider: str) -> dict[str, str]:
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


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)


def process_start_identity(pid: int) -> str | None:
    try:
        fields = (
            Path(f"/proc/{pid}/stat")
            .read_text(encoding="utf-8")
            .rsplit(")", 1)[1]
            .split()
        )
        return fields[19]
    except (FileNotFoundError, PermissionError, IndexError, OSError):
        return None


def process_matches(pid: int, identity: str | None) -> bool:
    return bool(identity) and process_start_identity(pid) == identity


def parse_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


class Provider:
    name = "provider"
    production = True

    def probe(self) -> dict[str, Any]:
        raise NotImplementedError

    def launch(
        self, job: dict[str, Any], brief: str, *, resume_thread: str | None = None
    ) -> dict[str, Any]:
        raise NotImplementedError

    def reconcile(self, job: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def continue_job(
        self, job: dict[str, Any], brief: str, parent: dict[str, Any]
    ) -> dict[str, Any]:
        return self.launch(
            job,
            brief,
            resume_thread=parent.get("provider_thread_id")
            or parent.get("provider_job_id"),
        )

    def interrupt(self, job: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class ExternalCommandProvider(Provider):
    """One-shot JSON provider command used for injected provider adapters."""

    production = False

    def __init__(self, name: str, executable: str) -> None:
        self.name = name
        self.executable = executable
        self._closed = False
        self._processes: set[subprocess.Popen[str]] = set()
        self._processes_lock = threading.Lock()

    def _available(self) -> bool:
        return shutil.which(self.executable) is not None

    def _call(self, action: str, request: dict[str, Any]) -> dict[str, Any]:
        if not self._available():
            raise OvermindError(
                f"{self.name} provider executable is unavailable: {self.executable}"
            )
        with self._processes_lock:
            if self._closed:
                raise OvermindError(f"{self.name} provider is shutting down")
        process = subprocess.Popen(
            [self.executable, action],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=dict(os.environ),
        )
        with self._processes_lock:
            if self._closed:
                process.terminate()
            self._processes.add(process)
        try:
            stdout, stderr = process.communicate(
                json.dumps(request, separators=(",", ":")), timeout=30
            )
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate()
            raise OvermindError(
                f"{self.name} provider {action} timed out"
            ) from error
        finally:
            with self._processes_lock:
                self._processes.discard(process)
        if process.returncode:
            detail = stderr.strip() or stdout.strip()
            raise OvermindError(
                detail
                or f"{self.name} provider {action} exited {process.returncode}"
            )
        response = parse_json(stdout)
        if not response:
            raise OvermindError(
                f"{self.name} provider {action} returned no JSON object"
            )
        return response

    def close(self) -> None:
        with self._processes_lock:
            self._closed = True
            processes = list(self._processes)
        for process in processes:
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 1
        for process in processes:
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    @staticmethod
    def _request(
        job: dict[str, Any], brief: str | None = None
    ) -> dict[str, Any]:
        request = dict(job.get("provider_payload") or {})
        public_job = {
            key: value
            for key, value in job.items()
            if key not in {"capabilities", "provider_payload"}
        }
        public_job.update(request)
        request["job"] = public_job
        request["billing_class"] = job.get("billing_class")
        if job.get("provider_job_id"):
            request["provider_job_id"] = job["provider_job_id"]
        if brief is not None:
            request["brief"] = brief
        return request

    @staticmethod
    def _observation(response: dict[str, Any]) -> dict[str, Any]:
        observation = dict(response)
        artifacts = list(observation.get("artifacts") or [])
        for kind, key in (("result", "result_path"), ("provider-log", "log_path")):
            path = observation.get(key)
            if path and not any(item.get("path") == path for item in artifacts):
                artifacts.append({"kind": kind, "path": path})
        if artifacts:
            observation["artifacts"] = artifacts
        return observation

    def probe(self) -> dict[str, Any]:
        if not self._available():
            return {
                "available": False,
                "reason": f"provider executable is unavailable: {self.executable}",
                "billing_class": "unknown",
            }
        response = self._call("capabilities", {})
        classes = response.get("billing_classes")
        if not response.get("billing_class") and isinstance(classes, list):
            response["billing_class"] = (
                "subscription-native"
                if "subscription-native" in classes
                else (classes[0] if classes else "unknown")
            )
        return response

    def launch(
        self, job: dict[str, Any], brief: str, *, resume_thread: str | None = None
    ) -> dict[str, Any]:
        action = "continue" if resume_thread else "launch"
        request = self._request(job, brief)
        if resume_thread:
            request["provider_job_id"] = resume_thread
        return self._observation(self._call(action, request))

    def continue_job(
        self, job: dict[str, Any], brief: str, parent: dict[str, Any]
    ) -> dict[str, Any]:
        request = self._request(job, brief)
        provider_job_id = parent.get("provider_job_id")
        if not provider_job_id:
            raise OvermindError("parent provider job identity is unavailable")
        request["provider_job_id"] = provider_job_id
        return self._observation(self._call("continue", request))

    def reconcile(self, job: dict[str, Any]) -> dict[str, Any]:
        if not self._available():
            return {
                "state": "unknown",
                "error": f"{self.name} provider is unavailable for reconciliation",
            }
        return self._observation(self._call("reconcile", self._request(job)))

    def interrupt(self, job: dict[str, Any]) -> dict[str, Any]:
        if not self._available():
            return {
                "state": "unknown",
                "error": f"{self.name} provider is unavailable for interruption",
            }
        return self._observation(self._call("interrupt", self._request(job)))


class FakeProvider(Provider):
    name = "fake"
    production = False

    def probe(self) -> dict[str, Any]:
        return {
            "available": True,
            "production": False,
            "billing_class": "subscription-native",
            "launch": True,
            "reconcile": True,
            "continue": True,
            "steer": False,
            "interrupt": True,
            "usage": True,
            "quota": {
                "available": False,
                "reason": "deterministic provider has no quota",
            },
        }

    @staticmethod
    def _settings(brief: str) -> tuple[float, str]:
        delay = 0.05
        match = re.search(r"FAKE_SLEEP=([0-9]+(?:\.[0-9]+)?)", brief)
        if match:
            delay = min(float(match.group(1)), 60.0)
        if "FAKE_FAIL" in brief:
            state = "failed"
        elif "FAKE_UNKNOWN" in brief:
            state = "unknown"
        else:
            state = "succeeded"
        return delay, state

    def launch(
        self, job: dict[str, Any], brief: str, *, resume_thread: str | None = None
    ) -> dict[str, Any]:
        delay, terminal_state = self._settings(brief)
        job_dir = Path(job["brief_path"]).parent
        state_path = job_dir / "fake-state.json"
        log_path = job_dir / "fake.log"
        provider_job_id = str(uuid.uuid4())
        atomic_json(
            state_path,
            {
                "state": "running",
                "due_at": time.time() + delay,
                "terminal_state": terminal_state,
                "provider_job_id": provider_job_id,
                "provider_thread_id": resume_thread or provider_job_id,
                "brief": brief,
            },
        )
        write_private(log_path, f"fake launch {provider_job_id}\n")
        return {
            "state": "running",
            "provider_job_id": provider_job_id,
            "provider_thread_id": resume_thread or provider_job_id,
            "provider_state_path": str(state_path),
            "log_path": str(log_path),
            "artifacts": [
                {"kind": "provider-state", "path": str(state_path)},
                {"kind": "provider-log", "path": str(log_path)},
            ],
        }

    def reconcile(self, job: dict[str, Any]) -> dict[str, Any]:
        path = Path(job.get("provider_state_path") or "")
        if not path.is_file():
            return {"state": "unknown", "error": "fake provider state is unavailable"}
        value = parse_json(path.read_text(encoding="utf-8"))
        state = str(value.get("state", "unknown"))
        if state == "running" and time.time() >= float(value.get("due_at", 0)):
            state = str(value.get("terminal_state", "succeeded"))
            value["state"] = state
            job_dir = path.parent
            result_path = job_dir / "result.md"
            result = (
                f"fake:{value.get('brief', '')}"
                if state == "succeeded"
                else f"fake terminal state: {state}"
            )
            write_private(result_path, result)
            value["result_path"] = str(result_path)
            value["usage"] = {"input_units": 1, "output_units": 1, "source": "fake"}
            atomic_json(path, value)
        update: dict[str, Any] = {
            "state": state,
            "provider_job_id": value.get("provider_job_id"),
            "provider_thread_id": value.get("provider_thread_id"),
        }
        if value.get("result_path"):
            update["result_path"] = value["result_path"]
            update["artifacts"] = [{"kind": "result", "path": value["result_path"]}]
        if value.get("usage"):
            update["usage"] = value["usage"]
        if state == "failed":
            update["error"] = "deterministic fake failure"
        return update

    def interrupt(self, job: dict[str, Any]) -> dict[str, Any]:
        path = Path(job.get("provider_state_path") or "")
        if not path.is_file():
            return {"state": "unknown", "error": "fake state unavailable during stop"}
        value = parse_json(path.read_text(encoding="utf-8"))
        value["state"] = "interrupted"
        atomic_json(path, value)
        return {"state": "interrupted"}


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self) -> None:
        self.binary = os.environ.get(
            "OVERMIND_V2_CLAUDE_BIN",
            os.environ.get(
                "OVERMIND_CLAUDE_BIN", os.environ.get("CLAUDE_BIN", "claude")
            ),
        )

    def _env(self) -> dict[str, str]:
        env = subscription_env("claude")
        env["CLAUDE_BIN"] = self.binary
        return env

    def probe(self) -> dict[str, Any]:
        if shutil.which(self.binary) is None:
            return {
                "available": False,
                "reason": f"Claude CLI not found: {self.binary}",
            }
        auth = subprocess.run(
            [self.binary, "auth", "status", "--json"],
            text=True,
            capture_output=True,
            env=self._env(),
            check=False,
            timeout=10,
        )
        status = parse_json(auth.stdout)
        authenticated = (
            auth.returncode == 0
            and status.get("loggedIn") is True
            and status.get("authMethod") == "claude.ai"
            and status.get("apiProvider") == "firstParty"
        )
        version = subprocess.run(
            [self.binary, "--version"],
            text=True,
            capture_output=True,
            env=self._env(),
            check=False,
            timeout=10,
        )
        agents = subprocess.run(
            [self.binary, "agents", "--help"],
            text=True,
            capture_output=True,
            env=self._env(),
            check=False,
            timeout=10,
        )
        return {
            "available": authenticated and agents.returncode == 0,
            "authenticated": authenticated,
            "billing_class": "subscription-native" if authenticated else "unknown",
            "version": version.stdout.strip() or version.stderr.strip(),
            "background_agents": agents.returncode == 0,
            "launch": agents.returncode == 0,
            "reconcile": True,
            "continue": True,
            "steer": False,
            "interrupt": True,
            "usage": True,
            "quota": {
                "available": False,
                "reason": "Claude CLI does not expose an authoritative quota snapshot",
            },
            "auth": {
                "auth_method": status.get("authMethod"),
                "api_provider": status.get("apiProvider"),
                "subscription_type": status.get("subscriptionType"),
            },
        }

    @staticmethod
    def _parse_job_id(output: str) -> str | None:
        identifier = (
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
            r"|[0-9a-fA-F]{8}"
        )
        for raw_line in output.splitlines():
            line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw_line).strip()
            exact = re.fullmatch(identifier, line)
            if exact:
                return exact.group(0)
            labelled = re.search(
                rf"\b(?:job|agent)(?:\s+id)?\s*[:=]\s*({identifier})\b",
                line,
                re.IGNORECASE,
            )
            if labelled:
                return labelled.group(1)
        return None

    def _agents(self) -> list[dict[str, Any]]:
        completed = subprocess.run(
            [self.binary, "agents", "--json", "--all"],
            text=True,
            capture_output=True,
            env=self._env(),
            check=False,
            timeout=10,
        )
        if completed.returncode:
            return []
        try:
            value = json.loads(completed.stdout)
        except ValueError:
            return []
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def _recover_agent_id(self, job: dict[str, Any]) -> str | None:
        launch_name = f"overmind-{job['short_id']}"
        matches = [
            item
            for item in self._agents()
            if item.get("name") == launch_name and item.get("cwd") == job["cwd"]
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: float(item.get("startedAt", 0) or 0))
        selected = matches[-1]
        identifier = selected.get("id") or selected.get("daemonShort")
        if not identifier and selected.get("sessionId"):
            identifier = str(selected["sessionId"])[:8]
        return str(identifier)[:8] if identifier else None

    def launch(
        self, job: dict[str, Any], brief: str, *, resume_thread: str | None = None
    ) -> dict[str, Any]:
        capabilities = job.get("capabilities") or {}
        if (
            not capabilities.get("available")
            or capabilities.get("billing_class") != "subscription-native"
        ):
            raise OvermindError(
                "Claude subscription-native capability preflight failed"
            )
        command = [self.binary]
        if resume_thread:
            command += ["--resume", resume_thread]
        command += [
            "--bg",
            "--model",
            job.get("model") or "sonnet",
            "--permission-mode",
            "dontAsk",
            "--name",
            f"overmind-{job['short_id']}",
            "--",
            brief,
        ]
        completed = subprocess.run(
            command,
            cwd=job["cwd"],
            text=True,
            capture_output=True,
            env=self._env(),
            check=False,
            timeout=30,
        )
        job_dir = Path(job["brief_path"]).parent
        launch_log = job_dir / "claude-launch.log"
        write_private(launch_log, completed.stdout + completed.stderr)
        if completed.returncode:
            raise OvermindError(
                completed.stderr.strip()
                or f"Claude launch exited {completed.returncode}"
            )
        provider_job_id = self._parse_job_id(completed.stdout + completed.stderr)
        if not provider_job_id:
            provider_job_id = self._recover_agent_id(job)
        if not provider_job_id:
            raise OvermindError("Claude launch returned no background job ID")
        config_root = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
        short_id = provider_job_id[:8]
        state_path = config_root / "jobs" / short_id / "state.json"
        return {
            "state": "running",
            "provider_job_id": short_id,
            "provider_state_path": str(state_path),
            "log_path": str(launch_log),
            "artifacts": [
                {"kind": "provider-state", "path": str(state_path)},
                {"kind": "provider-launch-log", "path": str(launch_log)},
            ],
        }

    def reconcile(self, job: dict[str, Any]) -> dict[str, Any]:
        if not job.get("provider_job_id"):
            recovered = self._recover_agent_id(job)
            if recovered:
                config_root = Path(
                    os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")
                )
                return {
                    "state": "running",
                    "provider_job_id": recovered,
                    "provider_state_path": str(
                        config_root / "jobs" / recovered / "state.json"
                    ),
                }
        state_path = Path(job.get("provider_state_path") or "")
        if not state_path.is_file():
            if time.time() - float(job.get("updated_at", 0)) < 5:
                return {"state": "running"}
            return {
                "state": "unknown",
                "error": "Claude daemon state path is unavailable",
            }
        value = parse_json(state_path.read_text(encoding="utf-8"))
        raw_state = str(value.get("state", value.get("status", "unknown"))).lower()
        mapping = {
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
        if raw_state in mapping:
            state = mapping[raw_state]
        elif raw_state in {
            "working",
            "running",
            "starting",
            "queued",
            "waiting",
            "idle",
            "blocked",
        }:
            state = "running"
        else:
            state = "unknown"
        update: dict[str, Any] = {
            "state": state,
            "provider_job_id": value.get(
                "daemonShort", value.get("id", job.get("provider_job_id"))
            ),
            "provider_thread_id": value.get("sessionId", value.get("resumeSessionId")),
            "artifacts": [{"kind": "provider-state", "path": str(state_path)}],
        }
        output = value.get("output")
        if state in TERMINAL_STATES and isinstance(output, dict) and "result" in output:
            result_path = Path(job["brief_path"]).parent / "result.md"
            result = output["result"]
            write_private(
                result_path, result if isinstance(result, str) else json.dumps(result)
            )
            update["result_path"] = str(result_path)
            update["artifacts"].append({"kind": "result", "path": str(result_path)})
        usage = value.get("usage")
        if isinstance(usage, dict):
            update["usage"] = usage
        elif isinstance(value.get("tokens"), (int, float)):
            update["usage"] = {
                "tokens": value["tokens"],
                "source": "claude-state",
            }
        detail = value.get("detail", value.get("error"))
        if detail and state in {"failed", "unknown", "interrupted"}:
            update["error"] = str(detail)
        return update

    def interrupt(self, job: dict[str, Any]) -> dict[str, Any]:
        provider_id = job.get("provider_job_id")
        if not provider_id:
            return {
                "state": "unknown",
                "error": "Claude provider job ID is unavailable",
            }
        completed = subprocess.run(
            [self.binary, "stop", str(provider_id)],
            text=True,
            capture_output=True,
            env=self._env(),
            check=False,
            timeout=20,
        )
        if completed.returncode:
            return {
                "state": "unknown",
                "error": completed.stderr.strip() or "Claude stop outcome is unknown",
            }
        return {"state": "interrupted"}


class CodexProvider(Provider):
    name = "codex"

    def __init__(self) -> None:
        self.binary = os.environ.get("OVERMIND_V2_CODEX_BIN", "codex")
        self.runner_script = Path(__file__).resolve()

    def probe(self) -> dict[str, Any]:
        if shutil.which(self.binary) is None:
            return {"available": False, "reason": f"Codex CLI not found: {self.binary}"}
        env = subscription_env("codex")
        auth = subprocess.run(
            [self.binary, "login", "status"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
            timeout=10,
        )
        status = "\n".join((auth.stdout, auth.stderr))
        authenticated = auth.returncode == 0 and bool(
            re.search(r"\bLogged in using ChatGPT\b", status, re.IGNORECASE)
        )
        version = subprocess.run(
            [self.binary, "--version"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
            timeout=10,
        )
        app_server = subprocess.run(
            [self.binary, "app-server", "--help"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
            timeout=10,
        )
        exec_help = subprocess.run(
            [self.binary, "exec", "--help"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
            timeout=10,
        )
        return {
            "available": authenticated and exec_help.returncode == 0,
            "authenticated": authenticated,
            "billing_class": "subscription-native" if authenticated else "unknown",
            "version": version.stdout.strip() or version.stderr.strip(),
            "app_server_available": app_server.returncode == 0,
            "adapter": "exec-json-fallback",
            "launch": exec_help.returncode == 0,
            "reconcile": True,
            "continue": True,
            "steer": False,
            "interrupt": hasattr(os, "pidfd_open")
            and hasattr(signal, "pidfd_send_signal"),
            "usage": True,
            "quota": {
                "available": False,
                "reason": "Codex CLI does not expose an authoritative quota snapshot",
            },
        }

    def launch(
        self, job: dict[str, Any], brief: str, *, resume_thread: str | None = None
    ) -> dict[str, Any]:
        capabilities = job.get("capabilities") or {}
        if (
            not capabilities.get("available")
            or capabilities.get("billing_class") != "subscription-native"
        ):
            raise OvermindError(
                "Codex ChatGPT subscription capability preflight failed"
            )
        job_dir = Path(job["brief_path"]).parent
        provider_state = job_dir / "codex-state.json"
        event_log = job_dir / "codex-events.jsonl"
        error_log = job_dir / "codex-stderr.log"
        result_path = job_dir / "result.md"
        runner_env = subscription_env("codex")
        scripts_root = str(self.runner_script.parent.parent)
        runner_env["PYTHONPATH"] = os.pathsep.join(
            part for part in (scripts_root, runner_env.get("PYTHONPATH", "")) if part
        )
        runner = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "overmind_v2.providers",
                "_codex_runner",
                "--codex-bin",
                self.binary,
                "--cwd",
                job["cwd"],
                "--brief-path",
                job["brief_path"],
                "--state-path",
                str(provider_state),
                "--event-path",
                str(event_log),
                "--error-path",
                str(error_log),
                "--result-path",
                str(result_path),
                *(["--model", str(job["model"])] if job.get("model") else []),
                *(["--resume", resume_thread] if resume_thread else []),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=runner_env,
        )
        threading.Thread(
            target=runner.wait,
            name=f"codex-reaper-{job['short_id']}",
            daemon=True,
        ).start()
        identity = process_start_identity(runner.pid)
        if not identity:
            raise OvermindError("could not establish Codex runner process identity")
        atomic_json(
            provider_state,
            {
                "state": "starting",
                "runner_pid": runner.pid,
                "runner_start_identity": identity,
            },
        )
        return {
            "state": "starting",
            "provider_state_path": str(provider_state),
            "runner_pid": runner.pid,
            "runner_start_identity": identity,
            "log_path": str(event_log),
            "artifacts": [
                {"kind": "provider-state", "path": str(provider_state)},
                {"kind": "provider-event-log", "path": str(event_log)},
                {"kind": "provider-error-log", "path": str(error_log)},
            ],
        }

    def reconcile(self, job: dict[str, Any]) -> dict[str, Any]:
        path = Path(
            job.get("provider_state_path")
            or (Path(job["brief_path"]).parent / "codex-state.json")
        )
        if not path.is_file():
            return {"state": "unknown", "error": "Codex provider state is unavailable"}
        value = parse_json(path.read_text(encoding="utf-8"))
        state = str(value.get("state", "unknown"))
        if state not in TERMINAL_STATES:
            pid = value.get("runner_pid", job.get("runner_pid"))
            identity = value.get(
                "runner_start_identity", job.get("runner_start_identity")
            )
            if not pid or not identity or not process_matches(int(pid), str(identity)):
                return {
                    "state": "unknown",
                    "error": "Codex runner is unobservable; PID was not signaled",
                }
        update: dict[str, Any] = {
            "state": state,
            "provider_job_id": value.get("provider_job_id"),
            "provider_thread_id": value.get("provider_thread_id"),
            "runner_pid": value.get("runner_pid"),
            "runner_start_identity": value.get("runner_start_identity"),
        }
        for key in ("result_path", "log_path", "error"):
            if value.get(key) is not None:
                update[key] = value[key]
        artifacts = []
        for kind, key in (
            ("result", "result_path"),
            ("provider-event-log", "log_path"),
            ("provider-error-log", "error_path"),
        ):
            if value.get(key):
                artifacts.append({"kind": kind, "path": value[key]})
        if artifacts:
            update["artifacts"] = artifacts
        if isinstance(value.get("usage"), dict):
            update["usage"] = value["usage"]
        return update

    def interrupt(self, job: dict[str, Any]) -> dict[str, Any]:
        pid = job.get("runner_pid")
        identity = job.get("runner_start_identity")
        if not pid or not identity or not process_matches(int(pid), str(identity)):
            return {
                "state": "unknown",
                "error": "Codex runner identity is stale or unverifiable; no signal was sent",
            }
        try:
            descriptor = os.pidfd_open(int(pid))
        except (AttributeError, OSError) as error:
            return {
                "state": "unknown",
                "error": f"could not open verified pidfd: {error}",
            }
        try:
            if not process_matches(int(pid), str(identity)):
                return {
                    "state": "unknown",
                    "error": "Codex runner identity changed; no signal was sent",
                }
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        except OSError as error:
            return {
                "state": "unknown",
                "error": f"Codex stop outcome is unknown: {error}",
            }
        finally:
            os.close(descriptor)
        return {"state": "running"}


def provider_registry() -> dict[str, Provider]:
    providers: dict[str, Provider] = {
        "claude": ClaudeProvider(),
        "codex": CodexProvider(),
    }
    fake = os.environ.get("OVERMIND_V2_FAKE_PROVIDER")
    if fake:
        providers["fake"] = (
            FakeProvider()
            if fake.strip().lower() in {"1", "true", "yes", "builtin"}
            else ExternalCommandProvider("fake", fake)
        )
    return providers


def ensure_billing(
    requested: str | None,
    capabilities: dict[str, Any],
    *,
    allow_billing_change: bool = False,
) -> str:
    actual = str(capabilities.get("billing_class", "unknown"))
    if actual not in BILLING_CLASSES:
        actual = "unknown"
    desired = requested or actual
    if desired not in BILLING_CLASSES:
        raise OvermindError(f"invalid billing class: {desired}")
    supported = capabilities.get("billing_classes")
    if isinstance(supported, list) and desired in supported:
        return desired
    if desired != actual and not allow_billing_change:
        raise OvermindError(
            f"provider billing class is {actual}, not {desired}; explicit opt-in is required"
        )
    return actual


def _codex_runner(arguments: argparse.Namespace) -> int:
    state_path = Path(arguments.state_path)
    event_path = Path(arguments.event_path)
    error_path = Path(arguments.error_path)
    result_path = Path(arguments.result_path)
    brief = Path(arguments.brief_path).read_text(encoding="utf-8")
    runner_pid = os.getpid()
    runner_identity = process_start_identity(runner_pid)
    deadline = time.monotonic() + 5
    state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        if state_path.is_file():
            state = parse_json(state_path.read_text(encoding="utf-8"))
            if (
                state.get("runner_pid") == runner_pid
                and state.get("runner_start_identity") == runner_identity
            ):
                break
        time.sleep(0.01)
    else:
        return 1
    state.update(
        state="running",
        runner_pid=runner_pid,
        runner_start_identity=runner_identity,
        log_path=str(event_path),
        error_path=str(error_path),
        result_path=str(result_path),
    )
    atomic_json(state_path, state)

    command = [arguments.codex_bin, "exec"]
    if arguments.resume:
        command += ["resume"]
    command += ["--ignore-user-config", "-c", 'model_provider="openai"']
    if arguments.resume:
        command += [arguments.resume]
    else:
        command += ["-C", arguments.cwd]
    command += ["--skip-git-repo-check", "--json"]
    if arguments.model:
        command += ["-m", arguments.model]
    command += ["-"]

    child: subprocess.Popen[str] | None = None
    child_identity: str | None = None
    interrupted = False
    escalation_started = False

    def escalate_stop(pid: int, identity: str) -> None:
        time.sleep(5)
        if process_matches(pid, identity):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def stop_child(_number: int, _frame: Any) -> None:
        nonlocal escalation_started, interrupted
        interrupted = True
        if child is None or child.poll() is not None:
            return
        if child_identity and process_matches(child.pid, child_identity):
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if not escalation_started:
                escalation_started = True
                threading.Thread(
                    target=escalate_stop,
                    args=(child.pid, child_identity),
                    name=f"codex-stop-escalation-{child.pid}",
                    daemon=True,
                ).start()

    previous = signal.signal(signal.SIGTERM, stop_child)
    event_fd = os.open(event_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    error_fd = os.open(error_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with (
            os.fdopen(event_fd, "w", encoding="utf-8") as events,
            os.fdopen(error_fd, "w", encoding="utf-8") as errors,
        ):
            child = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=events,
                stderr=errors,
                cwd=arguments.cwd,
                env=subscription_env("codex"),
                text=True,
                start_new_session=True,
            )
            child_identity = process_start_identity(child.pid)
            if interrupted:
                stop_child(signal.SIGTERM, None)
            child.communicate(brief)
            return_code = child.returncode
    finally:
        signal.signal(signal.SIGTERM, previous)

    messages: list[str] = []
    usage: dict[str, Any] = {}
    thread_id = arguments.resume
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
        if event.get("type") == "turn.completed" and isinstance(
            event.get("usage"), dict
        ):
            usage = event["usage"]
    write_private(result_path, messages[-1] if messages else "")
    current = parse_json(state_path.read_text(encoding="utf-8"))
    if interrupted:
        terminal = "interrupted"
    else:
        terminal = "succeeded" if return_code == 0 else "failed"
    current.update(
        state=terminal,
        provider_job_id=thread_id,
        provider_thread_id=thread_id,
        result_path=str(result_path),
        log_path=str(event_path),
        error_path=str(error_path),
        usage=usage,
        error=None
        if return_code == 0
        else error_path.read_text(encoding="utf-8")[-4000:],
    )
    atomic_json(state_path, current)
    return int(return_code or 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    runner = subparsers.add_parser("_codex_runner")
    runner.add_argument("--codex-bin", required=True)
    runner.add_argument("--cwd", required=True)
    runner.add_argument("--brief-path", required=True)
    runner.add_argument("--state-path", required=True)
    runner.add_argument("--event-path", required=True)
    runner.add_argument("--error-path", required=True)
    runner.add_argument("--result-path", required=True)
    runner.add_argument("--model")
    runner.add_argument("--resume")
    arguments = parser.parse_args()
    return _codex_runner(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
