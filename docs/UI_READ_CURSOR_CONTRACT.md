# 명시적 보고 확인 위치 계약

| 항목 | 내용 |
|---|---|
| 문서 유형 | 현재 데이터 계약 |
| 상태 | UI V2 S7 이후 적용 |
| 기준 | [2026-10-31 고정 기준선](BASELINE.md) |
| 적용 범위 | 사용자 확인 상태; 보고 의미의 검증 아님 |

## 보고 위치

성공한 협조형 보고는 선택적 `task.last_report_revision`을 그 태스크의 새 커밋 revision으로 설정한다. 메모·핀·리소스·관리 설정·관측 활동·UI 확인은 이 값을 바꾸지 않는다. 같은 보고의 영수증 재생도 전진시키지 않는다.

이 필드가 없는 옛 v1 태스크는 위치가 미확인이다. 상태값·작성자·시각·legacy cursor로 추측하지 않고 첫 새 보고에서 확립한다. 그 전에는 확실한 새 보고 수나 보고까지 확인 액션을 제공하지 않는다.

## 별도 사용자 파일

`<state root>/ui/read-cursors.json`은 자체 스키마와 revision을 가진다.

```json
{
  "schema_version": 1,
  "revision": 1,
  "cursors": {
    "task-example": {
      "through_report_revision": 7,
      "updated_at": "2026-10-31T00:00:00+00:00"
    }
  }
}
```

위 예시는 합성 자료다. [스키마](../schemas/ui-v2/read-cursors.schema.json)와 `observer.ui_reads.validate_cursors`로 검증한다. 파일이 없으면 file revision 0의 빈 map이며 조회만으로 생성하지 않는다. 기존 파일이 손상됐으면 빈 정상 map이 아니다.

## 동시성과 멱등

`ReadCursors(root).snapshot()`은 확인 문서를 읽는다. `mark(task_id, expected_revision, through_report_revision, request_id=None)`은 정상 태스크의 표시된 최신 보고 위치만 확인한다. request ID는 응답 대응용이며 위치 자체의 멱등성을 사용한다.

기존 FileStore의 공통 커밋 락과 복구 경계 안에서 정확한 표시 task revision·최신 보고 위치를 검사하고 확인 파일만 내구성 있게 원자 교체한다. **태스크 revision이나 이력을 변경하지 않는다.** 미해결 태스크를 현재 상태로 확인 처리하지 않는다.

이미 확인한 위치의 재시도는 이후 새 보고가 생겼어도 무변경이다. cursor가 후퇴하거나 새 보고까지 삼키지 않는다. 새 보고가 먼저 커밋되면 오래된 확인 요청은 충돌하고, 확인이 먼저면 뒤의 새 보고는 미확인으로 남는다.

## 실패와 범위

태스크 ID가 키이므로 같은 세션을 공유해도 확인 위치는 독립적이다. 손상 JSON/스키마/시각·정상 태스크의 보고 위치를 초과한 값은 `invalid_read_cursors`이며 원문을 보존한다. 태스크가 없어지거나 불건전해져도 기존 cursor를 삭제할 근거가 아니며 확인 액션만 거절한다.

유효한 직접 편집은 사용자 소유 상태다. 단조 증가 보장은 협력 writer에 한하며 파일은 인증 경계가 아니다. cursor 오류가 수집이나 세션 보고를 중단하지 않고 UI에 미확인/오류를 표시한다. 팝오버 열기·탐색·정렬·polling은 확인 행위가 아니다.
