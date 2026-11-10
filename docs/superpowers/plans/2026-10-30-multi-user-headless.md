# Multi-user Headless Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for isolated tasks; the root implements runtime/integration while a single implementer at a time handles independent file sets. No shared-file edits.

**Goal:** A runnable multi-user SGZ service with admin-created accounts, durable isolated conversations, and official server-side DSH headless.
**Architecture:** React -> authenticated NestJS /api -> bounded headless runs. SQLite stores business records and official native DSH headers/events. Local authenticated capability bridge scopes persistence to one conversation. Explicit SGZ-only tool configuration.
**Tech Stack:** Node >=22.19, TypeScript, NestJS, SQLite, React/Vite, DSH 0.2.1-rc.3, Python 3.11 base worker.
**Spec:** docs/superpowers/specs/2026-10-30-multi-user-headless-design.md

## Global Constraints
- Administrator-created users; no public registration. Server headless; no desktop client, personal credential store or E2B dependency.
- Backend derives all ownership; no native IDs, filesystem paths or model secrets in browser APIs.
- Only SGZ skills and pure calculation exposed to DSH. No shell/filesystem/web/subagent tools.
- One backend instance, persistent SQLite WAL. Keep original unowned browser chats separate; never auto-import them.
- Exact DSH 0.2.1-rc.3. Runtime failure does not invoke another executor or demo.
- Production HTTPS cookie, application mutation header, same-origin policy, hashed passwords/sessions, bounded concurrency and cleanup.

## Review Focus
- Account switch during requests cannot flash another account's conversations (Task 3).
- A user who guesses another conversation ID cannot read/send/delete/cancel it (Task 1).
- Duplicate request after connection loss cannot generate twice (Task 1).
- DSH output exceeding bounded JSON projection remains lossless, restart resumes actual native history (Task 2).
- Shutdown/cancellation while the child is flushing must leave durable terminal state and no orphan child (Tasks 1/2).

### Task 1: Authenticated application backend
Files: server/package.json, server/tsconfig.json (root setup); server/src/contracts.ts (root-owned); server/src/store.ts, auth.ts, app.ts, admin.ts, server/test/api.test.ts (implementer).
Interface: `createApplication({store,runner,publicOrigin?,secureCookies?}): Promise<INestApplication>`, `Store(filename)` with readonly `db: DatabaseSync`, `close()`, `createUser(username,password,role)` (async), and HTTP contract from spec. RuntimeAdapter lives in contracts.ts and is passed in; no production fake executor. Admin CLI bootstraps accounts via stdin and supports listing.
- [x] Write HTTP isolation/auth/concurrency/idempotency tests using real Nest HTTP and a test-only deterministic RuntimeAdapter; see failures.
- [x] Implement schema, password/session handling, strict input validation, ownership queries, SSE and lifecycle handling. All SQL parameterized.
- [x] Verify `npm --prefix server test -- --...` with actual defined script and build; test restart storage and disabled-user revocation.

### Task 2: Pinned headless runtime and durable native persistence (root)
Files: server/src/native-store.ts, server/src/runtime.ts, server/runtime/persistence.mjs, server/runtime/events.mjs (if needed), server/test/runtime.test.ts, server/src/main.ts.
Interface: `HeadlessRunner({rootDir,dataDir,db,model:{baseUrl,apiKey,name},pythonPath?,timeoutMs?})` implements RuntimeAdapter; official plugin reads only a per-run persistence bridge, no direct DB. NativeStore scopes nativeOperation to conversation and run.
- [x] Write native storage scope/ordering/idempotence tests and actual headless integration tests against a local deterministic OpenAI-compatible endpoint; see failures.
- [x] Implement provider using official SessionPersistence helpers; native events transactional and contiguous, reopen restores same session.
- [x] Build explicit headless profile, disable unrelated tools and default personal discovery, register existing SGZ plugin and scoped skills.
- [x] Stream committed native assistant text; reconcile lossless final. Require terminal completed status, propagate failure and cancellation. Bound execution time, child env and process cleanup.
- [x] Verify two-user isolation, two-turn restart resume, combat calculation and long output in actual pinned runtime.

### Task 3: Multi-user web interface
Files: frontend/src/App.tsx, frontend/src/lib/api.ts, frontend/src/lib/api.test.ts, frontend/src/components/AccountPanel.tsx, frontend/src/components/Login.tsx, frontend/src/styles.css, frontend/vite.config.ts and related view types/components.
- [x] Write tests for auth failures, cancellation, SSE and account clearing using server contract.
- [x] Replace direct DSH settings/localStorage history with login, server-owned history, account management, server SSE. Preserve visual hierarchy and responsive layout.
- [x] Remove direct /dsh proxy and arbitrary endpoint/cwd/token controls from active user flow.
- [x] Run frontend test/build, check browser with two accounts and refresh/relogin.

### Task 4: Deployment and review (root)
Files: Dockerfile, compose.yaml, server/.env.example, README.md, frontend/README.md, agent/deploy/*.
- [x] Package server, frontend, DSH, plugin/skills and Python base worker reproducibly; writable persistent volume, non-root runtime, health check.
- [x] Replace bare DSH cloud service launch/package with application service; retain rollback and secret handling boundaries.
- [x] Run deployment tests, full relevant server/frontend/plugin suites and production startup smoke.
- [x] Independent code/security review, fix findings, start current checkout application and verify real process cwd, auth, account isolation and run readiness. Report real-provider configuration separately from local protocol tests.

## Execution rulings and progress
- User selected the service architecture and runtime/account choices; proceed under that authorization without an additional design-approval round.
- Existing clean checkout is the user's intended workspace. Work on a feature branch in place to preserve the running frontend and local environments; no extra worktree.
