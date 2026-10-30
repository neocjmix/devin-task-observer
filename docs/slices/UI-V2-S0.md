# UI-V2-S0 — 읽기 전용 데이터 투영과 합성 fixture

2026-10-31. 범위는 [UI V2 로드맵](../UI_V2_ROADMAP.md)의 **S0만**이다.
시작 기준선은 `1fb3929489c16e53c974a3dde1958366b0b43ec6`
(`p0/desktop-probe`, Draft PR #1)이다. 로컬 `7a1eeca`에서 원격의 UI 설계 문서
두 커밋을 fast-forward한 뒤 시작했다. 기존 NSMenu·저장 스키마·트랜잭션·보고·수집 코드는 바꾸지 않았다.

## 현재 구현과 화면 요구의 차이

| 화면 요구 | 현재 근거 / S0 처리 | 남은 구현 |
|---|---|---|
| 태스크 목록·상세·세션 상세 | v1 task/item/contribution을 ID로 정규화; 같은 제목도 별개 | S1 팝오버와 탐색, S2 상세 화면 |
| 항목별 값·출처·시각·핀·근거 | 임의 JSON 값, 자유 상태, 작성/마지막 변경 actor, 시각, revision, report ID를 원문 보존 | UI에서 요약/펼침과 근거 이동 |
| 상황·사용자 개입·다음 행동 | v1에는 의미 역할 필드가 없음. 제목이나 `blocked`로 추론하지 않음 | S2에서 명시적 역할 연결 계약 결정; 현재는 일반 추적 항목 |
| 사용자 메모·View·이관 기록 | notes, view.markdown, legacy 전체 보존; 읽는 동안 변경 없음 | 편집은 기존 안전한 API 사용, 새 UI는 아직 없음 |
| 리소스 | 세션의 관측 mention을 후보로 묶고 모든 서로 다른 근거 보존 | S3 등록/수정/열기. 현재 bridge가 제외하는 workspace-only Git 사실은 이번에 추가하지 않음 |
| 완료·보관·관측 ON/OFF | 현재 task에는 해당 필드가 없음. item.archived·보고 ON/OFF와 혼동하지 않음 | S4 저장/동작 계약 및 UI |
| 보고 ON/OFF·최근 보고·활동 | ON/OFF와 최근 보고는 태스크–세션 연결별; 훅 활동과 관측 시각은 별개 | S5 세션 화면; 오래된 보고를 자동으로 보고 실패로 판단하지 않음 |
| 토큰·세션 활성화 | 현재 입력 계약에는 없음 | S5 실측 출처/중복 처리, S6 locator 실증; 0토큰 사용량을 꾸며 표시하지 않음 |
| 새 보고 개수·읽음·개입 우선 정렬 | 읽음 cursor·명시적 개입 역할이 없음 | S7 및 역할 계약. 미지원이지 0건이 아님 |
| 오류와 마지막 정상 기록 | task health/error/fallback와 collector 오류·마지막 성공 시각을 별도로 보존 | S1/S2에서 두 오류 영역을 명확히 표시 |

`legacy.lifecycle/resources/review_cursors`는 **이관 당시 보존 기록**이다.
현재 기능으로 승격하지 않는다. 설계 fixture의 “5개 active, 1개 개입, 3개 새 보고”
조건은 현행 저장 모델로 검증할 수 없으며 이번 완료 주장에 포함하지 않는다.

## 투영 계약

구현: [`observer/ui_projection.py`](../../observer/ui_projection.py).
Python 표준 라이브러리와 기존 v1 validator/resource identity만 사용한다.
새 서버·CLI 명령·네이티브 뷰·추가 의존성은 없다. 현재 NSMenu에 아직 연결하지 않았다.

```python
from observer.ui_projection import project

# 모두 이미 획득한 JSON 호환 snapshot이다. project 안에서는 조회하지 않는다.
view = project(
    task_snapshots,                # CooperativeState.list()의 raw envelopes
    observations=observation_snapshot,  # state_observations.observations() 반환값 또는 None
    session_activity=activity_by_id,     # session_id -> integration.session_activity() 반환값
    runtime=runtime_snapshot,           # CollectorRuntime.snapshot() 등 또는 None
    state_errors=acquisition_errors,    # 획득 실패를 빈 정상 목록으로 숨기지 않는다
)
```

출력 `projection_version=1`은 **투영 포맷 버전**이며 저장 스키마 버전이 아니다.

- `tasks[task_id]`: health/errors/projection_errors, display_origin,
  displayed_revision/updated_at, metadata, item_ids/session_ids/resource_candidate_ids.
  metadata는 원본 task에서 items/contributions만 분리한 값이다. notes/view/legacy도 여기 있다.
- `items[task_id][item_id]`: 원본 항목 전체. 배열의 순서는 task.item_ids에 유지한다.
  제목이나 다른 태스크의 같은 item_id로 합치지 않는다. pin으로 updated_by가 human이 되어도
  값이 인간 작성/검증되었다고 해석하지 않는다.
- `contributions[task_id][session_id]`: 원본 연결 전체. 보고 설정·최근 보고 시각을 세션 전역 값으로 만들지 않는다.
  last_good 태스크의 연결은 그 정상 기록 당시 연결이다. 연결을 사용할 때도 task.display_origin을 따른다.
- `sessions[session_id]`: task_ids 역참조, 별도의 observation/activity,
  plan_snapshots/resource_candidate_ids. 관측되지 않은 연결 세션은 observation=None이며 사라지지 않는다.
  계획은 기존 `ordering=not_inferred`와 근거를 유지하고 임의 최신 계획으로 합치지 않는다.
- `resource_candidates[id]`: 기존 discovery.resource_key로 동일 리소스를 묶는다.
  branch·bare ticket·bare commit은 workspace별, 기존에 인식하는 GitHub URL은 기존 정규화 규칙을 따른다.
  동일 근거의 완전 중복만 제거하고 session/workspace/source_ref/node_id/source_hash/verification/원문 값을 보존한다.
  related_task_ids는 **발견 세션에 연결된 태스크**이지 확인된 소속이 아니다.
  association=candidate, external_status=unknown이다. 관측 bridge가 제공하지 않는 발견 시각을 만들어내지 않는다.
- `health`: state_errors, runtime, observations의 collector/warnings/unobserved_session_ids 등 원본 진단.
  collector/runtime의 서로 다른 시점 값을 하나로 덮어쓰지 않는다. 자료 없음은 None이다.
- `capabilities`: task_lifecycle/task_observation_toggle/task_read_cursor/typed_item_roles/
  registered_resources/session_tokens/session_locator 모두 false. 대응하는 가짜 상태·숫자를 생성하지 않는다.

오류 task는 `health != ok`이면 **state를 표시하지 않고** 검증된 last_good만 사용한다.
fallback도 유효하지 않거나 없으면 metadata=None, display_origin=unavailable이다.
다른 정상 태스크는 남는다. 입력이 정상이라고 주장해도 v1 검증과 task ID 확인을 다시 하며,
실패는 projection_errors에 남긴다. raw 외부 편집 내용은 표시 모델에 섞지 않는다.
입력 목록의 중복 task/session ID는 조용히 덮어쓰지 않고 ValueError로 거부한다.
history를 새로 읽거나 복구용 content_digest를 제공하지 않으므로 이 출력은 복구/쓰기 API의
입력으로 사용할 수 없다. S2 이력 탐색은 별도 구현이다.

## 읽기 전용과 트랜잭션 경계

`project()` 자체는 순수 함수다. 파일·DB·네트워크·프로세스·모델 호출, 상태 변경,
체크포인트·읽음 처리, 실시간 시계 조회가 없다. 입력과 출력은 깊은 복사로 분리된다.
같은 입력은 같은 결과이며, 사용자 문자열/JSON 값은 축약·번역하지 않는다.

단, **기존 snapshot 획득 전체가 파일시스템 무쓰기인 것은 아니다.**
FileStore 초기화는 디렉터리/락 파일을 만들 수 있고 read/list/history는 락 안에서
미완료 journal을 복구하여 state/history/receipt를 쓰거나 journal을 지울 수 있다.
session_activity 조회도 기존 FileStore/락 경로를 사용한다. 이 동작을 바꾸거나 우회하지 않았다.
raw state.json을 파일별로 직접 읽으면 다중 태스크 커밋 도중의 혼합 상태를 볼 수 있으므로
S1에서도 기존 일관성 있는 list 경계를 거쳐 투영해야 한다.

태스크 list 내의 일관성과 별개로, 파일 상태·관측 DB·훅 활동·runtime은 각각 획득된다.
이 전체가 하나의 원자적 snapshot이라고 주장하지 않는다. task 표시 상태의 시각은
task.updated_at이며 마지막 수집 성공·활동·보고 시각과 대체할 수 없다.
수집 실패 시 원본 observations의 오류와 이전 성공 시각을 그대로 보여야 한다.
시각 단위도 원본 계약 그대로다(task/hook ISO 문자열, 관측 DB는 epoch nanoseconds).
S1에서 JavaScript를 선택하면 큰 정수의 정밀도를 잃지 않도록 Python에서 표시 시각을
포맷하거나 문자열로 전송하는 경계를 마련해야 한다. 현재는 Python 내 투영만 제공한다.

## 합성 fixture와 검증

재사용 입력: [`tests/fixtures/ui_v2/s0.json`](../../tests/fixtures/ui_v2/s0.json).
사례/미지원 범위: [fixture README](../../tests/fixtures/ui_v2/README.md).
회사 데이터·개인 대화록·실제 세션·인증정보는 포함하지 않는다.

- 시작 회귀: `DEVIN_OBSERVER_GIT=/Library/Developer/CommandLineTools/usr/bin/git python3 -m unittest discover -s tests -q`
  → 273개 실행, 271 통과, 기존 실제 M2 모델 opt-in 2 skip, 실패 0 (46.855초).
- S0 단위 15개 + 통합 4개: `python3 -m unittest tests.test_ui_projection tests.test_ui_projection_integration -v`
  → 19개 모두 통과, 실패/skip 0 (0.287초).
- 최종 전체 회귀: 시작과 같은 명령 → 292개 실행, 290 통과, 기존 실제 M2 모델 opt-in 2 skip,
  실패 0 (48.933초). 신규 테스트는 skip하지 않았다.
- 선택된 상태를 정규화 결과에서 완전히 재구성해 원본과 비교했다. M:N edge별 설정/시각,
  항목 actor/time/핀/메모/View/legacy 보존, 제목 중복·빈 상태·임의 JSON 값을 확인했다.
- PR 후보는 서로 다른 근거 3개를 보존하며 하나로 묶인다. workspace가 다른 같은 branch/
  bare ticket/commit은 합치지 않는다. 근거 없는 태스크 연결·상태·발견 시각을 생성하지 않는다.
- 파일·DB·프로세스·네트워크 I/O를 금지한 상태에서 순수 투영을 실행했다.
  실제 임시 FileStore + 합성 SQLite bridge 통합에서는 원본 sentinel/DB/state 파일 바이트 불변,
  재생성 후 동일 투영을 확인했다. 개인 Devin 원본은 열지 않았다.
- 다중 태스크 쓰기의 중간 실패를 주입했다. 기존 list가 journal을 복구한 후 양쪽 새 상태를
  함께 표시했고, 복구 충돌 시 양쪽 모두 precommit last_good을 표시했다. 외부 편집 원문,
  미완료 journal, 다른 정상 태스크를 보존하며 OCC·핀·메모 계약도 유지했다.
- 로그: `/tmp/devin-ui-v2-s0-baseline.log`, `/tmp/devin-ui-v2-s0-targeted.log`,
  `/tmp/devin-ui-v2-s0-full.log`. 저장소에는 재현 가능한 합성 fixture와 테스트만 추가했다.

이번 증거는 **합성 데이터 + 실제 Python 파일 저장소/SQLite 경로** 검증이다.
실제 Devin Desktop 보고 품질·운영 데이터·GUI 렌더링·팝오버·접근성·토큰 계측은
검증하지 않는다. 기존 M3-S04 GUI 증거를 S0의 새 UI 검증으로 재사용하지 않는다.

## S1 착수 조건

1. 이 투영 계약과 synthetic fixture, 전체 회귀가 통과한 S0 커밋을 기준으로 삼는다.
2. 기존 snapshot 획득의 복구/락 경계를 재사용하고, 별도 출처의 시각·오류를 유지한다.
3. S1은 420×600pt 기준 팝오버 spike에서 PyObjC native와 WKWebView를 비교한 후 선택한다.
   현재 NSMenu fallback을 보존한다. 기술 선택은 이번 S0에서 확정하지 않았다.
4. 실제 지원되는 태스크/항목·보고·수집 상태만 표시한다. active/new/intervention 카운트나
   우선순위는 근거 계약이 생기기 전 가짜 숫자로 채우지 말고 미지원/숨김으로 처리한다.
5. 필터·선택·스크롤 유지, 긴 문자열·빈 상태·last_good·수집 실패·공유 세션으로 UI를 검증한다.
   S2 이후의 편집·리소스 등록·완료 처리·세션 활성화를 앞당겨 구현하지 않는다.

이번 실행은 S0에서 종료한다. S1은 후속 실행 범위이며 아직 구현하지 않았다.
