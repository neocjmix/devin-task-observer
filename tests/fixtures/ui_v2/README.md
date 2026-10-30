# UI V2 S0 synthetic inputs

`s0.json` is an input fixture for
`observer.ui_projection.project(**json.loads(...))`. It contains only invented
tasks, session IDs, values, URLs, workspace paths and timestamps. It contains no
personal transcript, real session data, credentials or company information. The
URLs are example references; no network access, file opening or Devin action is
needed to load or project this fixture. These strings are data, not product copy.

The top-level keys are `task_snapshots`, `observations`, `session_activity`,
`runtime`, and `state_errors`. Their structures follow the existing
`FileStore.list()`, `state_observations.observations()`,
`integration.session_activity()` and `CollectorRuntime.snapshot()` contracts.
The activity results are keyed by session ID. The fixture is not a stored schema
migration and must not be installed into a user's state root.

## Cases

| Input | Purpose |
|---|---|
| `task-auth` | Sessions A/B both contribute. A defined an item later changed by B. Includes an item pinned by a human (not a claim of human value authorship), human note, Markdown, a reported `completed` item with a failed-test value, and an archived item. |
| `task-observer` | Also linked to A, with reporting OFF and an earlier last report than A's contribution to `task-auth`. Reuses item ID/title `situation` / `현재 상황` without identifying the same item across tasks. Includes custom status and nested JSON value. |
| `task-bridge` | Long Korean/English value, newlines, session C. Branch `main` is mentioned in a different workspace from A's `main`. |
| `task-migration` | Preserved legacy View, resource, lifecycle and read-cursor snapshots. They are historical imported data, not live UI V2 features. The legacy archive path/hash is illustrative; no archive file is provided or integrity claim made. |
| `task-empty` | No sessions/items/notes; intentionally has the same title as `task-auth`. |
| `task-external` | Schema-valid external edit differs from the committed `last_good`. Its new title/value/pin must not become the displayed current state. |
| `task-invalid` | Invalid JSON diagnostic and no last-good record; remains visible as unavailable. |

The five normal task envelopes and the external-edit envelope contain 12 state
objects in total, counting `state` and `last_good` separately. Every non-null
state passes the current `observer.state_schema.validate(state, 'task')` v1
validator. Normal FileStore envelopes include both copies; the fixture retains
that actual input shape.

The fixed reference time is **2026-10-31 12:00 UTC**. A's hook activity at 11:58
is two minutes old, while its last reports for the two tasks are 09:00 and 08:00.
Recent activity does not prove a recent report or reporting failure. Collector
last success is 11:55 and the later 11:59 attempt failed. Runtime remains running;
collector failure, session observation status and task file health are separate.
Projection code must preserve timestamps, not derive them from the test clock.

Observed PR #142 occurs at two nodes in A and one node in B. A's first mention
is deliberately repeated byte-for-byte to exercise idempotent projection input;
this does not claim the collector normally emits duplicate SQL rows. The PR
stays a candidate with three distinct source references and unknown external
status. Sharing a session does not confirm task ownership. Both workspaces'
`main` mentions remain separately scoped. Session A/B plan records retain
`source_status`, `verification=agent_reported`, and `ordering=not_inferred`.
One observed session has no task link; one linked session has no observation.

## Deliberately unsupported design examples

S0 does not invent fields to satisfy later screen examples. Task lifecycle,
per-task observation toggles, new-report/read-cursor counts, typed situation /
intervention / next-action roles, session token metrics and session locators are
absent from the live v1 contract. A report's `completed` status, an item's
`archived` flag or `legacy.lifecycle` must not supply those missing features.
The fixture has five normal tasks, not a claim of five lifecycle-active tasks.
Reporting OFF is not per-task observation OFF. No typed intervention is inferred
from a title, blocker, failed test or free-form JSON value.

The current observation bridge's `source_nodes` join excludes workspace-only
`local_git` rows. Therefore this fixture includes text-mentioned branches but no
pretend local-Git verification. Local Git candidate exposure requires separate
future bridge work. Tokens and session locator fields are likewise absent rather
than fabricated as zero usage or a working open-session action. Persisted
resource registration/opening, lifecycle transitions, popover focus, dark/light
appearance and other GUI behavior are not validated by this fixture.

The pure projection is detached from acquisition. Existing FileStore reads may
create the lock/directories and recover pending transactions. Do not bypass that
commit boundary with independent raw-file reads or call the acquisition path
filesystem-write-free. File, observation DB and hook snapshots are not one
globally atomic transaction.
