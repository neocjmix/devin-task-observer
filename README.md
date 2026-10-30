# Devin Task Observer

**현재 경로: M3 — Cooperative Task Tracking + UI V2.** 기본 관측은 데몬이 모델 없이 수행하고, 의미 변화는 작업 중인 Devin 세션이 스킬과 CLI로 보고합니다. 상태는 로컬 JSON과 이력 파일에 보존하며 macOS 상태 막대의 팝오버에서 조회·관리합니다. [UI V2 로드맵](docs/UI_V2_ROADMAP.md), [M3 실행 결과](docs/M3_PROGRESS.md), [슬라이스 목록](docs/SLICES.md)이 구현과 실제 검증 범위를 정합니다.

설계 기준은 [변경안](docs/DESIGN_CHANGE_PROPOSAL_2026-10-30_KO.md)과 [낙관적 트랜잭션 계약](docs/STATE_TRANSACTION_CONTRACT_2026-10-30_KO.md)입니다. 최신 유효 보고를 사용하고 명시적 고정값·메모·이력을 보호합니다. 각 세션이 추적 항목을 정하며, 충돌의 의미적 조정도 세션이 맡습니다.

## 현재 실행 경로

```sh
./observerctl configure --workspace /absolute/personal/workspace
python3 scripts/setup_menu.py
./observerctl integration install --workspace /absolute/personal/workspace
./observerctl state create --task my-task --title '작업 제목'
"$HOME/Library/Application Support/Devin Task Observer/menu-runtime/bin/python" -m observer.desktop
```

기본 데이터 위치는 `~/.devin-task-observer/`입니다. 설정은 `config.json`, 태스크·이력·세션 문맥·UI 확인 위치는 `state/`, 결정론적 관측 캐시는 `observer.sqlite3`에 둡니다. CLI·수집기·기본 앱은 같은 기본 경로를 사용합니다. `--config`, `--root`, `configure --store`를 명시하면 해당 경로를 사용합니다. 일반 실행이나 `configure`는 이전 Application Support·실험실 경로의 데이터를 자동으로 옮기거나 병합하지 않습니다. 선택적 Python 런타임은 데이터와 별개로 기존 `~/Library/Application Support/Devin Task Observer/menu-runtime/`에 유지합니다.

선택한 Devin 작업 공간의 새 세션에서 `observer-reporting 스킬로 my-task 자동 보고를 시작해`라고 요청합니다. 세션은 훅의 ID를 확인하고 연결·보고 ON·항목 정의를 수행합니다. 이후 중요한 변화는 세션이 판단해 보고합니다. 보고 중지는 같은 스킬로 요청합니다. 스킬은 도구 승인을 우회하지 않습니다. 기본 권한 모드에서는 명령 실행 승인이 필요할 수 있습니다.

`observer.desktop`은 **NSPopover + 로컬 WKWebView**를 기본으로 실행합니다. 화면은 저장소의 HTML/JS이며 외부 웹페이지·HTTP 서버·별도 브라우저가 필요하지 않습니다. 태스크 상세, 항목별 출처와 시각, 메모·이력, 등록 리소스, 연결 세션, 수집 오류와 마지막 정상 기록을 보여줍니다. 사용자 개입은 명시적인 항목 역할과 요청 여부로 표시하며 자유 상태값에서 추측하지 않습니다. 새 보고 확인 위치는 태스크별로 저장하고 사용자가 `여기까지 확인`을 눌렀을 때만 이동합니다.

등록 리소스와 자동 발견 후보는 구분합니다. 태스크 완료·관측 OFF는 해당 태스크의 자동 반영을 멈추며 공유 세션의 전역 수집은 계속됩니다. 세션 사용량은 확인된 로컬 기록의 합계이며 과금 총량을 보증하지 않습니다. 정확한 Devin 세션 탭 이동은 지원하지 않으며, 실행 중인 Devin 앱 활성화와 세션 ID 복사를 제공합니다.

`collect`는 UI 없는 수집기입니다. `observer.desktop --snapshot`은 새 UI의 읽기 전용 JSON 투영을 출력합니다. `observer.desktop --legacy-menu` 또는 기존 `observerctl menu`는 이전 **NSMenu**를 실행합니다. 새 `.app`은 `python3 scripts/setup_menu.py --app "$HOME/Applications/Devin Task Observer.app"`으로 만들 수 있습니다. 기존 관리형 앱은 `--upgrade-app`으로 런처를 확인하고 백업한 뒤 교체합니다. 데이터 위치를 명시적으로 변경할 때는 새 `--config`·`--root`와 정확한 기존 `--previous-config`·`--previous-root`를 함께 지정해야 합니다. 이 옵션은 검증된 실행기의 경로만 바꾸며 데이터 이동은 수행하지 않습니다. 수정된 런처·다른 앱·지정하지 않은 이전 경로는 거절합니다. Python과 Codex/Devin CLI는 앱에 포함하지 않습니다.

일반 설치·실행·앱 전환은 로그인 자동 실행을 등록하지 않습니다. 기존 수집기·NSMenu의 선택적 자동 실행 계약은 [메뉴 실행 문서](docs/slices/M3-S02_MENU.md)에 보존돼 있습니다. 현재 Mac의 기존 로컬 앱 런처를 팝오버 기본 경로로 전환하고 실제 재실행·기존 태스크 표시·첫 수집 후 정상 상태를 확인했습니다. [S8 수용 결과](docs/slices/UI-V2-S8.md)는 합성 데이터의 화면 검증과 실제 개인 합성 세션 관측을 구분하며, VoiceOver 음성 출력과 에이전트 5–10개의 동시 실행은 미검증입니다.

파일 상태 CLI·스키마·편집·복구는 [M3-S01 CLI 계약](docs/slices/M3-S01_CLI.md), 에이전트 명령은 [스킬](skills/observer-reporting/SKILL.md)에 있습니다. 기존 SQLite 자료는 명시적 `migrate plan/apply/check/rollback`으로 새 전용 root에 보존 이관합니다. 기존 DB와 새 root를 동시에 태스크 원본으로 쓰지 않습니다. 이관 이후 새 보고가 있으면 자동 롤백을 거절합니다.

핵심 한계: 보고 ON은 매 턴 보고·완전한 의미 정확성·유휴 세션 깨우기를 보장하지 않습니다. 원본 관측과 세션의 보고를 구분합니다. 개인 테스트만 수행하며 실제 업무 원문·인증정보는 커밋하지 않습니다. 상세 설명은 [한국어 문서](docs/PROJECT_EXPLAINED_KO.md)에 있습니다.

## 보존 중인 P0~M2 경로

아래는 **기존 SQLite·웹 대시보드·수동 의미 분석 경로**의 기록입니다. `run`은 호환성을 위해 기존 웹 서버를 실행합니다. 현재 기본 UI는 위의 `observer.desktop`이며 별도 모델 분석을 사용하지 않습니다.

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

Semantics stays off unless explicitly configured. `observerctl configure` accepts `--semantic-adapter codex_exec|devin_acp|off`, `--semantic-environment personal|internal`, and an existing `--semantic-executable` absolute path. Approved ACP additionally requires `--approve-acp`; the flag is not an authentication or organizational approval substitute. Saved `semantic` policy also supports per-day/per-task call and reserved-token limits. The UI never accepts a provider command from a browser request. Use only personal/synthetic data with personal Codex. See the normative model contract and [M2 limits](docs/M2_PROGRESS.md#실행-정책과-예산).

The real Codex integration test is opt-in via `OBSERVER_LIVE_CODEX` pointing to an existing approved personal CLI. The corresponding personal ACP integration test uses `OBSERVER_LIVE_DEVIN_ACP`. Without these explicit opt-ins the two live tests are skipped. A manual request may make at most one additional budgeted format/target repair call; network/auth/source failures do not automatically retry. No installer runs in tests or application startup. Model-reported tokens are recorded from Codex JSONL or the observed Devin ACP usage shape when available; reservations and call/time/size limits are not a guaranteed provider billing-token ceiling.
