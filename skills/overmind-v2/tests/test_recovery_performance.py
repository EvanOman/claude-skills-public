from __future__ import annotations

import os
import time
import unittest

from support import (
    Harness,
    IntegrationCase,
    cursor_from,
    first_value,
    ids_from,
    percentile,
    recursive_values,
    state_from,
)


def group_target(group_id: str) -> dict[str, object]:
    return {"target": {"group_id": group_id}}


class RecoveryTest(IntegrationCase):
    def test_daemon_restart_reconciles_without_duplicate_launch(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("restart", delay=0.35)], key="restart-once"
        )
        group_id = ids_from(created, "group")[0]
        job_id = ids_from(created, "job")[0]
        self.assertEqual(1, len(self.harness.provider_calls("launch")))
        doctor = self.harness.call("doctor").json()
        pid = first_value(doctor, "daemon_pid", "daemonPid", "pid")
        self.assertIsInstance(pid, int, doctor)
        self.harness.terminate_test_daemon(pid)
        time.sleep(0.4)

        reconciled = self.harness.call(
            "await",
            {**group_target(group_id), "condition": "all_terminal", "since_cursor": cursor_from(created), "timeout": 3},
        ).json()
        self.assertEqual("succeeded", state_from(self.harness.call("show", {"target": {"job_id": job_id}}).json()))
        self.assertTrue(first_value(reconciled, "condition_met", "conditionMet", "ready"))
        self.assertEqual(1, len(self.harness.provider_calls("launch")))

        retry = self.harness.run_many(
            [self.harness.job_spec("restart", delay=0.35)], key="restart-once"
        )
        self.assertEqual([group_id], ids_from(retry, "group"))
        self.assertEqual([job_id], ids_from(retry, "job"))
        self.assertEqual(1, len(self.harness.provider_calls("launch")))

    def test_unobservable_provider_is_preserved_as_unknown(self) -> None:
        created = self.harness.run_many(
            [self.harness.job_spec("unobservable", mode="hold")], key="unknown-reconcile"
        )
        job_id = ids_from(created, "job")[0]
        doctor = self.harness.call("doctor").json()
        pid = first_value(doctor, "daemon_pid", "daemonPid", "pid")
        self.assertIsInstance(pid, int, doctor)
        self.harness.terminate_test_daemon(pid)
        unavailable = str(self.harness.root / "missing-provider")
        shown = self.harness.call(
            "show",
            {"target": {"job_id": job_id}, "fresh": True},
            extra_env={"OVERMIND_V2_FAKE_PROVIDER": unavailable},
        ).json()
        self.assertEqual("unknown", state_from(shown), shown)
        self.assertEqual(1, len(self.harness.provider_calls("launch")))


class StatusPerformanceTest(IntegrationCase):
    def test_status_p95_with_1000_history_and_20_active_is_under_50ms(self) -> None:
        if os.environ.get("OVERMIND_V2_SKIP_PERF") == "1":
            self.skipTest("OVERMIND_V2_SKIP_PERF=1")

        # Keep each fan-out bounded while constructing the public fixture through the CLI.
        for batch in range(20):
            created = self.harness.run_many(
                [self.harness.job_spec(f"history-{batch}-{item}") for item in range(50)],
                key=f"perf-history-{batch}",
                label=f"history-{batch}",
            )
            group_id = ids_from(created, "group")[0]
            self.harness.call(
                "await",
                {**group_target(group_id), "condition": "all_terminal", "since_cursor": cursor_from(created), "timeout": 10},
                timeout=15,
            )

        active = self.harness.run_many(
            [self.harness.job_spec(f"active-{item}", mode="hold") for item in range(20)],
            key="perf-active",
            label="active",
        )
        active_ids = set(ids_from(active, "job"))
        self.assertEqual(20, len(active_ids))

        # Warm the daemon and page cache, then include CLI transport in the measured status latency.
        self.harness.call("jobs", {"active": True, "limit": 100})
        timings: list[float] = []
        for _ in range(40):
            result = self.harness.call("jobs", {"active": True, "limit": 100})
            timings.append(result.elapsed_ms)
            listed = set(ids_from(result.json(), "job"))
            self.assertEqual(active_ids, listed)
        p95 = percentile(timings, 0.95)
        self.assertLess(p95, 50.0, f"status p95={p95:.2f}ms; samples={timings}")

        history = self.harness.call("jobs", {"terminal": True, "limit": 2_000}).json()
        terminal_states = list(recursive_values(history, {"state"}))
        self.assertGreaterEqual(len(terminal_states), 1_000)


if __name__ == "__main__":
    unittest.main()
