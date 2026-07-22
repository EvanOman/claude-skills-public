# Overmind v2 bake-off report

Date: 2026-07-22

## Verdict

Overmind v2 is definitively better than v1 for persistent cross-harness fan-out. The strongest
evidence is restart-safe idempotent replay: a fresh parent can repeat the same grouped launch while
workers are running and receive the original group without creating another provider launch.

## Reproduce

From the owning repository root:

```bash
python3 -m unittest discover -s skills/overmind-v2/tests -v
python3 skills/overmind-v2/scripts/bakeoff.py --json
```

The full suite covers typed errors, exact mutation IDs, concurrent idempotency, partial launch
recovery, resumable event cursors, bounded artifact reads, billing-class refusal, usage deduplication,
provider reconciliation, held-worker shutdown, and persistent-MCP performance.

## Controlled four-worker result

| Measure | v1 | v2 |
| --- | ---: | ---: |
| Workers completed and collected | 4 | 4 |
| Ordinary lifecycle calls | 12 | 3 |
| Launch operation | 4 × `spawn` | 1 × `run-many` |
| Completion operation | 4 × `wait` | 1 × group `await` |
| Result operation | 4 × `result` | 1 × group `collect` |
| Model-driven polling | No | No |
| Launch replay idempotent after restart | No | Yes |
| Persistent-MCP status p95 gate | None | < 50 ms |

The v2 physical call count becomes four when an intentional restart retry is included; the retry is
the same logical `run-many` operation and does not launch more workers. Capability preflight via
`doctor` is separate from mission lifecycle calls.

## Restart trial

The initial v2 `run-many` returned four running jobs and cursor 25. An immediate retry from a fresh
CLI process with the same payload and idempotency key returned the same group with `created:false`
and `idempotent:true`. The fake provider log still contained exactly four launch actions and four
provider job IDs. One `await` resumed after cursor 25 and returned four terminal events at cursors
26–29; one bounded `collect` returned all four results.

V1 preserved already-recorded detached jobs across a parent restart, but its launch operation has no
idempotency key. A crash after the provider accepts a spawn and before the parent persists the job ID
can therefore make replay duplicate work.

## Live provider proof

A real grouped launch through the v2 broker ran one Claude subscription worker and one Codex ChatGPT
subscription worker. Both reached `succeeded`; bounded collection returned the exact markers
`CLAUDE_V2_LIVE_OK` and `CODEX_V2_LIVE_OK`, provider/thread IDs, artifacts, usage evidence, and
`subscription-native` billing records. A second live Claude completion verified that harmless
provider detail no longer appears as an error on a successful job.

## Blinded usability judgment

An independent judge received anonymized v1/v2 trial evidence and scored:

| Criterion | v1 | v2 |
| --- | ---: | ---: |
| Launch ergonomics | 5.0 | 8.5 |
| Restart safety | 3.0 | 9.5 |
| Waiting/completion reliability | 6.0 | 9.5 |
| Result/status discoverability | 5.0 | 9.0 |
| Billing provenance | 4.0 | 9.5 |
| Overall agent friendliness | 4.5 | 9.3 |

The judge found v2 definitively better. Verbose snapshots, deterministic-fixture setup, and lack of
native harness-registry membership remained paper cuts but did not weaken the demonstrated recovery,
completion, or billing guarantees.

## Honest boundaries

MCP Tasks remain an experimental, changing transport affordance, so the SQLite broker and event
ledger are the durable source of truth. Codex currently executes through its JSON event stream rather
than app-server. Provider-side interrupt cannot be made exactly-once without provider idempotency.
These boundaries are documented in [protocol.md](protocol.md) and do not affect the bake-off result.
