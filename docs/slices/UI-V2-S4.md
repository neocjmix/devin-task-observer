# UI-V2-S4 — lifecycle and task observation

Baseline `59fbfa3`; branch `p0/desktop-probe`, Draft PR #1.

Added user-managed active/completed lifecycle and task-specific observation preference. Missing v1 management fields mean active/on without rewriting old files. Completion stores the previous preference and disables task reflection; restoration restores that preference. Repeated completion cannot overwrite it. Completed/on is invalid. Agent-reported status and item archive never complete a task.

Session reporting preferences, task-session links, tracked values, notes, pins, resources and history survive. New reports to completed/off tasks are rejected explicitly, while existing receipt replays remain idempotent. No post-completion report is silently accepted as new history. Live candidate associations and task-plan entry points are omitted for off/completed tasks; global session collection continues, including for other linked tasks. The settings therefore stop task reflection, not the shared observer process.

Home now separates active and archived tasks. Bottom task controls use OCC. Hook guidance and repository skill explain effective reporting; installed skills are not silently rewritten.

## Verification (2026-10-31)

- 73 focused Python tests passed: 13 management tests plus 60 actions/resource/role/projection/integration regressions. Actual CLI lifecycle/observation commands and shared-session collection are included.
- Node helper/DOM and actual Python Core→Projection tests passed: off→complete→restore stays off, existing content survives, only explicit lifecycle drives archive filtering, pending notes survive updates.
- Native GUI controls are implemented; their actual click/restart acceptance is scheduled in S8, not claimed from these unit/DOM tests.
