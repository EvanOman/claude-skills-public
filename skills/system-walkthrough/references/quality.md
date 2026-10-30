# Design and verification

Use the same semantic role colors in all figures. Write short node labels and
plain-language explanations adjacent to the active view. The active path should be
obvious without relying on color alone; keep other context recognizable. Choose map
coordinates deliberately. No layout service is required. Give requests and returns
separate routes where they would overlap. Check arrowheads at BOTH active and muted
states. Do not trade readable SVG text for fitting every diagram on a phone.

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

At 1440, 900 and 390 widths:

- Visit every lens and intent. Confirm stable node positions and the intended active
  nodes/edges, including return direction. Inspect long labels, crossings and clipping.
- Select first, middle and last steps directly and via Previous/Next. Try arrows,
  Home and End from the step controls. Focus and the active inspector must agree.
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
