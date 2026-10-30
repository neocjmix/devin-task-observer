# Reporting CLI reference

Use the absolute `observerctl` and state-root paths in the installed skill binding.
The context file must be the one delivered to this session by its Devin hook.
Commands below show portable `./observerctl` notation; substitute the installed path.
All data stays local. The CLI does not call a model or send network requests.

## Read and begin

```sh
./observerctl state --root "$observer_root" whoami --context "$context_file"
./observerctl state --root "$observer_root" list --session "$session_id"
./observerctl state --root "$observer_root" list
./observerctl state --root "$observer_root" read --task "$task_id"
```

Get `session_id` from `whoami`. Use the unfiltered list only to find an existing
relevant task to link when the user requests reporting and this session has none.
Do not link to unrelated tasks. A context is identity attribution, not authentication.
If there is no hook context but an explicitly supplied real ID, register it once:

```sh
./observerctl state --root "$observer_root" session-register --session "$session_id" --out "$context_file"
```

To create the user-requested task, or join an existing relevant task:

```sh
./observerctl state --root "$observer_root" create --task "$task_id" --title "$title" --session "$session_id"
./observerctl state --root "$observer_root" link --task "$task_id" --session "$session_id" --expected-revision "$revision"
```

Use one of those operations as appropriate; `create` already links the supplied
session. Contributions start OFF. Read again to obtain the committed revision:

```sh
./observerctl state --root "$observer_root" reporting --task "$task_id" --session "$session_id" --enabled on --expected-revision "$revision"
./observerctl state --root "$observer_root" reporting --task "$task_id" --session "$session_id" --enabled off --expected-revision "$revision"
```

Enable on the user's start request. Stop on the user's stop request. Existing
ON contributions resume after reading current state; OFF contributions stay OFF.

## Define, update and archive

Read the [skill's report example](../SKILL.md#read--judge--conditional-report).
Each report has `schema_version: 1`, a unique `report_id`, this `session_id`,
`expected_revisions` mapping every task read for the judgment to its revision, and
`changes` mapping changed task IDs to operation arrays.

- `define`: `op`, new stable `item_id`, `title`, JSON `value`, `status`, `evidence`; optional `role` and `attention_required`.
- `update`: `op`, existing `item_id`, and changed fields among `title`, `value`, `status`, `evidence`, `role`, `attention_required`.
- `archive`: `op` and existing `item_id`. Retains value/history but stops tracking.
- Evidence: `{ "session_id": "linked-session", "kind": "local_artifact", "reference": "actual-reference" }`.
  Kinds are `session_event`, `local_artifact`, or `report`. Do not invent references.

`role` is one of `general`, `situation`, `intervention`, `next_action`, `resume`,
`blocker`, `decision`, `verification`. It is optional: existing v1 records remain
valid and are not rewritten. `attention_required` is an optional boolean. The UI
shows a user intervention only for a non-archived `intervention` item with an
explicit true flag; it never derives this from status or title. Clearing the flag
requires an explicit false update. Omitting either field preserves its current
value. These fields are protected by pins and the same task revision check as
other item fields. Safe human edit exports include them when present; an older
edit document that omits them preserves them. New fields require an updated
Observer CLI; an older binary may reject the extended v1 record.

Task/item/report IDs use 1–80 letters, numbers, hyphens or underscores and start
with a letter or number. Use UUIDs for report IDs. Session IDs come from the hook,
not this task-ID format. To inspect exact validation rules:

```sh
./observerctl state --root "$observer_root" schema --kind report
./observerctl state --root "$observer_root" validate --kind report --file "$report_file"
./observerctl state --root "$observer_root" report --context "$context_file" --file "$report_file"
./observerctl state --root "$observer_root" history --task "$task_id"
```

`--file -` accepts JSON stdin. Report submission verifies all related revisions
atomically and commits all changes or none. It also checks the supplied context's
session ID against the report. Success returns `ok: true` and task revisions;
read again for current state. Replaying the identical report returns its original
receipt, which may predate later changes.

## Failures

CLI errors return `ok: false`, `error.code`, `error.message`, and exit 2.
`read`/`list` can succeed with unhealthy tasks: use current values only when
`health` is `ok`. `last_good` is explicitly an older snapshot, never current.

On `revision_conflict`, read the returned state/updater and judge again; at most
two resubmissions for one report, each with a newly judged payload and new ID.
If outcome is uncertain, resend the identical payload with the same ID.
On `reporting_disabled`, stop. On a pin error, preserve the pinned item. On other
errors, briefly surface the uncommitted report and continue the original work.
Human editing (`pin`, `note`, `edit-apply`, `recover`) needs an actual user request;
it is not an automatic reporting workaround.

`task_completed` and `observation_disabled` also stop new reporting to that task.
Do not force another revision or silently enable observation. Existing reporting
preferences are retained; only active/observation-ON tasks accept new reports.
Already committed identical request IDs replay their old receipt without revival.

Ordinary Devin tool permissions still apply. This skill grants no auto-approved
shell access. If the host requests permission for a command, follow its normal
permission flow; do not bypass it with another tool or edit raw state.

## User-requested resource edits

Automatic reports cannot alter registered resources. On an explicit user editing
request, read the target task and use its current revision:

```sh
./observerctl state --root "$observer_root" resource-put --task "$task_id" --file "$resource_file" --expected-revision "$revision" --request-id "$request_id"
./observerctl state --root "$observer_root" resource-archive --task "$task_id" --resource "$resource_id" --archived on --expected-revision "$revision" --request-id "$request_id"
```

`--archived off` restores an archived registration. The resource JSON requires
`resource_id`, `kind`, `label`, `target`, `open_mode`; optional `evidence` uses the
same linked-session references as reports. Kinds: `issue`, `document`,
`environment`, `repository`, `branch`, `pr`, `communication`, `deliverable`,
`other`. Modes: `url` for absolute HTTP(S), `file` for an absolute local path,
`copy` for literal text such as a branch name. Opening is a separate UI action;
registration never probes or opens the target. Credentials in URLs and control
characters are rejected. No session-control URL schemes are allowed as links.

Reusing an ID updates that registration; omitting evidence preserves its sources.
Human creation attribution, history and archive state survive updates. Explicit
archive/restore retains the registration and prior history. External status stays
`unknown`; registration is not external verification. A candidate is not confirmed
automatically: preserve the chosen source node ID/hash and session reference when
the user explicitly registers it. Inspect `schema --kind resource` or validate the
input with `validate --kind resource --file ...` before submitting.

## User-managed task lifecycle and observation

Only on an explicit user request, read the current revision and use:

```sh
./observerctl state --root "$observer_root" observation --task "$task_id" --enabled off --expected-revision "$revision" --request-id "$request_id"
./observerctl state --root "$observer_root" lifecycle --task "$task_id" --completed on --expected-revision "$revision" --request-id "$request_id"
```

`--completed off` restores a task and the observation preference it had immediately
before completion. An active task may pause/resume observation with `--enabled
off/on`. A completed task cannot enable observation without restoration. These
controls preserve notes, pins, resources, history and per-session reporting
preferences; an agent's `completed` item status never completes the task.

Observation OFF means no new cooperative report updates or live candidate/plan
associations for this task. The workspace collector still observes sessions for
other tasks, and session-wide data remains separately available. No frozen copy
of automatic candidates is created. Global observation activity must not be read
as a new report for the paused task. Ordinary collection never calls a model.
