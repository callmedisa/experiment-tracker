# Tracker

A small experiment tracker: a training script sends metrics, a web page shows them live, and the page can prove that what it shows
is what the server stored (Verify, checksum). A take-home project. The written documents are `DESIGN.md`, `NOTE.md`,
`SURPRISE.md`, `AI_LOG.md` and `JOURNAL.md`. Raw test output is in `results/`.

## Quickstart

```bash
make pepper                               # once: writes a random SERVER_PEPPER to .env (git-ignored); see "Secrets" below
docker compose up --build                 # postgres, api, web (nginx), chaos proxy
open http://localhost:8080                # create a project; copy the three keys (ingest, read, admin) from the one-time page
python3 -m venv .venv && .venv/bin/pip install ./client   # the Python client (not on PyPI); `make setup` does this too
.venv/bin/python -c "import tracker; r = tracker.start(key='<ingest key>', run='hello', url='http://localhost:8080/api'); [r.log(s, loss=1/(s+1)) for s in range(50)]; r.finish()"
# then open the run in the web app: Runs -> hello
```

Ports: web + API gateway `8080` (the API is under `/api`), API alone `8000` (local tests only), chaos proxy `8081`/`8082`, its control API `8099`.
Defaults are the spec's: rate limits 20/min per IP for project creation, 10/min for sign-in and failed keys, 200 requests/s per project for
ingest; resync threshold 10,000 events; fault hooks off.

## Tests (each writes raw output to `results/`)

Once per clone: `make setup` (creates `.venv` with httpx and pytest, runs `npm ci` in `web/`, installs the Playwright Chromium browser).
Requirements on the machine: docker with compose, python3, node 20+, make.

| Command | What it runs | Needs |
|---|---|---|
| `make test-api` | 96 API tests in the api container against a fresh database (idempotency, conflicts, seq contiguity, rollback, late flags, SSE, auth, isolation matrix) | docker |
| `make test-web` | type check + 27 unit tests (checksum parity with the Python server, chart rule, connection state machine) | `make setup` |
| `.venv/bin/python -m pytest client/test_tracker.py` | 10 client tests (retries, stable batch ids) | `make setup` |
| `make e2e` | Playwright: 8 seeded consistency runs with a chaos proxy + 3 silent-failure tests + 7 UI tests (keys page, dialogs, dark theme, 390 px mobile; screenshots in `results/ui/`) + 1 chart test (drag zoom, Reset zoom, keyboard stepping) | `make setup` |
| `make stack-tests` | 12 checks over real HTTP through nginx: cache paths, timing, container logs, streams, and a Postgres restart (the last test restarts the postgres container) | `make setup` |
| `.venv/bin/python loadtest/load.py --out results/load/<name>` | 50 runs x 10 events/s for 10 minutes (see the script header) | `.venv`, stack running |

`make e2e` and `make stack-tests` restart the stack with raised rate limits (all their traffic comes from one IP) and fault hooks off;
the values are written to `results/e2e/settings.txt` and `results/stack/settings.txt`. The load test uses the shipped defaults.

## Secrets

`SERVER_PEPPER` is mixed into every key and session hash. Without a value the stack falls back to a default that is **in this repository**, and the
API then logs a `WARNING: SERVER_PEPPER is the public default …` at every start. `make pepper` (also part of `make setup`) writes a random value
to `.env` once and never overwrites it. Changing the value later invalidates every key and session (all hashes stop matching), so keep `.env`.
Not in `.env`: nothing else; the database password in `docker-compose.yml` (`tracker`/`tracker`) is a local-development value and the postgres port is not published.

## Layout

```
backend/    FastAPI + SQLAlchemy + Alembic, tests/       web/       React + Vite + uPlot, e2e/ (Playwright), nginx.conf
client/     tracker.py (Python client)                   chaos/     TCP chaos proxy (drop / block / flaky)
tests/stack/ tests over the running stack               loadtest/  load.py
results/    raw outputs, never edited
```

## Known limitations (more in `JOURNAL.md`)

Three key scopes: ingest writes events, read views runs, admin also manages keys (a read key gets 403 on `/keys`); lost keys are unrecoverable, and projects created before migration 0002 have no admin key; one API process (rate limits and the stream hub are in-process); no retention;
Not built: downsampling, y/wheel/touch zoom, mouse-hover readout, the Docs page, system-page artwork (404/500/429 are plain); the run page loads all
events of a run when it opens; the E2E and load tests ran on one laptop with Docker Desktop.

Running the test suites rewrites some committed files in `results/` (`e2e/seed-*.json`, `stack/timing-*`, `ui/*.png`) with the new run's output; `git diff results/` shows the difference between the run in this repository and yours.

**The only edit made to raw outputs in `results/`:** in 12 files the machine-specific absolute paths were replaced by placeholders
(`/Users/<user>/…/<project>` became `<repo>`, the temporary clone directory became `<repo-clone>`) so the repository does not carry the
author's local user name. Nothing else in those files was changed (16 replacements; file list in the JOURNAL entry "Local paths removed from raw outputs").
Paths of the system Python (`/Library/Frameworks/…`) were left as they are.
