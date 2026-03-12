---
name: bulletize
description: Restructure rambling, unstructured prose in a note into bullet points. Use when the user wants to clean up a voice note or long paragraph into bullets. Keeps the original wording almost exactly — no summarization.
argument-hint: <file-path>
allowed-tools: Read, Edit
---

# Bulletize

Reformat long, unstructured prose sections of a note into bullet points.

## Rules

1. **No summarization.** Keep the author's original wording. You are reformatting, not rewriting.
2. **One cogent thought per bullet.** Break on natural thought boundaries — where the speaker shifts topic, adds a new point, or qualifies something.
3. **Nest related thoughts.** If a thought elaborates on or qualifies the previous bullet, indent it as a sub-bullet.
4. **Only touch prose paragraphs.** Leave these alone:
   - Existing bullet lists
   - YAML front matter
   - Code blocks / comment blocks
   - Headers
   - Short single-sentence paragraphs that already stand alone
5. **Modify in place.** Replace the prose paragraphs with the bulleted version directly in the file.
6. **Only touch the file specified in `$ARGUMENTS`.** Do not modify any other file.

## Process

1. Read the file at `$ARGUMENTS`.
2. Identify sections that are long unstructured prose (multi-sentence paragraphs of rambling text).
3. For each such section, break it into bullets:
   - Each distinct thought or point becomes a `- ` bullet
   - Related sub-points nest with `  - ` (two-space indent)
   - Preserve the speaker's phrasing — rearrange minimally, only to fit bullet format
4. Use Edit to replace each prose section with its bulleted version.
5. Confirm what was changed.
