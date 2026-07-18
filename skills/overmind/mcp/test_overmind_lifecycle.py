#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("overmind_lifecycle.py")
SPEC = importlib.util.spec_from_file_location("overmind_lifecycle", MODULE_PATH)
assert SPEC and SPEC.loader
overmind = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(overmind)


FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, pathlib, sys, time, uuid
assert "ANTHROPIC_API_KEY" not in os.environ
assert "CLAUDE_CODE_USE_BEDROCK" not in os.environ
assert "CLAUDE_CODE_USE_VERTEX" not in os.environ
assert "CLAUDE_CODE_USE_FOUNDRY" not in os.environ
assert os.environ.get("CLAUDE_BIN") == os.environ.get("EXPECTED_CLAUDE_BIN")
root = pathlib.Path(os.environ["FAKE_CLAUDE_DIR"])
root.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]
if os.environ.get("FAKE_CLAUDE_ARGS_LOG"):
    with open(os.environ["FAKE_CLAUDE_ARGS_LOG"], "a") as stream:
        stream.write(json.dumps(args) + "\n")
if args[:3] == ["auth", "status", "--json"]:
    status = os.environ.get("FAKE_CLAUDE_AUTH_STATUS")
    if status:
        print(status)
    else:
        print(json.dumps({
            "loggedIn": True, "authMethod": "claude.ai",
            "apiProvider": "firstParty", "subscriptionType": "max"
        }))
    raise SystemExit(int(os.environ.get("FAKE_CLAUDE_AUTH_EXIT", "0")))
verb = args[0]
if verb == "run":
    time.sleep(float(os.environ.get("FAKE_CLAUDE_RUN_DELAY", "0")))
    if os.environ.get("FAKE_CLAUDE_RUN_MODE") == "fail":
        print("launch failed", file=sys.stderr); raise SystemExit(9)
    if os.environ.get("FAKE_CLAUDE_RUN_MODE") == "missing":
        print("launch returned without an id"); raise SystemExit(0)
    provider_id = uuid.uuid4().hex[:8]
    (root / f"{provider_id}.json").write_text(json.dumps({
        "state": "running", "session": "session-" + provider_id,
        "result": args[-1], "cwd": args[args.index("-C") + 1]
    }))
    print("JOB=" + provider_id)
elif verb == "cont":
    time.sleep(float(os.environ.get("FAKE_CLAUDE_CONT_DELAY", "0")))
    if os.environ.get("FAKE_CLAUDE_CONT_MODE") == "fail":
        print("continuation failed", file=sys.stderr); raise SystemExit(9)
    if os.environ.get("FAKE_CLAUDE_CONT_MODE") == "missing":
        print("continuation returned without an id"); raise SystemExit(0)
    old = json.loads((root / f"{args[-2]}.json").read_text())
    provider_id = uuid.uuid4().hex[:8]
    (root / f"{provider_id}.json").write_text(json.dumps({
        "state": "running", "session": old["session"],
        "result": "continued:" + args[-1], "cwd": old["cwd"]
    }))
    print("JOB=" + provider_id)
elif verb == "status":
    path = root / f"{args[1]}.json"; item = json.loads(path.read_text())
    if item.get("status_failures", 0):
        item["status_failures"] -= 1; path.write_text(json.dumps(item))
        print("temporary daemon error", file=sys.stderr); raise SystemExit(7)
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
if os.environ.get("FAKE_CODEX_ARGS_LOG"):
    with open(os.environ["FAKE_CODEX_ARGS_LOG"], "a") as stream:
        stream.write(json.dumps(args) + "\n")
if args[:2] == ["login", "status"]:
    print(os.environ.get("FAKE_CODEX_LOGIN_STATUS", "Logged in using ChatGPT"))
    raise SystemExit(int(os.environ.get("FAKE_CODEX_LOGIN_EXIT", "0")))
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
            OVERMIND_CLAUDE_BIN=str(self.claude),
            CLAUDE_BIN="/wrong/claude-must-not-reach-wrapper",
            OVERMIND_CODEX_BIN=str(self.codex),
            FAKE_CLAUDE_DIR=str(self.root / "claude"),
            EXPECTED_CLAUDE_BIN=str(self.claude),
            FAKE_CLAUDE_ARGS_LOG=str(self.root / "claude-args.jsonl"),
            ANTHROPIC_API_KEY="must-be-removed",
            CLAUDE_CODE_USE_BEDROCK="must-be-removed",
            CLAUDE_CODE_USE_VERTEX="must-be-removed",
            CLAUDE_CODE_USE_FOUNDRY="must-be-removed",
            OPENAI_API_KEY="must-be-removed",
            FAKE_CODEX_ARGS_LOG=str(self.root / "codex-args.jsonl"),
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
        invocations = [
            json.loads(line)
            for line in (self.root / "claude-args.jsonl").read_text().splitlines()
        ]
        self.assertIn(["auth", "status", "--json"], invocations)
        self.assertTrue(any(args and args[0] == "run" for args in invocations))

    def test_claude_interrupt(self) -> None:
        job = self.registry.spawn("claude", "brief", str(self.root), "worker", "haiku")
        stopped = self.registry.interrupt(job["job_id"])
        self.assertEqual("interrupted", stopped["state"])

    def test_claude_rejects_non_subscription_login(self) -> None:
        for status in (
            '{"loggedIn":true,"authMethod":"apiKey","subscriptionType":null}',
            '{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"bedrock","subscriptionType":"max"}',
            "not json",
        ):
            os.environ["FAKE_CLAUDE_AUTH_STATUS"] = status
            with self.assertRaisesRegex(
                overmind.LifecycleError, "subscription preflight"
            ):
                self.registry.spawn(
                    "claude", "brief", str(self.root), "worker", "haiku"
                )
        os.environ.pop("FAKE_CLAUDE_AUTH_STATUS")

    def test_claude_bin_fallback_is_verified_and_forced_into_wrapper(self) -> None:
        os.environ.pop("OVERMIND_CLAUDE_BIN")
        os.environ["CLAUDE_BIN"] = str(self.claude)
        registry = overmind.Registry()
        job = registry.spawn("claude", "brief", str(self.root), "worker", "haiku")
        self.assertEqual("running", job["state"])
        registry.interrupt(job["job_id"])

    def test_interrupted_claude_launch_failure_or_missing_id_is_terminal(self) -> None:
        for mode in ("fail", "missing"):
            job = self.registry.new_job("claude", str(self.root), f"launch-{mode}")
            with self.registry.job_lock(job["job_id"]):
                current = self.registry._load_unlocked(job["job_id"])
                current["state"] = "interrupting"
                self.registry._save_unlocked(current)
            os.environ["FAKE_CLAUDE_RUN_MODE"] = mode
            with self.assertRaises(overmind.LifecycleError):
                self.registry._spawn_claude(job, "brief", "haiku")
            self.assertEqual(
                "interrupted", self.registry.status(job["job_id"])["state"]
            )
        os.environ.pop("FAKE_CLAUDE_RUN_MODE")

    def test_interrupted_claude_continuation_failure_or_missing_id_is_terminal(
        self,
    ) -> None:
        parent = self.registry.spawn(
            "claude", "brief", str(self.root), "parent", "haiku"
        )
        self.finish_claude(parent)
        self.assertEqual("succeeded", self.registry.status(parent["job_id"])["state"])
        for mode in ("fail", "missing"):
            before = {
                path.parent.name for path in self.registry.jobs.glob("*/job.json")
            }
            os.environ.update(FAKE_CLAUDE_CONT_DELAY="0.3", FAKE_CLAUDE_CONT_MODE=mode)
            result: list[dict[str, object]] = []

            def continue_worker() -> None:
                result.append(self.registry.followup(parent["job_id"], "continue"))

            thread = threading.Thread(target=continue_worker)
            thread.start()
            child_id = ""
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not child_id:
                children = [
                    path.parent.name
                    for path in self.registry.jobs.glob("*/job.json")
                    if path.parent.name not in before
                ]
                child_id = children[0] if children else ""
                time.sleep(0.02)
            self.assertTrue(child_id)
            self.registry.interrupt(child_id)
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual("interrupted", self.registry.status(child_id)["state"])
        os.environ.pop("FAKE_CLAUDE_CONT_DELAY")
        os.environ.pop("FAKE_CLAUDE_CONT_MODE")

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
        invocations = [
            json.loads(line)
            for line in (self.root / "codex-args.jsonl").read_text().splitlines()
        ]
        exec_calls = [args for args in invocations if args and args[0] == "exec"]
        self.assertTrue(exec_calls)
        for args in exec_calls:
            self.assertIn("--ignore-user-config", args)
            self.assertIn('model_provider="openai"', args)

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

    def test_codex_followup_validates_parent_before_creating_child(self) -> None:
        for invalid in (None, "bad thread id with spaces"):
            parent = self.registry.new_job("codex", str(self.root), "parent")
            with self.registry.job_lock(parent["job_id"]):
                current = self.registry._load_unlocked(parent["job_id"])
                current.update(state="succeeded", provider_thread_id=invalid)
                self.registry._save_unlocked(current)
            before = {
                path.parent.name for path in self.registry.jobs.glob("*/job.json")
            }
            with self.assertRaisesRegex(overmind.LifecycleError, "thread ID"):
                self.registry.followup(parent["job_id"], "more")
            after = {path.parent.name for path in self.registry.jobs.glob("*/job.json")}
            self.assertEqual(before, after)

    def test_outer_codex_runner_is_reaped_after_cross_registry_cleanup(self) -> None:
        job = self.registry.spawn("codex", "brief", str(self.root), "reap")
        with self.registry.runners_lock:
            runner = self.registry.runners[job["job_id"]]
        other = overmind.Registry(self.registry.root)
        self.assertEqual("succeeded", other.wait(job["job_id"], 5)["state"])
        other.cleanup(job["job_id"])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with self.registry.runners_lock:
                owned = job["job_id"] in self.registry.runners
            if runner.returncode is not None and not owned:
                break
            time.sleep(0.02)
        self.assertIsNotNone(runner.returncode)
        self.assertFalse(owned)

    def test_codex_rejects_api_or_unknown_login(self) -> None:
        for status in ("Logged in using an API key", "mystery auth"):
            os.environ["FAKE_CODEX_LOGIN_STATUS"] = status
            with self.assertRaisesRegex(
                overmind.LifecycleError, "subscription preflight"
            ):
                self.registry.spawn("codex", "brief", str(self.root), "worker")
        os.environ.pop("FAKE_CODEX_LOGIN_STATUS")

    def test_stale_codex_pid_is_never_signaled(self) -> None:
        job = self.registry.new_job("codex", str(self.root), "stale")
        with self.registry.job_lock(job["job_id"]):
            current = self.registry._load_unlocked(job["job_id"])
            current.update(
                state="running",
                runner_pid=os.getpid(),
                runner_start_identity="definitely-not-this-process",
            )
            self.registry._save_unlocked(current)
        with (
            mock.patch.object(os, "pidfd_open") as pidfd_open,
            mock.patch.object(overmind.signal, "pidfd_send_signal") as send_signal,
        ):
            result = self.registry.interrupt(job["job_id"])
        self.assertEqual("failed", result["state"])
        pidfd_open.assert_not_called()
        send_signal.assert_not_called()

    def test_interrupt_wins_before_codex_runner_launch(self) -> None:
        job = self.registry.new_job("codex", str(self.root), "race")
        errors: list[Exception] = []

        def launch() -> None:
            try:
                self.registry._spawn_codex(job, "brief")
            except Exception as error:  # pragma: no cover - assertion reports it
                errors.append(error)

        with mock.patch.object(subprocess, "Popen") as popen:
            with self.registry.job_lock(job["job_id"]):
                thread = threading.Thread(target=launch)
                thread.start()
                time.sleep(0.1)
                current = self.registry._load_unlocked(job["job_id"])
                current.update(state="interrupted", detail="test interrupt")
                self.registry._save_unlocked(current)
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        popen.assert_not_called()
        self.assertEqual("interrupted", self.registry.status(job["job_id"])["state"])

    def test_transient_claude_status_is_unknown_retryable_and_not_cleanable(
        self,
    ) -> None:
        job = self.registry.spawn("claude", "brief", str(self.root), "worker", "haiku")
        path = self.root / "claude" / f"{job['provider_job_id']}.json"
        item = json.loads(path.read_text())
        item["status_failures"] = 1
        path.write_text(json.dumps(item))
        self.assertEqual("unknown", self.registry.status(job["job_id"])["state"])
        with self.assertRaisesRegex(overmind.LifecycleError, "cleanup requires"):
            self.registry.cleanup(job["job_id"])
        self.assertEqual("running", self.registry.status(job["job_id"])["state"])
        self.registry.interrupt(job["job_id"])

    def test_many_mcp_waits_do_not_block_controls_and_are_cancellable(self) -> None:
        job = self.registry.spawn("claude", "brief", str(self.root), "worker", "haiku")
        server = subprocess.Popen(
            [sys.executable, str(MODULE_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ,
        )
        assert server.stdin and server.stdout

        def call(request_id: int, name: str, arguments: dict[str, object]) -> None:
            server.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    }
                )
                + "\n"
            )
            server.stdin.flush()

        wait_ids = set(range(200, 212))
        for request_id in wait_ids:
            call(
                request_id,
                "overmind_wait",
                {"job_id": job["job_id"], "timeout_seconds": 30},
            )
        time.sleep(0.1)
        call(300, "overmind_status", {"job_id": job["job_id"]})
        ready, _, _ = select.select([server.stdout], [], [], 1)
        self.assertTrue(ready, "status was starved behind twelve waits")
        first = json.loads(server.stdout.readline())
        self.assertEqual(300, first["id"])

        for request_id in wait_ids:
            server.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/cancelled",
                        "params": {"requestId": request_id, "reason": "test"},
                    }
                )
                + "\n"
            )
        server.stdin.flush()
        call(301, "overmind_interrupt", {"job_id": job["job_id"]})
        replies: dict[int, dict[str, object]] = {}
        deadline = time.monotonic() + 3
        expected = wait_ids | {301}
        while time.monotonic() < deadline and set(replies) != expected:
            ready, _, _ = select.select([server.stdout], [], [], 0.5)
            if ready:
                reply = json.loads(server.stdout.readline())
                replies[reply["id"]] = reply
        self.assertEqual(expected, set(replies))
        for request_id in wait_ids:
            self.assertEqual(-32800, replies[request_id]["error"]["code"])
        server.stdin.close()
        server.wait(timeout=3)
        server.stdout.close()
        assert server.stderr
        server.stderr.close()

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
