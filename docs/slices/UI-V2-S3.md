# UI-V2-S3 — persistent resources

Baseline `571bde0`; branch `p0/desktop-probe`, Draft PR #1.

Added optional task-local registered resources with issue/document/environment/repository/branch/PR/communication/deliverable/other categories. Free-form values remain supported. Registration/edit/archive/restore use existing OCC and receipts; source, creation time, evidence, notes, pins and history survive. Candidate selection only fills a form; a separate explicit save confirms the task association. Node/hash/workspace/source references are retained. External status remains `unknown`, including manually registered PRs. Legacy snapshots remain separately read-only.

The UI resolves open/copy targets from the saved task resource ID, never from a submitted shell command. HTTP(S) uses the OS handler. Existing md/txt/json/yaml/yml/pdf/csv documents can open; other types and failures retain copy fallback. The embedded view cannot navigate externally. OS operation failure is reported as failure, not merely a successful dispatch.

## Verification (2026-10-31)

- 55 focused Python tests passed: resources, actions, native target policy, roles and projection regressions. Core also passed broader CLI/cooperative regression checks.
- Node helper/DOM and actual Core→Projection boundary checks passed, including explicit candidate registration, evidence preservation, edit conflicts, archive/restore and open failure/copy fallback.
- Actual Mac popover: existing Markdown resource appeared under 산출물; path copy succeeded through NSPasteboard. Registered a new HTTPS resource through the actual form; a saved row and success message appeared. Existing candidates remained candidates.
- File/URL OS opening is policy-tested with injected success/failure; full native opening and final app restart checks follow in S8. No remote URL status verification, model calls or Devin writes.
