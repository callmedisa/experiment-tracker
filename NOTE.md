# NOTE

I built a small experiment tracker. A Python script sends numbers (like "loss = 0.4") to a server. The server saves them. A web page shows them as a live chart. Everything starts with `docker compose up`.

Every claim below has a file in `results/` behind it (map: `results/evidence-map.md`). The full story, with my mistakes, is in `JOURNAL.md`. If I did not measure something, I say so.

## How it works
- The server gives every saved point a number inside its run: 1, 2, 3 and so on, with no gaps.
- If the browser loses the connection, it asks "give me everything after number N" and the server answers from the database.
- A Verify button compares what the page has with what the server has (count and checksum).

## Does it work?
Final tests, all passing: API 97, web 27, Python client 11, through nginx 12, browser 20. The browser tests read the truth straight from the database, not from the API they are testing. An earlier run on a fresh `git clone` also passed, before my last small fixes.

## Silent failures (data lost or doubled and nobody notices)
- **Lost events after a reconnect:** I cut connections and dropped responses on purpose, 8 random runs. The page matched the database every time. When I broke the replay code on purpose, the test failed, so the test really checks this.
- **Duplicates:** sending the same batch twice saves it once. The same id with different data gives an error and saves nothing.
- **Page far behind (13,000 events):** the server tells it to reload, and it ends equal to the database.
- **Half-written batch:** I forced a crash in the middle of a batch. Nothing was half saved.
- **Old key after rotation:** the old key gets 401, open streams close, and the page signs out in about 2 seconds.
- **API stopped during a live run:** the page showed "Reconnecting…", never "Live", and came back by itself. I found two problems and fixed both. The retry text looked stuck, and my Python client lost unsent points after too many failed tries. Now the client keeps them and sends them later. It still raises an error, so it is loud, not silent.

## Isolation (one project must not see another)
A project cannot list, read, stream or write another project's runs. I tested 9 read routes and the write routes. A wrong id, a missing id and another project's id all give the same 404, also through nginx. I also measured time: the difference was under 0.03 ms. A very long id is 0.2 ms slower, which is measurable but small. Logs contain no keys or cookies.

## My verdict: SHIP for the planned use, with three changes first
Planned use: 50 runs, 10 events per second each, for 10 minutes. Result: 299,010 events, lost 0, duplicated 0, no gaps, and every reader got every event. Saving a batch took 28.8 ms at p95 (95% of requests were faster). The API used about 21% of one CPU. This is fine for a pilot or an internal tool. I would not give it to strangers on the internet.

One thing I fixed at the end: a read key could create and revoke keys, so "read cannot write" could be bypassed. Now there is an admin key, and a read key gets 403.

Still to change, in this order:
1. **No cleanup.** Events and sessions only grow.
2. **Rate limits are per IP and per process.** Behind one proxy, one noisy client can block others. With two API servers, the counters are not shared. At 4x load, 49 requests got 429 and the client retried them all.
3. **The default `SERVER_PEPPER` is public in the repo.** It warns loudly and `make pepper` makes a random one, but the stack still starts without it. Also, `npm audit` flags `react-router`. I did not upgrade because the fix is a major version. The app runs only in the browser and all links are fixed paths or ids from the server, so I think it does not apply, but I did not test an attack.

## What my tests would NOT catch
- A page that loses events but repairs them by asking again still passes the browser test. I showed this by breaking it on purpose.
- Chart pixels, long outages, clock differences, more than one API process.
- Only Chromium was tested. Not Firefox, Safari, a phone or a screen reader.
- The load test ran on one laptop with the load tool on the same laptop, one project, 10 minutes. Memory grew 18 MB, which is too short to call a leak. I did not measure `GET /runs` with 500 runs.

## What could still leak
Checked: logs, the 404 answer and its timing for run ids, caching through nginx, swapping cookies.
Not checked: timing of other routes, timing of 401 for an unknown key versus a wrong key, error bodies other than 404, browser leaks other than `localStorage`.

## Not built
Downsampling, the Docs page, styled error pages, y-zoom, hover readout. Once in development I lost a browser session and could not reproduce it. I think it was a cookie from a second stack on the same machine, but I have not proven it.
