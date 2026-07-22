#!/usr/bin/env python3
"""Reproducible black-box Overmind v1/v2 orchestration bake-off."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


SKILL = Path(__file__).resolve().parents[1]
TESTS = SKILL / "tests"
sys.path.insert(0, str(TESTS))

from support import (  # noqa: E402
    ContractFailure,
    Harness,
    McpClient,
    V1_MCP,
    cursor_from,
    first_value,
    ids_from,
    percentile,
    require_entrypoints,
    state_from,
)


WORKERS = ("alpha", "beta", "gamma", "delta")


def structured(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result", {})
    value = result.get("structuredContent")
    if not isinstance(value, dict):
        raise ContractFailure(f"MCP response has no structuredContent: {response!r}")
    return value


def run_v1() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="overmind-v1-bakeoff.") as temporary:
        root = Path(temporary)
        env = dict(os.environ)
        env.update(
            OVERMIND_STATE_DIR=str(root / "state"),
            OVERMIND_CODEX_BIN=str(TESTS / "fake_v1_codex.py"),
            PYTHONUNBUFFERED="1",
        )
        client = McpClient(V1_MCP, env)
        operations: list[str] = []
        probes: list[str] = []
        mechanic_timings: dict[str, list[float]] = {
            "spawn": [], "list": [], "status": [], "wait": [], "result": []
        }
        started = time.perf_counter()
        try:
            jobs: list[str] = []
            for label in WORKERS:
                operation_started = time.perf_counter()
                response = client.call_tool(
                    "spawn",
                    {
                        "provider": "codex",
                        "brief": f"deterministic bakeoff {label}",
                        "cwd": str(root),
                        "label": label,
                    },
                )
                mechanic_timings["spawn"].append((time.perf_counter() - operation_started) * 1000)
                operations.append("spawn")
                jobs.append(ids_from(structured(response), "job")[0])
            states: list[str | None] = []
            results: list[str] = []
            for job_id in jobs:
                operation_started = time.perf_counter()
                response = client.call_tool("wait", {"job_id": job_id, "timeout_seconds": 5})
                mechanic_timings["wait"].append((time.perf_counter() - operation_started) * 1000)
                operations.append("wait")
                states.append(state_from(structured(response)))
            for job_id in jobs:
                operation_started = time.perf_counter()
                response = client.call_tool("result", {"job_id": job_id})
                mechanic_timings["result"].append((time.perf_counter() - operation_started) * 1000)
                operations.append("result")
                results.append(str(first_value(structured(response), "result") or ""))
            mission_elapsed = (time.perf_counter() - started) * 1000
            probe_started = time.perf_counter()
            client.call_tool("list", {})
            mechanic_timings["list"].append((time.perf_counter() - probe_started) * 1000)
            probes.append("list")
            for job_id in jobs:
                probe_started = time.perf_counter()
                client.call_tool("status", {"job_id": job_id})
                mechanic_timings["status"].append((time.perf_counter() - probe_started) * 1000)
                probes.append("status")
        finally:
            client.close()
        return {
            "elapsed_ms": mission_elapsed,
            "lifecycle_call_count": len(operations),
            "operations": operations,
            "verification_operations": probes,
            "mechanic_timings_ms": mechanic_timings,
            "worker_count": len(jobs),
            "terminal_count": sum(state == "succeeded" for state in states),
            "result_count": sum(bool(value) for value in results),
            "model_driven_polling": operations.count("wait") > 1,
        }


def populate_performance_fixture(
    harness: Harness, history: int, active: int
) -> dict[str, float]:
    batch_size = 50
    for offset in range(0, history, batch_size):
        size = min(batch_size, history - offset)
        created = harness.run_many(
            [harness.job_spec(f"bake-history-{offset + item}") for item in range(size)],
            key=f"bake-history-{offset}",
            label=f"bake-history-{offset}",
        )
        group_id = ids_from(created, "group")[0]
        harness.call(
            "await",
            {
                "target": {"group_id": group_id},
                "condition": "all_terminal",
                "since_cursor": cursor_from(created),
                "timeout": 10,
            },
            timeout=15,
        )
    active_group = harness.run_many(
        [harness.job_spec(f"bake-active-{item}", mode="hold") for item in range(active)],
        key="bake-active",
        label="bake-active",
    )
    expected = set(ids_from(active_group, "job"))
    arguments = {
        "state": ["queued", "starting", "running"],
        "limit": max(100, active),
    }
    client = McpClient(harness.mcp, harness.env)
    try:
        client.call_tool("jobs", arguments)
        persistent_samples: list[float] = []
        for _ in range(40):
            started = time.perf_counter()
            response = client.call_tool("jobs", arguments)
            persistent_samples.append((time.perf_counter() - started) * 1000)
            if set(ids_from(structured(response), "job")) != expected:
                raise ContractFailure("status fixture did not return exactly the active jobs")
    finally:
        client.close()

    cold_cli_samples: list[float] = []
    for _ in range(10):
        response = harness.call("jobs", arguments)
        if set(ids_from(response.json(), "job")) != expected:
            raise ContractFailure("cold CLI status did not return exactly the active jobs")
        cold_cli_samples.append(response.elapsed_ms)
    return {
        "persistent_mcp_p95_ms": percentile(persistent_samples, 0.95),
        "cold_cli_p95_ms": percentile(cold_cli_samples, 0.95),
    }


def run_v2(history: int, active: int, performance: bool) -> dict[str, Any]:
    harness = Harness()
    operations: list[str] = []
    verification_operations: list[str] = []
    mechanic_timings: dict[str, list[float]] = {
        "run-many": [], "jobs": [], "show": [], "await": [], "collect": []
    }
    specs = [harness.job_spec(label) for label in WORKERS]
    payload = {
        "group": {"label": "bakeoff"},
        "jobs": specs,
        "idempotency_key": "bakeoff-four-workers",
    }
    started = time.perf_counter()
    try:
        operation_started = time.perf_counter()
        launched = harness.call("run-many", payload).json()
        mechanic_timings["run-many"].append((time.perf_counter() - operation_started) * 1000)
        operations.append("run-many")
        group_id = ids_from(launched, "group")[0]
        job_ids = ids_from(launched, "job")
        operation_started = time.perf_counter()
        awaited = harness.call(
            "await",
            {
                "target": {"group_id": group_id},
                "condition": "all_terminal",
                "since_cursor": cursor_from(launched),
                "timeout": 5,
            },
        ).json()
        mechanic_timings["await"].append((time.perf_counter() - operation_started) * 1000)
        operations.append("await")
        operation_started = time.perf_counter()
        collected = harness.call(
            "collect", {"target": {"group_id": group_id}, "preview_bytes": 256}
        ).json()
        mechanic_timings["collect"].append((time.perf_counter() - operation_started) * 1000)
        operations.append("collect")
        mission_elapsed = (time.perf_counter() - started) * 1000

        probe_started = time.perf_counter()
        harness.call("jobs", {"group_id": group_id})
        mechanic_timings["jobs"].append((time.perf_counter() - probe_started) * 1000)
        verification_operations.append("jobs")
        for job_id in job_ids:
            probe_started = time.perf_counter()
            harness.call("show", {"target": {"job_id": job_id}})
            mechanic_timings["show"].append((time.perf_counter() - probe_started) * 1000)
            verification_operations.append("show")

        doctor = harness.call("doctor").json()
        verification_operations.append("doctor")
        pid = first_value(doctor, "daemon_pid", "daemonPid", "pid")
        if not isinstance(pid, int):
            raise ContractFailure(f"doctor did not expose daemon PID: {doctor!r}")
        harness.terminate_test_daemon(pid)
        retried = harness.call("run-many", payload).json()
        verification_operations.append("run-many-idempotent-retry")
        restart_idempotency = (
            ids_from(retried, "group") == [group_id]
            and ids_from(retried, "job") == job_ids
            and len(harness.provider_calls("launch")) == len(WORKERS)
        )
        status_latency = (
            populate_performance_fixture(harness, history, active) if performance else None
        )
        terminal_count = sum(
            1 for state in set(str(value) for value in recursive_states(awaited)) if state == "succeeded"
        )
        # Counts are authoritative when present; state cardinality above is only a fallback.
        counts = first_value(awaited, "counts", "state_counts", "stateCounts")
        if isinstance(counts, dict):
            terminal_count = int(counts.get("succeeded", 0))
        return {
            "elapsed_ms": mission_elapsed,
            "lifecycle_call_count": len(operations),
            "verification_call_count": len(verification_operations),
            "operations": operations,
            "verification_operations": verification_operations,
            "mechanic_timings_ms": mechanic_timings,
            "worker_count": len(job_ids),
            "terminal_count": terminal_count,
            "result_count": len(ids_from(collected, "job")),
            "model_driven_polling": "jobs" in operations or operations.count("await") != 1,
            "restart_idempotency": restart_idempotency,
            "persistent_mcp_status_p95_ms": (
                status_latency["persistent_mcp_p95_ms"] if status_latency else None
            ),
            "cold_cli_status_p95_ms": (
                status_latency["cold_cli_p95_ms"] if status_latency else None
            ),
        }
    finally:
        harness.close()


def recursive_states(value: Any) -> list[Any]:
    if isinstance(value, dict):
        result = ([value["state"]] if "state" in value else [])
        for item in value.values():
            result.extend(recursive_states(item))
        return result
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            result.extend(recursive_states(item))
        return result
    return []


def evaluate(v1: dict[str, Any], v2: dict[str, Any], performance: bool) -> dict[str, Any]:
    checks = {
        "equivalent_four_worker_completion": (
            v1["worker_count"] == v2["worker_count"] == 4
            and v1["terminal_count"] == v2["terminal_count"] == 4
            and v1["result_count"] == v2["result_count"] == 4
        ),
        "v2_at_most_three_parent_lifecycle_calls": v2["lifecycle_call_count"] <= 3,
        "v2_no_model_driven_polling": not v2["model_driven_polling"],
        "v2_restart_and_idempotency": bool(v2["restart_idempotency"]),
        "v2_persistent_mcp_status_p95_under_50ms": (
            not performance
            or (
                isinstance(v2["persistent_mcp_status_p95_ms"], (int, float))
                and v2["persistent_mcp_status_p95_ms"] < 50
            )
        ),
    }
    return {"pass": all(checks.values()), "checks": checks, "v1": v1, "v2": v2}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="write one machine-readable JSON report")
    parser.add_argument("--history", type=int, default=1_000)
    parser.add_argument("--active", type=int, default=20)
    parser.add_argument("--skip-performance", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        require_entrypoints()
        v1 = run_v1()
        v2 = run_v2(args.history, args.active, not args.skip_performance)
        report = evaluate(v1, v2, not args.skip_performance)
    except Exception as error:
        report = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print("Overmind v1-v2 bake-off:", "PASS" if report.get("pass") else "FAIL")
        if "checks" in report:
            for name, passed in report["checks"].items():
                print(f"  {'PASS' if passed else 'FAIL'} {name}")
            for version in ("v1", "v2"):
                item = report[version]
                print(
                    f"  {version}: {item['elapsed_ms']:.2f}ms, "
                    f"{item['lifecycle_call_count']} lifecycle calls, {item['operations']}"
                )
            if report["v2"].get("persistent_mcp_status_p95_ms") is not None:
                print(
                    "  v2 persistent MCP status p95: "
                    f"{report['v2']['persistent_mcp_status_p95_ms']:.2f}ms (gated)"
                )
                print(
                    "  v2 cold CLI status p95: "
                    f"{report['v2']['cold_cli_status_p95_ms']:.2f}ms (informational)"
                )
        else:
            print(" ", report["error"])
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
