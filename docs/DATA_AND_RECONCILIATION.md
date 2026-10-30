# Data and Reconciliation Specification

## 2026-10-30 planned storage and reporting change

[설계 변경안](DESIGN_CHANGE_PROPOSAL_2026-10-30_KO.md)의 파일 원본·직접 편집·버전 스키마·범위별 오류 격리 요구가 새 방향이다. 보고를 켠 작업 에이전트가 CLI로 의미 변경을 전달하고 Core가 검증하는 계약을 계획한다. 다대다·출처·인간 정보·충돌·신선도 보호는 유지한다. 파일 저장의 원자성, 보고와 checkpoint, 기존 SQLite의 전환은 아직 미설계·미구현이며 아래 DB 트랜잭션 보장을 새 저장 구조에 그대로 적용했다고 가정하지 않는다. 아래 내용은 기존 P0~M2 계약·구현 기록이다.

## Entities

Task(id,title,description,lifecycle,createdAt,updatedAt); Session(id,provider,externalId,model,runtimeState,lastSeen,sourceVersion); Contribution(id,taskId,sessionId,role,scope,linkedBy,confidence,from,to) permits many-to-many relationships.

Resource(id,taskId,kind,uri,repo,branch,externalStatus,verifiedAt) links tickets, PRs, repositories, worktrees, commits, tests and documents. Observation(id,sessionId,sourceKey,sequence,kind,contentHash,sourceRef,observedAt) is an immutable normalized event. Checkpoint(sessionId,sourceFingerprint,adapterVersion,lastStableKey,prefixHashes,lastCommittedAt) is persisted transactionally.

TaskView(id,taskId,revision,sections) contains stable section IDs and ordering. ViewItem(id,sectionId,content,status,evidenceRefs,origin,confidence,pinned,createdAt,updatedAt,supersededBy) is durable. ReconcileProposal(id,taskId,expectedRevision,patch,model,tokenUsage,validationStatus) is validated before applying.

## Incremental sync invariants

Discover actual Devin Desktop sources rather than assuming CLI storage layout. Read-only snapshots must tolerate concurrent writers. Normalize user, assistant, tool_call, tool_result, plan and unknown events. Prefer verified stable message IDs; fall back to anchored content hashes and common prefix/suffix checks. Byte offsets alone are unsafe for rewritten JSON.

Test append, same-length edit, rewrite, truncation, reordering, compaction, duplicates, missing records, malformed JSON, SQLite locking and crash between observation persistence and checkpoint. Persist derived state and checkpoint atomically. Replay is idempotent; ambiguous divergence is surfaced, not silently skipped.

Hooks or file watchers are optional wake signals. Polling and manual refresh always work. Debounce bursts. If a source disappears, preserve last-known state with stale timestamp.

## Stable views

Templates may cover implementation, investigation, design and incident response, but sections remain task-specific and freely composable. Keep section/item IDs, order, user edits and pinned content. No whole-view rewrite, destructive deletion or silent merge. Use superseded/retracted states. Uncategorized findings go to Inbox. Structural changes require approval. Conflicting claims coexist and link to provenance.

## Semantic patch contract

Inputs: only relevant new normalized events, affected existing ViewItems, the view contract and evidence references. Output: bounded add/update-status/attach-evidence/supersede/propose-section operations. Check expected revision, IDs, evidence, permissions and non-destructive constraints. Rejected patches are retained for diagnosis.

Do not equate agent claims with externally verified completion. PR/ticket status is authoritative only when fetched from approved external API. Never fabricate progress percentages.


## Implemented M1 subset

The existing Resource remains the human-confirmed task connection. M1 adds small discovered-resource fact, task-choice and Git-observation tables for candidates/provenance, one plan-record table for the observed structured snapshots, and persisted collector health. It does not implement generic claims, a plugin system or semantic patch reconciliation. Raw transcripts are not archived, but extracted resource values and plan text/status are retained locally as sensitive derived data. See [M1 implementation and validation](M1_PROGRESS.md).


## M2 normative semantic state protocol

The **Task** is the unit of semantic reconciliation; one task may draw from many sessions, and one session may contribute to several tasks. Maintain distinct collector and semantic cursors per task/session contribution. Source reordering, compaction or missing evidence must not silently advance semantic progress.

Input is a bounded delta bundle with stable evidence references, provenance, affected view items and the current per-task View Contract. Structured extraction precedes model interpretation. For ambiguous order, provide explicit uncertainty rather than inventing chronology. The model proposes *operations*, not a rewritten document.

Supported conceptual operations: add evidence-backed item, update machine-owned item status, attach evidence, supersede machine-owned claim, propose new section, flag contradiction and no-change. The Core checks item IDs, expected revision, source scope, evidence availability, ownership, conflict policy and deduplication key. Machine-generated material is stored separately from human-owned items; a user correction becomes authoritative for that item until explicitly changed by the user. Never delete, overwrite pinned content or silently reconcile contradictions.

Use deterministic idempotency keys tied to task, source delta identity, view revision and operation identity. Commit accepted effects and semantic cursor transactionally. On rejection or failure, retain pending delta and explicit semantic staleness; do not claim freshness. A no-change result may advance the cursor only when durably recorded. Previously interpreted facts may need retraction when their source changes: mark disputed/superseded, do not erase history.

Policy: safe additive evidence-backed updates can auto-apply to machine-owned projection. Human approval is reserved for view structure changes, destructive effects, uncertain conflict resolution and changes to human-authored content. Confidence scores alone never constitute verification. A claim's evidence may be agent-reported rather than independently verified; keep this distinction in UI.

Cost: zero-token reads; no LLM for unchanged input; manual semantic refresh first; per-task/daily hard budgets; cache by relevant delta + view revision + model/prompt version; persist usage and freshness. The current P4 single-node Ollama proposal is a plumbing experiment, **not** this protocol.


## Semantic model adapter contract (normative)

`analyze({taskId,viewRevision,viewContract,affectedItems,evidenceDelta,limits}) -> {proposal,usage,providerRunId,diagnostics}`.

Provider implementations: `CodexExecAdapter` for personal development/tests, `DevinAcpAdapter` for internal production. Adapters must not own state, mutate task views, or contact existing work sessions. A separate isolated analysis run/session receives only bounded, selected evidence; tool/permission requests must be denied unless specifically needed and approved.

Use a strict, versioned patch JSON schema and validate output again in the deterministic Core. Include stable evidence IDs, expected revision, operation IDs, uncertainty and source attribution. Usage may be unavailable: represent it as unknown rather than zero. Enforce hard limits before starting a run where measurable; when provider quotas cannot be guaranteed, expose this limitation and fail safely under configured call limits.

Codex CLI: noninteractive execution, isolated working directory, bounded prompt, output artifact, timeout and exit-status checks; do not assume token usage is always present. Devin ACP: negotiate capabilities, create a **new analysis session**, submit prompt, consume streamed chunks to a terminal result, reject unauthorized tool requests, handle disconnects, timeouts, cancellation and unsupported functionality. Session creation/streaming details must be verified against installed Devin implementation before enabling it.

No semantic checkpoint advancement until an accepted patch or durable no-change result commits. Never replay an analysis output as a trusted fact merely because it conforms to JSON schema.


## M2 implementation record (2026-10-29)

The bounded manual semantic vertical slice is implemented on the existing branch. Personal Codex exec was run with two synthetic session sources and with actual synthetic Devin Desktop sessions through the browser. Safe machine-owned patches, separate semantic cursors, human preservation, usage/budgets, retry and failure isolation are implemented. After an initial auth failure, personal browser reauthentication succeeded and actual Devin ACP new-session streaming plus the two-session patch integration test passed. Dangerous tool denial is mock-tested and internal organizational policy/isolation still require target-environment signoff. This does not close the real-work dogfood/product-quality gate. See [M2 implementation, measured usage, acceptance evidence and limitations](M2_PROGRESS.md). The normative contracts above remain the baseline.
