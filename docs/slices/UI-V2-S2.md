# UI-V2-S2 — task detail and safe interaction

Baseline `3bbb037`; branch `p0/desktop-probe`, Draft PR #1.

Implemented task detail with explicit situation/intervention/next-action/resume roles, free-form tracked values, item source/time, pin controls, evidence, linked-session drilldown, preserved notes/View and paginated before/after history. Candidate resources and legacy registered snapshots stay distinct. Existing v1 records without roles remain valid and are not automatically classified. Only a non-archived `role=intervention` item with `attention_required=true` contributes to the intervention count. New optional fields are preserved by reports and safe edits; older binaries do not understand newly populated fields, so keep the new reader when rolling back UI code.

The local bridge accepts bounded JSON strings from its own main-frame document. Writes run on the worker, through existing task revision checks, receipts, history and pin protections. A draft keeps its starting revision; conflict retains text and requires an explicit retry. Task-specific DOM and callback state preserve input/scroll/disclosure and prevent late responses replacing another task. History reads deserialize a bounded page while holding the existing lock; oversized individual records produce an explicit error. No Devin writes or model calls were added.

## Verification (2026-10-31)

- 59 targeted Python tests passed, including actual CLI role round trips, OCC/pin/history and bounded UI requests. Core agent additionally ran cooperative CLI/integration regressions successfully.
- Node helper and DOM tests passed, including actual Python Core→Projection→JS data, draft conflict/retry, late/duplicate responses and JSON-string bridge transport.
- Actual Mac WKWebView: 7 synthetic tasks, intervention count 1, source-separated task details and candidates visible. Added a note through the actual UI; success was visible and persisted in revision 8. History retrieval displayed revisions 8 through 1. Existing note remained.
- Actual testing found and fixed a role-name mismatch and WebKit numeric bridge conversion issue. Pure helper tests alone had not caught them.
- A whole-working-tree run passed 325 tests with 2 opt-in model skips while the future, unconnected S5 adapter was also present. This is not the exact S2-only release count; final S8 reruns the complete committed tree.

The installed reporting skill in an existing Devin workspace is not silently replaced. Its updated repository instructions support role metadata for subsequent explicit installations. Existing sessions continue with general items until they report roles. Lifecycle, resource editing, token display and read cursors follow in S3–S7. Full native restart/accessibility acceptance remains S8.
