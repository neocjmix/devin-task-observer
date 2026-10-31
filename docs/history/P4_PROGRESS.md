# P4: Optional local semantic suggestions (experimental)

| 항목 | 내용 |
|---|---|
| 문서 유형 | 과거 계획·결정·검증 기록 |
| 상태 | 보존; 현재 지시나 미완료 목록이 아님 |
| 기준 | 정리 전 원문; 당시 날짜·커밋·검증 범위는 본문 참조 |
| 적용 범위 | 당시의 구현·실험에 한정 |

> 현재 구현은 [기준선](../BASELINE.md), 남은 작업은 [백로그](../BACKLOG.md)를 따른다. 본문의 현재·다음·미구현은 당시 시점의 표현이며, 완료나 미검증 판정을 소급 변경하지 않았다.

Historical milestone record. See [M2 progress](M2_PROGRESS.md) for the current semantic pipeline and live adapter results.

Status: **partial experiment, not full semantic reconciliation**. The normal dashboard starts with analysis disabled. An explicit server flag enables a manual action; collection, polling and ordinary dashboard views never invoke a model. No network endpoint or model can be supplied by a client. The experimental implementation connects only to `127.0.0.1:11434` and requests the fixed `llama3.2:latest` model after checking local GGUF metadata. Its operation assumes the local Ollama service honestly serves its reported local model; it is not a network security audit of that separate service.

## Behavior and safeguards

- A human selects one present, classified user/assistant node from a linked session. The source workspace, session, node and stored hash are checked; no entire transcript or full history is passed. The text can still contain automatically injected context. Text longer than 600 UTF-8 bytes is rejected rather than truncated. Only the source content string is sent, not the JSON metadata. Results may be inaccurate or follow misleading source instructions; they are untrusted suggestions, not verified facts.
- Each successful or empty task/session/message ID/input-text hash/model/prompt-version result is reused, including when raw node metadata changes but the exact text supplied to the model does not. Raw node hashes are still retained as evidence. A cached proposal keeps its original source reference, so acceptance is blocked if that original version is no longer available. Legacy runs acquire an input key only when the same raw node/hash can be re-read. A failed or safely identified interrupted run can be explicitly retried only from its original node and raw hash, with a new budget reservation and an attempt record; retries are never automatic. This single-node cache intentionally does not incorporate a view revision: current view contents are not passed to this experiment. It does not provide multi-event reconciliation, conflict detection, or automatic task updates.
- Local requests specify 2,048 context tokens and at most 256 output tokens. Before a call, 2,304 tokens are reserved in a transactional UTC-day ledger with a fixed daily cap of 4,608 reserved tokens per observer database; failed, interrupted, or pending runs retain the reservation. Separate observer databases have separate budgets. Actual prompt/output token counts are recorded only when returned and validated. The cap assumes the local model server respects its requested limits. The ledger is local; no paid cloud API is involved. Analysis requests hold an exclusive per-store lock; after an observer process is killed, a user can explicitly retry an interrupted run after the lock is released. Pending runs created before lock markers existed cannot be assumed interrupted and require investigation.
- The model may propose one new note, or an empty result. Output shape, size, source identity and budget are checked. A person must separately choose a section and explicitly accept the proposal; the insertion, evidence reference, item history, proposal status and view revision commit atomically. Before acceptance the current source node and hash are checked again; changed or unavailable evidence blocks the write. Existing notes and pins are not overwritten. A rejected proposal remains recorded; provenance stays visible on accepted notes. Generated text is stored in the private observer database and can repeat sensitive source content, even though raw transcript bodies are not stored.
- The fixed local model, small input limit and conservative two-reservation daily budget are deliberately narrow experimental constraints, not a general model configuration or production budget system.

## Observed test

On the personal Mac, the already-installed Ollama 0.15.2 and local `llama3.2:latest` were used with one **synthetic** Devin Desktop message. The browser requested a suggestion, received it without an automatic view change, and explicitly accepted it into the synthetic task. Its source reference and `local_model` origin remained visible after a server/browser reload. The model reported 79 prompt tokens and 21 output tokens; 2,304 tokens were conservatively reserved. Repeating the same request returned the cached outcome without consuming a second reservation. This single example demonstrates the plumbing, not the factual quality or safety of model interpretation.

After adding the per-store lock and recovery markers, a separate temporary synthetic SQLite source was analyzed with the real local model. It produced a proposal, a test acceptance created one evidence-linked item, and the local model reported 78 prompt and 16 output tokens. The lock marker was recorded. This validates that particular post-change execution, not real-user data or model quality.

For the two actual synthetic Alpha assistant nodes differing only in `tpot_ms`, a local model analyzed one and the second reused the same result; the test store contained only one model reservation. This validates that specific metadata-only duplicate, not all possible source variants.

The synthetic regression suite covers default-off behavior, no calls on cache hit, input and output validation, hard reservation exhaustion including concurrent requests, failure accounting and an explicit retry using a simulated model, killing an analysis process and retrying after its lock is released, rejecting unsafe legacy pending runs, rejection, stale view revisions, retained human edits and pins, link scoping, and evidence-linked explicit acceptance. Tests with a simulated model do not prove real model behavior.

## Remaining

No automatic selection of relevant deltas, multi-event interpretation, affected-item context, non-destructive status proposals, conflicts, configurable reviewed models/budgets, automatic retry, or model quality evaluation exists. Recovery of interrupted runs depends on the per-store lock and the local filesystem honoring its locking semantics. The cache identifies the model by tag rather than content digest, so replacing the model behind that tag does not invalidate an existing result. P1's transcript and ordering limits still apply. Do not use this experiment to infer a verified task status.

Ollama's official [generate API](https://docs.ollama.com/api/generate) documents local generation and returned token counts; its [structured output documentation](https://docs.ollama.com/capabilities/structured-outputs) describes schema-formatted responses. Actual results are nevertheless validated locally and treated as untrusted.


## M1 scope note

M1 does not extend this experiment, add providers, alter model choice or enable analysis. Resource discovery, plan projection, live updates and runtime recovery are deterministic and make no LLM calls. The configured M1 development instance has analysis disabled; the M1 work is documented separately in [M1 progress](M1_PROGRESS.md).


## M2 follow-up (2026-10-29)

The statements above describe the earlier milestone. [M2](M2_PROGRESS.md) now implements separate task/session semantic cursors and a bounded multi-session patch pipeline through SemanticModelAdapter, with real personal Codex validation and a partially live-verified ACP transport. The original collector, many-to-many links, human View, review cursors and default-off legacy Ollama experiment are preserved. Target ACP authentication/streaming and real-work usefulness remain open; see M2 for current evidence and limits.
