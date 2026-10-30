# UI-V2-S8 — macOS 수용 검증과 기본 앱 전환

날짜: 2026-10-31 (KST). 시작 커밋: `a57a3dd7614afbf87138b904c8734d044805ed67` (S7).
브랜치: `p0/desktop-probe`, 기존 Draft PR #1 유지. S0–S8 순차 구현을 허용한 사용자 지시에 따른 마지막 슬라이스다.

## 구현

- `observer.desktop`을 기본 진입점으로 제공한다. 로컬 Python/PyObjC + NSPopover + WKWebView를 사용하며 직접 실행 시 팝오버를 표시한다. `--legacy-menu`는 기존 NSMenu, `--snapshot`은 UI 없는 진단이다.
- `scripts/setup_menu.py`는 필요한 얇은 Cocoa/WebKit bridge를 설치한다. `--upgrade-app`은 기존 관리형 py2app alias와 Python/repository/config/state 바인딩을 검증하고, 원래 런처를 백업한 뒤 원자적으로 교체한다. 다른 앱·수정된 런처·다른 데이터 경로는 거절한다. 자동 실행 등록과 Python/모델 CLI 번들링은 하지 않는다.
- 밝은/어두운 모드, 하단 진단·종료, 실제 수집 오류와 마지막 정상 기록 표시를 정리했다. 손상 태스크 때문에 확인 상태를 판단할 수 없는 경우와 확인 위치 파일 자체의 오류를 구분한다.
- 실제 테스트에서 숨겨진 상태바 앱이 종료 요청 후 다음 이벤트를 기다리며 남는 문제를 발견했다. worker/runtime을 종료한 뒤 AppKit 루프를 깨워 정상 종료되도록 수정했고 종료 버튼·SIGTERM 양쪽에서 exit 0을 확인했다.
- `scripts/ui_v2_demo.py`는 기존 대상 경로를 덮어쓰지 않고 8개 합성 source session, 7개 태스크, 다대다 연결, 메모·핀·리소스·확인 위치를 생성한다. 실제 수집기와 같은 경로를 통과하는 재현용 도구이며 개인 원본 데이터가 없다.

## 실제 Mac에서 확인한 동작

Python 3.14.6, PyObjC/Cocoa/WebKit 12.2.2, 로컬 네이티브 호스트에서 테스트했다. 브라우저 에뮬레이션이 아니다.

| 시나리오 | 실제 결과 |
|---|---|
| 홈·태스크·세션 탐색 | 7개 태스크/8개 합성 source session, 검색·필터·스크롤·Escape 뒤로 이동·키보드 포커스 확인 |
| 새 보고와 편집 | 열린 태스크에 세션 8의 새 보고 자동 반영. 작성 중 메모 보존. 오래된 revision 저장은 충돌로 거절하고 명시적 재시도로 저장 |
| 사용자 상태 | 재시작 후 기존·새 메모, 고정 항목, 리소스, 세션별 보고 설정 유지 |
| 확인 위치 | 상세를 열어도 자동 확인하지 않음. 명시적 확인 뒤 새 보고 revision은 다시 미확인. 태스크 revision과 확인 위치는 독립 |
| 완료·복원 | 완료 태스크를 복원하면 이전 자동 반영 OFF 유지. 보관 목록에서 빠지고 활성으로 복귀 |
| 세션 | 마지막 활동/보고 구분, 세션 ID 복사 성공, 한 세션 보고 OFF가 다른 연결의 설정을 바꾸지 않음 |
| 사용량 | 합성 세션 8의 input 2,200 = new 120 + cache 2,080, output 64 표시 |
| 리소스 | 앞선 S3 등록·복사에 더해, 없는 Markdown은 명시적 실패와 복사 제공. 파일 복구 후 기본 편집기 Typora에서 합성 `report.md` 내용까지 확인 |
| 오류·복구 | task-6 현재 JSON 손상 → 마지막 정상 기록; task-7 현재/이력 모두 손상 → 정상 기록 없음. 수정 버튼 비활성화, 정상 태스크 유지 |
| 수집 장애 | 합성 source DB를 일시적으로 이동 → 수집 실패와 `source_missing`, 마지막 성공 시각 유지. 복원·Refresh 후 관측 정상으로 회복 |
| 모드·종료 | light/dark 실제 화면, 종료 버튼 및 SIGTERM exit 0. 재실행 후 상태 유지 |
| 설치 앱 | 기존 `~/Applications/Devin Task Observer.app` 런처 백업·업그레이드. 기존 M3 개인 합성 config/state 경로 그대로 실행. 실제 앱 팝오버와 정상 수집 확인 |

오류 주입은 독립 합성 디렉토리에서만 수행했고 파일을 원복했다. 모델·ACP·Devin 작업 세션 제어는 호출하지 않았다. 기존 설치 앱의 원본 사용자 메모나 태스크는 UI 검증을 위해 변경하지 않았다.

## 실제 Devin 원본과 합성 검증의 경계

설치된 Devin Desktop 3.10.67의 기존 **개인 합성 작업 공간**을 별도로 읽었다. 태스크 1개, 세션 4개 모두 사용량 available, 관측/상태 오류 0개였다. 두 번 acquire해도 usage DB read는 한 번이고 측정 시각이 유지됐다. 첫 acquire는 약 0.0466초였다. 읽기 전후 DB/WAL SHA-256이 같았다(SQLite SHM 조정은 이 비교에서 제외).

이것은 8개 실시간 Devin 에이전트를 병렬 실행한 시험이 아니다. 8개 세션 UI 부하는 합성 SQLite, 실제 저장 구조·사용량 호환성은 개인 합성 Devin 세션 4개로 검증했다. 새 세션에 프롬프트를 보내거나 전체 대화를 외부로 전달하지 않았다.

## 자동 회귀

최종 전체 Python 회귀: **397개 실행, 395 통과, 실모델 opt-in 2개 제외, 실패 0** (52.806초). Node UI V2 helper·DOM·실제 Python Core→JS 경계 회귀 및 `git diff --check` 통과. 설치 런처·demo CLI 통합도 포함한다. 제외된 모델 테스트는 성공으로 계산하지 않는다. 기존 테스트의 일부 SQLite ResourceWarning은 남아 있으며 실패는 아니다.

재현 명령:

```sh
DEVIN_OBSERVER_GIT=/Library/Developer/CommandLineTools/usr/bin/git python3 -m unittest discover -s tests -q
node tests/ui_v2_frontend.cjs
python3 scripts/ui_v2_demo.py /new/isolated/path
# macOS native runtime Python으로 실행
python -m observer.desktop --config /new/isolated/path/config.json --root /new/isolated/path/state
```

## 증거 보관

로컬 `~/devin-observer-research-lab/2026-10-31-ui-v2/evidence/`:

- `s8-home-light.png`, `s8-task-light.png`: 합성 홈·태스크 실제 화면
- `s8-session-dark.png`, `s8-tokens-dark.png`: 합성 세션·사용량
- `s8-errors-dark.png`: 마지막 정상 기록/정상 기록 없음 구분
- `s8-installed-app.png`: 기존 관리형 앱의 새 팝오버 실행
- `native-acceptance.json`: 합성 상태·확인 위치와 수동 수용 결과
- `real-source-acquisition.json`: 실제 개인 합성 Devin 읽기 결과와 DB/WAL 불변 요약
- `s8-final-python.log`, `s8-final-node.log`: 최종 회귀 로그

원본 대화/DB/인증정보는 커밋하지 않는다. 화면의 합성 파서 보고 문장은 과거 세션의 주장으로, 파서 정확성을 이 UI 테스트로 재검증했다는 뜻이 아니다.

## 남는 제약

- 정확한 기존 Devin 세션 탭/창 활성화는 지원하지 않는다. 검증되지 않은 deep link 대신 ID 복사와 이미 실행 중인 Devin 앱 활성화만 제공한다. S6 버튼 클릭은 수행했으나 특정 세션 탭·소유권 전환 성공으로 보고하지 않는다.
- VoiceOver의 실제 음성 낭독, 5–10개 live agent 동시 작업, 장시간 상주·로그인/로그아웃·다른 Mac은 미검증이다. 접근성 트리의 제목/입력/버튼 라벨과 실제 키보드 탐색은 확인했다.
- 사용량은 남아 있는 메시지의 지표다. fork 상속 기록, 삭제·압축된 기록 때문에 과금/평생 총량과 다를 수 있다. 여러 태스크에 배분하지 않는다.
- 과거 태스크에 `last_report_revision`이 없으면 새 보고 개수는 미확인으로 표시한다. 다음 유효한 협조형 보고부터 명확해진다.
- 앱은 이 checkout과 로컬 Python 환경에 의존하는 alias 실행기다. 영구 자동 실행은 설치하지 않았다. 이전 바이너리는 새 optional v1 필드가 채워진 상태를 검증하지 못하므로 상태를 지우지 말고 새 reader를 유지해야 한다.
- 세션 보고의 의미 정확성·누락은 UI가 해결하지 않는다. 본 슬라이스는 보고된 내용과 출처를 보여주며 추가 LLM 분석을 하지 않는다.
