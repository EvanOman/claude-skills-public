---
name: add-skill
description: Create a new Claude Code skill. Use when the user wants to add a slash command, skill, or extend Claude's capabilities with a new workflow.
argument-hint: <skill-name> <description>
disable-model-invocation: true
allowed-tools: Bash(ls *), Bash(mkdir *), Read, Write, Glob
---

# Add a New Skill

Create a new personal skill at `~/.claude/skills/<name>/SKILL.md`.

## Existing skills

Before creating, check what's already installed by listing `~/.claude/skills/`.

## Process

1. **Understand** what the skill should do. If `$ARGUMENTS` provides a name and description, use those. Otherwise ask the user — keep questions minimal:
   - What should it do?
   - What triggers it? (user invokes, Claude auto-triggers, or both)
   - Any scripts, references, or assets needed?

2. **Plan the structure:**
   ```
   ~/.claude/skills/<name>/
   ├── SKILL.md              # Required
   ├── scripts/              # Optional: executable code
   ├── references/           # Optional: docs loaded into context as needed
   └── assets/               # Optional: files used in output, not loaded into context
   ```

3. **Create the skill** following the design principles and spec below.

4. **Confirm** the file path and how to invoke it (`/name` or `/name args`).

## Core Design Principles

**Claude is already smart.** Only include information Claude doesn't already have. Challenge each paragraph: "Does this justify its token cost?"

**Concise examples over verbose explanations.** Show, don't tell.

**Match freedom to fragility:**
- High freedom (text guidance): Multiple valid approaches, context-dependent
- Medium freedom (pseudocode/params): Preferred pattern exists, some variation OK
- Low freedom (specific scripts): Fragile operations, consistency critical

**Progressive disclosure:** SKILL.md body loads only when triggered. Keep it under 500 lines. Move detailed reference material to `references/` files — describe clearly in SKILL.md when to read them.

## SKILL.md Format

```yaml
---
name: my-skill              # lowercase, hyphens only, match dir name
description: What it does    # Claude uses this to decide when to auto-load
---

Instructions in markdown...
```

### Frontmatter Fields

| Field | Default | Purpose |
|---|---|---|
| `name` | dir name | Lowercase, hyphens, max 64 chars |
| `description` | — | What it does AND when to use it. This is the trigger mechanism — include scenarios, file types, tasks. Do NOT put "when to use" in the body. |
| `allowed-tools` | — | Pre-approved tool patterns: `Bash(npm *) Read Write` |
| `argument-hint` | — | Autocomplete hint: `<url>`, `[issue-number]` |
| `user-invocable` | `true` | `false` = hidden from `/` menu (background knowledge only) |
| `disable-model-invocation` | `false` | `true` = Claude won't auto-trigger (for side-effect workflows) |
| `context` | — | `fork` = run in isolated subagent context |
| `agent` | — | Subagent type with `context: fork`: `Explore`, `Plan`, `general-purpose` |

### String Substitutions

| Variable | Description |
|---|---|
| `$ARGUMENTS` | All args passed when invoking |
| `$ARGUMENTS[0]`, `$1` | Specific arg by index |
| `` !`command` `` | Dynamic context — runs shell command, injects output into prompt |

## Skill Patterns

Choose based on the skill's purpose:

**Side-effect workflow** (deploy, commit, send message):
```yaml
disable-model-invocation: true
```

**Background knowledge** (conventions, domain context):
```yaml
user-invocable: false
```

**Isolated task** (research, review):
```yaml
context: fork
agent: Explore
```

**Script-backed** (CLI tools):
- Scripts in `scripts/`, reference from body, `allowed-tools: Bash(uv run *)`

## Body Structure Patterns

Pick the pattern that fits:

**Workflow-based** (sequential processes):
`## Overview → ## Decision Tree → ## Step 1 → ## Step 2...`

**Task-based** (tool collections):
`## Overview → ## Quick Start → ## Task 1 → ## Task 2...`

**Reference/Guidelines** (standards, specs):
`## Overview → ## Guidelines → ## Specifications...`

For skills with multiple variants (frameworks, providers, domains), keep only core workflow and selection guidance in SKILL.md. Move variant-specific details to `references/`:

```
skill/
├── SKILL.md (overview + selection logic)
└── references/
    ├── variant-a.md
    └── variant-b.md
```

## What NOT to Include

- README.md, CHANGELOG.md, INSTALLATION_GUIDE.md — these are for humans, not agents
- "When to use this skill" sections in the body — that belongs in the `description` field
- Information Claude already knows (general programming, common tools)
- Duplicate content between SKILL.md and reference files
