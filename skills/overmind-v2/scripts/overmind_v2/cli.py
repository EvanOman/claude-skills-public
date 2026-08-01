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
    run.add_argument(
        "--allow-billing-class-change",
        action="store_true",
        help="explicitly allow a provider fallback to a different billing class",
    )
    run.add_argument(
        "--permission-mode",
        choices=["acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"],
        help=(
            "Claude worker permission mode (default: bypassPermissions; "
            "ignored by non-Claude providers)"
        ),
    )
    run.add_argument(
        "--no-workspace-note",
        dest="workspace_note",
        action="store_false",
        default=None,
        help=(
            "let a Claude worker create its own nested git worktree instead of "
            "committing on the branch you assigned"
        ),
    )
    run.add_argument(
        "--no-isolate-worker-config",
        dest="isolate_worker_config",
        action="store_false",
        default=None,
        help=(
            "let a Claude worker inherit the operator's full user-level "
            "settings/hooks/plugins instead of isolating it (default: isolated)"
        ),
    )
    run.add_argument(
        "--mcp-config",
        action="append",
        dest="mcp_config",
        help=(
            "MCP config file or inline JSON to give a Claude worker (repeatable). "
            "Workers otherwise get no MCP servers at all"
        ),
    )
    run.add_argument(
        "--no-strict-mcp-config",
        dest="strict_mcp_config",
        action="store_false",
        default=None,
        help=(
            "let a Claude worker inherit the operator's user- and project-scope MCP "
            "servers, at the risk of an approval prompt it cannot answer"
        ),
    )
    run.add_argument(
        "--min-result-bytes",
        type=int,
        help=(
            "smallest Claude result artifact reported as succeeded rather than "
            "unknown (default 300; 0 disables the check)"
        ),
    )
    run.add_argument("--idempotency-key")

    run_many = commands.add_parser(
        "run-many",
        aliases=["run_many"],
        parents=[common],
        help="launch a JSON job list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Each job requires provider, brief, cwd, and label; billing_class defaults "
            "to subscription-native. The input may be a JSON job array or an object "
            "with group, jobs, and idempotency_key. A Claude job may also set "
            "permission_mode (default bypassPermissions) and isolate_worker_config "
            "(default true) per-job, or at the request's top level as a default "
            "for jobs that omit them."
        ),
    )
    run_many.set_defaults(operation="run_many")
    run_many.add_argument("spec", nargs="?", help="JSON array/object file, or - to read stdin")
    run_many.add_argument("--label")
    run_many.add_argument(
        "--allow-billing-class-change",
        action="store_true",
        help="explicitly allow provider fallbacks to a different billing class",
    )
    run_many.add_argument("--idempotency-key")

    jobs = commands.add_parser(
        "jobs", aliases=["ps", "ls", "list"], parents=[common], help="list job snapshots"
    )
    jobs.set_defaults(operation="jobs")
    jobs.add_argument("--group-id")
    jobs.add_argument("--state")
    jobs.add_argument("--provider")
    jobs.add_argument("--label")
    jobs.add_argument(
        "--all",
        dest="all_sessions",
        action="store_true",
        help="list every session's jobs instead of only this session's",
    )
    jobs.add_argument(
        "--owner-session",
        help="list a specific session's jobs by identifier",
    )
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
    wait.add_argument(
        "--since-cursor",
        type=int,
        default=None,
        help="resume from a prior response's cursor; omit to wait from now, 0 to replay all",
    )
    wait.add_argument("--timeout", "--timeout-seconds", dest="timeout", type=float, default=3600)

    collect = commands.add_parser(
        "collect",
        aliases=["result", "last"],
        parents=[common],
        help="collect bounded result previews",
    )
    collect.set_defaults(operation="collect")
    collect.add_argument(
        "targets",
        nargs="*",
        help="one group/job ID, or multiple job IDs",
    )
    collect.add_argument(
        "--preview-bytes",
        "--max-chars",
        dest="preview_bytes",
        type=int,
        default=4000,
        help="maximum result preview bytes per job (default: 4000)",
    )
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
    reply.add_argument(
        "--permission-mode",
        choices=["acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"],
        help="override the parent's Claude worker permission mode for this continuation",
    )
    reply.add_argument(
        "--no-isolate-worker-config",
        dest="isolate_worker_config",
        action="store_false",
        default=None,
        help="override the parent's config isolation for this continuation",
    )

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

    restart = commands.add_parser(
        "restart",
        parents=[common],
        help="gracefully swap the broker daemon onto the current code",
    )
    restart.set_defaults(operation="restart")

    orphans = commands.add_parser(
        "orphans",
        parents=[common],
        help="list workers whose owning session is gone",
    )
    orphans.set_defaults(operation="orphans")
    orphans.add_argument(
        "--stop",
        action="store_true",
        help="stop the orphaned workers instead of only listing them",
    )
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
        operation = "run-many" if operation == "run_many" else operation
        if operation in {"run", "run-many", "reply", "jobs"} and value.get(
            "scope"
        ) != "all":
            _attach_caller(value, getattr(args, "state_dir", None))
        return operation, value

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
        for key in (
            "model",
            "group_id",
            "parent_job_id",
            "idempotency_key",
            "permission_mode",
            "mcp_config",
            "min_result_bytes",
        ):
            _set(params, key, getattr(args, key))
        if args.allow_billing_class_change:
            params["allow_billing_class_change"] = True
        _attach_caller(params, getattr(args, "state_dir", None))
        if getattr(args, "isolate_worker_config", None) is False:
            params["isolate_worker_config"] = False
        if getattr(args, "workspace_note", None) is False:
            params["workspace_note"] = False
        if getattr(args, "strict_mcp_config", None) is False:
            params["strict_mcp_config"] = False
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
        if args.allow_billing_class_change:
            params["allow_billing_class_change"] = True
        _attach_caller(params, getattr(args, "state_dir", None))
        operation = "run-many"
    elif operation == "jobs":
        params = {}
        for key in ("group_id", "state", "provider", "label", "limit", "owner_session"):
            _set(params, key, getattr(args, key))
        _set(params, "after_cursor", args.since_cursor)
        if getattr(args, "all_sessions", False):
            params["scope"] = "all"
        else:
            _attach_caller(params, getattr(args, "state_dir", None))
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
            "timeout": args.timeout,
        }
        _set(params, "since_cursor", args.since_cursor)
    elif operation == "collect":
        if not args.targets:
            raise OvermindError("collect requires a target unless --input is used", code="invalid_input")
        params = {"preview_bytes": args.preview_bytes}
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
        _set(params, "permission_mode", args.permission_mode)
        _attach_caller(params, getattr(args, "state_dir", None))
        if getattr(args, "isolate_worker_config", None) is False:
            params["isolate_worker_config"] = False
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


def _attach_caller(params: dict[str, Any], state_dir: Any) -> None:
    """Name the calling session: launch owner for run/reply, visibility scope for jobs."""

    if params.get("caller_session") or params.get("owner_session"):
        return
    try:
        from .client import default_state_dir
        from .sessions import caller_session

        caller = caller_session(Path(state_dir) if state_dir else default_state_dir())
    except Exception:
        return
    if caller:
        params["caller_session"] = caller


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
    job = value.get("job")
    if isinstance(job, dict):
        return [job]
    if "job_id" in value:
        return [value]
    return []


def _record_id(record: dict[str, Any], kind: str) -> str:
    identifier = record.get(f"{kind}_id") or record.get("id") or record.get("short_id")
    return _display_id(identifier)


def _artifact_hint(*records: dict[str, Any]) -> str | None:
    for record in records:
        for key in ("result_path", "artifact_path", "log_path"):
            value = record.get(key)
            if value:
                return str(value)
        artifacts = record.get("artifacts")
        if isinstance(artifacts, list):
            ordered = sorted(
                (item for item in artifacts if isinstance(item, dict)),
                key=lambda item: item.get("kind") != "result",
            )
            for item in ordered:
                if item.get("path"):
                    return str(item["path"])
    return None


def _job_table(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    lines = ["ID       STATE        PROVIDER  LABEL"]
    for job in rows:
        lines.append(
            (
                f"{_record_id(job, 'job'):<8} "
                f"{str(job.get('state', '-')):<12} "
                f"{str(job.get('provider', '-')):<9} "
                f"{job.get('label', '')}"
            ).rstrip()
        )
    return lines


def _one_line(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_human(operation: str, value: Any) -> str:
    if not isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    lines: list[str] = []
    if operation == "show":
        group = value.get("group")
        job = value.get("job")
        if isinstance(group, dict):
            counts = group.get("counts")
            count_text = ""
            if isinstance(counts, dict) and counts:
                values = " ".join(
                    f"{state}={count}" for state, count in sorted(counts.items())
                )
                count_text = f" [{values}]"
            lines.append(
                f"GROUP {_record_id(group, 'group')} {group.get('label', '')}{count_text}".rstrip()
            )
            lines.extend(_job_table(_job_rows(value)))
        elif isinstance(job, dict):
            lines.append(
                f"JOB {_record_id(job, 'job')} {job.get('state', '-')} "
                f"{job.get('provider', '-')} {job.get('label', '')}".rstrip()
            )
            hint = _artifact_hint(job, value)
            if hint:
                lines.append(f"ARTIFACT {hint}")
        if lines:
            if value.get("cursor") is not None:
                lines.append(f"CURSOR {value['cursor']}")
            return "\n".join(lines)

    if operation == "collect":
        items = value.get("results") or value.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                nested_job = item.get("job")
                job = nested_job if isinstance(nested_job, dict) else item
                prefix = (
                    f"{_record_id(job, 'job')} {job.get('state', '-')} "
                    f"{job.get('label', '')}"
                ).rstrip()
                preview = item.get("preview", item.get("result"))
                if preview is not None:
                    # The preview is already bounded by preview_bytes; flattening
                    # it to one ~240-char line here re-created the exact "results
                    # are one-liners" misread the artifacts were fixed to avoid.
                    # Short previews stay inline; long ones become a block.
                    text = str(preview).rstrip()
                    if len(text) <= 240 and "\n" not in text:
                        lines.append(f"{prefix}: {text}")
                    else:
                        lines.append(f"{prefix} ({len(text.encode('utf-8'))} bytes):")
                        lines.append(text)
                    if item.get("truncated"):
                        hint = _artifact_hint(item, job)
                        where = f"; full result at {hint}" if hint else ""
                        lines.append(f"[preview truncated{where}]")
                else:
                    hint = _artifact_hint(item, job)
                    detail = f"[artifact: {hint}]" if hint else "[no preview]"
                    lines.append(f"{prefix}: {detail}")
            if lines:
                return "\n".join(lines)

    rows = _job_rows(value)
    group_id = value.get("group_id")
    if group_id:
        lines.append(f"GROUP {_display_id(group_id)}")
    if rows or (operation == "jobs" and value.get("scope") is not None):
        scope = value.get("scope")
        if operation == "jobs" and scope is not None:
            total = value.get("total", len(rows))
            lines.append(
                f"SCOPE {_display_id(scope)} ({len(rows)} of {total} jobs)"
            )
        lines.extend(_job_table(rows))
        hint = value.get("hint")
        if hint:
            lines.append(f"NOTE {hint}")
        cursor = value.get("cursor")
        if cursor is not None:
            lines.append(f"CURSOR {cursor}")
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


def _orphans(args: Any) -> int:
    """Report (and optionally stop) workers whose owning session has exited.

    Deliberately not automatic. Surviving the parent session is a documented
    capability of this broker, so a worker outliving its launcher is a legitimate
    state, not a leak. This makes the cleanup available and explicit instead of
    quietly killing work someone meant to keep.
    """

    from .sessions import is_live, read_all

    client = DaemonClient(
        getattr(args, "state_dir", None),
        autostart=not getattr(args, "no_autostart", False),
    )
    live = {
        str(record["session_id"]) for record in read_all(client.state_dir) if is_live(record)
    }
    import sqlite3

    orphaned = []
    try:
        connection = sqlite3.connect(
            f"file:{client.state_dir / 'overmind.db'}?mode=ro", uri=True, timeout=1.0
        )
    except sqlite3.Error as error:
        raise OvermindError(f"cannot read the job store: {error}") from error
    try:
        rows = connection.execute(
            "SELECT id, short_id, label, provider, owner_session FROM jobs "
            "WHERE state IN ('running','starting','queued')"
        ).fetchall()
    finally:
        connection.close()
    for job_id, short_id, label, provider, owner in rows:
        if owner and str(owner) not in live:
            orphaned.append(
                (
                    {
                        "id": job_id,
                        "short_id": short_id,
                        "label": label,
                        "provider": provider,
                    },
                    str(owner),
                )
            )

    if getattr(args, "stop", False):
        for job, _owner in orphaned:
            try:
                client.request("stop", {"target": {"job_id": job["id"]}})
            except OvermindError:
                pass

    if getattr(args, "json", False):
        json.dump(
            {
                "orphans": [
                    {
                        "job_id": job.get("id"),
                        "short_id": job.get("short_id"),
                        "label": job.get("label"),
                        "provider": job.get("provider"),
                        "owner_session": owner,
                        "stopped": bool(getattr(args, "stop", False)),
                    }
                    for job, owner in orphaned
                ],
                "live_sessions": sorted(live),
            },
            sys.stdout,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return 0

    if not orphaned:
        print(f"no orphaned workers ({len(live)} live session(s))")
        return 0
    verb = "stopped" if getattr(args, "stop", False) else "orphaned"
    for job, owner in orphaned:
        print(
            f"{_display_id(job.get('short_id') or job.get('id'))} "
            f"{verb:9} {str(job.get('provider')):7} {job.get('label')} "
            f"(owner {_display_id(owner)} gone)"
        )
    if not getattr(args, "stop", False):
        print("re-run with --stop to end them")
    return 0


def _restart(args: Any) -> int:
    """Swap the daemon onto the code currently on disk, without losing jobs.

    Nonterminal jobs are reconciled by the fresh daemon on start; workers keep
    running throughout because providers own their processes, not the daemon.
    """

    import time

    state_dir = getattr(args, "state_dir", None)
    quiet = DaemonClient(state_dir, autostart=False)
    old_pid = None
    try:
        old_pid = quiet.request("shutdown", {}).get("pid")
    except OvermindError:
        pass  # no daemon running; starting one loads current code anyway
    deadline = time.monotonic() + 15
    while old_pid is not None and time.monotonic() < deadline:
        try:
            quiet.request("doctor", {})
            time.sleep(0.1)
        except OvermindError:
            break
    fresh = DaemonClient(state_dir, autostart=True)
    report: dict[str, Any] = {}
    last_error: OvermindError | None = None
    while time.monotonic() < deadline:
        try:
            report = fresh.request("doctor", {})
            break
        except OvermindError as error:
            # The old daemon releases its singleton lock a beat after its
            # socket closes; an early start attempt loses the lock race.
            last_error = error
            time.sleep(0.2)
    if not report:
        raise last_error or OvermindError("broker did not restart in time")
    daemon = report.get("daemon") or {}
    code = report.get("code") or {}
    if getattr(args, "json", False):
        json.dump(
            {"old_pid": old_pid, "daemon": daemon, "code": code},
            sys.stdout,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
    else:
        origin = f"pid {old_pid}" if old_pid else "not running"
        stale = "stale" if code.get("stale") else "current"
        print(f"restarted: {origin} -> pid {daemon.get('pid')} (code {stale})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "operation", None) in {"orphans", "restart"}:
        try:
            if args.operation == "orphans":
                return _orphans(args)
            return _restart(args)
        except OvermindError as error:
            print(f"om: {error}", file=sys.stderr)
            return 1
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
