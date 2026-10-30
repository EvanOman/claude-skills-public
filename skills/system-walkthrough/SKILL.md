---
name: system-walkthrough
description: Create source-grounded interactive system guides with selectable maps, request walkthroughs and scenario sequences using a bundled offline renderer. Use for explaining a bounded behavior in an existing codebase; use explorable for executable algorithm simulations and design-polish for restyling an existing page.
---

# System walkthrough

Explain one reader question through a concrete trace and the state that makes it
work. Use the bundled components; spend the work on evidence, abstraction and prose.
The result is a portable HTML document, not a new application backend.

## Establish the explanation

Name the reader, question and bounded behavior. Pin source revisions before drawing.
Read applicable project contracts, identify owners/state/boundaries, then follow one
real call path through completion and one useful counterexample or recovery path.
Read [evidence.md](references/evidence.md) while researching. Completion means each
significant causal claim has a primary-source pointer and its preconditions are known.
Mark unknown deployment/runtime facts; do not infer them from library capability.

Choose the figures that answer the question: a stable map for ownership, an ordered
walkthrough for one path, and a sequence for exchanges or competing scenarios.
These are optional views, not a required chapter count. Keep the SAME node identities
and positions across map lenses and walkthrough steps. Selective highlighting should
teach a path while leaving the rest of the system recognizable.

## Author structured content

Read [model.md](references/model.md). Use the skill's own directory when resolving
the following scripts and assets; the current working directory can be the target.

```sh
node <skill>/scripts/walkthrough.mjs init model.json
node <skill>/scripts/walkthrough.mjs validate model.json
node <skill>/scripts/walkthrough.mjs build model.json output-directory
```

`init` copies a small HTTP cache example; replace it with the researched system.
Write plain-text content in one JSON model. Declare semantic roles, primary sources,
claims, map geometry, highlight sets, ordered steps and scenario state snapshots.
Do not hand-edit generated HTML or fork the renderer per guide. Validate early so
broken IDs, missing evidence and incomplete states are found before layout work.
Warnings about missing reverse exchanges require an editorial decision, not automatic
invention of a reply.

Trace requests AND their supported responses. Distinguish a return value, an ack,
persisted state and successful business work. Local state changes are local events,
not imaginary network messages. Show who owns what AFTER each step, so jumping
directly to a step yields the same explanation as stepping through in order.
Label scenarios as source-derived illustrations; describe condensed repetition,
the chosen interleaving and assumptions. Never present invented timings as telemetry.

Build emits one self-contained `index.html`: no reading-time packages, remote assets,
model calls, credentials or network fetches. Embedding in another page is also supported
through `SystemWalkthrough.mount(element, model)` and the bundled CSS; see model.md.
Publish only within the user's authorization and the destination's normal workflow.

## Verify the reader's experience

Read [quality.md](references/quality.md) before delivery. Open the actual output in
a browser and exercise every lens, intent, step, scenario and return path, direct
selection, keyboard navigation, theme persistence and text alternatives. Inspect at
1440, 900 and 390 widths; label fit and directionality need visual inspection, not
just a schema pass. Dense diagrams may scroll internally; the document must not.

For a substantial guide, give an independent reviewer the model and primary sources.
Ask for counterexamples, unsupported causality and misleading arrows. Fix material
findings, rebuild from the model, and rerun affected checks. Report tested behavior,
source revision, remaining uncertainty and the actual artifact/publication location.

The authoring helper's tests run with `node --test <skill>/tests/*.test.mjs`.
See [the public showcase](https://evanoman.com/system-walkthroughs/) for two complete
models. [Forward-test evidence](references/forward-tests.md) records observed failures
and narrow corrections. The skill-local MIT license covers
the new tooling; preserve upstream attribution and licenses for any reused material.
