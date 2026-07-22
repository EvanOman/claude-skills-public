"""Human command-line client for the Overmind v2 broker."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .client import DaemonClient, OvermindError


ID_PATTERN = re.compile(
    r"(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\Z"
)


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument(
        "--input",
        default=argparse.SUPPRESS,
        metavar="FILE",
        help="use a complete JSON request object from FILE, or - for stdin",
    )
    parser.add_argument("--state-dir", default=argparse.SUPPRESS)
    parser.add_argument("--no-autostart", action="store_true", default=argparse.SUPPRESS)
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="om",
        description="Control persistent Claude and Codex workers through Overmind v2.",
        parents=[common],
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser(
        "run", aliases=["spawn", "start"], parents=[common], help="launch one worker"
    )
    run.set_defaults(operation="run")
    run.add_argument("brief", nargs="?", help="worker brief, or - to read stdin")
    run.add_argument("--provider")
    run.add_argument("-C", "--cwd", default=os.getcwd())
    run.add_argument("--label", default="worker")
    run.add_argument("--model")
    run.add_argument("--group-id")
    run.add_argument("--parent-job-id")
    run.add_argument(
        "--billing-class",
        choices=["subscription-native", "explicit-metered", "unknown"],
        default="subscription-native",
    )
    run.add_argument("--idempotency-key")

    run_many = commands.add_parser(
        "run-many", aliases=["run_many"], parents=[common], help="launch a JSON job list"
    )
    run_many.set_defaults(operation="run_many")
    run_many.add_argument("spec", nargs="?", help="JSON array/object file, or - to read stdin")
    run_many.add_argument("--label")
    run_many.add_argument("--idempotency-key")

    jobs = commands.add_parser(
        "jobs", aliases=["ps", "ls", "list"], parents=[common], help="list job snapshots"
    )
    jobs.set_defaults(operation="jobs")
    jobs.add_argument("--group-id")
    jobs.add_argument("--state")
    jobs.add_argument("--provider")
    jobs.add_argument("--label")
    jobs.add_argument("--since-cursor", type=int)
    jobs.add_argument("--limit", type=int)

    show = commands.add_parser(
        "show", aliases=["status"], parents=[common], help="show one job or group"
    )
    show.set_defaults(operation="show")
    show.add_argument("target", nargs="?")
    show.add_argument("--fresh", action="store_true")

    wait = commands.add_parser(
        "await", aliases=["wait", "join"], parents=[common], help="wait on a job or group"
    )
    wait.set_defaults(operation="await")
    wait.add_argument("target", nargs="?")
    wait.add_argument(
        "--condition",
        choices=["any_change", "any_terminal", "all_terminal"],
        default="all_terminal",
    )
    wait.add_argument("--since-cursor", type=int, default=0)
    wait.add_argument("--timeout", "--timeout-seconds", dest="timeout", type=float, default=3600)

    collect = commands.add_parser(
        "collect",
        aliases=["result", "last"],
        parents=[common],
        help="collect bounded result previews",
    )
    collect.set_defaults(operation="collect")
    collect.add_argument("targets", nargs="*")
    collect.add_argument("--max-chars", type=int, default=4000)
    collect.add_argument("--include-nonterminal", action="store_true")

    reply = commands.add_parser(
        "reply",
        aliases=["continue", "followup"],
        parents=[common],
        help="steer or continue a worker",
    )
    reply.set_defaults(operation="reply")
    reply.add_argument("target", nargs="?")
    reply.add_argument("prompt", nargs="?", help="follow-up prompt, or - to read stdin")
    reply.add_argument("--label")
    reply.add_argument("--idempotency-key")

    stop = commands.add_parser(
        "stop",
        aliases=["cancel", "interrupt"],
        parents=[common],
        help="interrupt a job or group",
    )
    stop.set_defaults(operation="stop")
    stop.add_argument("target", nargs="?")
    stop.add_argument("--idempotency-key")

    forget = commands.add_parser(
        "forget",
        aliases=["rm", "cleanup"],
        parents=[common],
        help="delete terminal lifecycle metadata",
    )
    forget.set_defaults(operation="forget")
    forget.add_argument("target", nargs="?")
    forget.add_argument("--idempotency-key")

    doctor = commands.add_parser("doctor", parents=[common], help="report broker capabilities")
    doctor.set_defaults(operation="doctor")
    return parser


def _read_text(value: str) -> str:
    text = sys.stdin.read() if value == "-" else value
    if not text.strip():
        raise OvermindError("input must not be empty", code="invalid_input")
    return text


def _read_json(value: str) -> Any:
    try:
        raw = sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")
    except OSError as error:
        raise OvermindError(f"cannot read JSON spec: {error}", code="invalid_input") from error
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise OvermindError(f"invalid JSON spec: {error}", code="invalid_input") from error


def _set(params: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        params[key] = value


def _require_unambiguous_id(target: str, operation: str) -> None:
    if not ID_PATTERN.fullmatch(target):
        raise OvermindError(
            f"{operation} requires a full UUID or exact 8-character short ID",
            code="ambiguous_target",
        )


def request_for(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    operation = args.operation
    input_path = getattr(args, "input", None)
    if input_path is not None:
        value = _read_json(input_path)
        if not isinstance(value, dict):
            raise OvermindError("--input must contain a JSON object", code="invalid_input")
        return "run-many" if operation == "run_many" else operation, value

    params: dict[str, Any]
    if operation == "run":
        if not args.provider or args.brief is None:
            raise OvermindError(
                "run requires --provider and a brief unless --input is used",
                code="invalid_input",
            )
        params = {
            "provider": args.provider,
            "brief": _read_text(args.brief),
            "cwd": str(Path(args.cwd).expanduser().resolve()),
            "label": args.label,
            "billing_class": args.billing_class,
        }
        for key in ("model", "group_id", "parent_job_id", "idempotency_key"):
            _set(params, key, getattr(args, key))
    elif operation == "run_many":
        if args.spec is None:
            raise OvermindError(
                "run-many requires a JSON spec unless --input is used",
                code="invalid_input",
            )
        value = _read_json(args.spec)
        if isinstance(value, list):
            params = {"jobs": value}
        elif isinstance(value, dict):
            params = dict(value)
        else:
            raise OvermindError("run-many spec must be a JSON array or object", code="invalid_input")
        _set(params, "label", args.label)
        _set(params, "idempotency_key", args.idempotency_key)
        operation = "run-many"
    elif operation == "jobs":
        params = {}
        for key in ("group_id", "state", "provider", "label", "limit"):
            _set(params, key, getattr(args, key))
        _set(params, "after_cursor", args.since_cursor)
    elif operation == "show":
        if args.target is None:
            raise OvermindError("show requires a target unless --input is used", code="invalid_input")
        params = {"target": args.target}
        if args.fresh:
            params["fresh"] = True
    elif operation == "await":
        if args.target is None:
            raise OvermindError("await requires a target unless --input is used", code="invalid_input")
        params = {
            "target": args.target,
            "condition": args.condition,
            "since_cursor": args.since_cursor,
            "timeout": args.timeout,
        }
    elif operation == "collect":
        if not args.targets:
            raise OvermindError("collect requires a target unless --input is used", code="invalid_input")
        params = {"max_chars": args.max_chars}
        if len(args.targets) == 1:
            params["target"] = args.targets[0]
        else:
            params["job_ids"] = args.targets
        if args.include_nonterminal:
            params["include_nonterminal"] = True
    elif operation == "reply":
        if args.target is None or args.prompt is None:
            raise OvermindError(
                "reply requires a target and prompt unless --input is used",
                code="invalid_input",
            )
        _require_unambiguous_id(args.target, operation)
        params = {"job_id": args.target, "prompt": _read_text(args.prompt)}
        _set(params, "label", args.label)
        _set(params, "idempotency_key", args.idempotency_key)
    elif operation in {"stop", "forget"}:
        if args.target is None:
            raise OvermindError(
                f"{operation} requires a target unless --input is used",
                code="invalid_input",
            )
        _require_unambiguous_id(args.target, operation)
        params = {"target": args.target}
        _set(params, "idempotency_key", args.idempotency_key)
    elif operation == "doctor":
        params = {}
    else:  # pragma: no cover - argparse owns this invariant
        raise OvermindError(f"unsupported operation: {operation}", code="invalid_request")
    return operation, params


def _display_id(value: Any) -> str:
    text = str(value or "-")
    return text[:8] if ID_PATTERN.fullmatch(text) and len(text) > 8 else text


def _job_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    jobs = value.get("jobs")
    if isinstance(jobs, list):
        return [item for item in jobs if isinstance(item, dict)]
    if "job_id" in value:
        return [value]
    return []


def render_human(operation: str, value: Any) -> str:
    if not isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    rows = _job_rows(value)
    lines: list[str] = []
    group_id = value.get("group_id")
    if group_id:
        lines.append(f"GROUP {_display_id(group_id)}")
    if rows:
        lines.append("ID       STATE        PROVIDER  LABEL")
        for job in rows:
            lines.append(
                f"{_display_id(job.get('job_id')):<8} "
                f"{str(job.get('state', '-')):<12} "
                f"{str(job.get('provider', '-')):<9} "
                f"{job.get('label', '')}"
            ).rstrip()
        cursor = value.get("cursor")
        if cursor is not None:
            lines.append(f"CURSOR {cursor}")
        return "\n".join(lines)

    if operation == "collect":
        items = value.get("results") or value.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"{_display_id(item.get('job_id'))} {item.get('state', '-')}: "
                    f"{item.get('preview') or item.get('result') or ''}"
                )
            if lines:
                return "\n".join(lines)

    if operation == "doctor":
        daemon = value.get("daemon")
        if isinstance(daemon, dict):
            lines.append(f"daemon: {daemon.get('status', daemon.get('state', 'unknown'))}")
        providers = value.get("providers")
        if isinstance(providers, dict):
            for name, details in providers.items():
                if isinstance(details, dict):
                    status = details.get("available", details.get("status", "unknown"))
                    billing = details.get("billing_class", "unknown")
                    lines.append(f"{name}: {status} ({billing})")
        if lines:
            return "\n".join(lines)

    preferred = (
        "job_id",
        "group_id",
        "state",
        "condition",
        "cursor",
        "fresh",
        "message",
        "artifact_path",
    )
    for key in preferred:
        if key in value and value[key] is not None:
            shown = _display_id(value[key]) if key.endswith("_id") else value[key]
            lines.append(f"{key.upper()}={shown}")
    if lines:
        return "\n".join(lines)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        operation, params = request_for(args)
        client = DaemonClient(
            getattr(args, "state_dir", None),
            autostart=not getattr(args, "no_autostart", False),
        )
        result = client.request(operation, params)
        if getattr(args, "json", False):
            json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
            sys.stdout.write("\n")
        else:
            sys.stdout.write(render_human(operation, result) + "\n")
        return 0
    except OvermindError as error:
        if getattr(args, "json", False):
            payload: dict[str, Any] = {"error": {"code": error.code, "message": str(error)}}
            if error.data is not None:
                payload["error"]["data"] = error.data
            json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
            sys.stdout.write("\n")
        else:
            print(f"om: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
