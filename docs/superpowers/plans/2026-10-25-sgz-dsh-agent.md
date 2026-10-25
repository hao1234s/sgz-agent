# SGZ DSH Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Windows agent with an inspectable P0 compatibility check, guarded UI actions, recoverable QR-login/map workflows and a DSH plugin.

**Architecture:** A TypeScript DSH plugin talks to a Python worker over line-delimited JSON-RPC on stdio. The worker owns window binding, vision, jobs and SQLite. Game-specific coordinates/templates are calibrated from observed UI; incomplete calibration must stop actions rather than simulate success.

**Tech Stack:** Python 3.11, pytest, MSS, Pillow, OpenCV, optional PaddleOCR; TypeScript/Node 22; SQLite; installed DSH 0.2.1-rc.3.

**Spec:** `docs/superpowers/specs/2026-10-25-sgz-dsh-agent-design.md`

## Global Constraints

- Only Windows desktop control; the user scans and approves on their phone.
- Single game window, visible foreground client area, no game-private protocol or process memory access.
- Screenshot evidence and deterministic validation precede input.
- Ctrl+Alt+F12 stops new input independently of OCR/LLM work; focus loss pauses.
- First exploration is at most 25 coordinate points; every accepted coordinate has visible evidence.
- Unknown UI, missing calibration, wrong identity or world mismatch causes explicit pause/error.
- Persistent jobs do not resume automatically after process restart.
- Do not read or print DSH credentials; reuse the existing provider configuration.
- No test result may be reported as a successful real-game login or exploration.

## Review Focus

1. Window moves/resizes or focus changes between observation and action: guard and input task tests must reject stale targets before input.
2. A QR page disappears into a loading/unknown page: workflow tests must not claim login success without identity and map evidence.
3. A duplicate job request arrives after reconnect/restart: persistence and RPC tests must return the same job and avoid a second input controller.
4. The same coordinates occur in another season or a target changes type: persistence tests must separate worlds and preserve observation history.
5. Stop arrives during a pending drag or slow recognition: execution tests must release owned buttons and discard subsequent input; bridge teardown must kill/close its child.

## Task 1: Install isolated runtime and implement core contracts

**Files:** `pyproject.toml`, `.gitignore`, `src/sgz_agent/{__init__,models,config,guard}.py`, `tests/test_guard.py`, `config/game.example.json`.

**Interfaces:** `WindowSnapshot(hwnd,pid,exe,rect,foreground,minimized)`; `Observation(id,window,captured_at)`; `ActionGuard.check(observed,current,now,point)` raises `GuardError(code)` on invalid input. `load_config(path)->AgentConfig` validates absolute executable, data path, target identity and UI calibration.

- [ ] Write tests that reject changed HWND/PID/rect, loss of focus, minimized windows, out-of-bounds points and observations older than 2 seconds; valid unchanged target passes.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/test_guard.py -q`, observe missing behavior.
- [ ] Implement contracts and guards; avoid depending on Windows APIs in unit tests.
- [ ] Run guard tests and complete suite, expected all pass.
- [ ] Commit task and record runtime versions.

## Task 2: Windows capture, guarded input, stop mechanism and P0 CLI

**Files:** `src/sgz_agent/{windows,capture,control,cli,__main__}.py`, `tests/test_control.py`, `scripts/Start-Agent.ps1`.

**Interfaces:** `WindowsDesktop.list_windows(exe)->list[WindowSnapshot]`, `snapshot(hwnd)->WindowSnapshot`, `capture(hwnd)->Frame`; `Controller.observe()->Frame`, `click(frame,x,y)`, `drag(frame,start,end)`, `stop()`, `close()`. `StopListener` owns an independent Win32 hotkey message thread. CLI `doctor --config`, `capture --config --output` and `serve --config`.

- [ ] Write tests for stopping before/during a drag, release on failure, refusing stale window state and serializing input ownership; use a narrowly scoped fake OS port for otherwise external input.
- [ ] Run tests and observe expected failures.
- [ ] Implement ctypes API signatures, DPI awareness, client-to-screen geometry, process-path filtering, native mutex, SendInput error handling and hotkey readiness handshake. Capture only the verified target client area.
- [ ] Run tests, then run `doctor` against the configured executable. Distinguish missing window from a failed UI-control connector.
- [ ] If a real game window is visible, capture and inspect it; retain local calibration evidence outside git. No speculative clicks.
- [ ] Commit and record actual P0 outcomes, including incomplete live checks.

## Task 3: OCR and evidence-based UI recognition

**Files:** `src/sgz_agent/{vision,recognition}.py`, `tests/test_recognition.py`, `config/game.local.json` (ignored), `tests/fixtures/` (synthetic only).

**Interfaces:** `TextBox(text,score,box)`; `Recognizer.classify(texts,profile)->ScreenState`; `parse_coordinate(text)->tuple[int,int]|None`; `Vision.read(frame)->list[TextBox]`. Profile anchors use normalized regions and exact/regex text matches; coordinates are never inferred from unlabeled numbers.

- [ ] Test explicit coordinates vs unrelated counts, conflicting coordinate readings, loading/QR-expired/QR-ready/unknown states and absence of identity not implying successful login.
- [ ] Run the tests, then implement parsing and classification.
- [ ] Install a compatible OCR engine only in the project virtualenv; evaluate screenshot crops from the actual client. If model download/recognition is unavailable, report vision as unavailable, never silently substitute fake text.
- [ ] Capture actual UI anchors in the local profile; tests use independent synthetic examples.
- [ ] Run complete suite and commit; record unverified game states.

## Task 4: Durable jobs and login/exploration state machines

**Files:** `src/sgz_agent/{store,jobs,workflow}.py`, `tests/test_store.py`, `tests/test_workflow.py`.

**Interfaces:** `Store.create_job(kind,request_id,payload)->Job`, `get_job(id)`, `save_observation(world,layer,x,y,fields,evidence)->id`, `recover_jobs()`, `export(job_id,format)->Path`; `LoginFlow.next(screen)->Decision`; `ExploreFlow.next(screen,target)->Decision`. Decisions are typed observe/wait/click/navigate/pause/complete operations, checked by the worker before use.

- [ ] Test persistent request deduplication, world isolation, changed-category history and interrupted job recovery to paused.
- [ ] Test QR expiration (at most two refreshes), bounded waits, already-logged-in path, wrong identity, 25-point serpentine ordering, unknown coordinates, pause and completion only after verified details.
- [ ] Observe test failures, implement SQLite transactions and deterministic state logic.
- [ ] Implement normal coordinate-entry UI navigation only if confirmed by real UI. Missing navigation capability returns `calibration_required`; arbitrary coordinate mapping is not pretended to be implemented.
- [ ] Run suite and commit. If calibration blocks integration, keep unaffected persistence/export functionality usable and record the exact blocker.

## Task 5: Worker RPC and installed-version DSH integration

**Files:** `src/sgz_agent/{rpc,service}.py`, `tests/test_rpc.py`, `plugin/{package.json,tsconfig.json,cordis.patch.yml,src/index.ts,src/worker.ts}`, `plugin/test/worker.test.mjs`.

**Interfaces:** JSON-RPC methods `game_status`, `prepare_login`, `observe_map`, `inspect_target`, `explore_region`, `pause_task`, `resume_task`, `export_results`; `WorkerClient.call(method,params)->Promise<result>`, `close()->Promise<void>`. `explore_region` immediately returns `job_id`; state changes are reported via notifications/status.

- [ ] Inspect the installed DSH tool registration API and use that version's public declarations, not guessed example signatures.
- [ ] Test JSON-RPC IDs, malformed requests, errors, concurrent status/pause handling, child exit and timeout without blind mutation retries.
- [ ] Observe failures; implement a worker whose stdin control path remains responsive while OCR/job work runs.
- [ ] Implement DSH tools and plugin teardown; build and run Node tests.
- [ ] Load/validate the bundle in an isolated DSH profile using the existing runtime; do not overwrite the user's desktop profile or credentials.
- [ ] Run combined Python/Node checks and commit.

## Task 6: Live acceptance, documentation and independent review

**Files:** `README.md`, `docs/p0-report.md`, `scripts/Start-Agent.ps1`, `scripts/Test-Agent.ps1`.

- [ ] Exercise actual capture and harmless UI navigation only with verified current target. QR scan remains user action; once logged in validate identity and map coordinate capability.
- [ ] If user authentication or a missing game screen prevents later stages, finish independent code/tests and record exactly what cannot yet be verified.
- [ ] Document setup, configuration, start/stop, tool usage, unsupported UI states, data locations and actual acceptance outcomes.
- [ ] Run full Python suite, plugin typecheck/build, Node tests and worker subprocess smoke test.
- [ ] Request one fresh whole-change review; fix material findings with a reproducing test and a passing suite.
- [ ] Commit the reviewed result and report implemented vs live-verified vs pending separately.

## Execution decisions

- User approved the design and explicitly asked to start. Execute inline in the existing empty workspace without another plan-approval round.
- There is no existing Git repository or branch to isolate. Initialize a local project branch; no external remote or push.
- The bundled Computer Use native pipe is unavailable. Do not fabricate UI observations; use the requested application's own developing capture/doctor capability for P0 and ask the user to open the target game.
- The game registry entry is named “三国志·战略版 模拟器” and points to `E:\sgzzlb\S3Client.exe`. Continue using its Windows window, without ADB.
