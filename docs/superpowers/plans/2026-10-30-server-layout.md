# Server Layout Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for one API extraction task while root owns non-overlapping file relocation/runtime/deployment work. No shared-file edits.

**Goal:** Make server the sole backend application, with agent/skills/Python inside it and deploy at repository root.
**Architecture:** Thin HTTP app wiring delegates auth and conversation operations; DSH and tool adapters live in agent; Python remains a subprocess package. Preserve business protocol and persistent data.
**Tech Stack:** Existing Node/NestJS/TypeScript/DSH0.2.1-rc.3, Python3.11, React.
**Spec:** docs/superpowers/specs/2026-10-30-server-layout-design.md

## Global Constraints
- Preserve all existing uncommitted work and current user data/config; no commits, reset, remote deployment or game actions.
- No top-level agent/backend duplicate source or compatibility directory symlinks at completion.
- Keep root .state/server, workspaces, .venv and server/.env stable; no new default accounts or data migration.
- server owns a single Node dependency/build boundary; DSH plugin is compiled by server.
- HTTP/SSE/authorization/cancel semantics and bounded runtime permissions unchanged.

## Review Focus
- Compiled DSH/provider/plugin paths work from an extracted release, not only source checkout.
- Python package/config/scripts derive repository paths correctly after deeper nesting.
- Auth split preserves post-hash authorization checks, per-client throttling, ownership and shutdown waiting.
- Deployment includes skills/reference files and worker sources; excludes secrets, caches and legacy desktop launchers.
- Existing native conversation resumes after restart with the same working directory.

### Task 1: Backend module boundaries (implementer)
Own app.ts, auth.ts -> auth/credentials.ts, store.ts -> storage/store.ts, storage-files.ts -> storage/files.ts; new auth/routes.ts, conversations/routes.ts and shared HTTP utilities as needed. Update imports in test/api.test.ts, permissions.test.ts, static.test.ts only. Root owns main.ts/admin.ts/contracts/runtime/native-store and their tests. Consume RuntimeAdapter from src/agent/contracts.ts. Retain Store class API and createApplication export. Extract behavior mechanically; app.ts should wire auth, conversation lifecycle, origin/proxy/body/static HTTP concerns, not contain account or conversation SQL handlers.
- [x] Reuse existing tests as behavior baseline; add only necessary integration regression for new boundary.
- [x] Extract cohesive handlers/lifecycle with shared injected Store/runner; no new route/protocol behavior.
- [x] Verify focused API/permissions/static tests and build once root relocation is ready; report.

### Task 2: Relocate runtime/plugin/skills/Python (root)
- [x] Move runtime/contracts/native-store/provider under src/agent; merge plugin source and tests into server build.
- [x] Move skills and Python package; archive desktop-only scripts/profiles; update repo-root/path discovery and source tests/config fixtures.
- [x] Update main/admin and tests imports, npm/TypeScript build, local editable Python installation; verify actual headless and calculation from new paths.

### Task 3: Deployment/docs/verification (root)
- [x] Move deploy, update Docker/installer/archive whitelist and all active docs/scripts; retain archived historical docs with migration note rather than rewriting dated records.
- [x] Verify server/plugin/Python/deployment/frontend relevant checks and relocated release execution; independently review scoped diff and fix findings.
- [x] Restart local server on existing data and verify auth+actual generation+native continuation, report final tree and limits.

## Verification outcome

- Server + internal plugin: 50 tests passed (including actual DSH headless against a local model-protocol fixture).
- Frontend: 37 tests passed; production build passed.
- Python: 52 selected config/analysis/RPC/dependency/module-boundary tests passed. The optional knowledge-boundary test required locked portalocker 4.4.1 in the test environment; production pure calculation remains dependency-free.
- Deployment: 7 tests passed; installer shell syntax and Compose config passed. Five offline profile scenarios passed.
- Release artifacts: extracted into a path containing spaces; independently installed Python wheel; actual DSH skills/reference loading, calculation, session recovery, isolation and cancellation passed. Compiled application health passed.
- Existing local service: restarted at http://127.0.0.1:3080/ with original data/config; model resumed a conversation created before the move. Health and frontend both respond successfully.
- Final independent review: one Docker build-context local-config exclusion fixed; scoped re-review confirms addressed, no remaining findings. Actual Docker image build/remote installation/Windows game actions were not exercised.
- Current working tree and branch retained; no commits or remote deployment performed. Ignored review snapshot retained because changes remain uncommitted.
