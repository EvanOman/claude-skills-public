"""SQLite persistence for Overmind v2.

The store deliberately contains no provider behavior. Every state mutation and
event append shares one SQLite transaction, which makes event cursors reliable
after cancellation or process restart.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import (
    AmbiguousIdError,
    ConflictError,
    NotFoundError,
    SCHEMA_VERSION,
    STATES,
    TERMINAL_STATES,
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def new_uuid() -> str:
    return str(uuid.uuid4())


class Store:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.db_path = state_dir / "overmind.db"
        self.artifacts_dir = state_dir / "artifacts"
        state_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        state_dir.chmod(0o700)
        self.artifacts_dir.chmod(0o700)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @contextlib.contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextlib.contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
        finally:
            connection.close()
        with self.transaction(immediate=True) as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS schema_meta (
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS groups (
                    id TEXT PRIMARY KEY,
                    short_id TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    short_id TEXT NOT NULL UNIQUE,
                    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    parent_job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
                    provider TEXT NOT NULL,
                    label TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    model TEXT,
                    billing_class TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN {STATES!r}),
                    brief_path TEXT NOT NULL,
                    provider_job_id TEXT,
                    provider_thread_id TEXT,
                    provider_state_path TEXT,
                    runner_pid INTEGER,
                    runner_start_identity TEXT,
                    result_path TEXT,
                    log_path TEXT,
                    error TEXT,
                    capabilities_json TEXT NOT NULL DEFAULT '{{}}',
                    provider_payload_json TEXT NOT NULL DEFAULT '{{}}',
                    allow_billing_class_change INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    terminal_at REAL
                );
                CREATE INDEX IF NOT EXISTS jobs_group_idx ON jobs(group_id, created_at);
                CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state, updated_at);
                CREATE TABLE IF NOT EXISTS events (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    group_id TEXT,
                    job_id TEXT,
                    kind TEXT NOT NULL,
                    state TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{{}}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_group_cursor_idx ON events(group_id, cursor);
                CREATE INDEX IF NOT EXISTS events_job_cursor_idx ON events(job_id, cursor);
                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    size_bytes INTEGER,
                    created_at REAL NOT NULL,
                    UNIQUE(job_id, kind, path)
                );
                CREATE TABLE IF NOT EXISTS usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    key TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    entity_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "provider_payload_json" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN provider_payload_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "allow_billing_class_change" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN allow_billing_class_change INTEGER NOT NULL DEFAULT 0"
                )
            row = connection.execute(
                "SELECT version FROM schema_meta LIMIT 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif int(row["version"]) > SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported Overmind schema {row['version']}; expected {SCHEMA_VERSION}"
                )
            elif int(row["version"]) < SCHEMA_VERSION:
                connection.execute(
                    "UPDATE schema_meta SET version=?", (SCHEMA_VERSION,)
                )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        os.chmod(self.db_path, 0o600)
        for sidecar in (
            self.db_path.with_name("overmind.db-wal"),
            self.db_path.with_name("overmind.db-shm"),
        ):
            if sidecar.exists():
                sidecar.chmod(0o600)

    def schema_version(self) -> int:
        with self.connection() as connection:
            return int(
                connection.execute("SELECT version FROM schema_meta").fetchone()[0]
            )

    def allocate_id(self, table: str) -> tuple[str, str]:
        if table not in {"groups", "jobs"}:
            raise ValueError("invalid ID table")
        with self.connection() as connection:
            for _ in range(100):
                identifier = new_uuid()
                short_id = identifier[:8]
                exists = connection.execute(
                    f"SELECT 1 FROM {table} WHERE short_id=?", (short_id,)
                ).fetchone()
                if exists is None:
                    return identifier, short_id
        raise RuntimeError("could not allocate a unique short ID")

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        entity_type: str,
        kind: str,
        group_id: str | None = None,
        job_id: str | None = None,
        state: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> int:
        cursor = connection.execute(
            """INSERT INTO events(entity_type,group_id,job_id,kind,state,payload_json,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                entity_type,
                group_id,
                job_id,
                kind,
                state,
                canonical_json(dict(payload or {})),
                time.time(),
            ),
        ).lastrowid
        assert cursor is not None
        return int(cursor)

    def create_launch(
        self,
        *,
        operation: str,
        request_payload: Mapping[str, Any],
        group: Mapping[str, Any] | None,
        jobs: Sequence[Mapping[str, Any]],
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        digest = payload_hash(request_payload)
        now = time.time()
        with self.transaction(immediate=True) as connection:
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM idempotency WHERE key=?", (idempotency_key,)
                ).fetchone()
                if existing:
                    if (
                        existing["operation"] != operation
                        or existing["payload_hash"] != digest
                    ):
                        raise ConflictError(
                            f"idempotency key {idempotency_key!r} conflicts with an earlier request"
                        )
                    entity = json.loads(existing["entity_json"])
                    return {**entity, "created": False, "idempotent": True}

            if group is not None:
                connection.execute(
                    "INSERT INTO groups(id,short_id,label,created_at,updated_at) VALUES (?,?,?,?,?)",
                    (group["id"], group["short_id"], group["label"], now, now),
                )
                self._event(
                    connection,
                    entity_type="group",
                    group_id=group["id"],
                    kind="group.created",
                    payload={"label": group["label"]},
                )
            group_id = group["id"] if group else str(jobs[0]["group_id"])
            for job in jobs:
                connection.execute(
                    """INSERT INTO jobs(
                         id,short_id,group_id,parent_job_id,provider,label,cwd,model,
                         billing_class,state,brief_path,capabilities_json,
                         provider_payload_json,allow_billing_class_change,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job["id"],
                        job["short_id"],
                        job["group_id"],
                        job.get("parent_job_id"),
                        job["provider"],
                        job["label"],
                        job["cwd"],
                        job.get("model"),
                        job["billing_class"],
                        "queued",
                        job["brief_path"],
                        canonical_json(job.get("capabilities", {})),
                        canonical_json(job.get("provider_payload", {})),
                        int(bool(job.get("allow_billing_class_change", False))),
                        now,
                        now,
                    ),
                )
                self._event(
                    connection,
                    entity_type="job",
                    group_id=job["group_id"],
                    job_id=job["id"],
                    kind="job.queued",
                    state="queued",
                    payload={"provider": job["provider"], "label": job["label"]},
                )
            entity = {"group_id": group_id, "job_ids": [job["id"] for job in jobs]}
            if idempotency_key:
                connection.execute(
                    "INSERT INTO idempotency(key,operation,payload_hash,entity_json,created_at) VALUES (?,?,?,?,?)",
                    (
                        idempotency_key,
                        operation,
                        digest,
                        canonical_json(entity),
                        now,
                    ),
                )
            return {**entity, "created": True, "idempotent": False}

    def lookup_idempotency(
        self, operation: str, request_payload: Mapping[str, Any], key: str | None
    ) -> dict[str, Any] | None:
        if not key:
            return None
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM idempotency WHERE key=?", (key,)
            ).fetchone()
        if row is None:
            return None
        if row["operation"] != operation or row["payload_hash"] != payload_hash(
            request_payload
        ):
            raise ConflictError(
                f"idempotency key {key!r} conflicts with an earlier request"
            )
        return json.loads(row["entity_json"])

    def remember_idempotency(
        self,
        operation: str,
        request_payload: Mapping[str, Any],
        key: str | None,
        result: Mapping[str, Any],
    ) -> None:
        if not key:
            return
        digest = payload_hash(request_payload)
        with self.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM idempotency WHERE key=?", (key,)
            ).fetchone()
            if existing:
                if (
                    existing["operation"] != operation
                    or existing["payload_hash"] != digest
                ):
                    raise ConflictError(
                        f"idempotency key {key!r} conflicts with an earlier request"
                    )
                return
            connection.execute(
                "INSERT INTO idempotency(key,operation,payload_hash,entity_json,created_at) VALUES (?,?,?,?,?)",
                (key, operation, digest, canonical_json(dict(result)), time.time()),
            )

    @staticmethod
    def _decode_job(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["capabilities"] = json.loads(value.pop("capabilities_json") or "{}")
        value["provider_payload"] = json.loads(
            value.pop("provider_payload_json") or "{}"
        )
        value["allow_billing_class_change"] = bool(
            value.get("allow_billing_class_change")
        )
        return value

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"job not found: {job_id}")
        return self._decode_job(row)

    def get_group(self, group_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM groups WHERE id=?", (group_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"group not found: {group_id}")
        return dict(row)

    def resolve(self, identifier: str, *, kind: str | None = None) -> tuple[str, str]:
        if not isinstance(identifier, str):
            raise NotFoundError("identifier must be a string")
        identifier = identifier.strip()
        tables = [kind] if kind else ["job", "group"]
        matches: list[tuple[str, str]] = []
        with self.connection() as connection:
            for entity_kind in tables:
                table = "jobs" if entity_kind == "job" else "groups"
                row = connection.execute(
                    f"SELECT id FROM {table} WHERE id=?", (identifier,)
                ).fetchone()
                if row:
                    return entity_kind, str(row["id"])
                if identifier and re_safe_id(identifier):
                    rows = connection.execute(
                        f"SELECT id FROM {table} WHERE id LIKE ?", (identifier + "%",)
                    ).fetchall()
                    matches.extend((entity_kind, str(item["id"])) for item in rows)
        unique = list(dict.fromkeys(matches))
        if not unique:
            raise NotFoundError(f"no {kind or 'job or group'} matches {identifier!r}")
        if len(unique) != 1:
            raise AmbiguousIdError(f"identifier {identifier!r} is ambiguous")
        return unique[0]

    def update_job(
        self,
        job_id: str,
        *,
        kind: str,
        state: str | None = None,
        fields: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
        allowed_states: set[str] | None = None,
    ) -> dict[str, Any]:
        if state is not None and state not in STATES:
            raise ValueError(f"invalid state: {state}")
        updates = dict(fields or {})
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"job not found: {job_id}")
            if allowed_states is not None and row["state"] not in allowed_states:
                raise ConflictError(
                    f"job {job_id} is {row['state']}; expected {sorted(allowed_states)}"
                )
            if (
                row["state"] in TERMINAL_STATES
                and state is not None
                and state != row["state"]
            ):
                # Provider observations can race a stop/reconcile operation.
                # Lifecycle terminality is monotonic, so a stale observation
                # must never resurrect or rewrite the terminal outcome.
                return self._decode_job(row)
            if state is not None:
                updates["state"] = state
                if state in TERMINAL_STATES:
                    updates["terminal_at"] = time.time()
            updates["updated_at"] = time.time()
            allowed = {
                "state",
                "provider_job_id",
                "provider_thread_id",
                "provider_state_path",
                "runner_pid",
                "runner_start_identity",
                "result_path",
                "log_path",
                "error",
                "billing_class",
                "terminal_at",
                "updated_at",
            }
            invalid = set(updates) - allowed
            if invalid:
                raise ValueError(f"invalid job fields: {sorted(invalid)}")
            assignments = ",".join(f"{column}=?" for column in updates)
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?",
                (*updates.values(), job_id),
            )
            new_state = state or str(row["state"])
            self._event(
                connection,
                entity_type="job",
                group_id=str(row["group_id"]),
                job_id=job_id,
                kind=kind,
                state=new_state,
                payload=payload,
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            assert updated is not None
            return self._decode_job(updated)

    def add_artifact(self, job_id: str, kind: str, path: str) -> None:
        size = Path(path).stat().st_size if Path(path).is_file() else None
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT group_id FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"job not found: {job_id}")
            existing = connection.execute(
                "SELECT size_bytes FROM artifacts WHERE job_id=? AND kind=? AND path=?",
                (job_id, kind, path),
            ).fetchone()
            if existing is not None and existing["size_bytes"] == size:
                return
            connection.execute(
                """INSERT INTO artifacts(job_id,kind,path,size_bytes,created_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(job_id,kind,path) DO UPDATE SET size_bytes=excluded.size_bytes""",
                (job_id, kind, path, size, time.time()),
            )
            self._event(
                connection,
                entity_type="artifact",
                group_id=str(row["group_id"]),
                job_id=job_id,
                kind="artifact.recorded",
                payload={"kind": kind, "path": path, "size_bytes": size},
            )

    def artifacts(self, job_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT kind,path,size_bytes,created_at FROM artifacts WHERE job_id=? ORDER BY id",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_usage(
        self, job_id: str, provider: str, evidence: Mapping[str, Any]
    ) -> bool:
        encoded = canonical_json(dict(evidence))
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT group_id FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"job not found: {job_id}")
            duplicate = connection.execute(
                "SELECT 1 FROM usage WHERE job_id=? AND provider=? AND evidence_json=? LIMIT 1",
                (job_id, provider, encoded),
            ).fetchone()
            if duplicate is not None:
                return False
            connection.execute(
                "INSERT INTO usage(job_id,provider,evidence_json,created_at) VALUES (?,?,?,?)",
                (job_id, provider, encoded, time.time()),
            )
            self._event(
                connection,
                entity_type="usage",
                group_id=str(row["group_id"]),
                job_id=job_id,
                kind="usage.recorded",
                payload={"provider": provider, "evidence": dict(evidence)},
            )
            return True

    def usage(self, job_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT provider,evidence_json,created_at FROM usage WHERE job_id=? ORDER BY id",
                (job_id,),
            ).fetchall()
        return [
            {
                "provider": row["provider"],
                "evidence": json.loads(row["evidence_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_jobs(
        self, filters: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        filters = dict(filters or {})
        clauses: list[str] = []
        values: list[Any] = []
        for key in ("group_id", "label"):
            if filters.get(key) is not None:
                clauses.append(f"j.{key}=?")
                values.append(filters[key])
        for key in ("state", "provider"):
            selected = filters.get(key)
            if isinstance(selected, (list, tuple, set)):
                selected = [str(item) for item in selected]
                if selected:
                    clauses.append(
                        f"j.{key} IN (" + ",".join("?" for _ in selected) + ")"
                    )
                    values.extend(selected)
            elif selected is not None:
                clauses.append(f"j.{key}=?")
                values.append(selected)
        if filters.get("active"):
            clauses.append(
                "j.state NOT IN (" + ",".join("?" for _ in TERMINAL_STATES) + ")"
            )
            values.extend(sorted(TERMINAL_STATES))
        if filters.get("terminal"):
            clauses.append(
                "j.state IN (" + ",".join("?" for _ in TERMINAL_STATES) + ")"
            )
            values.extend(sorted(TERMINAL_STATES))
        after_cursor = filters.get("after_cursor")
        if after_cursor is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM events e WHERE e.job_id=j.id AND e.cursor>?)"
            )
            values.append(int(after_cursor))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        limit = max(1, min(int(filters.get("limit", 200)), 5000))
        with self.connection() as connection:
            rows = connection.execute(
                f"SELECT j.* FROM jobs j{where} ORDER BY j.created_at LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [self._decode_job(row) for row in rows]

    def group_jobs(self, group_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE group_id=? ORDER BY created_at", (group_id,)
            ).fetchall()
        return [self._decode_job(row) for row in rows]

    def nonterminal_jobs(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in TERMINAL_STATES)
        with self.connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs WHERE state NOT IN ({placeholders}) ORDER BY created_at",
                tuple(sorted(TERMINAL_STATES)),
            ).fetchall()
        return [self._decode_job(row) for row in rows]

    def events_since(
        self,
        cursor: int,
        *,
        group_id: str | None = None,
        job_ids: Sequence[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["cursor>?"]
        values: list[Any] = [int(cursor)]
        if group_id:
            clauses.append("group_id=?")
            values.append(group_id)
        if job_ids:
            clauses.append("job_id IN (" + ",".join("?" for _ in job_ids) + ")")
            values.extend(job_ids)
        with self.connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY cursor LIMIT ?",
                (*values, max(1, min(limit, 1000))),
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["payload"] = json.loads(value.pop("payload_json") or "{}")
            result.append(value)
        return result

    def latest_cursor(self) -> int:
        with self.connection() as connection:
            return int(
                connection.execute(
                    "SELECT COALESCE(MAX(cursor),0) FROM events"
                ).fetchone()[0]
            )

    def forget_job(self, job_id: str) -> None:
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"job not found: {job_id}")
            if row["state"] not in TERMINAL_STATES:
                raise ConflictError(f"cannot forget nonterminal job {job_id}")
            connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            connection.execute(
                "DELETE FROM idempotency WHERE entity_json LIKE ?",
                (f"%{job_id}%",),
            )
            self._event(
                connection,
                entity_type="job",
                group_id=str(row["group_id"]),
                kind="job.forgotten",
                payload={"forgotten_job_id": job_id},
            )

    def forget_group(self, group_id: str) -> None:
        with self.transaction(immediate=True) as connection:
            states = connection.execute(
                "SELECT state FROM jobs WHERE group_id=?", (group_id,)
            ).fetchall()
            if any(row["state"] not in TERMINAL_STATES for row in states):
                raise ConflictError(
                    f"cannot forget group {group_id} with nonterminal jobs"
                )
            deleted = connection.execute(
                "DELETE FROM groups WHERE id=?", (group_id,)
            ).rowcount
            if not deleted:
                raise NotFoundError(f"group not found: {group_id}")
            connection.execute(
                "DELETE FROM idempotency WHERE entity_json LIKE ?",
                (f"%{group_id}%",),
            )
            self._event(
                connection,
                entity_type="group",
                kind="group.forgotten",
                payload={"forgotten_group_id": group_id},
            )


def re_safe_id(value: str) -> bool:
    return all(character in "0123456789abcdefABCDEF-" for character in value)
