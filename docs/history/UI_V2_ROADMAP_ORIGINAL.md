# Observer UI v2 implementation roadmap

| 항목 | 내용 |
|---|---|
| 문서 유형 | 과거 계획·결정·검증 기록 |
| 상태 | 보존; 현재 지시나 미완료 목록이 아님 |
| 기준 | 정리 전 원문; 당시 날짜·커밋·검증 범위는 본문 참조 |
| 적용 범위 | 당시의 구현·실험에 한정 |

> 현재 구현은 [기준선](../BASELINE.md), 남은 작업은 [백로그](../BACKLOG.md)를 따른다. 본문의 현재·다음·미구현은 당시 시점의 표현이며, 완료나 미검증 판정을 소급 변경하지 않았다.

Goal: resume 5+ asynchronous Devin tasks without opening every session. Best effort; honest failure; minimal uncertainty UI. Keep deterministic observation LLM-free, provenance and history preserved, and session ownership untouched.

The user authorized S1–S8 in one autonomous execution on 2026-10-31. Implement and validate in dependency order, with a commit per slice.

2026-10-31 status: **S0–S8 implemented**, with actual macOS acceptance and explicit platform limits. See [S0](../slices/UI-V2-S0.md), [S1](../slices/UI-V2-S1.md), [S2](../slices/UI-V2-S2.md), [S3](../slices/UI-V2-S3.md), [S4](../slices/UI-V2-S4.md), [S5](../slices/UI-V2-S5.md), [S6](../slices/UI-V2-S6.md), [S7](../slices/UI-V2-S7.md), [S8](../slices/UI-V2-S8.md).
The default local app uses a Python-hosted NSPopover and local HTML/JS; NSMenu remains an explicit fallback. Final regression: 395 Python passed, 2 opt-in model tests skipped; JS regression passed.
Exact session tab activation is unsupported and uses the S6 safe fallback. S8 tested eight synthetic source sessions plus read-only compatibility with four real personal synthetic Devin sessions, not eight simultaneous live agents. VoiceOver speech and long-running login/logout remain unverified.

S0: Read-only normalized projections and synthetic fixtures. Preserve item-level actor/time, many-to-many task-session mapping, last_good and errors. No storage migration.

S1: NSPopover prototype with 420x600pt reference, task home, filters, attention first, collector health, scroll and keyboard navigation. Compare PyObjC native vs WKWebView; keep NSMenu fallback.

S2: Task detail: situation, user intervention, next action, resume entry, resources, tracked items, linked sessions, notes/history. Each item displays value, status, last editor/session and time. Evidence and pin in disclosure.

S3: Persistent resource registration and opening: Jira, wiki, test URL, repository/branch, PR, Slack, evolving Markdown deliverables, other references. Preserve source, distinguish observed candidates, support URL/file opening and copy fallback. Maintain free-form key/value.

S4: User completion/archive and per-task observation toggle. Completion disables observation; reopening restores prior setting. Active tasks may disable observation. Shared sessions continue collecting for other tasks. Preserve notes, pins, history, resources.

S5: Session detail: reporting on/off, last activity vs last report, workspace, linked tasks, tokens (total input, new input, cache input, output, measured time/source). Token totals are session-wide and never double allocated to tasks. SQLite read-only or ATIF after validation.

S6: Session activation feasibility spike. Test Devin Desktop session URL, exact tab/window, and ownership preservation. No session/load, forced resume or prompt sending. If unsupported, copy session ID; other ACP clients are out of initial scope.

S7: Last-seen changes using explicit task read cursors, attention and recent reports, sorting and filtering. Never mark all tasks read merely on popover open.

S8: Real macOS acceptance with 5-10 parallel sessions, dark/light, focus, accessibility, errors, stale records, restart, no LLM observation calls.

Every run: inspect latest branch/code and M3 contracts, implement slices in dependency order, test regressions, record verified vs unverified behavior, commit and push. Synthetic M3 parser content is not product copy.
