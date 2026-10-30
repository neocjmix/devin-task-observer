# M3-S01 CLI·파일 계약

이 문서는 현재 `observer/state_cli.py`, `cooperative.py`, `file_store.py`, `state_schema.py`의 구현 계약이다.
완료 여부와 당시 테스트 결과는 [M3-S01](M3-S01.md)에 따로 기록한다. 후속 스킬·훅·메뉴는 [M3-S02](M3-S02.md), 이관은 [M3-S03](M3-S03.md)에 있다.
보고 스킬은 [observer-reporting](../../skills/observer-reporting/SKILL.md)이며 이 저장소에만 제공한다.
스킬 설치·Desktop 자동 전달·훅 설정은 수행하지 않는다.

## 진입점과 저장소 구분

```sh
./observerctl state --help
./observerctl state --root /absolute/path/to/personal-observer-state list
```

`state` 다음의 `--root`가 **새 협조형 태스크 파일 저장소**를 선택한다. 기본 경로는
`~/Library/Application Support/Devin Task Observer/state`다. 기존 서비스의 `--config`와
SQLite `store`는 별개이며 자동으로 가져오지 않는다. 기존 DB 태스크 ID와 같은 문자열을
새 태스크 ID로 쓰더라도 같은 태스크로 동기화하지 않는다. S01 자체는 이관하지 않는다. 현재 명시적 이관 명령은 [M3-S03](M3-S03.md)을 따른다.

기존 `observerctl configure/run/status/autostart`는 기존 수집기·브라우저 런타임에 계속 적용된다.
새 `state` 명령은 데몬이나 웹 서버 없이 동작하며 모델·네트워크를 호출하지 않는다.
Devin 원본 디렉터리 밖의 개인 로컬 경로를 사용한다. 저장소 파일·context·report·work copy는
민감한 파생 정보일 수 있으므로 Git에 넣지 않는다.

## 명령

아래 공통 접두부는 `./observerctl state [--root PATH]`다. `--root`는 하위 명령 앞에 둔다.
쓰기 뒤 반환된 revision이나 새 `read` 결과를 다음 쓰기에 사용한다.

| 명령·필수 인자 | 동작 |
|---|---|
| `create --task ID --title TEXT [--session SID] [--request-id ID]` | 새 태스크. 선택한 세션 연결은 reporting OFF로 시작 |
| `list [--session SID]` | 전체 또는 해당 세션의 태스크와 파일 건강 상태 |
| `read --task ID` | 현재 상태·revision·출처, 오류, 명시적인 last_good와 원본 digest |
| `history --task ID` | 커밋된 태스크 revision 스냅샷 |
| `link --task ID --session SID --expected-revision N [--request-id ID]` | 명시적 연결; 같은 연결 재생성은 `already_linked` |
| `reporting --task ID --session SID --enabled on\|off --expected-revision N [--request-id ID]` | 해당 contribution의 보고 활성화 변경 |
| `session-register --session SID --out FILE` | 명시한 ID의 비공개 context 파일 생성. 기존 파일 덮어쓰기 거부 |
| `whoami --context FILE` | context 검증·조회. `identity_verified: false` |
| `report --file FILE [--context FILE]` | 보고를 검증하고 expected revisions를 검사해 조건부 커밋 |
| `pin --task ID --item ID --pinned on\|off --expected-revision N [--request-id ID]` | 사람의 명시적 고정·해제 |
| `note --task ID --text TEXT --expected-revision N [--request-id ID]` | 인간 메모 추가. 기존 메모 삭제·수정 명령은 없음 |
| `edit-export --task ID --out FILE` | 인간 편집용 작업 사본·revision 생성. 기존 파일 덮어쓰기 거부 |
| `edit-apply --file FILE [--request-id ID]` | 인간 작업 사본을 CAS로 반영; 기존 항목 ID 집합 보존 |
| `validate --file FILE --kind task\|report\|edit\|context` | JSON 형식·스키마 검사. 커밋 권한/현재 revision 확인은 실제 반영에서 수행 |
| `validate [--task ID]` | 하나 또는 전체 파일 저장소의 현재 건강 상태 검사 |
| `schema --kind task\|report\|edit\|context` | 실제 검증기와 같은 정의의 JSON Schema 반환 |
| `recover --task ID --expected-digest HEX\|missing` | 검토한 현재 원문을 별도 보관한 뒤 마지막 정상 커밋 복원 |
| `observations --observer-config FILE [--task ID]` | 지정된 기존 Observer SQLite의 수집 사실만 읽기 |

입력 파일을 받는 `report`, `edit-apply`, `validate --file`에는 `-`로 stdin을 줄 수 있다.
쓰기 응답은 태스크별 revision만 담은 작은 receipt이며 `snapshot`은 `committed` 또는 재전송의 `original_commit`이다. `list`는 title/revision/item_count/contributions/health 요약을 돌려주고 전체 View는 포함하지 않는다. 정상 `read`는 같은 내용을 last_good에 중복해서 반환하지 않는다.

일반 응답은 JSON이다. 성공은 `ok: true`, 실패는 `ok: false`와 code/message/details이며 종료값 2다.
`read`/`list`는 조회 성공이어도 `health`가 invalid/external_edit/unavailable일 수 있다.
따라서 프로세스 성공만으로 내용을 현재 정상 상태라고 취급하지 않는다. `validate`는 유효하지 않으면 종료값 2다.

## 첫 보고의 흐름

다음은 격리된 합성 예다. 실제 세션 ID는 호스트가 제공하거나 명시적으로 확인한 값을 사용하며
경로·PID에서 추측하지 않는다. `session-register`는 작성자가 주장한 ID를 기록할 뿐 실제 주체를 인증하지 않는다.

```sh
./observerctl state --root /private/tmp/observer-example create --task task-example --title "Synthetic interface work" --session session-A --request-id create-example-001
./observerctl state --root /private/tmp/observer-example session-register --session session-A --out /private/tmp/observer-context-example.json
./observerctl state --root /private/tmp/observer-example reporting --task task-example --session session-A --enabled on --expected-revision 1 --request-id enable-example-001
./observerctl state --root /private/tmp/observer-example read --task task-example
```

위 경우 읽은 revision은 2다. [스킬의 보고 예제](../../skills/observer-reporting/SKILL.md#read--judge--conditional-report)를
실제 읽은 revision과 해당 세션·태스크 ID에 맞추어 사용한다. 예제의 숫자를 고정값으로 쓰지 않는다.
전송 전 `validate --kind report`로 파일을 확인하고 `report --context ...`로 context 일치를 검사한다.
기본 CLI에서 context는 선택적이지만 스킬 흐름은 등록 context를 사용한다. 이것이 인증 경계가 되지는 않는다.

보고 스키마의 `expected_revisions`는 읽은 태스크 ID → revision이고 `changes`는 변경할 태스크 ID → 연산 배열이다.
참고만 한 태스크도 expected revisions에 넣으면 커밋 시 함께 검사하되 상태를 쓰거나 revision을 올리지 않는다.
변경 대상은 모두 expected revisions에 있어야 한다. 변경 하나가 실패하면 전체 보고를 적용하지 않는다.

`define`에는 새 item_id/title/value/status/evidence가 필요하다. `update`는 기존 ID의 title/value/status/evidence 중
변경 필드만 보낸다. `archive`는 op/item_id만 보낸다. 제목이 같은 항목을 자동 병합하지 않는다.
value에는 JSON 값을, status에는 태스크가 정의한 문자열을 사용할 수 있다. 검증 상태는 항상 `reported`다.
근거 배열은 연결된 세션만 참조하며 kind는 session_event/local_artifact/report 중 하나다.
참조 문자열이 실제 원본이나 파일에 존재하는지, 보고 내용이 사실인지 자동 검증하지 않는다.

## 버전·재전송·사용자 보호

읽기 → 판단 → 쓰기 사이에는 락이 없다. 짧은 최종 커밋 구간에서 관련 태스크 revision을 함께 비교한다.
충돌 응답에는 최신 값·revision·업데이트 세션이 있다. 세션이 이를 읽고 의미를 재판단한다.
report ID를 바꾸고 revision만 최신 숫자로 올려 이전 판단을 그대로 밀어 넣는 자동 재시도는 하지 않는다.
표준 스킬은 한 보고당 충돌 재판단·재제출을 최대 2회로 제한한다.

결과가 불확실하면 같은 report ID와 정확히 같은 payload를 다시 보낸다. 이미 커밋된 보고는
`replayed: true`와 원래 receipt를 반환하고 다시 변경하지 않는다. 같은 ID의 다른 payload는 거부한다.
receipt는 그 보고 당시 상태이므로 현재 값은 별도 `read`로 확인한다. report/request ID는 저장소 전체에서 공유된다.
report ID는 최대 80자의 영문·숫자·하이픈·밑줄이며 첫 글자는 영문·숫자다. task/item ID도 같은 규칙이다. UUID를 사용할 수 있다.

태스크 제목·정의·보고값·연결·보고 ON/OFF·핀·인간 View와 메모는 세션 판단의 문맥이므로 같은 태스크 revision을 올린다. 기존 수집 시각·웹 확인 위치는 이 revision을 올리지 않는다.

고정된 항목은 보고로 변경하거나 archive할 수 없다. 인간 편집 후 고정하지 않은 값은 다음 유효 보고가
갱신할 수 있고 이전 값·작성자는 history에 남는다. `pin`/`edit-apply`는 인간의 명시적 편집 경로다.
동일 OS 사용자의 다른 프로세스가 이 명령이나 파일 권한을 악용하는 것까지 인증으로 막는 제품은 아니다.
스킬은 보호 우회를 위해 인간 전용 경로나 새 대체 ID를 사용하지 않는다.

보고 활성화는 contribution별이다. 끄면 이후 새 보고를 거부하며 마지막 보고 시각과 기존 값은 남는다.
ON은 실행 중인 에이전트에게 지침을 전달하거나 잠든 세션을 깨우는 기능이 아니다. 변화가 없으면 보고하지 않는다.

## 파일 배치와 복구

```text
state/
  .lock
  tasks/<task-id>/state.json
  tasks/<task-id>/history/<20자리-revision>.json
  reports/<report-id의-SHA256>.json
  .transactions/<report-id의-SHA256>.json
  recovery/<task-id>/<복구-id>.raw
  recovery/<task-id>/<복구-id>.json
```

새 태스크의 정의·contributions·items·notes·View markdown은 `state.json`을 원본으로 관리한다.
history는 각 커밋의 전체 스냅샷이며 reports는 중복 반영을 막는 영수증이다. 파일명은 식별자 해시일 수
있지만 본문은 JSON으로 읽을 수 있다. 현재 YAML 또는 별도 `view.md` 파일은 제공하지 않으며
자유로운 View 내용은 `view.markdown`에 둔다. published schema는 `schema` 명령으로 조회하며 [schemas/cooperative-v1](../../schemas/cooperative-v1/)에도 같은 JSON Schema를 제공한다. 파일과 입력은 2 MiB로 제한하며 report는 최대 20개 태스크, 태스크당 최대 100개 연산이다. 각각의 태스크는 최대 1,000개 항목·100개 contribution을 허용한다.

파일 임시 저장·fsync·원자적 교체와 짧은 공통 flock을 사용한다. 여러 태스크의 변경은 준비 journal을
먼저 저장하고 읽기/쓰기 전에 미완료 커밋을 복구한다. 정상적인 프로세스 중단은 roll-forward한다.
외부 편집과 충돌하면 원문을 덮어쓰지 않고 영향 태스크의 사용 불가·이전 상태를 표시한다.
손상된 journal·history 자체까지 임의로 고쳐 일관성을 추정하지 않는다.

안전한 인간 편집 절차:

1. `edit-export`로 작업 사본을 만든다. title, 기존 항목의 값·상태·핀·보관 여부, View markdown을 편집한다.
2. `validate --kind edit --file ...`로 검사한다. 항목 ID를 삭제하거나 새로 넣지 않는다. 새 항목 정의는 보고 경로를 사용한다.
3. `edit-apply`로 반영한다. 읽은 뒤 상태가 바뀌었다면 충돌을 반환한다. 새 사본을 읽고 인간 편집 의도를 다시 반영한다.
4. 에이전트 작성 보고와 달리 이 경로의 실제 변경 출처는 human이다. 메모는 `note`로 추가하고 보고가 수정하지 않는다.

원본을 직접 고친 경우에는 `read`의 health/errors/content_digest/last_good를 먼저 확인한다.
문법이 맞아도 history와 다른 원본은 external_edit이며 자동으로 현재 정상 상태로 채택하지 않는다.
원문을 살펴본 뒤 `recover`에 정확히 그 content_digest를 제공하면 원문을 recovery에 보존하고
마지막 커밋을 복원한다. 보존한 편집을 새 work copy에 옮겨 검증·반영할 수 있다.
state.json이 삭제돼 content_digest가 null이면 `--expected-digest missing`을 사용한다. 삭제 사실을 기록하고 마지막 커밋을 복원한다. 원본이 중간에 다시 바뀌면 digest 비교로 복구를 거절한다. 미완료 다중 태스크 커밋이나 손상된
history가 얽힌 경우 `recover`가 거부할 수 있으며 자동 수리·수동 journal 삭제는 하지 않는다.

## 기존 결정론적 관측과 한계

`observations`는 명시한 기존 설정의 workspace 허용 목록을 사용하고 기존 Observer SQLite를
mode=ro/query_only로 읽는다. collector의 마지막 시도·성공·오류, 세션 정보·관측 수,
현재 source hash와 일치하는 리소스 언급·plan 스냅샷을 돌려준다. `--task`는 새 태스크에
연결된 세션으로 좁힌다. 원본 트랜스크립트나 Devin DB를 직접 열어 내용을 추가 수집하지 않는다.
세션별 리소스/plan은 각각 최대 100건과 잘림 표시를 제공한다. 계획 순서는 추론하지 않는다.
조회 실패는 경고이며 보고된 태스크 값을 수정하지 않는다. 관측 세션 존재도 호출자 인증이 아니다.

이 단계는 로컬 파일 시스템의 협조하는 프로세스를 대상으로 한다. 임의 원본 파일 편집의 동시성,
하드웨어 손실, 네트워크 파일 시스템, 악의적인 동일 사용자 프로세스까지 원자성을 보장하지 않는다.
정상 read는 최신 스냅샷을 읽고 과거 JSON 본문을 전부 재해석하지 않는다. history 명령은 전체 이력을 읽으며, unpruned history/receipts 저장량은 보고마다 증가한다. 대규모·장기간 성능은 아직 검증하지 않았다. 손상된 journal의 대상 범위 자체를 신뢰할 수 없으면 그 저장소의 조회·쓰기를 보수적으로 중단할 수 있다. 영향 태스크를 알 수 없는 손상을 무조건 태스크별로 격리한다고 보장하지 않는다.
새 단위/프로세스/CLI 테스트는 실제 Desktop이 스킬을 스스로 실행하거나 의미를 정확히 보고했다는 증거가 아니다.
