from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
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
            # The thin-result guard is exercised on its own in
            # ClaudeThinResultTest; these cases are about state mapping.
            "provider_payload": {"min_result_bytes": 0},
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
        # An unrecognized state only settles to unknown once the CLI has stopped
        # moving (see ClaudeTransientStateTest), so "mystery" carries a stale
        # timestamp here; the point of this case is that the detail survives.
        stale = (
            (datetime.now(timezone.utc) - timedelta(seconds=600))
            .isoformat()
            .replace("+00:00", "Z")
        )
        cases = (
            ("failed", "detail", "worker failed", "failed", {}),
            ("cancelled", "detail", "stopped by caller", "interrupted", {}),
            (
                "mystery",
                "error",
                "unrecognized provider state",
                "unknown",
                {"updatedAt": stale},
            ),
        )
        for raw_state, field, message, expected_state, extra in cases:
            with self.subTest(state=raw_state, field=field):
                update = self.reconcile(raw_state, **{field: message}, **extra)
                self.assertEqual(expected_state, update["state"])
                self.assertEqual(message, update["error"])
                self.assertEqual(raw_state, update["raw_state"])

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


class ClaudeTransientStateTest(unittest.TestCase):
    """An unmapped CLI state is a transition, not an outcome.

    Observed live: a worker reported "SIGTERM (143); respawning", the broker recorded
    it terminal as unknown, and the worker then respawned and committed its work. A
    terminal job is never reconciled again, so a successful worker was permanently
    misreported as unknown and its result artifact discarded.
    """

    def reconcile(self, raw_state: str, *, age_seconds: float):
        with tempfile.TemporaryDirectory(prefix="overmind-v2-transient.") as root:
            job_dir = Path(root) / "job"
            job_dir.mkdir()
            state_path = job_dir / "state.json"
            observed = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
            state_path.write_text(
                json.dumps(
                    {
                        "state": raw_state,
                        "detail": "SIGTERM (143); respawning",
                        "updatedAt": observed.isoformat().replace("+00:00", "Z"),
                    }
                ),
                encoding="utf-8",
            )
            return ClaudeProvider().reconcile(
                {
                    "provider_job_id": "deadbeef",
                    "provider_state_path": str(state_path),
                    "brief_path": str(job_dir / "brief.md"),
                    "provider_payload": {},
                }
            )

    def test_a_respawning_worker_is_still_running(self) -> None:
        self.assertEqual("running", self.reconcile("respawning", age_seconds=1)["state"])

    def test_a_fresh_unmapped_state_is_not_yet_an_outcome(self) -> None:
        self.assertEqual("running", self.reconcile("kerfuffle", age_seconds=5)["state"])

    def test_an_unmapped_state_that_stops_moving_becomes_unknown(self) -> None:
        update = self.reconcile("kerfuffle", age_seconds=600)

        self.assertEqual("unknown", update["state"])

    def test_a_real_failure_is_still_reported_immediately(self) -> None:
        self.assertEqual("failed", self.reconcile("failed", age_seconds=1)["state"])


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
            age_seconds=600, inFlight={"tasks": 1, "queued": 0}
        )

        self.assertEqual("running", update["state"])
        self.assertEqual([], stopped)

    def test_a_worker_that_is_not_idle_is_never_reaped(self) -> None:
        update, _, _ = self.reconcile(age_seconds=600, tempo="thinking")

        self.assertEqual("running", update["state"])

    def test_reaping_can_be_disabled_per_job(self) -> None:
        update, stopped, _ = self.reconcile(age_seconds=600, grace=0)

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
            age_seconds=600,
            tempo="idle",
            inFlight={"tasks": 2, "queued": 0, "kinds": ["local_agent", "local_bash"]},
        )

        self.assertEqual("running", update["state"])
        self.assertEqual([], stopped)

    def test_a_permanently_busy_worker_is_reaped_by_the_default_ceiling(self) -> None:
        """The hard timeout used to be opt-in, so nothing ever set it.

        Measured over 188 jobs: not one passed idle_hard_timeout_seconds, and a
        wedged worker held a non-terminal record for 39 hours. The default bounds
        silence, not runtime -- the CLI touches the state file on every message and
        tool result -- so it cannot fire while a worker is doing anything.
        """

        update, stopped, _ = self.reconcile(
            age_seconds=9000, inFlight={"tasks": 1, "queued": 0, "kinds": ["local_bash"]}
        )

        self.assertEqual("unknown", update["state"])
        self.assertIn("made no progress", update["error"])
        self.assertEqual([["deadbeef"]], stopped)

    def test_a_busy_worker_within_the_default_ceiling_keeps_running(self) -> None:
        update, _, _ = self.reconcile(
            age_seconds=1800, inFlight={"tasks": 1, "queued": 0}
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

    def test_the_hard_timeout_can_be_disabled_per_job(self) -> None:
        update, stopped, _ = self.reconcile(
            age_seconds=9000,
            inFlight={"tasks": 1, "queued": 0},
            hard_timeout=0,
        )

        self.assertEqual("running", update["state"])
        self.assertEqual([], stopped)

    def test_a_genuinely_finished_worker_still_succeeds(self) -> None:
        update, stopped, _ = self.reconcile(
            age_seconds=9000,
            state="done",
            output={"result": "all done: " + "verified detail. " * 40},
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

    def test_bypass_permissions_is_launched_as_an_allowed_mode(self) -> None:
        """Selecting the mode is not enough; the CLI must also be told it is allowed.

        Without --allow-dangerously-skip-permissions the requested bypass can be
        refused and the worker falls back to prompting, which a background session
        with no TTY cannot answer.
        """

        argv = self.build_command()

        self.assertIn("--allow-dangerously-skip-permissions", argv)

    def test_a_narrower_permission_mode_does_not_allow_bypass(self) -> None:
        argv = self.build_command(provider_payload={"permission_mode": "dontAsk"})

        self.assertNotIn("--allow-dangerously-skip-permissions", argv)

    def test_workers_get_no_inherited_mcp_servers(self) -> None:
        """A project .mcp.json server stalls a worker on an unanswerable prompt.

        Measured: three jobs "succeeded" in under five seconds with a 64-byte
        artifact reading "approve 1 new project MCP server (grafana) -- attach to
        respond".
        """

        argv = self.build_command()

        self.assertIn("--strict-mcp-config", argv)
        self.assertNotIn("--mcp-config", argv)

    def test_a_named_mcp_config_is_the_only_one_a_worker_gets(self) -> None:
        argv = self.build_command(
            provider_payload={"mcp_config": ["/tmp/a.json", "/tmp/b.json"]}
        )

        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(
            ["/tmp/a.json", "/tmp/b.json"],
            [argv[index + 1] for index, item in enumerate(argv) if item == "--mcp-config"],
        )

    def test_mcp_isolation_can_be_declined(self) -> None:
        argv = self.build_command(provider_payload={"strict_mcp_config": False})

        self.assertNotIn("--strict-mcp-config", argv)

    def test_the_background_worktree_guard_is_disabled(self) -> None:
        """The guard refuses the first Write until the worker isolates itself.

        That is the opposite of what a broker-launched worker must do -- the
        orchestrator already assigned it a directory -- and bypassPermissions does
        not cover it, because it is workspace policy, not a permission prompt.
        """

        argv = self.build_command()

        self.assertIn("--settings", argv)
        self.assertEqual(
            {"worktree": {"bgIsolation": "none"}},
            json.loads(argv[argv.index("--settings") + 1]),
        )

    def test_a_worker_managing_its_own_worktree_keeps_the_guard(self) -> None:
        argv = self.build_command(provider_payload={"workspace_note": False})

        self.assertNotIn("--settings", argv)


class ClaudeFinalMessageTest(unittest.TestCase):
    """The result artifact must be the worker's report, not the CLI's headline.

    `output.result` is a one-line summary the CLI keeps for its job list. Recording
    it as the result is why broker-launched Claude jobs returned a 138-byte median
    result while Codex, which captures its last agent message, returned 1,929.
    """

    def reconcile(self, transcript: list[dict[str, object]] | None, **fields: object):
        with tempfile.TemporaryDirectory(prefix="overmind-v2-final.") as root:
            job_dir = Path(root) / "job"
            job_dir.mkdir()
            state: dict[str, object] = {"state": "done", **fields}
            if transcript is not None:
                transcript_path = job_dir / "session.jsonl"
                transcript_path.write_text(
                    "\n".join(json.dumps(record) for record in transcript),
                    encoding="utf-8",
                )
                state["linkScanPath"] = str(transcript_path)
            state_path = job_dir / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            update = ClaudeProvider().reconcile(
                {
                    "provider_job_id": "deadbeef",
                    "provider_state_path": str(state_path),
                    "brief_path": str(job_dir / "brief.md"),
                    "provider_payload": {"min_result_bytes": 0},
                }
            )
            written = (
                Path(str(update["result_path"])).read_text(encoding="utf-8")
                if update.get("result_path")
                else None
            )
            return update, written

    @staticmethod
    def _assistant(text: str, **extra: object) -> dict[str, object]:
        return {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            **extra,
        }

    def test_the_final_assistant_message_beats_the_cli_summary(self) -> None:
        _, written = self.reconcile(
            [
                self._assistant("Reading the file."),
                {"type": "user", "message": {"role": "user", "content": "go on"}},
                self._assistant("Full report: the arithmetic is sound to 8 figures."),
            ],
            output={"result": "arithmetic sound"},
        )

        self.assertEqual(
            "Full report: the arithmetic is sound to 8 figures.", written
        )

    def test_a_subagent_turn_is_not_the_workers_conclusion(self) -> None:
        _, written = self.reconcile(
            [
                self._assistant("The worker's own last word."),
                self._assistant("A subagent's last word.", isSidechain=True),
            ]
        )

        self.assertEqual("The worker's own last word.", written)

    def test_the_cli_summary_is_the_fallback_when_no_transcript_exists(self) -> None:
        _, written = self.reconcile(None, output={"result": "arithmetic sound"})

        self.assertEqual("arithmetic sound", written)

    def test_a_terminal_state_waits_for_the_transcript_to_settle(self) -> None:
        """The transcript is flushed after the state file goes terminal.

        Measured: a report recorded at :41.5 and a terminal marker at :43.4 were
        still not both on disk when the broker read at :44, so the artifact captured
        was the worker's *first* message. A terminal job is never reconciled again,
        so that was permanent.
        """

        just_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        update, _ = self.reconcile(
            [self._assistant("I'll run the steps in order.")],
            output={"result": "arithmetic sound"},
            firstTerminalAt=just_now,
        )

        self.assertEqual("running", update["state"])

    def test_the_wait_for_the_transcript_is_bounded(self) -> None:
        stale = (
            (datetime.now(timezone.utc) - timedelta(seconds=60))
            .isoformat()
            .replace("+00:00", "Z")
        )
        update, written = self.reconcile(
            [{"type": "user", "message": {"role": "user", "content": "go"}}],
            output={"result": "arithmetic sound"},
            firstTerminalAt=stale,
        )

        self.assertEqual("succeeded", update["state"])
        self.assertEqual("arithmetic sound", written)


class ClaudeThinResultTest(unittest.TestCase):
    """A success nobody can read is worse than an honest `unknown`.

    A `succeeded` job is never reconciled again and the orchestrator is told to
    trust it, so a no-op success means the brief looks done, nothing verifies it,
    and the same work is dispatched again later.
    """

    def reconcile(self, **fields: object):
        with tempfile.TemporaryDirectory(prefix="overmind-v2-thin.") as root:
            job_dir = Path(root) / "job"
            job_dir.mkdir()
            payload = dict(fields.pop("payload", {}) or {})  # type: ignore[arg-type]
            state_path = job_dir / "state.json"
            state_path.write_text(
                json.dumps({"state": "done", **fields}), encoding="utf-8"
            )
            return ClaudeProvider().reconcile(
                {
                    "provider_job_id": "deadbeef",
                    "provider_state_path": str(state_path),
                    "brief_path": str(job_dir / "brief.md"),
                    "provider_payload": payload,
                }
            )

    def test_a_progress_note_is_not_a_reported_result(self) -> None:
        update = self.reconcile(
            state="blocked",
            needs="approve 1 new project MCP server (grafana) — attach to respond",
        )

        self.assertEqual("unknown", update["state"])
        self.assertIn("below the 300-byte minimum", update["error"])
        self.assertIn("not a report from the worker", update["error"])

    def test_a_substantive_result_still_succeeds(self) -> None:
        update = self.reconcile(output={"result": "verified detail. " * 40})

        self.assertEqual("succeeded", update["state"])
        self.assertNotIn("error", update)

    def test_the_minimum_can_be_waived_for_a_one_word_verdict(self) -> None:
        update = self.reconcile(
            output={"result": "APPROVE"}, payload={"min_result_bytes": 0}
        )

        self.assertEqual("succeeded", update["state"])

    def test_a_job_that_produced_nothing_at_all_is_not_a_success(self) -> None:
        update = self.reconcile(output=None)

        self.assertEqual("unknown", update["state"])


class ClaudeLaunchRaceTest(unittest.TestCase):
    """A job younger than the grace has not had time to produce an outcome.

    Measured eleven times: a worker was declared terminal `unknown` with detail
    "SIGTERM (143); respawning" between five and twelve seconds after launch, while
    its state file carried an `updatedAt` older than the grace. One of those workers
    was still running an hour later, doing exactly what it was asked. Terminal jobs
    are never reconciled again, so each verdict was permanent.
    """

    def reconcile(self, *, job_age: float | None):
        with tempfile.TemporaryDirectory(prefix="overmind-v2-race.") as root:
            job_dir = Path(root) / "job"
            job_dir.mkdir()
            stale = (
                (datetime.now(timezone.utc) - timedelta(seconds=900))
                .isoformat()
                .replace("+00:00", "Z")
            )
            state_path = job_dir / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "state": "handoff",
                        "detail": "SIGTERM (143); respawning",
                        "updatedAt": stale,
                    }
                ),
                encoding="utf-8",
            )
            job: dict[str, object] = {
                "provider_job_id": "deadbeef",
                "provider_state_path": str(state_path),
                "brief_path": str(job_dir / "brief.md"),
                "provider_payload": {},
            }
            if job_age is not None:
                job["created_at"] = time.time() - job_age
            return ClaudeProvider().reconcile(job)

    def test_a_seconds_old_job_is_not_judged_on_a_stale_state_file(self) -> None:
        self.assertEqual("running", self.reconcile(job_age=7)["state"])

    def test_an_old_job_with_a_frozen_unmapped_state_still_settles(self) -> None:
        self.assertEqual("unknown", self.reconcile(job_age=3600)["state"])


if __name__ == "__main__":
    unittest.main()
