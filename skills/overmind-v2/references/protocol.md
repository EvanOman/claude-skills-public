# Overmind v2 protocol

## Contents

- Runtime layout
- State model
- Command contract
- Event and wait contract
- Provider contract
- Recovery and safety
- MCP Tasks compatibility
- Known boundaries
- Agent-native capability map

## Runtime layout

Overmind v2 uses one per-user broker and one durable SQLite database:

```text
~/.local/state/overmind-v2/
  overmind.db
  overmind.sock
  daemon.log
  artifacts/<job-id>/
```

Override the root with `OVERMIND_V2_STATE_DIR` for tests or isolated installations. The directory
must be mode `0700`; the database and socket must not be accessible to other users. The CLI and MCP
server are thin clients. They may start the broker when it is absent, but they never become a second
background owner for provider work.

## State model

Groups represent a fan-out mission. Jobs belong to one group and optionally to a parent job.
Provider-native logs and results remain artifacts; SQLite stores metadata and paths, not transcript
copies.

Normalized states are `queued`, `starting`, `running`, `succeeded`, `failed`, `interrupted`, and
`unknown`. Every mutation appends an event with a monotonically increasing cursor. Full UUIDs are
authoritative; short IDs are display-only and must resolve uniquely before any destructive action.

Each job records a billing class: `subscription-native`, `explicit-metered`, or `unknown`. Provider
fallback must preserve billing class unless the caller explicitly opts into a change.

## Command contract

- `run`: create one group or append one job to a group and launch it atomically.
- `run-many`: create a group and launch a bounded list. Return one group ID and job summaries.
- `jobs`: list concise snapshots using group, state, provider, label, or cursor filters.
- `show`: read one group or job with freshness and artifact metadata.
- `await`: block on `any_change`, `any_terminal`, or `all_terminal` after a cursor.
- `collect`: return bounded terminal previews and artifact paths for a group or job list.
- `reply`: steer a running provider turn when supported; otherwise create a related continuation.
- `stop`: interrupt a job or group without deleting its record.
- `forget`: delete terminal lifecycle metadata; provider-native deletion is separate and explicit.
- `doctor`: report schema, daemon, provider, adapter, authentication, billing, and quota capabilities.
  Each provider entry also carries `last_failure`: the most recent terminal job for that provider
  with a recorded error, as `{job_id, short_id, state, message, occurred_at}` (or `null` when the
  broker has observed no such failure). CLI-based probing (`available`/`authenticated`) cannot see
  capacity errors such as exhausted subscription quota; `last_failure` is factual evidence drawn
  from the broker's own job history, not a fabricated quota snapshot, so a parent can see e.g. "usage
  limit until Jul 29th" before fanning out more work to a provider that will immediately fail.

Mutating calls accept an idempotency key. Retrying the same logical launch returns the original
entity. Conflicting payloads with the same key are errors.

Human aliases are CLI-only. Existing v1 MCP names may be accepted by a private compatibility
dispatcher during migration but must not be advertised in v2 tool discovery.

## Event and wait contract

`await` receives a target, condition, `since_cursor`, and timeout. It returns immediately when the
condition is already true, otherwise it sleeps inside the broker until an event or deadline. The MCP
adapter emits progress notifications on meaningful changes when the client supplies a progress
token. Reuse the last cursor after cancellation so no transition is lost.

Responses contain concise state deltas, counts, freshness, and suggested next operations. Full logs
are resources or artifact files rather than inline tool output.

## Provider contract

A provider adapter exposes capability discovery, launch, reconcile, continue or steer, interrupt,
and usage collection. Claude should track the exact daemon job state path returned at launch rather
than scan the global registry. Codex should prefer app-server event streams when the installed CLI
supports them and fall back to `codex exec --json` without changing billing class.

Codex reports a failed turn through its JSON event stream (a `turn.failed` event, with a preceding
bare `error` event as fallback), not through stderr. The adapter treats `turn.failed` as
authoritative: its message becomes the job's `error` field and, when no `agent_message` item was
produced, the result artifact content too, so `collect` previews carry the reason. A terminal
failure's `error` is always a non-empty string; stderr is used only when no event carries a message,
and a generic exit-code message is the last resort. It is never the empty string.

Tests use a deterministic fake provider through the same adapter contract. The fake provider is not
advertised as a production backend.

## Recovery and safety

Use SQLite WAL transactions and a busy timeout. On broker start, reconcile only nonterminal jobs.
Never duplicate a launch during recovery. Preserve unobservable work as `unknown`. Verify process
identity before signaling locally managed processes. Stop and forget require a canonical UUID or an
unambiguous short ID.

## MCP Tasks compatibility

The broker ledger, not an MCP connection, is the durable source of truth. The current adapter uses
ordinary tool calls, progress notifications, and resumable broker cursors. It deliberately does not
advertise MCP Tasks yet: the `2025-11-25` task primitive is experimental and requestor-polled, while
the proposed `2026-07-28` protocol moves Tasks into a negotiated extension with a breaking lifecycle
change. See the [current Tasks specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
and the [Tasks extension overview](https://modelcontextprotocol.io/extensions/tasks/overview).

Once both harness clients negotiate the same stable Tasks extension, add it as a transport adapter:
map task creation to `run` or `run-many`, task inspection to `show`, result retrieval to `collect`,
and cancellation to `stop`. Keep groups, provider identities, artifacts, billing evidence, and event
cursors in the broker so reconnects and cross-harness handoffs do not depend on one client's task
registry.

## Known boundaries

- Broker lifecycle records are shared across harnesses, but they do not become entries in each
  harness's native in-process subagent registry.
- Codex capability discovery reports app-server availability, while execution currently uses the
  subscription-authenticated `codex exec --json` adapter.
- A provider interrupt can be retried after a crash. The broker verifies local process identity, but
  exactly-once provider-side stop or deletion requires provider-native idempotency support.
- `forget` removes terminal broker metadata; provider-native transcript deletion remains a separate,
  explicit operation.

## Agent-native capability map

| Principle | v2 mechanism |
| --- | --- |
| Parity | CLI and MCP expose the same create, read, continue, stop, and delete capabilities. |
| Granularity | Lifecycle primitives remain atomic; `run-many` and `collect` are convenience views. |
| Composability | Groups, filters, parent links, and cursors compose arbitrary orchestration loops. |
| Emergent use | Agents can build pipelines without a hard-coded workflow graph. |
| Discovery | `doctor` reports dynamic provider capabilities and billing facts. |
| CRUD | Run, show/jobs, reply/metadata changes, and forget cover lifecycle entities. |
| Shared workspace | Both harnesses and the CLI observe the same broker and artifacts. |
| Explicit completion | Provider terminal events normalize into durable terminal state. |
| Partial/resume | Append-only events and cursors resume interrupted waits. |
| Bounded context | Snapshots and previews are concise; full output stays in artifacts. |
| UI updates | MCP progress and the event stream surface changes without model polling. |
| Cost awareness | Billing class, usage counters, batching, and provider preflight are first-class. |
