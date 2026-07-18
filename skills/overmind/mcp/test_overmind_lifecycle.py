#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("overmind_lifecycle.py")
SPEC = importlib.util.spec_from_file_location("overmind_lifecycle", MODULE_PATH)
assert SPEC and SPEC.loader
overmind = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(overmind)


FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, pathlib, sys, uuid
assert "ANTHROPIC_API_KEY" not in os.environ
root = pathlib.Path(os.environ["FAKE_CLAUDE_DIR"])
root.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]
verb = args[0]
if verb == "run":
    provider_id = uuid.uuid4().hex[:8]
    (root / f"{provider_id}.json").write_text(json.dumps({
        "state": "running", "session": "session-" + provider_id,
        "result": args[-1], "cwd": args[args.index("-C") + 1]
    }))
    print("JOB=" + provider_id)
elif verb == "cont":
    old = json.loads((root / f"{args[-2]}.json").read_text())
    provider_id = uuid.uuid4().hex[:8]
    (root / f"{provider_id}.json").write_text(json.dumps({
        "state": "running", "session": old["session"],
        "result": "continued:" + args[-1], "cwd": old["cwd"]
    }))
    print("JOB=" + provider_id)
elif verb == "status":
    item = json.loads((root / f"{args[1]}.json").read_text())
    print("ID=" + args[1]); print("STATE=" + item["state"])
    print("SESSION=" + item["session"]); print("CWD=" + item["cwd"])
elif verb == "last":
    print(json.loads((root / f"{args[1]}.json").read_text())["result"])
elif verb == "stop":
    path = root / f"{args[1]}.json"; item = json.loads(path.read_text())
    item["state"] = "stopped"; path.write_text(json.dumps(item))
elif verb == "rm":
    (root / f"{args[1]}.json").unlink()
else:
    raise SystemExit(2)
"""


FAKE_CODEX = r"""#!/usr/bin/env python3
import json, os, sys, time
assert "OPENAI_API_KEY" not in os.environ
args = sys.argv[1:]
prompt = sys.stdin.read()
if prompt == "SLOW":
    time.sleep(20)
thread = "thread-resumed" if "resume" in args else "thread-new"
print(json.dumps({"type": "thread.started", "thread_id": thread}), flush=True)
print(json.dumps({"type": "item.completed", "item": {
    "type": "agent_message", "text": "codex:" + prompt
}}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
"""


class LifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="overmind-mcp-test.")
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.claude = self.bin / "fake-claude-worker"
        self.codex = self.bin / "fake-codex"
        self.claude.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.codex.write_text(FAKE_CODEX, encoding="utf-8")
        self.claude.chmod(0o755)
        self.codex.chmod(0o755)
        self.old_env = dict(os.environ)
        os.environ.update(
            OVERMIND_STATE_DIR=str(self.root / "state"),
            OVERMIND_CLAUDE_WORKER=str(self.claude),
            OVERMIND_CODEX_BIN=str(self.codex),
            FAKE_CLAUDE_DIR=str(self.root / "claude"),
            ANTHROPIC_API_KEY="must-be-removed",
            OPENAI_API_KEY="must-be-removed",
        )
        self.registry = overmind.Registry()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
        self.temporary.cleanup()

    def finish_claude(self, job: dict[str, object]) -> None:
        path = self.root / "claude" / f"{job['provider_job_id']}.json"
        item = json.loads(path.read_text(encoding="utf-8"))
        item["state"] = "done"
        path.write_text(json.dumps(item), encoding="utf-8")

    def test_claude_full_lifecycle_and_subscription_sanitization(self) -> None:
        job = self.registry.spawn("claude", "brief", str(self.root), "worker", "sonnet")
        self.assertEqual("running", job["state"])
        self.finish_claude(job)
        self.assertEqual("succeeded", self.registry.wait(job["job_id"], 2)["state"])
        self.assertIn("brief", self.registry.result(job["job_id"])["result"])

        child = self.registry.followup(job["job_id"], "fix it")
        self.assertEqual(job["job_id"], child["parent_job_id"])
        self.finish_claude(child)
        self.assertIn(
            "continued:fix it", self.registry.result(child["job_id"])["result"]
        )
        cleaned = self.registry.cleanup(child["job_id"], delete_provider_state=True)
        self.assertTrue(cleaned["cleaned"])
        self.assertTrue(cleaned["provider_state_deleted"])

    def test_claude_interrupt(self) -> None:
        job = self.registry.spawn("claude", "brief", str(self.root), "worker", "haiku")
        stopped = self.registry.interrupt(job["job_id"])
        self.assertEqual("interrupted", stopped["state"])

    def test_codex_full_lifecycle_followup_and_event_retention(self) -> None:
        job = self.registry.spawn("codex", "brief", str(self.root), "worker")
        done = self.registry.wait(job["job_id"], 5)
        self.assertEqual("succeeded", done["state"])
        self.assertEqual("thread-new", done["provider_thread_id"])
        self.assertEqual("codex:brief", self.registry.result(job["job_id"])["result"])
        self.assertTrue(
            (self.registry.job_dir(job["job_id"]) / "events.jsonl").exists()
        )
        self.assertEqual(0o700, self.registry.root.stat().st_mode & 0o777)
        self.assertEqual(
            0o700, self.registry.job_dir(job["job_id"]).stat().st_mode & 0o777
        )
        for filename in (
            "job.json",
            "prompt.txt",
            "events.jsonl",
            "stderr.log",
            "result.md",
        ):
            self.assertEqual(
                0o600,
                (self.registry.job_dir(job["job_id"]) / filename).stat().st_mode
                & 0o777,
                filename,
            )

        child = self.registry.followup(job["job_id"], "more")
        child_done = self.registry.wait(child["job_id"], 5)
        self.assertEqual("succeeded", child_done["state"])
        self.assertEqual("codex:more", self.registry.result(child["job_id"])["result"])

    def test_codex_interrupt(self) -> None:
        job = self.registry.spawn("codex", "SLOW", str(self.root), "worker")
        time.sleep(0.2)
        stopped = self.registry.interrupt(job["job_id"])
        self.assertEqual("interrupted", stopped["state"])
        time.sleep(0.1)
        self.assertEqual("interrupted", self.registry.status(job["job_id"])["state"])

    def test_codex_fast_runner_cannot_be_overwritten_to_running(self) -> None:
        for number in range(10):
            job = self.registry.spawn(
                "codex", f"fast-{number}", str(self.root), "worker"
            )
            self.assertEqual("succeeded", self.registry.wait(job["job_id"], 5)["state"])

    def test_codex_cleanup_does_not_claim_provider_deletion(self) -> None:
        job = self.registry.spawn("codex", "brief", str(self.root), "worker")
        self.registry.wait(job["job_id"], 5)
        cleaned = self.registry.cleanup(job["job_id"], delete_provider_state=True)
        self.assertFalse(cleaned["provider_state_deleted"])

    def test_mcp_handshake_and_tools(self) -> None:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "overmind_capabilities", "arguments": {}},
            },
        ]
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH)],
            input="".join(json.dumps(item) + "\n" for item in requests),
            text=True,
            capture_output=True,
            env=os.environ,
            check=True,
            timeout=5,
        )
        replies = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([1, 2, 3], [item["id"] for item in replies])
        names = {tool["name"] for tool in replies[1]["result"]["tools"]}
        self.assertIn("overmind_spawn", names)
        self.assertIn("overmind_followup", names)
        self.assertTrue(
            replies[2]["result"]["structuredContent"]["claude"]["continuation"]
        )

    def test_malformed_json_does_not_reuse_previous_request_id(self) -> None:
        payload = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 41, "method": "ping"}),
                "{not-json",
                json.dumps({"jsonrpc": "2.0", "id": 42, "method": "ping"}),
                "",
            ]
        )
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH)],
            input=payload,
            text=True,
            capture_output=True,
            env=os.environ,
            check=True,
            timeout=5,
        )
        replies = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([41, 42], [reply["id"] for reply in replies])

    def test_dispatch_list_structured_content_is_object(self) -> None:
        value = overmind.dispatch(self.registry, "overmind_list", {})
        self.assertIsInstance(value, dict)
        self.assertIn("jobs", value)


if __name__ == "__main__":
    unittest.main()
