from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from overmind_v2.providers import ClaudeProvider  # noqa: E402


class ClaudeProviderReconcileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory(prefix="overmind-v2-claude-state.")
        self.addCleanup(self._tempdir.cleanup)

    def reconcile(self, state: str, **fields: object) -> dict[str, object]:
        root = self._tempdir.name
        job_dir = Path(root) / f"job-{uuid.uuid4().hex[:8]}"
        job_dir.mkdir()
        state_path = job_dir / "state.json"
        state_path.write_text(json.dumps({"state": state, **fields}), encoding="utf-8")
        job = {
            "provider_job_id": "deadbeef",
            "provider_state_path": str(state_path),
            "brief_path": str(job_dir / "brief.md"),
        }
        return ClaudeProvider().reconcile(job)

    def test_succeeded_detail_is_not_reported_as_an_error(self) -> None:
        update = self.reconcile(
            "completed",
            detail="returned CLAUDE_V2_LIVE_OK",
            output={"result": "CLAUDE_V2_LIVE_OK"},
        )

        self.assertEqual("succeeded", update["state"])
        self.assertNotIn("error", update)

    def test_unsuccessful_states_preserve_provider_detail_or_error(self) -> None:
        cases = (
            ("failed", "detail", "worker failed", "failed"),
            ("cancelled", "detail", "stopped by caller", "interrupted"),
            ("mystery", "error", "unrecognized provider state", "unknown"),
        )
        for raw_state, field, message, expected_state in cases:
            with self.subTest(state=raw_state, field=field):
                update = self.reconcile(raw_state, **{field: message})
                self.assertEqual(expected_state, update["state"])
                self.assertEqual(message, update["error"])

    def test_blocked_turn_is_reported_terminal_with_final_message_as_result(self) -> None:
        # A worker denied a tool call (or genuinely needing guidance) ends its
        # turn and the CLI parks it in "blocked" state indefinitely -- it never
        # self-transitions to "done". The broker must treat this as a completed
        # turn (not a still-running job) so `show --fresh` and `reply` work, and
        # must capture the CLI's own summary as the result artifact since no
        # structured output is produced in this state.
        update = self.reconcile(
            "blocked",
            detail="Let me know which you'd prefer, or just run one and I'll pick back up.",
            needs="Let me know which you'd prefer, or just run one and I'll pick back up.",
            output=None,
        )

        self.assertEqual("succeeded", update["state"])
        self.assertNotIn("error", update)
        self.assertIn("result_path", update)
        self.assertEqual(
            "Let me know which you'd prefer, or just run one and I'll pick back up.",
            Path(update["result_path"]).read_text(encoding="utf-8"),
        )
        kinds = {artifact["kind"] for artifact in update["artifacts"]}
        self.assertIn("result", kinds)

    def test_blocked_turn_without_needs_falls_back_to_detail(self) -> None:
        update = self.reconcile("blocked", detail="waiting on operator input", output=None)

        self.assertEqual("succeeded", update["state"])
        self.assertEqual(
            "waiting on operator input",
            Path(update["result_path"]).read_text(encoding="utf-8"),
        )


class ClaudeProviderLaunchOptionsTest(unittest.TestCase):
    """Verifies the exact command line built for a launch, using a fake claude binary."""

    def build_command(self, provider_payload: dict[str, object] | None = None, **job_fields: object):
        with tempfile.TemporaryDirectory(prefix="overmind-v2-claude-launch.") as root:
            root_path = Path(root)
            fake_bin = root_path / "claude"
            capture = root_path / "captured-argv.json"
            fake_bin.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "if len(sys.argv) > 1 and sys.argv[1] == '--help':\n"
                "    print('... --setting-sources <sources> ...')\n"
                "    sys.exit(0)\n"
                f"open({str(capture)!r}, 'w').write(json.dumps(sys.argv))\n"
                "print('job id: deadbeef')\n",
                encoding="utf-8",
            )
            fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IEXEC)
            job_dir = root_path / "job"
            job_dir.mkdir()
            brief_path = job_dir / "brief.txt"
            brief_path.write_text("do the thing", encoding="utf-8")

            previous = os.environ.get("OVERMIND_V2_CLAUDE_BIN")
            os.environ["OVERMIND_V2_CLAUDE_BIN"] = str(fake_bin)
            try:
                provider = ClaudeProvider()
                job = {
                    "short_id": "deadbeef",
                    "cwd": str(root_path),
                    "brief_path": str(brief_path),
                    "capabilities": {
                        "available": True,
                        "billing_class": "subscription-native",
                    },
                    "provider_payload": provider_payload or {},
                    **job_fields,
                }
                provider.launch(job, "do the thing")
            finally:
                if previous is None:
                    os.environ.pop("OVERMIND_V2_CLAUDE_BIN", None)
                else:
                    os.environ["OVERMIND_V2_CLAUDE_BIN"] = previous
            return json.loads(capture.read_text(encoding="utf-8"))

    def test_default_launch_uses_bypass_permissions_and_isolates_config(self) -> None:
        argv = self.build_command()

        self.assertIn("--permission-mode", argv)
        self.assertEqual(
            "bypassPermissions", argv[argv.index("--permission-mode") + 1]
        )
        self.assertNotIn("dontAsk", argv)
        self.assertIn("--setting-sources", argv)
        self.assertEqual("project,local", argv[argv.index("--setting-sources") + 1])

    def test_permission_mode_option_overrides_the_default(self) -> None:
        argv = self.build_command(provider_payload={"permission_mode": "acceptEdits"})

        self.assertEqual(
            "acceptEdits", argv[argv.index("--permission-mode") + 1]
        )

    def test_isolate_worker_config_false_skips_setting_sources(self) -> None:
        argv = self.build_command(
            provider_payload={"isolate_worker_config": False}
        )

        self.assertNotIn("--setting-sources", argv)


if __name__ == "__main__":
    unittest.main()
