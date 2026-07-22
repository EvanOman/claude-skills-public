from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from support import (
    CANONICAL,
    TERMINAL,
    V1_MCP_NAMES,
    ContractFailure,
    IntegrationCase,
    McpClient,
    canonical_tool,
    cursor_from,
    first_value,
    ids_from,
)


def structured(response: dict[str, object]) -> dict[str, object]:
    result = response.get("result", {})
    if not isinstance(result, dict) or not isinstance(result.get("structuredContent"), dict):
        raise ContractFailure(f"MCP response has no structuredContent: {response!r}")
    return result["structuredContent"]


def vm_high_water_kib(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1])
    raise ContractFailure(f"process {pid} did not expose VmHWM")


class McpContractTest(IntegrationCase):
    def setUp(self) -> None:
        super().setUp()
        self.mcp = McpClient(self.harness.mcp, self.harness.env)

    def tearDown(self) -> None:
        self.mcp.close()
        super().tearDown()

    def test_discovery_advertises_only_canonical_operations(self) -> None:
        tools = self.mcp.tools()
        names = [tool["name"] for tool in tools]
        canonical = {canonical_tool(name) for name in names}
        self.assertEqual(CANONICAL, canonical)
        self.assertEqual(len(names), len(set(names)))
        legacy = canonical & V1_MCP_NAMES
        self.assertFalse(legacy, f"v1 compatibility aliases leaked into discovery: {legacy}")
        for tool in tools:
            self.assertIsInstance(tool.get("inputSchema"), dict, tool)

    def test_structured_outputs_and_progress_cover_group_lifecycle(self) -> None:
        request = {
            "group": {"label": "mcp"},
            "jobs": [
                self.harness.job_spec("one", delay=0.08),
                self.harness.job_spec("two", delay=0.25),
            ],
            "idempotency_key": "mcp-group",
        }
        launched = self.mcp.call_tool("run-many", request)
        launch_result = launched.get("result", {})
        self.assertIsInstance(launch_result.get("structuredContent"), dict, launched)
        structured = launch_result["structuredContent"]
        group_id = ids_from(structured, "group")[0]
        cursor = cursor_from(structured)

        awaited = self.mcp.call_tool(
            "await",
            {
                "target": {"group_id": group_id},
                "condition": "all_terminal",
                "since_cursor": cursor,
                "timeout": 3,
            },
            progress_token="progress-mcp-group",
            timeout=5,
        )
        await_result = awaited.get("result", {})
        self.assertIsInstance(await_result.get("structuredContent"), dict, awaited)
        progress = [
            message
            for message in self.mcp.notifications
            if message.get("method") == "notifications/progress"
            and message.get("params", {}).get("progressToken") == "progress-mcp-group"
        ]
        self.assertTrue(progress, self.mcp.notifications)
        cursors = [first_value(item, "cursor", "event_cursor", "eventCursor") for item in progress]
        cursors = [item for item in cursors if isinstance(item, int)]
        self.assertEqual(sorted(set(cursors)), cursors, progress)

        collected = self.mcp.call_tool(
            "collect", {"target": {"group_id": group_id}, "preview_bytes": 64}
        )
        collect_result = collected.get("result", {})
        self.assertIsInstance(collect_result.get("structuredContent"), dict, collected)
        self.assertEqual(2, len(ids_from(collect_result["structuredContent"], "job")))

    def test_jobs_since_cursor_returns_only_jobs_changed_after_cursor(self) -> None:
        old = structured(
            self.mcp.call_tool(
                "run-many",
                {
                    "group": {"label": "cursor-old"},
                    "jobs": [self.harness.job_spec("cursor-old")],
                    "idempotency_key": "cursor-old",
                },
            )
        )
        old_group = ids_from(old, "group")[0]
        settled = structured(
            self.mcp.call_tool(
                "await",
                {
                    "target": {"group_id": old_group},
                    "condition": "all_terminal",
                    "since_cursor": cursor_from(old),
                    "timeout": 3,
                },
            )
        )
        boundary = cursor_from(settled)
        unchanged = structured(self.mcp.call_tool("jobs", {"since_cursor": boundary, "limit": 50}))
        self.assertEqual([], unchanged.get("jobs"), unchanged)

        new = structured(
            self.mcp.call_tool(
                "run-many",
                {
                    "group": {"label": "cursor-new"},
                    "jobs": [self.harness.job_spec("cursor-new", mode="hold")],
                    "idempotency_key": "cursor-new",
                },
            )
        )
        changed = structured(self.mcp.call_tool("jobs", {"since_cursor": boundary, "limit": 50}))
        self.assertEqual(set(ids_from(new, "job")), set(ids_from(changed, "job")))

    def test_mcp_mutation_rejects_unique_short_id_with_typed_error(self) -> None:
        launched = structured(
            self.mcp.call_tool(
                "run-many",
                {
                    "group": {"label": "mcp-short"},
                    "jobs": [self.harness.job_spec("mcp-short", mode="hold")],
                    "idempotency_key": "mcp-short",
                },
            )
        )
        group_id = ids_from(launched, "group")[0]
        job_id = ids_from(launched, "job")[0]
        short = next(
            job_id[:size]
            for size in range(7, 0, -1)
            if not group_id.startswith(job_id[:size])
        )
        response = self.mcp.call_tool("stop", {"target": short})
        result = response.get("result", {})
        self.assertTrue(isinstance(result, dict) and result.get("isError"), response)
        error = structured(response).get("error")
        self.assertIsInstance(error, dict, response)
        assert isinstance(error, dict)
        code = str(error.get("code", ""))
        self.assertNotIn(code, {"", "broker_error", "overmind_error", "internal_error"})
        self.assertTrue("target" in code or "id" in code or "ambiguous" in code, error)
        shown = structured(self.mcp.call_tool("show", {"target": {"job_id": job_id}}))
        self.assertNotIn(first_value(shown, "state"), TERMINAL)

    def test_typed_broker_conflict_survives_mcp_boundary(self) -> None:
        payload = {
            "group": {"label": "typed-conflict"},
            "jobs": [self.harness.job_spec("typed-conflict", mode="hold")],
            "idempotency_key": "typed-conflict",
        }
        self.mcp.call_tool("run-many", payload)
        changed = json.loads(json.dumps(payload))
        changed["jobs"][0]["brief"] = "different logical request"
        response = self.mcp.call_tool("run-many", changed)
        error = structured(response).get("error")
        self.assertIsInstance(error, dict, response)
        assert isinstance(error, dict)
        code = str(error.get("code", ""))
        self.assertNotIn(code, {"", "broker_error", "overmind_error", "internal_error"})
        self.assertIn("conflict", code, error)

    def test_collect_excludes_nonterminal_by_default(self) -> None:
        held = structured(
            self.mcp.call_tool(
                "run-many",
                {
                    "group": {"label": "collect-held"},
                    "jobs": [self.harness.job_spec("collect-held", mode="hold")],
                    "idempotency_key": "collect-held",
                },
            )
        )
        held_job = ids_from(held, "job")[0]
        excluded = structured(
            self.mcp.call_tool("collect", {"target": {"job_id": held_job}, "preview_bytes": 64})
        )
        self.assertEqual([], excluded.get("results"), excluded)
        included = structured(
            self.mcp.call_tool(
                "collect",
                {
                    "target": {"job_id": held_job},
                    "preview_bytes": 64,
                    "include_nonterminal": True,
                },
            )
        )
        self.assertEqual(1, len(included.get("results", [])), included)

    def test_collect_streams_a_bounded_preview_from_a_large_sparse_artifact(self) -> None:
        large = structured(
            self.mcp.call_tool(
                "run-many",
                {
                    "group": {"label": "collect-sparse"},
                    "jobs": [self.harness.job_spec("collect-sparse")],
                    "idempotency_key": "collect-sparse",
                },
            )
        )
        large_group = ids_from(large, "group")[0]
        large_job = ids_from(large, "job")[0]
        self.mcp.call_tool(
            "await",
            {
                "target": {"group_id": large_group},
                "condition": "all_terminal",
                "since_cursor": cursor_from(large),
                "timeout": 3,
            },
        )
        shown = structured(self.mcp.call_tool("show", {"target": {"job_id": large_job}}))
        artifact = Path(str(first_value(shown, "result_path", "resultPath")))
        with artifact.open("r+b") as stream:
            stream.seek(0)
            stream.write(b"HEAD:")
            stream.truncate(128 * 1024 * 1024)
            stream.seek(-5, 2)
            stream.write(b":TAIL")

        doctor = structured(self.mcp.call_tool("doctor", {}))
        pid = first_value(doctor, "daemon_pid", "daemonPid", "pid")
        self.assertIsInstance(pid, int, doctor)
        assert isinstance(pid, int)
        before = vm_high_water_kib(pid)
        collected = structured(
            self.mcp.call_tool(
                "collect", {"target": {"job_id": large_job}, "preview_bytes": 128}
            )
        )
        growth = vm_high_water_kib(pid) - before
        self.assertLess(growth, 32 * 1024, f"collect raised daemon VmHWM by {growth} KiB")
        previews = [
            value
            for value in (first_value(item, "preview") for item in collected.get("results", []))
            if isinstance(value, str)
        ]
        self.assertEqual(1, len(previews), collected)
        self.assertTrue(previews[0].startswith("HEAD:"), previews)
        self.assertLessEqual(len(previews[0].encode()), 128)
        self.assertNotIn(":TAIL", previews[0])

    def test_partial_launch_error_exposes_ids_and_keeps_successful_sibling_observable(self) -> None:
        response = self.mcp.call_tool(
            "run-many",
            {
                "group": {"label": "partial-launch"},
                "jobs": [
                    self.harness.job_spec("partial-ok", mode="hold"),
                    self.harness.job_spec("partial-error", mode="launch-error"),
                ],
                "idempotency_key": "partial-launch",
            },
        )
        result = response.get("result", {})
        self.assertTrue(isinstance(result, dict) and result.get("isError"), response)
        error = structured(response).get("error")
        self.assertIsInstance(error, dict, response)
        assert isinstance(error, dict)
        data = error.get("data")
        self.assertIsInstance(data, dict, error)
        assert isinstance(data, dict)
        group_ids = ids_from(data, "group")
        job_ids = ids_from(data, "job")
        self.assertEqual(1, len(group_ids), data)
        self.assertEqual(2, len(job_ids), data)
        listed = structured(self.mcp.call_tool("jobs", {"group_id": group_ids[0]}))
        self.assertIn(job_ids[0], ids_from(listed, "job"), listed)
        snapshots = listed.get("jobs", [])
        self.assertTrue(
            any(
                isinstance(item, dict)
                and item.get("id") == job_ids[0]
                and item.get("provider_job_id")
                for item in snapshots
            ),
            listed,
        )

    def test_await_heartbeat_and_cancel_progress_are_cursor_monotonic(self) -> None:
        launched = structured(
            self.mcp.call_tool(
                "run-many",
                {
                    "group": {"label": "cancel-progress"},
                    "jobs": [
                        self.harness.job_spec("cancel-quick", delay=0.05),
                        self.harness.job_spec("cancel-held", mode="hold"),
                    ],
                    "idempotency_key": "cancel-progress",
                },
            )
        )
        group_id = ids_from(launched, "group")[0]
        token = "cancel-progress-token"
        request_id = self.mcp.begin_request(
            "tools/call",
            {
                "name": self.mcp.tool_name("await"),
                "arguments": {
                    "target": {"group_id": group_id},
                    "condition": "all_terminal",
                    "since_cursor": cursor_from(launched),
                    "timeout": 30,
                },
                "_meta": {"progressToken": token},
            },
        )
        amounts: list[float] = []
        top_level_cursors: list[int] = []
        last_cursors: list[int] = []
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            message = self.mcp.read_message(timeout=1)
            if message.get("id") == request_id:
                self.fail(f"await completed before cancellation: {message!r}")
            self.mcp.notifications.append(message)
            params = message.get("params", {})
            if message.get("method") != "notifications/progress" or params.get("progressToken") != token:
                continue
            amount = params.get("progress")
            if isinstance(amount, (int, float)):
                amounts.append(float(amount))
            cursor = params.get("cursor")
            if isinstance(cursor, int):
                top_level_cursors.append(cursor)
            last_cursor = params.get("lastCursor")
            if isinstance(last_cursor, int):
                last_cursors.append(last_cursor)
            data = params.get("data")
            if (
                top_level_cursors
                and last_cursors
                and isinstance(data, dict)
                and data.get("heartbeat") is True
            ):
                break
        else:
            self.fail(f"did not observe meaningful progress followed by heartbeat: {self.mcp.notifications!r}")

        self.assertEqual(sorted(set(top_level_cursors)), top_level_cursors, top_level_cursors)
        self.assertTrue(last_cursors, self.mcp.notifications)
        self.assertTrue(
            all(cursor == top_level_cursors[-1] for cursor in last_cursors),
            (top_level_cursors, last_cursors),
        )
        self.mcp.notify("notifications/cancelled", {"requestId": request_id})
        cancelled = self.mcp.wait_for_response(request_id, timeout=3, method="await cancellation")
        self.assertEqual(-32800, cancelled.get("error", {}).get("code"), cancelled)
        last_progress = cancelled.get("error", {}).get("data", {}).get("last_progress", {})
        self.assertGreaterEqual(cursor_from(last_progress), top_level_cursors[-1])
        self.assertEqual(sorted(amounts), amounts, amounts)
        self.mcp.call_tool("stop", {"target": {"group_id": group_id}})


if __name__ == "__main__":
    unittest.main()
