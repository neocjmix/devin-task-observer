# UI-V2-S6 — 세션 복귀 경로 조사와 안전한 대체 동작

| 항목 | 내용 |
|---|---|
| 문서 유형 | 구현·검증 기록 |
| 상태 | 해당 슬라이스의 당시 결과 (현재 상태는 [기준선](../BASELINE.md)) |
| 기준 | 2026-10-31 · `5e17829` |
| 적용 범위 | UI-V2-S6: 세션 복귀 조사와 안전한 대체 동작 |

## 범위와 기준

이 문서는 해당 슬라이스와 명시된 후속 수정의 구현·검증 증거를 보존한다. 아래 본문의 ‘현재’, ‘다음’, ‘미구현’은 각 기록 시점의 표현이며 현재 작업 목록이 아니다. 현재 기능·실행 경로는 [기준선](../BASELINE.md), 미완료 작업은 [백로그](../BACKLOG.md)를 따른다.

## 당시 기록

2026-10-31. **조사 및 안전한 대체 동작 구현 완료.** 기준 커밋 `5e17829`.
정확한 세션 탭 활성화는 미지원이다. 실제 앱 활성화 검증은 S8에서 별도로 기록한다.
기준 요구는 [UI V2 화면 명세](../UI_V2_SCREEN_SPEC.md)의 기존 소유 클라이언트·창·탭
활성화이며, `session/load`, resume, 프롬프트 전송이나 새 세션 생성은 허용하지 않는다.

## 직접 확인한 설치 앱의 정적 증거

설치된 개인 Devin Desktop **3.10.67**의 앱 번들을 읽었다. 인증 저장소·회사 자료는
조사하지 않았고, URL 실행·GUI 조작·ACP 접속·세션 제어·모델 호출은 하지 않았다.
아래 경로는 설치 앱 내부의 논리 경로이며 Observer의 실행 의존성이나 안정된 공개 API가 아니다.

| 위치 | 확인 내용 |
|---|---|
| `Devin.app/Contents/Info.plist` | bundle ID `com.exafunction.windsurf`; URL schemes `devin`, `windsurf` |
| `Contents/Resources/app/product.json` | URL protocol `devin`, application name `devin-desktop` |
| `Contents/Resources/app/out/vs/workbench/workbench.desktop.main.js` | `cYe._handleSessionURL()`과 `CascadeEditorInput._tryLoadSession()` 경로 |
| `Contents/Resources/app/out/vs/workbench/windsurf-chat-client/index.js` | connector별 세션 ID 처리와 Cloud 링크 생성 경로. Local exact-window 보장의 근거는 아님 |

이 버전에는 다음 형태를 해석하는 handler가 존재한다.

```text
devin://acp/session?sessionId=<raw-session-id>&connectorId=<connector-id>
```

`_handleSessionURL()`은 connector에 맞춘 내부 ID로 현재 ACP registry의 세션과 해당
space를 찾은 뒤 `setActiveAgentSpace(...)`를 호출한다. Local 대상이 없으면 오류를
표시한다. Cloud 대상은 웹페이지로 넘어갈 수 있어 초기 Local 범위와 구분해야 한다.

그러나 **URL이 존재한다는 사실만으로 이 기능을 사용하면 안 된다.** 같은 번들의
`CascadeEditorInput._tryLoadSession()`은 editor 구성 과정에서 연결된 per-session 상태가
없으면 `session/load`를 dispatch한다. 즉 특정 창의 세션 목록에 있다는 것과 이미 열린
소유 탭만 안전하게 활성화한다는 것은 다르다. URL에는 owning window를 지정하는 계약도
확인되지 않았다. 화면 전환이 load를 유발하지 않는다는 보장은 이번 정적 조사로 성립하지 않는다.

또한 `/session` 경로가 없는 ACP connector 링크는 `_handleConnectorURL()`에서
`newAgentWindowSession(...)` 경로로 이어진다. 세션 링크가 실패했다고 이 경로로
대체해서는 안 된다. 다른 ACP 클라이언트·다른 Desktop 버전의 동작도 검증하지 않았다.

## 구현된 경계

- 정확한 세션 열기는 **미지원**으로 둔다. 추측한 deep link나 stale locator를 실행하지 않는다.
- 기본 동작은 **원본 세션 ID 복사**다. 내부 connector prefix를 새로 붙이거나 실행 명령으로
  바꾸지 않는다. ID는 세션 상세에서 제공하며 태스크마다 locator를 복제하지 않는다.
- 보조 동작은 **이미 실행 중인 Devin Desktop으로 이동**이다. 실행 중인 앱을 macOS
  running-application API로 확인한 뒤 활성화만 한다. 앱 실행·재시작·URL 열기·CLI 호출로
  대체하지 않는다. 실행 중인 대상이 없으면 안내와 ID 복사를 제공한다.
- 이 보조 동작은 특정 태스크·세션·창·탭을 열었다고 표시하면 안 된다. 여러 인스턴스 등
  대상이 모호하면 임의로 하나를 선택하지 않는다.
- 실패는 해당 버튼 옆에 표시하며 원본 상태와 소유권은 건드리지 않는다.
- 외부 웹 리소스를 여는 S3 기능을 세션 실행 URL 우회 경로로 사용하지 않는다.

## 검증 상태와 완료 조건

| 항목 | 현재 증거 |
|---|---|
| 설치 버전, bundle ID, URL scheme | 설치 앱 파일에서 직접 확인 |
| 세션 URL handler와 잠재적 `session/load` 경로 | 설치 앱 코드의 정적 확인 |
| URL을 통한 정확한 기존 소유 창·탭 활성화 | **미검증·지원하지 않음** |
| 원본 ID 복사와 실패 안내 | Python/JS 테스트 통과; 실제 GUI 확인은 S8 |
| 이미 실행 중인 앱만 활성화 | Python 구현·없는/모호한/실패 대상 테스트 통과; 실제 Mac은 S8 |
| 기존 세션 소유권 보존 | 제어 동작을 수행하지 않았음. 활성화 성공 검증으로 볼 수 없음 |

S6 제품 연결 후에는 복사한 값의 일치, 앱 미실행·모호한 대상·활성화 실패 시 무실행
fallback, 실제 실행 중인 개인 앱 활성화, source/session 상태 불변을 각각 기록한다.
exact activation을 추후 추가하려면 owning client/window/tab의 신선한 식별 근거와
전후 load/resume/prompt·소유권 변경이 없다는 별도 실행 증거가 먼저 필요하다.
현재 문서에는 그 성공을 주장하는 내용이 없다.

## 구현 검증

UI action/native 경계 Python 15개 통과. 연결된 세션의 원본 ID만 복사하며, 실행 중인 정확히 한 Devin.app만 활성화한다. 대상 없음·여러 인스턴스·실패에서 URL/실행/ACP 대체 경로가 없다. `session_locator` capability는 계속 false다.
