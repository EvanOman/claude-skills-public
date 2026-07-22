"""Dependency-free MCP stdio adapter for the Overmind v2 broker."""

from __future__ import annotations

import concurrent.futures
import json
import sys
import threading
from dataclasses import dataclass
from typing import Any

from .client import DaemonClient, OvermindError, RequestCancelled


MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "0.2.0"


JOB_PROPERTIES: dict[str, Any] = {
    "provider": {"type": "string", "description": "Provider adapter name."},
    "brief": {"type": "string", "minLength": 1},
    "cwd": {"type": "string", "minLength": 1},
    "label": {"type": "string", "minLength": 1},
    "model": {"type": "string"},
    "parent_job_id": {"type": "string"},
    "billing_class": {
        "enum": ["subscription-native", "explicit-metered", "unknown"]
    },
    "allow_billing_class_change": {
        "type": "boolean",
        "default": False,
        "description": "Explicitly allow fallback to a different billing class.",
    },
}

TARGET_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "group_id": {"type": "string"},
            },
            "additionalProperties": False,
            "minProperties": 1,
            "maxProperties": 1,
        },
    ]
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "run",
        "description": "Launch one durable provider worker.",
        "inputSchema": {
            "type": "object",
            "required": ["provider", "brief", "cwd", "label"],
            "properties": {
                **JOB_PROPERTIES,
                "group_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "run_many",
        "description": "Launch a bounded group of workers atomically.",
        "inputSchema": {
            "type": "object",
            "required": ["jobs"],
            "properties": {
                "jobs": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["provider", "brief", "cwd", "label"],
                        "properties": JOB_PROPERTIES,
                        "additionalProperties": False,
                    },
                },
                "label": {"type": "string"},
                "group": {"type": "object"},
                "allow_billing_class_change": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Explicitly allow provider fallbacks to a different billing class."
                    ),
                },
                "idempotency_key": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "jobs",
        "description": "List concise job snapshots with optional filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "string"},
                "state": {"type": "array", "items": {"type": "string"}},
                "provider": {"type": "array", "items": {"type": "string"}},
                "label": {"type": "string"},
                "since_cursor": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "show",
        "description": "Read one job or group with freshness and artifact metadata.",
        "inputSchema": {
            "type": "object",
            "required": ["target"],
            "properties": {
                "target": TARGET_SCHEMA,
                "fresh": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "await",
        "description": "Wait once for a target condition after an event cursor.",
        "inputSchema": {
            "type": "object",
            "required": ["target"],
            "properties": {
                "target": TARGET_SCHEMA,
                "condition": {
                    "enum": ["any_change", "any_terminal", "all_terminal"],
                    "default": "all_terminal",
                },
                "since_cursor": {"type": "integer", "minimum": 0, "default": 0},
                "timeout": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 86400,
                    "default": 3600,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "collect",
        "description": "Return bounded terminal previews and artifact paths.",
        "inputSchema": {
            "type": "object",
            "anyOf": [{"required": ["target"]}, {"required": ["job_ids"]}],
            "properties": {
                "target": TARGET_SCHEMA,
                "job_ids": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string"},
                },
                "max_chars": {"type": "integer", "minimum": 0, "default": 4000},
                "preview_bytes": {"type": "integer", "minimum": 0},
                "include_nonterminal": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "reply",
        "description": "Steer a running turn or create a related continuation.",
        "inputSchema": {
            "type": "object",
            "required": ["target", "prompt"],
            "properties": {
                "target": TARGET_SCHEMA,
                "prompt": {"type": "string", "minLength": 1},
                "label": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "stop",
        "description": "Interrupt a job or group without deleting its record.",
        "inputSchema": {
            "type": "object",
            "required": ["target"],
            "properties": {
                "target": TARGET_SCHEMA,
                "idempotency_key": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "forget",
        "description": "Delete terminal lifecycle metadata.",
        "inputSchema": {
            "type": "object",
            "required": ["target"],
            "properties": {
                "target": TARGET_SCHEMA,
                "idempotency_key": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "doctor",
        "description": "Report broker, provider, authentication, billing, and quota capabilities.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = {tool["name"] for tool in TOOLS}


@dataclass
class PendingRequest:
    future: concurrent.futures.Future[None]
    cancel_event: threading.Event


class McpServer:
    def __init__(self, client: DaemonClient) -> None:
        self.client = client
        self.output_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.pending: dict[Any, PendingRequest] = {}
        self.control_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="overmind-v2-control"
        )
        self.wait_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=32, thread_name_prefix="overmind-v2-wait"
        )

    def send(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self.output_lock:
            sys.stdout.write(encoded + "\n")
            sys.stdout.flush()

    def respond(self, request_id: Any, result: Any) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def respond_error(
        self,
        request_id: Any,
        code: int,
        message: str,
        data: Any = None,
    ) -> None:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        self.send({"jsonrpc": "2.0", "id": request_id, "error": error})

    def serve(self) -> int:
        try:
            for raw in sys.stdin:
                self.handle_line(raw)
        finally:
            with self.pending_lock:
                active = list(self.pending.values())
            for pending in active:
                pending.cancel_event.set()
            self.control_executor.shutdown(wait=False, cancel_futures=True)
            self.wait_executor.shutdown(wait=False, cancel_futures=True)
        return 0

    def handle_line(self, raw: str) -> None:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as error:
            self.respond_error(None, -32700, f"Parse error: {error.msg}")
            return
        if not isinstance(request, dict):
            self.respond_error(None, -32600, "Invalid Request: expected an object")
            return
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        if not isinstance(params, dict):
            if request_id is not None:
                self.respond_error(request_id, -32602, "Invalid params: expected an object")
            return

        if method == "initialize":
            self.respond(
                request_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "overmind-v2", "version": SERVER_VERSION},
                },
            )
        elif method == "ping":
            self.respond(request_id, {})
        elif method == "tools/list":
            self.respond(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            if request_id is None:
                return
            self.start_tool_call(request_id, params)
        elif method == "notifications/initialized":
            return
        elif method == "notifications/cancelled":
            self.cancel(params.get("requestId"))
        elif request_id is not None:
            self.respond_error(request_id, -32601, f"Method not found: {method}")

    def start_tool_call(self, request_id: Any, params: dict[str, Any]) -> None:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if name not in TOOL_NAMES:
            self.respond(
                request_id,
                self._tool_error("unknown_tool", f"Unknown Overmind v2 tool: {name}"),
            )
            return
        if not isinstance(arguments, dict):
            self.respond(
                request_id,
                self._tool_error("invalid_arguments", "Tool arguments must be an object"),
            )
            return
        cancel_event = threading.Event()
        token = self._progress_token(params) if name == "await" else None
        executor = self.wait_executor if name == "await" else self.control_executor
        future = executor.submit(
            self._call_tool,
            request_id,
            name,
            arguments,
            cancel_event,
            token,
        )
        with self.pending_lock:
            self.pending[request_id] = PendingRequest(future, cancel_event)
        future.add_done_callback(
            lambda completed, rid=request_id: self._forget_pending(rid, completed)
        )

    def _call_tool(
        self,
        request_id: Any,
        name: str,
        arguments: dict[str, Any],
        cancel_event: threading.Event,
        progress_token: Any,
    ) -> None:
        sequence = 0
        last_amount: int | float = 0
        last_payload: str | None = None
        resumable_cursor: int | float | None = None

        def progress(value: dict[str, Any]) -> None:
            nonlocal last_amount, last_payload, resumable_cursor, sequence
            if progress_token is None:
                return
            payload = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            if payload == last_payload:
                return
            last_payload = payload
            sequence += 1
            candidate = value.get("cursor", value.get("last_cursor"))
            numeric_cursor = (
                candidate
                if isinstance(candidate, (int, float)) and not isinstance(candidate, bool)
                else None
            )
            cursor_advanced = numeric_cursor is not None and (
                resumable_cursor is None or numeric_cursor > resumable_cursor
            )
            if numeric_cursor is not None:
                resumable_cursor = (
                    numeric_cursor
                    if resumable_cursor is None
                    else max(resumable_cursor, numeric_cursor)
                )
                amount = max(last_amount, resumable_cursor)
            else:
                amount = max(last_amount, sequence)
            last_amount = amount
            params: dict[str, Any] = {
                "progressToken": progress_token,
                "progress": amount,
            }
            if resumable_cursor is not None:
                if "cursor" in value and cursor_advanced:
                    params["cursor"] = resumable_cursor
                else:
                    params["lastCursor"] = resumable_cursor
            params["data"] = value
            params["message"] = payload
            total = value.get("total")
            if isinstance(total, (int, float)):
                params["total"] = total
            self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})

        try:
            broker_method = "run-many" if name == "run_many" else name
            result = self.client.request(
                broker_method,
                arguments,
                cancel_event=cancel_event,
                on_progress=progress if name == "await" else None,
            )
            structured = self._structured(result)
            self.respond(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(structured, ensure_ascii=False, indent=2),
                        }
                    ],
                    "structuredContent": structured,
                    "isError": False,
                },
            )
        except RequestCancelled as error:
            self.respond_error(request_id, -32800, str(error), error.data)
        except OvermindError as error:
            self.respond(request_id, self._tool_error(error.code, str(error), error.data))
        except Exception as error:  # keep stdio alive after an unexpected client failure
            self.respond(request_id, self._tool_error("internal_error", str(error)))

    def cancel(self, request_id: Any) -> None:
        with self.pending_lock:
            pending = self.pending.get(request_id)
        if pending is not None:
            pending.cancel_event.set()

    def _forget_pending(
        self, request_id: Any, future: concurrent.futures.Future[None]
    ) -> None:
        with self.pending_lock:
            current = self.pending.get(request_id)
            if current is not None and current.future is future:
                self.pending.pop(request_id, None)

    @staticmethod
    def _progress_token(params: dict[str, Any]) -> Any:
        metadata = params.get("_meta")
        if isinstance(metadata, dict) and "progressToken" in metadata:
            return metadata["progressToken"]
        return params.get("progressToken")

    @staticmethod
    def _structured(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {"value": value}

    @staticmethod
    def _tool_error(code: str, message: str, data: Any = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        structured = {"error": error}
        return {
            "content": [{"type": "text", "text": f"{code}: {message}"}],
            "structuredContent": structured,
            "isError": True,
        }


def main() -> int:
    try:
        return McpServer(DaemonClient()).serve()
    except OvermindError as error:
        print(f"overmind-v2-mcp: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
