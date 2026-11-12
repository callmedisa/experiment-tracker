# JOURNAL.md

Format per TZ_dev.md §0.1. Newest at the bottom. Never rewrite earlier entries; add follow-ups.

Note on timestamps: entries 1–N below were all written in one batch right after the first read-through of the spec and `design/`, at about 13:44 local time. They are not minute-accurate; later entries will be stamped when they happen.

## [13:44] decision — Session 1 scope: read + document only
What happened: Owner asked for a read-through of TZ_dev.md and design/, then JOURNAL.md, AI_LOG.md and DESIGN.md only. No application code, no docker, no installs, no edits to TZ_dev.md or design/.
What I expected: n/a
What I did: Read TZ_dev.md fully, every file in design/boards/ (incl. the small dark/mobile wrappers), design/tracker.css, design/canvas.json. Also read TZ_design.md in the repo root (an older design brief, not in the reading order). Did NOT open design/screenshots/*.png. The boards are not renderable here (they reference `./support.js` and a `/_blob/...` stylesheet that are not in the repo), so I judged them from markup only.
Evidence: n/a
Status: resolved

## [13:44] decision — TZ_design.md treated as non-authoritative
What happened: `TZ_design.md` exists next to TZ_dev.md. TZ_dev.md §3 never mentions it.
What I expected: Only TZ_dev.md + design/ to exist.
What I did: Read it. It is the design brief the boards were made from, with no extra behavior requirements. Treated as background only; TZ_dev.md wins on behavior, design/ on looks.
Evidence: /TZ_design.md
Status: accepted-risk

## [13:44] spec-gap — Run page cannot know "stale" or "N s ago" without a trustworthy clock
What happened: §4 says `stale` = running and no event for ≥ 5 min. §5.2 `stats` payload is `{last_seq, count, duplicates_ignored, status}`, with no `last_event_at` and no server time. RunPage design shows a stale banner ("No events for 12 min") and "Last event #N · 0.3 s ago". A run with zero events has no "last event" at all.
What I expected: The fields needed by the screen to be in the API.
What I did: Nothing in code. Proposed (not applied) in DESIGN: add additive `last_event_at` and `server_time` to `stats` and `GET /runs/{id}`/`GET /runs`; compute `stale` on the server (also return the configured threshold); for a run with no events use `created_at` as the baseline.
Evidence: TZ_dev.md §4, §5.2; RunPage.dc.html banners `bStale`, `agoText`
Status: open

## [13:44] spec-gap — IntegrityBar will show false warnings during normal streaming
What happened: §6.2: "Received" = distinct points on the page (updates per event); "Stored" = latest `stats.count`. §5.2: `stats` is sent on connect, every 5 s and "after bursts". Between stats messages Received > Stored, and the design shows a warning icon + signed difference whenever they differ.
What I expected: Equal counters in steady state.
What I did: Proposed: Stored = max(last `stats.count`, highest seq received) (a page cannot receive what is not stored); raise the warning only for Received < Stored that persists longer than one stats interval; define "after bursts" as coalesced, at most 1 per second while seq advances.
Evidence: TZ_dev.md §5.2, §6.2; RunPage.dc.html `intWarn`
Status: open

## [13:44] spec-gap — Verify under live traffic has no defined comparison point
What happened: §5.3/§6.2: Verify = `GET /integrity` returning `{count, checksum, last_seq}`, compared with the page's `count/checksum`. While events are still arriving, the page and the server are never at the same seq, so a naive compare mismatches.
What I expected: A rule for what is compared.
What I did: Proposed: the page computes count/checksum over seqs ≤ response `last_seq`. If the page's `lastSeq` < response `last_seq`, wait for the stream and retry instead of declaring a mismatch. Only a persisting hole counts as a mismatch, and then `GET /seqs` produces `missing`. Note that with a contiguous seq, `/seqs` is always `1..last_seq`, so it is useful only as an independent cross-check.
Evidence: TZ_dev.md §5.3, §6.2
Status: open

## [13:44] spec-gap — Checksum formatting and sort order are not reproducible as written
What happened: §5.3: float formatting "`repr`/`%.17g` identically on server, browser and tests". JavaScript has neither: `String(1e-7)` is `1e-7` while Python `repr` is `1e-07`; `1.0` vs `1`; `-0.0` vs `0`. "Sorted by `point_id`" is undefined across Postgres collation, Python str order and JS UTF-16 order. `crypto.subtle` (SHA-256 in the browser) exists only in secure contexts, so it fails on plain http over a non-localhost host.
What I expected: One canonical form that three implementations can produce.
What I did: Proposed (not applied): serialize `value` as the 16-hex-digit IEEE-754 big-endian bits (identical in Python `struct` and JS `DataView`), sort by UTF-8 byte order of `point_id` in application code (never SQL `ORDER BY`), use a tiny pure-JS SHA-256 if `crypto.subtle` is missing. Restrict `point_id` charset so `|` cannot appear.
Evidence: TZ_dev.md §5.3
Status: open

## [13:44] spec-gap — Reusing a batch_id with different points is silently dropped
What happened: §5.1: "`batch_id` makes a retried batch a no-op returning the original summary". `ingest_batches` stores only counts and seq range, so a different payload under the same `batch_id` (a client bug) is ignored without any signal. That contradicts the stated principle "conflicts are loud" for `point_id`.
What I expected: Same strictness at batch level.
What I did: Proposed schema addition: `ingest_batches.payload_hash`; same `batch_id` + different hash → `409 batch_conflict`, nothing stored. Needs the owner's OK because it adds a column and an error code not in §5.
Evidence: TZ_dev.md §4 (`ingest_batches`), §5.1
Status: open

## [13:44] spec-gap — The seq-assignment recipe as written can leave holes
What happened: §5.1 says `UPDATE runs SET last_seq = last_seq + :n ... RETURNING` serializes batches. But `:n` (number of accepted points) is only known after deduplicating against `UNIQUE (run_id, point_id)` and `ingest_batches`, and dedup done before the lock can race with a concurrent batch containing the same points. A wrong `n` then makes the insert fail or leaves the counter ahead of the rows (a hole).
What I expected: A recipe that is safe under concurrency.
What I did: Proposed order inside one transaction: `SELECT … FROM runs WHERE id AND project_id FOR UPDATE` first; then check status (closed → 409), look up `batch_id`, load existing `point_id`s, compute accepted list; then `UPDATE last_seq += n`; then insert with seq = old+1…old+n. Same lock order for `finish` so a batch racing a finish is decided after the lock.
Evidence: TZ_dev.md §4 invariant, §5.1
Status: open

## [13:44] spec-gap — Duplicate corner cases are undefined
What happened: §5.1 does not say: (a) what happens if one batch contains the same `point_id` twice (identical or different payload); (b) what `first_seq/last_seq` mean in `duplicate_batch` and in `ingest_batches` for a re-split batch that is partly or fully duplicate (zero accepted points means no seq range; the table columns are not marked nullable); (c) how much `duplicates_ignored` grows on a `batch_id` retry; (d) whether a `batch_id` retry on a run that has since closed returns the original reply or `409 run_closed`.
What I expected: Rules for each.
What I did: Proposed: (a) first occurrence accepted, later identical = duplicate, later different = `point_conflict`; (b) make `first_seq/last_seq` nullable, in `duplicate_batch` use the original range for a `batch_id` retry and the accepted range (possibly null) for point-level duplicates; (c) retry adds accepted+duplicates of the original batch (all its points); (d) `batch_id` lookup wins, return the original reply. Also: a batch_id retry writes no new `ingest_batches` row, so the number of retries is not recorded anywhere.
Evidence: TZ_dev.md §4, §5.1, §5.2
Status: open

## [13:44] spec-gap — Optional/nullable `ts` is unspecified
What happened: The example point has `ts`, the table has `client_ts timestamptz`, nothing says whether `ts` is required or what to store when absent. The event log has a "client time" column.
What I expected: A rule.
What I did: Proposed: optional; absent → NULL, shown as "—"; never substituted by `received_at` (that would present server time as client time). `ts` is not part of conflict detection or the checksum, so a retry may carry a different `ts` without a 409.
Evidence: TZ_dev.md §4, §5.1; RunPage.dc.html event table
Status: open

## [13:44] spec-gap — "Overflow" and "10,000 behind" are two different mechanisms; "retention" does not exist
What happened: §5.2 says "bounded per-subscriber buffer; on overflow send resync" and also "RESYNC_THRESHOLD = 10,000 events behind … or a cursor older than retention". Since the stream replays from the DB and NOTIFY is only a wake-up, there is no growing per-subscriber buffer; the only thing that can pile up is the socket write queue. "Retention" is mentioned in §5.2, §8.1 and §11 but no retention job or setting is defined anywhere. At 10 ev/s per run, 10,000 events is about 17 minutes, so the §8.3.3 "paused reader → resync" test would never trip the threshold in a short run. Resync snapshot (§6.2) pages `/events` from 0, which costs the DB as much as replay.
What I expected: One clear definition of "too far behind".
What I did: Proposed: behind = (`last_seq − cursor` > threshold) OR (write queue above N bytes/events for longer than T s); cursor > `last_seq` also forces `resync`; no retention is implemented (events kept forever), so "older than retention" is dead code and documented as such; tests set a small threshold (e.g. 200) via config.
Evidence: TZ_dev.md §5.2, §6.2, §8.3
Status: open

## [13:44] spec-gap — `NOTIFY run_<id>` is unsafe as written
What happened: Postgres folds unquoted identifiers to lower case; run ids are mixed-case base62 (`run_` + 22 chars), so `NOTIFY run_AbC…` and `LISTEN "run_AbC…"` can silently never match. "Each stream wakes" also suggests one LISTEN connection per stream, which eats Postgres `max_connections` (default 100) with a few dozen streams. A dropped listener connection loses wake-ups with no signal.
What I expected: A wake-up mechanism that cannot stall.
What I did: Proposed: one shared channel, `pg_notify('run_events', run_id)`, one listener connection per API process fanning out in memory; each stream also re-reads the DB on its 5 s stats tick, so a lost wake-up delays delivery by at most 5 s and never loses data.
Evidence: TZ_dev.md §5.2
Status: open

## [13:44] spec-gap — How "close streams on revoke within a few seconds" works is unspecified
What happened: §5.2/§7 require closing open streams on revoke/rotate/expiry. No mechanism is given. A revoke can happen in another request (or another process).
What I expected: A mechanism that does not depend on in-process state.
What I did: Proposed: each stream re-validates its session and key (revoked_at, expires_at) from the DB every ≤ 2 s and closes with a final `closed`-like frame carrying no data beyond a generic reason (to avoid a close-reason side channel). Cost is about 25 small queries/s for 50 streams.
Evidence: TZ_dev.md §5.2, §7
Status: open

## [13:44] spec-gap — A "read" key is effectively an admin key
What happened: §5 puts `GET/POST /keys*` behind "session", and sessions come from read keys. So anyone with a read key can create ingest keys, rotate and revoke every key. "Read key cannot ingest" (§8.2) is therefore bypassable in two calls. Also: revoking the last active read key locks everyone out of the UI; and the sign-in page says "Lost your key? Whoever holds the project can rotate it", which is impossible when all read keys are lost (project unrecoverable).
What I expected: Scope semantics that match the Create-key dialog ("read: can sign in and view runs. Can't write.").
What I did: Followed the spec as written (any session may manage keys); proposed a guard that refuses to revoke/rotate-away the last active read key. Recorded as a known weak point in DESIGN.md. The isolation test "read key cannot ingest" will test the direct call only, and the note must say that key management is the bypass.
Evidence: TZ_dev.md §5, §8.2; Keys.dc.html; SignIn.dc.html footer text
Status: open

## [13:44] spec-gap — Rotating your own session key collides with the one-time reveal
What happened: §6.3: rotating/revoking the key of the current session signs the user out with the `revoked` banner. But rotate must show the new key once (KeyRevealPanel). If the session dies at rotate time, the next request (or the keys-list refetch) gets 401 and the §6.1 rule "401 key_revoked anywhere → redirect" fires before the user has copied the new key.
What I expected: The new key to survive until the user dismisses the reveal.
What I did: Proposed: the rotate response carries the new key; the client keeps it in memory, suppresses the 401 redirect and list refetch while the reveal dialog is open, and redirects to `/sign-in?reason=revoked` when "I've copied it" is clicked.
Evidence: TZ_dev.md §6.1, §6.3; Keys.dc.html `doRotate`, `mReveal`
Status: open

## [13:44] spec-gap — Project creation: auto sign-in and error states undefined
What happened: Design "Continue to runs" goes straight to the runs list, but §6.3 says sign-in needs a pasted read key, and the key is exchanged for a cookie. Nothing says whether `POST /projects` also sets a session. There is also no microcopy for create-project errors (invalid name, 429 rate limit, network).
What I expected: A flow from "I've saved both keys" to `/runs`.
What I did: Proposed: on Continue, the UI calls `POST /session` with the read key it still holds in memory, then navigates; the key is dropped from memory afterwards. Create-project errors reuse the key field style and the 429 page copy; exact strings to be added to `strings.ts` and logged as deviations.
Evidence: TZ_dev.md §5, §6.3; SignIn.dc.html, KeyReveal.dc.html
Status: open

## [13:44] spec-gap — Wrong-scope key has no error code
What happened: An ingest key pasted into sign-in (`POST /session`, read key required) or a read key sent to an ingest route: §5 lists `invalid_key`, `key_revoked`, `session_expired`, `run_closed`, 404, 429, but no "wrong scope" code.
What I expected: A defined response.
What I did: Proposed: `401 invalid_key` for both (no extra code, no distinguishing body), but the sign-in UI copy ("That doesn't look like a Tracker key…") is then misleading for an ingest key; a hint like "this looks like an ingest key" can be shown client-side from the `trk_ing_` prefix without asking the server.
Evidence: TZ_dev.md §5; SignIn.dc.html
Status: open

## [13:44] spec-gap — Rate limits will throttle the project's own tests and sit behind a proxy
What happened: §5 limits: `POST /projects` 20/min/IP, `POST /session` and failed key lookups 10/min/IP. Behind the compose proxy every request has the proxy's IP unless X-Forwarded-For is trusted. The isolation suite creates many projects and sends bad keys (revoked, rotated, wrong scope), which can trip 10/min quickly. The design's 429 page says "for this project", which is wrong for the pre-auth per-IP limits.
What I expected: Limits that do not break the evidence-producing tests.
What I did: Proposed: configurable limits via env, relaxed in the test profile (and the relaxation recorded in the results), a trusted-proxy setting for client IP, and a separate generic copy for pre-auth 429.
Evidence: TZ_dev.md §5, §8.2; SystemPages.dc.html
Status: open

## [13:44] spec-gap — Test hook cannot support the claims in §8.1
What happened: §6.2 defines `window.__tracker = { lastSeq, count, checksum, state, missing }`. §8.1 asserts "chart series == DB" and that the UI "passes through live → reconnecting → catching_up → live". The hook exposes neither the chart's series nor the state history; polling `state` can miss a short state.
What I expected: Hook fields matching the assertions.
What I did: Proposed additions (only with `VITE_TEST_HOOKS=1`): `series` (per metric, ordered by step, as drawn) and `history` (array of `{state, t}` appended on every transition).
Evidence: TZ_dev.md §6.2, §8.1
Status: open

## [13:44] spec-gap — Test "DB" ground truth must not be the server's own endpoint
What happened: §8.1 compares page `count/checksum` with `db.count/checksum`. If "db" is `GET /integrity`, the test checks the UI against server code that is itself under test.
What I expected: An independent source of truth.
What I did: Proposed: the test harness reads rows straight from Postgres and recomputes count/checksum/chart-rule series in test code (a second implementation), and separately asserts `/integrity` equals that.
Evidence: TZ_dev.md §5.3, §8.1
Status: open

## [13:44] spec-gap — "proxy" is two things, and "after commit, before response" is not deterministic with a TCP proxy
What happened: §2 lists compose services "postgres, api, web, proxy" and also "toxiproxy (or custom TCP proxy)". Not stated: is `proxy` the same-origin gateway (static `web` + `/api` → api) or the chaos proxy, and in what chain? The §7 CSRF rule "matching Origin" must accept the origin the browser actually uses (through the chaos proxy). Dropping the simulator's POST "after commit but before the response" cannot be timed exactly by a TCP proxy; §8.3.4 (fail mid-batch) needs a server-side fault hook in any case.
What I expected: One diagram of the chain and a deterministic way to lose a response.
What I did: Proposed: browser → chaos proxy → gateway → api, with the Origin/Host allowlist configurable; a test-only fault hook in the API (env-gated, e.g. fail-after-commit and fail-mid-batch switches) as the primary way to produce lost-response cases; the TCP proxy only for connection-level cuts. Keep the hook disabled in the load/verdict runs.
Evidence: TZ_dev.md §2, §7, §8.1, §8.3
Status: open

## [13:44] spec-gap — GapsPanel would be empty in the main scenario
What happened: §6.2: a gap is a seq jump seen by the page, "this page session only". With replay from `after_seq=lastSeq` the page receives lastSeq+1 next, so a disconnect never produces a jump. The design (RunPage, RunStates) shows disconnect windows as gaps going pending → filling 21/37 → filled. §13 #6 settled late events, not this.
What I expected: The panel to show something after a Wi-Fi drop.
What I did: Proposed: record each disconnect→catch-up window as a gap (`from = lastSeq+1`, `to` known from the first `stats` after reconnect, statuses pending/filling/filled), plus real seq jumps as before (status `open` if `/events` cannot fill them). §8.3.1 "zero gaps after catch-up" is then defined as: final `missing` is empty, not "panel is empty".
Evidence: TZ_dev.md §6.2, §13 #6; RunPage.dc.html `gaps`
Status: open

## [13:44] spec-gap — Design has a sixth connection look and a first-load flash
What happened: RunPage shows a "Stream closed" chip for finished runs; ConnectionChip/§6.1 define exactly five states. §5.2 `closed` ordering vs replay is not stated. On first page load the stream opens with `after_seq=<snapshot last>` and the first `stats` could briefly show "Catching up 0/N".
What I expected: Five states only, no flicker.
What I did: Proposed: "Stream closed" is derived (run status ≠ running and `closed` received), not a sixth machine state; the server sends `closed` only after replaying up to the final `last_seq`; "Catching up" is shown only for reconnects, never for the first connect.
Evidence: TZ_dev.md §5.2, §6.1; RunPage.dc.html `cDone`; Components.dc.html props
Status: open

## [13:44] spec-gap — "Last used: 2 s ago / now" vs "updated at most once per minute"
What happened: Keys board shows "2 s ago", "now". §5.1 limits `last_used_at` writes to once per minute per key. A read key is only used at sign-in, so its "last used" is the last sign-in, not browsing activity.
What I expected: Consistent granularity.
What I did: Proposed: display "last used" rounded to minutes ("< 1 min ago"); keep the throttle.
Evidence: TZ_dev.md §5.1; Keys.dc.html
Status: open

## [13:44] spec-gap — Python client API shape is not pinned, and the curl snippet needs a run id
What happened: §13 #1 says `client/tracker.py` provides `tracker.start/log/finish` and the UI snippet "must match exactly". The design shows `run = tracker.start(key=…, run="my-first-run")`, `run.log(step, loss=…, accuracy=…)`, `run.finish()`. Spec says module-level names; the design uses an object. `POST /runs {name}` returns an id, but the design curl posts to a run by name. A runnable curl needs a first step to create the run and then use the returned id.
What I expected: One signature.
What I did: Proposed: follow the design (`tracker.start(key, run) → Run`, `Run.log(step, **metrics)`, `Run.finish(status="finished")`); curl snippet shows `POST /runs` then `POST /runs/$RUN_ID/metrics`. Owner to confirm before coding.
Evidence: TZ_dev.md §2, §13 #1, §6.3; Components.dc.html, KeyReveal.dc.html, RunsList.dc.html
Status: open

## [13:44] spec-gap — Default point_id makes the chart rule almost unreachable
What happened: Default `point_id = '{metric}:{step}'` and "same point_id, different value → 409". So with default ids a corrected value for an existing (metric, step) is a conflict, and the §5.3 chart rule ("highest seq per (metric, step)") can only matter when clients send custom `point_id`s. If the E2E workload uses default ids only, the chart-rule half of the claim is never exercised.
What I expected: The test workload to cover the rule.
What I did: Noted for §8.1: the workload must include custom `point_id`s that share (metric, step) with different values, in addition to default ids.
Evidence: TZ_dev.md §5.1, §5.3, §8.1
Status: open

## [13:44] spec-gap — `.trk` root uses `container-type: inline-size`, which may break `position: fixed`
What happened: tracker.css sets `container-type: inline-size` on `.trk`. By the CSS containment spec this applies layout containment, which makes the element the containing block for fixed-position descendants. Dialogs/scrims/toasts using `position: fixed` would then be positioned against `.trk` (as tall as the whole page), not the viewport. This is from my knowledge of the spec, not tested here.
What I expected: Fixed dialogs and toasts to stay in the viewport.
What I did: Nothing yet. Plan to verify in a browser when implementing; fallback: make `.trk` a viewport-height scroll container (`height: 100dvh; overflow: auto`) so "fixed" means "visible area" without editing tracker.css.
Evidence: design/tracker.css line 28; Keys.dc.html scrim uses `position: absolute` inside the board
Status: open

## [13:44] concern — GET /runs aggregates may be too heavy for a 2 s poll
What happened: §5/§6.3: up to 500 runs, each with `events_count`, `last_event_at`, `primary_metric`, `latest_value` and a 30-point `spark`, one query, polled every 2 s per open tab. In the verdict workload that is 50 runs × 6,000 events after 10 min, and sparkline downsampling scans the primary metric's rows.
What I expected: Cheap list reads.
What I did: Nothing yet. Option: keep `last_event_at`/`latest_value`/`primary_metric` denormalized on `runs` (updated in the ingest transaction, which already holds the row lock) and compute `spark` from a bounded tail. Needs measurement before anyone calls it fine.
Evidence: TZ_dev.md §5, §6.3
Status: open

## [13:44] concern — Scope vs the 5–7 h budget
What happened: P0 alone covers backend auth/keys/sessions/ingestion/SSE, a React app with about 25 components and a five-state connection machine, virtualized table, custom chart with downsampling, zoom and keyboard navigation, chaos proxy, E2E with random seeds, a statistical timing test, five silent-failure tests, a 10-minute load test with latency/lost/duplicate/CPU/memory measurements, and four written documents.
What I expected: A set that fits 5–7 h.
What I did: Flagged to the owner in the session summary. No estimate was measured; this is a judgment from the task list.
Evidence: TZ_dev.md §3 (tiers), §8, §10
Status: open

## [13:44] concern — Timing-based isolation test and the load measurements can be flaky or misleading
What happened: §8.2 asks for a statistical timing comparison inside docker (shared CPU, jitter). §8.4 needs end-to-end stream delay, lost and duplicate events "measured, not assumed", plus DB CPU and API memory, for 10 minutes.
What I expected: Reliable numbers.
What I did: Nothing yet. Intent: pick the tolerance from a measured baseline, keep all raw samples in `results/`, and count loss/duplicates by comparing sent vs stored vs received per run with independent code.
Evidence: TZ_dev.md §8.2, §8.4
Status: open

## [14:54] decision — Owner accepted all earlier proposals; they are now binding
What happened: Owner replied to the session-1 summary: accept ALL `[P]` proposals in DESIGN.md and ALL proposals in the `spec-gap` entries above, with the notes below. TZ_dev.md is NOT edited; the accepted proposals override it where they differ (the earlier entries stay as written).
What I expected: n/a
What I did: Accepted proposals now binding (each refers to the spec-gap entry of the same title): stale/last_event_at/server_time; IntegrityBar Stored = max(stats.count, highest seq); Verify compares at response last_seq; checksum canonical form (IEEE-754 hex, UTF-8 byte sort, restricted point_id charset); `ingest_batches.payload_hash` + 409 batch_conflict; lock-first seq recipe; duplicate corner cases (a)-(d); optional/nullable ts; one definition of "too far behind", no retention; shared pg_notify channel + 5 s re-read; 2 s stream re-validation; guard for last read key; rotate-reveal handling; project-creation sign-in; wrong-scope = 401 invalid_key; env-configurable rate limits + trusted proxy setting; test-hook additions; independent test ground truth; env-gated fault hook; gaps from catch-up windows; "Stream closed" derived; last-used in minutes; Python client follows the design; custom point_ids in the E2E workload.
Evidence: owner message after session 1
Status: resolved

## [14:54] decision — Owner's notes on the accepted proposals
What happened: Owner qualified four proposals. (1) Read key as admin (spec-gap "A read key is effectively an admin key"): implement ONLY the guard against revoking the last active read key; key management stays available to sessions; NO new admin scope; the weakness is documented in NOTE.md. (2) container-type / position:fixed: verify in a real browser once the UI exists, then log the result. (3) Fault hook: yes, env-gated, server-side; must be disabled in load/verdict runs and the results must say so. (4) Rate limits: yes to env-configurable values; record the values used in every results/ run.
What I expected: n/a
What I did: Following them. Interpretation to confirm: `rotate` always leaves one new active key of the same scope, so the "last read key" guard is needed on `revoke` only; rotate cannot lower the count of active read keys.
Evidence: owner message
Status: resolved

## [14:54] decision — P0 UI scope cut
What happened: Owner cut P0 UI scope: use uPlot (no custom SVG chart); no zoom, no keyboard point-stepping, no downsampling in P0; keys page and dark theme move to P1. Keys API stays complete. Five connection states, IntegrityBar, Verify, event log and runs list stay in P0.
What I expected: n/a
What I did: Nothing in code yet (UI is step 4). Note: this reduces the budget risk I flagged ("custom SVG chart") but not the load/chaos/timing-test risks.
Evidence: owner message
Status: resolved

## [14:54] decision — Session 2 scope: TZ_dev.md §10 step 2 only
What happened: Owner asked for: tighten DESIGN.md to about 600 words (no [P] markers, one plain "what this guarantees" sentence per section, no shorthand), then ONLY schema + Alembic migration, auth (projects, keys, sessions, rate limits), run creation, batch ingestion with the lock-first transaction, pytest API tests, docker compose with postgres + api only; raw test output into results/. No SSE, no UI, no chaos proxy, no load test. Then STOP and report.
What I expected: n/a
What I did: Starting. Deferred on purpose: SSE/`duplicate_batch` emission and `NOTIFY` (step 3), `GET /runs` list with aggregates and `/events`, `/integrity`, `/seqs` (step 3/5).
Evidence: owner message
Status: resolved

## [15:00] problem — pytest could not import the app package
What happened: First `docker compose run --rm -T api pytest`: `ModuleNotFoundError: No module named 'app'` while loading tests/conftest.py.
What I expected: pytest to find /app/app (working dir is /app).
What I did: Added `pythonpath = .` to backend/pytest.ini (rootdir is not put on sys.path for a tests/ dir without __init__ in this setup).
Evidence: no raw file kept (setup error before any test ran)
Status: resolved

## [15:01] problem — Run id containing a NUL byte gave 500 instead of the uniform 404
What happened: First full run: 64 passed, 1 failed. `test_foreign_missing_and_malformed_run_ids_are_indistinguishable`: `GET /runs/run_%00` returned 500 `internal_error` (log: `unhandled DBAPIError`). Reproduced directly with asyncpg: `CharacterNotInRepertoireError: invalid byte sequence for encoding "UTF8": 0x00`. Postgres text cannot hold NUL, so the query itself blew up.
What I expected: Every malformed id to give the same 404 body as an unknown id (TZ_dev.md §5, §8.2).
What I did: Added `_lookup_id()` in routes_runs.py: ids that are not `[A-Za-z0-9_]{1,64}` are looked up as the empty string, so they still do one DB query and take the same code path (rather than an early return with different timing). Same for every run route that takes an id. Key ids already go through UUID parsing and give 404.
Evidence: results/api-tests-20261111-first-run-1-failure.txt (raw failing run, kept)
Status: resolved

## [15:02] problem — flaky test: concurrent revokes of the last two read keys
What happened: The full suite passed twice, then `make test-api` failed once: `test_two_concurrent_revokes_cannot_remove_the_last_two_read_keys` got a 401 from one revoke (expected [200, 409]). Raw output kept.
What I expected: The same result on every run.
What I did: Found the cause by reading the test: both revokes used one session whose own key (the default read key) was one of the two being revoked. If that revoke commits first, the other request's authentication fails with 401 key_revoked. The guard itself was not at fault. Rewrote the test: two read keys, each with its own session, each revoking the other; the assertions are "never both 200" and "at least one active read key remains in the DB" (checked directly in Postgres). Then ran the test 25 times in a row (see next entry).
Evidence: results/api-tests-20261111-150202.txt (the failing run; the fixed run is a later file)
Status: resolved

## [15:05] problem — API container crashed on start; tests had not caught it
What happened: `docker compose up -d --build api`, then curl to :8000 returned 000 for every request. `docker compose logs api`: `alembic upgrade head` died with `ModuleNotFoundError: No module named 'app'` from alembic/env.py (`from app.models import Base`). 65 pytest tests were green at the time.
What I expected: The stack that the tests exercise to be the stack that `docker compose up` starts.
What I did: Cause: the `alembic` console script does not put the working directory on sys.path; my test fixture ran `python -m alembic`, which does. Added `prepend_sys_path = .` to backend/alembic.ini so both invocations work. Re-ran the smoke test (next entry).
Evidence: results/smoke-compose-*.txt (the earlier of the two files is this failure; kept)
Status: resolved

## [15:06] surprise — Removing the row lock did NOT break the "concurrent distinct batches" contiguity test
What happened: Mutation check M1 (delete `.with_for_update()` on the run row) failed 3 tests (identical concurrent batch, overlapping batches under different ids, conflicting writers) but `test_concurrent_distinct_batches_keep_seq_contiguous_and_blocks_unbroken` still passed. Reason: `UPDATE runs SET last_seq = last_seq + n` itself takes the row lock and serialises counters, so with distinct points seqs stay contiguous even without the up-front lock.
What I expected: Removing the lock to break contiguity under concurrency.
What I did: Nothing to fix. It means the lock-first order protects the dedupe decision (which points are new), not the counter. The three tests that fail without the lock are the ones that prove it. Also noted that tests alone cannot show the lock is *needed* for contiguity; they show it is needed for exactly-once.
Evidence: results/mutation-checks-20261111.txt (M1–M4: each deliberate breakage was caught by 1–5 tests)
Status: accepted-risk

## [15:06] decision — Choices made while implementing step 2 (not in the spec)
What happened: Small choices the spec leaves open. (1) Enums are TEXT + CHECK constraints, not Postgres ENUM types (simpler migrations). (2) `sessions.id` = HMAC(pepper, cookie token): the token itself is never stored. (3) A missing cookie gives 401 `session_expired`; a revoked key behind a session gives 401 `key_revoked` (sessions are not deleted on revoke, so the UI can show the "revoked" banner). (4) Wrong-scope, missing header and malformed header all give the same 401 `invalid_key`. (5) `point_id`, `metric` and `batch_id` are limited to `[A-Za-z0-9._:/-]` (no `|`, no spaces), 128/64/128 chars. (6) `step` is a strict integer 0..2^62; `value` a strict finite number (strings and booleans rejected). (7) Naive `ts` is read as UTC. (8) The batch content hash is order-sensitive, covers point_id/metric/step and the IEEE-754 bytes of value, and ignores `ts`. Identical means identical bytes, so `0.0` vs `-0.0` is a `point_conflict`. (9) `GET /runs/{id}` was added now (needed to test 404 uniformity and `stale`), with `last_event_at`, `server_time`, `stale`, `stale_after_s`. (10) `rotate` cannot lower the number of active read keys, so the last-read-key guard is only on `revoke`. (11) Fault hook = request header `X-Fault: mid-batch|after-commit`, honoured only when FAULT_HOOKS=1. (12) `POST /projects` returns `{project, keys:{ingest, read}}`; key objects carry the full `key` once.
What I expected: n/a
What I did: Implemented as above; each has at least one test.
Evidence: backend/app/*.py, backend/tests/*.py
Status: resolved

## [15:06] spec-gap — Error codes the implementation needed that §5 does not list
What happened: §5 lists invalid_key, key_revoked, session_expired, rate_limit (429), run_closed, point_conflict. The implementation also needs: `validation_error` (422, with `fields`), `payload_too_large` (413), `csrf_failed` (403), `batch_conflict` (409, approved earlier), `last_read_key` (409), `already_revoked` (409), `rate_limited` (429 body code), `internal_error` (500), `not_found` (404), `http_error` (405 etc.). `csrf_failed` is a 403 even though the spec says "never 403": that rule was about foreign resources, so I kept it for CSRF only.
What I expected: A complete list of codes for the UI strings.
What I did: Chose the names above; they must be added to the UI's error mapping in step 4.
Evidence: backend/app/errors.py, auth.py, routes_*.py
Status: open

## [15:06] concern — The failed-lookup limiter is per IP, so one bad client can lock out good ones
What happened: `RL_AUTH_FAIL_PER_MIN` counts bad, revoked and wrong-scope keys per client IP and, once over the limit, answers 429 to every request from that IP, even with a valid key (tested: `test_failed_key_lookups_are_limited_per_ip`). Without a trusted-proxy setting every client behind the future gateway shares one IP.
What I expected: n/a
What I did: Followed the spec (10/min/IP). `TRUSTED_PROXY_HOPS` exists and is tested with X-Forwarded-For, but the gateway that sets it does not exist yet. Must be set correctly in step 3+ compose, or one noisy client blocks everyone.
Evidence: backend/tests/test_auth.py (rate-limit tests)
Status: open

## [15:06] concern — What the step-2 tests do NOT show
What happened: All 65 tests drive the app in-process (httpx ASGITransport) against a real Postgres. Not covered: real HTTP/uvicorn behaviour (only one curl smoke test, results/smoke-compose-*-after-fix.txt), client timeouts, a proxy in front, more than one API process, timing equality of 404s (no timing test yet), log contents (no test greps logs for keys yet), RLS, expired-session clean-up (the table only grows), and throughput: one batch costs about 6 round trips under the run lock and nobody has measured the per-run limit.
What I expected: n/a
What I did: Listed for NOTE.md; the owner decides which become tests in steps 5–7.
Evidence: backend/tests/
Status: open

## [15:06] concern — DESIGN.md length
What happened: Asked for about 600 words. `wc -w DESIGN.md` says 766, but that counts every bullet dash, backtick token and symbol as a word; running prose is closer to 600–650. I did not measure that separately.
What I expected: ~600.
What I did: Cut from 974 to 766 in two passes; stopped there so the sections stay readable.
Evidence: DESIGN.md
Status: accepted-risk

## [15:16] problem — my integrity test posted an invalid batch and did not notice
What happened: `test_integrity_matches_an_independent_computation...` failed on every run of the full suite with `assert (0 == 6)`. The batch used `point_id="é"`, which the approved charset (`[A-Za-z0-9._:/-]`) rejects with 422, and the test never checked the POST status, so it compared an empty run.
What I expected: The checksum test to exercise non-ASCII ordering.
What I did: Test now asserts the POST returned 200 and uses ids that are legal but sort differently in byte order than in a locale (`Zeta` < `_under` < `alpha`). Consequence worth knowing: because `point_id` is ASCII-only, the "UTF-8 byte order vs JS UTF-16 order" worry from the checksum spec-gap cannot occur; byte order and code-unit order are identical for ASCII.
Evidence: results/api-tests-step3-repeat8-20261111-151440.txt (8 identical failing runs, kept)
Status: resolved

## [15:41] surprise — Ingest batches got slower as a run grew, then suddenly fast again (missing planner statistics)
What happened: While checking the slow-reader test I saw 0.21 s per 1000-point batch against 0.024 s in an earlier measurement. A probe (`backend/tests/probe_ingest_latency.py`) showed 15 batches at 0.018 s, then a jump to 0.23 s growing linearly to 0.49 s (batches 16–32), then back to 0.02 s. EXPLAIN in that window: the dedupe lookup `point_id = ANY(1000 ids)` used a Bitmap Scan on `ix_metric_events_run_metric_step` with only `run_id =` as the index condition, i.e. it read every row of the run and filtered; `pg_class.reltuples = -1`, `last_autoanalyze = NULL`. With no statistics Postgres assumed a tiny table. It recovered when autoanalyze ran.
What I expected: Constant cost per batch (the unique index on (run_id, point_id) exists).
What I did: Replaced the lookup with `unnest(ids) CROSS JOIN LATERAL (SELECT ... WHERE run_id=:r AND point_id=u.pid LIMIT 1)`, which is one index probe per id by construction and does not depend on statistics. Probe afterwards: flat 0.017–0.033 s for all 40 batches. Note for the load test: each fresh DB has this problem class for its first minutes; the other hot query (max step per metric) showed an Index Only Scan Backward and was fine.
Evidence: results/probe-ingest-latency-20261111.txt (before, with EXPLAIN), results/probe-ingest-latency-20261111-after-lateral-fix.txt (after)
Status: resolved

## [15:41] problem — slow-reader test: first run failed its speed assertion
What happened: `test_slow_reader_gets_resync_and_ingestion_is_not_slowed` failed: `0.2099 < 0.0975` false. The resync itself worked (gap-free prefix, then `resync`). The comparison was unfair: the baseline was 6 batches into a fresh table (0.024 s each) and the measured run was 40 batches (hitting the planner problem above).
What I expected: n/a
What I did: Kept the failing output. Made the baseline 40 batches as well; with the same size and growth, a stuck reader costs about 16% (0.274 s vs 0.317 s per batch, measured before the LATERAL fix). Final threshold in the test: reader case must be < 3x baseline + 50 ms. This is a loose bound; stream and ingest share one process and one event loop in this test.
Evidence: results/api-tests-20261111-step3-slow-reader-failure.txt
Status: resolved

## [15:41] decision — Step 3 backend choices
What happened: (1) `POST/finish` call `pg_notify('run_events', json)` inside their transaction (delivered only on commit). One fixed lower-case channel; payload `{"r": run_id, "dup": {...}}`. (2) One listener connection per process (`hub.py`), started lazily on the first stream, auto-reconnect; reconnect wakes every stream. (3) Streams wake on notify or every min(2 s, 5 s), so a lost wake-up costs at most 2 s. (4) The stream ends silently (no frame) on revoke/rotate/expiry, checked every REVALIDATE_S (2 s). (5) Slow readers have no buffer: the generator blocks on the socket, then checks lag on resume and sends `resync` if over RESYNC_THRESHOLD. (6) `closed` is sent after the last replayed event, preceded by a fresh `stats`. (7) `/events`, `/seqs`, `/integrity` added (`up_to_seq` parameter on integrity for Verify). Checksum line = `point_id|metric|step|<16 hex digits of the IEEE-754 bits>\n`, sorted by UTF-8 bytes of point_id in Python. (8) Tests use a real uvicorn on a local port because httpx ASGITransport cannot stream SSE.
What I expected: n/a
What I did: As listed. `duplicate_batch` is live-only through a per-stream deque of 100.
Evidence: backend/app/hub.py, streaming.py, tests/test_stream.py
Status: resolved

## [15:41] concern — A reader that stops reading forever holds its stream task
What happened: With no write timeout, a client that never reads (but keeps the TCP connection open) leaves the generator blocked on the socket indefinitely: one task and one socket per such client, no DB connection held. Not tested beyond the 40-batch pause.
What I expected: n/a
What I did: Nothing. Candidate fix: a send timeout (close the stream after N seconds of no drain).
Evidence: n/a
Status: open

## [15:41] concern — Step 3 tests do not cover
What happened: Not covered: the browser side (EventSource behaviour, auto-reconnect headers, the state machine; none exists yet), a real proxy buffering SSE (`X-Accel-Buffering` is set but nothing in front of the API yet), multiple API processes (the hub is per process; `NOTIFY` does reach all of them, but rate limits do not), `resync` snapshot reload cost, and `/integrity` memory use for very large runs (it loads all rows of the run into Python). The "no events lost" test uses one seed (7) and 600 events; the E2E test with several seeds is step 5.
What I expected: n/a
What I did: Listed for NOTE.md.
Evidence: n/a
Status: open

## [15:41] concern — Mutation S3 shows the two resync paths are tested unevenly
What happened: Disabling the in-loop lag check was caught only by the slow-reader test; the connect-time check has its own test. Disabling the connect-time check was not tried.
What I expected: n/a
What I did: Noted only.
Evidence: results/mutation-checks-step3-20261111.txt (also contains two empty attempts: macOS has no `timeout`, then a test that hung instead of failing; both fixed in the tests, not hidden)
Status: accepted-risk

## [16:04] problem — /runs rendered a blank page for ~20 minutes of debugging
What happened: After the first sign-in in the browser, `/runs` showed an empty page (root element empty, no console error, no `/api/session` call). The same bundle rendered the landing page fine. I first suspected a render crash and added an error boundary and an in-page error capture; both stayed silent. I then looked at the Vite dev server (fine) and read the route table: I had added `<Route path="/runs/" element={<Navigate to="/runs" replace />} />`. React Router ignores trailing slashes, so that route matched `/runs` itself and rendered only a `Navigate` (renders nothing).
What I expected: A trailing-slash redirect that only fires for `/runs/`.
What I did: Removed the route. The error boundary stays (the design has a 500 page and a blank screen on any future render crash is worse). Lesson for the evidence: nothing in the unit tests renders the router; this was found only in the browser.
Evidence: no raw file (debugging session in the browser pane); fix is in web/src/App.tsx
Status: resolved

## [16:04] problem — connection watchdog ignored a stats message received at time 0
What happened: Unit test `two missed heartbeats...` failed: the watchdog used `lastStatsAt` truthiness, so a stats message at t=0 counted as "no stats yet". With real epoch milliseconds it could never be 0, so no user-visible bug, but the check was wrong.
What I expected: n/a
What I did: Explicit sentinel (`-1` = none) in web/src/lib/connection.ts.
Evidence: first failing vitest run was not saved to a file (fixed within the same minute); later runs in results/web-unit-tests-*.txt
Status: resolved

## [16:04] surprise — a JavaScript number cannot hold every step the server accepts
What happened: While writing the checksum fixture I noticed `step` was accepted up to 2^62, but the browser parses JSON numbers as doubles (exact only up to 2^53-1). A step above that would silently round in the page and the checksum would mismatch while the server is right.
What I expected: Both sides to agree on the integer range.
What I did: Server limit lowered to 2^53-1 (backend/app/routes_runs.py). The fixture contains a step of 2^53-1. Value (double) parity with Python is tested with awkward doubles: 0.1+0.2, 1e-7, -0.0, 5e-324, 1.797e308.
Evidence: web/test/checksum.test.ts, web/test/fixtures/checksum-vectors.json (generated by the server's own function), results/web-unit-tests-*.txt
Status: resolved

## [16:04] decision — Step 4 choices and deviations from the design/spec
What happened: (1) The SPA owns /runs, /keys, so the API is mounted under `/api` by an nginx gateway (`web` service, port 8080); the API runs with TRUSTED_PROXY_HOPS=1. Port 8000 stays published for local tests only. (2) The connection layer has a sixth internal state `connecting` (first page load only, shown with the reconnecting look and the text "Connecting…") so the first load never shows "Catching up". (3) Owner scope cut applied: no Keys page, no theme toggle or dark theme, no zoom, no keyboard point-stepping, no downsampling, no mobile drawer; side nav shows Runs only. (4) The event log renders the newest 200 rows in a normal table, not a virtualized list. (5) The chart is uPlot; late points get a warn ring for 5 s (not the design's 2 pulses). (6) Sparkline = every n-th point of the primary metric, ignoring the "highest seq per step" rule. (7) Snapshot on page load = `GET /runs/{id}` then `/events` in pages of 5,000 from seq 1, then stream with after_seq=cursor. (8) "N s ago" uses the server's `server_time`/`last_event_at` from `stats` (clock skew corrected). (9) IntegrityBar: Stored = max(stats.count, highest seq received); the warning shows only after the shortfall lasts more than 6 s. (10) One-time keys live in a module variable only; reload loses them (verified: nothing in localStorage/sessionStorage). (11) Sign-in field is type=password and rejects an ingest key client-side. (12) Verify = wait until the page cursor reaches the server's last_seq (max 10 s), compare count and checksum over seq <= last_seq, on mismatch read /seqs for the missing list.
What I expected: n/a
What I did: As listed. Strings are in web/src/strings.ts; a few are new (create-project errors, ingest-key hint).
Evidence: web/src, results/ui-manual-checks-20261111.md
Status: resolved

## [16:04] spec-gap — #25 (container-type vs position:fixed) could not be verified yet
What happened: The owner asked to verify in a real browser once the UI exists. The P0 UI uses no `position: fixed` element (dialogs and toasts are P1; toasts are rendered inline), so there was nothing to observe. Not verified.
What I expected: n/a
What I did: Left open. To verify when the first dialog or fixed toast is added.
Evidence: n/a
Status: open

## [16:04] concern — The Python snippet shown to users does not exist yet
What happened: The key-reveal page shows `pip install tracker-client` and `tracker.start(key=..., run=...)`, `run.log(step, loss=...)`, `run.finish()` exactly as in the design, but client/tracker.py has not been written and no `tracker-client` package exists. The spec says the snippet must match the client exactly.
What I expected: n/a
What I did: Snippet follows the owner-approved shape (design's object API). When the client is written, either it matches this snippet or the snippet is edited; the `pip install` line is design copy and is false until packaged (or removed).
Evidence: web/src/pages/KeyReveal.tsx
Status: open

## [16:04] concern — What the step-4 checks do NOT show
What happened: Evidence for the UI is 21 unit tests (checksum parity with the Python server, chart rule, state machine with a fake EventSource and fake timers) plus one manual pass in the built-in browser (results/ui-manual-checks-20261111.md). Not covered: real network failures (offline was simulated by window events, not a cut socket), several seeds, a mismatch scenario, long runs (largest seen: ~340 events), 5,000+ point charts (every event triggers a full rebuild of the aligned arrays, throttled to 10/s; unmeasured), the cost of Verify and snapshot load on a 50,000-event run (sorting and hashing happen on the main thread), mobile and dark theme, accessibility, other browsers. A short "catching up" is shown before "resync" because stats arrives first.
What I expected: n/a
What I did: Listed for NOTE.md; step 5's Playwright + chaos proxy test is where most of this should be asserted.
Evidence: results/ui-manual-checks-20261111.md, results/web-unit-tests-*.txt
Status: open

## [16:43] surprise — first E2E run was too weak to mean anything, although it passed
What happened: Seed 1 passed on its first run. The evidence file showed why that proved little: the simulator finished in about 10 s (14 batches, 315 points), so 3 of the 5 scripted connection cuts hit nothing (`open=0`), there were no same-batch resends and only 2 re-split resends. Only 2 cuts had hit a live stream.
What I expected: A pass to mean the faults were really injected.
What I did: Larger workload (400 steps, about 840 points, 40–55 batches, pace up to 0.35 s), cuts continue for as long as the simulator runs, and the test now asserts that at least 3 cuts hit a live stream and that responses were really lost after processing. The per-seed evidence file records what was injected.
Evidence: results/e2e/run-20261111-162558.txt (first full run), results/e2e/seed-N.json
Status: resolved

## [16:43] problem — E2E first full run: seeds 3 and 4 failed my own "chaos was real" assertion
What happened: With 5 seeds, 3 passed and 2 failed at `expect(cuts on a live stream).toBeGreaterThanOrEqual(3)` (received 2). All correctness assertions for those seeds (database contiguous, page == database, checksum, chart rule, state history) passed before that line.
What I expected: Every seed to cut at least 3 live streams.
What I did: After a "block" cut the next cut often landed while the page was still backing off. The test now waits for the page to be live before each cut, and block durations are 1–2 s. Re-run: all seeds pass. The failing run's raw output is kept; its per-seed JSON files were overwritten by later runs.
Evidence: results/e2e/run-20261111-162558.txt
Status: resolved

## [16:43] problem — E2E mutation checks: first attempt invalid, and my first diagnosis was wrong
What happened: To see the E2E test fail, I broke the server (replay skips an event) and rebuilt only the API: the test failed with `SyntaxError: Unexpected token '<', "<html>` (an HTML error page where JSON was expected). I diagnosed "nginx keeps the API container's old address after a restart", rewrote nginx.conf to re-resolve DNS every 5 s, and only then looked at the API log: the API container had crashed with `ValueError: invalid literal for int()` because in zsh `env $ENVV docker compose ...` does not word-split the variable, so all settings went into RL_PROJECTS_PER_MIN as one string. The 502s were the API being down. The stale-DNS theory is unproven: I never tested the old nginx config against a recreated API.
What I expected: n/a
What I did: Kept the nginx change (re-resolving upstreams is harmless and plausibly useful; after it, recreating the API gave 201 through the gateway), but I do not claim it fixed a problem. Redid the mutation runs with exported variables. The invalid first attempt is kept next to the valid one.
Evidence: results/e2e/mutation-checks-e2e-20261111.txt (invalid), results/e2e/mutation-checks-e2e-redo-20261111.txt (valid)
Status: resolved

## [16:43] surprise — a browser that silently drops events was NOT caught, because the page healed itself
What happened: Mutation "page ignores every 50th event" inside RunStore.applyEvent: the E2E test PASSED (page count 840 == database 840). The bundle served really contained the mutation. The gap-fill path (a seq jump makes the page fetch the hole from /events, applied through a different code path) repaired every dropped event. I then mutated RunData.apply, which every path goes through: the test failed (missing 50, 100, 150, ...). Also: the server mutation (replay skips one event) failed the test with `missing: [1]`.
What I expected: The first mutation to fail the test.
What I did: Nothing to fix; this is the design working, and the second mutation shows the test can fail. It also means the test cannot tell "stream delivered everything" from "stream lost events and the page repaired them"; the gaps panel (jump gaps) is where repairs would show.
Evidence: results/e2e/mutation-checks-e2e-redo-20261111.txt
Status: accepted-risk

## [16:43] problem — chaos counters are cumulative and the "responses lost" assertion used them
What happened: The first 8-seed table showed 141 resets for seed 1: the chaos proxy's counters persist for its lifetime, so my per-seed table and the assertion `sim.lost > 0` were using cumulative numbers (the assertion could pass on earlier seeds' losses).
What I expected: Per-seed numbers.
What I did: The test now stores the proxy status before and after each seed and asserts and reports deltas. Re-ran all 8 seeds.
Evidence: results/e2e/summary-8-seeds.md, results/e2e/run-20261111-163654.txt (cumulative version), results/e2e/run-20261111-163948.txt (final)
Status: resolved

## [16:43] decision — Step 5 design choices
What happened: (1) Own TCP chaos proxy (chaos/proxy.py, stdlib) instead of toxiproxy: scripted modes drop / block / flaky; "lost after processing" = forward the request, and when the first response byte returns from the server cut the client without relaying it (the server HAS processed the request). Two listeners: browser :8081 -> web -> api, simulator :8082 -> api. (2) The client opens one connection per request (no keep-alive) so a cut affects exactly one request. (3) Batch ids are content hashes, so resending the same batch (after a crash too) is the same id. (4) `tracker.start()` is not idempotent: a lost response can leave an extra empty run (documented in client/tracker.py; the E2E test does not exercise it because `start` is called before the chaos is on). (5) Ground truth = rows read straight from Postgres (psql, float bits via float8send), checksum and chart rule recomputed in the test with node crypto. (6) The key page snippet now shows `url=` and `pip install ./client` (design text said `pip install tracker-client`; no such package). (7) E2E runs use raised rate limits (all traffic shares the proxy's IP) and FAULT_HOOKS=0; settings are in results/e2e/settings.txt. (8) Seeds 1–8; the same seed gives the same workload and fault schedule for the simulator, but timing of the browser cuts and of the stream is not deterministic.
What I expected: n/a
What I did: As listed.
Evidence: chaos/proxy.py, client/tracker.py, web/e2e/, results/e2e/settings.txt
Status: resolved

## [16:43] concern — What the E2E consistency test would NOT catch (list required by TZ_dev.md §8.1; facts only)
What happened: Not covered: clock skew between browser and server (the test compares data, not "N s ago"); outages longer than anything tested (longest block 2 s; there is no retention, so a cursor "older than retention" cannot occur); chart-library rendering bugs (it compares the series the page holds, not pixels drawn by uPlot); more than one API process; background-tab throttling or a sleeping laptop; the proxy buffering SSE in real deployments (nginx here has proxy_buffering off); resync under the E2E workload (threshold 10,000, never reached; resync was only checked by hand and by API tests); runs far larger than 840 events; a page that repairs lost events via gap-fill (see the mutation surprise); the `start()` race; browsers other than Chromium; TCP-level faults other than reset/close (no delay, packet loss or half-open connections); `NOTIFY` loss (that is the API-level listener test).
What I expected: n/a
What I did: Listed for NOTE.md; the owner decides what to keep.
Evidence: web/e2e/consistency.spec.ts
Status: open

## [17:22] problem — first isolation test run: 3 of 11 failed, and one showed the app wrote almost no request logs
What happened: (1) `InvalidURL` from httpx for ids with control characters (my test must percent-encode, as real clients do); (2) the "own run works" sanity check called the stream route in-process and hung (the in-process transport cannot stream); (3) the log-secrets test captured zero application log lines: besides one error line the app logged nothing per request, although §7 asks for `request_id` in logs. In the same test I had rotated the very key I then used, so the 500 I wanted to log never happened.
What I expected: n/a
What I did: Percent-encode ids; stream routes are driven at ASGI level with an immediate client disconnect, so a route that wrongly opens a stream fails instead of hanging; added one access-log line per request (`request_id method path status ms`; no headers, query string, bodies or keys); the log test now asserts that log lines exist (not vacuous) and uses the rotated key.
Evidence: backend/app/main.py, backend/tests/test_isolation.py
Status: resolved

## [17:22] surprise — nginx answered malformed ids with its own HTML 400 page (and announced its version)
What happened: Through the gateway, ids with invalid %-escapes (`run_%00`, `%`, `%zz`) returned `400 text/html` from nginx, while the API alone returns the uniform JSON 404. Not a leak of B's data, but it breaks "malformed -> identical 404" on the deployed stack, and the `Server: nginx/1.27.5` header disclosed the version. Fix attempts: location-level `error_page` (not applied: nginx rejects the URL before choosing a location), a named location (`empty URI in redirect to named location`, 500), `error_page 400 /_bad_request` (JSON body but status stayed 400), and finally `error_page 400 =404 /_bad_request` at server level with an internal location returning the same JSON body and cache headers. Also `server_tokens off`. Remaining known difference: `/api/runs/..%2F..%2Fetc` is normalised by nginx to `/etc` before routing and returns the single-page app shell (200 HTML); it exposes no data.
What I expected: One code path for all bad ids.
What I did: As above; new stack test compares status, body, content type and cache headers for 11 ids x 3 routes through nginx.
Evidence: web/nginx.conf, results/stack/run-*.txt
Status: resolved

## [17:22] surprise — the page needed more than 90 s to replay 13,000 events: renders were not throttled
What happened: With the server's resync disabled (`RESYNC_THRESHOLD=1000000`, a configuration mutation to prove the overflow test can fail) the page timed out after 90 s replaying 13,000 events. Cause: `RunConnection` called `onChange` for every metric event and the store answered with an immediate render (`emit(true)`), so the "at most 10 renders per second" in my earlier code comment and journal entry was false. With resync on, the 13,000 events are never replayed, which hid it. Normal streaming had the same per-event render cost.
What I expected: A throttled render rate.
What I did: `onChange` now fires only when the connection state changes (or the retry counter changes); per-event and stats updates use the throttled emit. Replaying 13,000 events without resync now takes about 2 s. Unit tests (21), 8 consistency seeds and the 3 silent-failure tests pass after the change. The failing first mutation run (timeout) and the later correct failure (`resync` missing from history) are both in the file.
Evidence: results/e2e/silent-failures-checks-20261111.txt, web/src/lib/connection.ts, web/src/lib/runStore.ts
Status: resolved

## [17:22] decision — timing test: method, tolerance, result
What happened: `tests/stack/` timing test: project B requests GET /runs/{id} through nginx over one keep-alive connection, 6 id variants x 400 interleaved samples in random order (after 20 warm-up requests each). Tolerance (fixed before the final run): a variant passes if |median difference to the "foreign real id" baseline| <= max(0.25 ms, 3 x the A/A noise), where A/A noise is the median difference between two random halves of the baseline. Permutation p-values are reported but are not the pass criterion. Result (results/stack/timing-summary.md): baseline median 1.74 ms; missing, neighbour, short and space ids differ by -0.02..+0.01 ms (p 0.29–0.87); a 600-character id is +0.20 ms (p = 0.0007), statistically detectable, within the tolerance, plausibly the longer request line and body. One variant of the earlier list (id with a NUL byte) was removed from the timing comparison because nginx answers it itself without reaching the API (so it is faster by construction; it reveals only that the string is not a valid URL).
What I expected: n/a
What I did: As described; raw samples are in timing-samples.json.
Evidence: results/stack/timing-summary.md, results/stack/timing-samples.json
Status: accepted-risk

## [17:22] problem — isolation mutation checks: empty output, a hang, a stub header
What happened: Mutation I-M3 (project filter removed from the events/seqs/integrity/stream lookup) printed nothing twice: first the in-process stream call hung until the container timeout; after I changed the stream calls to ASGI level the run still printed nothing because my grep pipeline lost the lines. Running it with the output captured to a file showed pytest exit code 1 (fails), and a verbose run showed `1 failed`. The file also contains a header "I-M5" that I never ran (a stub left in the heredoc).
What I expected: A clean pass/fail summary per mutation.
What I did: Kept every attempt in the file; I-M1, I-M2 and I-M4 show clean failures; I-M3's last entry shows only the exit code. I-M5 was NOT run.
Evidence: results/isolation-mutation-checks-20261111.txt
Status: resolved

## [17:22] decision — Step 6 choices
What happened: (1) Isolation matrix in-process (fast, 94 API tests in total) plus the same checks that need real HTTP in tests/stack/ (nginx, chaos proxy, real container logs, real SSE). (2) Stack tests run with the repo's .venv and raised rate limits (all traffic shares one IP); FAULT_HOOKS=0 (settings in results/stack/settings.txt). (3) Silent-failure tests that are visible in the UI are Playwright tests (duplicates toast, overflow with the real default threshold of 10,000, rotation signs the page out); the rest map to existing API tests (results/evidence-map.md). (4) The compose api service now takes REVALIDATE_S and STATS_INTERVAL_S from the environment (used only to break things on purpose). (5) `X-Accel-Buffering` is consumed by nginx and not visible to clients, so the test checks its effect (first `stats` within 1 s), not the header.
What I expected: n/a
What I did: As listed.
Evidence: results/evidence-map.md
Status: resolved

## [17:22] concern — "What else could leak": checked vs not checked (facts only, list required by TZ_dev.md §8.2)
What happened: Checked: application and container logs for keys, session tokens and payload markers (the 500 path included); the 404 body, status and cache headers for foreign/missing/malformed run ids on 9 read routes, 2 write routes (in-process) and through nginx; timing of GET /runs/{id} only; shared-cache behaviour through nginx and the chaos proxy; `stats`/`events`/`stream` of foreign runs (404); cookie swapping between clients. NOT checked: timing of any route other than GET /runs/{id}, or of 401 for unknown vs wrong keys; error bodies other than 404 across projects (401/403/409/422 bodies are not compared; `point_conflict` lists the caller's own point ids); `last_used_at` (visible only to the key's own project, not tested as a side channel); run counts and id density (ids are random, nothing global is exposed, but there is no test that tries to count other projects' runs through any endpoint); SSE close behaviour as a side channel (the stream ends silently, no reason is sent, but revoked vs expired can be told apart afterwards via `GET /session`, which is the intended behaviour for the UI); 429 behaviour (limits are per IP and shared by everyone behind one IP, so one client can lock out another: noted in step 2, still true; not tested on the stack); 401 `key_revoked` vs `invalid_key` tells a caller that a key once existed (required by the spec); Postgres row-level security (not implemented); `Server`/`Via` headers of the API when reached directly on port 8000 (published for local tests only); browser-side leaks (referrer, history, localStorage: only the keys-page storage was checked, once, manually).
What I expected: n/a
What I did: Listed for NOTE.md; the owner decides which of these to conclude on.
Evidence: results/evidence-map.md, tests/stack/, backend/tests/test_isolation.py
Status: open

## [20:37] problem — first full load run: the latency numbers were distorted by my own load generator
What happened: The first 10-minute run (50 runs x 10 events/s) was clean on correctness (297,620 events stored, 0 lost, 0 duplicated) but its latency looked odd: ingest p50 16.9 ms but p95 204 ms, p99 257 ms, max 3,112 ms; stream delay p95 251 ms. The resource sampler called `subprocess.run` (docker stats, psql) directly inside the asyncio loop, freezing the generator for 1–3 s about every 10 s. Check on the raw samples: 90 % of the requests slower than 100 ms fell within 3 s after a sampler sample, against 11 % of the fast ones (about 30 % would be chance).
What I expected: Latency numbers that describe the system, not the tool.
What I did: Sampler now runs in a worker thread. Re-ran the full 10 minutes. In the re-run only 10 % of the rare slow requests fall near a sampler sample. The first run's folder is kept and was renamed afterwards to `run-20261111-2012-INVALID-latency-sampler-blocked-event-loop` (contents untouched; its correctness columns are still valid, its latency columns are not).
Evidence: results/load/run-20261111-2012-INVALID-latency-sampler-blocked-event-loop/, results/load/run-20261111-2024-final/
Status: resolved

## [20:37] surprise — at the spec workload the stack was far from busy; at 4x the per-project ingest limit started to answer 429
What happened: Final run (results/load/run-20261111-2024-final/results.md): 299,010 events at 497 events/s; ingest p50 12.6 ms, p95 28.8 ms, p99 71.3 ms, max 234.7 ms; stream delay p50 14.5 ms, p95 34.1 ms, p99 76.7 ms, max 292 ms; 0 errors, 0 retries; 0 events lost on ingest, 0 never received by a reader, 0 delivered twice, 0 duplicate rows, all 50 runs gap-free; 689 reader reconnects; 5 slow readers (2 events/s while 10/s were produced) drained fully; API CPU mean 21 % of one core (max 36 %), API memory 88 -> 106 MB, Postgres CPU mean 11.5 %, 12 Postgres connections, no restarts or OOM. Exploratory run (200 runs, 1,901 events/s, 90 s, not the spec workload): ingest p95 192 ms, p99 424 ms, max 1.8 s, stream delay p95 217 ms, still 0 lost / 0 duplicated, but 49 requests got HTTP 429 (the default 200 requests/s per project limit, hit in bursts because all producers tick on 1-second boundaries and all runs belong to one project); the client retried them with the same batch id and none was given up.
What I expected: Some visible strain at 500 events/s; I had no number in mind for the 4x run.
What I did: Nothing to fix. Facts only; the verdict is the owner's.
Evidence: results/load/run-20261111-2024-final/, results/load/exploratory-200runs-90s-20261111/
Status: open

## [20:37] decision — load test design
What happened: One Python asyncio process drives everything through nginx (shipped topology, shipped rate limits, FAULT_HOOKS=0, chaos proxy not in the path; settings.txt in each run folder). Producers send batches of 10–50 points with content-hash batch ids and retry on failure. Readers are HTTP clients, one per run plus 5 slow ones, and drop the connection every 20–60 s and resume with `after_seq=<cursor>`. Lost and duplicate events are measured two ways: producer-acknowledged points versus rows in Postgres (psql, independent of the API), and, per reader, stored seqs versus seqs received (raw receipts minus unique = duplicate deliveries). Stream delay = reader receipt minus the start of the POST that carried the point, so it includes ingest time. All 50 runs are in one project. A 30 s smoke run and a 40 s smoke run preceded the long runs. The database was not cleaned (277 runs / 430,314 events from earlier tests were already there).
What I expected: n/a
What I did: As listed; loadtest/load.py (header explains the measures) and loadtest/report.py (tables only).
Evidence: loadtest/load.py, loadtest/report.py
Status: resolved

## [20:37] concern — What the load results do NOT show
What happened: (1) The generator, nginx, API and Postgres share one laptop (10 CPUs, 8 GB for Docker Desktop); the generator competes for CPU and there is no network latency. (2) Readers are HTTP clients; the cost of rendering 500 events/s in the page was not measured (the page loads and re-renders all of a run's data; the E2E runs used 840 events). (3) Only 10 minutes; API memory went 88 -> 106 MB, which is not enough to say there is or is not a leak. (4) One project, one API process; the per-project ingest limit was reached at 4x. (5) `GET /runs` with 50 runs of about 6,000 events: p95 112 ms; with 200 runs of about 900 events (exploratory): p95 267 ms; 500 runs and a 2 s poll per open tab (the spec's figure) were not measured, and the cost grows with the number of runs. (6) Slow readers backlogged about 4,800 events each, below the 10,000 resync threshold, so a resync under load was not exercised (it is covered by the API and E2E tests only). (7) Stream delay includes ingest time and uses the generator's clock only; the server's `received_at` was not compared. (8) No faults were injected during the load run beyond client disconnects. (9) The tail of the last minute (min 10: p95 196 ms in the table) rests on very few requests (run end and finish calls).
What I expected: n/a
What I did: Listed for NOTE.md.
Evidence: results/load/*/results.md ("What was not measured here")
Status: open

## [20:54] problem — clean-clone check found three gaps (README, Makefile, defaults); everything else passed
What happened: This folder is not a git repository, so the "clone" was simulated: `rsync` of the project into a scratch directory using .gitignore as the exclude list (217 files, 20 MB, no node_modules, .venv, dist or caches), a new compose project name (`cleanclone`, so a new empty database volume), no environment overrides, previous containers stopped. Steps and results: (1) `docker compose up --build`: first run took 7 s because Docker's layer cache was warm, which proves nothing; redone with `docker compose build --no-cache`: 71 s, exit 0, all four services up, web 200, API 404 through the gateway. (2) README quickstart followed literally in a brand-new virtualenv: `pip install ./client` worked; creating a project and running the one-line client script logged 50 points (1 batch, 0 retries); the runs list showed `hello`, finished, 50 events, primary metric loss. (3) `make test-api`: 94 passed. (4) `make test-web` FAILED: `sh: tsc: command not found`; nothing in the README or Makefile installs the node packages. (5) After the fix below: `make setup` (11 s), `make test-web` 21 passed, client tests 10 passed, `make e2e` 11 passed (8 seeds + 3 silent-failure tests), `make stack-tests` 11 passed.
What I expected: The README to be enough on a clean machine.
What I did: Added `make setup` (venv with httpx, pytest and ./client; `npm ci`; Playwright Chromium) and documented it with the machine requirements; found that the README said `make e2e` runs 8 seeds while the spec's default was 5 seeds, and made the default 8; .gitignore lacked `.venv/`, `web/playwright-report/`, `web/test-results/`: added.
Evidence: Makefile, README.md, .gitignore, web/e2e/consistency.spec.ts. The copy's own raw outputs were written inside the scratch copy and are not part of results/ (the earlier results/ runs are unchanged).
Status: resolved

## [20:54] concern — What the clean-clone check does NOT show
What happened: Not a real `git clone` (so a file that exists here but would be forgotten at `git add` is not detected; the check used .gitignore as the rule); the same Mac, same Docker Desktop, node 22, python 3.14 on the host, docker images' base layers already in the local cache (a machine without them also needs network access to pull python:3.12, node:22, nginx:1.27, postgres:16); Chromium for Playwright was already in the user cache (`make setup` did not need to download it); the README says node 20+ and python3 without having been tried on those versions; no Linux, no Apple-silicon/Intel difference, no Windows. `npm ci` reports 10 known vulnerabilities (5 moderate, 3 high, 2 critical) in the development dependency tree (build and test tools, not shipped in the image); not investigated or fixed. Several raw result files contain the local path `/Users/disa/std/soup-task` (5 files: unit-test and Playwright/mutation outputs); they were not edited because results/ is never edited: the owner decides whether to submit them as they are. The compose default `SERVER_PEPPER` is the public string `dev-pepper-change-me`; the README does not tell a deployer to change it. NOTE.md and SURPRISE.md do not exist yet (owner-written deliverables).
What I expected: n/a
What I did: Listed only.
Evidence: this entry
Status: open

## [21:21] decision — P1 UI scope and choices
What happened: Built: Keys page (list, create, rotate, revoke with typed confirmation, one-time reveal, "this session" chip, last-read-key refusal shown inline, "last used" in whole minutes), dialogs (focus moves in, Tab stays inside, Esc closes except on the one-time key, focus returns to the trigger, alert dialogs start on Cancel), dark theme (follows prefers-color-scheme on first visit, toggle in the top bar, choice kept in localStorage, chart rebuilt with the theme's colours), mobile layout below 760 px (navigation drawer, hidden columns, no horizontal page scroll on /runs, /keys, a run page), and a Keys entry in the side nav. Rotating the key of the current session: the new key stays on screen (auth redirects are suppressed while it is shown), and "I've copied it" signs out to /sign-in?reason=revoked; revoking the current session's own key (allowed only when another read key exists) signs out at once. Not built (outside the P1 list or cut earlier): chart zoom, keyboard point stepping, downsampling, Docs page, system-page artwork. Deviations: no Docs nav item; the drawer reuses the dialog component; dialogs sit 140 px from the top as in the design boards.
What I expected: n/a
What I did: Implemented as listed; 7 new Playwright tests (web/e2e/p1-ui.spec.ts) plus screenshots in results/ui/.
Evidence: web/src/pages/Keys.tsx, components/Dialog.tsx, lib/theme.ts, components/Shell.tsx; results/ui/*.png
Status: resolved

## [21:21] surprise — #25 answered, and my memory-based claim was wrong; a different container-type problem appeared in my own portal
What happened: Question: does `container-type: inline-size` on `.trk` make `.trk` the containing block for `position: fixed` dialogs? Measured on a long page scrolled to the bottom: NO, in two Chromium builds (the app pane's, and Playwright's 129.0.6668.29): the scrim covered exactly the viewport, the dialog sat 140 px from the viewport top, its buttons received clicks. A different problem appeared in the version I built to be "safe": dialogs portalled into a new wrapper `<div class="trk">` that was itself `position: fixed` and so kept `container-type: inline-size`. In Playwright's Chromium nothing in it was clickable (`elementsFromPoint` returned only `HTML`) and the screenshot showed neither page nor dialog, although the DOM and computed styles looked right; in the app pane it worked. Four of seven new tests failed on it (45 s timeouts on "I've copied it"). Setting `container-type: normal` on the wrapper fixed it.
What I expected: Either the naive fixed scrim to break (my earlier belief), or the portal to work.
What I did: Kept the portal with a non-container wrapper (robust if another engine differs), and recorded the experiment. I also lost about 20 minutes assuming a timing problem before hit-testing the button. Not tested: Firefox, Safari, other Chromium versions.
Evidence: results/ui/fixed-position-experiment.md, results/ui/*.png, web/src/components/Dialog.tsx
Status: resolved

## [21:21] problem — three smaller test and tooling mistakes in the P1 work
What happened: (1) A test used `waitForLoadState("networkidle")` on a page with an open SSE stream: it never settles and ran into the 45 s test timeout (removed). (2) The first Playwright run was started through a pipe into `tail`, so nothing was visible for 10 minutes (moved to a log file with an explicit per-test timeout). (3) In the app pane the signed-in session was gone once after a dev-server restart (`GET /session` returned `session_expired`); I signed in again and did not find the cause. Cookies are per host, not per port, so several stacks on `localhost` (8080, 8081, 5173) share one `tracker_session` cookie name; that is a plausible cause but unproven.
What I expected: n/a
What I did: Fixed (1) and (2); (3) left open.
Evidence: results/ui/, web/e2e/p1-ui.spec.ts
Status: open

## [21:21] concern — What the P1 checks do NOT show
What happened: Chromium only (the app pane and Playwright's 129); no Firefox or Safari; no real phone (390 px was emulated); no screen reader, no keyboard-only walk-through beyond Tab/Shift+Tab/Esc inside dialogs, no automated contrast check (the design lists ratios per theme; I did not verify them); the dark theme was looked at on the run page, keys page and mobile run page only, not on landing, key reveal or system pages; the chart's late-point ring colour in dark mode was not checked; the theme toggle's effect on an open chart relies on a rebuild (tested for a single chart); the event log and runs table were not re-measured for performance. The 'Docs' side-nav item and its page are still missing.
What I expected: n/a
What I did: Listed for NOTE.md.
Evidence: results/ui/
Status: open

## [22:50] decision — chart zoom and keyboard point stepping built (owner chose "Chart zoom and keys")
What happened: Added to the uPlot chart: drag across the plot to zoom the x axis (steps), a "Reset zoom" button while zoomed, and keyboard stepping when the chart has focus (← → one point, Home/End first/last point of the visible window, Esc clears), with a text readout under the chart ("step N · loss … · acc …") and a dashed marker with one dot per series. The zoom window is kept when new points arrive and when the chart is rebuilt (theme, hidden series); stepping stays inside the window; the hovered point is remembered by step, not by index, so a late point landing earlier does not move it. The chart element is now role="group" (was "img") because it takes keys. Pure stepping logic is in web/src/lib/chartNav.ts. Without a hover, ← lands on the point before the newest and → on the newest, as in the design board.
What I expected: n/a
What I did: Unit tests (chartNav.test.ts, 6) and one Playwright test (chart-interaction.spec.ts) that reads the real canvas window from data-xmin/data-xmax attributes set in the draw hook. Mutation: making the scale ignore the saved zoom failed the test (results/e2e/chart-interaction-mutation-zoom-not-kept.txt). First attempt at the mutation used a sed that did not apply, so that "pass" meant nothing; its output is kept as results/e2e/INVALID-mutation-not-applied-sed-failed.txt. The test passed on its first run, and my first version of it did not check the canvas window at all (only the keyboard window), so it could not have caught the mutation; I added the data-xmin/xmax assertions after noticing.
Evidence: web/e2e/chart-interaction.spec.ts, web/test/chartNav.test.ts, results/e2e/ (latest run-*.txt: 19 passed), make test-web: 27 passed
Status: resolved

## [22:50] concern — What the zoom and keyboard checks do NOT show
What happened: Zoom is x only (no y zoom, no wheel or pinch zoom, no touch drag); the drag test uses a mouse. Mouse hover does not drive the readout (only keyboard focus does; the design says hover or focus). Downsampling is still not built: with many points the chart draws all of them. The readout is a role="status" line, so a screen reader will announce every step; I did not test with a screen reader. Chromium only. Zoom state is lost on page reload (not in the URL). Late-point rings use the old scale logic and were not re-checked while zoomed.
What I expected: n/a
What I did: Listed for NOTE.md.
Evidence: web/e2e/chart-interaction.spec.ts
Status: open

## [09:40] problem — Lost browser session: not reproduced as such; one real bug found on the way (500 after a Postgres restart)
What happened: Owner asked me to try to reproduce the earlier lost dev session (`GET /session` returned `session_expired` once in the app pane). Tried against the compose stack through nginx with curl cookie jars (results/session-repro/): (1) sign in, restart api: session still 200. (2) restart postgres: the FIRST request afterwards returned 500 (`asyncpg InterfaceError: connection is closed`, pool held dead connections); later requests were fine. (3) second sign-in from a second cookie jar: the first jar stays valid (sessions are independent). (4) changing SERVER_PEPPER on the api: 401 `session_expired`; setting the original pepper back: the same cookie works again. (5) session row set to expired in the database: 401 `session_expired`. (6) Not tested as written: cookie expiry by time (SESSION_TTL_S is not passed through by docker-compose, my first attempt silently did nothing and showed 200; the DB-row test in (5) replaces it); rotating the session's own key gives `key_revoked`, not `session_expired`, so it does not match; my first scripts also failed because zsh does not word-split $H (a 404 from nginx), those outputs were discarded and re-run under bash.
What I expected: One of these to match the earlier observation.
What I did: Found (2) and fixed it: `pool_pre_ping=True` on the engine. Test `tests/stack/test_zz_db_restart.py` (restarts postgres, first request after it must be 200): failed before the fix with 500 (results/session-repro/db-restart-test-BEFORE-fix.txt), passes after (…AFTER-fix.txt). This fix is for the 500 only; it is not the lost session, because that response was `session_expired` (401), not a 500.
The cause of the lost session is NOT found. What I ruled out: api restart, postgres restart (gives 500, not 401), a second tab or sign-in, key rotation (different error code), time expiry (12 h, the pane was used for minutes). What can produce exactly that response and cannot be excluded: the session row not found because the browser's cookie came from a different database or pepper. Cookies are per host and not per port, and during the clean-clone check a second stack (own database volume, same ports) ran on this machine, so the pane may have held a cookie from the other stack; alternatively the API ran once with a different SERVER_PEPPER. I have no log or record that shows which. Also seen: expired session rows are never deleted (288 rows in the sessions table); harmless for this, but it is unbounded growth.
Evidence: results/session-repro/*.txt
Status: open (cause unknown; the 500 is fixed with a failing-then-passing test)

## [09:40] decision — SERVER_PEPPER: loud warning at start + random value in .env (owner instruction)
What happened: The compose default `dev-pepper-change-me` is public (it is in the repo), so anyone with a copy of the database could check key guesses.
What I did: (1) The API logs a WARNING at start whenever the pepper equals the default (`backend/tests/test_config.py`, 2 tests: warns on the default, silent and never prints a custom value). (2) `make pepper` (part of `make setup`, first line of the README quickstart) writes a random 64-hex value to `.env` once; docker compose reads `.env`; `.env` is in .gitignore. It never overwrites an existing value because a change invalidates every key and session. The stack still starts without it (so the old quickstart does not break), but with the warning. Documented in README "Secrets". Not done: the stack does not refuse to start with the default; I did not run `make pepper` in this working copy, so the running dev stack still uses the default (and warns).
Evidence: backend/tests/test_config.py, README.md, Makefile
Status: resolved

## [09:40] concern — npm audit: 10 vulnerable packages, 2 of them runtime (react-router), nothing upgraded
What happened: Raw output saved in results/npm-audit-*. 10 packages: react-router-dom and react-router (moderate, runtime dependency, shipped to the browser), and vitest (critical), tinypool, vite-node, @vitest/mocker, vite (high), esbuild, playwright, @playwright/test (dev/test tooling only). My earlier journal line said "10 dev-dependency vulnerabilities"; that was wrong (two are runtime).
What I did: `npm audit fix` was not run (owner instruction); all fixes npm offers are major-version upgrades. Summary in results/npm-audit-summary.md. I did not test whether the react-router advisories are reachable in this app.
Evidence: results/npm-audit-summary.md
Status: open

## [09:46] decision — Real clone check (git clone into an empty directory, cold build): all suites passed; three differences from the README
What happened: Committed (1abc325), stopped the dev stack, cloned into an empty directory (new compose project, new database volume), ran: `make pepper`; `docker compose build --no-cache`; `docker compose up -d`; `make setup`; the README python one-liner; `make test-api`, `make test-web`, client tests, `make e2e`, `make stack-tests`. Results: API 96 passed, web unit 27 passed, client 10 passed, E2E 19 passed (8 seeds, 3 silent failures, 7 UI, 1 chart), stack 12 passed (incl. the Postgres restart test). The one-liner exited 0. No SERVER_PEPPER warning in the api log (a random value was in .env). Raw output: results/clean-clone-real-20261112/ (the run script, the quickstart log, one raw output per suite; the e2e/stack/api files are complete, the script's own log keeps only the tail of each step).
Differences from the README: (1) the README said `pip install ./client` with no virtualenv; I used the venv that `make setup` creates (a bare pip on a system Python can refuse, and I did not test that). README now uses `.venv`. (2) `pip install ./client` left untracked `client/build/` and `client/tracker_client.egg-info/`; added to .gitignore. (3) The test runs rewrite committed files under results/ (e2e seed-*.json, stack timing-*, ui/*.png: 31 changed files in the clone); the "raw results are never edited" rule therefore only holds for the copies in the first commit; README now says so. Quickstart step 2 (open the page and create a project) I did through the API, not in a browser, in the clone; the UI flow itself is covered by the E2E tests. Timing: the whole run took about 5 minutes after the cold build on this laptop; I did not time the build.
Not tested in the clone: the 10-minute load test (not rerun), Firefox/Safari, a machine other than this one (same Docker Desktop, same images cache for base layers: `--no-cache` rebuilt our layers but base images were already pulled).
Evidence: results/clean-clone-real-20261112/
Status: resolved
