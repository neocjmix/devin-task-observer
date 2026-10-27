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
