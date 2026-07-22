"""Concurrent Unix-socket daemon for the Overmind v2 broker."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import OvermindError, PROTOCOL
from .broker import Broker


MAX_REQUEST_BYTES = 1_048_576


def default_state_dir() -> Path:
    configured = os.environ.get("OVERMIND_V2_STATE_DIR")
    if configured:
        return Path(os.path.abspath(Path(configured).expanduser()))
    xdg = os.environ.get("XDG_STATE_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return Path(os.path.abspath(root / "overmind-v2"))


class Daemon:
    def __init__(self, state_dir: Path) -> None:
        if state_dir.is_symlink():
            raise OvermindError(f"refusing symlink state directory: {state_dir}")
        self.state_dir = state_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        info = state_dir.stat()
        if info.st_uid != os.getuid():
            raise OvermindError(
                f"state directory is owned by another user: {state_dir}"
            )
        if not state_dir.is_dir():
            raise OvermindError(f"state path is not a directory: {state_dir}")
        state_dir.chmod(0o700)
        self.socket_path = state_dir / "overmind.sock"
        self.log_path = state_dir / "daemon.log"
        self.lock_path = state_dir / "daemon.lock"
        self.stop_event = threading.Event()
        self.server: socket.socket | None = None
        self._threads: set[threading.Thread] = set()
        self._connections: set[socket.socket] = set()
        self._threads_lock = threading.Lock()
        self._lock_fd: int | None = None
        self._owns_socket = False
        self._closed = False
        self.broker: Broker | None = None

    def log(self, message: str) -> None:
        descriptor = os.open(
            self.log_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            stream.write(
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}\n"
            )

    def _acquire_singleton(self) -> None:
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise OvermindError(
                "another Overmind v2 daemon owns this state directory"
            ) from error
        self._lock_fd = descriptor

    def _prepare_socket(self) -> socket.socket:
        if self.socket_path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.2)
                probe.connect(str(self.socket_path))
            except OSError:
                self.socket_path.unlink()
            else:
                raise OvermindError(
                    f"active broker socket already exists: {self.socket_path}"
                )
            finally:
                probe.close()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        self._owns_socket = True
        self.socket_path.chmod(0o600)
        server.listen(64)
        server.settimeout(0.5)
        return server

    @staticmethod
    def _send(
        stream: Any,
        lock: threading.Lock,
        message: dict[str, Any],
    ) -> None:
        encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        with lock:
            stream.write(encoded + "\n")
            stream.flush()

    def _handle_client(self, connection: socket.socket) -> None:
        write_lock = threading.Lock()
        try:
            with (
                connection,
                connection.makefile("r", encoding="utf-8") as reader,
                connection.makefile("w", encoding="utf-8") as writer,
            ):
                while not self.stop_event.is_set():
                    line = reader.readline(MAX_REQUEST_BYTES + 1)
                    if not line:
                        return
                    request_id: Any = None
                    try:
                        if len(line.encode()) > MAX_REQUEST_BYTES:
                            raise OvermindError("request exceeds one MiB")
                        request = json.loads(line)
                        if not isinstance(request, dict):
                            raise OvermindError("request must be a JSON object")
                        request_id = request.get("id")
                        if request.get("protocol") != PROTOCOL:
                            raise OvermindError(
                                f"unsupported protocol: {request.get('protocol')!r}"
                            )
                        method = request.get("method")
                        if not isinstance(method, str):
                            raise OvermindError("request method must be a string")
                        params = request.get("params", {})
                        if not isinstance(params, dict):
                            raise OvermindError("request params must be an object")

                        def progress(value: dict[str, Any]) -> None:
                            self._send(
                                writer,
                                write_lock,
                                {
                                    "id": request_id,
                                    "event": "progress",
                                    "progress": value,
                                },
                            )

                        assert self.broker is not None
                        result = self.broker.dispatch(method, params, progress=progress)
                        self._send(
                            writer,
                            write_lock,
                            {"id": request_id, "ok": True, "result": result},
                        )
                    except Exception as error:
                        error_payload: dict[str, Any] = {
                            "type": type(error).__name__,
                            "message": str(error),
                        }
                        if isinstance(error, OvermindError):
                            error_payload["code"] = error.code
                            if error.data is not None:
                                error_payload["data"] = error.data
                        self._send(
                            writer,
                            write_lock,
                            {
                                "id": request_id,
                                "ok": False,
                                "error": error_payload,
                            },
                        )
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            with self._threads_lock:
                self._connections.discard(connection)
                self._threads.discard(threading.current_thread())

    def serve(self) -> int:
        self._acquire_singleton()
        self.broker = Broker(self.state_dir, recover=True)
        self.server = self._prepare_socket()
        self.log(f"started pid={os.getpid()} socket={self.socket_path}")
        while not self.stop_event.is_set():
            try:
                connection, _ = self.server.accept()
            except TimeoutError:
                continue
            except OSError:
                if self.stop_event.is_set():
                    break
                raise
            thread = threading.Thread(
                target=self._handle_client,
                args=(connection,),
                name="overmind-client",
                daemon=True,
            )
            with self._threads_lock:
                self._connections.add(connection)
                self._threads.add(thread)
            thread.start()
        self.close()
        return 0

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop_event.set()
        deadline = time.monotonic() + 2
        if self.server is not None:
            try:
                self.server.close()
            except OSError:
                pass
            self.server = None
        with self._threads_lock:
            connections = list(self._connections)
            threads = list(self._threads)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        if self.broker is not None:
            self.broker.close(timeout=max(0.0, deadline - time.monotonic()))
        for thread in threads:
            if thread is threading.current_thread():
                continue
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if self._owns_socket:
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
            self._owns_socket = False
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None
        self.log("stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    arguments = parser.parse_args()
    requested_state_dir = arguments.state_dir.expanduser()
    daemon: Daemon | None = None

    def stop(_number: int, _frame: Any) -> None:
        if daemon is None:
            return
        daemon.stop_event.set()
        if daemon.server is not None:
            try:
                daemon.server.close()
            except OSError:
                pass

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        daemon = Daemon(requested_state_dir)
        return daemon.serve()
    except OvermindError as error:
        print(f"overmind-v2 daemon: {error}", file=sys.stderr)
        return 1
    finally:
        if daemon is not None:
            daemon.close()


if __name__ == "__main__":
    raise SystemExit(main())
