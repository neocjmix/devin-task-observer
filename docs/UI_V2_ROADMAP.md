# UI V2 — 구현 범위와 기록 색인

| 항목 | 내용 |
|---|---|
| 문서 유형 | 현재 구현 색인 |
| 상태 | S0–S8 구현; 검증 한계 별도 |
| 기준 | [2026-10-31 고정 기준선](BASELINE.md) |
| 적용 범위 | UI V2 완료 범위 |

## 구현 상태

아래는 앞으로 구현할 계획이 아니라 완료한 범위의 색인이다. 초기 [원문 로드맵](history/UI_V2_ROADMAP_ORIGINAL.md)의 목표와 실제 결과를 구분한다. 정확한 세션 탭 이동·VoiceOver 음성·5–10 live agent 병렬 실행은 완료로 취급하지 않는다.

| ID | 구현 결과 | 경계 |
|---|---|---|
| [S0](slices/UI-V2-S0.md) | 읽기 전용 투영·합성 fixture | 당시 저장 스키마·NSMenu 유지 |
| [S1](slices/UI-V2-S1.md) | NSPopover + 로컬 WKWebView, 홈·검색·건강 상태 | Python/JS 사용; Swift/Electron 없음 |
| [S2](slices/UI-V2-S2.md) | 상세·출처·역할·메모·핀·이력·OCC | 자유 상태에서 사용자 개입을 추정하지 않음 |
| [S3](slices/UI-V2-S3.md) | 등록 리소스·후보 분리·열기/복사 | 외부 상태 unknown, 후보 자동 확정 안 함 |
| [S4](slices/UI-V2-S4.md) | 완료/복원·태스크 자동 반영 | 공유 세션 전역 수집 유지 |
| [S5](slices/UI-V2-S5.md) | 세션 활동·보고·사용량 | 보존 메시지 합계이며 과금 총량 아님 |
| [S6](slices/UI-V2-S6.md) | 앱 활성화·세션 ID 복사 | 정확한 탭 복귀 미지원 |
| [S7](slices/UI-V2-S7.md) | 독립 확인 위치·새 보고·정렬 | 명시적 확인만 기록; 옛 위치 추정 안 함 |
| [S8](slices/UI-V2-S8.md) | Mac 수용·기본 앱 전환·Finder·고정 상단 관리 | 실제/합성 검증 구분; 모든 운영 조건을 통과한 것은 아님 |

## 현재 적용

기본 실행은 `observer.desktop`, NSMenu는 `--legacy-menu`다. [화면 계약](UI_V2_SCREEN_SPEC.md)을 따르고, 최신 테스트와 저장 위치는 [BASELINE](BASELINE.md)에 한 번만 기록한다. 실제 앱의 숨김 홈 경로 이전은 [M3-S05](slices/M3-S05.md)다. 운영·접근성·실사용 검증은 [BACKLOG](BACKLOG.md)로 관리하며 새 UI 슬라이스를 임의로 추가하지 않는다.
