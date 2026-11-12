# SURPRISE

Green tests were wrong more often than I expected. 65 API tests passed while the real container crashed on start, because the tests set up the database differently from the Dockerfile. My first browser test passed even though 3 of 5 connection cuts hit nothing, so I made the test check that the cuts really happened. When I made the browser drop every 50th event, the test still passed, because the page quietly asked for the missing ones. So that test cannot tell "nothing was lost" from "something was lost and then repaired".

Saving a batch was slow on a fresh database (0.02 s became 0.49 s) until Postgres had its statistics. I only noticed because one number looked odd. My own load tool made the p95 look 7 times worse (204 ms instead of 28.8 ms), because it blocked itself while reading Docker stats. I caught it because p50 and p95 were so far apart. Two of my beliefs were also wrong. I thought `container-type: inline-size` breaks `position: fixed` (I measured: it does not). I blamed nginx for a 502 that was really an API crash from a bad environment variable.

What still worries me: the lost session I cannot explain, no cleanup of old data, and rate limits that live in one process. Everything, including the failed runs, is in `JOURNAL.md` and `results/`.
