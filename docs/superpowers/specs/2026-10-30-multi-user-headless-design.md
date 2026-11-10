# Multi-user server-managed DSH

## Intent and decisions
The user requires multiple users, selects the Flow architecture, rejects a dependency on the desktop DSH client, and explicitly chooses administrator-created accounts and server-side headless execution (no E2B service is available). Existing React presentation and SGZ pure computation should remain useful.

The selected approach is a NestJS HTTP backend supervising the official pinned DSH headless CLI. Frontend authenticates to this backend; it never receives DSH launch tokens, arbitrary server paths, provider credentials or another user's native session identifier. DSH uses a fresh per-run HOME, a per-conversation workspace, an authenticated persistence bridge, and only explicitly enabled SGZ tools. Native sessions and user-facing messages are separate durable records in the backend database.

## Approaches
The conversation considered (1) standalone DSH Web plus a portable plugin, and (2) a Flow-style application backend plus headless runtime. The user selected (2) for multi-user isolation. Remote E2B execution was discussed and the user selected server-side headless instead. We therefore do not introduce an E2B dependency or replace official DSH behavior with a second agent loop.

## Builder decisions
- Add `server/`: TypeScript, NestJS, Node >=22.19, official DSH 0.2.1-rc.3. This is a separate service; existing Python backend remains tools, not the web backend.
- First deployment is one server process with a persistent SQLite database (Node built-in sqlite, WAL and foreign keys), selected for a runnable local/Linux deployment without requiring new database credentials. Multi-user does not imply multi-replica. A shared MySQL service and horizontal replicas are outside this delivery.
- Admin bootstrap via a local CLI using a password prompt/stdin; no default production password. Admin manages users in the web UI. Passwords are scrypt-hashed with random salt. Login uses opaque random HttpOnly cookie sessions stored as hashes; SameSite=Lax, Secure in production, session expiry, logout/revocation, login throttling. Mutation requests require an application header and same-origin checks; no permissive CORS.
- Model endpoint/key/name are backend environment settings. A missing key produces explicit unconfigured status and rejects model runs; it never falls back to the desktop credential store or a demo response.
- Existing client automation stays out of the server runtime. No shell, filesystem, web, subagent, plugin installation or arbitrary MCP tools are exposed. The native plugin exposes only SGZ skills and pure combat calculation. Skill directories are explicitly scoped to conversation skills.

## Ownership and persistence
Users own conversations, messages and requests. Every list, detail, delete, send and cancel operation derives userId from the authenticated session, then checks the conversation owner. Cross-user IDs return 404. Request bodies cannot select owner, native session ID, model endpoint, workspace or plugin paths.

Store users, auth sessions, conversations, messages, runs, native session headers/events in SQLite. A run has requestId uniqueness per conversation; retrying a request must not generate again. At most one run per user and per conversation, with a configurable bounded global concurrency. Delete is rejected during a run. On startup, previous running rows become interrupted; unfinished responses remain visible. Native headers/events persist through restart, and headless receives the exact stored --session-id for continuation. It must never reconstruct native history by replaying browser messages.

The persistence bridge binds loopback on a random port and uses a per-run random capability token. Its scope is exactly one conversation/run; arbitrary native IDs outside the conversation are rejected. Append batches use stable request IDs and transactional ordering. Official header/event validation is used in the provider. The backend owns SQL; DSH children receive no database path or auth session secrets. Cleanup closes the bridge only after the child has exited and flushed.

## HTTP contract
All endpoints under `/api`. Responses and errors are JSON except the messages SSE endpoint. Mutations require `X-SGZ-Request: 1` and same-origin Origin when supplied.

- GET /auth/me -> {user:{id,username,role}}
- POST /auth/login {username,password} -> {user}; cookie set
- POST /auth/logout -> 204
- GET /status -> {ready,model,execution:'headless',capabilities:string[]}
- GET /admin/users -> {users}; POST {username,password,role?} -> {user}; PATCH /admin/users/:id {disabled?,password?} -> {user}. Protect the active/last admin.
- GET /conversations -> {conversations:[{id,title,updatedAt,status}]}
- POST /conversations {} -> {conversation:{id,title,updatedAt,status,messages:[]}}
- GET /conversations/:id -> {conversation:{id,title,updatedAt,status,messages}}
- DELETE /conversations/:id -> 204
- POST /conversations/:id/messages {text,requestId} -> SSE `data: JSON\n\n`
  - {type:'run',id,userMessage,assistantMessage}
  - {type:'delta',text} / {type:'replace',text} / {type:'status',text}
  - {type:'done',conversation} or {type:'error',message}
- POST /conversations/:id/cancel {} -> {cancelled:boolean}

Message shape: {id,role:'user'|'assistant',content,status:'complete'|'streaming'|'stopped'|'error',error?}. Timestamps are epoch milliseconds. Native IDs never cross the HTTP API. SSE disconnect cancels this run; cancellation waits for process termination and persists terminal status. Official headless JSON output is only a bounded projection, so the lossless final text/native committed events determine stored text. An exit code of zero alone is not proof of a completed turn.

## Frontend
Preserve the current visual layout and responsive composer. Login page replaces connection setup. Account panel contains logout/password change if implemented; admin panel lists/creates/disables accounts and resets passwords. No self-registration. Conversation list and content load from the server. Do not load or auto-import existing unowned browser localStorage chats. Logout clears all in-memory conversation state and aborts pending requests. UI errors distinguish login expiry, unconfigured model, busy capacity, cancellation and generation failure. No arbitrary endpoint/token/cwd inputs and no demo fallback.

## Deployment
Add reproducible lockfiles and Docker build/runtime definition containing server, frontend, SGZ plugin, skills and Python base package. Persistent data volume, environment model configuration, health check, restart policy, non-root execution. Frontend/API can be served by NestJS in production; Vite proxies /api in development. Replace the existing cloud deployment path's bare `dsh web` launch with this application service, including runtime and assets. Old /dsh endpoint must not remain an exposed authentication bypass.

## Acceptance
- Real HTTP tests with separate cookie jars: admin creates users A/B; A's history, details, send/delete/cancel and native storage cannot be accessed by B; ordinary users cannot manage accounts; disabled/reset users lose sessions.
- Password hashes, logout, CSRF/origin, brute force throttling, duplicate requests, run concurrency and disconnect/shutdown cancellation verified.
- Real pinned DSH against a deterministic local model endpoint: native persistence, two-turn resume after service restart, tool execution, long output and failure behavior. Label this protocol test accurately, not a live provider test.
- Frontend tests/build plus browser login, account switch and persistent history checks. Deployment packaging tested without production mutation.
- Real provider test only when server model credentials are available. Report missing credentials as a remaining setup dependency without fabricating responses.
