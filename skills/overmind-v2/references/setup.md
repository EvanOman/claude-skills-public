# Configure Overmind v2

Resolve this skill directory to an absolute, stable path. Register the same stdio launcher in both
harnesses:

```bash
claude mcp add --scope user overmind-v2 -- /absolute/path/to/skills/overmind-v2/scripts/overmind-v2-mcp
codex mcp add overmind-v2 -- /absolute/path/to/skills/overmind-v2/scripts/overmind-v2-mcp
```

The launcher starts or connects to the per-user broker automatically. Confirm the shared view from
both harnesses with the `doctor` tool or:

```bash
/absolute/path/to/skills/overmind-v2/scripts/om doctor --json
```

Use `OVERMIND_V2_STATE_DIR` only for tests, isolated development, or an intentional second broker.
Do not point v2 at v1's cache directory. V2 does not import, alter, or delete v1 lifecycle records.

To remove the integration, remove the `overmind-v2` MCP registration from each harness. Do not
delete the state directory while jobs are active. Lifecycle records and result artifacts remain
under `~/.local/state/overmind-v2/` until explicitly forgotten or archived.
