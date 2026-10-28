# Validation, Security, and Handoff

## Phase gates and estimates

P0: Personal Devin Desktop local-data and hook probe (4–12h). Go only if active sessions can be discovered and changed reliably via read-only access.
P1: Adapter, incremental diff, checkpoints, synthetic fixtures (12–24h). Pass rewrite/restart/duplicate/crash tests.
P2: Task registry and many-to-many contribution mapping (10–20h). Two sessions per task and multiple tasks per session.
P3: Persistent views, dashboard, manual corrections (16–32h). Zero-token reads and stable view structure.
P4: Optional semantic reconciliation (16–40h). Evidence-linked patches with strict budget.
P5: Dogfood, hardening and approved internal import (16–40h). Validate security and usefulness.

These are planning estimates, not delivery promises.

## Dashboard

Home: attention queue, active tasks, stale/unknown states, recent changes, unclassified sessions. Task detail: stable custom sections, ticket/PR links, plan, findings, decisions, blockers, freshness, evidence drilldown, contributions, manual edit and refresh. Session detail: read-only timeline, last seen and source health. Open-in-Devin links only after verifying deep-link support.

## Budget

Deterministic collection, diff, projection and dashboard reads must use zero LLM tokens. Semantic analysis is off by default and triggered by manual refresh initially. Later, use debounced stop/idle triggers. Cache by delta hash, view revision and model/prompt version. Enforce per-call/daily hard limits and a usage ledger. Budget exhaustion never disables basic tracking.

## Security

Personal development uses public docs, personal Devin sessions and synthetic fixtures only. Do not copy company transcripts, source, tickets, credentials, configs or incident details to personal environments. Localhost UI, no telemetry/egress by default, least-privilege file access, no writes to Devin storage, no ACP session takeover, minimal retention, redacted diagnostics and dependency/license review. Internal deployment uses company-approved transfer and models. No reverse sync from company repo to personal GitHub.

## Dogfood metrics

Count status-only session revisits/day; median time to find ticket, PR and next action; share of status questions answered without opening session; stale/incorrect claim rate; semantic tokens/day; bytes reprocessed; adapter failures. Test the hypothesis of 50% fewer status-only revisits rather than assuming it.

## P0 unresolved questions

Actual Devin Desktop storage locations and schemas across models; stable IDs and ordering; write frequency/atomicity; compaction and transcript rewrite; hook payloads in Desktop versus CLI; structured TODO availability; deep-link behavior; company data policy.

## Handoff

Use Codex for generic core/UI/fixtures and personal Devin only for integration probes. Keep external and internal repositories independent. If only documentation may be imported, implement internally from these plans. First deliverable: a read-only probe that reports discovered sessions, formats, change patterns and stable identifiers without retaining message bodies or sending data over the network.


## Current M1 handoff

The prototype now implements [M1 — Useful Observer](M1_PROGRESS.md) for the observed Local configuration. The current lightweight entry point and explicit login-startup choice are in the [README](../README.md#local-entry-point). Actual Desktop/browser evidence and fixture-only guarantees are listed separately in the M1 acceptance table. Full transcript reconstruction, external resource status, production scale and other provider support remain outside the validated delivery.


## Product-completion gate: M2 Semantic Task Tracking

**M1 is not the finished product.** The original user problem is repeated manual status reconstruction across 10+ sessions. After linking sessions once, the user should ordinarily *read* a continuously maintained task state, not transcribe findings or approve every ordinary update.

M2 must demonstrate: two sessions contributing to one task; incremental extraction of natural-language progress, blockers, decisions, open questions and next actions; persistent task-specific View structure; evidence-linked bounded patching; safe auto-apply to machine-owned items; preserved human edits; explicit conflicts and freshness; replay idempotence; zero-token unchanged reads/refresh; budget exhaustion fallback.

Implement a single complete vertical slice first, with synthetic labeled scenarios and real personal Devin Desktop smoke tests. Manual semantic refresh is sufficient initially. No complete transcript engine, agent-side hook contract, ACP control, cloud services or redesign required. Distinguish real Devin validation from synthetic fixtures and do not claim full product completion from test count alone.

Acceptance metrics: percentage of status questions answered without reopening Devin, unsupported-claim and omission rates against labeled scenarios, repeated-update duplicate rate, time to find blockers/next action, semantic tokens per useful update and number of user approvals. Record unresolved gaps and semantic staleness.

The next critical path after M1 is **M2**, before optional polish and broad compatibility work. The experimental P4 single-message local-model note proposal must not be mistaken for multi-session semantic reconciliation.
