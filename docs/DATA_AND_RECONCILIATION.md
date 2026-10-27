# Data and Reconciliation Specification

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
