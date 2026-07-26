# Audit Mode — reworking a system that already exists

For a system that works but has sprawled: features threaded through a growing orchestrator, the same logic in three places, no obvious home for the next change.

Prerequisite: the sizing gate in `SKILL.md` is done and the tier is written down. Without it, this degenerates into a survey of every pattern.

---

## Step 0 — What problem are we solving?

Before reading any code, get one sentence: *what is bad right now?* Hard to change? Slow? Producing wrong results? Frightening to touch?

If the answer is "nothing specific, it just feels messy," that is a legitimate answer, but say so — it means the audit is optimizing for future change velocity and every recommendation must be justified in those terms, not by correctness.

The problem statement is the ranking function for everything below. A finding that does not connect to it goes at the bottom or gets cut.

## Step 1 — Gather evidence before forming opinions

```bash
bash <skill-dir>/references/probe.sh <repo-root>
```

Read-only; writes nothing. It reports size, layer naming, domain imports of infrastructure, ORM coupling, commit sites, query dispersion, config access, test shape, side-effects-with-branching, the actual vocabulary, and 120-day churn.

Then read, in this order:
1. The largest two or three modules the probe named — orchestration hides there.
2. The entrypoints — every route, CLI command, cron job, queue consumer. **This list is the system's real use cases**, whatever the code calls them.
3. The persistence models.
4. The tests — what they cover tells you what is already safe to change, which sets the refactor order.

Note the probe's churn output. Structure that is not being modified is not costing anything. **Rework where the change pressure actually is.**

## Step 2 — Build the language table

Extract every domain-ish noun from class names, table names, and function names. For each, answer: what does it mean, and does the codebase use it consistently?

Three findings live here, and they are usually the cheapest fixes in the audit:

- **One word, several meanings.** A term that denotes a scanner's dict, a database row, and a rendered card is three concepts sharing a name. Split them and name each.
- **Several words, one meaning.** Synonym drift — agents adding features tend to coin a fresh word rather than find the existing one.
- **A word the business uses that the code lacks.** Usually the most important finding in the whole audit: a missing concept is why logic has no home and gets copied instead.

Look at scripts, admin tools, and log lines. Peripheral code often already invented the better vocabulary because it was written closer to how the system is talked about.

Six checks that turn this from impression into evidence. Run them; each failure is citable.

| Check | How | Failing signal |
|---|---|---|
| **Verb test** | List the public methods on the core types | All `get`/`set`/`create`/`update`/`save` and no domain verbs (`approve`, `reconcile`, `settle`, `expire`, `escalate`). CRUD-only verbs mean the language stopped at the database — usually the single most informative check |
| **Weasel-word census** | Grep core modules for `Manager`, `Handler`, `Processor`, `Helper`, `Util`, `Info`, `Data`, `Item`, `Object` | Any hit inside the core. These names are fine in infrastructure and are placeholders in the domain. Test: could you say this word to a stakeholder without translating? |
| **Ticket-to-code** | Take the last ~20 issues, commits, or TODOs. Grep their main nouns and verbs | The term is absent from the code, or present under a different name |
| **Synonym collision** | Look for two names for one concept, or one name for two | Both are findings; the second is worse |
| **Boundary honesty** | Find a word used in two or more modules. Compare its fields and behavior in each | Same word, materially different meaning, no translation between them — that is an undeclared boundary, not a naming nit. Escalate it |
| **Rename cost** | Ask what it would cost if the business renamed a central concept tomorrow | "It's in forty files and a column name" means the language is fossilized, not shared |

Failing the verb test or ticket-to-code means the system does not have a ubiquitous language, whatever its folder structure looks like.

## Step 3 — Find the invariants and check who enforces them

List the rules that must hold. For each: **where is it enforced, and how many places would have to be wrong for it to break?**

This is the heart of the audit. A rule enforced in one place is fine wherever it lives. A rule enforced in five places, or enforced nowhere but assumed everywhere, is the finding — and it names the aggregate that should exist.

Then the two mechanical checks:

- **Transaction test.** Does any single operation write two things that must both succeed? If yes and there is no transaction around them, that is a live data-loss bug, not a style issue — rank it accordingly.
- **Repository test.** If repositories exist, do any return non-root entities?

## Step 4 — Write the findings

Ordered by damage to the problem from Step 0. Not by chapter, not by layer, not by pattern.

Each finding, in this shape:

```
### <short name in the system's own words>

Evidence:   <file:line>, <file:line>
Rule:       <the invariant or concept currently unenforced or unnamed>
Cost today: <a real symptom — a bug, a duplicated edit, a thing that cannot be tested>
Fix:        <the specific change>
Size:       S | M | L
```

`Cost today` is the field that keeps this honest. If it can only be filled with "harder to maintain," the finding is speculative — mark it as such or drop it. Prefer findings where you can point at code that is *already* wrong over findings about code that might become wrong.

Findings that recommend a pattern above the tier do not go in the report. If one feels genuinely necessary, that means the gate was scored wrong — go back and rescore it with the new evidence rather than smuggling the pattern in.

## Step 5 — Sequence the work

Findings are not a plan. Convert them into an ordered list of changes where **each step is independently shippable and leaves the tests green.** For each: what it touches, what it unblocks, roughly how big.

Ordering rules that hold up in practice:

1. **Fix live bugs first**, especially any the audit uncovered. Never refactor over a known correctness bug — you will lose track of whether the refactor caused it.
2. **Get a test around it before moving it.** Where coverage is missing for something about to change, adding a characterization test is its own first step.
3. **Seams before models.** Extracting an I/O boundary — an LLM client, a notifier, a store — is mechanical, low-risk, and makes everything after it testable. Model changes before seams means changing untested code.
4. **Names before structure.** Renaming is cheap, reviewable, and often reveals that a planned structural change is unnecessary.
5. **One aggregate at a time**, each with its use cases moved along with it.
6. **Duplication is acceptable in transit.** Copying a use case to a new home and cleaning it there beats a chain of calls into the old mess. Delete the original once callers move.

Do not propose a big-bang rewrite. If the honest answer is that a subsystem should be rebuilt, describe it as a strangler: raise events or write an adapter at the boundary, build the replacement beside it, cut over, delete.

## Step 6 — Say what you are not doing

A short, explicit list of patterns considered and ruled out, each with a one-line reason tied to this system.

This section is load-bearing. It is what stops the next agent that reads the audit from treating the omissions as oversights and helpfully adding a message bus.

## Step 7 — Make it stick

An audit that lives in a chat window is spent effort. The sprawl returns because nothing tells the next agent where things go.

Propose a short block for the repo's `AGENTS.md` / `CLAUDE.md`:

- the glossary (terms, one line each)
- the layer rule for this repo, with the actual directory names
- where new use cases go, and how they are named
- the aggregate boundaries, and the one-per-transaction rule
- the patterns deliberately not used here, so nobody re-adds them

Six to fifteen lines. This is the highest-compounding output of the whole audit: it converts a one-time cleanup into a constraint that holds while agents keep building.

---

## Output contract

The report contains these sections, in this order, and no additional sections of its own invention.

If the person who asked for the audit also asked for something else — a critique of this skill, a cost estimate, an answer to a specific question — deliver it, but outside the report rather than as an extra section inside it. The contract governs the shape of the report, not the shape of your whole reply.

1. **Verdict** — tier with signal count, and in the first paragraph the honest headline: what is actually wrong and roughly how much work it is. If the system is fine, say that here.
2. **Language** — the term table, with disagreements marked.
3. **Findings** — ordered by damage, in the shape above. As many as the system has. Not one per pattern.
4. **Sequence** — numbered steps, each shippable and test-green.
5. **Not doing** — patterns ruled out, one line of reasoning each.
6. **Also noticed** — non-architectural defects found while reading. A bare list, one line each, no analysis, clearly marked as outside the audit's scope. Real bugs go here unless they are caused by the architecture, in which case they are findings. **Cap this at the five most consequential** and say how many more you are omitting — past that it stops being a footnote and starts competing with the audit for attention.
7. **Contract patch** — the proposed `AGENTS.md` block.

Length follows the system. A small tool gets a short report; padding it to look thorough is the failure mode this contract exists to prevent.

## This mode does not change code

The audit is advisory. It ends with the report. Do not edit, refactor, or "just fix" anything while producing it — not the live bug you found in finding 1, not the one-line rename that is obviously safe, not the missing test.

The reason is not caution about mistakes. It is that the sequence in Step 5 is a set of options ordered by value, and choosing where to enter it is the human's call — one they cannot make if the first few steps have already been taken. An audit that arrives with three commits attached has answered a question nobody asked.

If something you find is urgent, say so at the top of the Verdict and let them decide.

Executing the sequence is a separate piece of work, started deliberately from the approved report.
