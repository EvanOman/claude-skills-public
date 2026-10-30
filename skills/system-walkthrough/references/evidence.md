# Collect evidence before drawing

Start with a falsifiable reader question: “Why can delivery happen twice after a
consumer crash?” is more useful than “Explain this repository.” Write the initial
configuration, user action and observable ending. Exclude unrelated features.

Pin the repository's full revision and identify primary documentation, source entry
points and relevant tests. Follow the call chain far enough to distinguish admission,
execution, persistence and response. A test can reveal a boundary case; read the
implementation that explains it. Current deployment facts require deployment evidence.
Library code alone cannot establish a production flag, topology or durability setting.

Make a small working evidence table outside the published payload:

| Assertion | File/symbol or documentation section | Preconditions | Counterexample |
| --- | --- | --- | --- |
| What becomes visible, and when? | Immutable source pointer | Mode/transaction/flag | A stale reader, failed commit or missing ack |
| Who owns the state? | Data structure and mutation path | Lifetime/scope | Another process, consumer or connection |
| What does the reply promise? | Return construction and caller | Sync/async boundary | A reply before an external effect completes |

Turn the important rows into the model's claims and source IDs. Cite precise files,
symbols and line ranges at the pinned revision. For live documentation, record that
it is unpinned; reconcile it against the pinned code. Preserve upstream licensing
and record provenance when copying code or assets. Prefer original paraphrase plus
source links over copying documentation, screenshots or diagrams.

Choose one abstraction level per figure. A process, a logical queue and a file can
share a diagram only if their different meanings are explicit. Label an in-process
call as such. Draw each supported return to its actual caller. A sequence is one
illustrative interleaving, not proof that concurrent operations always serialize
that way. Mark repeated calls as condensed rather than hiding how often they occur.

Follow a failure past the crash icon: state that survives, state that is lost, who
detects the condition, what retries/reclaims/resumes, and what remains possible
(such as duplicate business effects). Do not invent an automatic recovery worker
or exactly-once guarantee. If the source does not answer a material question,
retain the unknown in scope/limits rather than filling it with a plausible story.

Check negative claims at the enforcement path. A structure comment describing one
reader/owner class does not prove another class cannot block the operation. Read the
actual lock/guard acquisition and failure branches before saying an actor is harmless.
Record object creation in state snapshots, or state its prior existence as a scoped
precondition. “Empty” and “not created” can produce different behavior.
