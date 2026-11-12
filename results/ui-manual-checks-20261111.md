# Manual UI checks, 2026-11-11 (step 4)

How: the real compose stack (postgres, api, nginx gateway) driven through the Claude desktop app's built-in browser, using
`tools/feed.py` as the data source. The web image was built with `VITE_TEST_HOOKS=1` (test hook `window.__tracker`).
"Offline" was simulated by dispatching `offline`/`online` events on `window`, which makes the page close its EventSource while the
server keeps receiving events. This is NOT the chaos-proxy E2E test (step 5): one run each, no random seeds, no assertions in code.
The values below are copied from the tool output, not edited.

## 1. Disconnect and catch-up (run run_5iq7SiZHwSIVzQLfnbl0GX, 5 steps/s x 2 metrics)
```
before:  state live, count 100, lastSeq 100
offline: state "offline"
server last_seq during the outage: 160, page during the outage: count 100, lastSeq 100
after:   state live, count 240, lastSeq 240, missing []
history: connecting, live, offline, catching_up, live
sampled states (100 ms polling): offline, live      <- catching_up was too short to be sampled; only `history` shows it
```

## 2. Verify button vs independent read of the server (same run, 330 events)
```
UI chip: "Matches server · checksum 0261…a770"
page checksum (browser, pure JS SHA-256)  c67570a3b472647595412da10c1e295504adefc0d54804f2b36dd4bcc5d52a08
server checksum (Python, GET /integrity?up_to_seq=<page lastSeq>)  c67570a3b472647595412da10c1e295504adefc0d54804f2b36dd4bcc5d52a08
equal: true, count 330 == 330
```
(The chip shows the checksum from the moment Verify ran; the two long values above were read a moment later at the same seq.)

## 3. Resync (api restarted with RESYNC_THRESHOLD=50; run run_fblEuOvozWERZ7q3tpLATZ; ~90 events missed)
```
history: connecting, live, offline, catching_up, resync, connecting, live
after:   state live, count 280, lastSeq 280, missing [], checksum and count equal to the server's
```
Observation: a short `catching_up` shows before `resync`, because the server sends `stats` first and the page cannot know yet that a resync follows.
The "Refreshing data…" banner was not caught by 50 ms polling (the snapshot reload is fast with 280 events).

## 4. Revoke while viewing
From the run page: created a second read key, revoked the key of the current session (allowed: another read key exists).
Result after 5 s: URL `/sign-in?reason=revoked`, banner "You were signed out because your key was revoked or rotated. Paste the new key to continue."
(the open stream was closed by the server's 2 s re-validation; the page then checked `/session` and got 401 key_revoked).

## 5. Other flows seen working
- Landing: create project (valid name) -> `/keys-created` with masked keys, snippet containing the full ingest key; "Continue to runs" disabled until the checkbox is ticked;
  after Continue nothing is stored in localStorage/sessionStorage (`[[],[]]`); opening `/keys-created` again redirects away (keys are gone from memory).
- Sign in with a read key: `/runs`, runs list with live-updating rows, sparkline, status chips.
- Empty runs list ("No runs yet"); a run id of another project shows the "Not found" card.

## Not checked
Mobile layout (390 px) and dark theme (P1); keyboard navigation and screen-reader behaviour; Firefox/Safari (only the built-in browser was used);
long runs (largest run seen: ~340 events); the "mismatch" banner (no way to produce a mismatch without the chaos/fault tooling).
