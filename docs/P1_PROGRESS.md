# P1: Local adapter and checkpoints (in progress)

Status: **not complete**. This is a narrow prototype for the observed Devin Local schema; it does not yet implement the full normalized event contract or the downstream product.

## Implemented

- `observer.devin_local.read_sessions` opens the source SQLite database using `mode=ro` and `query_only`, in a short read transaction, with a bounded lock timeout. It reads only explicitly selected workspace paths and requires the observed schema migration version 17. It fails closed on an unrecognized schema, malformed JSON, missing message IDs or an invalid active chain. It reads node metadata and hashes JSON payloads without persisting message bodies.
- The local SQLite store records session metadata, node identities, hashes and immutable added/modified/missing/restored observations. Observation changes and checkpoints commit together. Missing nodes, identity changes, and conflicting duplicate message IDs mark a session stale; data and prior observations are retained. A missing session is marked stale. Staleness is deliberately sticky; an explicit recovery workflow remains to be designed.
- A manual refresh and optional polling CLI use an explicit workspace allowlist. A local file lock serializes CLI refreshes; an unavailable source leaves checkpoints unchanged and marks known sessions stale. No hooks, network requests, model calls or Devin session control are involved. SQLite WAL readers may use shared-memory coordination and briefly hold read locks.
- The observer store is rejected if it is the source database or located in its directory. New store and lock files are created with restrictive permissions. Files contain personal session metadata and hashes; keep them local and out of Git.

## Actual validation

- A live run against **three newly created synthetic Desktop sessions** stored 201 node observations; immediate replay stored zero more. During a subsequent synthetic session update, a refresh recorded three new transitions and its immediate replay recorded zero; the Desktop session then completed its response. This is a limited sample, not proof of lossless collection across all timing scenarios.
- In a separate Desktop test the agent read only the synthetic `README.txt` and returned its first word. The Local store contained an assistant node with a `tool_calls` list and a `role: tool` result node with `tool_call_id`. These shapes were observed, but the collector does not yet normalize or correlate them.
- After that tool-call test, a fresh CLI run observed 210 nodes in three sessions, and an immediate replay recorded zero transitions. One session was marked stale because two assistant nodes had the same embedded message ID but different JSON payloads. In this sample the differing top-level field was `metadata`, specifically an added `extensions.chisel/tool_call_content`. Treating the divergence as ambiguous remains intentional until revision semantics are validated. This is a known blocker for P1 acceptance.
- The current synthetic test suite passes 17 tests. It covers append, same-length edit, replay, missing/restored nodes, reorder, disappearing sessions, duplicate identifiers and row-ID rewrite, malformed input, locked source, unknown source version, preventing writes to the source directory, failed checkpoint updates, and a process killed during a checkpoint transaction followed by replay. Probe tests cover snapshots, symlinks, missing sources and malformed chains. Tests do not use actual personal transcripts.

## Remaining before P1 acceptance

- Implement and validate normalized user/assistant/tool/plan events with sufficient evidence to reconstruct intended user input without confusing injected project context, internal user-role nodes, duplicate nodes, or compaction summaries. The current observations are **node-level metadata only**, not a usable transcript.
- Test changing and locked source databases during collection, source replacement/rewrite, concurrent refreshes, missing source recovery, and repeated compaction against both fixtures and live controlled sessions. Surface and resolve stale states; verify all atomicity and replay guarantees.
- Investigate WAL shared-memory effects and checkpoint delays under sustained load and observer crash. The current short live tests do not establish zero interference.
- Add version and source identity handling beyond this one observed migration. Other Desktop agent types and versions remain unverified. Do not infer universal support.
- Only after P1 acceptance proceed with Task Registry, task views, dashboard and optional semantic reconciliation. None is implemented here.
