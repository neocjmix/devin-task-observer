# Devin Task Observer

| 항목 | 내용 |
|---|---|
| 문서 유형 | 제품 안내·설치 가이드 |
| 상태 | 현재 |
| 기준 | [고정 기준선](docs/BASELINE.md) |
| 적용 범위 | M3 협조형 보고 + UI V2, 회사 Mac 설치·사용 |

Devin 작업 세션의 변화를 공동 태스크에 모으고 **macOS 상태 막대 팝오버**에서 확인하는 로컬 도구입니다. 기본 정보는 데몬이 읽기 전용으로 수집하고, 진행 상황·블로커·결정·다음 행동은 작업 세션이 스킬과 CLI로 보고합니다. 사용자는 기존 Devin Desktop에서 계속 작업합니다.

관측·저장·조회·보고 CLI는 별도 모델을 호출하지 않습니다. 보고할 내용을 판단하는 **기존 Devin 세션의 토큰은 사용**합니다. 자동 보고 ON은 매 턴 보고나 의미 정확성을 보증하지 않습니다.

## 설치 에이전트에게

**이 README만으로 신규 설치와 첫 사용 검증을 진행할 수 있습니다.** 아래 명령은 사용자가 복사할 튜토리얼이 아니라 설치 에이전트가 실행할 절차입니다. 일반 설치·설정·테스트는 직접 수행하고, 해결할 수 없는 인증·조직 정책·권한 문제만 사용자에게 요청하세요. 실패한 단계와 실제 검증하지 못한 항목을 성공으로 보고하지 마세요.

회사에서 승인한 실행 에이전트·Devin 계정·소스 반입·패키지 배포 경로를 사용하세요. 회사 데이터나 인증정보를 개인 Codex·외부 API·이 저장소의 GitHub 이슈/PR로 보내지 마세요. 개인 Mac의 상태·세션 DB·venv·앱을 복사해 가져오지 마세요. 초기 검증에는 **내용이 합성인 전용 작업 공간과 Devin Local 세션**을 사용합니다.

이번 설치는 M3 경로만 사용합니다. **Codex CLI·Ollama를 설치하지 않고, Semantic Refresh·별도 ACP 분석·웹 대시보드를 켜지 않습니다.** 승인된 Devin Desktop의 일반 작업 세션이 보고합니다. 기본 팝오버는 HTTP 서버를 열지 않고 Observer에는 외부 텔레메트리가 없습니다.

## 1. 환경과 소스 준비

검증 환경은 Apple Silicon Mac, macOS 26.6.2, Python 3.14.6, Devin Desktop 3.10.67의 **Local schema 17**입니다. 다른 OS/Python/Desktop 버전과 provider는 같은 동작을 보장하지 않습니다. 현재 회사 환경에서의 호환성은 아래 절차로 따로 확인해야 합니다.

필요한 것은 승인된 Git, `venv`·`pip`를 사용할 수 있는 Python, 로그인된 Devin Desktop의 Local 작업 세션입니다. 이 프로젝트는 Python이나 Devin을 자동 설치하지 않습니다. 에이전트가 기존 설치를 먼저 확인하고, 없다면 조직의 정상 배포 경로로 확보하세요. 로그인/MFA나 관리자 승인이 필요하면 그 지점에서만 요청합니다. Gatekeeper·TLS·도구 승인 규칙을 비활성화하지 마세요.

실험 결과를 수용한 **`main` 브랜치가 정본**입니다. [PR #1](https://github.com/neocjmix/devin-task-observer/pull/1)의 M3 협조형 보고와 UI V2를 기준으로 사용합니다. 회사에서 GitHub 직접 접근이 허용되는 경우:

```sh
git clone --branch main --single-branch https://github.com/neocjmix/devin-task-observer.git
cd devin-task-observer
git status --short --branch
git rev-parse HEAD
```

비공개 저장소 접근이 허용되지 않으면 승인된 내부 미러나 소스 아카이브를 사용하세요. 회사 Mac에서 개인 GitHub 로그인을 임의로 추가하지 마세요. 아카이브는 main의 코드와 README·scripts·observer·skills·schemas·tests를 포함해야 하며 반입한 원본 commit SHA도 기록합니다. 제품 태그 `baseline/2026-10-31-m3-ui-v2`는 이전 코드 스냅샷이므로 현재 설치 소스와 혼동하지 마세요.

이미 checkout이 있다면 remote·branch·미커밋 변경을 확인하고 기존 작업을 보존하세요. 무조건 reset/checkout/pull하거나 새 clone으로 대체하지 마세요. 설치 후에도 **checkout과 Python 경로를 유지**해야 합니다.

이후 명령의 변수는 같은 셸에서 유지합니다. 아래 세 개의 예시 경로는 에이전트가 실제 승인된 경로로 정한 뒤 실행하세요. `observer_workspace`는 Observer 소스 폴더와 별개의 합성 테스트 프로젝트이며 미리 존재해야 합니다.

```sh
observer_repo='/absolute/path/to/devin-task-observer'
observer_python='/absolute/path/to/approved/python3'
observer_workspace='/absolute/path/to/synthetic-test-workspace'
observer_config="$HOME/.devin-task-observer/config.json"
observer_root="$HOME/.devin-task-observer/state"
observer_runtime="$HOME/Library/Application Support/Devin Task Observer/menu-runtime/bin/python"
observer_app="$HOME/Applications/Devin Task Observer.app"
cd "$observer_repo"
"$observer_python" --version
"$observer_python" -m venv --help
"$observer_python" "$observer_repo/observerctl" --help
```

CLI의 shebang은 `/usr/bin/env python3`입니다. Devin의 실행 환경에서도 `python3`가 승인된 호환 인터프리터로 해석되는지 확인하세요. 아래 설치 명령은 명시한 Python으로 실행하고 훅도 그 실행 인터프리터에 바인딩합니다. 시스템 Python을 덮어쓰지 마세요.

## 2. 설정 생성

기본 저장 경로는 다음과 같습니다. 로컬 사용자 디스크를 사용하며 네트워크 공유 폴더나 Devin 원본 폴더에 상태를 두지 않습니다.

| 용도 | 경로 |
|---|---|
| 설정 | `~/.devin-task-observer/config.json` |
| 태스크·메모·핀·이력·보고 영수증 | `~/.devin-task-observer/state/` |
| 결정론적 관측·체크포인트 캐시 | `~/.devin-task-observer/observer.sqlite3` |
| 기본 Devin 원본 DB — 읽기 전용 | `~/.local/share/devin/cli/sessions.db` |
| 네이티브 UI Python 환경 | `~/Library/Application Support/Devin Task Observer/menu-runtime/` |

**기존 config/state/앱/실행 프로세스를 먼저 확인하세요.** 아래 `configure`는 설정 전체를 다시 저장하므로 신규 설치에서만 그대로 실행합니다. 기존 설치는 실제 설정과 작업 공간 목록을 읽어 재사용하며, 변경이 필요하면 먼저 백업하고 의도한 전체 목록을 지정하세요. 이 명령은 옛 상태를 자동 이관하지 않습니다.

```sh
"$observer_python" "$observer_repo/observerctl" --config "$observer_config" configure \
  --workspace "$observer_workspace" --semantic-adapter off
```

여러 관측 대상은 `--workspace`를 반복해서 명시합니다. 홈 전체나 무관한 회사 프로젝트를 넣지 마세요. DB 위치가 실제로 다르면 확인한 절대 경로를 `--source`로 지정합니다. 기본 DB가 없다고 빈 DB를 만들거나 다른 provider 구조를 추측하지 마세요. 정상 Git 실행 파일이 PATH에 없다면 `--git /absolute/path/to/git`를 함께 지정할 수 있습니다.

`off`는 설정에 `semantic` 항목을 만들지 않습니다. 저장된 설정을 읽어 `semantic`이 없거나 `enabled: false`인지 확인하세요. `--semantic-environment internal`만으로 조직 승인이나 차단 정책이 생기는 것은 아닙니다. 기존 모델 설정이 켜져 있다면 그대로 실행하지 말고, 백업 후 비활성화해야 합니다.

## 3. 상태 막대 앱 설치

저장소 루트에서 실행합니다. `--app`은 새 앱 경로만 받으며 기존 앱을 덮어쓰지 않습니다.

```sh
"$observer_python" scripts/setup_menu.py \
  --app "$observer_app" --config "$observer_config" --root "$observer_root"
"$observer_runtime" -c 'import AppKit, WebKit, rumps; print("Native UI imports OK")'
```

설치 스크립트는 전용 venv에 `rumps==0.4.0`, `pyobjc-framework-Cocoa==12.2.2`, `pyobjc-framework-WebKit==12.2.2`를 설치합니다. 앱 생성에는 `py2app==0.28.10`, `setuptools==84.2.1`도 설치합니다. 조직의 패키지 미러·프록시가 필요하면 승인된 pip 설정을 사용하세요. 다운로드/빌드가 실패하면 원인을 확인하고 실패로 남기며 고정 버전을 임의로 바꾸지 마세요.

앱은 **checkout과 로컬 Python을 참조하는 얇은 alias 실행기**이며 독립 배포·서명·공증된 앱이 아닙니다. 모델 CLI나 Python을 앱에 번들하지 않습니다. 조직이 이런 실행기를 허용하지 않으면 보안을 우회하지 말고 승인 여부를 확인하세요. 앱 생성 없이 실행하려면 `--app`을 생략해 의존성만 설치한 후, 같은 저장소에서 `"$observer_runtime" -m observer.desktop --config "$observer_config" --root "$observer_root"`로 실행할 수 있습니다.

## 4. 작업 공간에 스킬·훅 설치

```sh
"$observer_runtime" "$observer_repo/observerctl" integration install \
  --workspace "$observer_workspace" --root "$observer_root"
"$observer_runtime" "$observer_repo/observerctl" integration status \
  --workspace "$observer_workspace" --root "$observer_root"
```

`installed: true`, `health: "ok"`를 확인합니다. 설치 대상은 선택한 workspace의 `.devin/skills/observer-reporting/`와 `.devin/hooks.v1.json`입니다. 기존 무관한 훅을 보존하고 소유 파일의 변경은 거절합니다. **이 파일에는 머신별 절대 경로가 있으므로 회사 프로젝트 Git에 무심코 커밋하지 마세요.** 기존 Git 정책을 보존하면서 필요하면 로컬 exclude로 관리합니다.

훅은 SessionStart·UserPromptSubmit·PostCompaction·Stop·SessionEnd에서 세션 ID와 작은 문맥을 전달하며 보고를 대신 생성하지 않습니다. 설치 성공은 실제 훅 전달 성공과 별개입니다. 이미 열려 있던 Devin 창이 새 설정을 안 읽으면 진행 중 작업이 끝난 뒤 **해당 테스트 창만** `Developer: Reload Window`로 재로드하세요. Observer가 기존 작업 세션에 ACP로 붙거나 프롬프트를 자동 전송하지 않습니다.

## 5. 첫 수집과 태스크 생성

먼저 승인된 합성 프로젝트에서 Devin **Local 세션을 하나 열어** 간단한 합성 작업 기록을 만듭니다. 앱이나 다른 collector가 실행되지 않은 상태에서 다음을 실행합니다.

```sh
"$observer_runtime" "$observer_repo/observerctl" collect --config "$observer_config" --once
"$observer_runtime" "$observer_repo/observerctl" --config "$observer_config" status
"$observer_runtime" "$observer_repo/observerctl" state --root "$observer_root" create \
  --task observer-smoke --title 'Observer 설치 검증'
"$observer_runtime" "$observer_repo/observerctl" state --root "$observer_root" read --task observer-smoke
"$observer_runtime" -m observer.desktop --config "$observer_config" --root "$observer_root" --snapshot
```

첫 `collect`의 `ok: true`, `status.last_success`와 비어 있는 `last_error`, 태스크 `health: "ok"`를 확인하세요. 일회 수집 후 `status.running: false`는 정상입니다. 기존 `observer-smoke`가 있으면 덮어쓰지 말고 상태를 확인하거나 새 고유 ID를 사용하세요. `--snapshot`은 UI 표시 없이 같은 투영을 읽는 진단이며 **GUI 검증을 대신하지 않습니다**.

```sh
open "$observer_app"
```

상단 상태 막대 아이콘을 눌러 팝오버에서 태스크와 수집 상태·마지막 성공 시각을 확인합니다. 앱 자체가 수집기를 실행하므로 별도 `collect`/웹/메뉴 프로세스를 중복 실행하지 마세요. 브라우저를 열 필요가 없습니다.

## 6. Devin 세션의 보고 시작과 실제 사용

해당 합성 작업 세션에 다음 요청을 전달합니다. 설치 에이전트가 그 Devin 세션이라면 직접 수행하세요. 이 단계는 설치 셸에서 가짜 session ID로 대체할 수 없습니다.

```text
observer-reporting 스킬을 사용해 observer-smoke 태스크에 이 세션을 연결하고 자동 보고를 시작해.
훅이 준 context로 whoami를 먼저 실행해서 실제 session_id를 확인해.
현재 태스크를 읽고, 연결 → 다시 읽기 → 보고 ON → 다시 읽기 순서로 최신 revision을 사용해.
이 합성 작업에서 추적할 항목을 정의하고, 실제 수행한 작업과 확인한 결과만 근거와 함께 보고해.
이미 있는 항목은 안정된 item_id로 갱신하고, 변화가 없으면 보고하지 마.
명시적으로 고정된 값·사용자 메모·View는 보존하고 다른 세션이 쓴 값은 읽은 후 재판단해.
```

동작의 핵심 명령은 다음과 같습니다. `observer_context`, `observer_session`, `observer_revision`은 훅이 전달한 context와 실제 응답에서 얻으며 임의로 만들지 않습니다. `--expected-revision`은 **각 쓰기 직전에 읽은 값**입니다. 설치된 스킬에는 이 Mac의 CLI/root 경로와 보고 스키마가 포함됩니다.

```sh
"$observer_runtime" "$observer_repo/observerctl" state --root "$observer_root" whoami --context "$observer_context"
"$observer_runtime" "$observer_repo/observerctl" state --root "$observer_root" list --session "$observer_session"
"$observer_runtime" "$observer_repo/observerctl" state --root "$observer_root" read --task observer-smoke
# 아직 연결되지 않은 경우에만 link. 실행 후 반드시 read를 다시 한다.
"$observer_runtime" "$observer_repo/observerctl" state --root "$observer_root" link \
  --task observer-smoke --session "$observer_session" --expected-revision "$observer_revision"
# 다시 읽어 observer_revision을 갱신한 뒤 실행한다.
"$observer_runtime" "$observer_repo/observerctl" state --root "$observer_root" reporting \
  --task observer-smoke --session "$observer_session" --enabled on --expected-revision "$observer_revision"
```

에이전트는 보고 ON 뒤 다시 읽고, 변경 항목만 JSON 보고로 작성해 `state validate --kind report --file FILE`, `state report --context CONTEXT --file FILE`로 커밋하고 `state read`로 검증합니다. 보고에는 고유 report ID·실제 session ID·판단에 사용한 revision·item ID·값·상태·근거를 넣습니다. revision 충돌이면 최신 값과 작성 세션을 읽고 재판단합니다. 같은 판단의 revision 숫자만 바꿔 강제 적용하지 않습니다. 전달 결과가 불확실할 때는 같은 ID와 동일 payload로 재전송합니다.

태스크에 다른 세션도 참여한다면 **그 세션에서 같은 시작 절차를 수행**합니다. 태스크–세션은 다대다이며 같은 세션이 다른 태스크에도 기여할 수 있습니다. 각 세션이 무엇을 추적할지 정의하고 마지막 유효 커밋이 현재 값이 됩니다. 항목별 작성 세션·시각·근거·이력으로 다른 세션의 변경을 구분합니다.

사용자는 계속 Devin에서 작업하고 팝오버에서 상황·개입 요청·다음 행동·세션·리소스·수집 오류를 확인합니다. 상단 태스크 관리에서 생성·완료·관측 설정을 조작하고, 메모·핀·확인 위치는 유지됩니다. `데이터 폴더 열기`와 `상태 파일 보기`로 로컬 파일에 접근할 수 있습니다. 리소스 **후보**와 사용자 등록 링크는 구분하며 PR/티켓의 외부 상태는 API로 확인하지 않은 경우 unknown입니다.

보고 중단은 Devin에 `observer-smoke 자동 보고를 중단해`라고 요청합니다. 해당 세션의 contribution만 OFF가 되고 기존 값과 이력은 남습니다. 태스크 완료 또는 관측 OFF도 새 보고를 막습니다. 보고 ON은 유휴 세션을 깨우지 않으므로 진행 중인 작업에서 의미 있는 변화가 생겨야 새 보고가 나옵니다.

## 7. 설치 완료 판정

설치 에이전트가 아래를 실제 확인하고 결과를 사용자에게 짧게 보고하세요. 초기 합성 검증이 통과한 뒤에만 승인된 실제 작업 공간을 설정 목록에 추가하고 그 workspace에도 스킬·훅을 설치합니다. 회사 자료를 이 개인 저장소로 보내지 않는 경계는 그대로 유지합니다.

| 확인 | 통과 증거 |
|---|---|
| 환경·소스 | OS/아키텍처/Python/Desktop 버전, branch·commit 또는 반입 SHA |
| 기본 모델 비활성화 | config에 semantic이 없거나 enabled가 false, 별도 모델 미실행 |
| 읽기 전용 수집 | collect 성공, 마지막 성공 시각, 대상 합성 세션 발견; 경고·누락도 별도 기록 |
| 실제 훅·보고 | 해당 세션 whoami 성공, task 연결·reporting ON, 보고 receipt와 read-back revision |
| 상태 막대 UI | 실제 팝오버에 보고값·작성 세션·시각 표시; snapshot만으로 통과 처리하지 않음 |
| 갱신·보호 | 같은 항목 두 번째 보고가 중복 없이 갱신, 사용자 메모·명시적 핀 보존 |
| 재시작 | 앱에서 종료 후 다시 open, 같은 태스크·보고값·메모·핀·수집 상태 유지 |
| 중단 | 해당 contribution OFF 후 기존 상태 유지, 새 보고 차단 |

검증 로그·화면은 필요 최소한으로 회사 로컬에만 보관하고 원본 DB·대화록·상태·인증정보를 커밋하지 않습니다. 이 README 작성 시 설치 관련 합성 회귀를 확인했지만 **회사 Mac에서의 설치·네트워크·권한·실제 보고는 아직 미검증**입니다.

## 8. 오류·재실행·업데이트

| 증상 | 처리 |
|---|---|
| 원본 DB 없음·대상 세션 없음 | 로그인/Local 세션 여부, 실제 DB 위치와 workspace 절대 경로 확인. 빈 DB 생성·원본 수정 금지 |
| schema/provider 불일치·모호한 기록 | 오류와 마지막 정상 기록을 유지. 실패한 checkpoint를 강제로 넘기거나 원본 구조를 추측하지 않음 |
| `running: false` | 일회 수집 후에는 정상. 앱 실행 중이면 실제 프로세스·오류 확인 |
| 이미 수집 중·서비스 락 | 같은 store를 쓰는 기존 프로세스 확인. 앱의 정상 종료나 해당 collector에 SIGTERM 후 재시도. 락 파일 삭제로 우회하지 않음 |
| 훅 context 없음·옛 경로 | integration status, 대상 창 재로드 후 실제 훅 재확인. 다른 세션 context 검색·ID 추측 금지 |
| Devin 도구 승인 요청 | 조직 규칙대로 해당 명령만 정상 승인. 전체 허용이나 권한 파일 자동 확대 금지 |
| 파일 health가 invalid/external_edit/unavailable | 현재 오류와 last_good 구분. 원본·이력 백업 후 조사; state.json/journal을 임의로 덮어쓰거나 삭제하지 않음 |
| 네이티브 import·패키지·앱 실행 차단 | 선택한 Python·고정 의존성·조직 정책 확인. venv/앱을 삭제해 재설치하기 전 기존 설치 보존 |

조회 명령의 종료값 0만으로 건강 상태를 판단하지 마세요. `read/list`의 health, 수집 응답의 ok, 마지막 성공과 최근 오류를 함께 봅니다. 정확한 Devin 세션 탭 이동·독립 배포/공증·다른 provider 지원·장기 대용량 성능은 현재 보장하지 않습니다. 토큰 표시는 보존 메시지 기준이며 과금 총량이 아닙니다. 보고는 에이전트의 주장으로 저장되며 근거 문자열 자체가 사실 확인을 대신하지 않습니다.

평상시 종료는 팝오버의 종료 기능, 재실행은 `open "$observer_app"`입니다. 설정을 바꾸면 재시작합니다. 로그인 자동 실행은 설치 과정에서 등록하지 않습니다. 현재 `autostart --mode menu`는 이전 NSMenu용이므로 새 팝오버 자동 실행으로 사용하지 마세요.

업데이트는 앱을 종료하고 checkout의 미커밋 변경을 보존한 뒤, 검토한 main을 fast-forward합니다. 기존 개발 브랜치 checkout이라면 변경사항과 main의 포함 관계를 확인한 후 main으로 전환하세요. 실행 중이던 설정과 root를 유지하세요. 코드 업데이트만으로 기존 workspace에 복사된 스킬이 바뀌지 않습니다. integration 소유 파일이 수정되지 않았는지 확인한 후 `integration remove` → `integration install`로 갱신하고 해당 Devin 창이 새 설정을 읽는지 검증하세요. 수정된 파일을 강제로 덮어쓰지 마세요. 기존 관리형 앱의 런처 전환만 필요하면 다음을 사용하며 일반 데이터 이관 명령으로 취급하지 않습니다.

```sh
"$observer_python" scripts/setup_menu.py --upgrade-app "$observer_app" \
  --config "$observer_config" --root "$observer_root"
```

사용을 그만둘 때는 보고를 OFF로 하고 앱을 종료한 뒤 설치한 workspace별로 다음을 실행합니다. 이 명령은 소유 스킬·훅만 제거하며 기존 사용자 수정과 무관한 훅은 보존합니다. 태스크 데이터·checkout·Python 환경은 자동 삭제하지 않습니다.

```sh
"$observer_runtime" "$observer_repo/observerctl" integration remove \
  --workspace "$observer_workspace" --root "$observer_root"
```

## 개발 검증과 상세 문서

핵심 CLI/수집은 표준 라이브러리를 사용합니다. 네이티브 UI 설치와 별도로 저장소에서 회귀 검증할 수 있습니다. 아래 전체 테스트는 네이티브 venv 대신 승인된 원본 Python으로 실행하세요. 현재 모델 어댑터 Mock 테스트가 생성하는 실행 파일의 shebang은 공백이 있는 Python 경로를 처리하지 못합니다. 이 테스트에는 공백 없는 인터프리터 경로와 정상 Git이 필요합니다. opt-in 실모델 테스트는 기본 skip이며 회사에서 활성화하지 마세요.

```sh
OBSERVER_LIVE_CODEX= OBSERVER_LIVE_DEVIN_ACP= "$observer_python" -m unittest discover -s tests
"$observer_python" scripts/check_docs.py
```

가이드 검증 결과: 위 개인 환경의 공백 없는 Python과 정상 Git으로 **414개 실행·412개 통과·실모델 2개 skip·실패 0**, 문서 13개·로컬 링크 81개 오류 0, 셸 예제 11개 문법 검사와 실제 명령 진입점 8개 help 확인. 이 결과는 회사 환경의 설치 통과를 의미하지 않습니다.

| 내용 | 문서 |
|---|---|
| 현재 기능·검증·Git 기준 | [기준선](docs/BASELINE.md) |
| 목적·사용 흐름·기능·구현 | [제품 설명](docs/PROJECT_EXPLAINED_KO.md) |
| 세부 실행·복구·CLI·스키마 | [운영 안내](docs/RUNTIME.md), [CLI 참조](docs/CLI_REFERENCE.md) |
| 앞으로 할 일 | [백로그와 이슈](docs/BACKLOG.md) |
| 전체 문서·작성 규칙 | [문서 안내](docs/README.md) |
