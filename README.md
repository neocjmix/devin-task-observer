# Devin Task Observer

Private, local-first task observability for Devin Desktop. The observer reads the source without controlling Devin; it stores task data separately and does not send telemetry or make LLM calls.

See [implementation plan](docs/IMPLEMENTATION_PLAN.md).

The P0 feasibility check succeeded for the tested **Devin Desktop 3.10.67 Local** configuration only; see [observations and outstanding gates](docs/P0_VALIDATION.md). A read-only incremental collector, task registry, persistent views and localhost dashboard have been implemented as a prototype. See [P1](docs/P1_PROGRESS.md), [P2](docs/P2_PROGRESS.md), and [P3](docs/P3_PROGRESS.md) for tests and limitations. The source event index is not yet a verified complete chronological transcript, and semantic reconciliation is not implemented.

Only explicitly selected personal workspaces should be observed. The observer stores hashes and references, not transcript bodies; evidence is read from the source on demand and may become unavailable when the source changes. Do not commit session databases, transcripts, credentials or personal data.
