---
name: session-history-search
description: Search, list, and review past Claude Code sessions, Claude Code conversation history, and Claude Code chat logs. Use when the user asks "what did I work on", "find the session where", "show me recent sessions", "search my Claude history", "find that conversation", "what was that conversation about", "Claude Code session history", "past Claude sessions", "previous Claude conversations", "search Claude Code logs", or any question about past Claude Code work. Also useful for self-reflection on past approaches.
argument-hint: <search keyword, "recent", "today", "project <name>", or a question about past work>
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, AskUserQuestion
---

# Session History Search

Search and review past Claude Code sessions. Four CLI tools are installed to `~/.claude/bin/`:

## Setup

Run the setup script to install the CLI tools:

```bash
bash "$(dirname "$0")/setup.sh"
```

Or manually copy the scripts from this skill's `bin/` directory to `~/.claude/bin/` and make them executable.

## Tools

### `cc-sessions` — List recent sessions
```bash
cc-sessions                        # Last 15 sessions across all projects
cc-sessions --count 30             # More sessions
cc-sessions --project obsidian     # Filter by project (substring match)
cc-sessions --days 3               # Only last N days
cc-sessions --long                 # Include duration, tokens, tool usage, goal/outcome
cc-sessions --tag job-search       # Filter by derived tag (requires cc-index)
cc-sessions --skill deep-research  # Filter by skill invoked (requires cc-index)
cc-sessions --tags                 # Show available tags and their counts
cc-sessions --project-summary      # Group by project with session counts and totals
cc-sessions --json                 # Machine-readable output
```

### `cc-index` — Build/update the session tag index
```bash
cc-index                           # Index new/changed sessions (incremental)
cc-index --full                    # Re-index everything from scratch
cc-index --stats                   # Show tag and skill distribution
```

Tags are derived automatically from file paths touched and skills invoked — no manual tagging needed. Run `cc-index` periodically to keep the index current.

### `cc-search` — Search by keyword
```bash
cc-search "kubernetes"             # Search all prompts for keyword
cc-search "deploy" --project inbox # Filter by project
cc-search "bug" --days 7           # Last 7 days only
cc-search "MCP" --sessions         # Also search session summaries (from sessions-index)
cc-search "error" --full           # Search full transcripts (slower, finds matches in assistant responses too)
cc-search --recent 20              # Show 20 most recent prompts (no keyword needed)
```

### `cc-transcript` — Read a session transcript
```bash
cc-transcript 975b31e1             # Render readable transcript (session ID prefix match)
cc-transcript 975b31e1 --summary   # Just first/last messages and stats
cc-transcript 975b31e1 --tools     # Include tool calls and results
cc-transcript 975b31e1 --user-only # Only user messages
cc-transcript 975b31e1 --tail 10   # Last 10 messages
cc-transcript 975b31e1 --raw       # Raw JSON
```

## Handling User Requests

Parse `$ARGUMENTS` to determine what the user wants:

### "What did I work on [today/this week/recently]?"
1. Run `cc-sessions --days N --long` (1 for today, 7 for this week)
2. Group sessions by project
3. Summarize: what projects were touched, what was accomplished, how much effort (message counts, durations)

### "Find the session where I [did X / discussed Y / fixed Z]"
1. Run `cc-search "keyword" --sessions` to find matching sessions
2. If no matches, try `cc-search "keyword" --full` for deeper search
3. Present matches with session IDs and context
4. Offer to show the full transcript with `cc-transcript <id>`

### "Show me recent sessions [for project X]"
1. Run `cc-sessions --count 20` or `cc-sessions --project X --count 20`
2. Present as a clean list

### "What was the context of [that conversation about X]?"
1. Search for it: `cc-search "X" --sessions --full`
2. Once found, read the transcript: `cc-transcript <id> --summary` first, then `--tail 20` for recent context
3. Summarize the conversation's arc: what was asked, what was done, what was the outcome

### "How much have I used Claude [today/this week]?"
1. Run `cc-sessions --days N --long` to get token/duration data
2. Run `cc-sessions --project-summary` for project breakdown

### "Review my recent work" / "What patterns do you see?"
1. Run `cc-sessions --days 7 --long`
2. Read transcripts of the most substantial sessions (highest message counts)
3. Look for patterns: repeated tasks, common friction points, projects getting the most attention

## Data Sources

The tools query these automatically, but for manual exploration:

| Source | Path | What's in it |
|--------|------|-------------|
| History | `~/.claude/history.jsonl` | Every user prompt with timestamp, project, session ID |
| Session transcripts | `~/.claude/projects/<project>/<uuid>.jsonl` | Full conversation logs |
| Session index | `~/.claude/projects/<project>/sessions-index.json` | Summaries, message counts (may be stale) |

## Tips

- Session IDs are UUIDs. You only need the first 8 characters for prefix matching.
- `cc-transcript` outputs to stdout — pipe to `less` or redirect to a file for long sessions.
- The `--long` flag on `cc-sessions` pulls from usage-data which has richer metadata than sessions-index.
- To resume a past session: `claude --resume <session-id>` (in the terminal, not from within Claude).
- Full transcripts can be multi-MB. Use `--summary` or `--tail` first before reading the whole thing.
