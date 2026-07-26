# Design Mode — shaping a system before it is built

For a new system, or a substantial new capability inside an existing one.

Prerequisite: the sizing gate in `SKILL.md` is done and the tier is written down. The tier caps what this document is allowed to produce.

The goal is not a complete blueprint. It is **the smallest set of decisions that are expensive to reverse later**, made deliberately, plus a first slice of running code. Everything else is better discovered by building.

---

## Step 1 — Event storm

Event storming finds the model by walking the process in time, rather than by naming things and hoping structure appears.

**Be honest about what this version is.** Event storming works because several people who know different parts of the business collide over the same wall and discover they disagree. One person plus a language model does not reproduce that; Brandolini's own view of the single-participant session is that you are worse off and less likely to notice. Treat what follows as a **degraded mode with deliberate compensations** for the missing second perspective — and treat its main output as a list of questions for a human who actually knows the domain, not as a finished model.

When the developer *is* the domain expert — a personal tool, an internal workflow they run themselves — the gap is small and this works well. When they are not, the honest finding may be that the domain knowledge to model this does not exist yet, and no amount of pattern application substitutes for it.

Work in one artifact, a table or a list. Do not open an editor and start defining classes.

**1a. Flood the timeline with events.** Past-tense facts, in the language the business would use. `BudgetExceeded`, `DraftAccepted`, `PaymentCaptured`. Generate liberally — twenty to forty for a real system — then order them in time. Do not tidy as you go.

Deliberately generate the unhappy path. The things that go wrong are where the model lives; a timeline of only successes produces a model that cannot express failure. Ask directly: what gets reversed, retried, expired, refunded, escalated?

**1b. Add the second perspective artificially.** Generate a further fifteen to thirty events that a differently-positioned person might raise — operations, finance, support, the person cleaning up afterwards — and **tag each with its provenance** as a guess. Then go through them one at a time and accept, reject, or promote to hotspot. This is the deliberate substitute for a second participant, and it only works if the guesses stay visibly marked as guesses rather than blending into the timeline.

**1c. Do not normalize the vocabulary.** When the same thing appears under two names, or one name covers two things, **leave it and mark it**. The inconsistency is the signal you came for — it is how boundaries announce themselves. Tidying names at this stage destroys the primary evidence. Cleanup happens in Step 2, after the boundaries are drawn.

**1d. Mark hotspots.** Anywhere there is disagreement, a word meaning two things, an "it depends," or a step you cannot describe confidently. Do not resolve them — hotspots are the highest-information part of the exercise.

*Hotspot floor:* if you finish with fewer than five, you are fabricating certainty rather than modeling. Go back and find what you glossed over.

**1e. Run the reverse narrative.** Walk the timeline backwards from the final event, asking of each: what must have already been true for this to happen? This is the highest-yield solo step, because it is structural rather than memory-based — it catches missing preconditions and invented ordering that forward narration sails past.

**1f. Find the commands.** For each event, what caused it? A command has an imperative name and an actor: a person, a scheduler, another system, or a policy reacting to an earlier event. Policy-triggered commands (`when X happened, do Y`) are where the process rules actually live.

**1g. Find the read models.** For each command with a human actor: what does that person need to *see* to issue it? Those views are requirements, and they are usually why the read side ends up shaped differently from the write side.

**1h. Mark pivotal events.** The few events where the process changes phase and responsibility hands over — `OrderPlaced`, `PaymentSettled`, `ShipmentDispatched`. Language often shifts around them: the same thing gets a new name on the other side. Those shifts are your candidate boundaries.

## Step 2 — Boundaries

Cluster events between pivotal events. Each cluster where the vocabulary is internally consistent is a candidate **bounded context**: a region within which each term has exactly one meaning.

Draw a boundary where you see:
- **Linguistic drift** — the same word meaning different things on either side. The strongest signal there is. A `Product` in a catalog and a `Product` in stock allocation share a name and almost no fields.
- **Different rates of change** — parts that get modified on different schedules for different reasons.
- **Different lifecycles** — things created, updated, and retired on independent clocks.
- **A consistency divide** — the two sides never need to be transactionally consistent.

**Boundaries are about model integrity, not deployment.** Two contexts in one process, one repository, and one database is the normal and usually correct arrangement. Splitting into services is a separate decision with separate justification (independent scaling, independent deployment, separate teams) and it is not this decision.

**Default to one context.** A second is justified when you can point at a specific word that means two different things. Personal-scale systems almost never need more than two or three; if the count is climbing past that, you are decomposing by noun — by table, by module, by "area" — rather than by language. Merge back until each boundary is defended by an actual conflict.

When two contexts must talk, name the relationship and what protects you at the seam:

| Relationship | Use when |
|---|---|
| **Anticorruption layer** | The other side's model would pollute yours — a legacy system, a third-party API. Translate at the edge; let nothing of theirs past it |
| **Open host / published language** | Several consumers need you; publish a stable contract and version it |
| **Conformist** | You need their data and have no leverage; adopt their model wholesale and stop pretending otherwise |
| **Shared kernel** | A small model deliberately shared, changed only by agreement. Keep it tiny |
| **Customer/supplier** | Upstream will accommodate downstream's needs |
| **Separate ways** | Cheaper to duplicate than to integrate. Frequently correct |

For the common case — one system, one external API — the answer is usually an anticorruption layer, and that is just a translating adapter with your own types on the inside.

**Generate two or three competing decompositions, not one.** For each, name the heuristic that justifies it and what it makes awkward. Then choose, and record why the losers lost. A single decomposition presented without alternatives is a decision that was never actually made — and working alone, the first plausible carving is very hard to see past.

**Sanity check on size.** A bounded context is the *largest* region that has no conflicting models — an upper bound, not a target to subdivide toward. Strict application of this produces good monoliths, not many services. If your boundaries came out numerous and small, you decomposed by noun and should merge back.

### Distillation — decide where the effort goes

Not every part of the system deserves the same care. Sort each context or major area into one of three:

- **Core** — the reason the system exists, where being better than the obvious implementation actually matters. Usually small. This is where the modeling effort, the tests, and your attention go.
- **Supporting** — necessary, specific to you, but not differentiating. Do it plainly and correctly.
- **Generic** — solved problems: auth, scheduling, notifications, storage. Buy, adopt, or write the dullest possible version. Never model these carefully.

Uniform architectural effort across all three is the signature of having skipped this step, and it is expensive in both directions — the core gets underserved while the generic parts get ceremony they will never repay.

This classification also settles most anemic-model arguments before they start: a supporting or generic area implemented as plain data plus procedures is *correct*, not anemic. See `references/patterns.md`.

## Step 3 — Aggregates from invariants

Not from nouns. From rules.

1. Write the invariants as plain sentences. "Allocated stock never exceeds stock on hand." "A grant that has expired cannot be drawn against."
2. For each, ask which objects must be read *and written in the same transaction* to enforce it.
3. That set is a candidate aggregate. Give it the domain's own name — if you have to invent an ugly compound (`GlobalSkuStock`), keep hunting for the word the business already uses.
4. Prefer the smallest boundary that still holds the rule. Smaller means more concurrency.
5. Everything outside the boundary refers to the root by identity, never by object reference.

Then check:
- Does any use case write two aggregates? If so, either they are one aggregate, or the two writes do not need to be atomic and a domain event connects them. Decide which, explicitly.
- Are there bidirectional references? Break one direction and replace it with an id.
- Does anything outside reach past a root to touch its internals? Move the operation onto the root.

Reading another aggregate is fine. Writing two in one transaction is the thing to avoid.

**Immutable records are their own aggregate.** Append-only facts — ledger entries, audit events, readings — do not belong inside a mutable root just because they are related to it. Keeping them separate is what stops the root from growing without bound.

**Prefer deriving state over storing it.** If a value can be computed from facts you already keep, computing it removes a whole class of drift bugs and the reconciliation jobs that come with them. Where the derivation is too slow, store it as a read model, not as truth.

## Step 4 — Use cases

Each command from the storm becomes one function with an imperative name: fetch the aggregate, check preconditions, call one domain method, persist. No business conditionals at this layer.

Write the list of use case names before writing any code. It is the system's API, and it is the artifact most likely to survive every implementation decision you later reverse.

## Step 5 — Decide the reversible-versus-not split

Before implementing, sort your open questions into two piles:

- **Expensive to reverse** — aggregate boundaries, the identity of core concepts, what is stored versus derived, the meaning of the central words, anything that migrates data. Decide these now, and write down why.
- **Cheap to reverse** — framework, storage engine, whether a layer exists, on-disk format, transport. Pick something reasonable, note it as revisable, move on.

Most design paralysis is spending expensive-decision effort on cheap-decision questions.

**Check the cheap assumptions against the real thing before committing to them.** Where a decision rests on how a library, database, or runtime actually behaves, spend the two minutes to find out rather than reasoning from memory. Does this database's `lower()` handle non-ASCII? Are foreign keys enforced by default? How slow is the naive query at the size you actually expect? A design grounded in three measured facts beats one grounded in plausible recollection, and these are exactly the assumptions that are expensive to discover after the schema ships. Record what you verified.

Where a genuinely expensive decision is underdetermined by the requirements, **make the call and state the trigger that would reverse it.** Do not hand back a list of open questions in place of a design; the whole value of doing this before building is that someone made the call.

Then, for the requirements as given: name any that turned out to be ambiguous, and say which reading you designed against. Requirements that hide two different things inside one word — "override", "cancel", "archive" — are the most common place this matters, and finding one is worth more than any diagram.

## Step 6 — Walking skeleton

Implement the thinnest end-to-end slice that exercises the real wiring: one entrypoint, one use case, one aggregate, real persistence, one test at each level you intend to keep.

Do this before building out the model. It forces the infrastructure questions early and gives you something to run. Modeling further before there is a running skeleton is how designs get elaborate in ways the system never justifies.

Then work outward one use case at a time, in domain-first order: write the test in the language of the domain, make the model satisfy it, then wire it.

---

## Output contract

The design document contains these sections, in this order:

1. **Verdict** — tier with signal count, and what is deliberately *not* being built at this size.
2. **Language** — the glossary. Term, meaning, and for the contested ones, what it explicitly does not mean.
3. **Timeline** — the event storm result: events in order, with commands and actors. A table is fine; this is a working artifact, not a diagram exercise.
4. **Hotspots** — the unresolved questions, verbatim, with which ones need a human who knows the domain. Do not resolve these to make the document look finished. This section is the point of Step 1.
5. **Boundaries** — contexts, the alternatives considered and why they lost, and the relationship at each seam. If there is one context, say so in one line and move on. Include the core/supporting/generic classification.
6. **Aggregates** — each with the invariant that justifies it, its root, what it contains, and what it references by id.
7. **Use cases** — the list of named operations.
8. **Decisions** — expensive-to-reverse calls, each with its reasoning and its reversal trigger. Ambiguities in the requirements and which reading was chosen. Any assumption you checked against the real runtime, with what it turned out to be.
9. **Skeleton** — the first slice, and the order of the next few.
10. **Not building** — patterns ruled out by tier, one line each.

**Sections are gated by tier. Omit the ones your tier does not reach — do not write a section explaining that it does not apply.**

| Tier | Sections |
|---|---|
| CRUD | 1, 2, 9, 10 |
| Thin | 1, 2, 3, 4, 7, 8, 9, 10 |
| Core | all except 5 if there is only one context |
| Full | all |

A Thin-tier design that contains an Aggregates section has contradicted its own verdict on the first page. If the tier ruled a pattern out, its section does not exist; the one-line reason belongs in section 10 with the other ruled-out patterns.

**Budget, because omitting sections does not by itself make a document shorter — the prose just redistributes.**

| Tier | Target |
|---|---|
| CRUD | under 600 words |
| Thin | under 1,500 |
| Core | under 3,000 |
| Full | as long as the evidence needs |

Spend the budget on hotspots, decisions and their reversal triggers — those are the parts that are expensive to reconstruct later. Cut explanation of patterns the reader can look up, restatement of the requirements back to the person who wrote them, and any passage justifying a section that the tier already ruled out. Going over budget is a signal to cut, not a rule to obey blindly; going over by 3× means the design is describing a system more elaborate than the one being built.

## Red flags

- More than three bounded contexts for a system one person maintains
- An aggregate named for a table
- Contexts drawn by noun (`UserContext`, `OrderContext`) rather than by a word that changes meaning at the boundary
- A timeline containing only successful outcomes
- Fewer than five hotspots — certainty was fabricated somewhere
- Vocabulary tidied up during the storm, erasing the inconsistencies that mark the boundaries
- Every area getting the same architectural care, meaning distillation was skipped
- Aggregates and repositories designed while the boundary and language work was skipped — the tactical patterns without the strategic ones is the most common way this material gets misapplied
- Ending on a list of open questions in place of decisions with triggers
- Designing past the walking skeleton before any of it runs
- Events introduced because the flow is long, rather than because a downstream failure must not unwind the upstream work
