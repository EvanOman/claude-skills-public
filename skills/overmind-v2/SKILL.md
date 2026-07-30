---
name: overmind-v2
description: Orchestrate persistent, cross-harness subagents through a shared local task broker with grouped fan-out, event-driven waiting, bounded result collection, continuation, interruption, recovery, and usage evidence. Use when the user explicitly asks for Overmind v2, coordinated parallel work, a Claude-to-Codex or Codex-to-Claude worker, background agents that must survive the parent session, a subagent bake-off, or reliable status tracking without model-driven polling.
---

# Overmind v2

Operate as the mission controller. Infer the outcome from the conversation, keep synthesis and final
judgment in the parent, and delegate bounded work through the Overmind v2 broker. Prefer native
subscription-backed workers; never select a metered backend unless the user explicitly requests it.

## Start with capabilities

Resolve the directory containing this `SKILL.md` to an absolute `SKILL_ROOT`; commands below are
relative to that directory, not the user's current working directory. Run
`$SKILL_ROOT/scripts/om doctor --json` before the first cross-harness fan-out in a session. Use the
returned provider and billing facts instead of assuming that a harness, model, or live-steering
feature is available. Read [references/setup.md](references/setup.md) when installing the broker in
Claude and Codex. Read [references/protocol.md](references/protocol.md) when debugging lifecycle
behavior or using advanced filters. Read [references/testing.md](references/testing.md) for a
deterministic bake-off or broker regression work.

## Orchestrate a mission

1. Decompose the outcome into independently verifiable briefs with one owner per artifact.
2. Record the current checkout state before dispatching repository work. Isolate concurrent writers
   in separate worktrees.
3. Launch a group in one operation with `run-many`; use an idempotency key when retrying a request.
4. Continue useful control-plane work only while capacity remains. Never poll `jobs` in a reasoning
   loop.
5. Call `await` once with `all_terminal`, or `any_terminal` when later work depends on the first
   result. Resume an interrupted wait with its returned event cursor.
6. Call `collect` for bounded previews. Read full result artifacts only for workers whose details are
   needed.
7. Inspect produced artifacts and run the brief's named verification before synthesis.
8. Use `reply` for a stateful correction. Use `stop` for obsolete work and `forget` only when the
   lifecycle record is no longer useful.

Write each brief as:

```text
GOAL:        One observable outcome.
CONTEXT:     Relevant paths, facts, and dependencies.
CONSTRAINTS: Scope, invariants, billing class, and forbidden changes.
DONE WHEN:   Acceptance criteria visible outside the worker's narrative.
VERIFY:      Exact commands or checks.
```

Each job records the session that launched it, derived automatically from process
ancestry. `om orphans` shows workers whose owning session has exited and `--stop` ends
them; it is a command, not a sweep, because workers are meant to be able to outlive the
session that started them.

## Keep context and spend bounded

- Prefer `run-many -> await -> collect` over one launch, wait, and result cycle per worker.
- Ask `jobs` for active work in the current group, not global history.
- Keep result previews small; use artifact paths for full output.
- Treat subscription quota and token counters as usage evidence, not dollar invoices.
- Reject silent provider fallback across billing classes.
- Treat broker jobs as execution state, never as durable user to-dos.

## Handle completion and recovery

Trust normalized terminal states from the broker: `succeeded`, `failed`, `interrupted`, and
`unknown`. A worker summary alone is not proof. If the parent or broker restarts, query the existing
group and resume from its event cursor; do not relaunch without the same idempotency key. When a
provider cannot be observed, preserve the job as `unknown` rather than inventing failure. A Claude
worker that ends its turn waiting on operator input (a "blocked" CLI state) is reported `succeeded`
with that message as the result artifact; judge its content like any other result rather than
treating it as still running.

## Claude worker defaults

Claude workers launch with `permission_mode: bypassPermissions`, `isolate_worker_config: true`, and
`strict_mcp_config: true` by default. A background worker has no TTY, so anything that asks it a
question ends its turn: a permission prompt, a session-start hook, or an MCP server it has not
approved. Those defaults remove all three, and the worker acts on its brief unattended. All are
per-job options on `run`/`run-many`/`reply` (inherited by continuations unless overridden); see
[references/setup.md](references/setup.md#claude-worker-launch-options) to opt into a narrower
permission mode, the operator's full config, or a specific `mcp_config`. Prefer `dontAsk` for
read-only auditors and reviewers, and say in the brief that the work is read-only.

A worker's result artifact is its own final assistant message, not the CLI's one-line headline for
it. A terminal job whose artifact is under `min_result_bytes` (default 300) is reported `unknown`
rather than `succeeded`: nothing readable was reported, so the outcome is unverified and the brief's
artifacts are what to check.

A worker whose `cwd` is a git checkout is also told not to create its own nested worktree, and the
CLI's background worktree-isolation guard — which otherwise refuses its first write until it calls
`EnterWorktree` — is turned off, so its commits land on the branch you assigned rather than one you
are not watching. Keep assigning one worktree per writer yourself; pass `workspace_note: false` when
a worker must manage its own, which also restores the guard.

Omit `model` unless a job genuinely needs a specific one, so workers inherit the configured default
rather than a hardcoded tier.

A worker that finishes its work but never emits a final message parks at "working" forever. The
broker reaps such a worker after `idle_grace_seconds` (default 300) of no turn in progress, ends its
session, and reports it terminal as `unknown` with its last progress note as the result. `unknown`
here means exactly what it says: the work may well be complete, but nothing reported it, so inspect
the artifacts the brief asked for before trusting or redoing it. Raise `idle_grace_seconds` for a
job that legitimately sits idle, or set 0 to disable reaping for it.

Reaping requires the CLI to report no work in flight, which matters more than it sounds: a parent
waiting on a subagent reports `tempo: "idle"` for the whole wait, so idleness alone would kill
orchestrating workers mid-flight. A worker the CLI still reports as busy is ended instead by
`idle_hard_timeout_seconds` (default 3600), which bounds silence rather than runtime: the CLI touches
the worker's state file on every message and tool result, so an hour of no change means wedged, not
slow. Raise it for a job with a single legitimately silent step longer than that.

## Use the command surface

Use `$SKILL_ROOT/scripts/om --help` for the human CLI and `$SKILL_ROOT/scripts/overmind-v2-mcp` for
the MCP stdio server. Canonical operations are `run`, `run-many`, `jobs`, `show`, `await`, `collect`,
`reply`, `stop`, `forget`, and `doctor`. Human aliases are accepted, but do not teach duplicate MCP
tool names.

Use v1 only as the control during migration or when v2's doctor reports an unavailable required
capability. Do not modify or migrate v1 state implicitly.
