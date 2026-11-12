# DESIGN — Tracker

From TZ_dev.md §4–5, plus the owner-approved changes in JOURNAL.md.

## 1. Event format and ordering
**Guarantee:** every stored point gets one arrival number, with no gaps, and everyone sees points in that order.
- A stream message is `event: metric`, `id: <seq>`, data `{seq, point_id, metric, step, value, ts, received_at, is_late}`. Only these carry `id:`, so other messages never move the reconnect position.
- `seq` is the server's arrival number within a run (1, 2, 3…); it drives streaming and reconnecting. `step` is the client's training step, the chart's x position. Never mixed.
- `is_late`: the `step` is lower than the highest stored step of that metric. It still gets a new `seq`, so connected clients receive it.
- No gaps: a Postgres sequence skips numbers after a rollback, so we don't use one. One transaction locks the run's row, raises `last_seq` by the number of new points, inserts them as `old+1…old+n`, and commits, so counter and rows succeed or fail together.
- Commit order equals `seq` order because the lock is held until commit; a reader always sees `1..k`. Price: one run's batches are written one at a time.

## 2. Same batch sent again
**Guarantee:** sending data twice never stores it twice, and a conflicting resend is rejected loudly.
- `batch_id`: each batch is saved with a hash of its content. Same id and content: nothing is stored, the original answer is returned (safe after a lost response), the points count toward `duplicates_ignored`, and `duplicate_batch` is sent. Same id, different content: `409 batch_conflict`.
- `point_id` (default `metric:step`, unique per run): an identical known point is a duplicate, not stored, no `seq`. Same id with different metric, step or value: the whole batch is rejected with `409 point_conflict` listing the ids; nothing is stored or counted.
- Checks run under the run lock in this order: `batch_id`, closed run, `point_id`.

## 3. Catch-up after a disconnect
**Guarantee:** after any disconnect the client gets exactly the points it missed, read from the database, or is told to reload.
- The client remembers the last `seq` it applied and reconnects with `?after_seq=<that>` (same as `Last-Event-ID`; the query wins).
- The server sends `stats` first (so the page can show "Catching up 21/37"), replays later `seq`s from the database, then goes live. No history is kept in memory.
- `NOTIFY` only wakes a stream, which reads rows by `seq`; streams also re-read every 5 s, so a lost wake-up costs delay, never data.
- No `stats` for about 10 s means dead; retry from 1 s, backing off to 30 s with jitter, then Verify.
- `resync` (server then closes) is sent when the client is over `RESYNC_THRESHOLD` (10,000) events behind, its cursor is ahead of the server, or its socket backlog stays full. The client reloads and reopens. Ingestion never waits for readers.

## 4. Project isolation
**Guarantee:** a key or session reaches only its own project's data, and a foreign id looks exactly like a missing one.
- One function turns a Bearer key or cookie into `(project_id, key_id, scope)`. Routes never take a project id; database helpers require it and filter `WHERE id=… AND project_id=…`. REST, replay and streaming share this path.
- Missing, foreign and malformed ids return the same `404` from the same code path. Run ids are random.
- Keys are stored only as `HMAC-SHA256(server secret, key)`, compared in constant time. Unknown and wrong keys give the same `401`. Revocation is checked on every request; a session is tied to its key.
- Open streams re-check session and key at least every 2 s and close on revoke, rotation or expiry.
- Responses carry `Cache-Control: private, no-store` and `Vary: Cookie, Authorization`. The cookie is HttpOnly, SameSite=Strict; changes made with it also need a custom header and matching `Origin`.

## Known weak points
1. One busy run is limited by transaction speed; a stuck transaction blocks it.
2. `NOTIFY` is best effort; its delay is unmeasured.
3. Resync costs as much as replay. No retention: tables only grow.
4. Gap-free `seq` proves the server has no holes, not that the browser applied everything.
5. Key scopes: ingest writes, read views, admin views and manages keys (migration 0002; at first a read key could manage keys, which made "read cannot ingest" bypassable). Lost keys are unrecoverable.
6. Isolation relies on every query carrying `project_id`; no row-level security. Timing equality is statistical; other side channels remain.
7. Rate limits live in one process: one API instance only.
8. `GET /runs` with 500 runs and sparklines is unmeasured.
9. The checksum needs identical number formatting and ordering in Python, JavaScript and tests.
