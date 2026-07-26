# Tactical Pattern Catalog

Each pattern: what it is, the symptom that earns it, the cost, how to spot it missing or faked, and the smallest correct shape.

**Read the cost column before the benefit column.** Every entry here is a trade. A pattern applied without its symptom is a net loss.

---

## Value Object

**What.** A domain concept identified entirely by its data, with no lifecycle of its own. Immutable. Two value objects with equal fields *are* the same thing.

**Symptom that earns it.** A concept travels the codebase as a loose tuple, dict, or bare primitive, and the same validation gets re-applied at every use site. `Money(amount, currency)` passed as two arguments that can be swapped. A `sku: str` that is really a constrained format checked in four places.

**Cost.** Nearly zero in Python. `@dataclass(frozen=True)` is one line. This is the cheapest pattern in the book and the one most often skipped.

**Present / absent / faked.**
- Absent: functions take `(str, str, int, str)` and the parameter order is the only thing keeping them straight.
- Absent: the same regex or range check appears in more than one module.
- Faked: a "value object" that is mutable, or that has an `id` field. If it has identity, it is an entity.
- Faked: a frozen dataclass used purely as a DTO at the transport boundary, with no domain meaning. That is fine, but do not count it as domain modeling.

**Shape.**
```python
@dataclass(frozen=True)
class Sku:
    value: str
    def __post_init__(self):
        if not SKU_RE.fullmatch(self.value):
            raise InvalidSku(self.value)
```

**Highest-leverage move in agent-built codebases.** Agents love primitives. Replacing a widely-passed primitive with a value object is a small, mechanical, high-yield refactor that immediately kills a class of bugs and gives the concept a name.

---

## Entity

**What.** A domain concept with identity that persists across changes to its attributes. Equality is by identity, not by value.

**Symptom that earns it.** You need to say "this is the same order it was yesterday, even though its status changed."

**Cost.** You must define `__eq__` and `__hash__` deliberately, and decide what the identity actually is.

**Present / absent / faked.**
- Faked: an entity that inherits from the ORM base class. Now the domain depends on the database and you cannot instantiate it in a unit test without a session. This is the single most common failure.
- Faked: an entity with only getters and setters and no behavior. That is a record, not an entity — see Anemic Domain Model below.
- Absent: identity is compared with `a.id == b.id` scattered at call sites rather than `a == b`.

**Shape.**
```python
class Batch:
    def __init__(self, ref: str, sku: Sku, qty: int):
        self.reference = ref
        ...
    def __eq__(self, other):
        return isinstance(other, Batch) and other.reference == self.reference
    def __hash__(self):
        return hash(self.reference)
```

---

## Aggregate

**What.** A cluster of objects treated as one unit for the purpose of changing data. One entity is the root; outside code may only touch the root. The aggregate is a **consistency boundary** — everything inside it is consistent at the end of every transaction.

**Symptom that earns it.** An invariant spans several objects and something must enforce it. "An order line can be allocated to only one batch." "A room cannot be double-booked." Also: lock contention, or a single transaction that updates half the database.

**Cost.** Real, and the book says so: a third kind of domain object to explain; the discipline of one-aggregate-per-transaction is a genuine mental shift; and you now have to deal with eventual consistency *between* aggregates.

**How to choose one — this is the whole game.**
1. List the invariants. Write them as sentences a non-programmer would say.
2. For each, ask: which objects must be read and written *in the same transaction* to enforce it?
3. That set is a candidate aggregate. Name it. If the natural name is ugly (`GlobalSkuStock`), keep hunting for the domain's own word for it (`Product`).
4. Prefer the smallest boundary that still holds the invariant. Smaller aggregates = more concurrency.
5. Accept that the choice is somewhat arbitrary and revisable. There is no single correct aggregate. If a boundary causes performance pain, move it.

**Present / absent / faked.**
- Absent: bidirectional object references. If `Document` knows its `Folder` and `Folder` holds a list of `Document`, the boundary has not been drawn. Break it by replacing one direction with an id.
- Absent: code that dots its way across the object graph — `user.account.workspaces[0].documents.versions[1].owner` — each hop possibly a database query.
- Absent: a use case that loads two roots and writes both before one commit.
- Faked: "aggregates" that are just the ORM models, one per table, with a repository each. That is a DAO layer wearing a costume.

**The two mechanical tests an auditor can run.**
- *Repository test:* is there a repository for a non-root entity? If `BatchRepository` and `ProductRepository` both exist and `Batch` lives inside `Product`, the boundary is leaking. **One aggregate, one repository** — repositories return roots only.
- *Transaction test:* does any single unit of work mutate two roots? If yes, either the boundary is wrong (merge them) or the two changes do not actually need to be atomic (split them with a domain event).

**Reading across boundaries is fine.** Loading another aggregate read-only in a handler is not a violation. Writing two is. If you keep needing another aggregate's data to decide something, that is a signal to build a read model, not to merge the aggregates.

**Concurrency.** The aggregate root is the natural place to hang a version number for optimistic locking: bump it on every state change, let the database reject the second writer. The number is meaningless; what matters is that the root's row is touched on every write to anything inside it.

---

## Domain Service

**What.** A domain operation that has no natural home on any single entity or value object. In Python, usually just a function.

**Symptom that earns it.** An operation genuinely involves several objects equally and forcing it onto one of them would be a lie. Evans: sometimes it just isn't a thing.

**Cost.** Approximately none — it's a function. The risk is overuse: every operation becoming a service is how you get an anemic model.

**Not the same as a service-layer service.** A domain service expresses a business concept. A service-layer service expresses an application use case. The service layer calls domain services, never the reverse.

**Faked.** A module called `services.py` full of functions that take an ORM session. That is a service layer (or worse, a transaction script), not a domain service.

---

## Repository

**What.** An abstraction over persistent storage that presents itself as a collection of aggregates. Minimal interface: `add(thing)`, `get(identity)`, plus the few queries the application actually needs.

**Symptom that earns it.** You cannot write a unit test for business logic without a database. Domain classes import the ORM. You want to change the storage decision later and cannot.

**Cost.** The book is honest here: an ORM already buys some decoupling; hand-maintained mappings are extra code; and every layer of indirection adds maintenance cost and a "what is this" tax for readers who have not seen the pattern. **If the app is a CRUD wrapper around a database, you do not need a repository or a domain model.**

**Present / absent / faked.**
- Absent (mechanical check): grep the domain package for infrastructure imports. Any hit on `sqlalchemy`, `django`, `sqlite3`, `psycopg`, `redis`, `requests`, `httpx`, `boto3`, `os.environ` means the domain is not persistence-ignorant.
- Faked: a repository that returns ORM query objects or lets callers chain `.filter()`. The abstraction has leaked; callers are writing queries through a keyhole.
- Faked: a repository with thirty methods, one per screen. Query methods belong in a read model, not bolted onto the write-side repository.
- Faked: `get_all()` returning everything so the caller can filter in Python.

**Shape.** An ABC with two or three methods; a SQLAlchemy implementation; and a `FakeRepository` backed by a `set` used by unit tests. The fake is the point — if a fake is hard to write, the interface is wrong.

**Mapping direction matters.** Use imperative/classical mapping so the ORM depends on the domain rather than the domain inheriting from the ORM. In SQLAlchemy that is `mapper_registry.map_imperatively(Batch, batches_table)`. This is what makes the domain importable with no database installed.

---

## Service Layer (application layer / use cases)

**What.** One function per use case, orchestrating: begin transaction, fetch aggregate, check preconditions, call domain method, persist, return. It contains no business rules — it contains the *script* for a job the application does.

**Symptom that earns it.** Orchestration logic creeping into controllers. The same five steps duplicated in a web handler, a CLI command, and a cron job. No single place to answer "what can this system actually do?"

**Cost.** If the app is *purely* a web app, the view functions can be that single place — an extra layer buys little. And a service layer that accumulates business rules produces an anemic domain model, which is worse than what you started with. The book's ordering advice is explicit: **introduce the service layer after you spot orchestration creeping into controllers**, not before.

**Present / absent / faked.**
- Absent: business decisions inside a route handler, a Celery task, or a `if __name__ == "__main__"` block.
- Absent: no function in the codebase has an imperative use-case name. Use cases should be named `allocate`, `lock_account`, `send_digest` — not `handle_post`.
- Faked (anemic): service functions that reach into aggregate internals — `product.batches.sort(); product.batches[0].qty -= line.qty` — instead of calling `product.allocate(line)`. The rule: if the service function contains an `if` about *business* meaning, that `if` belongs in the domain.
- Faked (passthrough): a service function that only forwards its arguments. That is ceremony, delete it.
- Smell: service functions calling other service functions in a chain. Prefer duplication, or a domain event, over a call chain.

**Signature test.** A service function should be callable from a test with primitives and a fake unit of work — no HTTP objects, no ORM session, no request context. If the signature mentions `Request` or `session`, the layer has not separated.

---

## Unit of Work

**What.** A context manager owning a single atomic operation. Enter to begin, exit to roll back unless committed. Holds the repositories.

**Symptom that earns it.** Transaction boundaries are implicit or scattered — `session.commit()` sprinkled through business code, or partial writes surviving a failure. You cannot tell by reading a function what will be atomic.

**Cost.** The ORM probably already gives you this; SQLAlchemy has session context managers. Rollbacks, nesting and threading need real thought. Sticking with the framework's transaction handling is a legitimate choice.

**Present / absent / faked.**
- Absent: `commit()` called from more than one layer, or inside domain code.
- Absent: exceptions leaving a half-written state.
- Faked: a UoW whose `__exit__` commits by default. Default must be rollback; commit is explicit.
- Faked: a UoW passed around but with the raw session also passed around beside it.

**Shape.**
```python
with uow:
    product = uow.products.get(sku)
    product.allocate(line)
    uow.commit()
```
One aggregate fetched, one domain method called, one commit. When a use case does not look like this, ask why.

---

## Domain Events

**What.** A record that something meaningful happened, named in the past tense, raised by the domain and handled elsewhere.

**Symptom that earns it.** A use case is growing secondary responsibilities — allocate, *and* email, *and* update the read model, *and* notify Slack. The core rule is now buried in a chain of side effects, and the "and then" list keeps growing with each feature.

**Cost.** Control flow becomes harder to follow — you can no longer read one function top to bottom and know what happens. You need monitoring for handlers that fail independently, and eventually replay tooling. Do not reach for events until the "and then" list is actually causing pain.

**Present / absent / faked.**
- Absent: functions whose names contain `and` — `allocate_and_notify`. Or long handlers where the first ten lines are the rule and the next forty are consequences.
- Absent: a domain module that imports an email client, an HTTP client, or a Telegram SDK. Side effects in the domain are events waiting to be extracted.
- Faked: "events" named imperatively (`SendEmail`) — those are commands.
- Faked: events dispatched synchronously inside the domain method itself, so failure of a subscriber rolls back the core operation. Then it isn't decoupled, it's just indirection.

**Shape.** The aggregate appends to `self.events`. The unit of work collects them after commit. A message bus dispatches to handlers.

---

## Commands vs Events

The sharpest, most portable distinction in the whole book, and cheap to apply:

| | Command | Event |
|---|---|---|
| Named | imperative — `AllocateStock` | past tense — `StockAllocated` |
| Sent to | exactly one handler | every interested listener |
| On failure | fail loudly; caller must learn | fail independently; sender does not care |
| Captures | intent — what we want to happen | fact — what already happened |

**Why it earns its keep.** Get this right and error handling designs itself: retry and surface command failures; log and alert on event-handler failures without unwinding the original operation. Get it wrong and you either swallow errors the user needed, or you roll back completed work because a notification failed.

**Enforce it with data structures, not convention.** The distinction should be visible in the dispatch code: events map to a *list* of handlers, commands to exactly one; the event path wraps each handler in try/except and continues, the command path re-raises. A single `HANDLERS` dict covering both means the semantics exist only in people's heads.

```python
EVENT_HANDLERS   = {StockAllocated: [update_read_model, notify]}   # list
COMMAND_HANDLERS = {AllocateStock: allocate}                        # one
```

**Audit checks.**
- Any imperative-named message with multiple subscribers, or past-tense message with one required subscriber whose failure must abort the caller, is misclassified.
- An entrypoint constructing a past-tense message directly from user input is always a mislabeled command. Users express intent; they do not report facts.

**Why the split is load-bearing.** Independent event failure is only safe *because* aggregates are consistency boundaries. Letting handlers fail independently without that guarantee is not decoupling, it is data corruption on a delay.

---

## Read Models / CQRS

**What.** Serve queries from a path that does not go through the domain model — a direct SQL query, a denormalized table, or a cache maintained by event handlers.

**Symptom that earns it.** Reads and writes have diverged in shape. A screen needs data spanning several aggregates. N+1 queries from looping over ORM relations. Read volume dwarfs write volume.

**Cost.** A second model to keep correct, and if it is event-maintained, staleness you must reason about. Start with the plainest thing: a hand-written SQL query returning dicts. Only add an event-maintained denormalized table when that query is measurably too slow.

**The permission this pattern grants.** You do *not* have to load aggregates to answer questions. A reporting endpoint doing `SELECT ... JOIN ...` and returning rows is correct design, not a shortcut. The purity rules apply to the write side.

**Faked.** A "read model" that is just the repository with more query methods. Or CQRS with event sourcing adopted wholesale when a `SELECT` would have done.

**Complex reads.** If read logic is genuinely complicated (permissions, authorization), split a *view fetcher* (pulls rows) from a *view builder* (maps and filters), so the builder is unit-testable against a list of dicts.

---

## Dependency Injection / Bootstrap

**What.** A single composition root that builds concrete adapters and injects them into handlers. Everything below it takes its dependencies as arguments.

**Symptom that earns it.** Tests full of `mock.patch` targeting module paths. Config read via `os.environ` deep inside business code. Import-time side effects — a database connection opened when a module is imported.

**Cost.** One more indirection, and Python's flexibility means you can go a long way with plain function arguments and closures. A framework-grade DI container is almost always overkill; a `bootstrap()` function returning a wired message bus is usually enough.

**Present / absent / faked.**
- Absent (mechanical check): count `mock.patch` in the test suite. Heavy use means production code is reaching out to grab its dependencies rather than receiving them.
- Absent: `os.environ` or `load_config()` called anywhere outside the composition root.
- Faked: a DI container that is really a global service locator, fetched from inside functions.

---

## The Anemic Domain Model debate

Most of this argument dissolves once you ask which kind of area you are in.

- **Generic or supporting area** — data classes plus procedures are *correct*. That is not anemia, it is a proportionate model of a thin domain. Demanding behavior-rich objects here is ceremony.
- **Core area with real invariants** — an anemic model is a genuine defect, because the rules have nowhere to live and therefore live in every caller.

**The test is not "does the class have methods."** It is:

> Name the invariant. Say where it is enforced. Then ask whether any code path can reach the state that violates it.

If some path can, that is the finding, and it comes with a file and a line. If none can, the model is fine however thin it looks.

Note also that a functional core — pure functions transforming immutable values, with I/O at the edges — is not anemia, even though it puts logic outside the data classes. Evans has a name for it (side-effect-free functions), and it satisfies the test above perfectly well.

---

## Layering and the one law

```
entrypoints/   web, CLI, cron, webhook handlers    → may import anything below
service_layer/ use cases, unit of work, message bus → may import domain
domain/        model, events, domain services       → imports NOTHING below itself
adapters/      ORM mappings, repositories, clients  → imports domain (never the reverse)
```

**The law: dependencies point inward. The domain imports nothing infrastructural.**

This is the one rule in DDD that is mechanically checkable rather than a matter of taste, which makes it the best anchor for an audit:

```bash
grep -rnE '^\s*(from|import)\s+(sqlalchemy|django|flask|fastapi|requests|httpx|redis|boto3|psycopg|pymongo|celery|os\b)' src/*/domain/
```

Any hit is a finding with a file and a line number. No hits and a domain package that imports in a REPL with no services running means the core is genuinely decoupled.

A directory layout alone proves nothing. Folders named `domain/` and `services/` with imports crossing freely between them is cargo cult — the names without the constraint.
