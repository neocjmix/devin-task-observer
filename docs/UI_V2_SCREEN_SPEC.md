# Observer UI v2 — screen and interaction specification
Companion to [roadmap](UI_V2_ROADMAP.md). Design target is NSPopover, not implemented. All names/numbers below are synthetic fixtures. No browser/Electron requirement; Python/PyObjC native views or NSPopover+WKWebView after spike. Current NSMenu remains supported.

## Global contract
- Status bar: neutral icon normally, attention only for real collector/data failure. Click opens popover; no model calls.
- Design reference: width 420pt, height 600pt; scroll list/detail, adapt to available screen. macOS native typography/system colors, 14–16pt primary, 11–12pt secondary, 8–12pt gaps, 12pt card radius. Light/dark, reduced motion, keyboard and VoiceOver. This is a proposed design scale, not a macOS limit.
- Header: “Task Observer” at left, “관측 정상” or “수집 실패” at right. Failure reveals last successful collection time and a diagnostic disclosure. Do not conflate collector health with agent progress.
- Navigation: home ↔ task detail ↔ session detail, back preserves filter/scroll/selection. External links open with OS handler, not in embedded webview. Closing and reopening should preserve useful navigation state without stale data masquerading as current.
- Do not repeat “unverified session report” badges. Item-level source/time in summary and evidence/history in disclosure. Actual failure, last_good replacement and unconfirmed candidate association must remain distinguishable.

## Screen H: task home
Vertical stack, 12pt padding:
1. Header.
2. Three compact summary cells: “활성 태스크 5”, “개입 필요 1”, “새 보고 3”. Counts derive from real data; if no read cursor, do not fake “새 보고”.
3. Segmented filter: “활성 | 개입 필요 | 새 보고 | 보관”.
4. Scrollable task list sorted explicit intervention first, then new report, then other tasks. No unsupported AI-generated urgency ranking.
5. Footer “마지막 성공 수집 · HH:mm” and settings/diagnostic entry.

Task row (min ~76pt, variable for wrap): first line status icon + title, right side observation-off icon only if disabled. Second line one most useful short reported situation/intervention. Third line subdued “Devin #12 · 12분 전” (last changing session/time) plus short status if needed. One click opens detail. Do not show token, every URL, revision, evidence, or repeated caveat here. Multiple sessions on task must not make task row look like session list.
Sample fixture:
- 인증 갱신: intervention “세션 만료 정책 결정 필요”, Devin #12 12분 전.
- 태스크 자동추적: “구현 완료 보고 · 검증 확인 필요”, Devin #07 25분 전.
- Figma Bridge: “선택 노드 전달 경로 개선”, Devin #18 5분 전.
- 데이터 이관: “실사용 데이터 이관 대기”, 관측 꺼짐.
- 이전 파서 검증: 완료/보관 필터에만 표시.

## Screen T: task detail
Top: back, task title, overflow actions. Compact reported progress + last report time (not collector timestamp).
Vertical priority:
A. “현재 상황” single prominent text panel; source/time short.
B. “내가 할 일” only when explicitly reported intervention. Show precise decision/request. “다음 행동” separately (agent's next action); neither inferred from blocker. “복귀 지점” direct link to relevant session/resource if available.
C. “작업 리소스” grouped collapsible categories: 이슈, 문서·위키, 환경, 코드(저장소·브랜치), PR, 소통(Slack), 산출물(Markdown), 기타. Each entry icon, concise label, external-open icon; branch may copy. Distinguish source docs from living deliverables. Candidates get short “후보” mark, not a blanket warning. Multiple entries/category, no single forced URL slot.
D. “추적 항목 N개”: each card title + status, value (1–3 lines with expand), footer last updated session and absolute/relative time. Disclosure: full value, evidence references, pinned flag, history, item ID/revision diagnostics. “고정” is protection, not proof. Free key/value items remain; recommended categories (not mandatory): 현재 상황, 사용자 개입 필요, 블로커, 다음 행동, 복귀 지점, 주요 결정, 검증 결과. Multiple blockers/decisions/results allowed.
E. “연결 세션 N개”: session name, auto-report on/off, last activity, last report, direct session detail/open action. Multiple sessions per task.
F. “메모·이력”: user notes preserved; editable only through validated existing/new safe write API. History by time with source/old/new values.
G. “태스크 관리”: auto-observation toggle, complete/archive, restore. Place destructive/lifecycle actions in overflow or bottom, not primary action card.
Sample: auth task situation “통합 테스트 진행 중”, intervention “정책 A/B 결정”, blocker “테스트 계정 권한 부족”, next “권한 확보 후 재실행”, resume Devin #12, resource PR #142.

## Screen S: session detail
Title session name + client type if known. Rows: “자동보고 켜짐/꺼짐”, “최근 활동”, “최근 보고”, “작업 공간”, “연결 태스크 N개”. These are separate facts; enabled reporting does not imply fresh/accurate reports.
Tokens collapsible: total input, new input, cached input, output, measurement timestamp and source; input total includes cached input, never add them again. Missing metrics: “사용량 정보 없음”, not error.
“복귀 위치” from session-level locator; never duplicate per task. Primary “세션 열기” only for supported verified adapter, otherwise “ID 복사” or “Devin Desktop으로 이동”. A click must activate the existing owning client/window/tab only; never create, resume, steal ownership, send prompt, or execute agent. Failure message adjacent to action with copy fallback.

## Lifecycle state machine
Three distinct concepts: reported work progress (agent's free status), user-managed task lifecycle (active/completed-archived), and per-task observation setting (on/off).
Allowed: active/on, active/off, completed/off. Completed/on disallowed. On complete store previous observation preference; on restore recover it. A task's observation-off must not stop shared-session collection needed by another task. Data/history remain available while archived. Post-completion agent reports may be retained as history without auto-reactivation; exact transaction semantics must be defined/tested before implementation.
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

## Implementation reality
Current code: observer/menu.py NSMenu; observer/state_schema.py v1 (no task lifecycle/read cursor/resource collection/session locator/token metrics). docs/M3_PROGRESS.md and docs/slices/M3-S04.md document tested scope. UI design is NOT a claim that these new features exist. Never use synthetic “M3 합성 파서” strings as fixed product copy. Use [roadmap](UI_V2_ROADMAP.md) S0–S8 one per Codex run and report exact test evidence.
