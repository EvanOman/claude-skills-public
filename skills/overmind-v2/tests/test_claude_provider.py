from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
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


class ClaudeIdleReaperTest(unittest.TestCase):
    """A worker that finishes without a final message must not hang the group.

    The CLI parks such a session at state "working" with tempo "idle" and an empty
    inFlight, and stops touching its state file. Left alone the broker polls it
    forever, so `await` never satisfies and `reply` refuses to continue it.
    """

    def reconcile(
        self,
        *,
        age_seconds: float,
        grace: object = None,
        hard_timeout: object = None,
        **fields: object,
    ):
        with tempfile.TemporaryDirectory(prefix="overmind-v2-idle.") as root:
            job_dir = Path(root) / "job"
            job_dir.mkdir()
            state_path = job_dir / "state.json"
            observed = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
            payload = {
                "state": "working",
                "tempo": "idle",
                "inFlight": {"tasks": 0, "queued": 0},
                "detail": "committed OPTOUT.txt; running git status",
                "updatedAt": observed.isoformat().replace("+00:00", "Z"),
                **fields,
            }
            state_path.write_text(json.dumps(payload), encoding="utf-8")
            job: dict[str, object] = {
                "provider_job_id": "deadbeef",
                "provider_state_path": str(state_path),
                "brief_path": str(job_dir / "brief.md"),
                "provider_payload": {
                    **({} if grace is None else {"idle_grace_seconds": grace}),
                    **(
                        {}
                        if hard_timeout is None
                        else {"idle_hard_timeout_seconds": hard_timeout}
                    ),
                },
            }
            stopped: list[list[str]] = []
            provider = ClaudeProvider()
            provider._stop_quietly = lambda pid: stopped.append([pid])  # type: ignore[assignment]
            update = provider.reconcile(job)
            written = (
                Path(str(update["result_path"])).read_text(encoding="utf-8")
                if update.get("result_path")
                else None
            )
            return update, stopped, written

    def test_a_long_quiescent_worker_is_reaped_and_its_session_ended(self) -> None:
        update, stopped, _ = self.reconcile(age_seconds=900)

        self.assertEqual("unknown", update["state"])
        self.assertIn("without reporting a final message", update["error"])
        self.assertEqual([["deadbeef"]], stopped)

    def test_the_reaped_worker_keeps_its_progress_note_as_the_result(self) -> None:
        update, _, written = self.reconcile(age_seconds=900)

        self.assertIn("result_path", update)
        self.assertIsNotNone(written)
        self.assertIn("committed OPTOUT.txt", str(written))

    def test_a_recently_active_worker_is_left_running(self) -> None:
        update, stopped, _ = self.reconcile(age_seconds=5)

        self.assertEqual("running", update["state"])
        self.assertEqual([], stopped)

    def test_a_worker_with_a_turn_in_progress_is_never_reaped(self) -> None:
        update, stopped, _ = self.reconcile(
            age_seconds=9000, inFlight={"tasks": 1, "queued": 0}
        )

        self.assertEqual("running", update["state"])
        self.assertEqual([], stopped)

    def test_a_worker_that_is_not_idle_is_never_reaped(self) -> None:
        update, _, _ = self.reconcile(age_seconds=9000, tempo="thinking")

        self.assertEqual("running", update["state"])

    def test_reaping_can_be_disabled_per_job(self) -> None:
        update, stopped, _ = self.reconcile(age_seconds=9000, grace=0)

        self.assertEqual("running", update["state"])
        self.assertEqual([], stopped)

    def test_a_shorter_grace_reaps_sooner(self) -> None:
        update, _, _ = self.reconcile(age_seconds=120, grace=60)

        self.assertEqual("unknown", update["state"])

    def test_a_busy_worker_is_never_reaped_by_the_default_grace(self) -> None:
        """Measured: a parent waiting on a subagent reports tempo idle for minutes.

        Its inFlight carries the subagent and its shell, and updatedAt does not move
        while a tool call runs, so idleness plus staleness alone would kill a parent
        mid-flight. Only the in-flight guard prevents that.
        """

        update, stopped, _ = self.reconcile(
            age_seconds=9000,
            tempo="idle",
            inFlight={"tasks": 2, "queued": 0, "kinds": ["local_agent", "local_bash"]},
        )

        self.assertEqual("running", update["state"])
        self.assertEqual([], stopped)

    def test_the_hard_timeout_is_off_unless_the_caller_sets_it(self) -> None:
        update, _, _ = self.reconcile(
            age_seconds=9000, inFlight={"tasks": 1, "queued": 0}
        )

        self.assertEqual("running", update["state"])

    def test_the_hard_timeout_reaps_a_permanently_busy_worker(self) -> None:
        update, stopped, _ = self.reconcile(
            age_seconds=9000,
            inFlight={"tasks": 1, "queued": 0, "kinds": ["local_bash"]},
            hard_timeout=600,
        )

        self.assertEqual("unknown", update["state"])
        self.assertIn("made no progress", update["error"])
        self.assertEqual([["deadbeef"]], stopped)

    def test_a_busy_worker_within_its_hard_timeout_keeps_running(self) -> None:
        update, _, _ = self.reconcile(
            age_seconds=120, inFlight={"tasks": 1, "queued": 0}, hard_timeout=600
        )

        self.assertEqual("running", update["state"])

    def test_a_genuinely_finished_worker_still_succeeds(self) -> None:
        update, stopped, _ = self.reconcile(
            age_seconds=9000, state="done", output={"result": "all done"}
        )

        self.assertEqual("succeeded", update["state"])
        self.assertNotIn("error", update)
        self.assertEqual([], stopped)


class ClaudeProviderLaunchOptionsTest(unittest.TestCase):
    """Verifies the exact command line built for a launch, using a fake claude binary."""

    def build_command(
        self,
        provider_payload: dict[str, object] | None = None,
        *,
        make_repo: bool = False,
        **job_fields: object,
    ):
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
            if make_repo:
                (root_path / ".git").mkdir()
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

    def test_an_unset_model_defers_to_the_operator_configuration(self) -> None:
        """A hardcoded tier silently overrode whatever the operator had configured."""

        self.assertNotIn("--model", self.build_command())

    def test_an_explicit_model_is_passed_through(self) -> None:
        argv = self.build_command(model="opus")

        self.assertEqual("opus", argv[argv.index("--model") + 1])

    def test_a_git_worktree_is_pinned_so_commits_land_where_assigned(self) -> None:
        """Otherwise a write-capable worker commits into its own nested worktree."""

        argv = self.build_command(make_repo=True)

        self.assertIn("Do not call EnterWorktree", argv[-1])
        self.assertTrue(argv[-1].startswith("do the thing"))

    def test_a_non_repository_cwd_is_left_alone(self) -> None:
        argv = self.build_command()

        self.assertEqual("do the thing", argv[-1])

    def test_the_workspace_pin_can_be_declined(self) -> None:
        argv = self.build_command(
            provider_payload={"workspace_note": False}, make_repo=True
        )

        self.assertNotIn("EnterWorktree", argv[-1])


if __name__ == "__main__":
    unittest.main()
