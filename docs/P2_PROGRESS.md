# P2: Task registry (in progress)

The registry is local Python/SQLite task-core functionality. P1 remains in progress. A prototype dashboard is described in [P3 progress](P3_PROGRESS.md); automatic semantic reconciliation is not implemented.

## Implemented

- Create and list tasks with title, description, lifecycle, timestamps and revision. Task updates require the expected revision; conflicting edits fail rather than overwriting another edit. Existing values are preserved when fields are omitted. Lifecycle changes are explicit and no automatic deletion is implemented.
- Link tasks and observed sessions through a contribution table with role, scope, attribution, confidence and creation time. The foreign keys reject unknown tasks and sessions; a task can link multiple sessions and a session can link multiple tasks. Repeating the same link is idempotent; attempting to silently replace its metadata is rejected. Stale session state remains visible through a link.
- Add manually supplied resource links or local paths to a task. External status is explicitly unknown; adding a resource does not query it.
- Explicitly unlink and restore a session; its contribution and existing evidence references remain recorded. Edit, archive and restore a resource. These operations use per-record revision checks and preserve change history. Existing stores without these auxiliary records are supported on their first change.
- Contribution role, scope and confidence can be edited through the local API with revision checks; earlier metadata is retained. This metadata editing does not yet have a dashboard control.
- Each task has a separate per-linked-session review position over the deterministic observation stream. Acknowledging a displayed page is revision checked and never removes observations; another task linked to the same session retains its own position. Unlinking and restoring a session preserves the position. The stream suppresses identical repeated added-node payloads and the narrow observed timing-only variant as described in [P1](P1_PROGRESS.md).
- A local task attention summary counts unreviewed observations and stale linked sessions, scoped to explicitly allowed workspaces. It does not call a model or infer task completion. On the actual synthetic test store, two tasks sharing one session produced independently tracked pending counts after one task's review position was advanced.
- Persistence uses the local observer store; there are no model calls, network requests, or writes to Devin session data.

## Validation

- The current full-suite result is reported in [M1 progress](M1_PROGRESS.md), including registry tests for the two-way many-to-many case, invalid references, idempotent links, resource history, link restoration, metadata editing, task-specific review positions and attention counts, migration from existing records, lifecycle changes, and protection from stale edits.
- A live integration against the three **synthetic** Desktop sessions created two synthetic tasks and linked two sessions to one task, with one session linked to both tasks. Results were verified from the private local test store.

## Remaining

More integration and concurrency tests remain, as does a dashboard control for contribution metadata. Archived resources and links remain visible and can be restored; no permanent delete is implemented. The upstream adapter still has the limitations documented in [P1 progress](P1_PROGRESS.md), and persistent-view and UI limitations are documented in [P3 progress](P3_PROGRESS.md).


## M1 update (2026-10-29)

Automatic resource candidates are projections of retained source facts, scoped through active task/session links. A candidate never creates or edits a manual Resource until explicit confirmation; exact already-registered global URLs can be reused. Candidate confirmation in one task does not confirm it in another. Branches, URL-less ticket keys and bare commit hashes include workspace scope, preventing identical names in different repositories from becoming one resource. Human edits/archives are retained and reflected as such. Every external status remains unknown.

The existing Resource table is reused for confirmed connections. Small auxiliary fact/choice/Git snapshot tables retain provenance and freshness. Recognized bare ticket keys may join one unambiguous URL in the same workspace; colliding hosts remain separate candidates. Contribution metadata editing still has no dedicated dashboard control. Automatic resource discovery is not automatic semantic task attribution.
