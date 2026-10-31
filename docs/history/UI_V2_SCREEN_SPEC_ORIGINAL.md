# Observer UI v2 — screen and interaction specification

| 항목 | 내용 |
|---|---|
| 문서 유형 | 과거 계획·결정·검증 기록 |
| 상태 | 보존; 현재 지시나 미완료 목록이 아님 |
| 기준 | 정리 전 원문; 당시 날짜·커밋·검증 범위는 본문 참조 |
| 적용 범위 | 당시의 구현·실험에 한정 |

> 현재 구현은 [기준선](../BASELINE.md), 남은 작업은 [백로그](../BACKLOG.md)를 따른다. 본문의 현재·다음·미구현은 당시 시점의 표현이며, 완료나 미검증 판정을 소급 변경하지 않았다.

Companion to [roadmap](UI_V2_ROADMAP_ORIGINAL.md). As of 2026-10-31, S0–S8 implement a Python/PyObjC NSPopover with local HTML/JS in WKWebView. NSMenu remains an explicit fallback. Names/numbers below are illustrative synthetic fixtures. The specification is not evidence for untested platform capabilities; see [S8 acceptance](../slices/UI-V2-S8.md).

## Global contract
- Status bar: neutral icon normally, attention only for real collector/data failure. Click opens popover; no model calls.
- Design reference: width 420pt, height 600pt; scroll list/detail, adapt to available screen. macOS native typography/system colors, 14–16pt primary, 11–12pt secondary, 8–12pt gaps, 12pt card radius. Light/dark, reduced motion, keyboard and VoiceOver. This is a proposed design scale, not a macOS limit.
- Header: “Task Observer” at left, “관측 정상” or “수집 실패” at right. Failure reveals last successful collection time and a diagnostic disclosure. Do not conflate collector health with agent progress.
- Navigation: home ↔ task detail ↔ session detail, back preserves filter/scroll/selection. External links open with OS handler, not in embedded webview. Closing and reopening should preserve useful navigation state without stale data masquerading as current.
- Do not repeat “unverified session report” badges. Item-level source/time in summary and evidence/history in disclosure. Actual failure, last_good replacement and unconfirmed candidate association must remain distinguishable.

## Screen H: task home
Vertical stack, 12pt padding:
1. Header.
2. Compact counted filters: 활성, 확인 필요, 내가 할 일, 새 보고, 보관. Counts derive from stored state; unknown report/read positions remain unknown. Missing initial cursor means unread only when a valid report revision exists.
3. Search and filters share the same task list; 확인 필요 means data integrity issues, 내가 할 일 means explicit intervention.
4. Scrollable task list sorted explicit intervention first, then new report, then other tasks. No unsupported AI-generated urgency ranking.
5. Footer “마지막 성공 수집 · HH:mm”, 데이터 폴더 (configured Observer state root in Finder), diagnostic and quit controls.

Task row (min ~76pt, variable for wrap): first line status icon + title, right side observation-off icon only if disabled. Second line one most useful short reported situation/intervention. Third line subdued “Devin #12 · 12분 전” (last changing session/time) plus short status if needed. One click opens detail. Do not show token, every URL, revision, evidence, or repeated caveat here. Multiple sessions on task must not make task row look like session list.
Sample fixture:
- 인증 갱신: intervention “세션 만료 정책 결정 필요”, Devin #12 12분 전.
- 태스크 자동추적: “구현 완료 보고 · 검증 확인 필요”, Devin #07 25분 전.
- Figma Bridge: “선택 노드 전달 경로 개선”, Devin #18 5분 전.
- 데이터 이관: “실사용 데이터 이관 대기”, 관측 꺼짐.
- 이전 파서 검증: 완료/보관 필터에만 표시.

## Screen T: task detail
Fixed top: back, task title, then compact task controls (automatic reflection on/off and complete/restore), outside the scrolling body. No management heading or explanatory paragraph. Compact reported progress + last report time remain in the body (not collector timestamp).
Vertical priority:
A. “현재 상황” single prominent text panel; source/time short.
B. “내가 할 일” only when explicitly reported intervention. Show precise decision/request. “다음 행동” separately (agent's next action); neither inferred from blocker. “복귀 지점” direct link to relevant session/resource if available.
C. “작업 리소스” grouped collapsible categories: 이슈, 문서·위키, 환경, 코드(저장소·브랜치), PR, 소통(Slack), 산출물(Markdown), 기타. Each entry icon, concise label, external-open icon; branch may copy. Distinguish source docs from living deliverables. Candidates get short “후보” mark, not a blanket warning. Multiple entries/category, no single forced URL slot.
D. “추적 항목 N개”: each card title + status, value (1–3 lines with expand), footer last updated session and absolute/relative time. Disclosure: full value, evidence references, pinned flag, history, item ID/revision diagnostics. “고정” is protection, not proof. Free key/value items remain; recommended categories (not mandatory): 현재 상황, 사용자 개입 필요, 블로커, 다음 행동, 복귀 지점, 주요 결정, 검증 결과. Multiple blockers/decisions/results allowed.
E. “연결 세션 N개”: session name, auto-report on/off, last activity, last report, direct session detail/open action. Multiple sessions per task.
F. “메모·이력”: user notes preserved; editable only through validated existing/new safe write API. History by time with source/old/new values.
G. Task management actions are in the fixed top controls, not at the bottom: automatic reflection on/off and complete/restore. Existing revision checks, archived observation preference and error handling remain. Hide task controls in session detail. A separate “상태 파일 보기” button remains at the bottom and reveals this task’s state.json in Finder even when the JSON is unhealthy; navigation does not mutate or recover state.
Sample: auth task situation “통합 테스트 진행 중”, intervention “정책 A/B 결정”, blocker “테스트 계정 권한 부족”, next “권한 확보 후 재실행”, resume Devin #12, resource PR #142.

## Screen S: session detail
Title session name + client type if known. Rows: “자동보고 켜짐/꺼짐”, “최근 활동”, “최근 보고”, “작업 공간”, “연결 태스크 N개”. These are separate facts; enabled reporting does not imply fresh/accurate reports.
Tokens collapsible: total input, new input, cached input, output, measurement timestamp and source; input total includes cached input, never add them again. Missing metrics: “사용량 정보 없음”, not error.
“복귀 위치” from session-level locator; never duplicate per task. Primary “세션 열기” only for supported verified adapter, otherwise “ID 복사” or “Devin Desktop으로 이동”. A click must activate the existing owning client/window/tab only; never create, resume, steal ownership, send prompt, or execute agent. Failure message adjacent to action with copy fallback.

## Lifecycle state machine
Three distinct concepts: reported work progress (agent's free status), user-managed task lifecycle (active/completed-archived), and per-task observation setting (on/off).
Allowed: active/on, active/off, completed/off. Completed/on disallowed. On complete store previous observation preference; on restore recover it. A task's observation-off must not stop shared-session collection needed by another task. Data/history remain available while archived. New reports while completed or observation-off are rejected inside the transaction without consuming their report ID; a retry of an already accepted receipt remains idempotent. Restore does not fabricate reports or reactivate another task.
Do not conflate item.archived with task completion. No deletion on archive.

## Errors and integrity
System collection failure: header alert, last successful collection time, retry/diagnostic affordance; do not claim data current. Task file corruption: mark affected task, show last_good with “마지막 정상 기록” and its timestamp, or “표시할 정상 기록 없음”. Other tasks remain usable. Action failure: “링크를 열지 못했습니다” / “세션을 열지 못했습니다”, with copy fallback. Missing optional metrics/resources are not failures. Read while collector updates; never jump scroll/reset edited text.

## Interaction acceptance fixtures
1. Five active tasks, one intervention, three changed, one observation-off; archived one filtered out by default.
2. One task linked to sessions A/B, another task also linked to A. Items changed by A and B at different times.
3. Agent reports “completed” but user task remains active; another item pinned and a user note persists.
4. Candidate PR URL is not shown as confirmed; registered URL can be opened, failed open gives copy.
5. Session auto-report ON but last report hours old; activity 2 minutes old, no “report failure” claim.
6. Collector fails while last_good exists; separate corrupted task with no last_good.
7. Archived task restored with prior observation OFF; shared session observation continues for another task.
8. Session token metrics have cache subset and duplicates; session used by two tasks; no double count.
9. Session locator stale/unsupported: no automatic resume, ID copy works.
10. Duplicate task/item titles, long Korean/English values, empty states, inaccessible URL, dark mode, keyboard focus.

## Implemented scope and remaining boundaries

- `observer.desktop` is the native entry, `observer.popover` the host, `observer/ui_v2/` the local frontend. No HTTP server, browser window, Electron, network content or model call is needed.
- Pure projections preserve current vs last-good state, item source/time/evidence, pins, notes, candidates and all task–session edges. UI writes use the existing optimistic transactions and validate the displayed revision.
- Optional v1 fields add item role/intervention, resources, task lifecycle/observation and report revision. `ui/read-cursors.json` has a separate validated contract and does not modify task revision. Old binaries cannot validate records populated with these new optional fields; do not silently roll back the reader.
- Session metrics read the verified local SQLite v17 format. Input totals include cache input once. These are retained-message metrics, potentially including inherited fork history; not billing or lifetime totals, and never apportioned to linked tasks.
- Exact owning session tab activation is unsupported. The UI copies the linked session ID or activates one already-running Devin app. No session URL, load, resume, ACP or prompt is issued.
- Keyboard, AX labels, light/dark, live refresh, conflicts, errors and restart were checked on the actual native host with synthetic data. VoiceOver speech and 5–10 simultaneous live Devin agent executions were not tested. Eight synthetic source sessions and a separate read-only check of four real personal synthetic Devin sessions are distinct evidence.
- Detailed per-slice results: [roadmap](UI_V2_ROADMAP_ORIGINAL.md), [S8 acceptance](../slices/UI-V2-S8.md). Earlier S0 claims describe that historical baseline, not current capability.
