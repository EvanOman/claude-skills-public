---
name: handoff
description: Generate a structured handoff note for project continuity. Use when ending a session and wanting to capture context for the next agent or session.
allowed-tools: Bash, Read, Glob, Grep
---

# Handoff Note Generation

Generate a structured handoff note optimized for a future coding agent to pick up work on this project.

## Gather Context First

Before writing the note, collect this information:

### Current Date/Time
!`date "+%A, %B %d, %Y at %I:%M %p %Z"`

### Git Status
!`git status --short 2>/dev/null | head -20`

### Recent Commits
!`git log --oneline -5 2>/dev/null`

### Current Branch
!`git branch --show-current 2>/dev/null`

### Files Changed Recently
!`git diff --stat HEAD~3 2>/dev/null | tail -15`

### Check for Active Plans
!`ls -la ~/.claude/plans/*.md 2>/dev/null | head -5`

### Project Test Command (check common patterns)
!`ls package.json pyproject.toml Makefile Justfile Cargo.toml 2>/dev/null | head -1`

## Handoff Note Template

Write the handoff note using this structure. Output it directly to the conversation (not to a file).

```markdown
# [Project Name] - Handoff Note

**Date:** [Full date and time from above]
**Branch:** [current git branch]
**Last Commit:** [most recent commit hash and message]

## Session Summary
[2-3 sentences describing what was accomplished this session. Be specific about features, fixes, or changes made.]

## Completed This Session
- [Bullet list of completed items]
- [Include file paths where relevant, e.g., `src/api/routes.py`]
- [Note any significant decisions made]

## Test Status
- **Test Command:** [e.g., `just test`, `npm test`, `pytest`]
- **Last Run Result:** [X passed / Y failed / Z skipped, or "not run"]
- **Coverage:** [percentage if known]
- **Known Failures:** [list any expected/known failing tests]

## In Flight / Partially Complete
- [ ] [Task description] - [what's done vs what remains]
- [ ] [Another task] - [current blockers if any]

## Not Yet Validated
- [Features added but not fully tested end-to-end]
- [Edge cases identified but not covered]
- [Manual testing still needed for X]
- [Integration points not verified]

## Environment & Configuration
- **Required API Keys:** [list env vars needed]
- **Services Required:** [Docker, database, external APIs]
- **Config Files:** [.env, docker-compose.yml, etc.]
- **Run Command:** [how to start the dev server]

## Key Files Modified This Session
- `path/to/file.py` - [brief description of changes]
- `path/to/another.ts` - [description]

## Active Plan (if any)
[Reference any plan file from ~/.claude/plans/ or note "No active plan"]

## Suggested Next Steps
1. [Most important next action]
2. [Second priority]
3. [Nice to have]

## Context for Future Agent
- [Non-obvious decisions and their rationale]
- [Gotchas or pitfalls discovered]
- [Technical debt introduced or deferred]
- [Dependencies on external factors]
```

## Guidelines for Writing the Note

1. **Be specific** - Include file paths, function names, exact error messages
2. **Be honest about uncertainty** - Clearly mark things as "not validated" if untested
3. **Timestamp everything** - Future agents need to know how fresh the info is
4. **Assume zero context** - The next agent starts completely fresh
5. **Include reproduction steps** - How to run tests, start services, verify changes
6. **Link to evidence** - Reference specific commits, test output, log files
7. **Distinguish done vs untested** - "Implemented" is not the same as "verified working"

## After Writing

Review the note and ensure:
- All placeholder text is replaced with actual information
- Test status reflects reality (run tests if unsure)
- File paths are accurate
- No sensitive information (API keys, passwords) is included
