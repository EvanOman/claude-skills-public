#!/usr/bin/env python3
"""Subscription-looking deterministic Codex CLI fixture for the v1 control."""

from __future__ import annotations

import json
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
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "v1:" + prompt},
            }
        )
    )
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
