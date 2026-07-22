"""Overmind v2 durable local worker broker."""

from __future__ import annotations

PROTOCOL = "overmind-v2"
SCHEMA_VERSION = 1
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


class ConflictError(OvermindError):
    """An idempotency key or state precondition conflicts with durable state."""


class NotFoundError(OvermindError):
    """An entity or provider cannot be resolved."""


class AmbiguousIdError(OvermindError):
    """A short identifier matches more than one entity."""
