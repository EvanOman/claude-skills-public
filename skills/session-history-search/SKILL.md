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
bash "$(dirname "$0")/setup.sh"           # symlinks bin/* into ~/.claude/bin (default)
bash "$(dirname "$0")/setup.sh" --copy    # copies instead of symlinking
```

By default the tools are **symlinked** into `~/.claude/bin/`, so a `git pull` of this repo updates them with no re-install. Use `--copy` if you don't keep the repo checked out (a standalone copy that won't track upstream fixes). Either way, make sure `~/.claude/bin` is on your `PATH`.

### Recommended: keep the index fresh automatically

Add an async `Stop` hook to `~/.claude/settings.json` so the index updates in the background after every Claude Code turn (cc-index is incremental, so this only touches changed sessions; `flock -n` skips the run if another session is already indexing):

```json
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "flock -n \"$HOME/.claude/usage-data/cc-index.lock\" \"$HOME/.claude/bin/cc-index\" >/dev/null 2>&1 || true",
        "timeout": 120,
        "async": true
      }]
    }]
  }
}
```

### Recommended: retain transcripts longer

Claude Code deletes session transcripts after 30 days by default, which silently shrinks what this skill can search (the FTS index only covers transcripts that still exist on disk). Raise the retention in `~/.claude/settings.json`:

```json
{ "cleanupPeriodDays": 3650 }
```

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

### `cc-index` — Build/update the search + tag index
```bash
cc-index                           # Index new/changed sessions (incremental)
cc-index --full                    # Rebuild the whole index from scratch
cc-index --stats                   # Show tag and skill distribution
```

`cc-index` builds two things: the derived tag/skill index (tags come automatically from file paths touched and skills invoked — no manual tagging) and the full-text search index that `cc-search` queries. The search index covers **every user prompt in every session**, not just the first.

Run `cc-index` periodically (or before a search) to pick up new sessions. Run `cc-index --full` after upgrading the tools, since a schema or tokenizer change means the old index needs a clean rebuild.

### `cc-search` — Full-text search across all prompts
```bash
cc-search "Oman family tree"       # Stemmed AND across terms (all three, any order, anywhere)
cc-search '"family tree" Henry'    # Exact phrase "family tree" AND term Henry
cc-search "deploy" --project inbox # Filter by project (substring match on project name)
cc-search "bug" --days 7           # Last 7 days only
cc-search "MCP" --sessions         # Also search session summaries (from sessions-index)
cc-search "error" --full           # Also search full transcripts (slower; matches assistant responses too)
cc-search "https://..." --literal  # Legacy substring scan over history.jsonl (exact-string / URL matches)
cc-search --recent 20              # Show 20 most recent prompts (no keyword needed)
```

**Search semantics** (default, FTS-backed):

- **Bareword terms are stemmed tokens combined with implicit AND.** `cc-search "Oman family tree"` matches sessions whose prompts contain all three terms, in any order, anywhere in any user prompt. Stemming means `families` matches `family`, `deploying` matches `deploy`, etc. (SQLite FTS5, porter stemmer, unicode61 tokenizer.)
- **Double-quote a phrase inside the query for an exact phrase match.** `cc-search '"family tree" Henry'` requires the contiguous phrase `family tree` and, separately, the term `Henry`.
- **Results are BM25-ranked.** Matches in a session's **first prompt are weighted 3x** over matches elsewhere in the session, so the sessions a topic was actually *about* rank above sessions that merely mention it in passing. Each hit shows a `snippet()` excerpt of the matching text.
- **Matching is token-based, not substring.** Searching `oman` will **not** match `evanoman.com` — that's a single different token. When you need a raw substring (URLs, IDs, file paths, code fragments), use `--literal`, which scans `~/.claude/history.jsonl` directly for the exact string.

All flags compose with the search: `--project`, `--days`, `--sessions`, `--full`, `--recent`, and `--literal`.

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
1. Run `cc-search "term1 term2 term3"` with a few distinctive words from the request — they AND together and stem, so extra words narrow the results rather than breaking them. Top hits are the sessions the topic was central to (first-prompt matches rank 3x).
2. If nothing lands, loosen (drop a term) or add `--sessions` to also match session summaries; use `--full` to reach into assistant responses.
3. For an exact string that got tokenized apart (a URL, an ID, `evanoman.com`), use `--literal`.
4. Present matches with session IDs and their snippet excerpts.
5. Offer to show the full transcript with `cc-transcript <id>`.

### "Show me recent sessions [for project X]"
1. Run `cc-sessions --count 20` or `cc-sessions --project X --count 20`
2. Present as a clean list

### "What was the context of [that conversation about X]?"
1. Search for it: `cc-search "X" --sessions --full` (add distinctive terms; they AND together)
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
| History | `~/.claude/history.jsonl` | Every user prompt with timestamp, project, session ID. Backing store for `cc-search --literal`. |
| Session transcripts | `~/.claude/projects/<project>/<uuid>.jsonl` | Full conversation logs (read by `cc-transcript`; searched with `cc-search --full`) |
| Session index | `~/.claude/projects/<project>/sessions-index.json` | Per-session summaries, message counts (may be stale) |
| Usage database | `~/.claude/usage-data/sessions.db` | Durations, token counts, tool usage — the richer metadata behind `cc-sessions --long` |
| Session memory | `~/.claude/projects/<project>/memory/` | Persisted per-project auto-memory notes |

## Tips

- If a search comes up empty for something recent, run `cc-index` first — the search index only covers sessions that have been indexed.
- Search is token-based and stemmed. Prefer a couple of distinctive words (they AND together) over one long phrase, and reach for `--literal` when you need an exact substring like a URL or ID.
- Session IDs are UUIDs. You only need the first 8 characters for prefix matching.
- `cc-transcript` outputs to stdout — pipe to `less` or redirect to a file for long sessions.
- The `--long` flag on `cc-sessions` pulls from usage-data which has richer metadata than sessions-index.
- To resume a past session: `claude --resume <session-id>` (in the terminal, not from within Claude).
- Full transcripts can be multi-MB. Use `--summary` or `--tail` first before reading the whole thing.
