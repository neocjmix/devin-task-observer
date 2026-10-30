# Observer UI v2 implementation roadmap

Goal: resume 5+ asynchronous Devin tasks without opening every session. Best effort; honest failure; minimal uncertainty UI. Keep deterministic observation LLM-free, provenance and history preserved, and session ownership untouched.

Run one slice per Codex execution; commit and push after tests. Do not mistake proposed UI for implemented behavior.

S0: Read-only normalized projections and synthetic fixtures. Preserve item-level actor/time, many-to-many task-session mapping, last_good and errors. No storage migration.

S1: NSPopover prototype with 420x600pt reference, task home, filters, attention first, collector health, scroll and keyboard navigation. Compare PyObjC native vs WKWebView; keep NSMenu fallback.

S2: Task detail: situation, user intervention, next action, resume entry, resources, tracked items, linked sessions, notes/history. Each item displays value, status, last editor/session and time. Evidence and pin in disclosure.

S3: Persistent resource registration and opening: Jira, wiki, test URL, repository/branch, PR, Slack, evolving Markdown deliverables, other references. Preserve source, distinguish observed candidates, support URL/file opening and copy fallback. Maintain free-form key/value.

S4: User completion/archive and per-task observation toggle. Completion disables observation; reopening restores prior setting. Active tasks may disable observation. Shared sessions continue collecting for other tasks. Preserve notes, pins, history, resources.

S5: Session detail: reporting on/off, last activity vs last report, workspace, linked tasks, tokens (total input, new input, cache input, output, measured time/source). Token totals are session-wide and never double allocated to tasks. SQLite read-only or ATIF after validation.

S6: Session activation feasibility spike. Test Devin Desktop session URL, exact tab/window, and ownership preservation. No session/load, forced resume or prompt sending. If unsupported, copy session ID; other ACP clients are out of initial scope.

S7: Last-seen changes using explicit task read cursors, attention and recent reports, sorting and filtering. Never mark all tasks read merely on popover open.

S8: Real macOS acceptance with 5-10 parallel sessions, dark/light, focus, accessibility, errors, stale records, restart, no LLM observation calls.

Every run: inspect latest branch/code and M3 contracts, implement only next slice, test regressions, record verified vs unverified behavior, commit and push. Synthetic M3 parser content is not product copy.
