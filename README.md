# Devin Task Observer

| 항목 | 내용 |
|---|---|
| 문서 유형 | 제품 안내 |
| 상태 | 현재 |
| 기준 | [고정 기준선](docs/BASELINE.md) |
| 적용 범위 | M3 협조형 보고 + UI V2 |

Devin 작업 세션의 변화를 공동 태스크에 모으고 macOS 상태 막대 팝오버에서 확인하는 로컬 도구입니다. 기본 정보는 데몬이 읽기 전용으로 수집하고, 의미 변화는 작업 세션이 스킬과 CLI로 보고합니다. 관측·저장·조회에는 별도 모델 호출이 없습니다.

## 문서

| 내용 | 문서 |
|---|---|
| 현재 기능·검증·Git 기준 | [기준선](docs/BASELINE.md) |
| 목적·사용 흐름·기능·구현 | [제품 설명](docs/PROJECT_EXPLAINED_KO.md) |
| 설치·실행·설정·복구 | [운영 안내](docs/RUNTIME.md), [CLI 참조](docs/CLI_REFERENCE.md) |
| 앞으로 할 일 | [백로그와 이슈](docs/BACKLOG.md) |
| 전체 문서·작성 규칙 | [문서 안내](docs/README.md) |

## 사용

개인 작업 공간에 스킬·훅을 설치하고 태스크를 만든 뒤 Devin에서 `observer-reporting 스킬로 my-task 자동 보고를 시작해`라고 요청합니다. 세션이 실제 ID를 확인하고 연결·보고 설정·추적 항목을 정의합니다. 이후 사용자는 Devin에서 작업하고 상태 막대에서 결과를 읽습니다.

개발 환경의 실행 진입점:

```sh
./observerctl configure --workspace /absolute/personal/workspace
python3 scripts/setup_menu.py
./observerctl integration install --workspace /absolute/personal/workspace
./observerctl state create --task my-task --title '작업 제목'
"$HOME/Library/Application Support/Devin Task Observer/menu-runtime/bin/python" -m observer.desktop
```

데이터는 `~/.devin-task-observer/{config.json,state/,observer.sqlite3}`에 있습니다. 태스크·메모·핀·이력은 JSON 파일, 관측·체크포인트는 SQLite로 관리합니다. 앱의 `데이터 폴더 열기`와 `상태 파일 보기`로 Finder에서 접근합니다.

## 지원 범위

Python 코어와 HTML/JS를 PyObjC NSPopover + 로컬 WKWebView로 표시합니다. 태스크–세션은 다대다이며 값·작성 세션·시각·근거를 보존합니다. 판단은 세션이 수행하고 커밋 시 revision을 검사합니다. 명시적 핀·메모·이력·확인 위치를 보호하며 파일 오류와 마지막 정상 기록을 드러냅니다.

보고 ON이 매 턴 보고나 의미 정확성을 보증하지 않습니다. 정확한 Devin 세션 탭 이동은 지원하지 않으며 실행 중인 앱 활성화·ID 복사를 제공합니다. 토큰은 보존 메시지 기준이고 과금 총량이 아닙니다. 앱은 checkout과 로컬 Python에 의존합니다.

제품 기준은 `3ed1955`, 태그는 `baseline/2026-10-31-m3-ui-v2`입니다. 개발은 `p0/desktop-probe`와 [Draft PR #1](https://github.com/neocjmix/devin-task-observer/pull/1)에서 진행하며 아직 main에 병합하지 않았습니다. 테스트와 미검증 조건은 기준선, 후속 작업은 백로그에서 관리합니다.

개인 환경과 합성 자료만 사용하며 원본 세션 DB·대화록·인증정보·개인 상태는 Git에 넣지 않습니다.
