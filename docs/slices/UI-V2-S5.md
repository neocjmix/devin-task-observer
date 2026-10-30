# UI-V2-S5 — session detail and retained token metrics

Baseline `dc053b4`; branch `p0/desktop-probe`, Draft PR #1.

Session detail separates activity, per-task last report, reporting preference/effective state, workspace and linked tasks. Reporting toggles use task OCC. Optional token metrics live once per session and are never allocated or added again across tasks.

The dedicated reader uses the observed Devin Local SQLite schema 17 in read-only/query-only transactions. Actual native `input_tokens` means new input; total input is new plus cache reads. Deduplication uses (session ID, message ID), retaining node references. Conflicting repeated metrics, unsupported schema/cache creation, malformed values and missing metrics are unavailable rather than zero. Totals cover retained records, may include inherited fork history, and are not billing or guaranteed lifetime totals. Read time and source activity time remain separate.

The acquisition worker reuses one scope's cache for 15 seconds. Manual Refresh can force a read; state edits only refresh the projection. Failed reads retain same-scope last-good metrics with stale/error markers; changing source/workspace cannot expose the old scope's cache. Size/node/time bounds keep this optional read independent of normal session work.

## Verification (2026-10-31)

- 61 focused Python tests passed: 26 reader/cache tests plus projection, actions and host regressions.
- Actual existing personal synthetic Devin workspace: 32 nodes deduplicated to 16 messages; total input 317,590 = new 10,902 + cached 306,688; output 2,485. These exactly matched the retained ATIF export. No new model calls or prompts.
- Actual acquisition pipeline against isolated synthetic source: 8 sessions; first session total 1,101 = new 101 + cached 1,000, output 41. Source database hash unchanged after read.
- Frontend helper/DOM regressions cover unavailable/stale metrics, activity versus report time, shared-session reporting controls and candidate evidence scoped to the explicitly selected task. Candidates from unrelated sessions remain globally visible but are not attached as that task's evidence.
- Final native session-detail interaction belongs to S8 acceptance. Real metrics verification above is a source/adapter check, not a claim of live UI acceptance or full billing accuracy.
