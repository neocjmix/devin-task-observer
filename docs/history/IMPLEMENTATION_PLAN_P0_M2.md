# Devin Task Observer — Implementation Plan

| 항목 | 내용 |
|---|---|
| 문서 유형 | 과거 계획·결정·검증 기록 |
| 상태 | 보존; 현재 지시나 미완료 목록이 아님 |
| 기준 | 정리 전 원문; 당시 날짜·커밋·검증 범위는 본문 참조 |
| 적용 범위 | 당시의 구현·실험에 한정 |

> 현재 구현은 [기준선](../BASELINE.md), 남은 작업은 [백로그](../BACKLOG.md)를 따른다. 본문의 현재·다음·미구현은 당시 시점의 표현이며, 완료나 미검증 판정을 소급 변경하지 않았다.

## Current delivery — M3 (2026-10-30)

[슬라이스 목록](../SLICES.md)에 따라 S01 파일 상태·CLI, S02 실제 스킬·훅·메뉴 막대·수집기, S03 보존 이관·복원을 순서대로 구현한다. 사용자 승인 범위는 M3 전체이며 [설계 변경안](DESIGN_CHANGE_PROPOSAL_2026-10-30_KO.md)과 [트랜잭션 계약](STATE_TRANSACTION_CONTRACT_2026-10-30_KO.md)을 따른다. 범위별 실제 결과와 미검증 항목은 개별 슬라이스에 기록한다. 기존 SQLite 관측 체크포인트는 유지하며 새 태스크의 원본은 파일이다.

**이 아래 P0~M2 본문은 기존 런타임의 역사적 계약과 검증 기록이다.** 아래 `normative`, `next`, `First task`는 당시 단계에만 적용되며 새 슬라이스의 착수 조건이나 우선순위가 아니다. 기존 별도 모델 경로를 확장하거나 자동 실행하는 지시로 사용하지 않는다.

Historical starting status: planning, 2026-10-27. Devin Desktop internals require verification on a personal installation.

## Objective and constraints

Track 10+ Devin Desktop sessions without repeatedly asking agents for ticket, PR, plan, decisions, findings and next actions. Support development, investigation, design and mixed tasks.

- Work directly in Devin Desktop, CLI or IDE, never via mandatory wrapper.
- Manager reads existing sessions only. Optional launch command is permitted; no session prompts/control.
- Many sessions per task, and multiple tasks per session.
- No required agent skill, MCP writer or hook. Hooks can trigger observation.
- Deterministic collection and state store. Optional budgeted LLM for meaning extraction.
- Persist stable per-task views, manual edits, evidence and freshness.
- Personal development uses public documentation and synthetic/personal data only.

## Architecture

Devin local data -> read-only adapter -> incremental diff -> SQLite observations -> deterministic projection -> persistent task view -> local dashboard. Optional semantic reconciler proposes evidence-linked patches. Never attach as a competing ACP writer. Observer failure must not affect Devin.

## Data model

Task; Session; Contribution (many-to-many); Resource (ticket, PR, repo, worktree, test); Observation (normalized event, source ref, hash); Checkpoint (source fingerprint, processed keys); TaskView (stable section IDs); ViewItem (stable ID, content, evidence, provenance, pinned); ReconcileProposal (revisioned patch); Claim (verification and conflicts).

## Stable View Contract

Sections are arbitrary per task; templates are optional. Preserve IDs, order, human edits and pinned content. No automatic deletion, merging or whole-view regeneration. Add uncategorized findings to Inbox; structural changes require approval. Contradictions coexist with provenance.

## Incremental collection

Probe Devin Desktop actual local storage, IDs, rewrites and hook payloads. Normalize user/assistant/tool/plan events. Compare with committed checkpoint using stable IDs when verified, otherwise anchored hashes. Handle append, edit, rewrite, truncate, reorder, compaction, duplicate and malformed input. Commit events and checkpoint atomically; mark ambiguity stale rather than skipping. Manual refresh and polling must work without hooks. Zero LLM tokens for collection.

## Semantic reconciliation

Default OFF. Analyze only new relevant events plus affected view items. Produce bounded patch proposals; validate revisions, evidence and non-destructive rules before applying. Never analyze unchanged deltas. Enforce daily token budget, cache, usage ledger and approved model policy.

## Security

Localhost-only; no telemetry or default egress; read-only Devin access; minimal retention; no transcript upload or credentials; redact diagnostics. Keep company data and personal GitHub strictly separate. Import through company-approved path.

## Delivery

| Phase | Goal | Gate |
|---|---|---|
| P0 | Probe real Devin Desktop local storage/hooks | Reliable read-only discovery and change detection |
| P1 | Adapter, diff, checkpoint, fixture tests | Rewrite/restart/crash tests pass |
| P2 | Task registry and many-to-many links | Multiple sessions/tasks work |
| P3 | Stable views, dashboard, manual edits | Zero-token lookup and preserved views |
| P4 | Optional semantic patching | Evidence-linked, non-destructive, budgeted |
| P5 | Dogfood, hardening, internal import | Security and usefulness verified |

## Acceptance

Devin unaffected by observer crashes; no writes/prompts to sessions; idempotent replay; stale indicators; no lost user edits; conflicting findings retained; no model required for basic operation. Measure session revisits/day, time to find ticket/PR, incorrect claims, and token cost.

## First task

Implement a read-only personal Devin Desktop probe reporting session discovery, source schema, write behavior and stable identifiers without storing message bodies. Document sanitized findings before building the dashboard.


## Product North Star — M2 Semantic Task Tracking (normative)

The product is **not** a manual notebook or merely a deterministic session dashboard. After a user creates a task and links relevant sessions once, the manager must maintain an **always-readable, stable, task-specific current-state view** with progress, blockers, decisions, unresolved questions, next actions, tickets/PRs and supporting evidence. The ordinary interaction is **read, not retype, ask each agent, or approve every routine update**. Sessions remain independent and read-only to the manager; no agent-side reporting contract is required.

M1 deterministic resource and structured-plan discovery is the foundation, **not product completion**. The next product-critical milestone is **M2: incremental semantic reconciliation**. LLM calls are optional at runtime and budget-controlled, but semantic understanding is required to deliver the full product goal. Default-off/zero-token operation must continue to function with an explicitly marked semantic freshness gap.

### M2 minimal vertical slice (implement before broad polish)

1. For a task linked to two sessions, obtain new normalized evidence since each *semantic* checkpoint; deterministic collection checkpoints and semantic checkpoints are separate.
2. Use rule-based extraction first; select only relevant new natural-language/tool events and affected existing view items. Retrieve older context only if a reference requires it. No whole-transcript or whole-view summarization on routine refresh.
3. Send the task-specific View Contract, affected items and evidence IDs to a configurable **approved** model adapter. Start with manual Refresh; add debounced automatic updates only after accuracy and cost are measured.
4. Produce structured, bounded patches for progress, blockers, decisions, open questions, next actions and task-specific sections. Distinguish reported claims, verified facts and hypotheses. Cross-session contradictions remain visible.
5. Deterministic Core validates revision, provenance, permissions, idempotency and non-destructive rules. Auto-apply only safe, evidence-backed additive/status changes to a separate *machine-managed* projection. Never overwrite human-owned or pinned items. Destructive changes, conflict resolution and View structural edits require explicit approval.
6. Persist semantic checkpoint only with successfully committed effects or a durable reviewed/no-change outcome. Retries must not duplicate items; failed/budget-exhausted updates retain pending deltas and show stale semantic status.
7. Dashboard reads saved state without model calls and shows last semantic refresh, source provenance, uncertainty and pending changes. User can correct/override results without losing subsequent updates.

### Acceptance: task-state utility, not only passing tests

With two linked sessions containing a synthetic cross-session dependency change, the dashboard must automatically show the changed blocker and next action after one semantic refresh, preserve previous decisions and human notes, and provide traceable evidence. Replay produces no duplicates. Contradictory evidence is not silently resolved. Unchanged refresh consumes zero model tokens. Budget exhaustion leaves deterministic tracking usable. Compare against manually labeled synthetic scenarios for omissions and unsupported assertions; measure status-only session revisits in dogfood.

### Explicit non-goals for M2

No ACP control, wrapper, agent reporting skills, broad provider support, full chronological transcript reconstruction, generic workflow engine, cloud service, UI redesign or expansion of the legacy single-message Ollama experiment. Reuse existing storage/projections where possible; avoid speculative abstraction.

### Delivery priority override

P0–P3 and M1 are infrastructure milestones. **M2 semantic task tracking is the next critical path, ahead of further UI polish, generalized transcript reconstruction, and new metadata features.** P5 dogfood follows M2. Do not claim the product goal achieved from deterministic candidates alone.


## M2 model execution decision (normative, 2026-10-29)

The semantic reconciler uses a provider-neutral `SemanticModelAdapter`. **Personal development and integration tests use Codex CLI in noninteractive mode**; **internal deployment targets Devin ACP**. The existing local Ollama single-message experiment is legacy, not the M2 default and not a reason to delay the primary adapters.

Neither adapter may attach to, prompt, resume or seize an existing user task session. The Devin ACP adapter must create **separate analysis-only sessions** in an isolated workspace; ACP is a transport for analysis, not a task-session controller. No mandatory wrapper for user work.

Implement and test Codex CLI end to end first, with schema-constrained output where supported, usage collection, explicit timeout/cancellation, restricted filesystem/tool access, and failure isolation. Implement a Devin ACP adapter behind the same interface with protocol negotiation, independent session creation, prompt, streamed response assembly, structured patch validation, timeout/cancellation and explicit unsupported-capability errors. Treat Devin ACP unattended execution, auth, concurrency, permission-denial behavior and token reporting as **unverified until tested in the target environment**. Do not invent protocol support or mark production-ready prematurely.

Both adapters return the same bounded patch proposal and usage/diagnostics shape. The deterministic Core validates every patch independently of model or provider. A model failure must not advance semantic cursors or damage saved views. Credentials stay in approved local stores and never in source control. Internal transcripts must never be sent to personal Codex accounts; company use is restricted to the approved Devin environment. Keep local-only zero-token deterministic observation available.


## M2 implementation record (2026-10-29)

The bounded manual semantic vertical slice is implemented on the existing branch. Personal Codex exec was run with two synthetic session sources and with actual synthetic Devin Desktop sessions through the browser. Safe machine-owned patches, separate semantic cursors, human preservation, usage/budgets, retry and failure isolation are implemented. After an initial auth failure, personal browser reauthentication succeeded and actual Devin ACP new-session streaming plus the two-session patch integration test passed. Dangerous tool denial is mock-tested and internal organizational policy/isolation still require target-environment signoff. This does not close the real-work dogfood/product-quality gate. See [M2 implementation, measured usage, acceptance evidence and limitations](M2_PROGRESS.md). The contracts above remain the baseline for the retained M2 runtime; M3 follows the newer contracts linked at the top.
