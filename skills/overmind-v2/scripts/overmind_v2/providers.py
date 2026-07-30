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
from datetime import datetime
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


DEFAULT_CLAUDE_PERMISSION_MODE = "bypassPermissions"

# CLI states that mean the turn is still in motion. Kept as a named set because
# both the state mapping and the "unrecognized state" grace test against it.
CLAUDE_RUNNING_STATES = frozenset(
    {
        "working",
        "running",
        "starting",
        "queued",
        "waiting",
        "idle",
        "respawning",
        "restarting",
    }
)

# Default ceiling on a worker whose state file has stopped changing while the CLI
# still claims work in flight. This bounds silence, not runtime: the CLI rewrites
# state.json on every message and tool result, so any worker that is actually
# making progress resets the clock. An hour of a completely frozen state file is
# longer than the longest legitimately silent single tool call measured here (a
# full Lean `lake build`, a full pytest run), and it replaces the previous
# behavior where a wedged worker could sit unbounded -- one measured job held a
# non-terminal record for 39 hours. Raise it per job when a single step really can
# be silent for longer; 0 disables the ceiling.
CLAUDE_HARD_TIMEOUT_SECONDS = 3600.0

# The transcript is flushed after the CLI marks its state file terminal, so a job
# finalized the instant it goes terminal reads a transcript that is missing the
# worker's last message. Measured: a report written at :41.5 and a terminal marker
# at :43.4 were still not both on disk when the broker read at :44. Recording the
# wrong last message is permanent, because a terminal job is never reconciled
# again, so wait for the file to go quiet -- and stop waiting at the ceiling, since
# a transcript that never settles must not hold a finished job open forever.
CLAUDE_TRANSCRIPT_QUIET_SECONDS = 2.0
CLAUDE_FINALIZE_CEILING_SECONDS = 30.0

# A terminal Claude job whose result artifact is smaller than this did not report
# a work product. Measured over 188 broker-launched Claude jobs: the median
# `succeeded` artifact was 138 bytes of CLI progress note ("approve 1 new project
# MCP server (grafana)", "awaiting write permission"), and only 4.8% carried a
# real report. Calling those `succeeded` is worse than calling them unknown,
# because the orchestrator trusts the state, skips verification, and re-dispatches
# the same brief later. Set `min_result_bytes: 0` on a job whose deliverable is
# genuinely a one-word verdict.
CLAUDE_MIN_RESULT_BYTES = 300

# A Claude worker that finishes its work but never emits a final message parks at
# state "working" forever: tempo goes "idle", inFlight empties, and the CLI stops
# updating its state file. That is distinct from "blocked" (which waits on operator
# input). The broker reaps such a worker after this much quiescence so `await` and
# `reply` are not blocked by a session that has stopped working. 0 disables.
CLAUDE_IDLE_GRACE_SECONDS = 300.0

# How long an unmapped CLI state is treated as a transition rather than an outcome.
# Observed: a worker reported "SIGTERM (143); respawning", was recorded terminal, then
# respawned and committed its work -- but the broker never looked again, so a
# successful worker was reported as unknown.
UNRECOGNIZED_STATE_GRACE_SECONDS = 60.0


def _parse_cli_timestamp(value: Any) -> float | None:
    """Read the Claude CLI's ISO-8601 `updatedAt` as an epoch float."""

    if not isinstance(value, str) or not value:
        return None
    try:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


# Appended to the brief when a worker's cwd is a git checkout. A write-capable agent
# otherwise creates its own nested worktree and commits there, so the orchestrator finds
# nothing on the branch it assigned.
WORKSPACE_NOTE = (
    "Workspace: you are already running in the dedicated working directory the "
    "orchestrator assigned to you, and you are its only writer. Do not call "
    "EnterWorktree and do not create another git worktree. Work, commit, and verify "
    "directly in your current working directory, so the orchestrator finds your work "
    "on the branch it assigned. Do not switch, rebase, or reset that branch."
)

# Prepended to the brief when a worker's user-level config cannot be excluded via
# --setting-sources (see ClaudeProvider._supports_setting_sources). Keeps a worker
# from burning its turn on session-start skill/hook ceremony before touching the brief.
CEREMONY_PREAMBLE = (
    "Skip session-start onboarding ceremony: do not invoke standing workflow skills "
    "(TDD rituals, worktree setup, brainstorming prompts, or similar) before starting. "
    "Execute the brief below directly with only the tools it requires, then stop.\n\n"
)


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self) -> None:
        self.binary = os.environ.get(
            "OVERMIND_V2_CLAUDE_BIN",
            os.environ.get(
                "OVERMIND_CLAUDE_BIN", os.environ.get("CLAUDE_BIN", "claude")
            ),
        )
        self._setting_sources_supported: bool | None = None

    def _env(self) -> dict[str, str]:
        env = subscription_env("claude")
        env["CLAUDE_BIN"] = self.binary
        return env

    @staticmethod
    def _is_quiescent(value: dict[str, Any]) -> bool:
        """True when the CLI reports no turn in progress and nothing queued."""

        if str(value.get("tempo") or "").lower() != "idle":
            return False
        in_flight = value.get("inFlight")
        if isinstance(in_flight, dict):
            for key in ("tasks", "queued"):
                try:
                    if int(in_flight.get(key) or 0) > 0:
                        return False
                except (TypeError, ValueError):
                    return False
        return True

    def _idle_grace_seconds(self, job: dict[str, Any]) -> float:
        for candidate in (
            self._job_option(job, "idle_grace_seconds"),
            os.environ.get("OVERMIND_V2_CLAUDE_IDLE_GRACE_SECONDS"),
            CLAUDE_IDLE_GRACE_SECONDS,
        ):
            if candidate is None or candidate == "":
                continue
            try:
                return max(0.0, float(candidate))
            except (TypeError, ValueError):
                continue
        return CLAUDE_IDLE_GRACE_SECONDS

    def _hard_timeout_seconds(self, job: dict[str, Any]) -> float:
        """Ceiling on a worker the CLI still reports as busy.

        A worker can sit at inFlight tasks > 0 indefinitely: the CLI's counter is not
        always decremented when the underlying process exits, so a finished worker can
        look permanently busy and the quiescence reaper never touches it. This used to
        be opt-in on the theory that a long tool call is indistinguishable from a hang,
        and the result was that no job ever set it and wedged workers were never ended.
        A default that bounds silence at an hour is strictly better than no bound: it
        cannot fire while a worker is emitting anything at all.
        """

        for candidate in (
            self._job_option(job, "idle_hard_timeout_seconds"),
            os.environ.get("OVERMIND_V2_CLAUDE_HARD_TIMEOUT_SECONDS"),
            CLAUDE_HARD_TIMEOUT_SECONDS,
        ):
            if candidate is None or candidate == "":
                continue
            try:
                return max(0.0, float(candidate))
            except (TypeError, ValueError):
                continue
        return CLAUDE_HARD_TIMEOUT_SECONDS

    def _min_result_bytes(self, job: dict[str, Any]) -> int:
        for candidate in (
            self._job_option(job, "min_result_bytes"),
            os.environ.get("OVERMIND_V2_CLAUDE_MIN_RESULT_BYTES"),
            CLAUDE_MIN_RESULT_BYTES,
        ):
            if candidate is None or candidate == "":
                continue
            try:
                return max(0, int(float(candidate)))
            except (TypeError, ValueError):
                continue
        return CLAUDE_MIN_RESULT_BYTES

    @staticmethod
    def _mcp_arguments(job: dict[str, Any]) -> list[str]:
        """Isolate a worker from the operator's MCP configuration.

        A background worker has no TTY, so a server it has not already approved
        stops the turn dead: three measured jobs "succeeded" in under five seconds
        with a 64-byte artifact reading "approve 1 new project MCP server (grafana)
        -- attach to respond". `--strict-mcp-config` makes the worker use only the
        servers named on its own command line, so a project `.mcp.json` the operator
        happens to have cannot introduce a prompt nothing can answer. A job that
        genuinely needs a server passes `mcp_config` and gets exactly that server.
        """

        if ClaudeProvider._job_option(job, "strict_mcp_config") is False:
            return []
        configs = ClaudeProvider._job_option(job, "mcp_config")
        if configs is None:
            selected: list[str] = []
        elif isinstance(configs, (list, tuple)):
            selected = [str(item) for item in configs if str(item)]
        else:
            selected = [str(configs)]
        arguments = ["--strict-mcp-config"]
        for config in selected:
            arguments += ["--mcp-config", config]
        return arguments

    def _stop_quietly(self, provider_id: str) -> None:
        """Best-effort release of a session the broker has decided is finished."""

        try:
            subprocess.run(
                [self.binary, "stop", str(provider_id)],
                text=True,
                capture_output=True,
                env=self._env(),
                check=False,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    @staticmethod
    def _brief_with_workspace_note(job: dict[str, Any], brief: str) -> str:
        """Pin a worker to the directory the orchestrator gave it.

        A write-capable background agent otherwise isolates itself into a nested
        `.claude/worktrees/<name>` checkout on its own branch. The orchestrator already
        assigns one worktree per writer, so that nesting is redundant and silently
        strands the result on a branch nobody is watching.
        """

        if ClaudeProvider._job_option(job, "workspace_note") is False:
            return brief
        if not (Path(str(job.get("cwd") or ".")) / ".git").exists():
            return brief
        return f"{brief}\n\n---\n\n{WORKSPACE_NOTE}\n"

    @staticmethod
    def _settings_arguments(job: dict[str, Any]) -> list[str]:
        """Turn off the CLI's background worktree-isolation guard.

        The guard refuses a background session's first Write with "This background
        session hasn't isolated its changes yet. Call EnterWorktree first", which is
        precisely what a broker-launched worker must not do: the orchestrator has
        already assigned it a directory and is watching that branch. bypassPermissions
        does not cover it, because it is a workspace policy rather than a permission
        prompt. Measured cost of leaving it on: a worker finished an 85,000-token
        audit and then sat blocked for 39 hours on "Approve Write for
        experiments/... , or use EnterWorktree, or take summary as-is".

        Left on for a job that passed `workspace_note: false`, which is how a caller
        says the worker is responsible for isolating itself.
        """

        if ClaudeProvider._job_option(job, "workspace_note") is False:
            return []
        return ["--settings", json.dumps({"worktree": {"bgIsolation": "none"}})]

    @staticmethod
    def _job_option(job: dict[str, Any], key: str) -> Any:
        payload = job.get("provider_payload")
        if isinstance(payload, dict) and payload.get(key) is not None:
            return payload[key]
        return None

    @staticmethod
    def _transcript_path(value: dict[str, Any]) -> Path | None:
        """Locate the worker's own session transcript.

        The CLI records it as `linkScanPath`; the session id plus the projects
        directory is the fallback for a state file written before that field
        existed.
        """

        link = value.get("linkScanPath")
        if isinstance(link, str) and link and Path(link).is_file():
            return Path(link)
        session = value.get("sessionId") or value.get("resumeSessionId")
        if not session:
            return None
        root = (
            Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
            / "projects"
        )
        matches = sorted(root.glob(f"*/{session}.jsonl"))
        return matches[-1] if matches else None

    @staticmethod
    def _settled_for(value: dict[str, Any]) -> float:
        """Seconds since the CLI first reported this session terminal."""

        for key in ("firstTerminalAt", "updatedAt"):
            observed = _parse_cli_timestamp(value.get(key))
            if observed is not None:
                return max(0.0, time.time() - observed)
        return float("inf")

    @staticmethod
    def _transcript_settled(value: dict[str, Any]) -> bool:
        """True when the transcript can be trusted to hold the worker's last word."""

        path = ClaudeProvider._transcript_path(value)
        if path is None:
            return True
        settled_for = ClaudeProvider._settled_for(value)
        if settled_for >= CLAUDE_FINALIZE_CEILING_SECONDS:
            return True
        try:
            quiet_for = time.time() - path.stat().st_mtime
        except OSError:
            return True
        return (
            quiet_for >= CLAUDE_TRANSCRIPT_QUIET_SECONDS
            and settled_for >= CLAUDE_TRANSCRIPT_QUIET_SECONDS
        )

    @staticmethod
    def _final_assistant_message(value: dict[str, Any]) -> str | None:
        """The worker's actual last word, not the CLI's headline for it.

        `output.result` is a one-line summary the CLI asks the worker to write for
        its job list -- "hello.txt contains OK" -- and recording that as the result
        artifact is why broker-launched Claude jobs returned a 138-byte median while
        Codex returned 1,929. The final assistant message in the session transcript
        is the report the brief actually asked for, and it is what the Codex adapter
        already captures (`messages[-1]`). Sidechain records are subagent turns, not
        the worker's own conclusion, so they are skipped.
        """

        path = ClaudeProvider._transcript_path(value)
        if path is None:
            return None
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        latest: str | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or record.get("isSidechain"):
                continue
            message = record.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                text = "\n\n".join(
                    str(block.get("text", "")).strip()
                    for block in content
                    if isinstance(block, dict)
                    and block.get("type") == "text"
                    and str(block.get("text", "")).strip()
                ).strip()
            else:
                text = ""
            if text:
                latest = text
        return latest

    def _supports_setting_sources(self) -> bool:
        if self._setting_sources_supported is None:
            try:
                completed = subprocess.run(
                    [self.binary, "--help"],
                    text=True,
                    capture_output=True,
                    env=self._env(),
                    check=False,
                    timeout=10,
                )
                self._setting_sources_supported = (
                    "--setting-sources" in completed.stdout
                )
            except (OSError, subprocess.TimeoutExpired):
                self._setting_sources_supported = False
        return self._setting_sources_supported

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
        permission_mode = str(
            self._job_option(job, "permission_mode") or DEFAULT_CLAUDE_PERMISSION_MODE
        )
        isolate_config = self._job_option(job, "isolate_worker_config")
        isolate_config = True if isolate_config is None else bool(isolate_config)
        effective_brief = brief
        command = [self.binary]
        if resume_thread:
            command += ["--resume", resume_thread]
        if isolate_config:
            if self._supports_setting_sources():
                command += ["--setting-sources", "project,local"]
            else:
                effective_brief = CEREMONY_PREAMBLE + brief
        command += [
            "--bg",
            *(["--model", str(job["model"])] if job.get("model") else []),
            "--permission-mode",
            permission_mode,
            # bypassPermissions is only selectable when the CLI has been told the
            # operator accepts it; without this flag the requested mode can be
            # refused and the worker falls back to prompting, which a background
            # session has no way to answer.
            *(
                ["--allow-dangerously-skip-permissions"]
                if permission_mode == "bypassPermissions"
                else []
            ),
            *self._mcp_arguments(job),
            *self._settings_arguments(job),
            "--name",
            f"overmind-{job['short_id']}",
            "--",
            self._brief_with_workspace_note(job, effective_brief),
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
            # "blocked" means the CLI's turn has genuinely ended and it is waiting
            # synchronously for operator input (e.g. a permission denial or a
            # clarifying question); it never resolves on its own. Treat it as a
            # completed turn so the broker surfaces the final assistant message
            # instead of polling forever. See references/setup.md.
            "blocked": "succeeded",
            "failed": "failed",
            "error": "failed",
            "stopped": "interrupted",
            "cancelled": "interrupted",
            "canceled": "interrupted",
            "killed": "interrupted",
        }
        observed = _parse_cli_timestamp(value.get("updatedAt"))
        if observed is None:
            observed = state_path.stat().st_mtime
        idle_for = time.time() - observed
        created_at = float(job.get("created_at") or 0)
        job_age = time.time() - created_at if created_at else float("inf")
        if raw_state in mapping:
            state = mapping[raw_state]
        elif raw_state in CLAUDE_RUNNING_STATES:
            state = "running"
        elif (
            idle_for < UNRECOGNIZED_STATE_GRACE_SECONDS
            or job_age < UNRECOGNIZED_STATE_GRACE_SECONDS
        ):
            # An unrecognized state is usually a transition, not an outcome. A worker
            # that takes a SIGTERM reports something unmapped and then respawns and
            # finishes normally; calling that terminal locks in a wrong verdict,
            # because a terminal job is never reconciled again and its real result is
            # discarded. Wait for the CLI to stop moving before judging it.
            #
            # The job's own age is part of that test because staleness alone misread
            # eleven measured launches: each was declared terminal `unknown` with
            # detail "SIGTERM (143); respawning" between five and twelve seconds after
            # launch, while the state file carried an `updatedAt` older than the grace
            # (the CLI hands a background session a pre-spawned host process, whose
            # recorded timestamp predates this job). One of those workers was still
            # running an hour later, doing exactly what it was asked. A job younger
            # than the grace has not had time to produce an outcome, whatever its
            # state file claims.
            state = "running"
        else:
            state = "unknown"
        reaped_after: float | None = None
        reaped_reason = ""
        if state == "running":
            grace = self._idle_grace_seconds(job)
            hard_timeout = self._hard_timeout_seconds(job)
            if grace > 0 and idle_for >= grace and self._is_quiescent(value):
                # The turn ended without a final message. Left alone this job never
                # reaches a terminal state, so `await` hangs and `reply` refuses to
                # continue it.
                reaped_after = idle_for
                reaped_reason = "stopped working"
            elif hard_timeout > 0 and idle_for >= hard_timeout:
                # Ceiling for a worker the CLI still reports as busy. inFlight can
                # stay non-zero after the underlying process has exited, so the
                # quiescence test above never fires for a wedged worker.
                reaped_after = idle_for
                reaped_reason = "made no progress"
            if reaped_after is not None:
                state = "unknown"
                provider_id = value.get(
                    "daemonShort", value.get("id", job.get("provider_job_id"))
                )
                if provider_id:
                    self._stop_quietly(str(provider_id))
        update: dict[str, Any] = {
            "state": state,
            "raw_state": raw_state,
            "provider_job_id": value.get(
                "daemonShort", value.get("id", job.get("provider_job_id"))
            ),
            "provider_thread_id": value.get("sessionId", value.get("resumeSessionId")),
            "artifacts": [{"kind": "provider-state", "path": str(state_path)}],
        }
        result: Any = None
        reported_work = False
        if state in TERMINAL_STATES:
            # Preference order is deliberate: the worker's own final message, then
            # the CLI's one-line headline for it, then whatever note the CLI has.
            # Only the first is a work product; the rest are descriptions of one.
            if reaped_after is None and not self._transcript_settled(value):
                # The state file goes terminal before the last assistant record is
                # flushed. Finalizing inside that window records an earlier message,
                # or the CLI's headline, as the worker's report. Look again next poll.
                update["state"] = "running"
                return update
            final_message = self._final_assistant_message(value)
            output = value.get("output")
            if final_message:
                result = final_message
                reported_work = True
            elif isinstance(output, dict) and output.get("result") is not None:
                result = output["result"]
            elif raw_state == "blocked":
                # The turn ended waiting on the operator and produced no report.
                # Keep what it is waiting on so the parent has something to judge.
                result = value.get("needs") or value.get("detail")
            elif reaped_after is not None:
                result = value.get("detail")
        if state in TERMINAL_STATES and result is not None:
            result_path = Path(job["brief_path"]).parent / "result.md"
            body = result if isinstance(result, str) else json.dumps(result)
            write_private(result_path, body)
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
        if reaped_after is not None:
            update["error"] = (
                f"worker {reaped_reason} for {reaped_after:.0f}s without reporting a "
                "final message; the broker ended the session. Its outcome is "
                "unverified: check the artifacts it was asked to produce."
            )
            return update
        thin = self._thin_result_error(job, state, result, reported_work)
        if thin:
            update["state"] = "unknown"
            update["error"] = thin
            return update
        detail = value.get("detail", value.get("error"))
        if detail and state in {"failed", "unknown", "interrupted"}:
            update["error"] = str(detail)
        return update

    def _thin_result_error(
        self,
        job: dict[str, Any],
        state: str,
        result: Any,
        reported_work: bool,
    ) -> str | None:
        """Refuse to call a job that reported nothing a success.

        A `succeeded` job is never reconciled again and the orchestrator is told to
        trust it, so a success with no work product is the most expensive possible
        misreport: the brief looks done, nothing verifies it, and the same work is
        dispatched again later. `unknown` says what is actually true -- the work may
        be finished, but nothing reported it, so check the artifacts.
        """

        if state != "succeeded":
            return None
        minimum = self._min_result_bytes(job)
        if minimum <= 0:
            return None
        body = "" if result is None else (
            result if isinstance(result, str) else json.dumps(result)
        )
        size = len(body.strip().encode("utf-8"))
        if size >= minimum:
            return None
        source = (
            "its own final message"
            if reported_work
            else "a CLI progress note, not a report from the worker"
        )
        return (
            f"worker ended with a {size}-byte result ({source}), below the "
            f"{minimum}-byte minimum for a reported work product; the outcome is "
            "unverified. Check the artifacts the brief asked for, then treat this "
            "job as done or continue it with reply. Set min_result_bytes: 0 for a "
            "job whose deliverable really is this short."
        )

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


def _codex_event_failure_message(event: dict[str, Any]) -> str | None:
    """Extract a human-readable failure message from a codex exec JSON event.

    Covers the two shapes Codex CLI emits for a failed turn: a bare
    ``{"type": "error", "message": "..."}`` event and the authoritative
    ``{"type": "turn.failed", "error": {"message": "..."}}`` event.
    """
    event_type = event.get("type")
    if event_type == "turn.failed":
        detail = event.get("error")
        if isinstance(detail, dict):
            message = detail.get("message")
            return str(message) if message else (str(detail) if detail else None)
        return str(detail) if detail else None
    if event_type == "error":
        message = event.get("message")
        return str(message) if message else None
    return None


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
    turn_error: str | None = None
    for line in event_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id") or thread_id
        item = event.get("item") or {}
        if event_type == "item.completed" and item.get("type") == "agent_message":
            messages.append(str(item.get("text", "")))
        if event_type == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
        message = _codex_event_failure_message(event)
        if message:
            # `turn.failed` is authoritative; a preceding bare `error` event is
            # kept only as a fallback in case a run never emits `turn.failed`.
            if event_type == "turn.failed" or turn_error is None:
                turn_error = message
    write_private(
        result_path, messages[-1] if messages else (turn_error or "")
    )
    current = parse_json(state_path.read_text(encoding="utf-8"))
    if interrupted:
        terminal = "interrupted"
    else:
        terminal = "succeeded" if return_code == 0 else "failed"
    if return_code == 0:
        error: str | None = None
    else:
        stderr_text = error_path.read_text(encoding="utf-8").strip()
        # Codex CLI reports turn failures (e.g. quota exhaustion) through the
        # JSON event stream, not stderr, so prefer that message; stderr is a
        # fallback for launch/transport failures that never reach a turn.
        error = (
            turn_error
            or stderr_text
            or f"codex exec exited {return_code} with no captured detail"
        )[-4000:]
    current.update(
        state=terminal,
        provider_job_id=thread_id,
        provider_thread_id=thread_id,
        result_path=str(result_path),
        log_path=str(event_path),
        error_path=str(error_path),
        usage=usage,
        error=error,
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
