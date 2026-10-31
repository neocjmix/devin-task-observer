# Validation, Security, and Handoff

| 항목 | 내용 |
|---|---|
| 문서 유형 | 과거 계획·결정·검증 기록 |
| 상태 | 보존; 현재 지시나 미완료 목록이 아님 |
| 기준 | 정리 전 원문; 당시 날짜·커밋·검증 범위는 본문 참조 |
| 적용 범위 | 당시의 구현·실험에 한정 |

> 현재 구현은 [기준선](../BASELINE.md), 남은 작업은 [백로그](../BACKLOG.md)를 따른다. 본문의 현재·다음·미구현은 당시 시점의 표현이며, 완료나 미검증 판정을 소급 변경하지 않았다.

## Current validation scope — M3 (2026-10-30)

현재 기준선은 [M3-S01](../slices/M3-S01.md)의 파일 원본·보고 CLI·낙관적 갱신·복구이며,
[M3-S02](../slices/M3-S02.md)의 실제 세션 연결·메뉴 막대·지속 수집과
[M3-S03](../slices/M3-S03.md)의 기존 데이터 보존 이관·롤백을 순서대로 검증한다.
기본 회귀, 합성 파일·CLI·프로세스 테스트, 실제 Devin Desktop·macOS 확인은 증거를 나누어 기록한다.
기존 P0~M2 테스트 통과를 새 경로의 증거로 대신하지 않는다.

- 합성 검증은 다중 세션 보고·충돌·핀·이력, 설치/제거 시 기존 훅 보존, 비공개 대화 필드 미저장,
  수집 실패 후 재시도·재시작, 메뉴의 last-good 표시, 이관 전후 데이터 보존과 롤백 거부 조건을 포함한다.
- 실제 새 개인 합성 Devin Desktop/CLI 세션에서 훅 식별·스킬·능동보고를 확인했다. 별도 보고 요청 없는 후속 보고, 두 세션의 충돌 재판단, 핀·메모 보호도 검증했다. 실제 긴 대화 압축 후 이행이나 보고의 완전한 정확성을 보증하지 않는다. 최종 결과와 실패 사례는 [M3 진행 기록](M3_PROGRESS.md), 메뉴 실행 증거는 S02를 따른다.
- 스킬은 Devin의 정상 도구 승인 절차를 따른다. 전역 훅이나 blanket 권한을 추가하지 않으며,
  훅 출력은 문맥 전달·진단용이다. 훅이 원래 작업의 종료를 막거나 대신 LLM 보고를 생성하지 않는다.
- 이관 검증은 합성 자료와 명시적으로 선택한 개인 Observer 자료에 한정한다. 원본 Devin 저장소·기존 Observer DB를
  덮어쓰지 않고, 새 파일 저장소·백업·복원 경로의 실제 검사 결과를 S03에 남긴다.

핵심 수용 기준은 사용자가 태스크를 연결하고 보고를 켠 뒤 메뉴 막대에서 최근 보고값·작성 세션·시각·근거를
확인할 수 있는지다. 세션의 의미 판단과 보고는 best effort이며, ON이나 훅 활동만으로 값이 최신·정확하다고
보증하지 않는다. 단위 테스트 수, CLI 성공, 실제 스킬 로드 각각의 증거 범위를 넘어 완료를 선언하지 않는다.

[슬라이스 목록](../SLICES.md)이 현재 우선순위와 문서 범위를 정한다. **아래 P0~M2 게이트·예산·next 지시는 보존 중인 기존 런타임의 역사적 계약이다.** 새 방향의 충실도·비용 사전 증명을 착수 조건으로 되살리지 않는다.

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

The prototype now implements [M1 — Useful Observer](M1_PROGRESS.md) for the observed Local configuration. The current lightweight entry point and explicit login-startup choice are in the [README](LEGACY_RUNTIME.md#legacy-local-entry-point). Actual Desktop/browser evidence and fixture-only guarantees are listed separately in the M1 acceptance table. Full transcript reconstruction, external resource status, production scale and other provider support remain outside the validated delivery.


## Product-completion gate: M2 Semantic Task Tracking

**M1 is not the finished product.** The original user problem is repeated manual status reconstruction across 10+ sessions. After linking sessions once, the user should ordinarily *read* a continuously maintained task state, not transcribe findings or approve every ordinary update.

M2 must demonstrate: two sessions contributing to one task; incremental extraction of natural-language progress, blockers, decisions, open questions and next actions; persistent task-specific View structure; evidence-linked bounded patching; safe auto-apply to machine-owned items; preserved human edits; explicit conflicts and freshness; replay idempotence; zero-token unchanged reads/refresh; budget exhaustion fallback.

Implement a single complete vertical slice first, with synthetic labeled scenarios and real personal Devin Desktop smoke tests. Manual semantic refresh is sufficient initially. No complete transcript engine, agent-side hook contract, ACP control, cloud services or redesign required. Distinguish real Devin validation from synthetic fixtures and do not claim full product completion from test count alone.

Acceptance metrics: percentage of status questions answered without reopening Devin, unsupported-claim and omission rates against labeled scenarios, repeated-update duplicate rate, time to find blockers/next action, semantic tokens per useful update and number of user approvals. Record unresolved gaps and semantic staleness.

The next critical path after M1 is **M2**, before optional polish and broad compatibility work. The experimental P4 single-message local-model note proposal must not be mistaken for multi-session semantic reconciliation.


## M2 adapter verification and environment boundary

Primary development/testing adapter: **Codex CLI** in a personal environment. Target internal adapter: **Devin ACP**, using only newly created isolated semantic-analysis sessions; existing task sessions are read-only. The earlier Ollama proof of concept is not the target inference path.

Acceptance tests: mock adapter parity; Codex CLI real noninteractive execution and schema validation; subprocess timeout, nonzero exit, invalid JSON, interrupted run and duplicate replay; ACP capability negotiation, new-session-only invariant, streamed output assembly, tool-permission denial, disconnect, cancellation, auth failure, usage-unknown and concurrent requests. Distinguish mocked ACP tests from real Devin ACP integration; target environment signoff requires live verification. No hidden fallback to an external provider.

Company session content must never be sent to personal Codex, public API endpoints or personal GitHub. Internal deployment must explicitly select the approved Devin ACP adapter; missing ACP access fails closed while deterministic dashboard remains available. Document operational token/call limits and any unmetered provider behavior.


## M2 implementation record (2026-10-29)

The bounded manual semantic vertical slice is implemented on the existing branch. Personal Codex exec was run with two synthetic session sources and with actual synthetic Devin Desktop sessions through the browser. Safe machine-owned patches, separate semantic cursors, human preservation, usage/budgets, retry and failure isolation are implemented. After an initial auth failure, personal browser reauthentication succeeded and actual Devin ACP new-session streaming plus the two-session patch integration test passed. Dangerous tool denial is mock-tested and internal organizational policy/isolation still require target-environment signoff. This does not close the real-work dogfood/product-quality gate. See [M2 implementation, measured usage, acceptance evidence and limitations](M2_PROGRESS.md). The contracts above remain the baseline for the retained M2 runtime; M3 follows the newer contracts linked at the top.
