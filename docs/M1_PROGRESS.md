# M1 — Useful Observer

Status: implemented and validated for the tested personal Local configuration (2026-10-29). Full P1 transcript acceptance and long-running production readiness remain open. Continues `p0/desktop-probe` and draft PR #1; baseline `ecf7b28`, 90 passing tests. Only Devin Desktop 3.10.67 Local schema 17 is supported by observed evidence. P4 remains default-off and is not expanded.

## Implemented so far

- Live updates fetch the currently selected task's review page and automatic projection. Only observational regions change; editable DOM and review cursors are retained. Navigation epochs and request sequences discard responses from a prior task/session, including switching away and back. Existing expanded review evidence is reused.
- Deterministic resource candidates from recognized message text and fixed read-only Git commands. Mentions, locally checked Git values, task association and external status are separate. Every automatically discovered item remains a candidate until explicit confirmation; external status stays unknown. Shared sessions and workspace-only Git facts never imply exclusive task attribution. Confirmation reuses the existing Resource model and does not edit human values.
- Session-scoped plan snapshots from the observed structured `todo_write` shape. Duplicate message/tool/plan snapshots share evidence. Only source ancestry establishes order; disconnected plans, source revisions and unsupported shapes are explicitly uncertain. Exact unique item text permits added, removed-or-replaced and agent-reported completion/reopening comparisons. No actual completion verification or cross-session merge is performed.
- `observerctl configure`, `run`, `status`, and explicitly opted-in `autostart install/remove`. Private atomic JSON settings, persistent existing SQLite state, one service per store, bounded retry backoff and graceful termination. Login auto-start is not silently enabled.
- Collection health distinguishes unavailable source, readable ambiguity, readable structural divergence and normal readable data. Normal tested compaction/head changes and exact/observed timing duplicates retain the existing conservative handling. Unreadable input preserves checkpoints; readable divergence is recorded with a warning. No universal transcript restoration is attempted.

## Validation recorded during implementation

- Baseline suite: 90 passed. Latest completed suite: 115 passed, including JS controller regression checks, scoped resource discovery, plan lineage/ambiguity, API integration and actual process restart/SIGTERM integrity tests. Node is a development-only dependency for JS regression tests.
- Browser with a disposable SQLite source: new events appeared automatically in an already open review list; manual Refresh showed another event; an unsaved note and its focused input survived.
- Actual Desktop: created a new isolated M1 synthetic Local session. Dummy ticket/PR/branch references and a two-item `todo_write` plan appeared in the observer after linking the session once. Multiple source references produced one resource candidate; external status remained unknown. Locally created disposable Git metadata produced repository, branch, worktree and HEAD commit facts without fetching a remote.
- Actual Desktop follow-up: two pending plan items changed to completed and in_progress. The open observer task automatically showed agent-reported changes and new observations while an unsaved note remained. The saved and pinned note survived observer process restart and browser reload.
- The same actual synthetic session was linked to two tasks. Both automatically received candidates with an explicit shared-session warning. In the browser, acknowledging 23 entries in one task left the other task at 23 pending; a second session on the first task showed a separate plan and separate evidence. Explicitly confirming the dummy PR in one task left external status unknown and did not confirm it in the other task.
- Live testing exposed an overbroad ticket match for the model name SWE-1.6. The extractor now rejects decimal-version prefixes; regression tests cover it and malformed URL ports. The incorrect experimental derivation is excluded by the extractor-version marker while source observations remain intact.

## Retention and limitations

Raw transcript bodies are still not archived. M1 persists extracted resource values and structured plan item text/status as derived state; these can be sensitive and must stay local. Source hashes/references are retained and original evidence is read on demand. A changed/deleted source body cannot be recovered.

Recognized text syntax is limited to GitHub PR/repository/commit URLs, Jira-style `/browse/KEY-N` and Linear issue URLs, ticket keys, explicit `branch:`/`브랜치:`, `worktree:`/`워크트리:` and `commit` labels. Mentions can be examples or injected context, so false associations are never automatically confirmed. Other hosts/providers and unlabeled prose are not generally understood. Local Git observes only an explicitly allowed worktree root and current HEAD, not the full commit history or the branch used by a past session.

Plan content has no observed stable item ID. Changed wording is removed-or-replaced plus added, not a verified identity match. Duplicate item text blocks item-level comparison. Unsupported/empty/new argument shapes are not guessed. Graph lineage does not establish a universal chronological transcript or externally verified work completion.

Long-running load, other Devin agents/versions, external ticket/PR verification and automatic startup across an actual macOS logout/login are not validated. A temporary launchd job did validate bootstrap, source collection, restart after SIGKILL and bootout; no permanent LaunchAgent was installed.


## M1 acceptance scenarios

| Scenario | Evidence |
|---|---|
| New event updates open task | Actual M1 Desktop session and disposable SQLite source; review rows and plan summary updated automatically |
| Manual Refresh updates open task | Disposable-source browser flow; a third event appeared without replacing the draft |
| Unsaved input survives live updates | Focused draft retained in fixture and actual Desktop flows; same-origin token recovery also retained it after service restart |
| Resource candidates, deduplication and provenance | Actual dummy ticket/PR/branch text plus local disposable Git repository; one candidate with multiple node references, PR number shown |
| No automatic false association | Shared-session warnings; candidates remained unconfirmed until explicit action; task-specific confirmation, manual edit/archive preservation and same branch names across workspaces covered by tests |
| Structured plan item/status | Actual Desktop generated pending, completed and in_progress states; displayed as agent reports, never verified completion |
| Multiple plans remain separate | Two real synthetic Desktop sessions linked to one task displayed distinct plans and session/node evidence |
| Independent review positions | Browser: one task acknowledged 23 entries while another retained 23; API and concurrency regression tests |
| Restart restores state | Actual process restart and page reload preserved saved note/pin/links/plan; process tests verified one observation after replay, SIGTERM exit 0 and SQLite integrity |
| Source divergence/ambiguity | Actual M1 `/compact` completed with four observed sessions and zero stale sessions, no manual recovery. Fixture tests distinguish readable ambiguity, unreadable JSON and readable structural divergence; failed reads retained checkpoints and last success |

The actual Desktop tests used only isolated synthetic sessions and dummy resources. No production transcripts, credentials, company repository or remote Git/API queries were used. The original P0 synthetic workspace gained a disposable local Git repository with an empty synthetic commit solely for read-only Git observation.

## Runtime and remaining limits

- `configure` never installs a login service. A user who wants login startup must explicitly choose `autostart install`; macOS may require background-item consent. The temporary service test was unloaded. The configured development instance observes only the known synthetic workspace.
- A page recovers its process-local request token through a custom-header same-origin bootstrap after server restart, then retries the rejected request once. No CORS is enabled. This avoids a full page reload and retains editable DOM. It is not an independent security audit.
- Source/derived reads remain bounded by polling and hashing entire selected sessions; sustained scale and every filesystem/WAL race are not proven. Git reads check HEAD/branch again before publishing, but cannot prove absence of every concurrent ABA change.
- Git uses PATH or the explicitly configured Git executable. The validation Mac uses an existing alternative because the system Git requires accepting an Xcode license. No Codex-specific executable path is built into product code.
- The discovery grammar is deliberately bounded (including URL/identifier length and explicit text labels). It does not understand arbitrary prose, every ticket host, GitHub Enterprise, all quoting conventions or all branch/commit command output. A branch or short hash with the same spelling in another workspace is a separate candidate.
- Derived resources and plan records are additive. Older unavailable source versions cannot be newly interpreted or their full bodies recovered. No automatic note rewrite, pin change, review acknowledgment, verified completion, external status inference or LLM analysis is performed.
