# UI-V2-S7 — explicit read positions and task ordering

Baseline `f16bd10`; branch `p0/desktop-probe`, Draft PR #1.

Accepted session reports stamp optional `last_report_revision`; human notes, pins, settings and resources do not move it. UI read positions live separately in `ui/read-cursors.json`, with a published schema and their own revision. Acknowledging the displayed report uses the existing short FileStore lock and atomic replacement, checks task/reported revision, and never changes task revision or causes a reporting conflict merely because the UI was read. Existing records without a report position remain unknown until a new report, rather than inferring history from timestamps.

The home counts tasks with new reports, not an invented number of report events. Explicit intervention sorts first, then unread, then known report recency. Opening the popover/task or refreshing never acknowledges it. The user explicitly chooses “여기까지 확인”. Shared sessions retain independent task read positions. Corrupt or future cursors remain visible errors; raw files and task reports continue to be preserved.

## Verification (2026-10-31)

- 45 focused Python tests passed: cursor, UI actions, projection and host. The 12 cursor tests include thread races with a real common lock, restart, old-format unknowns, stale revisions, repeated acknowledgement and damaged file preservation.
- Broader cooperative/CLI regression checks passed during implementation.
- Frontend tests exercise explicit-only acknowledgement and preserve drafts/navigation. Final actual native read-button, later-report and restart acceptance is recorded in S8.
- See [read cursor contract](../UI_READ_CURSOR_CONTRACT.md) for the separate schema and concurrency boundary.
