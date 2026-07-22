"""Overmind v2 durable local worker broker."""

from __future__ import annotations

PROTOCOL = "overmind-v2"
SCHEMA_VERSION = 2
STATES = (
    "queued",
    "starting",
    "running",
    "succeeded",
    "failed",
    "interrupted",
    "unknown",
)
TERMINAL_STATES = frozenset({"succeeded", "failed", "interrupted", "unknown"})
BILLING_CLASSES = frozenset({"subscription-native", "explicit-metered", "unknown"})


class OvermindError(RuntimeError):
    """A concise error safe to return through the local RPC boundary."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "overmind_error",
        data: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class ConflictError(OvermindError):
    """An idempotency key or state precondition conflicts with durable state."""

    def __init__(self, message: str, *, data: object | None = None) -> None:
        super().__init__(message, code="conflict", data=data)


class NotFoundError(OvermindError):
    """An entity or provider cannot be resolved."""

    def __init__(self, message: str, *, data: object | None = None) -> None:
        super().__init__(message, code="not_found", data=data)


class AmbiguousIdError(OvermindError):
    """A short identifier matches more than one entity."""

    def __init__(self, message: str, *, data: object | None = None) -> None:
        super().__init__(message, code="ambiguous_id", data=data)
