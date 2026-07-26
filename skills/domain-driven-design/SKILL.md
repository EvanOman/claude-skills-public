---
name: domain-driven-design
description: Use when designing a new system's structure or reworking an existing one around its business domain — triggers include domain-driven design, DDD, Cosmic Python, Architecture Patterns with Python, bounded contexts, ubiquitous language, aggregates, event storming, anemic domain model, or a codebase that has sprawled and needs architectural refactoring rather than a bug fix.
---

# Domain-Driven Design

## Overview

Two jobs: shape a system before it is built, or rework one that already exists. Both rest on one idea — **the structure of the code should mirror the structure of the problem, and the words should match the ones a person who understands the business would use.**

Everything else in DDD is a trade you make to protect that idea when it comes under pressure from databases, frameworks, and concurrency. The trades cost real complexity. Most systems earn only a few of them.

## Both modes are advisory

This skill produces reports and designs. It does not change code — no refactors, no renames, no "obvious" one-line fixes applied along the way, in either mode.

Its output is a set of options ordered by value. Choosing which to take, and when, belongs to the person who owns the system. Implementation is separate work, started deliberately from an approved report.

## Which mode

| Situation | Mode |
|---|---|
| No code yet, or a new subsystem being added to an existing one | **Design** → read `modes/designing.md` |
| Code exists and works; the question is how to restructure it | **Audit** → read `modes/auditing.md` |
| Code exists and is being extended with a substantial new capability | Design mode for the new capability, scoped by an audit of the boundary it touches |

Both modes start with the same gate. Score it before opening the mode file.

Scoring it needs evidence, so in audit mode go and get that evidence first — run the probe named in `modes/auditing.md`, skim the entrypoints and the largest modules, check the churn. That is targeted inspection in service of the gate, not the audit proper. What the gate rules out is forming *findings* before the tier is set.

## Step 1 — Sizing gate (always first)

DDD is a set of paid options, not a standard of correctness. The books that teach it say plainly that a CRUD wrapper around a database needs neither a domain model nor a repository. Honor that.

Score the system on these seven signals. Each is either present or not — decide from evidence, not impression. For an audit, cite files. For a design, cite the requirement.

1. **Invariants.** Can you write three or more rules that must hold *across* multiple objects or rows, where violation causes real harm? Field validation does not count — an invariant spans things. ("A room cannot be double-booked." "Allocated stock can never exceed stock on hand.")
2. **Contested language.** Are there two or more concepts that mean different things in different parts of the system, or that the code names differently from how a person would say it?
3. **Lifecycle.** Does anything move through states with rules about which transitions are legal?
4. **Multiple entrypoints.** Is the same operation reachable from more than one place — web, CLI, cron, webhook, queue?
5. **Consistency under concurrency.** Can two operations run at once and corrupt state or double-spend?
6. **Change pressure.** Is this code actually being modified regularly? (`git log --since='90 days ago' --name-only` — real churn, not one big initial commit.)
7. **Longevity.** Will this still be running and changing in six months?

**The tier sets which patterns are eligible. Patterns above your tier are not available as recommendations, however tempting.**

| Signals | Tier | Eligible |
|---|---|---|
| 0–1 | **CRUD** | Good names. Value objects for constrained primitives. Pure functions separated from I/O. **Nothing else.** |
| 2–3 | **Thin** | The above, plus: one named module where the rules live; dependencies passed in rather than imported at point of use. No repository, no unit of work, no aggregates, no events. |
| 4–5 | **Core** | The above, plus: a domain package obeying the import law; aggregate boundaries derived from invariants; a service layer of named use cases; repository and unit of work **if** a single operation genuinely writes several things that must succeed or fail together. |
| 6–7 | **Full** | The above, plus: domain events and a message bus; read models separate from the write model; explicit context boundaries; a composition root. |

### Scale cap (apply after scoring — it can only lower the tier)

The seven signals measure how complicated the *problem* is. They say nothing about how big the *system* is, and a small system with a rich problem still cannot carry much machinery. Cap the tier:

| System | Maximum tier |
|---|---|
| Under ~1,000 lines, or a single script | **Thin** |
| Under ~5,000 lines with one maintainer | **Core** |
| Anything larger, or more than one maintainer, or genuinely separate processes that must stay consistent | **Full** available |

A personal tool with a rich domain lands at Core, not Full. That is the correct answer: name the concepts, put the rules in one place, keep I/O at the edges — and skip the message bus, the read-model machinery, and the context map, all of which pay off through coordination costs that a single maintainer does not have.

Report the cap when it binds: `Signals 6/7 → Full, capped to Core by size (2.7k LOC, one maintainer)`. The uncapped score is still worth stating, because it is what predicts the next threshold.

**Write the verdict down before going further, in this form:**

```
Tier: <CRUD|Thin|Core|Full>  (<n>/7, capped by <reason> | uncapped)
Signals present: <list, each with its evidence>
Signals absent:  <list>
Eligible: <patterns>
Ineligible for this system: <the named patterns you are now forbidden to recommend>
```

Recheck trigger: name the one thing that, if it changed, would move the tier. ("If a second writer appears, this becomes Core.")

### The gate is allowed to return "almost nothing"

A CRUD or Thin verdict is a successful outcome, not a failure to find work. If that is the answer, say it in the first paragraph, give the two or three things that do pay at that size, and stop. Do not soften it into a phased plan toward Full.

If the request was explicitly to *learn* the patterns rather than to ship the system, say which tier the system earns, then build the fuller version anyway and label the extra layers as practice. Do not silently split the difference by producing two designs and asking the user to choose — make the call, state the trigger that would change it.

## Step 2 — Language before structure

The highest-leverage move, and the cheapest. Before drawing any boxes:

Produce a glossary of the terms the system traffics in. One line each: the term, what it means, and what it does *not* mean. Then check the glossary against reality —

- **Designing:** does every term come from how the problem is actually described, or did you invent it? Invented words (`Manager`, `Handler`, `Processor`, `Item`, `Data`, `Info`) are placeholders for concepts you have not found yet.
- **Auditing:** grep the class and function names. Every place the code uses a word the glossary does not have, or uses one word for two concepts, or two words for one concept, is a finding — and usually a cheaper fix than any structural change.

One concept, one name, everywhere: in conversation, in the glossary, in class names, in database columns, in the log lines. When you cannot name something cleanly, that is a signal the model is wrong, not that you need a better thesaurus.

## Step 3 — The one law

```
entrypoints/   web, CLI, cron, webhooks     → may import anything below
service_layer/ use cases, unit of work      → may import domain
domain/        model, events, rules         → imports NOTHING infrastructural
adapters/      ORM, repositories, clients   → imports domain, never the reverse
```

**Dependencies point inward. The domain imports nothing infrastructural.**

Applies from Thin upward, and it is the only DDD rule that is mechanically checkable rather than a matter of taste — which makes it the anchor for any audit:

```bash
grep -rnE '^\s*(from|import)\s+(sqlalchemy|django|flask|fastapi|requests|httpx|redis|boto3|psycopg|sqlite3|celery|openai|anthropic)' <domain-package>/
```

Every hit is a finding with a file and a line number. Directory names prove nothing on their own — folders called `domain/` with imports crossing freely are the cargo-cult version.

## Step 4 — Go to the mode file

- `modes/designing.md` — event storming, boundaries, aggregate selection, walking skeleton
- `modes/auditing.md` — evidence gathering, findings, sequenced refactor
- `references/patterns.md` — the tactical catalog: what each pattern is, the symptom that earns it, its cost, how to spot it faked
- `references/probe.sh` — read-only structural probe for a Python repo (audit mode runs this first)

The examples and the probe are Python. The gate, the language work, and the boundary reasoning are not — in another language, translate the import check to that language's equivalent and gather the same evidence by hand.

## Findings are organized by the system's problems, never by the book's chapters

The strongest pull when applying this material is to walk the pattern list and find one instance of each — a repository finding, an aggregate finding, an events finding. That produces a report that recommends every pattern regardless of what the system needs, and it is the single most common way this work goes wrong.

Order by damage done to *this* system. If four of the eligible patterns have nothing to say here, four sections do not appear. A three-finding audit that names the right three is worth more than a survey.

## Red flags

Any of these means stop and re-read the gate:

- The number of findings equals the number of patterns you know
- A recommendation whose justification is "the book does it this way" rather than a named rule in this system that is currently unenforced
- Calling a model anemic without naming the specific invariant that should live on it and showing where it leaks today
- Proposing repositories or a unit of work for a system with a single writer and no multi-write transaction
- Proposing events because the flow is long, rather than because failure of a downstream step should not unwind the upstream one
- A phased plan whose last phase is "adopt the rest of the patterns"
- Producing two designs and asking the user to choose the level of rigor

## Quick reference

| Question | Answer |
|---|---|
| Where do business rules go? | The domain object that owns the invariant; a domain function when it belongs to no single object |
| Where does orchestration go? | Service layer — one function per use case, imperative name, no business `if`s |
| What decides an aggregate? | The set of things that must be consistent within one transaction |
| How many aggregates per transaction? | One. Two means the boundary is wrong or the two changes need not be atomic |
| Command or event? | Imperative name, one handler, fails loudly = command. Past tense, many listeners, fails independently = event |
| Where do queries go? | Read side, plain SQL, straight to the caller. Do not load aggregates to render a screen |
| When is a repository justified? | You cannot test rules without a database, and a fake is easy to write |
| What proves the domain is decoupled? | It imports in a REPL with no services running |
