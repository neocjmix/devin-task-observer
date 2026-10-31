# 현재 기준선 — M3 + UI V2

| 항목 | 내용 |
|---|---|
| 문서 유형 | 고정 기준선 |
| 상태 | 2026-10-31 확정; 이후 검증 이력을 소급 합치지 않음 |
| 기준 | 제품 커밋 `3ed1955`; 2026-10-31 KST |
| 적용 범위 | 제품 코드·검증·실행·Git 상태 |

## 기준 식별

| 구분 | 고정 값 |
|---|---|
| 제품 코드 | [`3ed1955f97aaaef4056b5a764a9afa001ea2d1f4`](https://github.com/neocjmix/devin-task-observer/commit/3ed1955f97aaaef4056b5a764a9afa001ea2d1f4) |
| Git 태그 | `baseline/2026-10-31-m3-ui-v2` (위 제품 커밋을 가리킴) |
| 개발 브랜치 | `p0/desktop-probe` |
| 검토 | [Draft PR #1](https://github.com/neocjmix/devin-task-observer/pull/1), base `main`, 미병합 |
| main | `c913494` — 초기 계획 기준; 현재 실행 코드 아님 |
| 완료 범위 | M3-S01–S05, UI-V2-S0–S8의 구현 및 기록된 수용 범위 |
| 이전 개인 사용 기준 | [`998978a`](https://github.com/neocjmix/devin-task-observer/commit/998978a471007e1b29fc07e3ee2633e0a435d967), M3 초기 NSMenu 단계 |

문서 정리 커밋은 제품 기준 커밋 뒤에 추가된다. 따라서 이후 브랜치 HEAD와 태그 SHA가 다를 수 있지만, 그 차이가 문서·정리 도구뿐이면 제품 기준선이 바뀐 것이 아니다. 태그를 이동하지 않고 다음 구현에서 새 슬라이스와 기준을 기록한다. 브랜치 이름은 기존 PR 연속성을 위해 유지한다. 기준선 고정은 정식 배포·공증·운영 품질 보증이나 PR 병합을 뜻하지 않는다.

## 현재 구현

| 영역 | 구현 상태 | 근거 |
|---|---|---|
| 기본 관측 | 확인한 Devin Local schema 17을 읽기 전용 수집, 결정론적 변경·checkpoint, 실패 재시도 | [P1 기록](history/P1_PROGRESS.md), [M1 기록](history/M1_PROGRESS.md) |
| 협조형 보고 | 작업 세션의 스킬·CLI·최소 훅, 다대다 연결, 보고 ON/OFF, 세션이 정의하는 추적 항목 | [M3-S01](slices/M3-S01.md), [M3-S02](slices/M3-S02.md) |
| 지속 상태 | JSON Schema, OCC, 짧은 공통 락, journal 복구·중복 방지, 출처·핀·메모·이력 | [트랜잭션 계약](STATE_TRANSACTION_CONTRACT.md) |
| UI V2 | 태스크·세션 탐색, 고정 상단 관리, 리소스·메모·핀·이력, 토큰·명시적 확인 위치, 오류·last_good | [화면 명세](UI_V2_SCREEN_SPEC.md), [S8](slices/UI-V2-S8.md) |
| Finder 접근 | 전체 데이터 폴더·태스크 state.json 보기 | [S8 후속 기록](slices/UI-V2-S8.md) |
| 실행·복구 | 팝오버 기본, NSMenu 명시적 fallback, 별도 수집기·앱 종료/재시작, 명시적 legacy 이관 | [M3-S02 실행](slices/M3-S02_MENU.md), [M3-S03](slices/M3-S03.md) |
| 상태 위치 | `~/.devin-task-observer/`로 기본값과 활성 설치 이전, 옛 root 차단 | [M3-S05](slices/M3-S05.md) |

의미 판단은 작업 세션이 한다. Observer는 완료 보고를 독립 검증한 사실로 승격하거나 상충하는 주장을 모델로 자동 조정하지 않는다. 수집과 조회에는 LLM 호출이 없고, 에이전트의 보고 판단·도구 실행은 해당 작업 세션의 토큰을 사용한다.

## 검증 근거

| 증거 종류 | 확인한 내용 | 한계 |
|---|---|---|
| 이번 기준선 자동 회귀 | Python 414 실행, **412 통과·2 skip·0 실패**, 50.936초; Node helper/DOM/Core→JS 회귀 통과 | 실모델 opt-in은 실행하지 않음 |
| 기존 실제 Devin 개인 합성 실험 | 스킬·훅 ID, 후속 능동보고, 충돌 재판단, 메모·핀 보호 | 일상 업무의 누락률·정확성 보장은 아님 |
| 기존 실제 Mac + 합성 fixture | 8 source session/7 task, 입력 보존·충돌·리소스·관리·확인 위치·light/dark·AX·오류/복구·재시작 | 8개 live agent 동시 실행이 아님 |
| 기존 실제 개인 합성 원본 읽기 | Desktop 3.10.67의 4세션 사용량, DB/WAL 읽기 전후 불변 | 다른 provider/schema·과금 총량 보장 아님 |
| 직전 M3-S05 실제 유지보수 | 태스크/이력/영수증 보존, SQLite 내용·무결성, 설치 두 곳, 재실행·수집·Finder 새 경로 | 기존 세션의 경로/권한 캐시 재인식은 미검증 |
| 이번 실행 상태 점검 | 설치 앱·서비스 실행, 마지막 수집 성공·오류 없음, revision 18/항목 6/연결 3/메모 1 유지 | 읽기 전용 점검; 실제 새 보고 턴은 실행하지 않음 |

이번 로그는 로컬 `~/devin-observer-research-lab/2026-10-31-baseline-3ed1955/`의 `python-tests.log`, `node-tests.log`, `runtime-readonly.json`에 둔다. 이전 GUI·실험 증거 위치는 각 슬라이스에 있다. 비공개 상태와 스크린샷은 커밋하지 않는다. 당시 테스트 수는 당시 기록에만 두며 합산하지 않는다.

## 로컬 설치 상태

- 데이터: `~/.devin-task-observer/{config.json,state/,observer.sqlite3}`. 명시적 경로 override 허용.
- 앱: `~/Applications/Devin Task Observer.app`; 로컬 checkout과 Python 환경을 참조하는 alias 실행기.
- 실행 의존성: `~/Library/Application Support/Devin Task Observer/menu-runtime/`. Python·모델 CLI를 앱에 포함하지 않음.
- 기본 진입점: `observer.desktop`; `--legacy-menu`/`observerctl menu`는 이전 NSMenu, `observerctl run`은 이전 웹 경로.
- 영구 로그인 자동 실행 미등록. 별도 이전 M2 실험 프로세스와 백업은 유지하며 현재 태스크 원본으로 합치지 않음.

## 미검증과 미지원

[백로그](BACKLOG.md)는 후속 작업의 유일한 목록이다. 데이터 이전 후 기존 세션의 보고 연속성, 개인 사용 품질·비용, 장기/병렬 실행, 접근성·추가 환경, 이력 증가, 실제 legacy DB 이관 검증이 남는다.

정확한 세션 탭 이동, 다른 Devin provider/schema, 독립 배포·공증은 지원 범위 밖이다. 토큰은 보존 메시지 합계이며 과금·평생 총량·fork 중복 제거를 보장하지 않는다. 과거 보고 revision이 없는 태스크의 확인 위치는 추측하지 않는다. 새 optional 필드가 있는 상태를 옛 바이너리로 검증할 수 있다고 가정하지 않는다.
