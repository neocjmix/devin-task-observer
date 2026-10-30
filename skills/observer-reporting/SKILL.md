---
name: observer-reporting
description: Use Devin Task Observer's local CLI to read linked tasks, define tracking items, and report meaningful changes from the current working session when cooperative reporting is requested or enabled.
---

# Observer reporting

Report changes using the context already available in the working session. The Observer CLI does not call a model. Do not start a separate model, send transcripts, query another work session, or interrupt the user's work to obtain a report.

This repository skill is a reference artifact. Automatic Devin Desktop discovery, hook delivery and session-ID injection are not installed or verified by this file. The exact commands and recovery details are in [M3-S01 CLI](../../docs/slices/M3-S01_CLI.md).

## Identify and read

Use the existing `observerctl` from its checkout and an explicitly selected state root outside Devin's source directory. Use a real session ID supplied by the host or explicit registration; never infer one from a working directory/PID or invent a replacement. If no ID is available, leave reporting unregistered and continue the work.

```sh
./observerctl state --root "$observer_root" session-register --session "$session_id" --out "$context_file"
./observerctl state --root "$observer_root" whoami --context "$context_file"
./observerctl state --root "$observer_root" list --session "$session_id"
./observerctl state --root "$observer_root" read --task "$task_id"
```

Registration records a claimed identity; it does not authenticate the caller. `--context` must match the report's `session_id`. Reuse the existing context file: registration refuses to overwrite it.

Read the task title, contributions, current items, `updated_by`, revisions and reporting flag. Read existing items before defining another one. Reuse a matching stable `item_id`; a title match alone is not proof that two items mean the same thing. Explicitly link a relevant task when needed; do not report to every task sharing this session.

Reporting is enabled separately for each task/session contribution:

```sh
./observerctl state --root "$observer_root" reporting --task "$task_id" --session "$session_id" --enabled on --expected-revision 2
# Use the current revision when stopping:
./observerctl state --root "$observer_root" reporting --task "$task_id" --session "$session_id" --enabled off --expected-revision 5
```

The numbers above are illustrative. Read again after a control change before preparing a report. ON does not wake an idle agent or guarantee complete reporting; OFF preserves existing values/history. Continue ordinary work, reporting only meaningful changes in the tracked items.

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

Evidence may refer only to sessions linked to the target task. Include relevant references you actually have; do not invent evidence. Replacing an evidence list replaces its current references, so retain the earlier references that still support the new value. Stored claims remain `reported`; schema-valid references do not prove source existence or actual completion.

Each distinct intent gets a unique report ID, preferably a UUID. If delivery outcome is uncertain, resend the **same ID and exact payload**. A successful replay returns the original receipt, which can be older than the current task; use `read` for the latest state.

On `revision_conflict`, read the returned latest values and updater, reconsider whether the other session changed your conclusion, then submit a newly judged payload with a new report ID. Allow at most **two** rejudgment/resubmission attempts for one report. Never change only the revision number to force stale intent through.

## Preserve user control and continue work

Agent reports cannot change pinned items, pins, human notes or the human View. Unpinned human-edited tracking values may receive a valid later report, with history preserved. Do not use `pin`, `edit-apply`, raw file editing, or replacement items to bypass protections. Human-only edits/recovery require an actual user editing request, not inferred authority from reporting being ON.

On disabled reporting, missing identity, validation failure, file damage, lock timeout or repeated conflicts, keep the unsubmitted intent locally when useful and surface the failure briefly. Do not mark it as committed, silently repair raw state, busy-loop, or block the original Devin task. Ordinary collection remains independent. Resume reporting after the relevant condition is resolved and current state has been reread.
