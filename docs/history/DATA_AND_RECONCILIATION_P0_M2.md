# Data and Reconciliation Specification

| 항목 | 내용 |
|---|---|
| 문서 유형 | 과거 계획·결정·검증 기록 |
| 상태 | 보존; 현재 지시나 미완료 목록이 아님 |
| 기준 | 정리 전 원문; 당시 날짜·커밋·검증 범위는 본문 참조 |
| 적용 범위 | 당시의 구현·실험에 한정 |

> 현재 구현은 [기준선](../BASELINE.md), 남은 작업은 [백로그](../BACKLOG.md)를 따른다. 본문의 현재·다음·미구현은 당시 시점의 표현이며, 완료나 미검증 판정을 소급 변경하지 않았다.

## M3 storage and reporting contract (2026-10-30)

새 보고 경로는 파일 원본·조건부 커밋·명시적 고정값 보호를 따른다. [S01](../slices/M3-S01.md)은 저장/CLI, [S02](../slices/M3-S02.md)는 실제 세션 전달/메뉴, [S03](../slices/M3-S03.md)은 기존 자료의 명시적 이관이다. task 스키마의 선택적 legacy 영역은 가져온 View·리소스·커서 원형을 보존한다. 이관 당시 값은 legacy_import, 이후 세션이 보고한 항목은 reported다. hooks의 context/activity는 세션 ID·시각·이벤트만 저장하고 대화/압축 요약을 복제하지 않는다. 원본 관측과 보고 state revision은 분리한다.

후속 확정된 [상태 트랜잭션 계약](STATE_TRANSACTION_CONTRACT_2026-10-30_KO.md)은 낙관적 읽기–판단–커밋, 세션별 항목 정의·업데이트 출처, 명시적 고정값 보호를 정한다. 미래 설계에서는 사람이 수정한 추적값도 고정하지 않았다면 후속 유효 보고로 갱신할 수 있고 메모·변경 이력은 항상 보존한다. 이 정책은 새 M3 경로에 적용한다. 아래 기존 M2의 인간 소유 전환·보호 규칙은 보존 중인 기존 런타임의 계약이다.

**아래 `normative`를 포함한 P0~M2 본문은 역사적 범위다.** 새 보고 경로에서는 세션이 의미를 조정하며 최신 유효 커밋을 사용한다. 기존 모델 패치·의미 커서와 새 보고 revision을 같은 상태로 혼용하지 않는다.

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

The bounded manual semantic vertical slice is implemented on the existing branch. Personal Codex exec was run with two synthetic session sources and with actual synthetic Devin Desktop sessions through the browser. Safe machine-owned patches, separate semantic cursors, human preservation, usage/budgets, retry and failure isolation are implemented. After an initial auth failure, personal browser reauthentication succeeded and actual Devin ACP new-session streaming plus the two-session patch integration test passed. Dangerous tool denial is mock-tested and internal organizational policy/isolation still require target-environment signoff. This does not close the real-work dogfood/product-quality gate. See [M2 implementation, measured usage, acceptance evidence and limitations](M2_PROGRESS.md). The contracts above remain the baseline for the retained M2 runtime; M3 follows the newer contracts linked at the top.
