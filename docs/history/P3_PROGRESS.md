# P3: Persistent views and local dashboard (prototype)

| 항목 | 내용 |
|---|---|
| 문서 유형 | 과거 계획·결정·검증 기록 |
| 상태 | 보존; 현재 지시나 미완료 목록이 아님 |
| 기준 | 정리 전 원문; 당시 날짜·커밋·검증 범위는 본문 참조 |
| 적용 범위 | 당시의 구현·실험에 한정 |

> 현재 구현은 [기준선](../BASELINE.md), 남은 작업은 [백로그](../BACKLOG.md)를 따른다. 본문의 현재·다음·미구현은 당시 시점의 표현이며, 완료나 미검증 판정을 소급 변경하지 않았다.

Historical milestone record. See [M2 progress](M2_PROGRESS.md) for the current semantic pipeline and live adapter results.

Status: **partially implemented and tested; not full acceptance**. The dashboard is available only on `127.0.0.1`. Basic operation uses no LLM or external services; a separate opt-in local model experiment is described in [P4 progress](P4_PROGRESS.md). The active experimental instance uses a disposable synthetic workspace and a temporary test store.

## Implemented

- Task-specific, persistent views with stable section/item IDs, custom section names and order, user-authored items, pinning, explicit status changes, and a revision checked on writes. Prior item versions are retained. There is no automatic item deletion, whole-view rewrite, silent merge or AI update.
- Items can be moved between sections or reordered with a view revision check; stable IDs, content, pins and evidence remain attached.
- Evidence can be attached only to a linked task/session and an already observed node/hash. Retrieval checks the hash again; unavailable or changed evidence fails rather than returning another version. The raw source field may contain injected context and the UI says so.
- A simple localhost dashboard creates and updates tasks, links sessions, adds manual resources (external status explicitly unknown), creates/reorders/renames sections, edits and pins items, attaches evidence and reads it on demand. It shows observed and unclassified sessions and provides paged event metadata with a warning that the order is not yet a verified transcript order. Collection state is not presented as the agent's execution state. Polling runs locally and a manual refresh is available.
- Session detail also offers a current-chain view in parent-to-child order, with the count of present nodes outside that chain and a warning that compaction and branching exclude history. This is a structural view, not a verified complete transcript; the all-observations index remains separately available.
- When a structural change is safely recoverable, the session screen shows missing-node counts and offers explicit acknowledgment of a freshly refreshed snapshot to clear the structural warning. Readable observations continue while the warning remains. Prior observation metadata remains and a warning stays visible if historical source nodes are absent. Ambiguous or missing source sessions cannot be acknowledged.
- HTTP requests enforce loopback binding, Host checks, an in-memory per-process token, Origin/content-type checks for writes, no CORS, and no request logging. Source access is limited to explicitly configured personal workspaces. This is not an independent security audit.

## Actual validation

The browser-to-API-to-SQLite flow was exercised on actual **synthetic** Devin Desktop sessions. In the dashboard, two tasks and two sessions were linked many-to-many; a manual note was saved, pinned and linked to a hash-checked synthetic source response; a custom section was added and reordered; a PR example URL displayed as unverified. After browser and server restart, the saved note, pin, evidence and section order remained. A draft was retained in the page while the section order changed, and the user was offered an explicit reapply action for the new view revision.

The browser also displayed the observed two-item synthetic plan from hash-checked source evidence, marking both source statuses as unverified and showing the raw tool call and the added tool metadata. This preview is limited to the one observed argument shape.

The current full-suite result is reported in [M1 progress](M1_PROGRESS.md), including registry and view preservation, revision conflict, evidence scoping, section/item movement, task-specific review progress, HTTP request guards, workspace isolation, parent-chain and event pagination, and a full API task/link/view/evidence flow. The browser was also used to unlink and restore a synthetic session, edit and restore a synthetic resource, and move/reorder a note without losing its pin or evidence. Two synthetic tasks linked to the same real Desktop synthetic session retained separate review positions. A fixture session appeared automatically while a user entry remained intact. For the live synthetic session that underwent compaction, the current-chain view showed 11 nodes and 41 present nodes outside the chain; a synthetic user node's source content was retrieved. Recovery and its persistent missing-content warning were exercised in a separate synthetic SQLite fixture. Browser console had no errors during the earlier checked flow. No real company data or private transcripts were used.

## Remaining

- P1 still needs a verified complete ordered event timeline, correct handling of all source variants and recovery of unresolved ambiguity. The dashboard exposes the current event index with explicit ordering and freshness limitations; it does not claim a complete transcript.
- A dashboard control for editing contribution metadata, search, conflict/claim management, and richer attention triage remain incomplete. M1 adds a lightweight configured process entry point and optional macOS LaunchAgent rather than an application bundle. The simple task counts are not progress percentages or completion claims. Unsaved browser drafts are retained during in-page rerenders but not across a browser reload or crash.
- Evidence bodies are read on demand from Devin and are not retained by the observer. If Devin removes or changes them, their old content is unavailable and lookup reports an error; hashes and references remain. This is a material retention limitation.
- Only Desktop 3.10.67 / Local schema migration 17 has been tested. Cloud, Cascade, other ACP agents, scale and long-running behavior remain unverified. Full P4 semantic reconciliation is not implemented; basic operation makes zero LLM calls.


## M1 update (2026-10-29)

Open task reviews and automatic resource/plan regions now update on polling and manual Refresh. Editable DOM is retained, review pages remain capped at 50, cursor advancement is still explicit and task-specific, and expanded review evidence is reused. Navigation epochs and per-request sequences reject stale responses, including same-task detail races. Task metadata and view are read together in one snapshot.

The browser displays resource candidates, local Git facts and agent-reported session plans without a model. Resource candidates include evidence and shared-session/workspace warnings. Plan transitions use exact unique text and source graph ancestry; ambiguous branches, revisions and unsupported shapes remain visible rather than selecting a latest plan by timestamp. Plans from different sessions remain separate.

`observerctl` provides configure/run/status and explicit autostart install/remove. The dashboard shows last successful collection time and safe error guidance. A same-origin custom-header token bootstrap lets an already open page recover after service restart without discarding drafts. No CORS is enabled. A temporary macOS service restarted after SIGKILL and was unloaded; no permanent login startup was enabled. See M1 for the actual Desktop/browser scenarios and remaining limits.


## M2 follow-up (2026-10-29)

The statements above describe the earlier milestone. [M2](M2_PROGRESS.md) now implements separate task/session semantic cursors and a bounded multi-session patch pipeline through SemanticModelAdapter, with real personal Codex validation and a partially live-verified ACP transport. The original collector, many-to-many links, human View, review cursors and default-off legacy Ollama experiment are preserved. Target ACP authentication/streaming and real-work usefulness remain open; see M2 for current evidence and limits.
