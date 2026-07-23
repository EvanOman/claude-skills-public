#!/usr/bin/env python3
"""Deterministic fake `codex` CLI for exercising the `_codex_runner` adapter.

Mode is selected via ``OVERMIND_V2_TEST_CODEX_MODE`` (default ``succeed``):

- ``succeed``: emits a normal agent_message turn and exits 0.
- ``fail``: emits a bare ``error`` event followed by the authoritative
  ``turn.failed`` event (mirroring the real Codex CLI shape captured from a
  quota-exhaustion failure) and exits 1. The message text is taken from
  ``OVERMIND_V2_TEST_CODEX_MESSAGE`` when set.
"""

from __future__ import annotations

import json
import os
import sys
import uuid


def main() -> int:
    arguments = sys.argv[1:]
    if arguments[:2] == ["login", "status"]:
        print("Logged in using ChatGPT")
        return 0
    if not arguments or arguments[0] != "exec":
        print("unsupported fake Codex command", file=sys.stderr)
        return 2
    prompt = sys.stdin.read()
    thread_id = str(uuid.uuid5(uuid.NAMESPACE_URL, prompt or "empty"))
    print(json.dumps({"type": "thread.started", "thread_id": thread_id}))
    print(json.dumps({"type": "turn.started"}))
    mode = os.environ.get("OVERMIND_V2_TEST_CODEX_MODE", "succeed")
    if mode == "fail":
        message = os.environ.get(
            "OVERMIND_V2_TEST_CODEX_MESSAGE",
            "You've hit your usage limit. try again at Jul 29th, 2026 1:19 AM.",
        )
        print(json.dumps({"type": "error", "message": message}))
        print(json.dumps({"type": "turn.failed", "error": {"message": message}}))
        return 1
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "ok:" + prompt},
            }
        )
    )
    print(
        json.dumps(
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
