# Devin Task Observer

**현재 경로: M3 — Cooperative Task Tracking.** 기본 관측은 데몬이 모델 없이 수행하고, 의미 변화는 작업 중인 Devin 세션이 스킬과 CLI로 보고합니다. 상태는 로컬 JSON과 이력 파일에 보존하며 macOS 메뉴 막대에서 조회합니다. [M3 실행 결과](docs/M3_PROGRESS.md), [슬라이스 목록](docs/SLICES.md), [스킬·메뉴 검증](docs/slices/M3-S02.md), [보존 이관](docs/slices/M3-S03.md)이 실제 지원 범위를 정합니다.

설계 기준은 [변경안](docs/DESIGN_CHANGE_PROPOSAL_2026-10-30_KO.md)과 [낙관적 트랜잭션 계약](docs/STATE_TRANSACTION_CONTRACT_2026-10-30_KO.md)입니다. 최신 유효 보고를 사용하고 명시적 고정값·메모·이력을 보호합니다. 각 세션이 추적 항목을 정하며, 충돌의 의미적 조정도 세션이 맡습니다.

## M3 실행 경로

```sh
./observerctl configure --workspace /absolute/personal/workspace
python3 scripts/setup_menu.py
./observerctl integration install --workspace /absolute/personal/workspace --root /absolute/observer-state
./observerctl state --root /absolute/observer-state create --task my-task --title '작업 제목'
./observerctl menu --root /absolute/observer-state
```

선택한 Devin 작업 공간의 새 세션에서 `observer-reporting 스킬로 my-task 자동 보고를 시작해`라고 요청합니다. 세션은 훅의 ID를 확인하고 연결·보고 ON·항목 정의를 수행합니다. 이후 중요한 변화는 세션이 판단해 보고합니다. 보고 중지는 같은 스킬로 요청합니다. 스킬은 도구 승인을 우회하지 않습니다. 기본 권한 모드에서는 명령 실행 승인이 필요할 수 있습니다.

메뉴는 상태·출처·마지막 보고·마지막 수집·오류를 보여주고 브라우저나 별도 모델을 실행하지 않습니다. `collect`는 메뉴 없는 수집기, `menu --snapshot`은 동일 화면 데이터의 JSON 진단입니다. 선택적 `.app` 설치와 로그인 자동 실행은 [메뉴 실행 문서](docs/slices/M3-S02_MENU.md)를 따릅니다. 일반 설치나 실행은 로그인 자동 실행을 등록하지 않습니다.

파일 상태 CLI·스키마·편집·복구는 [M3-S01 CLI 계약](docs/slices/M3-S01_CLI.md), 에이전트 명령은 [스킬](skills/observer-reporting/SKILL.md)에 있습니다. 기존 SQLite 자료는 명시적 `migrate plan/apply/check/rollback`으로 새 전용 root에 보존 이관합니다. 기존 DB와 새 root를 동시에 태스크 원본으로 쓰지 않습니다. 이관 이후 새 보고가 있으면 자동 롤백을 거절합니다.

핵심 한계: 보고 ON은 매 턴 보고·완전한 의미 정확성·유휴 세션 깨우기를 보장하지 않습니다. 원본 관측과 세션의 보고를 구분합니다. 개인 테스트만 수행하며 실제 업무 원문·인증정보는 커밋하지 않습니다. 상세 설명은 [한국어 문서](docs/PROJECT_EXPLAINED_KO.md)에 있습니다.

## 보존 중인 P0~M2 경로

아래는 **기존 SQLite·웹 대시보드·수동 의미 분석 경로**의 기록입니다. `run`은 호환성을 위해 기존 웹 서버를 실행합니다. M3의 기본 진입점은 `menu`이며 별도 모델 분석을 사용하지 않습니다.

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
./observerctl autostart install --mode legacy-web
./observerctl autostart remove
```

This registers/removes a user LaunchAgent without admin access. macOS may require the user to approve a background item. Ordinary `configure`/`run` does not register auto-start. The agent references this checkout and Python executable, so moving/upgrading them may require re-registering. Private service logs are beside the observer database. Permanent auto-start was not enabled during M1 development; a temporary launchd job was started, killed, observed restarting, and unloaded.

Development regression tests use `python3 -m unittest discover -s tests -q`; Node is needed only for the JavaScript controller tests. Tests that initialize a disposable repository need a working Git executable (optionally set `DEVIN_OBSERVER_GIT` for the test process). On the validation Mac, the system Git is blocked by an unaccepted Xcode license; an already-installed alternative Git was explicitly configured. No license was accepted on the user's behalf.

## Optional semantic configuration

Semantics stays off unless explicitly configured. `observerctl configure` accepts `--semantic-adapter codex_exec|devin_acp|off`, `--semantic-environment personal|internal`, and an existing `--semantic-executable` absolute path. Approved ACP additionally requires `--approve-acp`; the flag is not an authentication or organizational approval substitute. Saved `semantic` policy also supports per-day/per-task call and reserved-token limits. The UI never accepts a provider command from a browser request. Use only personal/synthetic data with personal Codex. See the normative model contract and [M2 limits](docs/M2_PROGRESS.md#실행-정책과-예산).

The real Codex integration test is opt-in via `OBSERVER_LIVE_CODEX` pointing to an existing approved personal CLI. The corresponding personal ACP integration test uses `OBSERVER_LIVE_DEVIN_ACP`. Without these explicit opt-ins the two live tests are skipped. A manual request may make at most one additional budgeted format/target repair call; network/auth/source failures do not automatically retry. No installer runs in tests or application startup. Model-reported tokens are recorded from Codex JSONL or the observed Devin ACP usage shape when available; reservations and call/time/size limits are not a guaranteed provider billing-token ceiling.
