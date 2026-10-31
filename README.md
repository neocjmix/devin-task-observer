# Devin Task Observer

| 항목 | 내용 |
|---|---|
| 문서 유형 | 제품 진입점 |
| 상태 | 개인 로컬 사용 기준선 고정 |
| 기준 | [2026-10-31 고정 기준선](docs/BASELINE.md) |
| 적용 범위 | M3 협조형 보고 + UI V2 |

Devin 작업 세션의 변화를 공동 태스크에 지속해서 모으고, macOS 상태 막대 팝오버에서 확인하는 로컬 도구입니다. 기본 정보는 데몬이 읽기 전용으로 수집하고, 의미 변화는 작업 세션이 스킬과 CLI로 보고합니다. 관측·저장·조회에는 별도 모델 호출이 없습니다.

## 문서 탐색

| 알고 싶은 것 | 문서 |
|---|---|
| 지금 무엇이 되고 어디까지 검증됐는가 | [고정 기준선](docs/BASELINE.md) |
| 목적·사용 흐름·기능·구현·부작용 | [제품 설명](docs/PROJECT_EXPLAINED_KO.md) |
| 다음에 무엇을 할 것인가 | [백로그와 GitHub 이슈](docs/BACKLOG.md) |
| 개발·검증을 어떻게 이어가는가 | [구현 절차](docs/IMPLEMENTATION_PLAN.md), [검증·인수인계](docs/VALIDATION_AND_HANDOFF.md) |
| 데이터와 화면 계약 | [데이터 계약](docs/DATA_AND_RECONCILIATION.md), [화면 명세](docs/UI_V2_SCREEN_SPEC.md) |
| 당시 계획과 실행 증거 | [이력](docs/history/README.md), [슬라이스 목록](docs/SLICES.md) |
| 문서 형식과 갱신 규칙 | [문서 안내](docs/README.md) |

## 실행과 저장 위치

기본 경로는 `~/.devin-task-observer/`이며 `config.json`, `state/`, `observer.sqlite3`를 둡니다. 태스크·메모·핀·이력은 JSON 파일이고, SQLite는 결정론적 관측과 체크포인트를 담당합니다. 앱의 `데이터 폴더 열기`와 태스크의 `상태 파일 보기`로 Finder에서 접근합니다.

개발용 진입점은 다음과 같습니다. 기존 설치를 사용할 때 이를 다시 실행하거나 데이터를 재생성할 필요는 없습니다.

```sh
./observerctl configure --workspace /absolute/personal/workspace
python3 scripts/setup_menu.py
./observerctl integration install --workspace /absolute/personal/workspace
./observerctl state create --task my-task --title '작업 제목'
"$HOME/Library/Application Support/Devin Task Observer/menu-runtime/bin/python" -m observer.desktop
```

Devin 작업 공간에서 `observer-reporting 스킬로 my-task 자동 보고를 시작해`라고 요청합니다. 해당 세션이 실제 ID를 확인하고 태스크 연결·보고 설정·추적 항목 정의를 합니다. 정상적인 도구 승인은 그대로 적용됩니다. [CLI 계약](docs/slices/M3-S01_CLI.md)과 [에이전트 스킬](skills/observer-reporting/SKILL.md)을 참고합니다.

## 현재 범위

- Python 코어, HTML/JS, PyObjC의 NSPopover + 로컬 WKWebView를 사용합니다. 별도 브라우저·HTTP 서버·Swift·Electron이 필요하지 않습니다.
- 태스크–세션은 다대다이며, 최신 유효 보고를 출처·시각과 함께 표시합니다. 판단 중에는 락을 잡지 않고 커밋 시 revision을 검사합니다.
- 명시적 핀·메모·이력·확인 위치를 보호합니다. 데이터 손상은 원문과 마지막 정상 기록을 보존하며 드러냅니다.
- 정확한 Devin 세션 탭 이동은 미지원입니다. 실행 중인 Devin 앱 활성화와 ID 복사를 제공합니다.
- 보고 ON은 매 턴 보고나 의미 정확성을 보증하지 않습니다. 실사용 품질·장기 실행 등 남은 검증은 백로그에 있습니다.

## 개발 상태

기준 제품 커밋은 `3ed1955`, 태그는 `baseline/2026-10-31-m3-ui-v2`입니다. 개발 브랜치 `p0/desktop-probe`와 [Draft PR #1](https://github.com/neocjmix/devin-task-observer/pull/1)을 유지합니다. `main`은 이전 계획 기준이며 아직 병합하지 않았습니다.

기준선 재검증: Python **414개 실행·412 통과·실모델 opt-in 2 skip·실패 0**, Node UI 회귀 통과. 실제 Mac·Devin 합성 세션 증거와 미검증 조건은 기준선 문서에서 구분합니다.

개인 환경과 합성 자료만 사용합니다. 원본 세션 DB·대화록·인증정보·개인 상태는 Git에 넣지 않습니다. P0–M2의 웹/모델 경로는 [별도 참고 기록](docs/history/LEGACY_RUNTIME.md)에 보존하며 현재 기본 경로와 섞지 않습니다.
