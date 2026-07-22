from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

from support import (
    IntegrationCase,
    McpClient,
    cursor_from,
    first_value,
    ids_from,
    percentile,
    recursive_values,
    state_from,
)


def group_target(group_id: str) -> dict[str, object]:
    return {"target": {"group_id": group_id}}


def processes_with_environment(marker: bytes) -> list[int]:
    matches: list[int] = []
    for process in Path("/proc").glob("[0-9]*"):
        try:
            environment = (process / "environ").read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if marker in environment:
            matches.append(int(process.name))
    return matches


def file_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


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

    def test_shutdown_with_eighteen_held_jobs_leaves_no_workers_or_file_recreation(self) -> None:
        launched = self.harness.call(
            "run-many",
            {
                "group": {"label": "shutdown-eighteen"},
                "jobs": [
                    self.harness.job_spec(f"shutdown-{item}", mode="hold")
                    for item in range(18)
                ],
                "idempotency_key": "shutdown-eighteen",
            },
            timeout=60,
        ).json()
        self.assertEqual(18, len(ids_from(launched, "job")), launched)
        deadline = time.monotonic() + 5
        while len(self.harness.provider_calls("launch")) < 18 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(18, len(self.harness.provider_calls("launch")))
        doctor = self.harness.call("doctor").json()
        pid = first_value(doctor, "daemon_pid", "daemonPid", "pid")
        self.assertIsInstance(pid, int, doctor)
        assert isinstance(pid, int)
        self.harness.terminate_test_daemon(pid)

        marker = f"OVERMIND_V2_FAKE_STATE_DIR={self.harness.provider_state}".encode()
        self.assertEqual([], processes_with_environment(marker))
        before = file_snapshot(self.harness.provider_state)
        socket = self.harness.state / "overmind.sock"
        self.assertFalse(socket.exists(), socket)
        time.sleep(0.5)
        self.assertEqual([], processes_with_environment(marker))
        self.assertEqual(before, file_snapshot(self.harness.provider_state))
        self.assertFalse(socket.exists(), socket)


class StatusPerformanceTest(IntegrationCase):
    def test_persistent_mcp_status_p95_with_1000_history_and_20_active_is_under_50ms(self) -> None:
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

        # Gate the long-lived MCP lifecycle path. CLI process startup is sampled
        # separately below as diagnostic information, never as the latency SLO.
        mcp = McpClient(self.harness.mcp, self.harness.env)
        active_filter = {"state": ["queued", "starting", "running"], "limit": 100}
        try:
            mcp.call_tool("jobs", active_filter)
            timings: list[float] = []
            for _ in range(40):
                started = time.perf_counter()
                response = mcp.call_tool("jobs", active_filter)
                timings.append((time.perf_counter() - started) * 1000)
                listed = set(ids_from(response["result"]["structuredContent"], "job"))
                self.assertEqual(active_ids, listed)
        finally:
            mcp.close()
        persistent_p95 = percentile(timings, 0.95)
        cold_cli_samples = [
            self.harness.call("jobs", active_filter).elapsed_ms
            for _ in range(10)
        ]
        cold_cli_p95 = percentile(cold_cli_samples, 0.95)
        self.assertLess(
            persistent_p95,
            50.0,
            f"persistent MCP p95={persistent_p95:.2f}ms; "
            f"cold CLI p95={cold_cli_p95:.2f}ms; samples={timings}",
        )

        history = self.harness.call("jobs", {"terminal": True, "limit": 2_000}).json()
        terminal_states = list(recursive_values(history, {"state"}))
        self.assertGreaterEqual(len(terminal_states), 1_000)


if __name__ == "__main__":
    unittest.main()
