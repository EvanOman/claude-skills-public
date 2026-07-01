# Explorable design principles

Distilled from Bret Victor (Learnable Programming, Ladder of Abstraction, Explorable Explanations), algorithm-visualizer.org, visualgo.net, thesecretlivesofdata.com/raft, Distill.pub, Red Blob Games, and the reference LSM-tree visualiser build.

## Non-negotiables

1. **Learner controls time.** Pause, step, replay. Autoplay-only is a screensaver. A speed slider is not time control.
2. **State is always visible.** State-as-position (everything laid out spatially); animation only for *transitions* between visible states. Never hide state behind clicks or time.
3. **Defaults are pedagogically chosen.** Open with a live, interesting example — never a blank canvas. Cycling defaults (LSM's fruit sequence) make every interaction one click and every record traceable.
4. **Learner-supplied inputs.** The demo→tool jump happens when you can inject your own data and test "what if?".
5. **Two channels: picture + words.** An annotated event log ("WAL append → memtable insert → flushed") correlates cause and effect. Narration bridges animation to understanding.
6. **Show the contrast.** Make the naive alternative's cost visible on screen. The intuition usually *is* the delta (consistent-hash vs mod-N key movement; LSM sequential writes vs in-place updates).
7. **Multiple simultaneous representations.** Same system at 2–3 levels, linked (Victor's ladder): the structure, the operation trace, the aggregate metric.

## Layout pattern by subject type

| Subject | Dominant pattern | Time model |
|---|---|---|
| Algorithm (sort, search) | Code panel + canvas, current-line highlight | Discrete steps, scrub 1:1 with code lines |
| Data structure (LSM, B-tree, hash) | Spatial tiers + operation log; read path vs write path visualized | Operation-driven; animate the path |
| Distributed system (Raft, 2PC) | Nodes visible simultaneously, messages animate between, failure-injection buttons | Event-driven; narration pane critical |
| Math / ML | Sliders + reactive plots, scrubbable numbers | Often no time — parameter exploration |
| State machine (TCP, GC) | State diagram, current-state highlight | Transition-driven |

## Failure modes (each has sunk a real visualizer)

- Autoplay with no pause — forces the system's pace on the learner
- Animation without labels — pretty, teaches nothing
- No custom inputs — canned demo, can't test hypotheses
- Everything animating at once — cognitive overload; animate one thing, stagger the rest
- Requires knowing the algorithm already to decode the picture — narration is the fix
- No reset — learners must re-run experiments freely
- Logic tangled into UI state — the implementation should itself be a readable study artifact (separate, tested module)

## Stack conventions

FastAPI + one logic module + plain JS, no build step; triptych CSS grid; color-coded component cards; `.checking`/`.found`/`.miss` animation classes; event log capped at ~80 entries; mutating endpoints return the new state inline (no polling); `get_with_trace`-style methods so animations replay *real* execution, never a fabricated narrative.
