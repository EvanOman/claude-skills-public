"""Which orchestrator session owns which worker, and whether it is still alive.

The Claude CLI does not tell an MCP server which session invoked it, and a worker's
own state file carries no parent reference, so ownership has to be established from
the outside. Two halves:

- A session registers itself (id, pid, start identity) while it is alive. The status
  line does this on every render because it is the one in-session hook that is handed
  the session id.
- The MCP server, which is a child of the session process, walks its own process
  ancestry and matches a registered pid. That is how a job learns its owner without
  the caller having to remember to pass one.

Liveness is pid-plus-start-identity, never pid alone: pids are recycled, and claiming
a stranger's process is the one mistake this module must not make.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .providers import process_start_identity, write_private

MAX_ANCESTRY_DEPTH = 12


def sessions_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "sessions"


def register(state_dir: Path, session_id: str, pid: int, cwd: str = "") -> None:
    """Record a live orchestrator session. Safe to call repeatedly."""

    if not session_id or pid <= 0:
        return
    directory = sessions_dir(state_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private(
        directory / f"{session_id}.json",
        json.dumps(
            {
                "session_id": session_id,
                "pid": int(pid),
                "identity": process_start_identity(int(pid)),
                "cwd": cwd,
            }
        ),
    )


def read_all(state_dir: Path) -> list[dict[str, Any]]:
    directory = sessions_dir(state_dir)
    records: list[dict[str, Any]] = []
    try:
        entries = sorted(directory.glob("*.json"))
    except OSError:
        return records
    for entry in entries:
        try:
            value = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("session_id"):
            records.append(value)
    return records


def is_live(record: dict[str, Any]) -> bool:
    """True when the registered process is still the process that registered."""

    try:
        pid = int(record.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    identity = record.get("identity")
    current = process_start_identity(pid)
    if current is None:
        return False
    if identity is None:
        # Registered before an identity could be read; pid liveness is all we have.
        return True
    return str(current) == str(identity)


def forget(state_dir: Path, session_id: str) -> None:
    try:
        (sessions_dir(state_dir) / f"{session_id}.json").unlink()
    except OSError:
        pass


def _parent_pid(pid: int) -> int | None:
    try:
        fields = (
            Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        )
        return int(fields[1])
    except (FileNotFoundError, PermissionError, IndexError, ValueError, OSError):
        return None


def ancestry(pid: int) -> list[int]:
    """This process and its forebears, nearest first."""

    chain: list[int] = []
    current: int | None = int(pid)
    while current and current > 1 and len(chain) < MAX_ANCESTRY_DEPTH:
        chain.append(current)
        current = _parent_pid(current)
    return chain


def owning_session(state_dir: Path, pid: int | None = None) -> str | None:
    """Identify the registered session that this process is running under."""

    explicit = os.environ.get("OVERMIND_V2_OWNER_SESSION")
    if explicit:
        return explicit
    live = {
        int(record["pid"]): str(record["session_id"])
        for record in read_all(state_dir)
        if is_live(record) and record.get("pid")
    }
    if not live:
        return None
    for candidate in ancestry(os.getpid() if pid is None else int(pid)):
        session = live.get(candidate)
        if session:
            return session
    return None


# Process names that look like an agent harness. Used only to anchor an
# anonymous session identity; claiming the wrong ancestor costs nothing worse
# than a private scope named after that process.
HARNESS_COMMS = frozenset({"claude", "codex", "node", "bun"})


def _process_comm(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def caller_session(state_dir: Path) -> str | None:
    """The session this process runs under, registering an anonymous one if needed.

    Session isolation must not depend on the operator having installed the
    status-line registration hook. When no registered session matches, anchor an
    identity on the nearest harness-looking ancestor process and register it —
    every client spawned by the same session then resolves the same identity,
    because the registry match walks the same ancestry. A process with no
    harness ancestor (a bare terminal) gets no identity and sees only unowned
    jobs unless it asks for a wider scope by name.
    """

    owner = owning_session(state_dir)
    if owner:
        return owner
    for pid in ancestry(os.getpid())[1:]:
        comm = _process_comm(pid)
        if comm and comm.lower() in HARNESS_COMMS:
            identity = process_start_identity(pid)
            if not identity:
                return None
            session_id = f"proc-{pid}-{identity}"
            try:
                register(state_dir, session_id, pid)
            except OSError:
                pass
            return session_id
    return None
