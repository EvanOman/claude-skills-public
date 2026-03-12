---
name: slides
description: Turn research, notes, or a topic into a Markdown slide deck optimized for Gamma. Use when the user wants to create a presentation, slide deck, or talk outline from existing research or a new topic.
argument-hint: <topic, file path, or research folder>
allowed-tools: Read, Write, Glob, Grep, Bash(ls *), Bash(curl *), AskUserQuestion
user-invocable: true
---

# Create Slide Deck

Generate a Markdown slide deck from research, notes, or a topic description. The output format is optimized for [Gamma](https://gamma.app) -- Markdown with `---` slide separators that Gamma renders into visual presentations with minimal manual refinement. Optionally submit to Gamma's API to generate the visual presentation automatically.

## Step 1: Identify Source Material

Parse `$ARGUMENTS` to determine the input:

1. **A file or folder path** -- read the research reports, notes, or synthesis to base the deck on
2. **A topic description** -- create slides from scratch using web research
3. **No argument** -- ask the user what the presentation should cover

If a folder is given (e.g., a deep-research output folder), prioritize reading the synthesis report (`00 - Synthesis Report.md` or similar) first. Only read individual reports if the synthesis is missing or insufficient for the slide content needed.

## Step 2: Gather Presentation Parameters

Before drafting, determine (infer from context or ask if ambiguous):

- **Duration** -- how long is the talk? (default: 10 minutes, ~1 slide per minute)
- **Audience** -- technical, non-technical, or mixed? This controls jargon level and explanation depth
- **Tone** -- informational, persuasive, provocative, educational? (default: informational)
- **Point of view** -- does the presenter have a position, or is this neutral? If presenting research, check the synthesis for recommendations

Do not over-interview. If the user provided a clear request, infer reasonable defaults and start drafting.

## Step 3: Design the Arc

A good 10-minute presentation follows this arc:

```
1. Hook          -- what is this and why should I care? (1 slide)
2. Context       -- background the audience needs (1-2 slides)
3. How it works  -- simplified explanation (1-2 slides)
4. The good      -- what's interesting, novel, or exciting (2-3 slides)
5. The bad       -- risks, costs, gotchas, counterarguments (2-3 slides)
6. So what       -- what do we do with this information? (1-2 slides)
7. Takeaways     -- 3-5 bullet summary (1 slide)
```

Adjust the ratio based on the presenter's point of view. If they're enthusiastic, weight toward "the good." If they're cautionary, weight toward "the bad." If neutral, balance evenly.

Scale slide count to duration:
- 5 minutes: 5-7 slides
- 10 minutes: 10-14 slides
- 15 minutes: 14-18 slides
- 20+ minutes: 18-25 slides

## Step 4: Write the Slides

### Format Rules

Each slide is separated by a horizontal rule (`---`). The file starts with YAML front matter, then the presentation title, then slides.

```markdown
---
title: "Presentation Title"
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags:
  - ai-generated
  - presentation
  - <topic-tags>
status: active
---

# Presentation Title

Subtitle or one-line description.

---

## Slide 1: Title Here

Content here.

---

## Slide 2: Next Slide

More content.
```

### Slide Content Guidelines

**Gamma renders these Markdown elements well:**
- `## Heading` -- becomes the slide title
- Bullet points -- rendered as slide body text
- Tables -- rendered as formatted tables (great for comparisons, data)
- `> Blockquotes` -- rendered as callout boxes (good for quotes, key stats)
- Code blocks -- rendered as code snippets with syntax highlighting
- Bold text -- for emphasis within bullets

**What works in Gamma:**
- Short bullets (1-2 lines each) -- Gamma renders them as clean slide points
- Tables with 3-5 rows -- easy to read, not overwhelming
- One key stat or quote per slide as a blockquote
- Simple ASCII diagrams in code blocks -- Gamma renders them in monospace

**What to avoid:**
- Walls of text -- if a slide has more than 6-8 bullet points, split it
- Nested bullets deeper than 2 levels -- Gamma renders them but they're hard to read
- Images or links -- Gamma won't fetch external images from Markdown; the user adds visuals manually
- Complex Mermaid diagrams -- Gamma doesn't render them; use simple ASCII or describe the flow in bullets

### Writing Style

- **Slide titles should be assertions, not topics.** "Security Is Structurally Broken" > "Security Issues"
- **Lead with the punchline.** The first bullet on each slide should be the key takeaway; supporting evidence follows
- **Use concrete numbers.** "$3,600/month" lands harder than "very expensive"
- **Include speaker-note-style context in the flow.** Since this is Markdown (not PowerPoint), the slide content should be self-contained enough that someone reading the file gets the full story, not just cryptic bullet fragments
- **One idea per slide.** If you're covering two distinct points, make two slides
- **Use tables for comparisons.** Side-by-side data is more persuasive than prose
- **Blockquote memorable quotes.** Expert opinions, dramatic stats, or key findings work well as callouts

## Step 5: Choose Output Location

1. **If the source is a research folder** -- write the slides into that same folder (e.g., `Research Topic/Presentation Slides.md`)
2. **If working in a project** -- write to the project root or a `docs/` folder
3. **If the user specified a path** -- use it

## Step 6: Review Gate (HARD STOP)

**This is a mandatory confirmation step. Do NOT proceed to Gamma submission without explicit user approval.**

After writing the Markdown file, present to the user:
- Where the file was saved
- Slide count and estimated talk duration
- A brief outline of the slide titles

Then ask using `AskUserQuestion`:

> "Markdown slide deck saved. Would you like to submit it to Gamma for visual presentation generation? (This uses Gamma API credits.)"

Options:
1. **Submit to Gamma** -- proceed to Step 7
2. **Review first** -- user will review the Markdown and come back when ready
3. **Markdown only** -- skip Gamma, done

**If the user selects "Review first" or "Markdown only", STOP HERE.** The user can always ask to submit to Gamma later by saying "submit to Gamma" or "generate the presentation."

## Step 7: Submit to Gamma API

### Prerequisites

The Gamma API key must be available as `$GAMMA_API_KEY` environment variable.

### Preparation

Strip the YAML front matter from the Markdown content before submitting. Gamma doesn't need it -- it's for local file organization. Extract everything after the closing `---` of the front matter block.

### API Call

Submit to Gamma's Generate API:

```bash
curl -s -X POST https://public-api.gamma.app/v1.0/generations \
  -H "Content-Type: application/json" \
  -H "X-API-KEY: $GAMMA_API_KEY" \
  -d '{
    "inputText": "<SLIDE CONTENT WITHOUT YAML FRONT MATTER>",
    "textMode": "preserve",
    "format": "presentation",
    "themeId": "<THEME_ID>",
    "numCards": <SLIDE_COUNT>
  }'
```

**Parameters:**
- `inputText` -- the full Markdown slide content (with `---` separators). Strip the YAML front matter but keep everything else.
- `textMode` -- always `"preserve"`. This tells Gamma to keep the content as-is rather than rewriting it. Other options are `"generate"` (Gamma rewrites from a prompt) and `"condense"` (Gamma summarizes).
- `format` -- `"presentation"` for slide decks. Other options: `"document"`, `"webpage"`, `"social_post"`.
- `themeId` -- optional. A theme ID from the `/v1.0/themes` endpoint. Good defaults: `"default-dark"`, `"default-light"`, `"ash"`, `"aurora"`. If the user has a preference, use it; otherwise omit and let Gamma pick.
- `numCards` -- optional. Number of slides. Set this to match the slide count from the Markdown to prevent Gamma from adding/removing slides.

**Important:** The `inputText` value must be a valid JSON string. Escape newlines as `\n`, escape quotes, etc. Use a tool or script to properly JSON-encode the Markdown content.

### Poll for Completion

The API returns a `generationId`. Poll for completion:

```bash
curl -s https://public-api.gamma.app/v1.0/generations/<generationId> \
  -H "X-API-KEY: $GAMMA_API_KEY"
```

Poll every 10 seconds. Status values:
- `"pending"` -- still generating
- `"completed"` -- done, includes `gammaUrl` and `credits` info
- `"failed"` -- generation failed

Typical generation time: 30-90 seconds.

### Present Results

When completed, tell the user:
- The Gamma presentation URL (`gammaUrl` from the response)
- Credits used and remaining (`credits.deducted` and `credits.remaining`)
- Remind them they can edit the presentation directly in Gamma

---

## Example Output Structure

For a 10-minute research presentation:

```
Slide 1:  What is [Topic]? (hook + one-sentence explanation)
Slide 2:  Timeline / Context (how we got here)
Slide 3:  How it works (simplified, accessible to non-technical)
Slide 4:  Interesting idea #1 (the thing that makes this worth talking about)
Slide 5:  Interesting idea #2 (another novel concept)
Slide 6:  Why people are excited (adoption data, quotes, real-world examples)
Slide 7:  The cost/complexity problem (numbers, real examples)
Slide 8:  The security/risk problem (what's actually gone wrong)
Slide 9:  A concrete incident or case study (makes the risk tangible)
Slide 10: Why it's not right for us (specific, not generic)
Slide 11: What we should take from it (transferable ideas)
Slide 12: Key takeaways (3-5 bullets, the "if you remember one thing" slide)
```

---

## Gamma API Reference

**Base URL:** `https://public-api.gamma.app/v1.0`

**Authentication:** `X-API-KEY: $GAMMA_API_KEY` header on every request

**Endpoints used:**
- `POST /generations` -- create a presentation
- `GET /generations/<id>` -- check generation status
- `GET /themes` -- list available themes (for theme selection)

**Valid `textMode` values:** `generate`, `condense`, `preserve`
**Valid `format` values:** `presentation`, `document`, `webpage`, `social_post`

**Credits:** Each generation costs credits (typically 40-50 for a presentation). The response includes `credits.remaining` so the user can track their budget.
