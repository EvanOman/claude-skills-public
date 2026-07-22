from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import time
import unittest
from pathlib import Path

from support import (
    ALIASES,
    TERMINAL,
    ContractFailure,
    Harness,
    IntegrationCase,
    assert_uuid_text,
    concurrent_calls,
    cursor_from,
    first_value,
    ids_from,
    private_mode,
    recursive_values,
    state_from,
)


def group_target(group_id: str) -> dict[str, object]:
    return {"target": {"group_id": group_id}}


def job_target(job_id: str) -> dict[str, object]:
    return {"target": {"job_id": job_id}}


class SchemaAndCrudTest(IntegrationCase):
    def test_schema_zero_migrates_idempotently_and_runtime_is_private(self) -> None:
        self.harness.state.mkdir(mode=0o700)
        database = self.harness.state / "overmind.db"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA user_version=0")
            connection.execute("CREATE TABLE legacy_probe(value TEXT)")
        first = self.harness.call("doctor").json()
        with sqlite3.connect(database) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        self.assertGreater(version, 0, first)
        second = self.harness.call("doctor").json()
        with sqlite3.connect(database) as connection:
            self.assertEqual(version, connection.execute("PRAGMA user_version").fetchone()[0])
        self.assertTrue(private_mode(self.harness.state))
        self.assertTrue(private_mode(database))
        socket = self.harness.state / "overmind.sock"
        if socket.exists():
            self.assertTrue(private_mode(socket))
        self.assertEqual(
            first_value(first, "schema_version", "schemaVersion", "version"),
            first_value(second, "schema_version", "schemaVersion", "version"),
        )

    def test_group_and_job_crud_remains_concise(self) -> None:
        created = self.harness.run_many(
            [
                self.harness.job_spec("alpha", result="ALPHA"),
                self.harness.job_spec("beta", result="BETA"),
            ],
            key="crud-create",
            label="crud-group",
        )
        group_ids, job_ids = ids_from(created, "group"), ids_from(created, "job")
        self.assertEqual(1, len(group_ids), created)
        self.assertEqual(2, len(job_ids), created)
        assert_uuid_text(group_ids[0], "group ID")
        for job_id in job_ids:
            assert_uuid_text(job_id, "job ID")

        shown_group = self.harness.call("show", group_target(group_ids[0])).json()
        self.assertEqual(group_ids[0], ids_from(shown_group, "group")[0])
        shown_job = self.harness.call("show", job_target(job_ids[0])).json()
        self.assertEqual(job_ids[0], ids_from(shown_job, "job")[0])
        listed = self.harness.call(
            "jobs", {"group_id": group_ids[0], "label": "alpha"}
        ).json()
        self.assertIn(job_ids[0], ids_from(listed, "job"))
        self.assertLess(len(json.dumps(listed)), 16_384, "jobs output is not concise")

        cursor = cursor_from(created)
        done = self.harness.call(
            "await",
            {**group_target(group_ids[0]), "condition": "all_terminal", "since_cursor": cursor, "timeout": 5},
        ).json()
        self.assertTrue(first_value(done, "condition_met", "conditionMet", "ready"), done)
        collected = self.harness.call(
            "collect", {**group_target(group_ids[0]), "preview_bytes": 128}
        ).json()
        self.assertEqual(set(job_ids), set(ids_from(collected, "job")))

        reply = self.harness.call(
            "reply",
            {**job_target(job_ids[0]), "prompt": "stateful correction", "idempotency_key": "crud-reply"},
        ).json()
        child_ids = [value for value in ids_from(reply, "job") if value not in job_ids]
        self.assertEqual(1, len(child_ids), reply)
        self.assertEqual(job_ids[0], first_value(reply, "parent_job_id", "parentJobId"))

        self.harness.call("stop", group_target(group_ids[0]))
        terminal = self.harness.call(
            "await",
            {**group_target(group_ids[0]), "condition": "all_terminal", "since_cursor": 0, "timeout": 5},
        ).json()
        # Historical events intentionally retain queued/starting states. Assert
        # terminality from the current job snapshots, not from the event log.
        states = {
            item.get("state")
            for item in terminal.get("jobs", [])
            if isinstance(item, dict)
        }
        self.assertTrue(states)
        self.assertTrue(states.issubset(TERMINAL), states)
        self.harness.call("forget", group_target(group_ids[0]))
        missing = self.harness.call("show", group_target(group_ids[0]), check=False)
        self.assertNotEqual(0, missing.returncode)


class IdempotencyAndAliasTest(IntegrationCase):
    def test_collect_preview_bytes_flag_matches_structured_contract(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("preview-flag", result="abcdef")],
            key="preview-flag",
        )
        group_id = ids_from(created, "group")[0]
        self.harness.call(
            "await",
            {
                **group_target(group_id),
                "condition": "all_terminal",
                "since_cursor": cursor_from(created),
                "timeout": 2,
            },
        )

        completed = subprocess.run(
            [
                str(self.harness.cli),
                "collect",
                group_id,
                "--preview-bytes",
                "2",
                "--json",
            ],
            text=True,
            capture_output=True,
            env=self.harness.env,
            timeout=5,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(2, result["bounded"]["preview_bytes"])
        self.assertEqual("ab", result["results"][0]["preview"])

    def test_idempotent_retry_and_conflict(self) -> None:
        payload = {
            "group": {"label": "retry"},
            "jobs": [self.harness.job_spec("one", mode="hold")],
            "idempotency_key": "same-launch",
        }
        first = self.harness.call("run-many", payload).json()
        second = self.harness.call("run-many", payload).json()
        self.assertEqual(ids_from(first, "group"), ids_from(second, "group"))
        self.assertEqual(ids_from(first, "job"), ids_from(second, "job"))
        self.assertEqual(1, len(self.harness.provider_calls("launch")))

        conflict = json.loads(json.dumps(payload))
        conflict["jobs"][0]["brief"] = "different logical work"
        failed = self.harness.call("run-many", conflict, check=False)
        self.assertNotEqual(0, failed.returncode)
        self.assertIn("conflict", (failed.stdout + failed.stderr).lower())
        self.assertEqual(1, len(self.harness.provider_calls("launch")))

    def test_concurrent_clients_share_one_idempotent_launch(self) -> None:
        payload = {
            "group": {"label": "concurrent"},
            "jobs": [self.harness.job_spec("only", mode="hold")],
            "idempotency_key": "concurrent-launch",
        }

        responses = concurrent_calls(
            lambda: self.harness.call("run-many", payload).json(), 8
        )
        group_ids = {ids_from(response, "group")[0] for response in responses}
        job_ids = {ids_from(response, "job")[0] for response in responses}
        self.assertEqual(1, len(group_ids), responses)
        self.assertEqual(1, len(job_ids), responses)
        self.assertEqual(1, len(self.harness.provider_calls("launch")))

    def test_human_aliases_match_canonical_operations(self) -> None:
        spec = self.harness.job_spec("alias", mode="hold")
        payload = {
            "group": {"label": "alias-group"},
            "job": spec,
            "idempotency_key": "alias-run",
        }
        canonical = self.harness.call("run", payload).json()
        aliased = self.harness.call("spawn", payload).json()
        self.assertEqual(ids_from(canonical, "job"), ids_from(aliased, "job"))
        job_id = ids_from(canonical, "job")[0]
        group_id = ids_from(canonical, "group")[0]

        read_pairs = (("jobs", "list", {"group_id": group_id}), ("show", "status", job_target(job_id)))
        for canonical_name, alias, request in read_pairs:
            left = self.harness.call(canonical_name, request).json()
            right = self.harness.call(alias, request).json()
            self.assertEqual(ids_from(left, "job"), ids_from(right, "job"), alias)
            self.assertEqual(state_from(left), state_from(right), alias)

        stopped = self.harness.call("interrupt", job_target(job_id)).json()
        self.assertIn(state_from(stopped), TERMINAL)
        stopped_again = self.harness.call("stop", job_target(job_id)).json()
        self.assertEqual(state_from(stopped), state_from(stopped_again))
        wait_request = {
            **job_target(job_id),
            "condition": "all_terminal",
            "since_cursor": 0,
            "timeout": 1,
        }
        canonical_wait = self.harness.call("await", wait_request).json()
        alias_wait = self.harness.call("wait", wait_request).json()
        self.assertEqual(state_from(canonical_wait), state_from(alias_wait))
        collect_request = {**job_target(job_id), "preview_bytes": 64}
        canonical_result = self.harness.call("collect", collect_request).json()
        alias_result = self.harness.call("result", collect_request).json()
        self.assertEqual(ids_from(canonical_result, "job"), ids_from(alias_result, "job"))

        parent = self.harness.run_many(
            [self.harness.job_spec("reply-parent")], key="alias-reply-parent"
        )
        parent_id = ids_from(parent, "job")[0]
        parent_group = ids_from(parent, "group")[0]
        self.harness.call(
            "await",
            {**group_target(parent_group), "condition": "all_terminal", "since_cursor": cursor_from(parent), "timeout": 2},
        )
        reply_request = {
            **job_target(parent_id),
            "prompt": "same continuation",
            "idempotency_key": "alias-reply",
        }
        canonical_reply = self.harness.call("reply", reply_request).json()
        alias_reply = self.harness.call("followup", reply_request).json()
        self.assertEqual(ids_from(canonical_reply, "job"), ids_from(alias_reply, "job"))

        cleanup_a = self.harness.run_many(
            [self.harness.job_spec("cleanup-a")], key="cleanup-a"
        )
        cleanup_b = self.harness.run_many(
            [self.harness.job_spec("cleanup-b")], key="cleanup-b"
        )
        for created_item in (cleanup_a, cleanup_b):
            self.harness.call(
                "await",
                {**group_target(ids_from(created_item, "group")[0]), "condition": "all_terminal", "since_cursor": cursor_from(created_item), "timeout": 2},
            )
        forgotten = self.harness.call(
            "forget", job_target(ids_from(cleanup_a, "job")[0])
        ).json()
        cleaned = self.harness.call(
            "cleanup", job_target(ids_from(cleanup_b, "job")[0])
        ).json()
        self.assertEqual(
            bool(first_value(forgotten, "forgotten", "deleted", "ok")),
            bool(first_value(cleaned, "forgotten", "deleted", "ok")),
        )


class WaitCollectAndSafetyTest(IntegrationCase):
    def test_generic_mutations_reject_unique_prefixes_shorter_than_eight_characters(self) -> None:
        operations = (
            ("stop", {}),
            (
                "reply",
                {"prompt": "must not launch", "idempotency_key": "short-reply-mutation"},
            ),
            ("forget", {}),
        )
        for operation, extra in operations:
            with self.subTest(operation=operation):
                created = self.harness.run_many(
                    [self.harness.job_spec(f"short-{operation}", mode="hold")],
                    key=f"short-{operation}",
                )
                group_id = ids_from(created, "group")[0]
                job_id = ids_from(created, "job")[0]
                candidates = [job_id[:size] for size in range(1, 8)]
                short = next(value for value in reversed(candidates) if not group_id.startswith(value))
                failed = self.harness.call(
                    operation,
                    {"target": short, **extra},
                    check=False,
                )
                self.assertNotEqual(0, failed.returncode, failed.stdout)
                detail = (failed.stdout + failed.stderr).lower()
                self.assertTrue(
                    "ambiguous" in detail or "8" in detail or "full" in detail,
                    detail,
                )
                shown = self.harness.call("show", job_target(job_id)).json()
                self.assertNotIn(state_from(shown), TERMINAL, shown)

    def test_unchanged_usage_reconciliation_does_not_append_duplicate_evidence(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("usage-stable", mode="hold")],
            key="usage-stable",
        )
        job_id = ids_from(created, "job")[0]
        database = self.harness.state / "overmind.db"

        def counts() -> tuple[int, int]:
            with sqlite3.connect(database) as connection:
                usage = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM usage WHERE job_id=?", (job_id,)
                    ).fetchone()[0]
                )
                events = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM events WHERE job_id=? AND kind='usage.recorded'",
                        (job_id,),
                    ).fetchone()[0]
                )
            return usage, events

        deadline = time.monotonic() + 2
        before = counts()
        while before[0] == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
            before = counts()
        self.assertGreater(before[0], 0, before)
        for _ in range(3):
            self.harness.call("show", {**job_target(job_id), "fresh": True})
        time.sleep(0.25)
        self.assertEqual(before, counts())
        self.harness.call("stop", job_target(job_id))

    def test_any_all_await_uses_monotonic_resumable_cursors(self) -> None:
        created = self.harness.run_many(
            [
                self.harness.job_spec("first", mode="hold"),
                self.harness.job_spec("second", mode="hold"),
            ],
            key="await-cursors",
        )
        group_id = ids_from(created, "group")[0]
        job_ids = ids_from(created, "job")
        original = cursor_from(created)
        self.harness.call("stop", job_target(job_ids[0]))
        any_done = self.harness.call(
            "await",
            {**group_target(group_id), "condition": "any_terminal", "since_cursor": original, "timeout": 2},
        ).json()
        any_cursor = cursor_from(any_done)
        self.assertGreater(any_cursor, original)
        self.harness.call("stop", job_target(job_ids[1]))
        all_done = self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "since_cursor": any_cursor, "timeout": 2},
        ).json()
        self.assertGreaterEqual(cursor_from(all_done), any_cursor)
        counts = first_value(all_done, "counts", "state_counts", "stateCounts")
        self.assertIsInstance(counts, dict, all_done)
        self.assertEqual(2, sum(int(value) for value in counts.values()))

    def test_timeout_and_cancelled_client_do_not_cancel_work_or_lose_events(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("held", mode="hold")], key="cancel-await"
        )
        group_id = ids_from(created, "group")[0]
        job_id = ids_from(created, "job")[0]
        cursor = cursor_from(created)
        started = time.monotonic()
        timeout = self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "since_cursor": cursor, "timeout": 0.1},
        ).json()
        self.assertLess(time.monotonic() - started, 1)
        self.assertFalse(first_value(timeout, "condition_met", "conditionMet", "ready"))
        self.assertTrue(first_value(timeout, "timed_out", "timedOut", "timeout"))

        process = self.harness.start_call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "since_cursor": cursor, "timeout": 30},
        )
        assert process.stdin
        process.stdin.write(json.dumps({**group_target(group_id), "condition": "all_terminal", "since_cursor": cursor, "timeout": 30}))
        process.stdin.close()
        time.sleep(0.1)
        process.terminate()
        process.wait(timeout=2)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
        self.harness.call("stop", job_target(job_id))
        resumed = self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "since_cursor": cursor, "timeout": 2},
        ).json()
        self.assertGreater(cursor_from(resumed), cursor)
        self.assertTrue(first_value(resumed, "condition_met", "conditionMet", "ready"))

    def test_collect_is_bounded_and_points_to_full_artifact(self) -> None:
        full = "HEAD:" + ("x" * 12_000) + ":TAIL"
        created = self.harness.run_many(
            [self.harness.job_spec("large", result=full)], key="bounded-collect"
        )
        group_id = ids_from(created, "group")[0]
        self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "since_cursor": cursor_from(created), "timeout": 3},
        )
        collected = self.harness.call(
            "collect", {**group_target(group_id), "preview_bytes": 128}
        ).json()
        previews = list(recursive_values(collected, {"preview", "result_preview", "resultPreview"}))
        self.assertTrue(previews, collected)
        self.assertTrue(all(len(item.encode()) <= 128 for item in previews if isinstance(item, str)))
        self.assertNotIn(":TAIL", json.dumps(collected))
        paths = [Path(item) for item in recursive_values(collected, {"result_path", "resultPath", "artifact_path", "artifactPath"}) if isinstance(item, str)]
        self.assertTrue(paths, collected)
        self.assertTrue(any(path.read_text() == full for path in paths if path.is_file()))

    def test_billing_class_cannot_fallback_silently(self) -> None:
        spec = self.harness.job_spec("billing", mode="fallback-metered")
        refused = self.harness.call(
            "run-many",
            {"group": {"label": "billing"}, "jobs": [spec], "idempotency_key": "billing-refused"},
            check=False,
        )
        self.assertNotEqual(0, refused.returncode)
        detail = (refused.stdout + refused.stderr).lower()
        self.assertTrue("billing" in detail and ("fallback" in detail or "metered" in detail), detail)

        accepted = self.harness.run_many(
            [spec],
            key="billing-explicit",
            allow_billing_class_change=True,
        )
        classes = set(recursive_values(accepted, {"billing_class", "billingClass"}))
        self.assertIn("explicit-metered", classes)

    def test_exact_and_ambiguous_ids_and_stop_identity_safety(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec(f"held-{number}", mode="hold") for number in range(17)]
            + [self.harness.job_spec("stale", mode="stale-process")],
            key="ambiguous-ids",
        )
        job_ids = ids_from(created, "job")
        self.assertEqual(18, len(job_ids), created)
        by_prefix: dict[str, list[str]] = {}
        for job_id in job_ids[:-1]:
            by_prefix.setdefault(job_id[0], []).append(job_id)
        common = next(values for values in by_prefix.values() if len(values) > 1)
        ambiguous = self.harness.call(
            "stop", {"target": {"job_id": common[0][0]}}, check=False
        )
        self.assertNotEqual(0, ambiguous.returncode)
        self.assertIn("ambiguous", (ambiguous.stdout + ambiguous.stderr).lower())
        for job_id in common:
            self.assertNotIn(state_from(self.harness.call("show", job_target(job_id)).json()), TERMINAL)

        exact = self.harness.call("stop", job_target(common[0])).json()
        self.assertEqual("interrupted", state_from(exact))
        stale = self.harness.call("stop", job_target(job_ids[-1])).json()
        self.assertEqual("unknown", state_from(stale))
        stop_calls = self.harness.provider_calls("interrupt")
        unsafe = [call for call in stop_calls if call["response"].get("signal_sent") is True and call["response"].get("mode") == "stale-process"]
        self.assertFalse(unsafe, stop_calls)


if __name__ == "__main__":
    unittest.main()
