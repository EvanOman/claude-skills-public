#!/usr/bin/env python3
"""Deterministic one-shot provider used only by the Overmind v2 black-box suite."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(os.environ.get("OVERMIND_V2_FAKE_STATE_DIR", "/nonexistent"))
CALLS = Path(os.environ.get("OVERMIND_V2_FAKE_CALL_LOG", ROOT / "calls.jsonl"))
STATE = ROOT / "state.json"
LOCK = ROOT / ".lock"


def read_request() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except ValueError as error:
        raise SystemExit(f"fake-provider: invalid JSON request: {error}")
    if not isinstance(value, dict):
        raise SystemExit("fake-provider: request must be a JSON object")
    return value


@contextlib.contextmanager
def locked() -> Any:
    ROOT.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def load() -> dict[str, Any]:
    if not STATE.exists():
        return {"jobs": {}}
    return json.loads(STATE.read_text())


def save(value: dict[str, Any]) -> None:
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True))
    os.chmod(temporary, 0o600)
    os.replace(temporary, STATE)


def log(action: str, request: dict[str, Any], response: dict[str, Any]) -> None:
    CALLS.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(CALLS, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(json.dumps({"action": action, "request": request, "response": response}) + "\n")


def fake_options(request: dict[str, Any]) -> dict[str, Any]:
    options = request.get("fake")
    if isinstance(options, dict):
        return options
    job = request.get("job")
    if isinstance(job, dict) and isinstance(job.get("fake"), dict):
        return job["fake"]
    return {}


def snapshot(record: dict[str, Any]) -> dict[str, Any]:
    current = dict(record)
    if current["state"] == "running" and current.get("ready_at", 0) <= time.time():
        mode = current.get("mode", "success")
        current["state"] = {
            "success": "succeeded",
            "fail": "failed",
            "unknown": "unknown",
            "hold": "running",
            "stale-process": "running",
            "fallback-metered": "succeeded",
        }.get(mode, "succeeded")
    return current


def launch(request: dict[str, Any]) -> dict[str, Any]:
    options = fake_options(request)
    mode = str(options.get("mode", "success"))
    if mode == "launch-error":
        raise SystemExit("fake-provider: requested launch failure")
    provider_job_id = str(uuid.uuid4())
    delay = float(options.get("delay", 0))
    result = str(options.get("result", f"fake-result:{provider_job_id}"))
    artifact_dir = ROOT / "artifacts" / provider_job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    result_path = artifact_dir / "result.txt"
    log_path = artifact_dir / "events.jsonl"
    result_path.write_text(result)
    log_path.write_text(json.dumps({"event": "launched", "provider_job_id": provider_job_id}) + "\n")
    os.chmod(result_path, 0o600)
    os.chmod(log_path, 0o600)
    requested_billing = request.get("billing_class") or request.get("job", {}).get(
        "billing_class", "subscription-native"
    )
    actual_billing = "explicit-metered" if mode == "fallback-metered" else requested_billing
    record = {
        "provider_job_id": provider_job_id,
        "state": "running",
        "mode": mode,
        "ready_at": time.time() + delay,
        "billing_class": actual_billing,
        "result_path": str(result_path),
        "log_path": str(log_path),
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }
    record = snapshot(record)
    with locked():
        state = load()
        state["jobs"][provider_job_id] = record
        save(state)
    return record


def find_id(request: dict[str, Any]) -> str:
    value = request.get("provider_job_id") or request.get("providerJobId")
    if not isinstance(value, str):
        job = request.get("job", {})
        value = job.get("provider_job_id") or job.get("providerJobId")
    if not isinstance(value, str):
        raise SystemExit("fake-provider: provider_job_id is required")
    return value


def reconcile(request: dict[str, Any]) -> dict[str, Any]:
    provider_job_id = find_id(request)
    with locked():
        state = load()
        record = state["jobs"][provider_job_id]
        updated = snapshot(record)
        state["jobs"][provider_job_id] = updated
        save(state)
    return updated


def interrupt(request: dict[str, Any]) -> dict[str, Any]:
    provider_job_id = find_id(request)
    with locked():
        state = load()
        record = state["jobs"][provider_job_id]
        if record.get("mode") == "stale-process":
            return {
                **record,
                "state": "unknown",
                "signal_sent": False,
                "detail": "process identity mismatch",
            }
        record["state"] = "interrupted"
        record["signal_sent"] = True
        save(state)
        return record


def continuation(request: dict[str, Any]) -> dict[str, Any]:
    parent = find_id(request)
    response = launch(request)
    response["parent_provider_job_id"] = parent
    return response


def dispatch(action: str, request: dict[str, Any]) -> dict[str, Any]:
    if action in {"capabilities", "doctor"}:
        return {
            "provider": "fake",
            "available": True,
            "billing_classes": ["subscription-native", "explicit-metered"],
            "continue": True,
            "steer": False,
            "interrupt": True,
            "usage": True,
        }
    if action == "launch":
        return launch(request)
    if action in {"reconcile", "status", "show"}:
        return reconcile(request)
    if action in {"continue", "reply", "steer"}:
        return continuation(request)
    if action in {"interrupt", "stop"}:
        return interrupt(request)
    if action == "usage":
        return reconcile(request).get("usage", {})
    raise SystemExit(f"fake-provider: unsupported action: {action}")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: fake_provider.py <action>")
    action = sys.argv[1]
    request = read_request()
    response = dispatch(action, request)
    log(action, request, response)
    print(json.dumps(response, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
