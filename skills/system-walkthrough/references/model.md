# Authoring model (version 1)

Author one JSON document. All prose is plain text, never HTML. IDs are unique within
their collection and use lowercase letters, numbers and hyphens. No renderer edits
are needed for a new system. See `examples/starter.json` for a small complete model.

## Document and evidence

- `version`: `1`.
- `title`, `subtitle`, `question`, `scope`: reader-facing strings. Scope names the
  relevant configuration, preconditions and excluded behavior.
- `takeaways`: array of `{text, claims}` objects; explain the few things to remember.
- `project`: `{name, repository, revision, version, license}`. Repository is an HTTPS
  URL; revision is a full 40-character Git commit. Version is a human-readable label.
- `sources`: array of `{id, label, url, note?}`. Prefer immutable links into the pinned
  revision with line ranges or symbols. For live documentation, explicitly say so in
  `note`. Use original source URLs, not search results or local paths.
- `claims`: array of `{id, text, kind, sources}`. Kind is `source` or `inference`;
  sources are source IDs. Record the exact supported assertion, not just a topic.
  Inferences must explain their assumptions. A citation is not proof by proximity.
- `roles`: array of `{id, label, color}`. Color is one of `teal`, `blue`, `amber`,
  `violet`, `rose`, `slate`. Pick semantic roles for this system, then reuse them.
- `limits`: strings explaining material uncertainty and simplification.
- Optional `navigation`: array of `{label, url}` for ordinary page links. URLs may
  be relative paths, anchors or public HTTPS destinations. Omit when embedding.

Figures are optional: provide a map, sequence, or both. A walkthrough needs the
map. Match the views to the question rather than filling every possible section.

## Stable map

`map` has `title`, `description`, `width`, `height`, `nodes`, `edges`, `lenses`,
and `intents` (which may be empty). Coordinates are SVG units. Keep labels readable;
use an internal scroll viewport rather than scaling a dense map down to a phone.

Nodes are `{id, label, detail, role, x, y, w, h}`. `x,y` are the top-left corner;
`w,h` are box dimensions (start around 200 × 84). Label is one short line;
detail is one short secondary line. Each node represents one owner or relevant
state store at the chosen level of abstraction.

Keep box heights large enough for wrapped detail text; inspect the built glyphs.
Start with short labels, then use an extra row or more width when it is earned.
The reading stage keeps the diagram beside or above its explanation within a
viewport budget. Large coordinates mean internal panning, not automatic enlargement
or text shrinking. Keep the selected path understandable in that bounded view.

Edges are `{id, from, to, label, kind, via?}`. Endpoints are node IDs; `kind` is
`request`, `return`, `data`, or `control`. The renderer clips straight edges to
node boundaries. Optional `via` is an ordered array of `{x,y}` waypoints for
routes that need a bend. Arrowheads always point to `to`, including return edges.
Use distinct routes for request and response if they would overlap.
Labels sit over the middle route segment (or waypoint for an odd point count).
An extra collinear waypoint can select a clearer segment without moving the route.
The validator does not detect label collisions; inspect the actual SVG.

Lenses and intents share `{id, label, title, body, nodes, edges, claims}`. Nodes,
edges and claims are ID lists. Lenses ask different questions of the SAME map;
they do not move its nodes. Intents trace a concrete user action. Endpoints of
highlighted edges are included automatically. Other context stays visible.

## Walkthrough on the same map

`walkthrough` has `title`, `intro`, `steps`. A step is
`{id, title, body, why, boundary, nodes, edges, claims, state}`. The ID lists use
the map and claim IDs. `boundary` says what boundary is crossed, or explicitly
that this is local work. `state` is an array of `{owner, detail}` strings describing
the state AFTER this step. Use explicit full snapshots of relevant state, not
an ambiguous patch carried over from whichever step the reader visited last.
Lead `body` with the explanation a reader needs while viewing the highlight. Keep
supporting `why`, state and evidence available without making the primary text or
navigation depend on scrolling past the diagram.
Titles omit numeric prefixes; the controls number them automatically. State snapshots
must distinguish an empty object from one that has not yet been created, and receipt
of a request from its completion or reply.

## Multi-lane sequence

`sequence` has `title`, `intro`, `lanes`, `scenarios`.

- A lane is `{id, label, role}`. Labels are short. A lane can be an actor, file,
  logical structure or service; explain logical structures so readers do not infer
  separate network processes. `role` references a semantic role.
- A scenario is `{id, label, summary, assumptions, events}`. Assumptions is an array
  of strings. Label it source-derived and illustrative; no fabricated timing.
- An event is `{id, from, to, label, kind, body, claims, state}`. Endpoints are lane
  IDs. Kinds match map edges. `from === to` means local work and renders as a loop.
  Each event has a full relevant post-event state snapshot, as above.

An arrow represents a supported exchange or explicitly named logical data movement.
Include response/result/ack returns where they matter to causality. Distinguish a
command reply from durable completion or successful business processing. An idle
timeout, cancellation or crash is not itself a network message. Represent it as a
local event, then show actual effects and supported recovery steps. State the chosen
interleaving; sequence order alone does not prove global serialization.
Include a short ordinary successful path before recovery alternatives when that
comparison helps the reader. A return closes a preceding request; use a local control
event for a call omitted at this abstraction level. The validator warns on an
unopened return, but does not enforce a call stack for condensed narratives.

## Build and component API

```sh
node scripts/walkthrough.mjs validate model.json
node scripts/walkthrough.mjs build model.json output-directory
```

`build` emits one self-contained `index.html` with the model, CSS, renderer, text
alternative and theme initializer embedded. It works at any path and from a local
file; reading requires no fetches. Regenerate rather than editing the output.

For an existing page, load `assets/walkthrough.css` and `assets/walkthrough.js`,
then call `SystemWalkthrough.mount(element, model)`. The mount owns only that
element. Use `assets/theme-init.js` in the head before CSS for a saved theme
without first-paint flashing. The single-file builder is the simplest default.
