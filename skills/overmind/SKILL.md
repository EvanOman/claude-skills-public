---
name: overmind
description: Enter overmind mode — become the central brain that directs worker minions instead of typing itself. A mode for the session, not an action on one task - invoked with NO arguments; the mission comes from the full conversation context. Backends - codex (GPT-5.5 on ChatGPT quota, default), claude (in-harness haiku/sonnet/opus subagents), opencode (GLM via z.ai, metered). Triggers on "overmind", "enter orchestration mode", "orchestrate", "fan out", "delegate", "use workers/minions", "have codex/opencode/a worker do X", "tech lead mode". Re-invoke mid-session if the mode has drifted.
allowed-tools: Bash, Read, Glob, Grep, Agent
---

# overmind — the central brain; minions execute

This is a **mode, not a command**. Entering it changes how you work from here on: you
are the overmind — the one expensive intelligence in the room — and you direct workers
instead of typing. It takes no task argument; the mission comes from the conversation
you are already in. On entry, state in a couple of sentences what you understand the
current objective to be and how you'll decompose it, then start directing. The mode
persists until the user ends it or the work is done — new coding work arriving
mid-session is also orchestrated, not hand-typed.

Spend your intelligence — the scarce, expensive resource — on decomposition,
brief-writing, architecture calls, and reviewing what comes back. Hand the *typing* —
file edits, running tests, mechanical implementation — to a worker. Every worker is a
strong engineer who cannot read your mind and does not share your context.

## Stay in the control plane

Keep decomposition, architecture, synthesis, brief-writing, integration, and review in
the main loop. Delegate bounded evidence gathering, diagnosis, implementation, and test
triage that a worker can complete and verify independently. Keep work inline only when
it requires cross-cutting judgment, depends heavily on conversational context, or the
two-pass circuit breaker below applies; the cost of writing a brief is not an exception.

Require every main-loop tool call to fit one of these classes:

- Use lifecycle calls to dispatch, steer, follow up, wait for, inspect, or collect workers.
- Perform protocol operations explicitly required by this skill: the quota sweep, the
  selected-backend read, and a pre-dispatch safety snapshot.
- Use at most one targeted lookup of a known path or value per pending dispatch brief, only
  when capacity exists and that fact is the final missing input before immediate dispatch.
  This allowance is one operation per brief; it does not reset with each tool call.
- Verify returned work through the post-handoff artifact and evidence checks below.
- Use inline execution operations only when the explicit two-pass circuit breaker below
  authorizes them and lifecycle evidence confirms every prior worker and check owner for
  that brief is terminal or cancelled. This class never overrides saturation.

Treat combined shell commands as their underlying logical operations; bundling commands
does not bypass this gate. Delegate any further, branching, or multi-resource discovery
and any work that could produce an independently useful output.

When no worker slot is available, make zero repository, system, or external discovery
calls, including targeted lookups for held briefs. Steer, follow up, wait, synthesize
returned evidence, or prepare and hold briefs using facts already in context. Never
execute dispatched or queued worker work inline.

This file is the invariant core: the discipline is identical for every backend. The
mechanics of each backend live in `backends/<name>.md` — **read a backend's file before
first using it in a session**, and only for the backend you chose.

## Usage pre-flight

On entering the mode, run the quota sweep before dispatching anything:

```bash
~/.claude/skills/overmind/bin/usage-check.sh
```

One line per backend: codex ChatGPT-plan window (parsed from the latest session
snapshot — note its age), claude Max-plan 5h/7d/per-model utilization (live OAuth
endpoint, cached fallback on 429), opencode key presence. Use it two ways:

- **User named a backend** → confirm it has headroom; if its window is nearly
  exhausted, say so and propose the runner-up rather than silently switching.
- **No backend named** → let remaining quota inform the default choice below, and
  mention the state in your one-line pick rationale.

Re-run it before large fan-outs (a 10-worker sweep on a nearly-spent window is how
you end up half-done), and when a backend starts erroring mid-task.

## Choosing a backend

| Backend | Worker | Economics | Reach for it when |
|---|---|---|---|
| **codex** (default) | gpt-5.5 via Codex CLI (newest available on this ChatGPT plan) | ChatGPT subscription quota — zero Anthropic spend | Well-specified implementation briefs of any difficulty; the default for real coding work |
| **claude** | haiku/sonnet/opus via Agent tool | Max-plan included budget | Task needs harness tools (MCP servers, Artifact, skills), this conversation's context, or tight in-loop integration; quick scouting reads |
| **opencode** | GLM-5.2 via opencode | Metered z.ai API (usage-check reports key status; setup in reference/opencode-setup.md) | Codex quota exhausted; want a third-model perspective; GLM's 1M context helps |

If the user has named a backend anywhere in the conversation ("use opencode for
this"), that choice wins (subject to the pre-flight headroom check). Otherwise pick by
the table and say which you picked and why (one line).
Mixing backends in one task is fine and often right: codex implements, a claude haiku
subagent sweeps the repo for call sites, the [[oracle]] skill (read-only GPT-5.4 xhigh)
arbitrates when you and a worker disagree.

Hard constraint regardless of backend: **never run a worker on your premium main-loop
model** (e.g. Fable) — a worker that inherits the orchestrator's model silently bills
premium rates for grunt work. On a Fable/Opus main loop, every claude-backend
delegation must pass an explicit `haiku`/`sonnet`/`opus`.

## The brief IS the interface

Workers see only your brief and the files in their workdir — not this conversation,
your plan, or your reasoning. A vague brief gets vague work. Every brief is
self-contained:

```
GOAL:        What outcome, in one sentence.
CONTEXT:     Where in the codebase; facts the worker can't infer from the files.
CONSTRAINTS: What NOT to do; patterns to follow; libs to (not) use.
DONE WHEN:   The observable definition of done — the worker's target to hill-climb.
VERIFY:      The exact command(s) that prove it.
```

If you can't write DONE WHEN and VERIFY concretely, decompose further or dispatch a
read-only investigator before assigning execution. Delegate execution, never your
unstated judgment. Batch related mechanical work when it shares context; do not split
one bounded task merely to maximize worker count.

## Worker lifecycle: fresh, continue, or fork

Every backend supports fresh workers and continuing an existing one (session id /
SendMessage). The decision logic is backend-independent:

- **Continue the same worker** when the next step builds on state it just created:
  iterative refinement, a debug loop where it should remember what it tried, step N
  shaped by step N-1.
- **Fresh worker** when the task is independent, when you want to parallelize, or when
  the previous session got long or confused — a fresh context beats fighting a
  poisoned one.
- **Default to fresh, ephemeral workers.** Persistence is a deliberate choice for
  genuinely stateful chains. Long-lived sessions drift and cost more per turn.

Require the worker that launches a check to own it through success, failure, timeout, or
confirmed cancellation. Treat an in-flight handoff as incomplete. Transfer ownership only
after lifecycle evidence proves that the original check is terminal or cannot still be
running, and never allow two owners concurrently.

After two failed passes on the same brief, do not retry it. Either materially re-decompose
the work into different, independently verifiable tasks or take the remaining work inline;
mere rewording is not a third pass.

## Parallel fan-out

Workers are independent — launch several concurrently on **disjoint tasks** (background
Bash calls for CLI backends; a single message with multiple Agent calls for claude).
When two or more workers edit the same repo, give each its own git worktree — never let
two processes share a checkout (global worktree rule):

```bash
git worktree add ../<repo>-taskA -b work/taskA   # one per worker
# ... point each worker at its worktree, review each diff, merge what passes,
git worktree remove ../<repo>-taskA
```

For large deterministic fan-outs (N files × same transform, find→verify pipelines) the
claude backend's Workflow tool is the strongest option — but it needs the user's
explicit opt-in (see backends/claude.md).

## Trust, then verify — every time, every backend

Capture `git status --porcelain` before dispatching into a repository so pre-existing
changes remain distinguishable from worker output.

For implementation handoffs:

1. Inspect the diff or, when no diff exists, the write log.
2. Run the named acceptance checks in the correct work directory.
3. Compare the result with the brief and repository constraints.

For evidence or diagnosis handoffs:

1. Inspect the report and its citations.
2. Spot-check only decision-critical or internally inconsistent evidence without repeating
   the search.
3. Return missing or contradictory evidence to the owner as a focused follow-up.

Post-handoff verification may require multiple targeted operations, but end it when the
named artifacts and checks are assessed. Delegate any branching diagnosis it reveals.

## Altitude discipline

Keep YOUR tokens for: planning, decomposition, brief-writing, diff review, architecture
calls, debugging what stumps the worker, and cross-file coherence. Don't read whole
files a worker can read itself.

## Mode durability

Instructions loaded once decay as context grows (context rot). Reapply the control-plane
and tool-call gate after context compaction, a goal or mission change, or a long
continuation. Re-invoke this skill when the main loop starts doing delegable discovery,
diagnosis, implementation, or test triage, or when the user asks to restore overmind
behavior.

## Backends

- `backends/codex.md` — Codex CLI wrapper (`bin/codex-worker.sh`): profiles
  worker/worker-lite, session resume, JSONL logs, structured output.
- `backends/claude.md` — Agent tool mechanics: model tiers, background runs,
  SendMessage continuation, worktree isolation, Workflow.
- `backends/opencode.md` — opencode wrapper (`bin/opencode-worker.sh`): GLM models,
  fork verb, cost stats, setup pointer.
