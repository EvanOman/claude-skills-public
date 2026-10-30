# Design and verification

Use the same semantic role colors in all figures. Write short node labels and
plain-language explanations adjacent to the active view. The active path should be
obvious without relying on color alone; keep other context recognizable. Choose map
coordinates deliberately. No layout service is required. Give requests and returns
separate routes where they would overlap. Check arrowheads at BOTH active and muted
states. Do not trade readable SVG text for fitting every diagram on a phone.

## Read while looking at the diagram

Treat the diagram, active explanation and navigation as one reading task. They
must remain available together at the actual viewport height. A diagram that fits
its own scroll panel can still push the explanation or Next button off the screen.
Do not enlarge an SVG merely to fill a wide document column; use native readable
scale and a bounded viewport when the diagram is dense.

The shared renderer budgets a reading stage for the viewport: diagram/reader
columns where space permits, bounded rows on narrow screens, and navigation outside
the scrolling panels. Keep the active title and explanation first. Put long state,
evidence, assumptions and the full step index behind scrolling or disclosures that
preserve access to the diagram and navigation. Reset the reader to its new heading
on selection; reveal relevant diagram content without scrolling the outer document.
Primary section links should reach this usable reading area.

For very short windows or large browser zoom, allow document scrolling rather
than crushing both panels into unusable strips. Verify that this fallback leaves
all content and controls reachable; test a short landscape viewport as well.

Test a reader task, not just isolated controls: read a highlighted step, advance
three times with the pointer, move back with the keyboard, inspect state/evidence,
then choose a distant step or another scenario. The diagram, current explanation
and Previous/Next must be visible together throughout the task. Avoid overlays
that cover the active diagram, tiny text, or a toolbar that consumes the whole
mobile screen. Preserve full text and unbounded print alternatives.

## Browser checks

For output built by the bundled helper, run the optional dependency-free verifier
(Node 22+ and a local Chrome/Chromium executable; set `BROWSER_BIN` if discovery fails):

```sh
node <skill>/scripts/browser-check.mjs output-directory/index.html --out evidence-directory
```

It exercises the model's actual controls at all three widths, compares full state
snapshots and highlights, checks rendered arrow endpoints/markers and node label
fit, uses real keyboard events, and writes JSON plus screenshots. It also checks
offline resource requests, theme reload/denied storage and reduced motion. It does
not establish factual truth or replace visual inspection of crossings and pedagogy.
Its mutation test proves that removing a real arrowhead is detected:

```sh
RUN_BROWSER_TESTS=1 node --test <skill>/tests/browser-check.test.mjs
```

Use realistic width **and height** pairs, including 1440×800, 900×700 and 390×844:

- Visit every lens and intent. Confirm stable node positions and the intended active
  nodes/edges, including return direction. Inspect long labels, crossings and clipping.
- Select first, middle and last steps directly and via Previous/Next. Try arrows,
  Home and End from the step controls. Focus and the active inspector must agree.
- Anchor the reading stage and verify that the diagram viewport, active title/body
  and navigation intersect the physical screen together. Exercise real pointer or
  keyboard input without a preparatory page scroll before every Next click. Scroll
  the reader into long state/evidence, then advance: the next heading must reappear
  while the diagram and navigation remain available. Check actual hit targets and
  clipping; injected `.click()` calls can activate invisible controls.
- Visit every scenario and event, including return paths and failure endings. Change
  scenarios after reaching their last event; state must reset to the new first event.
  Jump backward/directly to prove snapshots do not depend on visit history.
- Check text alternatives, SVG title/description, visible focus, meaningful button
  labels, source links and information that would be lost without color.
- Confirm no document horizontal overflow. Scroll a dense diagram inside its labeled
  viewport with keyboard and touch-sized controls. Read labels at their actual size.
- Toggle theme, reload, deny storage, and enable reduced motion. Nothing should animate
  automatically. Print/text reading should expose the causal story and evidence.
- Open the exported HTML from disk or block all network requests. Interactions must
  still work. Inspect console/errors, unexpected requests and broken anchors.

Keep screenshots and a compact interaction result as evidence. A schema pass and
an HTTP 200 cannot establish these behaviors. When publishing, repeat key interactions
on the deployed document and check its source revision to catch stale or SPA fallback
responses. Do not publish private review notes, local paths or source material.

## Adversarial content review

Ask a reader who did not author the explanation to find a concrete counterexample:
an ack without successful work, a snapshot that stays old after a commit, a retry
that duplicates an effect, a checkpoint that only partly completes, or a request
that never gets the implied response. Let the sources decide disagreements.

Distinguish static source review from runtime verification. Report browsers/devices
actually exercised and any significant source or behavior not verified. Fix observed
problems narrowly; do not accumulate an elaborate framework from hypothetical cases.
