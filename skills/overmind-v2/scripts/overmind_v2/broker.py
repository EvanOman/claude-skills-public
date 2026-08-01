"""Durable orchestration operations for Overmind v2."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from . import (
    AmbiguousIdError,
    ConflictError,
    NotFoundError,
    OvermindError,
    SCHEMA_VERSION,
    STATES,
    TERMINAL_STATES,
)
from .providers import Provider, ensure_billing, provider_registry, write_private
from .store import Store


ProgressCallback = Callable[[dict[str, Any]], None]

# Consecutive failed observations before a watcher gives up on a job. Retries are
# backed off, so this is roughly half a minute of sustained failure.
OBSERVATION_FAILURE_LIMIT = 5

_SOURCE_DIR = Path(__file__).resolve().parent
_STARTED_AT = time.time()


def _source_mtime() -> float:
    """Newest mtime across this package's modules, 0.0 when unreadable."""
    try:
        return max(
            (path.stat().st_mtime for path in _SOURCE_DIR.glob("*.py")),
            default=0.0,
        )
    except OSError:
        return 0.0


# Captured at import so a long-lived daemon can tell whether the code on disk
# has moved on without it. A stale daemon silently lacks documented behavior,
# and nothing else in the system can detect that.
_LOADED_SOURCE_MTIME = _source_mtime()


class Broker:
    # Per-job provider options that flow through unvalidated into
    # job["provider_payload"] so providers can read them (e.g. ClaudeProvider's
    # permission_mode and isolate_worker_config). Not stored as dedicated
    # columns; a run/run-many default is merged in when a job omits them.
    _PASSTHROUGH_PROVIDER_OPTIONS = (
        "permission_mode",
        "isolate_worker_config",
        "workspace_note",
        "idle_grace_seconds",
        "idle_hard_timeout_seconds",
        "strict_mcp_config",
        "mcp_config",
        "min_result_bytes",
        # Which orchestrator session launched this worker. Recorded so a session
        # can find its own workers, and so workers orphaned by a dead session can
        # be identified rather than lingering unattributed.
        "owner_session",
    )

    def __init__(
        self,
        state_dir: Path,
        *,
        providers: Mapping[str, Provider] | None = None,
        recover: bool = True,
    ) -> None:
        self.state_dir = state_dir
        self.store = Store(state_dir)
        self.providers = dict(providers or provider_registry())
        self._condition = threading.Condition()
        self._closing = threading.Event()
        self._generation = 0
        self._watchers: dict[str, threading.Thread] = {}
        self._watchers_lock = threading.Lock()
        self._destructive_lock = threading.RLock()
        if recover:
            self.reconcile_nonterminal()

    def close(self, timeout: float = 5.0) -> None:
        self._closing.set()
        self._notify()
        for provider in set(self.providers.values()):
            provider.close()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._watchers_lock:
                watchers = [thread for thread in self._watchers.values() if thread.is_alive()]
            if not watchers:
                return
            for thread in watchers:
                if thread is threading.current_thread():
                    continue
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
            if time.monotonic() >= deadline:
                return

    def _notify(self) -> None:
        with self._condition:
            self._generation += 1
            self._condition.notify_all()

    @staticmethod
    def _job_snapshot(job: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = {
            key: job.get(key)
            for key in (
                "id",
                "short_id",
                "group_id",
                "parent_job_id",
                "provider",
                "label",
                "state",
                "billing_class",
                "provider_job_id",
                "provider_thread_id",
                "result_path",
                "log_path",
                "error",
                "owner_session",
                "created_at",
                "updated_at",
                "terminal_at",
            )
        }
        snapshot["job_id"] = job.get("id")
        return snapshot

    def _group_snapshot(self, group: Mapping[str, Any]) -> dict[str, Any]:
        jobs = self.store.group_jobs(str(group["id"]))
        counts = Counter(job["state"] for job in jobs)
        return {
            "id": group["id"],
            "group_id": group["id"],
            "short_id": group["short_id"],
            "label": group["label"],
            "counts": dict(sorted(counts.items())),
            "total": len(jobs),
            "created_at": group["created_at"],
            "updated_at": group["updated_at"],
        }

    def _provider(self, name: str) -> Provider:
        try:
            return self.providers[name]
        except KeyError as error:
            raise NotFoundError(f"provider not found: {name}") from error

    @staticmethod
    def _should_watch(job: Mapping[str, Any]) -> bool:
        payload = job.get("provider_payload")
        fake = payload.get("fake") if isinstance(payload, Mapping) else None
        mode = fake.get("mode") if isinstance(fake, Mapping) else None
        return not (job.get("provider") == "fake" and mode in {"hold", "stale-process"})

    @staticmethod
    def _validate_cwd(value: Any) -> str:
        path = Path(str(value or os.getcwd())).expanduser().resolve()
        if not path.is_dir():
            raise OvermindError(f"working directory does not exist: {path}")
        return str(path)

    def _prepare_specs(
        self,
        raw_jobs: Sequence[Mapping[str, Any]],
        *,
        defaults: Mapping[str, Any],
        group_id: str,
    ) -> list[dict[str, Any]]:
        if not raw_jobs or len(raw_jobs) > 100:
            raise OvermindError("run-many requires between 1 and 100 jobs")
        prepared: list[dict[str, Any]] = []
        capability_cache: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(raw_jobs):
            provider_name = str(raw.get("provider", defaults.get("provider", "")))
            provider = self._provider(provider_name)
            capabilities = capability_cache.get(provider_name)
            if capabilities is None:
                capabilities = provider.probe()
                capability_cache[provider_name] = capabilities
            if not capabilities.get("available"):
                raise OvermindError(
                    f"provider {provider_name} is unavailable: "
                    f"{capabilities.get('reason', 'capability probe failed')}"
                )
            billing = ensure_billing(
                raw.get("billing_class", defaults.get("billing_class")),
                capabilities,
                allow_billing_change=bool(
                    raw.get(
                        "allow_billing_class_change",
                        raw.get(
                            "allow_billing_change",
                            defaults.get(
                                "allow_billing_class_change",
                                defaults.get("allow_billing_change", False),
                            ),
                        ),
                    )
                ),
            )
            brief = str(raw.get("brief", ""))
            if not brief.strip():
                raise OvermindError(f"job {index} has an empty brief")
            job_id, short_id = self.store.allocate_id("jobs")
            job_dir = self.store.artifacts_dir / job_id
            job_dir.mkdir(mode=0o700)
            brief_path = job_dir / "brief.txt"
            write_private(brief_path, brief)
            parent = raw.get("parent_job_id", defaults.get("parent_job_id"))
            if parent:
                parent_kind, parent_id = self.store.resolve(str(parent), kind="job")
                assert parent_kind == "job"
                if self.store.get_job(parent_id)["group_id"] != group_id:
                    raise ConflictError("parent job belongs to a different group")
            else:
                parent_id = None
            provider_payload = dict(raw)
            provider_payload.pop("caller_session", None)
            for option in self._PASSTHROUGH_PROVIDER_OPTIONS:
                if option not in provider_payload and defaults.get(option) is not None:
                    provider_payload[option] = defaults[option]
            owner = provider_payload.get("owner_session") or defaults.get(
                "caller_session"
            )
            if owner is not None:
                provider_payload["owner_session"] = str(owner)
            prepared.append(
                {
                    "owner_session": str(owner) if owner is not None else None,
                    "id": job_id,
                    "short_id": short_id,
                    "group_id": group_id,
                    "parent_job_id": parent_id,
                    "resume_thread": raw.get(
                        "resume_thread", defaults.get("resume_thread")
                    ),
                    "provider": provider_name,
                    "label": str(raw.get("label") or f"{provider_name}-{index + 1}"),
                    "cwd": self._validate_cwd(raw.get("cwd", defaults.get("cwd"))),
                    "model": raw.get("model", defaults.get("model")),
                    "billing_class": billing,
                    "brief_path": str(brief_path),
                    "brief": brief,
                    "capabilities": capabilities,
                    "provider_payload": provider_payload,
                    "allow_billing_class_change": bool(
                        raw.get(
                            "allow_billing_class_change",
                            raw.get(
                                "allow_billing_change",
                                defaults.get(
                                    "allow_billing_class_change",
                                    defaults.get("allow_billing_change", False),
                                ),
                            ),
                        )
                    ),
                }
            )
        return prepared

    def run(self, params: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(params)
        job = dict(raw.pop("job", {})) if raw.get("job") else dict(raw)
        for key in ("idempotency_key", "group_id", "group_label", "caller_session"):
            job.pop(key, None)
        group = params.get("group") if isinstance(params.get("group"), dict) else {}
        label = str(
            params.get("label")
            or params.get("group_label")
            or group.get("label")
            or "overmind-run"
        )
        return self._run_many(
            [job],
            defaults=params,
            operation="run",
            idempotency_key=params.get("idempotency_key"),
            requested_group=params.get("group_id") or group.get("group_id"),
            group_label=label,
        )

    def run_many(self, params: Mapping[str, Any]) -> dict[str, Any]:
        jobs = params.get("jobs")
        if not isinstance(jobs, list) or not all(
            isinstance(item, dict) for item in jobs
        ):
            raise OvermindError("run-many requires a jobs array")
        group = params.get("group") if isinstance(params.get("group"), dict) else {}
        return self._run_many(
            jobs,
            defaults=params,
            operation="run-many",
            idempotency_key=params.get("idempotency_key"),
            requested_group=params.get("group_id") or group.get("group_id"),
            group_label=str(params.get("label") or group.get("label") or "overmind-group"),
        )

    def _run_many(
        self,
        raw_jobs: Sequence[Mapping[str, Any]],
        *,
        defaults: Mapping[str, Any],
        operation: str,
        idempotency_key: Any,
        requested_group: Any,
        group_label: str,
    ) -> dict[str, Any]:
        # The idempotency hash covers what the caller asked for, not who asked.
        # owner_session is injected per calling session, so hashing it would make
        # the same logical launch, replayed after a parent restart from a new
        # session, a conflict instead of the original entity — the exact recovery
        # the key exists for. The worker options ARE hashed: a same-key request
        # that changes them is a different request and must conflict loudly.
        hashed_options = tuple(
            option
            for option in self._PASSTHROUGH_PROVIDER_OPTIONS
            if option != "owner_session"
        )
        request_payload = {
            "jobs": [
                {
                    key: value
                    for key, value in dict(item).items()
                    if key not in {"owner_session", "caller_session"}
                }
                for item in raw_jobs
            ],
            "defaults": {
                key: defaults.get(key)
                for key in (
                    "provider",
                    "cwd",
                    "model",
                    "billing_class",
                    "allow_billing_change",
                    "allow_billing_class_change",
                    "parent_job_id",
                    "resume_thread",
                    *hashed_options,
                )
                if defaults.get(key) is not None
            },
            "group_id": requested_group,
            "group_label": group_label,
        }
        key = str(idempotency_key) if idempotency_key else None
        existing = self.store.lookup_idempotency(operation, request_payload, key)
        if existing:
            return self._launch_response(
                existing["group_id"], existing["job_ids"], False, True
            )

        if requested_group:
            kind, group_id = self.store.resolve(str(requested_group), kind="group")
            assert kind == "group"
            group_record = None
        else:
            group_id, short_id = self.store.allocate_id("groups")
            group_record = {"id": group_id, "short_id": short_id, "label": group_label}

        prepared = self._prepare_specs(raw_jobs, defaults=defaults, group_id=group_id)
        entity = self.store.create_launch(
            operation=operation,
            request_payload=request_payload,
            group=group_record,
            jobs=prepared,
            idempotency_key=key,
        )
        self._notify()
        if not entity["created"]:
            artifact_root = self.store.artifacts_dir.resolve()
            for spec in prepared:
                brief_path = Path(str(spec["brief_path"])).resolve()
                if brief_path.parent.parent == artifact_root:
                    try:
                        brief_path.unlink()
                        brief_path.parent.rmdir()
                    except FileNotFoundError:
                        pass
        if entity["created"]:
            errors: list[Exception] = []
            with ThreadPoolExecutor(
                max_workers=min(32, len(prepared)),
                thread_name_prefix="overmind-launch",
            ) as executor:
                futures = [executor.submit(self._launch_job, spec) for spec in prepared]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as error:
                        errors.append(error)
            if errors:
                errors.sort(key=lambda error: str(error))
                partial = self._launch_response(
                    entity["group_id"], entity["job_ids"], True, False
                )
                partial["partial_launch"] = True
                partial["errors"] = [str(error) for error in errors]
                raise OvermindError(
                    str(errors[0]), code="partial_launch", data=partial
                ) from errors[0]
        return self._launch_response(
            entity["group_id"],
            entity["job_ids"],
            bool(entity["created"]),
            bool(entity["idempotent"]),
        )

    def _launch_response(
        self, group_id: str, job_ids: Sequence[str], created: bool, idempotent: bool
    ) -> dict[str, Any]:
        return {
            "group": self._group_snapshot(self.store.get_group(group_id)),
            "jobs": [
                self._job_snapshot(self.store.get_job(job_id)) for job_id in job_ids
            ],
            "created": created,
            "idempotent": idempotent,
            "cursor": self.store.latest_cursor(),
            "suggested_next": "await",
        }

    def _launch_job(self, spec: Mapping[str, Any]) -> None:
        job_id = str(spec["id"])
        self.store.update_job(
            job_id,
            kind="job.starting",
            state="starting",
            allowed_states={"queued"},
        )
        self._notify()
        provider = self._provider(str(spec["provider"]))
        try:
            job = self.store.get_job(job_id)
            parent = (
                self.store.get_job(str(job["parent_job_id"]))
                if job.get("parent_job_id")
                else None
            )
            update = (
                provider.continue_job(job, str(spec["brief"]), parent)
                if parent is not None
                else provider.launch(
                    job,
                    str(spec["brief"]),
                    resume_thread=spec.get("resume_thread"),
                )
            )
            actual_billing = update.get("billing_class")
            if (
                actual_billing is not None
                and str(actual_billing) != job["billing_class"]
                and not job.get("allow_billing_class_change")
            ):
                detail = (
                    "provider billing fallback changed "
                    f"{job['billing_class']} to {actual_billing}; explicit opt-in is required"
                )
                if str(update.get("state", "running")) not in TERMINAL_STATES:
                    try:
                        provider.interrupt({**job, **update})
                    except Exception:
                        pass
                self._apply_observation(
                    job_id,
                    {**update, "state": "failed", "error": detail},
                    kind="job.billing_rejected",
                )
                raise OvermindError(detail, code="billing_class_changed")
            self._apply_observation(job_id, update, kind="job.launched")
            job = self.store.get_job(job_id)
            if job["state"] not in TERMINAL_STATES and self._should_watch(job):
                self._watch(job_id)
        except Exception as error:
            if self.store.get_job(job_id)["state"] != "failed":
                self.store.update_job(
                    job_id,
                    kind="job.launch_failed",
                    state="failed",
                    fields={"error": str(error)},
                )
            self._notify()
            raise

    def _apply_observation(
        self, job_id: str, observation: Mapping[str, Any], *, kind: str
    ) -> dict[str, Any]:
        current = self.store.get_job(job_id)
        state = str(observation.get("state", current["state"]))
        if state not in STATES:
            state = "unknown"
        fields = {
            key: observation[key]
            for key in (
                "provider_job_id",
                "provider_thread_id",
                "provider_state_path",
                "runner_pid",
                "runner_start_identity",
                "result_path",
                "log_path",
                "error",
                "billing_class",
            )
            if key in observation
        }
        changed = state != current["state"] or any(
            current.get(key) != value for key, value in fields.items()
        )
        if changed:
            payload: dict[str, Any] = {"provider_state": observation.get("state")}
            if observation.get("raw_state") is not None:
                # The provider's own unnormalized state string. Recorded because a
                # verdict of `unknown` is otherwise unattributable after the fact:
                # the CLI state that produced eleven bad terminal verdicts could
                # only be guessed at from the surrounding detail text.
                payload["provider_raw_state"] = observation["raw_state"]
            current = self.store.update_job(
                job_id,
                kind=kind,
                state=state,
                fields=fields,
                payload=payload,
            )
            self._notify()
        recorded_evidence = False
        for artifact in observation.get("artifacts", []):
            path = artifact.get("path")
            if path:
                self.store.add_artifact(
                    job_id, str(artifact.get("kind", "provider")), str(path)
                )
                recorded_evidence = True
        usage = observation.get("usage")
        if isinstance(usage, dict) and usage:
            recorded_evidence = (
                self.store.add_usage(job_id, current["provider"], usage)
                or recorded_evidence
            )
        if recorded_evidence:
            self._notify()
        return current

    def _reconcile_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job["state"] in TERMINAL_STATES:
            return job
        observation = self._provider(job["provider"]).reconcile(job)
        return self._apply_observation(job_id, observation, kind="job.reconciled")

    def _watch(self, job_id: str) -> None:
        if self._closing.is_set():
            return
        with self._watchers_lock:
            active = self._watchers.get(job_id)
            if active and active.is_alive():
                return

            def watch() -> None:
                # A single failed observation is not evidence that a worker is
                # unobservable, and declaring it so is destructive: the job goes
                # terminal, is never reconciled again, and its real result is
                # discarded. Six measured jobs were lost that way to a transient
                # "database is locked" -- write contention between watchers, not a
                # dead worker. Give up only after the failure persists.
                failures = 0
                try:
                    while not self._closing.is_set():
                        try:
                            job = self._reconcile_job(job_id)
                        except Exception as error:
                            failures += 1
                            if failures >= OBSERVATION_FAILURE_LIMIT:
                                raise
                            self._closing.wait(timeout=min(2.0**failures, 10.0))
                            continue
                        failures = 0
                        if job["state"] in TERMINAL_STATES:
                            return
                        self._closing.wait(
                            timeout=0.1 if job["provider"] == "fake" else 1.0
                        )
                except Exception as error:
                    try:
                        self.store.update_job(
                            job_id,
                            kind="job.observation_lost",
                            state="unknown",
                            fields={
                                "error": (
                                    "provider observation failed "
                                    f"{failures} times in a row: {error}"
                                )
                            },
                        )
                        self._notify()
                    except Exception:
                        pass
                finally:
                    with self._watchers_lock:
                        self._watchers.pop(job_id, None)

            thread = threading.Thread(
                target=watch,
                name=f"overmind-watch-{job_id[:8]}",
                daemon=True,
            )
            self._watchers[job_id] = thread
            thread.start()

    def reconcile_nonterminal(self) -> None:
        for job in self.store.nonterminal_jobs():
            try:
                reconciled = self._reconcile_job(job["id"])
                if (
                    reconciled["state"] not in TERMINAL_STATES
                    and self._should_watch(reconciled)
                ):
                    self._watch(job["id"])
            except Exception as error:
                self.store.update_job(
                    job["id"],
                    kind="job.recovery_unknown",
                    state="unknown",
                    fields={"error": f"restart reconciliation failed: {error}"},
                )
        self._notify()

    def jobs(self, params: Mapping[str, Any]) -> dict[str, Any]:
        filters = dict(params)
        caller = filters.pop("caller_session", None)
        scope_request = str(filters.pop("scope", "") or "")
        explicit_owner = filters.pop("owner_session", None)
        if filters.get("since_cursor") is not None and filters.get("after_cursor") is None:
            filters["after_cursor"] = filters["since_cursor"]
        for key in ("group", "group_id"):
            # Resolve every group reference, short IDs included. A short ID that
            # silently matched nothing used to return a well-formed empty list,
            # which reads as "my group is done" and invites a duplicate launch.
            if filters.get(key):
                _, filters["group_id"] = self.store.resolve(
                    str(filters.pop(key)), kind="group"
                )
                break
        # Visibility: a session sees its own workers. Anything wider must be
        # asked for by identifier (a group, a named owner) or with scope=all.
        scope: dict[str, Any]
        if scope_request == "all":
            scope = {"all": True}
        elif filters.get("group_id") is not None:
            scope = {"group": True}
        elif explicit_owner is not None:
            scope = {"owner_session": str(explicit_owner)}
        elif scope_request in {"", "mine"}:
            scope = {"owner_session": str(caller) if caller else None}
        else:
            raise OvermindError(f"invalid jobs scope: {scope_request}")
        rows, total = self.store.list_jobs(filters, scope=scope)
        described = (
            "all"
            if scope.get("all")
            else "group"
            if scope.get("group")
            else scope.get("owner_session") or "unowned"
        )
        response = {
            "jobs": [self._job_snapshot(job) for job in rows],
            "cursor": self.store.latest_cursor(),
            "count": len(rows),
            "total": total,
            "truncated": total > len(rows),
            "scope": described,
        }
        if described == "unowned" and not scope.get("all"):
            response["hint"] = (
                "no calling session was identified, so only unowned jobs are "
                "listed; pass scope=all (om jobs --all) for every session"
            )
        if response["truncated"]:
            response["hint"] = (
                f"showing newest {len(rows)} of {total}; raise limit or narrow "
                "filters for the rest"
            )
        return response

    def _resolve_target(
        self,
        target: Any,
        *,
        kind: str | None = None,
        destructive: bool = False,
    ) -> tuple[str, str]:
        if isinstance(target, Mapping):
            group_id = target.get("group_id", target.get("groupId"))
            job_id = target.get("job_id", target.get("jobId"))
            if group_id and job_id:
                raise OvermindError("target must identify either one group or one job")
            if group_id:
                if kind == "job":
                    raise OvermindError("a job target is required")
                identifier = str(group_id)
                if destructive:
                    self._require_safe_mutation_id(identifier)
                return self.store.resolve(identifier, kind="group")
            if job_id:
                if kind == "group":
                    raise OvermindError("a group target is required")
                identifier = str(job_id)
                if destructive:
                    self._require_safe_mutation_id(identifier)
                return self.store.resolve(identifier, kind="job")
            raise OvermindError("target requires group_id or job_id")
        identifier = str(target)
        if destructive:
            self._require_safe_mutation_id(identifier)
        return self.store.resolve(identifier, kind=kind)

    @staticmethod
    def _require_safe_mutation_id(identifier: str) -> None:
        full_uuid = re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
            identifier,
        )
        short_id = re.fullmatch(r"[0-9a-fA-F]{8}", identifier)
        if not (full_uuid or short_id):
            raise AmbiguousIdError(
                "mutation target requires a full UUID or exact 8-character short ID; "
                f"{identifier!r} is ambiguous"
            )

    def show(self, params: Mapping[str, Any]) -> dict[str, Any]:
        identifier = params.get("id", params.get("target"))
        if not identifier:
            raise OvermindError("show requires id")
        kind, entity_id = self._resolve_target(identifier)
        if kind == "job":
            job = self.store.get_job(entity_id)
            if params.get("fresh") and job["state"] not in TERMINAL_STATES:
                job = self._reconcile_job(entity_id)
            return {
                "kind": "job",
                "job": self._job_snapshot(job),
                "artifacts": self.store.artifacts(entity_id),
                "usage": self.store.usage(entity_id),
                "freshness_seconds": max(0.0, time.time() - job["updated_at"]),
                "cursor": self.store.latest_cursor(),
            }
        group = self.store.get_group(entity_id)
        return {
            "kind": "group",
            "group": self._group_snapshot(group),
            "jobs": [
                self._job_snapshot(job) for job in self.store.group_jobs(entity_id)
            ],
            "cursor": self.store.latest_cursor(),
        }

    def _target_jobs(self, target: Any) -> tuple[dict[str, str], list[dict[str, Any]]]:
        kind, entity_id = self._resolve_target(target)
        if kind == "group":
            return {
                "kind": "group",
                "id": entity_id,
                "group_id": entity_id,
            }, self.store.group_jobs(entity_id)
        return {
            "kind": "job",
            "id": entity_id,
            "job_id": entity_id,
        }, [self.store.get_job(entity_id)]

    @staticmethod
    def _condition_satisfied(
        condition: str,
        jobs: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
    ) -> bool:
        if condition == "any_change":
            return bool(events)
        if condition == "any_terminal":
            return any(job["state"] in TERMINAL_STATES for job in jobs)
        if condition == "all_terminal":
            return bool(jobs) and all(job["state"] in TERMINAL_STATES for job in jobs)
        raise OvermindError(f"invalid await condition: {condition}")

    @staticmethod
    def _public_events(
        events: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for event in events:
            value = dict(event)
            if "state" in value:
                value["event_state"] = value.pop("state")
            result.append(value)
        return result

    def await_jobs(
        self,
        params: Mapping[str, Any],
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        target_value = params.get("target", params.get("id"))
        if not target_value:
            raise OvermindError("await requires target")
        target, jobs = self._target_jobs(target_value)
        condition = str(params.get("condition", "all_terminal"))
        since = params.get("since_cursor")
        # An omitted cursor means "from now": replaying a group's whole event
        # history is 3-4x the payload and is only wanted when explicitly resuming
        # (pass the cursor from a prior response, or an explicit 0 for everything).
        cursor = int(since) if since is not None else self.store.latest_cursor()
        timeout = max(
            0.0,
            min(float(params.get("timeout", params.get("timeout_seconds", 3600))), 86400.0),
        )
        deadline = time.monotonic() + timeout
        while True:
            if self._closing.is_set():
                raise OvermindError("broker is shutting down")
            with self._condition:
                generation = self._generation
            jobs = (
                self.store.group_jobs(target["id"])
                if target["kind"] == "group"
                else [self.store.get_job(target["id"])]
            )
            events = self.store.events_since(
                cursor,
                group_id=target["id"] if target["kind"] == "group" else None,
                job_ids=[target["id"]] if target["kind"] == "job" else None,
            )
            latest = events[-1]["cursor"] if events else self.store.latest_cursor()
            satisfied = self._condition_satisfied(condition, jobs, events)
            if satisfied or time.monotonic() >= deadline:
                counts = Counter(job["state"] for job in jobs)
                return {
                    "target": target,
                    "condition": condition,
                    "satisfied": satisfied,
                    "condition_met": satisfied,
                    "timed_out": not satisfied,
                    "cursor": latest,
                    "counts": dict(sorted(counts.items())),
                    "events": self._public_events(events),
                    "jobs": [self._job_snapshot(job) for job in jobs],
                    "suggested_next": "collect" if satisfied else "await",
                }
            if events:
                cursor = int(events[-1]["cursor"])
                if progress:
                    progress(
                        {
                            "cursor": cursor,
                            "events": self._public_events(events),
                            "counts": dict(Counter(job["state"] for job in jobs)),
                        }
                    )
            remaining = deadline - time.monotonic()
            with self._condition:
                if self._generation == generation:
                    self._condition.wait(timeout=max(0.0, min(remaining, 0.25)))
            if progress and not events and time.monotonic() < deadline:
                # A bounded heartbeat detects a caller that closed its socket
                # without manufacturing a lifecycle event.
                progress(
                    {
                        "heartbeat": True,
                        "cursor": cursor,
                        "counts": dict(Counter(job["state"] for job in jobs)),
                    }
                )

    def collect(self, params: Mapping[str, Any]) -> dict[str, Any]:
        max_jobs = max(1, min(int(params.get("max_jobs", 32)), 100))
        preview_bytes = max(
            0,
            min(
                int(params.get("preview_bytes", params.get("max_chars", 4000))),
                20000,
            ),
        )
        raw_target = params.get("target", params.get("id"))
        if isinstance(raw_target, Mapping) and isinstance(
            raw_target.get("job_ids"), list
        ):
            # The MCP schema folds an explicit job list into target so the tool
            # keeps a plain `required` list (a root anyOf gets it dropped).
            params = {**dict(params), "job_ids": raw_target["job_ids"]}
            raw_target = None
        if raw_target:
            target, jobs = self._target_jobs(raw_target)
        elif isinstance(params.get("targets"), list):
            jobs = []
            seen: set[str] = set()
            for value in params["targets"]:
                _, selected = self._target_jobs(value)
                for job in selected:
                    if job["id"] not in seen:
                        seen.add(job["id"])
                        jobs.append(job)
            target = {"kind": "jobs", "id": None}
        elif isinstance(params.get("job_ids"), list):
            jobs = []
            for value in params["job_ids"]:
                _, job_id = self.store.resolve(str(value), kind="job")
                jobs.append(self.store.get_job(job_id))
            target = {"kind": "jobs", "id": None}
        else:
            raise OvermindError("collect requires target, targets, or job_ids")
        results = []
        selected_jobs = (
            jobs
            if bool(params.get("include_nonterminal", False))
            else [job for job in jobs if job["state"] in TERMINAL_STATES]
        )
        for job in selected_jobs[:max_jobs]:
            preview = None
            truncated = False
            path = job.get("result_path")
            if path and Path(path).is_file() and preview_bytes:
                with Path(path).open("rb") as stream:
                    raw = stream.read(preview_bytes + 1)
                preview = raw[:preview_bytes].decode("utf-8", errors="ignore")
                truncated = len(raw) > preview_bytes
            results.append(
                {
                    "job": self._job_snapshot(job),
                    "preview": preview,
                    "truncated": truncated,
                    "artifacts": self.store.artifacts(job["id"]),
                    "usage": self.store.usage(job["id"]),
                }
            )
        return {
            "target": target,
            "results": results,
            "bounded": {
                "max_jobs": max_jobs,
                "preview_bytes": preview_bytes,
                # Silent truncation reads as "collected everything". Say when
                # the cap bit and how many terminal jobs were actually eligible.
                "jobs_total": len(selected_jobs),
                "jobs_truncated": len(selected_jobs) > max_jobs,
            },
            "cursor": self.store.latest_cursor(),
        }

    def reply(self, params: Mapping[str, Any]) -> dict[str, Any]:
        identifier = params.get(
            "target", params.get("id", params.get("job_id"))
        )
        prompt = str(params.get("prompt", ""))
        if not identifier or not prompt.strip():
            raise OvermindError("reply requires job id and prompt")
        _, job_id = self._resolve_target(identifier, kind="job", destructive=True)
        parent = self.store.get_job(job_id)
        if not (parent.get("provider_thread_id") or parent.get("provider_job_id")):
            raise ConflictError(
                "provider thread ID is unavailable; continuation was not created"
            )
        if (
            parent["state"] not in TERMINAL_STATES
            and not parent.get("capabilities", {}).get("steer")
        ):
            raise ConflictError(
                "parent job is still running and provider does not support live steering"
            )
        child_params = {
            "provider": parent["provider"],
            "brief": prompt,
            "cwd": parent["cwd"],
            "model": parent.get("model"),
            "billing_class": parent["billing_class"],
            "parent_job_id": parent["id"],
            "label": str(params.get("label") or f"{parent['label']}-reply"),
        }
        parent_payload = parent.get("provider_payload") or {}
        for option in self._PASSTHROUGH_PROVIDER_OPTIONS:
            if params.get(option) is not None:
                child_params[option] = params[option]
            elif parent_payload.get(option) is not None:
                child_params[option] = parent_payload[option]
        # A continuation of an owned job stays owned. Prefer the parent's owner
        # (set above via passthrough); otherwise attribute to whoever is asking,
        # so the continuation does not become an unattributable orphan.
        if not child_params.get("owner_session"):
            child_params["owner_session"] = parent.get("owner_session") or params.get(
                "caller_session"
            )
        if not child_params.get("owner_session"):
            child_params.pop("owner_session", None)
        response = self._run_many(
            [child_params],
            defaults={**child_params, "resume_thread": parent["provider_thread_id"]},
            operation="reply",
            idempotency_key=params.get("idempotency_key"),
            requested_group=parent["group_id"],
            group_label="continuation",
        )
        response["parent_job_id"] = parent["id"]
        return response

    def stop(self, params: Mapping[str, Any]) -> dict[str, Any]:
        with self._destructive_lock:
            return self._stop(params)

    def _stop(self, params: Mapping[str, Any]) -> dict[str, Any]:
        target_value = params.get("target", params.get("id"))
        if not target_value:
            raise OvermindError("stop requires target")
        key = str(params["idempotency_key"]) if params.get("idempotency_key") else None
        request_payload = {"target": target_value}
        existing = self.store.lookup_idempotency("stop", request_payload, key)
        if existing:
            return existing
        kind, entity_id = self._resolve_target(target_value, destructive=True)
        target, jobs = self._target_jobs(
            {f"{kind}_id": entity_id}
        )
        results = []
        actions = Counter()
        for job in jobs:
            if job["state"] in TERMINAL_STATES:
                # Nothing was interrupted; without saying so, the caller cannot
                # tell a landed stop from one that arrived after the fact.
                snapshot = self._job_snapshot(job)
                snapshot["stop_action"] = "already_terminal"
                actions["already_terminal"] += 1
                results.append(snapshot)
                continue
            if not (
                job.get("provider_job_id")
                or job.get("provider_state_path")
                or job.get("runner_pid")
            ):
                updated = self.store.update_job(
                    job["id"],
                    kind="job.interrupted",
                    state="interrupted",
                    fields={"error": "stopped before provider launch"},
                )
                action = "interrupted_before_launch"
            else:
                observation = self._provider(job["provider"]).interrupt(job)
                updated = self._apply_observation(
                    job["id"], observation, kind="job.stop_requested"
                )
                if updated["state"] not in TERMINAL_STATES:
                    self._watch(job["id"])
                action = "interrupt_requested"
            snapshot = self._job_snapshot(updated)
            snapshot["stop_action"] = action
            actions[action] += 1
            results.append(snapshot)
        self._notify()
        result = {
            "target": target,
            "jobs": results,
            "actions": dict(sorted(actions.items())),
            "cursor": self.store.latest_cursor(),
        }
        self.store.remember_idempotency("stop", request_payload, key, result)
        return result

    def forget(self, params: Mapping[str, Any]) -> dict[str, Any]:
        with self._destructive_lock:
            return self._forget(params)

    def _forget(self, params: Mapping[str, Any]) -> dict[str, Any]:
        target_value = params.get("target", params.get("id"))
        if not target_value:
            raise OvermindError("forget requires target")
        key = str(params["idempotency_key"]) if params.get("idempotency_key") else None
        request_payload = {"target": target_value}
        existing = self.store.lookup_idempotency("forget", request_payload, key)
        if existing:
            return existing
        kind, entity_id = self._resolve_target(target_value, destructive=True)
        if kind == "job":
            self.store.forget_job(entity_id)
        else:
            self.store.forget_group(entity_id)
        self._notify()
        result = {
            "forgotten": {"kind": kind, "id": entity_id},
            "provider_state_deleted": False,
            "cursor": self.store.latest_cursor(),
        }
        self.store.remember_idempotency("forget", request_payload, key, result)
        return result

    def _last_failure_evidence(self, provider: str) -> dict[str, Any] | None:
        job = self.store.last_provider_failure(provider)
        if job is None:
            return None
        excerpt = str(job.get("error") or "")
        if len(excerpt) > 500:
            excerpt = excerpt[:500] + "…"
        occurred_at = job.get("terminal_at") or job.get("updated_at")
        last_success = self.store.last_provider_success(provider)
        return {
            "job_id": job.get("id"),
            "short_id": job.get("short_id"),
            "state": job.get("state"),
            "message": excerpt,
            "occurred_at": occurred_at,
            # A failure older than the newest success is history. Without this
            # flag a parent keeps avoiding a provider that already recovered.
            "superseded_by_success": bool(
                last_success is not None
                and occurred_at is not None
                and last_success > float(occurred_at)
            ),
        }

    def doctor(self, _params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        production: dict[str, Any] = {}
        tests: dict[str, Any] = {}
        for name, provider in self.providers.items():
            try:
                capabilities = provider.probe()
            except Exception as error:
                capabilities = {"available": False, "reason": str(error)}
            # doctor evidence: CLI-reported availability/auth can't see quota
            # exhaustion or other capacity errors. Surface the most recent
            # terminal failure this broker actually observed for the provider
            # so a parent sees "usage limit until <date>" before fanning out.
            capabilities = {
                **capabilities,
                "last_failure": self._last_failure_evidence(name),
            }
            if provider.production:
                production[name] = capabilities
            else:
                tests[name] = capabilities
        disk_mtime = _source_mtime()
        code_stale = bool(
            _LOADED_SOURCE_MTIME and disk_mtime > _LOADED_SOURCE_MTIME + 1.0
        )
        report = {
            "schema_version": SCHEMA_VERSION,
            "database": str(self.store.db_path),
            "state_dir": str(self.state_dir),
            "daemon": {
                "pid": os.getpid(),
                "socket": str(self.state_dir / "overmind.sock"),
                "socket_present": (self.state_dir / "overmind.sock").exists(),
                "started_at": _STARTED_AT,
                "uptime_seconds": max(0.0, time.time() - _STARTED_AT),
            },
            "code": {
                "source_dir": str(_SOURCE_DIR),
                "loaded_mtime": _LOADED_SOURCE_MTIME,
                "disk_mtime": disk_mtime,
                "stale": code_stale,
            },
            "journal_mode": "wal",
            "providers": production,
            "test_providers": tests,
            "latest_cursor": self.store.latest_cursor(),
        }
        if code_stale:
            report["code"]["hint"] = (
                "broker source changed on disk after this daemon loaded it; "
                "documented behavior may be missing until you run `om restart`"
            )
        return report

    def dispatch(
        self,
        method: str,
        params: Mapping[str, Any] | None,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        arguments = dict(params or {})
        handlers: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
            "run": self.run,
            "run-many": self.run_many,
            "run_many": self.run_many,
            "jobs": self.jobs,
            "show": self.show,
            "collect": self.collect,
            "reply": self.reply,
            "stop": self.stop,
            "forget": self.forget,
            "doctor": self.doctor,
        }
        if method == "await":
            return self.await_jobs(arguments, progress=progress)
        try:
            handler = handlers[method]
        except KeyError as error:
            raise NotFoundError(f"unknown broker method: {method}") from error
        result = handler(arguments)
        # Catch accidental non-JSON public values at the broker boundary.
        json.dumps(result)
        return result
