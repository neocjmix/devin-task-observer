# Devin Task Observer

Private, local-first task observability for Devin Desktop. The observer reads the source without controlling Devin and stores task data separately. Collection and dashboard viewing make no LLM calls and send no telemetry.

See [implementation plan](docs/IMPLEMENTATION_PLAN.md).

프로젝트 목적, 사용 흐름, 기능별 구현과 한계는 [한국어 설명 문서](docs/PROJECT_EXPLAINED_KO.md)를 참고하세요.

The P0 feasibility check succeeded for the tested **Devin Desktop 3.10.67 Local** configuration only; see [observations and outstanding gates](docs/P0_VALIDATION.md). A read-only incremental collector, task registry, persistent views and localhost dashboard have been implemented as a prototype. See [P1](docs/P1_PROGRESS.md), [P2](docs/P2_PROGRESS.md), and [P3](docs/P3_PROGRESS.md) for tests and limitations. The source event index is not yet a verified complete chronological transcript. [P4](docs/P4_PROGRESS.md) documents an opt-in, local-only single-evidence suggestion experiment; full semantic reconciliation is not implemented.

Only explicitly selected personal workspaces should be observed. The observer stores hashes and references, not transcript bodies; evidence is read from the source on demand and may become unavailable when the source changes. Do not commit session databases, transcripts, credentials or personal data.
