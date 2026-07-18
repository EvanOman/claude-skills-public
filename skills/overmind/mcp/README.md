# Overmind lifecycle MCP server

`../bin/overmind-mcp` exposes one lifecycle vocabulary for Claude and Codex workers:

- `overmind_spawn`
- `overmind_list` / `overmind_status`
- `overmind_wait` / `overmind_result`
- `overmind_followup`
- `overmind_interrupt`
- `overmind_cleanup`

It is a dependency-free stdio MCP server. Jobs have stable 12-character Overmind IDs and durable
state under `${OVERMIND_STATE_DIR:-~/.cache/overmind-lifecycle}`. Provider IDs remain available in
each job record for provider-native inspection.

Both backends use locally authenticated native CLIs. The server removes provider API-key/base-URL
environment overrides before launch so subscription login wins. Claude dispatch uses the bundled
`claude-worker.sh` and Claude's background-agent daemon. Codex dispatch uses `codex exec --json` and
`codex exec resume`, retaining the official event log with each job.

## Configure

Resolve the launcher to an absolute path and register the same command in each harness:

```bash
claude mcp add --scope user overmind -- /absolute/path/to/skills/overmind/bin/overmind-mcp
codex mcp add overmind -- /absolute/path/to/skills/overmind/bin/overmind-mcp
```

Use `overmind_capabilities` to discover provider differences. In particular, the Codex exec
protocol supports continuation only after a turn finishes; it does not expose live steering or
thread fork. Native Codex collaboration remains the richer path inside Codex. Cross-harness jobs do
not appear as native entries in the orchestrating harness's agent registry.

`overmind_cleanup` removes the adapter's durable record. It deletes Claude's provider-side record
only when `delete_provider_state` is explicitly true. Interrupt a running job before cleanup.
