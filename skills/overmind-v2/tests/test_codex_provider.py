from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from overmind_v2.providers import (  # noqa: E402
    CodexProvider,
    _codex_event_failure_message,
    _codex_runner,
    atomic_json,
    process_start_identity,
)


FIXTURE = Path(__file__).resolve().parent / "fake_codex_cli.py"


class CodexRunnerErrorPropagationTest(unittest.TestCase):
    """`_codex_runner` must surface a `turn.failed` event's message, not stderr."""

    def _run(self, mode: str, message: str | None = None) -> tuple[dict, str]:
        with tempfile.TemporaryDirectory(prefix="overmind-v2-codex-runner.") as root:
            job_dir = Path(root)
            brief_path = job_dir / "brief.md"
            brief_path.write_text("do the thing", encoding="utf-8")
            state_path = job_dir / "codex-state.json"
            event_path = job_dir / "codex-events.jsonl"
            error_path = job_dir / "codex-stderr.log"
            result_path = job_dir / "result.md"
            runner_pid = os.getpid()
            identity = process_start_identity(runner_pid)
            atomic_json(
                state_path,
                {
                    "state": "starting",
                    "runner_pid": runner_pid,
                    "runner_start_identity": identity,
                },
            )
            env = dict(os.environ)
            env["OVERMIND_V2_TEST_CODEX_MODE"] = mode
            if message is not None:
                env["OVERMIND_V2_TEST_CODEX_MESSAGE"] = message
            previous = dict(os.environ)
            os.environ.clear()
            os.environ.update(env)
            try:
                arguments = argparse.Namespace(
                    codex_bin=str(FIXTURE),
                    cwd=str(job_dir),
                    brief_path=str(brief_path),
                    state_path=str(state_path),
                    event_path=str(event_path),
                    error_path=str(error_path),
                    result_path=str(result_path),
                    model=None,
                    resume=None,
                )
                return_code = _codex_runner(arguments)
            finally:
                os.environ.clear()
                os.environ.update(previous)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["_return_code"] = return_code
            result_text = result_path.read_text(encoding="utf-8")
            return state, result_text

    def test_turn_failure_message_propagates_to_job_error(self) -> None:
        message = (
            "You've hit your usage limit. Visit https://chatgpt.com/codex/settings"
            "/usage to purchase more credits or try again at Jul 29th, 2026 1:19 AM."
        )
        state, result_text = self._run("fail", message=message)

        self.assertEqual("failed", state["state"])
        self.assertNotEqual(0, state["_return_code"])
        self.assertEqual(message, state["error"])
        self.assertIn("usage limit", result_text)
        self.assertIn("Jul 29th, 2026", result_text)

    def test_success_leaves_error_empty_and_uses_agent_message(self) -> None:
        state, result_text = self._run("succeed")

        self.assertEqual("succeeded", state["state"])
        self.assertIsNone(state["error"])
        self.assertEqual("ok:do the thing", result_text)

    def test_empty_stderr_never_yields_an_empty_error_string(self) -> None:
        # Regression: a failed turn with a blank codex-stderr.log used to leave
        # job.error == "" (falsy but not None), hiding the failure from a parent.
        state, _ = self._run("fail", message="quota exhausted")

        self.assertNotEqual("", state["error"])
        self.assertTrue(state["error"])


class CodexEventFailureMessageTest(unittest.TestCase):
    def test_turn_failed_dict_error(self) -> None:
        event = {"type": "turn.failed", "error": {"message": "usage limit hit"}}
        self.assertEqual("usage limit hit", _codex_event_failure_message(event))

    def test_bare_error_event(self) -> None:
        event = {"type": "error", "message": "usage limit hit"}
        self.assertEqual("usage limit hit", _codex_event_failure_message(event))

    def test_unrelated_event_is_ignored(self) -> None:
        event = {"type": "turn.completed", "usage": {"input_tokens": 1}}
        self.assertIsNone(_codex_event_failure_message(event))


class CodexProviderReconcileTest(unittest.TestCase):
    def test_reconcile_forwards_terminal_error_and_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="overmind-v2-codex-state.") as root:
            job_dir = Path(root)
            state_path = job_dir / "codex-state.json"
            result_path = job_dir / "result.md"
            result_path.write_text("usage limit hit", encoding="utf-8")
            message = "You've hit your usage limit. try again at Jul 29th, 2026."
            atomic_json(
                state_path,
                {
                    "state": "failed",
                    "provider_job_id": "thread-1",
                    "provider_thread_id": "thread-1",
                    "result_path": str(result_path),
                    "error": message,
                },
            )
            job = {
                "provider_state_path": str(state_path),
                "brief_path": str(job_dir / "brief.md"),
            }
            update = CodexProvider().reconcile(job)

        self.assertEqual("failed", update["state"])
        self.assertEqual(message, update["error"])
        self.assertEqual(str(result_path), update["result_path"])
        kinds = {artifact["kind"] for artifact in update.get("artifacts", [])}
        self.assertIn("result", kinds)


if __name__ == "__main__":
    unittest.main()
