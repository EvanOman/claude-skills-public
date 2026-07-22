"""Thin newline-delimited JSON client for the Overmind v2 broker."""

from __future__ import annotations

import fcntl
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any


PROTOCOL_NAME = "overmind-v2"
MAX_FRAME_BYTES = 16 * 1024 * 1024
DEFAULT_START_TIMEOUT = 5.0
PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class OvermindError(RuntimeError):
    """A broker, transport, or configuration error safe to show to a caller."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "overmind_error",
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class RequestCancelled(OvermindError):
    """A local wait cancelled by its MCP or CLI caller."""

    def __init__(self, last_progress: dict[str, Any] | None = None) -> None:
        super().__init__(
            "request cancelled",
            code="request_cancelled",
            data={"last_progress": last_progress} if last_progress else None,
        )
        self.last_progress = last_progress


def default_state_dir() -> Path:
    configured = os.environ.get("OVERMIND_V2_STATE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".local/state/overmind-v2"


def _private_state_dir(path: Path) -> Path:
    path = path.expanduser().resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid():
        raise OvermindError(
            f"state directory is owned by another user: {path}",
            code="unsafe_state_dir",
        )
    if stat.S_IMODE(info.st_mode) != 0o700:
        path.chmod(0o700)
    return path


class DaemonClient:
    """One-request-per-connection client for the per-user broker."""

    def __init__(
        self,
        state_dir: str | os.PathLike[str] | None = None,
        *,
        autostart: bool = True,
        start_timeout: float | None = None,
    ) -> None:
        self.state_dir = _private_state_dir(
            Path(state_dir) if state_dir is not None else default_state_dir()
        )
        self.socket_path = self.state_dir / "overmind.sock"
        self.autostart = autostart
        configured_timeout = os.environ.get("OVERMIND_V2_START_TIMEOUT")
        self.start_timeout = (
            start_timeout
            if start_timeout is not None
            else float(configured_timeout or DEFAULT_START_TIMEOUT)
        )

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> Any:
        if not method or not isinstance(method, str):
            raise OvermindError("request method must be a non-empty string", code="invalid_request")
        if params is not None and not isinstance(params, dict):
            raise OvermindError("request params must be an object", code="invalid_request")

        connection = self._connect()
        request_id = str(uuid.uuid4())
        envelope = {
            "protocol": PROTOCOL_NAME,
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        encoded = json.dumps(envelope, separators=(",", ":")).encode("utf-8") + b"\n"
        latest_progress: dict[str, Any] | None = None
        try:
            connection.sendall(encoded)
            connection.settimeout(0.2)
            buffered = bytearray()
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise RequestCancelled(latest_progress)
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    raise OvermindError(
                        "broker closed the connection before replying",
                        code="broker_disconnected",
                    )
                buffered.extend(chunk)
                if len(buffered) > MAX_FRAME_BYTES:
                    raise OvermindError("broker response exceeded size limit", code="response_too_large")
                while b"\n" in buffered:
                    raw, _, remainder = buffered.partition(b"\n")
                    buffered = bytearray(remainder)
                    if not raw.strip():
                        continue
                    message = self._decode_message(raw)
                    if message.get("id") != request_id:
                        raise OvermindError(
                            "broker returned a mismatched request ID",
                            code="protocol_error",
                        )
                    if message.get("event") == "progress":
                        progress = message.get("progress")
                        if not isinstance(progress, dict):
                            raise OvermindError(
                                "broker progress payload must be an object",
                                code="protocol_error",
                            )
                        latest_progress = progress
                        if on_progress is not None:
                            on_progress(progress)
                        continue
                    if message.get("ok") is True:
                        return message.get("result")
                    if message.get("ok") is False:
                        self._raise_broker_error(message.get("error"))
                    raise OvermindError(
                        "broker response omitted a terminal ok field",
                        code="protocol_error",
                    )
        finally:
            connection.close()

    def _connect(self) -> socket.socket:
        try:
            return self._connect_once()
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError) as first_error:
            if not self.autostart:
                raise OvermindError(
                    f"Overmind v2 broker is unavailable at {self.socket_path}",
                    code="daemon_unavailable",
                ) from first_error
            self._start_daemon()
            deadline = time.monotonic() + self.start_timeout
            last_error: Exception = first_error
            while time.monotonic() < deadline:
                try:
                    return self._connect_once()
                except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError) as error:
                    last_error = error
                    time.sleep(0.05)
            raise OvermindError(
                f"Overmind v2 broker did not become ready within {self.start_timeout:g}s; "
                f"see {self.state_dir / 'daemon.log'}",
                code="daemon_start_failed",
            ) from last_error

    def _connect_once(self) -> socket.socket:
        if self.socket_path.exists():
            info = self.socket_path.stat()
            if info.st_uid != os.getuid() or not stat.S_ISSOCK(info.st_mode):
                raise OvermindError(
                    f"refusing unsafe broker socket: {self.socket_path}",
                    code="unsafe_socket",
                )
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise OvermindError(
                    f"broker socket is accessible to other users: {self.socket_path}",
                    code="unsafe_socket",
                )
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(0.5)
            connection.connect(str(self.socket_path))
            return connection
        except Exception:
            connection.close()
            raise

    def _start_daemon(self) -> None:
        lock_path = self.state_dir / "daemon-start.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "r+", encoding="utf-8", closefd=False) as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                try:
                    probe = self._connect_once()
                except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError):
                    probe = None
                if probe is not None:
                    probe.close()
                    return

                log_path = self.state_dir / "daemon.log"
                log_descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                env = dict(os.environ)
                env["OVERMIND_V2_STATE_DIR"] = str(self.state_dir)
                existing_pythonpath = env.get("PYTHONPATH")
                env["PYTHONPATH"] = str(PACKAGE_ROOT) + (
                    os.pathsep + existing_pythonpath if existing_pythonpath else ""
                )
                try:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "overmind_v2.daemon",
                            "--state-dir",
                            str(self.state_dir),
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=log_descriptor,
                        stderr=log_descriptor,
                        close_fds=True,
                        start_new_session=True,
                        env=env,
                    )
                finally:
                    os.close(log_descriptor)

                # Keep the inter-process start lock until this daemon accepts
                # connections. A second caller can then probe it instead of
                # racing to launch another background owner.
                deadline = time.monotonic() + self.start_timeout
                while time.monotonic() < deadline:
                    try:
                        probe = self._connect_once()
                    except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError):
                        probe = None
                    if probe is not None:
                        probe.close()
                        return
                    return_code = process.poll()
                    if return_code is not None:
                        raise OvermindError(
                            f"Overmind v2 broker exited during startup (status {return_code}); "
                            f"see {log_path}",
                            code="daemon_start_failed",
                        )
                    time.sleep(0.05)
                raise OvermindError(
                    f"Overmind v2 broker did not become ready within {self.start_timeout:g}s; "
                    f"see {log_path}",
                    code="daemon_start_failed",
                )
        finally:
            os.close(descriptor)

    @staticmethod
    def _decode_message(raw: bytes) -> dict[str, Any]:
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OvermindError("broker returned invalid JSON", code="protocol_error") from error
        if not isinstance(value, dict):
            raise OvermindError("broker response must be an object", code="protocol_error")
        return value

    @staticmethod
    def _raise_broker_error(error: Any) -> None:
        if isinstance(error, dict):
            message = str(error.get("message") or "broker request failed")
            code = str(error.get("code") or "broker_error")
            data = error.get("data")
        else:
            message = str(error or "broker request failed")
            code = "broker_error"
            data = None
        raise OvermindError(message, code=code, data=data)
