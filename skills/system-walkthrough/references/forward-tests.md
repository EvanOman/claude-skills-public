# Forward tests and review corrections

The first release was exercised on two public systems by separate native agents.
Each received the skill, a realistic reader question and raw pinned sources, without
the designer's intended diagram or answer. Both authored JSON and used the shared
renderer unchanged. Their choices were then reviewed independently, including a
subscription-backed Claude source review, a native adversarial browser review and
a native security review. The final examples are in `examples/`.

| Guide | Bounded question | Source pin |
| --- | --- | --- |
| Valkey 9.1.3 | Delivery, acknowledgement and recovery of one consumer-group entry | `29173b67508b2de4c0f0ed551f3b47cf19fc3ca8` |
| SQLite 3.54.1 | An old reader, a newer WAL commit and partial checkpoint progress | `57520227c95396ce39ba6ecaac8d475b78dd3964` |

## Problems observed, and the narrow corrections

| Observed problem | Change |
| --- | --- |
| Authors had to inspect the builder to discover optional page navigation. | Documented `navigation` in the model reference. |
| Pre-numbered step titles duplicated the renderer's numbers. | Documented unnumbered titles. |
| A return-route label landed on a bend. | Documented middle-segment placement and collinear waypoints; corrected authored geometry. |
| Valkey's first draft compared two recovery cases without an ordinary baseline. | Added normal completion before the two failure alternatives; guidance now asks for this comparison when useful. |
| Empty consumer PEL snapshots implied records that were not necessarily created. | Stated prior consumer registration with pinned creation evidence; guidance distinguishes absent from empty state. |
| SQLite's first draft excluded slot-0 readers from checkpoint barriers. | Corrected the claim and takeaway: a held slot-0 read lock can block all backfill. Added the actual lock acquisition and reader-entry code pointers. Guidance now checks negative claims at the enforcement path. |
| SQLite's copy arrow visually assigned work to a file; a local COMMIT result was styled as an unopened return. | Copy arrows now originate at the checkpoint operation; the condensed COMMIT result is local control. Validator warns on unopened returns. |
| An event became active while its arrow remained off-screen on phones. | Shared renderer reveals the selected exchange horizontally. Geometry checks detect the old failure and pass the fix. |
| Lane names scrolled away when a lower sequence row became active. | A sticky header preserves lane names and horizontal alignment while the event viewport scrolls. |
| The desktop inspector could clip the right edge of an intrinsically wide map. | The shared layout stacks the inspector when the complete diagram cannot fit beside it. |
| Direct embedding could inject CSS through an unvalidated map width. | Runtime dimensions must be finite positive numbers; custom width uses `style.setProperty`. The exact exploit was rejected on recheck. Packed builds already validated dimensions. |

## Final verification

On 30 October 2026, the final generated Valkey page passed 2,565 browser assertions
and SQLite passed 2,966 across 1440, 900 and 390 widths. Checks cover every control,
direct/reverse selection and full snapshots, highlighted nodes/edges, actual arrow
endpoints and visible destinations, node text fit, real keyboard events, theme
reload, denied storage, reduced motion and local-file reading with zero remote
assets. All eight helper/mutation tests passed. A mutation test removes rendered
arrowheads, freezes horizontal scrolling and disables sticky headers; the browser
verifier detects all three.

Thirty-three primary source URLs were checked against exact Git objects and line
bounds, or live documentation HTTP responses with unpinned status disclosed. Source
review checked causal claims separately from UI checks. Security recheck found no
remaining actionable finding in the reviewed build/embedding paths.

These are source-derived illustrative guides. Neither upstream database/server was
built or executed for this release, and no production configuration or durability
claim was tested. Browser tests used desktop Chromium at resized viewports, not
physical phones, Safari or a screen-reader session. Print expansion and no-script
text are provided; a physical print/accessibility-device audit was not performed.
