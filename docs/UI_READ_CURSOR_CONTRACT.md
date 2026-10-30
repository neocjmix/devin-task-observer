# UI V2 explicit read cursors

This is the S7 core contract. Native UI integration and its acceptance evidence
are tracked separately in the UI V2 slice records. It does not establish that a
user understood a report, or that a reported claim was independently verified.

## Task report position

An accepted cooperative report sets optional `task.last_report_revision` to that
task's new commit revision. Notes, pins, resources, lifecycle controls, collection
activity and read acknowledgements do not change this position. Exact report
replays return their original receipt without advancing it.

Older v1 tasks without this field have an unknown report position. Neither item
status, last editor, timestamp nor old legacy review cursors are guessed into it.
The first new accepted report establishes a reliable position. Before then the UI
cannot offer a truthful new-report count or mark-through-report action.

## Separate user state

`<state root>/ui/read-cursors.json` has its own schema and revision:

```json
{
  "schema_version": 1,
  "revision": 1,
  "cursors": {
    "task-example": {
      "through_report_revision": 7,
      "updated_at": "2026-10-31T00:00:00+00:00"
    }
  }
}
```

The example is synthetic. See the generated
[`read-cursors.schema.json`](../schemas/ui-v2/read-cursors.schema.json) and
`observer.ui_reads.validate_cursors` for local validation. The missing file means
an empty cursor map at file revision zero; reading alone does not create it. A
malformed existing file is an error, never an empty map.

`ReadCursors(root).snapshot()` returns the cursor document. `mark(task_id,
expected_revision, through_report_revision, request_id=None)` confirms only a
healthy task's displayed latest report position. The request ID is response
correlation; positional idempotence avoids a second unlimited receipt database.

Advancing a cursor checks the exact displayed task revision and latest report
position under the same FileStore commit lock used by reports. It then writes
only the cursor document with durable atomic replacement. **It never advances
the task revision or writes task history.** The existing recovery boundary runs
before reads/writes; unresolved task state cannot be acknowledged as current.

An already acknowledged position is a no-op, including a retry after a subsequent
report. The cursor cannot move backward or swallow that subsequent report. A
report that commits before a new acknowledgement causes a conflict; if the
acknowledgement commits first, the later report remains beyond the cursor.

## Failure and scope

- Task IDs are cursor keys. Two tasks sharing a session have independent positions.
- Unknown old positions remain unknown until a new report establishes one.
- Damaged JSON/schema/timestamps and positions beyond a healthy task's latest
  report raise `invalid_read_cursors`; original bytes are retained.
- Missing/unhealthy tasks are not evidence that an existing cursor should be
  deleted. Their positions remain, while confirming an unhealthy task is rejected.
- A schema-valid direct edit is human-owned UI state. These local files are not
  an authentication boundary; monotonic guarantees cover cooperating writers.
- A cursor failure does not stop the collector or session reporting. The UI must
  expose the failure and unknown read state rather than show zero new reports.
- Opening the popover, polling, sorting and navigating never confirm all reports.
  Confirmation is an explicit per-task action.
