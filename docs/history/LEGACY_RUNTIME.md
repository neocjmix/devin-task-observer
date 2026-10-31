# 보존 중인 P0–M2 실행 경로

| 항목 | 내용 |
|---|---|
| 문서 유형 | 과거 실행 경로 참고 |
| 상태 | 호환 코드 유지; 현재 기본 제품 경로 아님 |
| 기준 | 3ed1955의 README 기록 |
| 적용 범위 | 명시적으로 선택한 legacy 웹·의미 분석 실행 |



아래는 **기존 SQLite·웹 대시보드·수동 의미 분석 경로**의 기록입니다. `run`은 호환성을 위해 기존 웹 서버를 실행합니다. 현재 기본 UI는 위의 `observer.desktop`이며 별도 모델 분석을 사용하지 않습니다.

Private, local-first task observability for Devin Desktop. The observer reads the source without controlling Devin and stores task data separately. Collection and dashboard viewing make no LLM calls and send no telemetry.

See [implementation plan](IMPLEMENTATION_PLAN_P0_M2.md).

프로젝트 목적, 사용 흐름, 기능별 구현과 한계는 [한국어 설명 문서](PROJECT_EXPLAINED_BEFORE_BASELINE_KO.md)를 참고하세요.

The P0 feasibility check succeeded for the tested **Devin Desktop 3.10.67 Local** configuration only; see [observations and outstanding gates](P0_VALIDATION.md). A read-only incremental collector, task registry, persistent views and localhost dashboard have been implemented as a prototype. See [P1](P1_PROGRESS.md), [P2](P2_PROGRESS.md), and [P3](P3_PROGRESS.md) for tests and limitations. The source event index is not yet a verified complete chronological transcript. [P4](P4_PROGRESS.md) documents an opt-in, local-only single-evidence suggestion experiment; the legacy experiment is separate from the M2 pipeline below.

Only explicitly selected personal workspaces should be observed. The observer does not archive full transcript bodies. M1 stores extracted resource values and plan item text/status; M2 additionally stores model-generated claims, patches, history and usage as sensitive derived local state, alongside hashes and references; evidence is read from the source on demand and may become unavailable when the source changes. Do not commit session databases, transcripts, credentials or personal data.

## M1 — Useful Observer

After creating a task and linking its sessions, the task screen automatically shows resource candidates, workspace Git facts and separate session plans. New observations update the open review page without replacing unsaved edits. Candidate association and agent-reported completion remain unverified until separately checked. See [M1 implementation and validation](M1_PROGRESS.md).

## M2 — Semantic Task Tracking

With an explicitly enabled model policy, **manual Semantic Refresh** reads only bounded new evidence from linked sessions and updates a separate persistent task-state projection: progress, blockers, decisions, questions, next actions, findings and hypotheses. Human notes, corrections, pins and review cursors are preserved. Routine safe changes auto-apply; uncertain supersession and View structure changes need approval. Read-only viewing and normal Refresh make no model calls. Pending/partial semantics and failures remain visible.

`SemanticModelAdapter` uses an **existing external Codex CLI** for personal development and an approved **Devin ACP** for internal deployment. No CLI binary is bundled or downloaded by Observer. Internal policy rejects Codex and never falls back to it. Actual Codex + synthetic Desktop/browser validation succeeded; after personal browser reauthentication, actual ACP created an independent analysis session, streamed JSON and passed the two-session patch integration test. Dangerous tool denial and internal organizational policy are not production-validated. See [M2 results, token usage, policy and remaining gates](M2_PROGRESS.md).

## Legacy local entry point

The legacy collector/web runtime uses the Python standard library (tested with Python 3.14 on this Mac); it needs no pip install or cloud backend. The current native UI separately uses optional Cocoa/WebKit Python bridges. To run the retained web path from this checkout:

```sh
./observerctl configure --workspace /absolute/path/to/personal-worktree
./observerctl run
./observerctl status
```

`configure` saves private settings in `~/.devin-task-observer/config.json`; the default observation database is `~/.devin-task-observer/observer.sqlite3`. This retained runtime uses the same current defaults as the native UI. Multiple `--workspace` options are supported. Optional `--source`, `--store`, `--port` and `--git /absolute/path/to/git` override the defaults; a top-level `--config` selects a different settings file. Startup and configuration never automatically migrate previous data locations. A missing/unusable Git executable disables Git facts, not Devin collection. The selected workspace must be a Git worktree root for Git facts; source sessions still work without Git. Observe only explicitly authorized personal workspaces.

The dashboard prints its local address (default `http://127.0.0.1:8765`). It shows the last successful collection time and scoped warnings. Ctrl-C or SIGTERM shuts down safely; restart `run` with the same configuration to retain saved tasks, notes, pins, evidence and review positions. Temporary source failures retry with bounded backoff. Unsaved browser drafts survive live updates, but not a full browser reload.

Login auto-start is a **separate explicit opt-in**:

```sh
./observerctl autostart install --mode legacy-web
./observerctl autostart remove
```

This registers/removes a user LaunchAgent without admin access. macOS may require the user to approve a background item. Ordinary `configure`/`run` does not register auto-start. The agent references this checkout and Python executable, so moving/upgrading them may require re-registering. Private service logs are beside the observer database. Permanent auto-start was not enabled during M1 development; a temporary launchd job was started, killed, observed restarting, and unloaded.

Development regression tests use `python3 -m unittest discover -s tests -q`; Node is needed only for the JavaScript controller tests. Tests that initialize a disposable repository need a working Git executable (optionally set `DEVIN_OBSERVER_GIT` for the test process). On the validation Mac, the system Git is blocked by an unaccepted Xcode license; an already-installed alternative Git was explicitly configured. No license was accepted on the user's behalf.

## Legacy optional semantic configuration

Semantics stays off unless explicitly configured. `observerctl configure` accepts `--semantic-adapter codex_exec|devin_acp|off`, `--semantic-environment personal|internal`, and an existing `--semantic-executable` absolute path. Approved ACP additionally requires `--approve-acp`; the flag is not an authentication or organizational approval substitute. Saved `semantic` policy also supports per-day/per-task call and reserved-token limits. The UI never accepts a provider command from a browser request. Use only personal/synthetic data with personal Codex. See the normative model contract and [M2 limits](M2_PROGRESS.md#실행-정책과-예산).

The real Codex integration test is opt-in via `OBSERVER_LIVE_CODEX` pointing to an existing approved personal CLI. The corresponding personal ACP integration test uses `OBSERVER_LIVE_DEVIN_ACP`. Without these explicit opt-ins the two live tests are skipped. A manual request may make at most one additional budgeted format/target repair call; network/auth/source failures do not automatically retry. No installer runs in tests or application startup. Model-reported tokens are recorded from Codex JSONL or the observed Devin ACP usage shape when available; reservations and call/time/size limits are not a guaranteed provider billing-token ceiling.
