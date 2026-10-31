# UI-V2-S1 — macOS 팝오버 기반

| 항목 | 내용 |
|---|---|
| 문서 유형 | 구현·검증 기록 |
| 상태 | 해당 슬라이스의 당시 결과 (현재 상태는 [기준선](../BASELINE.md)) |
| 기준 | 2026-10-31 · `93e9ddcddbc109666e4c9b93c8da155ee7f74ccf` |
| 적용 범위 | UI-V2-S1: 팝오버 호스트와 기본 탐색 |

## 범위와 기준

브랜치는 `p0/desktop-probe`, Draft PR #1이다. 사용자의 후속 지시로 한 실행에서 S1–S8을 순서대로 완료하도록 허용됐으며, 슬라이스별 검증과 커밋을 유지했다. 아래는 S1 당시 증거이며 후속 구현 여부는 [현재 기준선](../BASELINE.md)을 따른다.

## 구현 결과

420×600pt NSPopover, 로컬 WKWebView 화면, 작업 스레드의 Python 데이터 획득, 태스크 검색, 건강 진단과 마지막 성공 시각을 구현했다. HTTP 서버·모델 호출·외부 웹 콘텐츠는 필요하지 않다. 기존 `observer.menu`는 NSMenu 대체 경로로 보존했다. 당시 태스크 상세는 탐색용 자리 표시자이며 편집과 후속 화면 요구를 구현했다고 주장하지 않았다.

네이티브 NSStackView/NSScrollView와 WKWebView를 모두 실제 생성했다. 긴 텍스트, ID별 DOM 갱신과 익숙한 JS 스택을 위해 WKWebView를 선택하고 모든 상태 접근은 Python에 남겼다. Swift와 Electron은 추가하지 않았다. 기존 Cocoa bridge 옆에 WebKit 12.2.2를 설치했다. 설치된 PyObjC의 정식 WKNavigationDelegate 선언에서 completion-block 서명이 손실되는 문제는 등록된 NSObject selector 메타데이터 사용으로 해결했다. 탐색 검사를 우회하지 않았고 허용된 로컬 로드·script message·다른 파일 탐색 거절을 실제 확인했다.

## 검증 결과

- Python 40개 통과: 팝오버, 메뉴·설치, S0 투영 회귀.
- Node 프런트엔드 assertion 18개 통과.
- 실제 macOS NSPopover: 로컬 페이지 로드, 합성 태스크 7개, 태스크 진입·뒤로 이동, 검색과 새로고침 후 검색 유지. 접근성 트리와 스크린샷으로 확인했다.
- 네이티브 probe: 크기 420×600, 팝오버 표시, WebKit 오류 null, native stack·scroll 객체 생성.
- 큰 나노초 타임스탬프는 JS 경계에서 손실 없는 십진 문자열로 직렬화했다.
- 증거는 로컬 `/tmp/devin-ui-v2/`에 보관했다. 픽스처는 합성이며 실제 Devin 세션을 변경하지 않았다.

## 남은 제약

S1 당시 키보드·포커스, 밝은 모드, 쓰기, 재시작, 5–10개 세션 수용 검증은 완성된 화면의 S8 검증으로 넘겼다. 이 슬라이스에서는 설치 앱의 기본 진입점을 팝오버로 바꾸지 않았다.
