# Evidence map (prepared by the agent; conclusions are the owner's)

Which test covers which required check (TZ_dev.md §8.1–§8.3), how it was checked, where the raw output is, and whether the test was
shown to fail when the code was broken on purpose. "Mutation" = a deliberate breakage of the code, run against the suite, then restored.

## §8.1 Consistency (E2E)
| Check | Test | Raw output | Mutation shown to fail it |
|---|---|---|---|
| page count/checksum/chart series == database after cut SSE, lost responses, duplicates, late points; seq contiguous; UI passes live > reconnecting > catching_up > live | `web/e2e/consistency.spec.ts`, seeds 1–8 | `results/e2e/run-20261111-163948.txt`, `seed-N.json`, `summary-8-seeds.md`, `settings.txt` | server replay skips an event (caught: `missing: [1]`); page store drops every 50th seq (caught only when the drop is in `RunData.apply`; dropping in the stream handler alone is repaired by gap-fill and NOT caught) — `results/e2e/mutation-checks-e2e-redo-20261111.txt` |

## §8.2 Isolation
| Check | Test | Raw output | Mutation shown to fail it |
|---|---|---|---|
| A cannot list/read B's runs, events, seqs, integrity; cannot POST metrics / finish B's run; cannot open B's stream (cookie, `after_seq`, `Last-Event-ID`) | `backend/tests/test_isolation.py` (matrix: 9 read routes x foreign / sibling / 17 malformed ids; write + finish with the same ids) | `results/api-tests-*.txt` (latest) | project filter removed from `_lock_run`, from `GET /runs/{id}`, from the events/seqs/integrity/stream lookup: each caught — `results/isolation-mutation-checks-20261111.txt` |
| guess B's id (random, neighbouring, malformed) -> identical 404 | same matrix (status, body, cache headers, content type compared) and `tests/stack/` `test_malformed_ids_are_the_same_404_through_the_gateway...` (through nginx) | `results/stack/run-*.txt` | not run |
| similar timing for existing-foreign vs missing vs malformed ids | `tests/stack/` `test_unknown_vs_foreign_vs_malformed_run_ids_take_the_same_time` (6 variants x 400 interleaved samples, tolerance max(0.25 ms, 3 x A/A noise)) | `results/stack/timing-summary.md`, `timing-samples.json` | not run |
| no read of B's data through a cache path (same URL with A's and B's cookies against the compose stack, conditional requests, cache headers) | `tests/stack/` cache tests (through nginx :8080 and through the chaos proxy :8081; 30 alternations x 3 identities) | `results/stack/run-*.txt` | not run |
| read key cannot ingest, ingest key cannot read, revoked key fails everywhere, CSRF-less mutation rejected | `backend/tests/test_isolation.py` (every route, 6 bad CSRF header combinations on 4 mutating routes) | `results/api-tests-*.txt` | not run |
| logs contain no keys, cookies or payloads | `test_isolation.py::test_logs_contain_no_keys...` (app log records, including a 500) and `tests/stack/` `test_container_logs_contain_no_secrets...` (real `docker compose logs` of api, web, chaos, postgres) | `results/api-tests-*.txt`, `results/stack/run-*.txt` | access log made to include the Authorization header: caught |

## §8.3 Silent failures (one test each)
| # | Failure | Test | How checked | Raw output | Mutation shown to fail it |
|---|---|---|---|---|---|
| 1 | events lost on reconnect | `backend/tests/test_stream.py::test_no_events_lost_or_duplicated_across_random_disconnects` (600 events, random disconnects, one seed) and the E2E consistency test (8 seeds, real browser) | gap-free `1..N` list from the stream; page == database | `results/api-tests-*.txt`, `results/e2e/` | replay `>` -> `>=` and `> after + 1` caught |
| 2 | duplicates | `backend/tests/test_ingest.py` (batch retry, re-split, within-batch, concurrent identical, conflicts) and `web/e2e/silent-failures.spec.ts` (toast shown; `duplicates_ignored` exact; conflicting payload -> 409 and nothing stored) | counters read from Postgres | `results/api-tests-*.txt`, `results/e2e/run-*.txt` | no conflict check; `<=` late; counter by submitted points: caught (`results/mutation-checks-20261111.txt`) |
| 3 | queue overflow / slow client | `test_stream.py::test_slow_reader_gets_resync...` (API level, ingestion speed compared) and `silent-failures.spec.ts` overflow (13,000 events while the page is away, default threshold 10,000; page recovers via resync; count/checksum == database) | history contains `resync`; ingest time recorded | `results/e2e/silent-failures-checks-20261111.txt` | `RESYNC_THRESHOLD=1000000` (no resync): caught; the same run first exposed a slow-render bug (see JOURNAL) |
| 4 | partial writes | `test_ingest.py::test_failure_mid_batch...`, `test_many_random_failures_never_break_contiguity` (fault hook `X-Fault: mid-batch`, hooks ON only in tests) | rows, counter and batch table read from Postgres after the failure; retry succeeds | `results/api-tests-*.txt` | row lock / counter mutations caught; the fault hook itself is OFF in the compose stack |
| 5 | old key after rotation | `test_stream.py::test_rotating_the_key_and_session_expiry...`, `tests/stack/` rotation through nginx (REST 401 `key_revoked`, open streams closed), `silent-failures.spec.ts` (page signed out about 2 s after rotation) | stream ends; page URL becomes `/sign-in?reason=revoked` | `results/stack/run-*.txt`, `results/e2e/silent-failures-checks-20261111.txt` | `REVALIDATE_S=100000`: caught |
| + | response lost after commit, retry | `test_ingest.py::test_response_lost_after_commit...`, client tests, E2E (chaos "lost" mode: 6–13 per seed) | retry returns the original result; stored once | as above | as above |

Suites at the time of this table: API 94, web unit 21, client 10, stack 11, E2E 11 (8 consistency seeds + 3 silent-failure tests).

## §8.4 Load
| Measure | Raw output | Notes |
|---|---|---|
| 50 runs x 10 events/s x 10 min, batches 10–50, 5 slow readers, periodic disconnects; ingest p50/p95/p99, stream delay, lost, duplicates, DB/API resources | `results/load/run-20261111-2024-final/` (`results.md`, `summary.json`, raw samples, `settings.txt`) | the valid run |
| same, first attempt | `results/load/run-20261111-2012-INVALID-latency-sampler-blocked-event-loop/` | correctness columns valid; latency invalid (generator stalled by its own sampler) |
| 200 runs x 10 events/s x 90 s (exploratory, not the spec workload) | `results/load/exploratory-200runs-90s-20261111/` | 49 requests got 429 (per-project limit), all retried |

## P1 UI
| Check | Test | Raw output |
|---|---|---|
| Keys page: list, create (validation, new key works), rotate (focus, Esc, old key dead), revoke (typed confirmation), last read key refused, rotating the current session's key | `web/e2e/p1-ui.spec.ts` (keys page, 4 tests) | `results/e2e/run-*.txt` (latest) |
| #25 dialogs stay in the viewport on a long scrolled page, Tab never leaves a dialog | `p1-ui.spec.ts` (1 test) and the experiment | `results/ui/fixed-position-experiment.md` |
| dark theme follows OS, toggles, persists, chart palette changes; mobile 390 drawer, no horizontal scroll | `p1-ui.spec.ts` (2 tests) | `results/ui/*.png` |

## Chart zoom and keyboard stepping
| Check | Test | Raw output | Mutation shown to fail it |
|---|---|---|---|
| ← → Home End Esc step through points and clamp; drag zooms the canvas window; zoom kept when new points arrive; Reset zoom restores 0..149 | `web/e2e/chart-interaction.spec.ts`; stepping logic `web/test/chartNav.test.ts` | `results/e2e/run-*.txt` (latest), `chart-interaction-try2.txt` | scale ignores saved zoom: caught (`chart-interaction-mutation-zoom-not-kept.txt`); an earlier attempt that did not apply is `INVALID-mutation-not-applied-sed-failed.txt` |

## Added after the P1 report
| Check | Test | Raw output | Failing before the fix |
|---|---|---|---|
| a Postgres restart does not leave 500s (dead pooled connections) | `tests/stack/test_zz_db_restart.py` | `results/session-repro/db-restart-test-BEFORE-fix.txt`, `…AFTER-fix.txt` | yes: 500 before, 200 after `pool_pre_ping` |
| start-up warning when SERVER_PEPPER is the public default | `backend/tests/test_config.py` | `results/api-tests-*.txt` (latest) | not run |

Suites now: API 96, web unit 27, client 10, stack 12, E2E 19.
