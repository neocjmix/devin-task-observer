# P2: Task registry (in progress)

The registry is local Python/SQLite task-core functionality. P1 remains in progress. A prototype dashboard is described in [P3 progress](P3_PROGRESS.md); automatic semantic reconciliation is not implemented.

## Implemented

- Create and list tasks with title, description, lifecycle, timestamps and revision. Task updates require the expected revision; conflicting edits fail rather than overwriting another edit. Existing values are preserved when fields are omitted. Lifecycle changes are explicit and no automatic deletion is implemented.
- Link tasks and observed sessions through a contribution table with role, scope, attribution, confidence and creation time. The foreign keys reject unknown tasks and sessions; a task can link multiple sessions and a session can link multiple tasks. Repeating the same link is idempotent; attempting to silently replace its metadata is rejected. Stale session state remains visible through a link.
- Add manually supplied resource links or local paths to a task. External status is explicitly unknown; adding a resource does not query it.
- Explicitly unlink and restore a session; its contribution and existing evidence references remain recorded. Edit, archive and restore a resource. These operations use per-record revision checks and preserve change history. Existing stores without these auxiliary records are supported on their first change.
- Persistence uses the local observer store; there are no model calls, network requests, or writes to Devin session data.

## Validation

- The full synthetic test suite passes 55 tests, including registry tests for the two-way many-to-many case, invalid references, idempotent links, resource history, link restoration, migration from existing records, lifecycle changes, and protection from stale edits.
- A live integration against the three **synthetic** Desktop sessions created two synthetic tasks and linked two sessions to one task, with one session linked to both tasks. Results were verified from the private local test store.

## Remaining

Editing contribution metadata and more integration and concurrency tests remain. Archived resources and links remain visible and can be restored; no permanent delete is implemented. The upstream adapter still has the limitations documented in [P1 progress](P1_PROGRESS.md), and persistent-view and UI limitations are documented in [P3 progress](P3_PROGRESS.md).
