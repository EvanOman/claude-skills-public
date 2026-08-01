"""Session isolation, listing limits, and the response contracts added with them.

These are black-box contract tests: everything goes through the public CLI or
MCP entrypoints, and ownership is pinned via ``OVERMIND_V2_OWNER_SESSION`` so
the assertions do not depend on the host machine's process ancestry.
"""

from __future__ import annotations

import json
import sqlite3
import unittest

from support import (
    IntegrationCase,
    McpClient,
    cursor_from,
    ids_from,
)


OTHER_SESSION = {"OVERMIND_V2_OWNER_SESSION": "blackbox-other-session"}


def group_target(group_id: str) -> dict[str, object]:
    return {"target": {"group_id": group_id}}


class SessionScopeTest(IntegrationCase):
    def test_default_listing_is_scoped_to_the_calling_session(self) -> None:
        mine = self.harness.run_many(
            [self.harness.job_spec("scoped-mine")], key="scope-mine"
        )
        other = self.harness.call(
            "run-many",
            {
                "group": {"label": "scoped-other"},
                "jobs": [self.harness.job_spec("scoped-other")],
                "idempotency_key": "scope-other",
            },
            extra_env=OTHER_SESSION,
        ).json()
        my_job = ids_from(mine, "job")[0]
        other_job = ids_from(other, "job")[0]

        listed = self.harness.call("jobs", {}).json()
        listed_ids = set(ids_from(listed, "job"))
        self.assertIn(my_job, listed_ids)
        self.assertNotIn(other_job, listed_ids)
        self.assertEqual("blackbox-test-session", listed.get("scope"), listed)

        theirs = self.harness.call("jobs", {}, extra_env=OTHER_SESSION).json()
        their_ids = set(ids_from(theirs, "job"))
        self.assertIn(other_job, their_ids)
        self.assertNotIn(my_job, their_ids)

        everything = self.harness.call("jobs", {"scope": "all"}).json()
        every_id = set(ids_from(everything, "job"))
        self.assertLessEqual({my_job, other_job}, every_id)
        self.assertEqual("all", everything.get("scope"))

        named = self.harness.call(
            "jobs", {"owner_session": "blackbox-other-session"}
        ).json()
        self.assertEqual({other_job}, set(ids_from(named, "job")))

    def test_group_identifier_is_a_cross_session_capability(self) -> None:
        other = self.harness.call(
            "run-many",
            {
                "group": {"label": "capability"},
                "jobs": [self.harness.job_spec("capability")],
                "idempotency_key": "scope-capability",
            },
            extra_env=OTHER_SESSION,
        ).json()
        group_id = ids_from(other, "group")[0]
        listed = self.harness.call("jobs", {"group_id": group_id}).json()
        self.assertEqual(set(ids_from(other, "job")), set(ids_from(listed, "job")))

    def test_reply_continuation_inherits_the_parent_owner(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("continuation-owner")], key="scope-reply"
        )
        group_id = ids_from(created, "group")[0]
        job_id = ids_from(created, "job")[0]
        self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "timeout": 5},
        )
        continued = self.harness.call(
            "reply", {"job_id": job_id, "prompt": "carry on"}
        ).json()
        child = [job for job in continued.get("jobs", []) if job.get("id") != job_id]
        self.assertTrue(child, continued)
        self.assertEqual("blackbox-test-session", child[0].get("owner_session"), child)


class ListingLimitsTest(IntegrationCase):
    def test_listing_is_newest_first_with_honest_truncation(self) -> None:
        first = self.harness.run_many(
            [self.harness.job_spec("older")], key="limits-old"
        )
        second = self.harness.run_many(
            [self.harness.job_spec("newer")], key="limits-new"
        )
        listed = self.harness.call("jobs", {"limit": 1}).json()
        self.assertEqual(1, listed["count"])
        self.assertEqual(2, listed["total"])
        self.assertTrue(listed["truncated"])
        self.assertIn("hint", listed)
        self.assertEqual(ids_from(second, "job"), ids_from(listed, "job"))
        self.assertNotEqual(ids_from(first, "job"), ids_from(listed, "job"))

    def test_short_group_ids_resolve_and_garbage_errors(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("short-id")], key="limits-short"
        )
        group_id = ids_from(created, "group")[0]
        listed = self.harness.call("jobs", {"group_id": group_id[:8]}).json()
        self.assertEqual(set(ids_from(created, "job")), set(ids_from(listed, "job")))

        missing = self.harness.call("jobs", {"group_id": "deadbeef"}, check=False)
        self.assertNotEqual(0, missing.returncode, missing.stdout)
        self.assertIn("not_found", missing.stdout + missing.stderr)

    def test_await_without_cursor_does_not_replay_history(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("no-replay")], key="limits-replay"
        )
        group_id = ids_from(created, "group")[0]
        self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "timeout": 5},
        )
        settled = self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "timeout": 5},
        ).json()
        self.assertTrue(settled.get("satisfied"))
        kinds = {event.get("kind") for event in settled.get("events", [])}
        self.assertNotIn("job.queued", kinds, settled)

        replayed = self.harness.call(
            "await",
            {
                **group_target(group_id),
                "condition": "all_terminal",
                "since_cursor": 0,
                "timeout": 5,
            },
        ).json()
        replay_kinds = {event.get("kind") for event in replayed.get("events", [])}
        self.assertIn("job.queued", replay_kinds, replayed)

    def test_collect_accepts_job_ids_inside_target(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("collect-a"), self.harness.job_spec("collect-b")],
            key="limits-collect",
        )
        group_id = ids_from(created, "group")[0]
        job_ids = ids_from(created, "job")
        self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "timeout": 5},
        )
        collected = self.harness.call(
            "collect", {"target": {"job_ids": job_ids}}
        ).json()
        results = collected.get("results", [])
        self.assertEqual(len(job_ids), len(results), collected)
        bounded = collected.get("bounded", {})
        self.assertEqual(4000, bounded.get("preview_bytes"), bounded)
        self.assertFalse(bounded.get("jobs_truncated"), bounded)


class StopForgetSignalsTest(IntegrationCase):
    def test_stop_names_a_noop_on_terminal_jobs(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("stop-noop")], key="signal-stop"
        )
        group_id = ids_from(created, "group")[0]
        self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "timeout": 5},
        )
        stopped = self.harness.call("stop", {"target": group_id}).json()
        actions = stopped.get("actions", {})
        self.assertEqual({"already_terminal": 1}, actions, stopped)
        self.assertEqual(
            "already_terminal", stopped["jobs"][0].get("stop_action"), stopped
        )

    def test_forget_removes_events_and_empty_group_shells(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("forget-clean")], key="signal-forget"
        )
        group_id = ids_from(created, "group")[0]
        job_id = ids_from(created, "job")[0]
        self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "timeout": 5},
        )
        self.harness.call("forget", {"target": job_id})

        shown = self.harness.call(
            "show", {"target": {"group_id": group_id}}, check=False
        )
        self.assertNotEqual(0, shown.returncode, shown.stdout)
        self.assertIn("not_found", shown.stdout + shown.stderr)

        connection = sqlite3.connect(
            f"file:{self.harness.state / 'overmind.db'}?mode=ro", uri=True
        )
        try:
            leftover = connection.execute(
                "SELECT COUNT(*) FROM events WHERE job_id=?", (job_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(0, leftover)


class DoctorEvidenceTest(IntegrationCase):
    def test_doctor_reports_daemon_age_and_code_freshness(self) -> None:
        doctor = self.harness.call("doctor").json()
        daemon = doctor.get("daemon", {})
        code = doctor.get("code", {})
        self.assertIn("started_at", daemon, doctor)
        self.assertGreaterEqual(daemon.get("uptime_seconds", -1), 0.0)
        self.assertIs(False, code.get("stale"), doctor)
        self.assertIn("loaded_mtime", code)


class McpSchemaGuardTest(IntegrationCase):
    def test_every_tool_schema_survives_strict_validators(self) -> None:
        """A root-level anyOf/oneOf/allOf gets a tool silently dropped by the
        harness-side schema validator; collect vanished from discovery that way."""

        mcp = McpClient(self.harness.mcp, self.harness.env)
        try:
            tools = mcp.tools()
            names = {tool["name"] for tool in tools}
            self.assertIn("collect", names, sorted(names))
            for tool in tools:
                schema = tool["inputSchema"]
                self.assertEqual("object", schema.get("type"), tool["name"])
                for combinator in ("anyOf", "oneOf", "allOf", "not"):
                    self.assertNotIn(
                        combinator,
                        schema,
                        f"{tool['name']} has root-level {combinator}",
                    )
                self.assertIsInstance(
                    schema.get("required", []), list, tool["name"]
                )
            # The serialized catalog is loaded into every orchestrator context;
            # hold the line on its size so descriptions do not creep back up.
            catalog_bytes = len(json.dumps(tools, separators=(",", ":")))
            self.assertLess(catalog_bytes, 12000, catalog_bytes)
        finally:
            mcp.close()

    def test_collect_and_scoped_jobs_work_over_mcp(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("mcp-collect", result="x" * 400)],
            key="mcp-collect",
        )
        group_id = ids_from(created, "group")[0]
        self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "timeout": 5},
        )
        mcp = McpClient(self.harness.mcp, self.harness.env)
        try:
            collected = mcp.call_tool("collect", {"target": group_id})
            structured = collected["result"]["structuredContent"]
            self.assertEqual(1, len(structured["results"]), structured)
            self.assertTrue(
                structured["results"][0]["preview"].startswith("x"), structured
            )
            listed = mcp.call_tool("jobs", {})["result"]["structuredContent"]
            self.assertEqual("blackbox-test-session", listed.get("scope"), listed)
            self.assertLessEqual(
                {ids_from(created, "job")[0]}, set(ids_from(listed, "job")), listed
            )
        finally:
            mcp.close()


class RestartIdempotencyTest(IntegrationCase):
    def test_same_key_replays_across_sessions(self) -> None:
        """A restarted parent gets a fresh session identity; replaying the same
        idempotency key must return the original group, not a conflict."""

        payload = {
            "group": {"label": "replay"},
            "jobs": [self.harness.job_spec("replay-owner")],
            "idempotency_key": "replay-across-sessions",
        }
        first = self.harness.call("run-many", payload).json()
        second = self.harness.call(
            "run-many", payload, extra_env=OTHER_SESSION
        ).json()
        self.assertEqual(ids_from(first, "group"), ids_from(second, "group"))
        self.assertEqual(ids_from(first, "job"), ids_from(second, "job"))
        self.assertFalse(second.get("created"), second)
        self.assertTrue(second.get("idempotent"), second)

    def test_same_key_different_worker_options_conflicts(self) -> None:
        base = {
            "group": {"label": "conflict"},
            "jobs": [self.harness.job_spec("conflict-options")],
            "idempotency_key": "conflict-on-options",
        }
        self.harness.call("run-many", base).json()
        changed = dict(base)
        changed["min_result_bytes"] = 0
        conflicted = self.harness.call("run-many", changed, check=False)
        self.assertNotEqual(0, conflicted.returncode, conflicted.stdout)
        self.assertIn("conflict", conflicted.stdout + conflicted.stderr)


if __name__ == "__main__":
    unittest.main()
