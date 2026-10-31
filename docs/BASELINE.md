# 현재 기준선

| 항목 | 내용 |
|---|---|
| 문서 유형 | 고정 기준선 |
| 상태 | 현재 |
| 기준 | 제품 커밋 `3ed1955`; 2026-10-31 KST |
| 적용 범위 | 제품 코드·기능·검증·로컬 실행 |

## 코드와 Git

| 구분 | 기준 |
|---|---|
| 제품 코드 | [`3ed1955f97aaaef4056b5a764a9afa001ea2d1f4`](https://github.com/neocjmix/devin-task-observer/commit/3ed1955f97aaaef4056b5a764a9afa001ea2d1f4) |
| 태그 | `baseline/2026-10-31-m3-ui-v2` |
| 개발 브랜치 | `p0/desktop-probe` |
| 검토 | [Draft PR #1](https://github.com/neocjmix/devin-task-observer/pull/1), base `main`, 미병합 |
| main | `c913494`; 현재 제품 코드는 개발 브랜치에 있음 |
| 확인일 | 2026-10-31 KST |

문서·주석 정리 커밋은 제품 기준 커밋 뒤에 추가될 수 있다. 태그는 제품 코드를 고정하며 이동하지 않는다. 다음 기능 변경은 새 ID와 검증 결과를 명시한다.

## 구현 상태

| 기능 | 현재 동작 | 구현 |
|---|---|---|
| 읽기 전용 관측 | Devin Local schema 17, 결정론적 변경·checkpoint, 실패 재시도 | [어댑터](../observer/devin_local.py), [저장](../observer/store.py) |
| 협조형 보고 | 스킬·최소 훅·CLI, 태스크–세션 다대다, 세션이 항목 정의·갱신 | [보고 Core](../observer/cooperative.py), [훅](../observer/integration.py) |
| 파일 상태 | JSON Schema·OCC·짧은 락·journal·중복 방지, 출처·핀·메모·이력 | [트랜잭션](STATE_TRANSACTION_CONTRACT.md) |
| 팝오버 | 탐색·검색·역할·리소스·메모·핀·이력·관리·사용량·독립 확인 위치 | [화면](UI_V2_SCREEN_SPEC.md), [호스트](../observer/popover.py) |
| Finder 접근 | 전체 데이터 폴더·태스크 state.json 보기 | [동작 경계](../observer/ui_actions.py) |
| 지속 실행 | 별도 수집 프로세스, 서비스 락·정상 종료·재시작·설정 보존 | [런타임](../observer/runtime.py), [운영](RUNTIME.md) |
| 기본 저장 위치 | `~/.devin-task-observer/`, 활성 설치도 같은 위치 사용 | [경로](../observer/paths.py) |

의미 판단과 충돌 재판단은 작업 세션이 맡는다. Core는 검증된 커밋을 적용하며 보고의 사실성을 인증하지 않는다. 기본 관측·조회에 별도 LLM 호출은 없고 보고 판단·CLI 호출은 작업 세션의 토큰을 사용한다.

## 검증 상태

| 종류 | 확인한 범위 | 남은 경계 |
|---|---|---|
| 자동 회귀 | Python 414 실행: **412 통과·실모델 opt-in 2 skip·실패 0**; Node helper/DOM/Core→JS 회귀 통과 | 실모델 테스트는 비활성화 |
| 실제 Devin 개인 합성 세션 | 훅 ID·스킬·후속 보고·충돌 재판단·메모·핀 보호 | 일상 사용의 누락률·정확성은 미측정 |
| 실제 Mac + 합성 데이터 | 8 source session/7 task, 입력 보존·충돌·리소스·관리·확인 위치·light/dark·AX·오류/복구·재시작 | 8개 live agent 동시 실행과 다름 |
| 실제 원본 읽기 | Desktop 3.10.67 개인 합성 4세션 사용량, DB/WAL 읽기 전후 불변 | 다른 provider/schema·과금 총량 미보장 |
| 상태 위치 이전 | 태스크·이력·영수증 보존, SQLite 내용·무결성, 설치 두 곳, 재실행·수집·Finder 새 경로 | root 이동 명령은 제공하지 않음 |
| 기존 세션 보고 연속성 | 동일 개인 합성 세션이 새 저장소에 보고해 revision 18→19, 창 재로드·실제 압축 후 새 context로 다시 보고해 20. SessionStart→PostCompaction→UserPromptSubmit 확인 | Desktop 3.10.67의 해당 세션에서 확인. 재로드 전 옛 context 사용과 단일 명령 승인 5회, 재로드 후 Python wrapper 승인·조회 명령 형태 조정이 있었으며 승인 없는 전환을 보장하지 않음 |
| 보호·회귀 | 기존 항목 6개·핀·메모·View·리소스·관리·다른 기여 보존, 기존 root 보고 거절·해시 불변, Python 파일·workspace 권한 불변. integration 12개·relocation 6개 통과 | 검증 항목 하나를 추가·갱신했으며 합성 파서의 알려진 실패는 수정하지 않음 |
| 설치 상태 점검 | 수집 성공·오류 없음, 태스크 revision 20/항목 7/연결 3/메모 1 | 새 보고 확인은 실제 Devin UI·CLI·파일로 검증; 이번 Observer 팝오버 연결은 CUA timeout으로 추가 화면 검증하지 못함 |

자동 회귀 로그는 로컬 `~/devin-observer-research-lab/2026-10-31-baseline-3ed1955/`의 `python-tests.log`, `node-tests.log`, `runtime-readonly.json`에 있다. GUI 증거는 `~/devin-observer-research-lab/2026-10-31-ui-v2/evidence/`, 데이터 이전 검증·백업은 `~/devin-observer-research-lab/2026-10-31-state-home/`에 있다. 보고 연속성의 결과·실제 훅 이벤트·압축/보고 화면은 `~/devin-observer-research-lab/2026-10-31-b01/`의 `verification.json`, `activity-events.jsonl`, `old-root-rejected.json`, `compaction-complete.png`, `post-compaction-accepted.png`에 있다. 이 비공개 자료는 커밋하지 않는다.

## 로컬 설치

데이터는 `~/.devin-task-observer/{config.json,state/,observer.sqlite3}`, 앱은 `~/Applications/Devin Task Observer.app`, 실행 환경은 `~/Library/Application Support/Devin Task Observer/menu-runtime/`이다. 앱은 checkout과 venv를 참조하는 alias다. Python·모델 CLI를 번들하지 않는다.

기본 진입점은 `observer.desktop`이다. 영구 로그인 자동 실행은 미등록이며 선택적 호환 명령과 이관은 [운영 안내](RUNTIME.md)에 정의한다.

## 남은 제약

[백로그](BACKLOG.md)는 개인 사용 품질·비용, 장기/병렬 실행, 접근성·추가 환경, 이력 증가, 실제 legacy DB 전환을 관리한다. 정확한 세션 탭 이동·다른 provider/schema·독립 배포/공증은 미지원이다.

토큰은 보존 메시지 합계이며 과금·평생 총량·fork 중복 제거를 보장하지 않는다. 보고 위치가 없는 태스크의 확인 위치는 추측하지 않는다. 새 optional 필드가 있는 상태를 옛 바이너리가 검증할 수 있다고 가정하지 않는다.
