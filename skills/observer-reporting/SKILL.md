---
name: observer-reporting
description: Use Devin Task Observer's local CLI to read linked tasks, define tracking items, and report meaningful changes from the current working session when cooperative reporting is requested or enabled.
---

# Observer reporting

Report changes using the context already available in the working session. The Observer CLI does not call a model. Do not start a separate model, send transcripts, query another work session, or interrupt the user's work to obtain a report.

Install this skill only in an explicitly selected workspace with `observerctl integration install`. The project hook supplies a session context path; the installed copy includes the exact CLI and state-root paths. The exact commands and recovery details are in [CLI reference](references/cli.md). Installing does not enable reporting or grant tool permissions by itself.

## Identify and read

Use the installation binding and the exact context file supplied to this session by the Devin hook. Run `whoami` first and use the returned `session_id`. Never scan another session's context, infer identity from a working directory/PID, or invent a replacement. If the hook did not supply an ID, use an ID explicitly supplied by the host/operator and register it once; otherwise leave reporting unregistered and continue the work.

When the user asks to start reporting, use this sequence (each write may change the revision):

1. Run `whoami` on this hook context and take its `session_id`; list that session's linked tasks.
2. Read the relevant task (`read --task ...`) and inspect `state.contributions` and `state.revision`. If no suitable task exists and the user requested a new task, `create --task ... --title ... --session ...` creates it with this session already linked and reporting OFF; read it afterward.
3. If the relevant existing task does not yet include this session, run `link --task ... --session ... --expected-revision <state.revision from the read>`. **Reporting cannot be enabled before the session is linked.** Read the task again after linking.
4. Run `reporting --task ... --session ... --enabled on --expected-revision <revision from the latest read>`. The `--expected-revision` argument is required for both `link` and `reporting`. Do not reuse the pre-link revision.
5. Read once more before defining or updating tracking items. The report's `expected_revisions` must use this latest revision, not the one from before enabling.

Do not select every task sharing the session. On a stop request, read the task and run `reporting --enabled off` with that current revision. On resumed work or after compaction, reread contributions and continue reporting only for those still ON; do not silently re-enable a stopped contribution.

```sh
./observerctl state --root "$observer_root" whoami --context "$context_file"
./observerctl state --root "$observer_root" list --session "$session_id"
./observerctl state --root "$observer_root" read --task "$task_id"
```

The hook already creates its context; do not run registration over it. Only when no hook context exists and the host/operator explicitly supplied the real session ID, use `session-register --session "$session_id" --out "$context_file"`. Registration or hook provenance records a supplied identity; it does not authenticate the caller. `--context` must match the report's `session_id`. Reuse the existing context file: registration refuses to overwrite it.

Read the task title, contributions, current items, `updated_by`, revisions and reporting flag. Read existing items before defining another one. Reuse a matching stable `item_id`; a title match alone is not proof that two items mean the same thing. Explicitly link a relevant task when needed; do not report to every task sharing this session.

Reporting is enabled separately for each task/session contribution:

```sh
./observerctl state --root "$observer_root" reporting --task "$task_id" --session "$session_id" --enabled on --expected-revision 2
# Use the current revision when stopping:
./observerctl state --root "$observer_root" reporting --task "$task_id" --session "$session_id" --enabled off --expected-revision 5
```

The numbers above are illustrative. Read again after a control change before preparing a report. ON does not wake an idle agent or guarantee complete reporting; OFF preserves existing values/history. Once enabled, continue ordinary work and proactively report meaningful changes without requiring the user to request another update. Read current state before reporting, and check the contribution is still ON. Suitable moments include verified test outcomes, changed blockers/decisions, or a completed work unit; no-change turns need no report. Hooks do not block stopping or generate reports for you.

## Read → judge → conditional report

Read current state, judge without holding any lock, then send only changed items with the exact revisions used for the judgment. The session chooses what the task should track and how new findings change earlier claims. The Core validates the conditional write; it does not reconcile meaning.

Example for a synthetic task/session, after reading task revision 3:

```json
{
  "schema_version": 1,
  "report_id": "report-example-001",
  "session_id": "session-A",
  "expected_revisions": {"task-example": 3},
  "changes": {
    "task-example": [{
      "op": "define",
      "item_id": "interface-compatibility",
      "title": "Interface compatibility",
      "value": "Tests fail after the interface change; update the implementation and rerun them.",
      "status": "blocked",
      "role": "blocker",
      "evidence": [{"session_id": "session-A", "kind": "local_artifact", "reference": "test-output/run-001"}]
    }]
  }
}
```

```sh
./observerctl state --root "$observer_root" validate --kind report --file "$report_file"
./observerctl state --root "$observer_root" report --context "$context_file" --file "$report_file"
```

Use actual identifiers/references in real reports. `define` requires title/value/status/evidence; `update` uses an existing ID and only changed fields. `archive` accepts just `op` and `item_id`, stopping tracking while retaining history. Reuse IDs after rewording; do not manufacture new IDs merely to bypass a conflict or a pin. No change means no report.

Optional `role` helps the UI place an item: `general`, `situation`, `intervention`,
`next_action`, `resume`, `blocker`, `decision`, or `verification`. Choose it explicitly
when useful; free-form items remain supported and no set of roles is mandatory.
Use `intervention` with `attention_required: true` only for an actual pending action
or decision requested of the user. To clear that request, update
`attention_required: false`; a `completed` status alone does not clear it. The UI
does not infer intervention from a blocker, title or free-form status. An absent
role displays as general and an absent attention flag does not request attention.
Role and attention updates use the same revision, attribution and pin protection
as values. Omitted optional fields preserve the existing value; to reset a role,
send `role: "general"` explicitly. A `resume` value is information, not authority
to activate or control a work session.

Evidence may refer only to sessions linked to the target task. Include relevant references you actually have; do not invent evidence. Replacing an evidence list replaces its current references, so retain the earlier references that still support the new value. Stored claims remain `reported`; schema-valid references do not prove source existence or actual completion.

Each distinct intent gets a unique report ID, preferably a UUID. If delivery outcome is uncertain, resend the **same ID and exact payload**. A successful replay returns the original receipt, which can be older than the current task; use `read` for the latest state.

On `revision_conflict`, read the returned latest values and updater, reconsider whether the other session changed your conclusion, then submit a newly judged payload with a new report ID. Allow at most **two** rejudgment/resubmission attempts for one report. Never change only the revision number to force stale intent through.

## Preserve user control and continue work

Agent reports cannot change pinned items, pins, human notes or the human View. Unpinned human-edited tracking values may receive a valid later report, with history preserved. Do not use `pin`, `edit-apply`, raw file editing, or replacement items to bypass protections. Human-only edits/recovery require an actual user editing request, not inferred authority from reporting being ON.

Registered `state.resources` are human-managed links, distinct from resource
candidates found in session observations. Read them for context, but do not add,
rewrite or archive them as part of automatic reporting. `resource-put` and
`resource-archive` require an explicit user editing request. Registration confirms
the chosen task association only; `external_status: "unknown"` does not imply a
PR is open/merged or that a URL/file is accessible. Legacy resource snapshots
remain under `legacy.resources` and are not automatically promoted.

On disabled reporting, missing identity, validation failure, file damage, lock timeout or repeated conflicts, keep the unsubmitted intent locally when useful and surface the failure briefly. Do not mark it as committed, silently repair raw state, busy-loop, or block the original Devin task. Ordinary collection remains independent. Resume reporting after the relevant condition is resolved and current state has been reread.
