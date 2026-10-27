# Devin Task Observer — Implementation Plan

Status: planning, 2026-10-27. Devin Desktop internals require verification on a personal installation.

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
