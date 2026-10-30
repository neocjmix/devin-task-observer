# Devin Task Observer

**현재 슬라이스: [M3-S01 — 협조형 보고 CLI와 파일 원본 상태](docs/slices/M3-S01.md), CLI·파일 상태 슬라이스 구현·검증 완료 (2026-10-30).** [슬라이스 목록](docs/SLICES.md)이 현재 범위와 문서 우선순위를 정합니다. 새 태스크의 보고 상태를 파일 원본으로 관리하고, 기존 작업 에이전트가 CLI로 보고하는 경로를 구현했습니다. 메뉴 막대 View·실제 스킬 전달·기존 데이터 이관은 후속 슬라이스입니다.

설계 기준: [변경안](docs/DESIGN_CHANGE_PROPOSAL_2026-10-30_KO.md)과 [낙관적 트랜잭션·업데이트 세션·세션별 추적 정의·명시적 고정값 보호 계약](docs/STATE_TRANSACTION_CONTRACT_2026-10-30_KO.md). 고정하지 않은 추적값은 사람이 수정했어도 후속 유효 보고로 갱신할 수 있고 메모·변경 이력은 항상 보존합니다. 실제 검증 결과는 슬라이스 문서에 기록합니다. 현재 명령과 저장 경계는 [M3-S01 CLI·파일 계약](docs/slices/M3-S01_CLI.md), 에이전트 사용 지침은 [보고 스킬](skills/observer-reporting/SKILL.md)에 있습니다.

아래는 보존 중인 **기존 P0~M2 SQLite·웹·수동 의미 분석 경로**의 사용 기록입니다. 새 파일 상태로 자동 이관하지 않으며 메뉴 막대 구현 완료를 뜻하지 않습니다.

Private, local-first task observability for Devin Desktop. The observer reads the source without controlling Devin and stores task data separately. Collection and dashboard viewing make no LLM calls and send no telemetry.

See [implementation plan](docs/IMPLEMENTATION_PLAN.md).

프로젝트 목적, 사용 흐름, 기능별 구현과 한계는 [한국어 설명 문서](docs/PROJECT_EXPLAINED_KO.md)를 참고하세요.

The P0 feasibility check succeeded for the tested **Devin Desktop 3.10.67 Local** configuration only; see [observations and outstanding gates](docs/P0_VALIDATION.md). A read-only incremental collector, task registry, persistent views and localhost dashboard have been implemented as a prototype. See [P1](docs/P1_PROGRESS.md), [P2](docs/P2_PROGRESS.md), and [P3](docs/P3_PROGRESS.md) for tests and limitations. The source event index is not yet a verified complete chronological transcript. [P4](docs/P4_PROGRESS.md) documents an opt-in, local-only single-evidence suggestion experiment; the legacy experiment is separate from the M2 pipeline below.

Only explicitly selected personal workspaces should be observed. The observer does not archive full transcript bodies. M1 stores extracted resource values and plan item text/status; M2 additionally stores model-generated claims, patches, history and usage as sensitive derived local state, alongside hashes and references; evidence is read from the source on demand and may become unavailable when the source changes. Do not commit session databases, transcripts, credentials or personal data.

## M1 — Useful Observer

After creating a task and linking its sessions, the task screen automatically shows resource candidates, workspace Git facts and separate session plans. New observations update the open review page without replacing unsaved edits. Candidate association and agent-reported completion remain unverified until separately checked. See [M1 implementation and validation](docs/M1_PROGRESS.md).

## M2 — Semantic Task Tracking

With an explicitly enabled model policy, **manual Semantic Refresh** reads only bounded new evidence from linked sessions and updates a separate persistent task-state projection: progress, blockers, decisions, questions, next actions, findings and hypotheses. Human notes, corrections, pins and review cursors are preserved. Routine safe changes auto-apply; uncertain supersession and View structure changes need approval. Read-only viewing and normal Refresh make no model calls. Pending/partial semantics and failures remain visible.

`SemanticModelAdapter` uses an **existing external Codex CLI** for personal development and an approved **Devin ACP** for internal deployment. No CLI binary is bundled or downloaded by Observer. Internal policy rejects Codex and never falls back to it. Actual Codex + synthetic Desktop/browser validation succeeded; after personal browser reauthentication, actual ACP created an independent analysis session, streamed JSON and passed the two-session patch integration test. Dangerous tool denial and internal organizational policy are not production-validated. See [M2 results, token usage, policy and remaining gates](docs/M2_PROGRESS.md).

## Local entry point

The runtime uses the Python standard library (tested with Python 3.14 on this Mac); there is no pip install, cloud backend or desktop runtime dependency. From this checkout:

```sh
./observerctl configure --workspace /absolute/path/to/personal-worktree
./observerctl run
./observerctl status
```

`configure` saves private settings in `~/Library/Application Support/Devin Task Observer/config.json`. Multiple `--workspace` options are supported. Optional `--source`, `--store`, `--port` and `--git /absolute/path/to/git` override the defaults. A missing/unusable Git executable disables Git facts, not Devin collection. The selected workspace must be a Git worktree root for Git facts; source sessions still work without Git. Observe only explicitly authorized personal workspaces.

The dashboard prints its local address (default `http://127.0.0.1:8765`). It shows the last successful collection time and scoped warnings. Ctrl-C or SIGTERM shuts down safely; restart `run` with the same configuration to retain saved tasks, notes, pins, evidence and review positions. Temporary source failures retry with bounded backoff. Unsaved browser drafts survive live updates, but not a full browser reload.

Login auto-start is a **separate explicit opt-in**:

```sh
./observerctl autostart install
./observerctl autostart remove
```

This registers/removes a user LaunchAgent without admin access. macOS may require the user to approve a background item. Ordinary `configure`/`run` does not register auto-start. The agent references this checkout and Python executable, so moving/upgrading them may require re-registering. Private service logs are beside the observer database. Permanent auto-start was not enabled during M1 development; a temporary launchd job was started, killed, observed restarting, and unloaded.

Development regression tests use `python3 -m unittest discover -s tests -q`; Node is needed only for the JavaScript controller tests. Tests that initialize a disposable repository need a working Git executable (optionally set `DEVIN_OBSERVER_GIT` for the test process). On the validation Mac, the system Git is blocked by an unaccepted Xcode license; an already-installed alternative Git was explicitly configured. No license was accepted on the user's behalf.

## Optional semantic configuration

Semantics stays off unless explicitly configured. `observerctl configure` accepts `--semantic-adapter codex_exec|devin_acp|off`, `--semantic-environment personal|internal`, and an existing `--semantic-executable` absolute path. Approved ACP additionally requires `--approve-acp`; the flag is not an authentication or organizational approval substitute. Saved `semantic` policy also supports per-day/per-task call and reserved-token limits. The UI never accepts a provider command from a browser request. Use only personal/synthetic data with personal Codex. See the normative model contract and [M2 limits](docs/M2_PROGRESS.md#실행-정책과-예산).

The real Codex integration test is opt-in via `OBSERVER_LIVE_CODEX` pointing to an existing approved personal CLI. The corresponding personal ACP integration test uses `OBSERVER_LIVE_DEVIN_ACP`. Without these explicit opt-ins the two live tests are skipped. A manual request may make at most one additional budgeted format/target repair call; network/auth/source failures do not automatically retry. No installer runs in tests or application startup. Model-reported tokens are recorded from Codex JSONL or the observed Devin ACP usage shape when available; reservations and call/time/size limits are not a guaranteed provider billing-token ceiling.
