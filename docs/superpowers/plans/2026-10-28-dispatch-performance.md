# Dispatch latency repair

User request: explain the latest round's wall time and optimize the agent workflow. Continue within the existing dirty deployed checkout; no additional attacks for timing tests.

Measured evidence: `.state/diagnostics/dispatch-timing-audit-20261028.json`, root transcript lines1753 and2253, durable batch/attack journals. User switch09:29:21.840, batchcreated09:40:56.955, finaldeparture09:49:11.055, reply09:49:43.196 (UTC+8). Total1221.356s; setup695.115s; search/preparation392.410s; sends/confirmation101.690s; reporting32.141s. All six server replies arrived about0.3s after send, while status observations each waited15.05–15.12s.

Narrow design:

1. Preserve all-army preparation before the send phase, current identity/profile/radius constraints, selected-target fresh recheck and send-time fresh preflight. Add lazy near-first candidate windows; stop when one available target is selected, instead of requiring three nearby candidates or individually confirming six unrelated targets. Existing adapters without streaming retain their list interface.
2. Preserve one durable send claim, same-runtime status-only observation and strict departure validation. Poll current status on a bounded early schedule; acceptance still requires clean runtime closure and the exact existing army/target/timing/auto-return proof. Keep unknown outcomes fenced and never resend.
3. Record per-army stage timestamps and discovery diagnostics. Use focused regression tests during each independent edit, one integrated full suite after both sources freeze, one read-only native validation and one managed restart. No new gameplay orders as a benchmark.

Alternatives considered: immediate per-army send changes the user's same-round grouping; parallel native sessions conflict with exclusive lease and the game API. Neither is needed for these measured bottlenecks.

- [x] Streaming search and timing regression tests: focused 174 passed. The real adapter/coordinator offline test performs one discovery window and two selected-target refreshes, with no unrelated-target confirmation.
- [x] Early status polling and unchanged failure/cleanup fences: focused 69 passed. Fake clock: status ready at 0.5s is confirmed at 0.75s; ready at 4s is confirmed at 5s; no valid witness remains unknown at 15s. Exactly one send in each test, clean close required before reconciliation.
- [x] Integrated verification and offline timing comparison: 927 tests passed in 27.93s; git diff --check passed. No live new-dispatch latency claim.
- [x] Read-only live validation and one managed reload: fresh inactive army 347987, normal profile-9 target 451095, real preview 61 charged cells, current preflight reproduced, capability available=true. No command prepared or sent. Managed worker restarted once (PID 27500); logical hashes of every attack/dispatch table unchanged, including existing six accepted attack records and all reservations.

Evidence: `.state/diagnostics/dispatch-performance-readonly-validation-20261028.json` and `.state/diagnostics/dispatch-performance-orders-baseline-20261028.json`. Search timestamps distinguish discovery from preparation; `send_started_at` measures entry into the execute pipeline, including its fresh preflight. The attack receipt separately records status poll count, elapsed confirmation, and response/status timing from the durable claim.

Post-restart managed session turn 9 completed its single read-only `sgz_game_status` check: single_round_dispatch.available=true, missing=[], stop_requested=false, worker child PID 5764. Agent is available and idle. A second logical comparison afterward confirmed all attack/dispatch tables still match the saved baseline.

The current request is explicit execution authorization for these routine performance fixes. Broader NPC support, automatic recurring hunts, recruitment changes and old battle outcome reconciliation are outside this change.
