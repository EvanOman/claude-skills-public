"""Implementation-independent Overmind v2 acceptance harness.

The CLI profile is ``scripts/om COMMAND --json [--input -]``. The MCP profile is the
``scripts/overmind-v2-mcp`` stdio server. ``OVERMIND_V2_FAKE_PROVIDER`` injects the sibling
``fake_provider.py`` executable, which reads one JSON request from stdin for each provider action
(``capabilities``, ``launch``, ``reconcile``, ``continue``, ``interrupt``, or ``usage``) and writes
one JSON response. No Overmind v2 production module is imported here.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import math
import os
import select
import signal
import sqlite3
import stat
import subprocess
import tempfile
import time
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


REPO = Path(__file__).resolve().parents[3]
SKILL = REPO / "skills" / "overmind-v2"
TESTS = SKILL / "tests"
DEFAULT_CLI = SKILL / "scripts" / "om"
DEFAULT_MCP = SKILL / "scripts" / "overmind-v2-mcp"
V1_MCP = REPO / "skills" / "overmind" / "bin" / "overmind-mcp"
CANONICAL = {
    "run",
    "run-many",
    "jobs",
    "show",
    "await",
    "collect",
    "reply",
    "stop",
    "forget",
    "doctor",
}
V1_MCP_NAMES = {
    "spawn",
    "list",
    "status",
    "wait",
    "result",
    "followup",
    "interrupt",
    "cleanup",
    "capabilities",
}
ALIASES = {
    "spawn": "run",
    "list": "jobs",
    "status": "show",
    "wait": "await",
    "result": "collect",
    "followup": "reply",
    "interrupt": "stop",
    "cleanup": "forget",
}
TERMINAL = {"succeeded", "failed", "interrupted", "unknown"}


class ContractFailure(AssertionError):
    pass


def entrypoint(name: str) -> Path:
    variable = "OVERMIND_V2_CLI" if name == "cli" else "OVERMIND_V2_MCP"
    default = DEFAULT_CLI if name == "cli" else DEFAULT_MCP
    return Path(os.environ.get(variable, default)).resolve()


def require_entrypoints() -> tuple[Path, Path]:
    cli, mcp = entrypoint("cli"), entrypoint("mcp")
    missing = [str(path) for path in (cli, mcp) if not path.is_file()]
    if missing:
        raise unittest.SkipTest("Overmind v2 production entrypoint missing: " + ", ".join(missing))
    return cli, mcp


def unwrap(value: Any) -> Any:
    while isinstance(value, dict):
        candidate = next(
            (value[key] for key in ("data", "result", "structuredContent") if key in value),
            None,
        )
        if candidate is None or candidate is value:
            return value
        value = candidate
    return value


def recursive_values(value: Any, names: set[str]) -> Iterator[Any]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names:
                yield item
            yield from recursive_values(item, names)
    elif isinstance(value, list):
        for item in value:
            yield from recursive_values(item, names)


def first_value(value: Any, *names: str) -> Any:
    return next(recursive_values(value, set(names)), None)


def ids_from(value: Any, kind: str) -> list[str]:
    keys = {"group_id", "groupId"} if kind == "group" else {"job_id", "jobId"}
    found: list[str] = []
    for item in recursive_values(value, keys):
        if isinstance(item, str) and item not in found:
            found.append(item)
    return found


def cursor_from(value: Any) -> int:
    cursor = first_value(value, "cursor", "event_cursor", "eventCursor", "last_cursor")
    if not isinstance(cursor, int):
        raise ContractFailure(f"response has no integer event cursor: {value!r}")
    return cursor


def state_from(value: Any) -> str | None:
    state = first_value(value, "state", "status")
    return state if isinstance(state, str) else None


def assert_uuid_text(value: str, label: str = "ID") -> None:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ContractFailure(f"{label} is not a canonical UUID: {value!r}") from error
    if str(parsed) != value.lower():
        raise ContractFailure(f"{label} is not canonical lowercase UUID text: {value!r}")


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return math.inf
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: float

    def json(self) -> dict[str, Any]:
        text = self.stdout.strip()
        try:
            value = json.loads(text)
        except ValueError as error:
            raise ContractFailure(
                f"command did not return one JSON object: {self.command!r}\n"
                f"stdout={self.stdout!r}\nstderr={self.stderr!r}"
            ) from error
        if not isinstance(value, dict):
            raise ContractFailure(f"JSON response must be an object, got {type(value).__name__}")
        return value


class Harness:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="overmind-v2-blackbox.")
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.provider_state = self.root / "provider"
        self.calls = self.root / "provider-calls.jsonl"
        self.cli = entrypoint("cli")
        self.mcp = entrypoint("mcp")
        self.env = dict(os.environ)
        self.env.update(
            OVERMIND_V2_STATE_DIR=str(self.state),
            OVERMIND_V2_FAKE_PROVIDER=str(TESTS / "fake_provider.py"),
            OVERMIND_V2_FAKE_STATE_DIR=str(self.provider_state),
            OVERMIND_V2_FAKE_CALL_LOG=str(self.calls),
            PYTHONUNBUFFERED="1",
        )
    def close(self) -> None:
        with contextlib.suppress(Exception):
            doctor = self.call("doctor", timeout=2, check=False).json()
            pid = first_value(doctor, "pid", "daemon_pid", "daemonPid")
            if isinstance(pid, int):
                self.terminate_test_daemon(pid)
        # A regression under test can make the broker too busy to answer
        # doctor. Only processes carrying this harness's exact state marker
        # are eligible for the teardown fallback.
        marker = f"OVERMIND_V2_STATE_DIR={self.state}".encode()
        for process in Path("/proc").glob("[0-9]*"):
            try:
                environment = (process / "environ").read_bytes().split(b"\0")
                pid = int(process.name)
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
                continue
            if marker not in environment:
                continue
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 6
            while process.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            if process.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
                deadline = time.monotonic() + 2
                while process.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
        self.temporary.cleanup()

    def call(
        self,
        command: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 15,
        check: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> CommandResult:
        argv = [str(self.cli), command, "--json"]
        input_text = None
        if payload is not None:
            argv += ["--input", "-"]
            input_text = json.dumps(payload)
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        started = time.perf_counter()
        completed = subprocess.run(
            argv,
            input=input_text,
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout,
            check=False,
        )
        result = CommandResult(
            argv,
            completed.returncode,
            completed.stdout,
            completed.stderr,
            (time.perf_counter() - started) * 1000,
        )
        if check and result.returncode != 0:
            raise ContractFailure(
                f"command failed ({result.returncode}): {argv!r}\n"
                f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
            )
        return result

    def start_call(self, command: str, payload: dict[str, Any]) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [str(self.cli), command, "--json", "--input", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env,
        )

    def provider_calls(self, action: str | None = None) -> list[dict[str, Any]]:
        if not self.calls.exists():
            return []
        calls = [json.loads(line) for line in self.calls.read_text().splitlines() if line]
        return [item for item in calls if action is None or item.get("action") == action]

    def terminate_test_daemon(self, pid: int) -> None:
        proc = Path(f"/proc/{pid}")
        if not proc.exists():
            return
        if proc.stat().st_uid != os.getuid():
            raise ContractFailure(f"refusing to signal daemon owned by another uid: {pid}")
        environ = (proc / "environ").read_bytes().split(b"\0")
        marker = f"OVERMIND_V2_STATE_DIR={self.state}".encode()
        if marker not in environ:
            raise ContractFailure(f"refusing to signal unverified process {pid}")
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 8
        while proc.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        if proc.exists():
            raise ContractFailure(f"test daemon did not stop after SIGTERM: {pid}")

    def job_spec(
        self,
        label: str,
        *,
        mode: str = "success",
        delay: float = 0,
        result: str | None = None,
        billing_class: str = "subscription-native",
    ) -> dict[str, Any]:
        fake: dict[str, Any] = {"mode": mode, "delay": delay}
        if result is not None:
            fake["result"] = result
        return {
            "provider": "fake",
            "label": label,
            "cwd": str(self.root),
            "brief": f"deterministic fake task {label}",
            "billing_class": billing_class,
            "fake": fake,
        }

    def run_many(
        self,
        specs: list[dict[str, Any]],
        *,
        key: str,
        label: str = "test-group",
        **extra: Any,
    ) -> dict[str, Any]:
        payload = {"group": {"label": label}, "jobs": specs, "idempotency_key": key}
        payload.update(extra)
        return self.call("run-many", payload).json()


class IntegrationCase(unittest.TestCase):
    harness: Harness

    @classmethod
    def setUpClass(cls) -> None:
        require_entrypoints()

    def setUp(self) -> None:
        self.harness = Harness()

    def tearDown(self) -> None:
        self.harness.close()


class McpClient:
    def __init__(self, command: Path, env: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            [str(command)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        assert self.process.stdin and self.process.stdout
        self.stdin = self.process.stdin
        self.stdout = self.process.stdout
        self.next_id = 1
        self.notifications: list[dict[str, Any]] = []
        self._tool_names: dict[str, str] = {}
        self.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
        self.notify("notifications/initialized", {})

    def send(self, message: dict[str, Any]) -> None:
        self.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = 10,
    ) -> dict[str, Any]:
        request_id = self.begin_request(method, params)
        return self.wait_for_response(request_id, timeout=timeout, method=method)

    def begin_request(self, method: str, params: dict[str, Any]) -> int:
        request_id = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return request_id

    def read_message(self, *, timeout: float = 10) -> dict[str, Any]:
        ready, _, _ = select.select([self.stdout], [], [], timeout)
        if ready:
            line = self.stdout.readline()
            if line:
                return json.loads(line)
        stderr = ""
        if self.process.poll() is not None and self.process.stderr:
            stderr = self.process.stderr.read()
        raise ContractFailure(f"MCP message timed out; stderr={stderr!r}")

    def wait_for_response(
        self,
        request_id: int,
        *,
        timeout: float = 10,
        method: str = "request",
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.read_message(timeout=min(0.25, deadline - time.monotonic()))
            except ContractFailure:
                if self.process.poll() is None:
                    continue
                raise
            if message.get("id") == request_id:
                return message
            self.notifications.append(message)
        stderr = ""
        if self.process.poll() is not None and self.process.stderr:
            stderr = self.process.stderr.read()
        raise ContractFailure(f"MCP request timed out: {method}; stderr={stderr!r}")

    def tools(self) -> list[dict[str, Any]]:
        response = self.request("tools/list", {})
        return response["result"]["tools"]

    def tool_name(self, canonical: str) -> str:
        cached = self._tool_names.get(canonical)
        if cached is not None:
            return cached
        matches = [tool["name"] for tool in self.tools() if canonical_tool(tool["name"]) == canonical]
        if len(matches) != 1:
            raise ContractFailure(f"expected one canonical {canonical!r} tool, got {matches!r}")
        self._tool_names[canonical] = matches[0]
        return matches[0]

    def call_tool(
        self,
        canonical: str,
        arguments: dict[str, Any],
        *,
        progress_token: str | None = None,
        timeout: float = 15,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"name": self.tool_name(canonical), "arguments": arguments}
        if progress_token:
            params["_meta"] = {"progressToken": progress_token}
        return self.request("tools/call", params, timeout=timeout)

    def close(self) -> None:
        if not self.stdin.closed:
            self.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=2)
        self.stdout.close()
        if self.process.stderr:
            self.process.stderr.close()


def canonical_tool(name: str) -> str:
    normalized = name.replace("overmind_v2_", "", 1).replace("overmind_", "", 1)
    return normalized.replace("_", "-")


def concurrent_calls(function: Any, count: int) -> list[Any]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as executor:
        return list(executor.map(lambda _: function(), range(count)))


def database_user_version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def private_mode(path: Path) -> bool:
    return stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
