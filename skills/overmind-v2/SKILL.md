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
feature is available. `doctor` also reports whether the long-lived broker daemon predates the code
on disk (`code.stale`) — a stale daemon keeps old behavior silently while the documentation
describes the new. When it reports stale, check `om jobs --all --state running` for other sessions'
in-flight workers, then run `$SKILL_ROOT/scripts/om restart`: the swap is designed to be safe
(workers survive; nonterminal jobs reconcile without duplicate launches), but their orchestrators'
blocked awaits will return early once and must resume from their cursors, so restarting under
someone else's live fan-out is an operator call, not a reflex. Read [references/setup.md](references/setup.md) when installing the broker in
Claude and Codex. Read [references/protocol.md](references/protocol.md) when debugging lifecycle
behavior or using advanced filters. Read [references/testing.md](references/testing.md) for a
deterministic bake-off or broker regression work.

## Orchestrate a mission

1. Decompose the outcome into independently verifiable briefs with one owner per artifact.
2. Record the current checkout state before dispatching repository work. Isolate concurrent writers
   in separate worktrees.
3. Launch a group in one operation with `run-many`; use an idempotency key when retrying a request.
4. Do remaining control-plane work now, before waiting — `await` blocks until it returns, so there
   is no window during it. Never poll `jobs` in a reasoning loop.
5. Call `await` once with `all_terminal`, or `any_terminal` when later work depends on the first
   result. Omit `since_cursor` to wait from now; pass a prior response's cursor only when resuming
   an interrupted wait, so no transition is lost and no history is replayed.
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

Each job records the session that launched it, derived automatically from process ancestry, and
that ownership is the visibility boundary: you see your own fleet, not other sessions'. A full
group or job identifier is a deliberate capability that crosses sessions — that is how recovery
and handoff work — and `scope: all` (`om jobs --all`) is the explicit wide view. Continuations
inherit their parent's owner. `om orphans` shows workers whose owning session has exited and
`--stop` ends them; it is a command, not a sweep, because workers are meant to be able to outlive
the session that started them.

## Keep context and spend bounded

- Prefer `run-many -> await -> collect` over one launch, wait, and result cycle per worker.
- `jobs` lists your session's workers, newest first, with `total` and `truncated` flags; trust
  them rather than raising the limit reflexively, and filter by group when you mean one mission.
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
permission mode, the operator's full config, or a specific `mcp_config`. Keep the default even for
read-only work and constrain scope in the brief instead: `dontAsk` silently auto-denies Bash-shaped
calls (`git -C <path>`, `find`, compound commands) while plain reads still work, so `VERIFY:` steps
fail quietly; if you do use it, write VERIFY commands that run bare in the worker's own cwd and tell
the worker to report denials rather than route around them.

A worker's result artifact is its own final assistant message, not the CLI's one-line headline for
it; the full session transcript is also registered as a `transcript` artifact.

A worker whose `cwd` is a git checkout is also told not to create its own nested worktree, and the
CLI's background worktree-isolation guard — which otherwise refuses its first write until it calls
`EnterWorktree` — is turned off, so its commits land on the branch you assigned rather than one you
are not watching. Keep assigning one worktree per writer yourself; pass `workspace_note: false` when
a worker must manage its own, which also restores the guard.

Omit `model` unless a job genuinely needs a specific one, so workers inherit the configured default
rather than a hardcoded tier.

`unknown` always means one thing: the work may well be done, but nothing readable reported it —
inspect the artifacts the brief asked for before trusting or redoing it. Three paths produce it: a
`succeeded` result under `min_result_bytes` (default 300; set 0 for a genuinely one-word verdict),
the broker reaping a worker that stopped reporting (`idle_grace_seconds`, default 300, for a worker
with no turn in progress; `idle_hard_timeout_seconds`, default 3600, bounding silence for one still
claiming work in flight — neither can fire while a worker makes progress, and a parent waiting on
its own subagent is never reaped), and a provider that cannot be observed. Raise the reaping knobs
for legitimately quiet jobs, or set 0 to disable; the measured incidents behind each rule are in
[references/setup.md](references/setup.md#claude-worker-launch-options).

## Use the command surface

Use `$SKILL_ROOT/scripts/om --help` for the human CLI and `$SKILL_ROOT/scripts/overmind-v2-mcp` for
the MCP stdio server. Canonical operations are `run`, `run-many`, `jobs`, `show`, `await`, `collect`,
`reply`, `stop`, `forget`, and `doctor`; `om orphans` and `om restart` are deliberately CLI-only
operator commands with no MCP tool. Human aliases are accepted, but do not teach duplicate MCP
tool names.

Use v1 only as the control during migration or when v2's doctor reports an unavailable required
capability. Do not modify or migrate v1 state implicitly.
