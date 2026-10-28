# P3: Persistent views and local dashboard (prototype)

Status: **partially implemented and tested; not full acceptance**. The dashboard is available only on `127.0.0.1` and uses no LLM or external services. The active experimental instance uses a disposable synthetic workspace and a temporary test store.

## Implemented

- Task-specific, persistent views with stable section/item IDs, custom section names and order, user-authored items, pinning, explicit status changes, and a revision checked on writes. Prior item versions are retained. There is no automatic item deletion, whole-view rewrite, silent merge or AI update.
- Evidence can be attached only to a linked task/session and an already observed node/hash. Retrieval checks the hash again; unavailable or changed evidence fails rather than returning another version. The raw source field may contain injected context and the UI says so.
- A simple localhost dashboard creates and updates tasks, links sessions, adds manual resources (external status explicitly unknown), creates/reorders/renames sections, edits and pins items, attaches evidence and reads it on demand. It shows observed and unclassified sessions and provides paged event metadata with a warning that the order is not yet a verified transcript order. Collection state is not presented as the agent's execution state. Polling runs locally and a manual refresh is available.
- HTTP requests enforce loopback binding, Host checks, an in-memory per-process token, Origin/content-type checks for writes, no CORS, and no request logging. Source access is limited to explicitly configured personal workspaces. This is not an independent security audit.

## Actual validation

The browser-to-API-to-SQLite flow was exercised on actual **synthetic** Devin Desktop sessions. In the dashboard, two tasks and two sessions were linked many-to-many; a manual note was saved, pinned and linked to a hash-checked synthetic source response; a custom section was added and reordered; a PR example URL displayed as unverified. After browser and server restart, the saved note, pin, evidence and section order remained. A draft was retained in the page while the section order changed, and the user was offered an explicit reapply action for the new view revision.

The complete test suite passes 42 synthetic tests, including registry and view preservation, revision conflict, evidence scoping, section reorder, HTTP request guards, workspace isolation, event pagination, and a full API task/link/view/evidence flow. The browser was also used to unlink and restore a synthetic session and to edit, archive and restore a synthetic resource. Previously attached evidence remained present. Browser console had no errors during the earlier checked flow. No real company data or private transcripts were used.

## Remaining

- P1 still needs a verified complete ordered event timeline, correct handling of all source variants and stable recovery of structural ambiguity. The dashboard exposes the current event index with explicit ordering and freshness limitations; it does not claim a complete transcript.
- Editing contribution metadata, section/item move workflows beyond section reorder, search, conflict/claim management, per-task attention queue and packaged startup are not implemented. Unsaved browser drafts are retained during in-page rerenders but not across a browser reload or crash.
- Evidence bodies are read on demand from Devin and are not retained by the observer. If Devin removes or changes them, their old content is unavailable and lookup reports an error; hashes and references remain. This is a material retention limitation.
- Only Desktop 3.10.67 / Local schema migration 17 has been tested. Cloud, Cascade, other ACP agents, scale and long-running behavior remain unverified. No P4 semantic reconciler is implemented; basic operation makes zero LLM calls.
