# M3-S02 — 메뉴 막대와 지속 수집 검증

2026-10-30. [M3-S02](M3-S02.md)의 Python 메뉴·수집 실행 부분을 기록한다.
이 문서는 네이티브 객체·프로세스 검증과 실제 화면 클릭 검증을 구분한다.

## 구현

- `observer/runtime.py`는 기존 결정론적 수집기를 별도 프로세스로 실행한다. HTTP 서버와 모델 호출은 없다.
  수집 기본 주기는 5초, 실패 재시도는 최대 60초다. 한 번 수집은 30초 timeout이며 종료 시
  진행 중 자식 프로세스를 중단하고 수집기 락을 해제한다. SQLite 트랜잭션과 기존 체크포인트를 재사용한다.
- 기존 웹 서비스와 같은 `.service.lock`으로 중복 상주 실행을 거절한다. 한 저장소에 웹 서비스·메뉴·
  별도 headless 서비스를 동시에 시작하지 않는다. 메뉴 프로세스에 수집기가 포함되며 별도 서버는 필요 없다.
- `observer/menu.py`의 비GUI 상태 투영과 네이티브 표현을 분리했다. 파일 읽기와 수집 대기는
  작업 스레드에서 수행한다. 상태가 같으면 메뉴를 재구성하지 않는다.
- 메뉴는 수집 상태·마지막 성공·오류, 태스크 revision, 세션별 보고 ON/OFF·마지막 보고·훅 활동,
  추적값·상태·작성 세션·고정·근거, 인간 메모와 View를 표시한다.
- 리소스와 계획은 기존 읽기 전용 관측 연결에서 세션별로 표시한다. 후보의 태스크 소속·PR 상태·
  에이전트의 완료 주장을 검증된 사실로 승격하지 않는다. 기존 이관 View·리소스·확인 위치는 이관 당시 기록으로 표시한다.
- 깨진 파일·외부 직접 편집은 건강 오류와 **마지막 정상 커밋** 표시를 함께 제공한다.
  해당 편집을 현재 유효한 값으로 보이지 않게 하고, 무관한 정상 태스크는 계속 조회한다.
- 메뉴 동작은 지금 수집·새로고침, 상태 폴더·파일의 Finder 표시, 안전 종료다. 브라우저를 열지 않는다.
  보고 ON은 기존 세션을 깨우거나 매 턴 보고를 보장하지 않는다는 안내를 제공한다.

## 실행·설치 경계

Core와 CLI는 기존 Python 표준 라이브러리를 유지한다. 네이티브 메뉴만 선택적 전용 venv에
`rumps==0.4.0`, `pyobjc-framework-Cocoa==12.2.2`를 사용한다. Swift·Electron은 추가하지 않았다.

`scripts/setup_menu.py`를 명시적으로 실행하면 메뉴 환경을 설치한다. `--app PATH.app --config FILE --root DIR`을
추가하면 `py2app==0.28.10`의 alias 모드로 로컬 앱 실행기를 만든다. 이때만 build 의존성을 설치한다.
기존 앱 경로는 덮어쓰지 않는다. 로그인 자동 실행을 등록하지 않으며 설치 과정에 모델 CLI를 포함하거나 다운로드하지 않는다.

alias 앱은 약 114 KiB의 실행기·부트스트랩이며 설치된 Python을 외부 심볼릭 링크로 참조한다.
Python 프레임워크·인터프리터나 Codex/Devin CLI를 앱에 복사하지 않는다. 저장소 checkout, 전용 venv,
개인 설정 경로가 유지되어야 하는 **현재 Mac용 로컬 실행기**다. 재배포용 독립 앱·공증된 배포물이 아니다.

처음 시도한 shell `.app`은 Python 프레임워크의 `org.python.python`으로 실행되어 앱 ID가 보존되지 않았다.
이를 최종 설치 방식에서 제거하고 alias 실행기로 수정했다. 실제 실행의 bundle ID는
`ai.neocjmix.devin-task-observer.menu`, 이름은 `Devin Task Observer`로 확인했다.

명령 수준 진입점은 `python -m observer.runtime --config FILE [--once]`와
`python -m observer.menu --config FILE --root DIR`다. `menu --snapshot`은 GUI·수집을 실행하지 않고
동일한 메뉴 투영을 JSON으로 읽는다. 이미 실행 중인 수집기의 상태는 공유 락과 읽기 전용 DB로 확인한다.
실행 중 설정을 변경하면 기존 수집기 설정과 새 설정을 혼합하지 않고 재시작 필요 오류를 표시한다.

공식 참고: [rumps](https://github.com/jaredks/rumps),
[PyObjC 설치](https://pyobjc.readthedocs.io/en/latest/install.html),
[py2app alias 모드](https://py2app.readthedocs.io/en/latest/tutorial.html#development-with-alias-mode),
[CPython macOS 프레임워크 실행기](https://github.com/python/cpython/blob/main/Mac/Tools/pythonw.c).

## 실제 검증

| 구분 | 확인한 결과 |
|---|---|
| 단위·프로세스 테스트 | `test_runtime.py` 8개, `test_menu.py` 10개, `test_menu_setup.py` 2개: 총 20개 통과 |
| 수집 프로세스 | 실제 subprocess 수집·수동 새로고침·중복 실행 거절·SIGTERM·재시작 후 관측 중복 없음·SQLite integrity_check=ok |
| 원본 보호 | 합성 Devin DB의 실행 전후 SHA-256 동일, 태스크 보고 상태 불변 |
| 장애 복구 | 원본 누락 시 마지막 성공·체크포인트 보존, 지수 대기 상한 내 재시도, 원본 복귀 후 수집 재개; timeout/비정상 종료·출력 거절 |
| 파일 건강 | 직접 편집·JSON 손상은 오류와 last_good로 표시, 다른 정상 태스크는 유지 |
| 실제 Cocoa 객체 | 실행 중 NSStatusItem.isVisible()=true, title `◉`, NSMenu와 중첩 submenu에서 태스크·보고 ON·업데이트 세션·근거·사용자 메모·새로고침·종료 항목을 직접 읽어 확인 |
| 실제 화면 클릭 | 후속 검증에서 사용자가 macOS 자동화·접근성 권한을 승인·인증한 뒤 AppleScript로 상태바 아이콘, 태스크·항목·세션 하위 메뉴를 실제 클릭하고 화면 캡처. 수동 새로고침 이후 0.776초 내 수집 성공, 상태 파일 해시 불변 |
| 최종 로컬 앱 | 개인 Applications에 실행기 설치·실행, 지정된 M3 합성 작업 공간만 관측하도록 기존 설정 연결; service lock 유지·last_success 갱신·last_error 없음 |

네이티브 객체 검증은 임시 합성 태스크 하나에서 실행했다. 합성 값 `3개 통과`는 메뉴 전달 테스트용 보고이며
실제 업무 테스트 결과가 아니다. 부모 작업의 실제 Devin 세션·자발적 스킬 보고 결과는 [M3-S02](M3-S02.md)에 기록한다.
개인 경로·태스크 상태·원본 대화·native 진단 파일·설치된 앱은 Git에 넣지 않는다.

## 남은 제약

- 메뉴에는 최대 50개 태스크·태스크당 50개 활성 추적 항목, 세션별 후보·계획 최대 15개를 표시한다.
  초과·긴 텍스트는 잘림을 표시하고 CLI·원본 상태 파일에 전체 정보를 보존한다. 전체 대형 View 편집 UI는 추가하지 않았다.
- 스킬 활성 여부와 hook 전달 기록은 보고 이행·내용 정확성의 증명이 아니다. 마지막 보고 시각과 출처를 확인해야 한다.
- app/venv/checkout 이동·Python 교체 시 로컬 실행기를 다시 만들 수 있다. 설치가 수집 시작이나 영구 자동 실행 동의의 대체는 아니다.
- native 브리지의 검증 조합은 이 Mac의 Python 3.14·rumps 0.4.0·PyObjC 12.2.2다. 다른 macOS/Python 조합은 미검증이다.

## 후속 실제 화면 검증 — 2026-10-30

최초 CUA의 창 없는 앱 연결 실패와 별도로, 사용자가 승인한 AppleScript/System Events 경로로 실제 UI를 조작했다. 현재 태스크 revision 14, 6개 항목과 핀·메모, 항목 작성 세션과 `reported` 검증 구분, 세션 A의 보고 OFF·마지막 Stop 활동이 표시됐다. 메뉴의 `지금 수집·상태 새로고침`을 클릭한 뒤 수집 간격은 0.819초였고, 기본 5초 주기보다 앞서 성공했다. 상태 JSON의 해시는 동일했다. 이 검증을 위해 제품 코드를 변경하거나 모델을 호출하지 않았다.

로컬 증거는 `~/devin-observer-research-lab/2026-10-30-m3/native-menu-ui-acceptance.json`과 `screenshots/observer-menu-detail.png`, `screenshots/observer-session-off-full.png`다. 캡처에는 개인 데스크톱 배경이 포함되어 Git에는 넣지 않는다. 이 결과는 해당 Mac에서 실제로 조작한 메뉴 범위의 증거이며 모든 macOS 조합·장기 사용성을 보증하지 않는다.
