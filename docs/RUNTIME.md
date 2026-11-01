# 설치·실행·운영

| 항목 | 내용 |
|---|---|
| 문서 유형 | 운영 안내 |
| 상태 | 현재 |
| 기준 | [고정 기준선](BASELINE.md) |
| 적용 범위 | 네이티브 앱·수집·스킬·설정·호환 기능 |

## 기본 경로

| 용도 | 경로·진입점 |
|---|---|
| 사용자 데이터 | `~/.devin-task-observer/{config.json,state/,observer.sqlite3}` |
| 기본 UI | `python -m observer.desktop` — NSPopover + 로컬 WKWebView |
| UI 없는 조회 | `python -m observer.desktop --snapshot` |
| 상태 조회 | `./observerctl state list`, `./observerctl status` |
| 수집만 실행 | `./observerctl collect`, 한 번만은 `collect --once` |
| Python 환경 | `~/Library/Application Support/Devin Task Observer/menu-runtime/` |

`--config`, `state --root`, `configure --store`는 해당 기본값을 재정의한다. 설정과 태스크 root는 별개다. 일반 시작과 configure는 다른 경로의 상태를 자동 이관·병합하지 않는다. 원본 Devin DB는 `~/.local/share/devin/cli/sessions.db`이며 읽기 전용으로 접근한다.

## 설치와 앱 실행

```sh
./observerctl configure --workspace /absolute/personal/workspace
python3 scripts/setup_menu.py
"$HOME/Library/Application Support/Devin Task Observer/menu-runtime/bin/python" -m observer.desktop
```

여러 `--workspace`로 개인 작업 공간을 명시한다. Git 사실 수집은 정상 Git 실행 파일이 필요하며 `configure --git /absolute/path/to/git`로 지정할 수 있다. Git이 없으면 Git 정보만 제한된다.

설치 도구는 rumps 0.4.0·PyObjC Cocoa/WebKit 12.2.2를 전용 환경에 설치한다. `--app /absolute/path/Observer.app`을 추가하면 py2app 0.28.10의 alias 실행기를 만든다. 기존 앱은 덮어쓰지 않는다. Python·Codex/Devin CLI는 앱에 포함하지 않으며 checkout과 venv가 유지되어야 한다.

이미 설치된 관리형 앱의 전환은 `--upgrade-app APP --config CONFIG --root ROOT`를 사용한다. 바인딩을 바꾸려면 `--previous-config OLD_CONFIG --previous-root OLD_ROOT`를 함께 명시한다. 정확한 관리형 런처만 검증·백업한 뒤 원자 교체하며, 수정된 실행기는 거절한다. 이 명령은 데이터 파일을 옮기지 않는다.

## 스킬과 훅

```sh
./observerctl integration install --workspace /absolute/personal/workspace
./observerctl integration status --workspace /absolute/personal/workspace
./observerctl integration remove --workspace /absolute/personal/workspace
```

설치는 선택한 workspace의 `.devin/skills/observer-reporting/`와 `.devin/hooks.v1.json`에 필요한 항목만 추가한다. 소유권 checksum을 기록하고 무관한 훅을 보존한다. 설치된 파일이 사용자가 수정한 상태라면 덮어쓰거나 제거하지 않는다.

SessionStart·UserPromptSubmit·PostCompaction·Stop·SessionEnd 훅은 실제 session ID와 작은 문맥을 전달한다. `context.json`에는 식별 정보, `activity.json`에는 활동 시각·이벤트·문맥 재전달 필요 여부를 저장하며 프롬프트·응답·압축 요약은 보관하지 않는다. 훅은 보고를 대신 생성하거나 원래 작업을 차단하지 않는다. 실패 시 빈 결과와 정상 종료로 빠진다.

Devin에서 `observer-reporting 스킬로 my-task 자동 보고를 시작해`라고 요청하면 세션이 context 확인·태스크 연결·보고 ON·항목 정의를 한다. 판단과 CLI 호출은 해당 세션이 수행하고 정상 도구 승인을 따른다. ON은 세션을 깨우거나 매 턴 보고를 강제하지 않는다. [스킬](../skills/observer-reporting/SKILL.md)과 [태스크 CLI](CLI_REFERENCE.md)를 따른다.

## 지속 실행과 종료

앱에 수집 런타임을 포함한다. 기본 수집 주기는 5초, 실패 재시도 상한은 60초, 한 수집의 timeout은 30초다. 별도 자식 프로세스로 수집하며 정상 종료 시 중단·락 해제를 수행한다. projection 갱신은 기본 2초다.

관측 DB별 `.service.lock`으로 수집 소유자를 하나만 둔다. 웹·메뉴·headless 수집을 같은 DB에 중복 실행하지 않는다. 설정을 실행 중 바꾸면 재시작이 필요하며 다른 설정을 조용히 혼합하지 않는다. 종료 버튼이나 SIGTERM으로 종료한 뒤 같은 설정으로 재실행하면 저장된 태스크와 관측 상태를 읽는다.

로그인 자동 실행은 별도 명시적 opt-in이다. `observerctl autostart install --mode collector|menu|legacy-web`와 `autostart remove`를 제공한다. `menu` 모드는 NSMenu이며 기본 팝오버 자동 실행 등록을 의미하지 않는다. 현재 영구 등록은 없다. 기존 실행 중인 앱과 서비스 소유권을 확인한 뒤 필요한 범위만 등록한다.

## 상태 경로 변경

활성 설치는 `~/.devin-task-observer/`를 사용한다. 경로 변경 시 앱·수집을 중지하고 기존 락 안에서 상태·설정·SQLite의 일관된 백업과 복사를 수행해야 한다. 훅·스킬·설치 소유권·프로젝트 로컬 권한·앱 바인딩의 절대 경로도 함께 맞춰야 한다.

옛 root에는 원래 `.lock`과 `.relocated.json`을 남겨 협력 writer를 `relocated_store`로 거절한다. symlink나 자동 전달을 사용하지 않는다. 태스크·이력·영수증은 보존하고, 다음 정상 훅이 새 문맥을 전달하도록 활동 메타데이터를 준비한다. 범용 root 이동 명령은 제공하지 않는다.

검증한 Desktop 3.10.67에서는 열린 창이 옛 훅·context를 유지했다. 진행 중 작업이 끝난 뒤 해당 창의 `Developer: Reload Window`로 설정을 다시 읽으면 기존 세션 ID를 유지하며 새 경로의 SessionStart 훅이 전달된다. 실제 `/compact` 완료 후 PostCompaction이 재전달을 예약하고, 다음 UserPromptSubmit에서 새 context로 식별·조회·보고가 이어지는 것을 확인했다.

경로 갱신과 명령 승인은 별개다. Python 인터프리터를 앞에 붙인 실행은 CLI 직접 실행의 허용 규칙과 다를 수 있다. 설치된 CLI의 절대 경로를 직접 사용하는 형태에서 기존 workspace 규칙으로 validate·report·read가 추가 승인 없이 진행되는 것을 확인했다. 허용 규칙에 맞지 않는 명령은 Devin의 정상 승인을 받아야 하며 Observer가 권한을 확대하거나 승인을 우회하지 않는다. 검증 범위는 [기준선](BASELINE.md)을 따른다.

## SQLite 태스크 이관

`migrate`의 source는 **Observer SQLite**이며 Devin sessions.db가 아니다. 개인 DB와 태스크를 명시하고 새 전용 target을 사용한다.

```sh
./observerctl migrate plan --source /absolute/observer.sqlite3 --task my-task
./observerctl migrate apply --source /absolute/observer.sqlite3 --task my-task --target /absolute/new-state
./observerctl migrate check --target /absolute/new-state
./observerctl migrate rollback --target /absolute/new-state --restore-to /absolute/new-restored.sqlite3
```

전체 선택은 `--task` 대신 `--all`이다. apply는 `--expected-fingerprint`로 검토한 source를 확인할 수 있다. 원본 백업·JSON 아카이브·스키마·관계·이력 보존을 검사하고 새 target을 공개한다. 기존 target 병합이나 양방향 동기화는 하지 않는다.

가져온 View·리소스·확인 위치는 태스크의 `legacy` 스냅샷이며 새 UI 데이터로 자동 승격하지 않는다. 이관값과 새 보고를 `legacy_import`/`reported`로 구분한다. source를 덮어쓰지 않으며 rollback도 새로운 SQLite 파일에 복원한다. 이관 후 새 보고나 파일 변경이 있으면 보수적 rollback을 거절한다. 합성 회귀는 통과했지만 실제 개인 legacy DB 전환은 [B06](BACKLOG.md)에 남아 있다.

## 선택적 호환 기능

`observer.desktop --legacy-menu` 또는 `observerctl menu`는 NSMenu, `observerctl run`은 로컬 웹 대시보드다. 웹 주소 기본값은 `http://127.0.0.1:8765`이며 선택한 설정의 workspace/store를 사용한다. 이 기능의 SQLite 태스크와 현재 파일 태스크는 별개이며 같은 ID라고 자동 동기화되지 않는다.

웹의 수동 Semantic Refresh는 별도의 선택적 모델 기능이다. 기본 비활성화이며 팝오버·일반 조회·수집은 이를 호출하지 않는다. 활성화는 configure의 `--semantic-adapter codex_exec|devin_acp`, `--semantic-executable`, `--semantic-environment personal|internal`로 명시한다. ACP는 `--approve-acp`가 필요하며 실제 조직 승인이나 인증을 대신하지 않는다. internal 정책은 Codex로 fallback하지 않는다.

코드에 남은 모델 경로는 호출/토큰 예약 예산·시간/크기 제한·실패 시 cursor 보존을 적용한다. 예산은 실제 제공자 과금의 확정 상한이 아니다. 알려진 제약은 원본 공백 뒤 재개, 혼합 출처 갱신, 연결 scope 변경 신선도, 보류 상태 표시, 의미 재서술 중복이며 [B07](BACKLOG.md)로 관리한다. 회사 데이터를 개인 Codex나 외부 API로 보내지 않는다.
