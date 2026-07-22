from __future__ import annotations

import unittest

from support import (
    CANONICAL,
    V1_MCP_NAMES,
    IntegrationCase,
    McpClient,
    canonical_tool,
    cursor_from,
    first_value,
    ids_from,
)


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


if __name__ == "__main__":
    unittest.main()
