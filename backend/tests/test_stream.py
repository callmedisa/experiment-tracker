"""SSE stream over real HTTP: replay from the DB, stats, resync, closed, revalidation, wake-ups (TZ_dev.md §5.2, §8.3)."""
import asyncio
import hashlib
import json
import random
import struct
import time

import httpx
from sqlalchemy import text

from conftest import new_project, pt, sign_in

FAST = dict(stats_interval_s=0.3, revalidate_s=0.2)


class Stream:
    """Minimal SSE client. next() -> (event, id, data) or None when the server ended the stream."""

    def __init__(self, client, path, **kw):
        self.cm = client.stream("GET", path, **kw)

    async def __aenter__(self):
        self.r = await self.cm.__aenter__()
        self.lines = self.r.aiter_lines()
        return self

    async def __aexit__(self, *a):
        await self.cm.__aexit__(*a)

    async def next(self, timeout=5.0):
        ev, id_, data = None, None, []
        while True:
            try:
                line = await asyncio.wait_for(self.lines.__anext__(), timeout)
            except StopAsyncIteration:
                return None
            if line == "":
                if ev is not None:
                    return ev, id_, json.loads("\n".join(data))
                continue
            k, _, v = line.partition(": ")
            if k == "event":
                ev = v
            elif k == "id":
                id_ = v
            elif k == "data":
                data.append(v)

    async def until(self, pred, timeout=5.0):
        out = []
        end = time.monotonic() + timeout
        while True:
            e = await self.next(max(0.01, end - time.monotonic()))
            if e is None:
                return out, False
            out.append(e)
            if pred(e):
                return out, True


async def setup(live, **over):
    app, url = await live(**{**FAST, **over})
    c = httpx.AsyncClient(base_url=url, timeout=10)
    p = await new_project(c)
    assert (await sign_in(c, p.admin_key)).status_code == 200
    run_id = (await c.post("/runs", json={"name": "r"}, headers=p.ingest)).json()["id"]
    return app, url, c, p, run_id


def csrf(url):
    return {"X-Requested-With": "tracker", "Origin": url}


async def ingest(c, p, run_id, batch_id, steps, metric="loss"):
    r = await c.post(f"/runs/{run_id}/metrics", json={"batch_id": batch_id, "points": [pt(metric, s, s / 10) for s in steps]},
                     headers=p.ingest)
    assert r.status_code == 200, r.text
    return r.json()


async def test_stats_first_then_replay_then_live_and_only_metric_has_id(live):
    app, url, c, p, rid = await setup(live)
    await ingest(c, p, rid, "b1", range(5))
    async with Stream(c, f"/runs/{rid}/stream?after_seq=2") as s:
        first = await s.next()
        assert first[0] == "stats" and first[2]["last_seq"] == 5 and first[1] is None
        assert first[2]["last_event_at"] and first[2]["server_time"] and first[2]["status"] == "running"
        got, _ = await s.until(lambda e: e[0] == "metric" and e[2]["seq"] == 5)
        assert [(e[0], e[1], e[2]["seq"]) for e in got] == [("metric", "3", 3), ("metric", "4", 4), ("metric", "5", 5)]
        await ingest(c, p, rid, "b2", [5, 6])
        got, ok = await s.until(lambda e: e[0] == "metric" and e[2]["seq"] == 7)
        assert ok
        metrics = [e for e in got if e[0] == "metric"]
        assert [e[2]["seq"] for e in metrics] == [6, 7]
        assert all(e[1] is None for e in got if e[0] != "metric"), "only metric events may carry an id"
        d = metrics[0][2]
        assert set(d) == {"seq", "point_id", "metric", "step", "value", "ts", "received_at", "is_late"}
    await c.aclose()


async def test_last_event_id_header_and_query_wins(live):
    app, url, c, p, rid = await setup(live)
    await ingest(c, p, rid, "b1", range(6))
    async with Stream(c, f"/runs/{rid}/stream", headers={"Last-Event-ID": "4"}) as s:
        got, _ = await s.until(lambda e: e[0] == "metric" and e[2]["seq"] == 6)
        assert [e[2]["seq"] for e in got if e[0] == "metric"] == [5, 6]
    async with Stream(c, f"/runs/{rid}/stream?after_seq=5", headers={"Last-Event-ID": "1"}) as s:
        got, _ = await s.until(lambda e: e[0] == "metric" and e[2]["seq"] == 6)
        assert [e[2]["seq"] for e in got if e[0] == "metric"] == [6]
    await c.aclose()


async def test_stats_heartbeat_when_idle(live):
    app, url, c, p, rid = await setup(live)
    async with Stream(c, f"/runs/{rid}/stream") as s:
        evs = [await s.next(3) for _ in range(3)]
    assert [e[0] for e in evs] == ["stats"] * 3
    await c.aclose()


async def test_late_flag_is_delivered(live):
    app, url, c, p, rid = await setup(live)
    async with Stream(c, f"/runs/{rid}/stream") as s:
        await ingest(c, p, rid, "b1", [5])
        await ingest(c, p, rid, "b2", [1])
        got, ok = await s.until(lambda e: e[0] == "metric" and e[2]["seq"] == 2)
        assert ok and [e[2]["is_late"] for e in got if e[0] == "metric"] == [False, True]
    await c.aclose()


async def test_duplicate_batch_is_sent_live_and_not_replayed(live):
    app, url, c, p, rid = await setup(live)
    await ingest(c, p, rid, "b1", range(3))
    async with Stream(c, f"/runs/{rid}/stream?after_seq=3") as s:
        await s.next()
        await ingest(c, p, rid, "b1", range(3))          # retry of the same batch
        await ingest(c, p, rid, "b2", [1, 2, 3])          # re-split: 2 duplicates + 1 new
        got, ok = await s.until(lambda e: e[0] == "metric" and e[2]["seq"] == 4)
        dups = [e[2] for e in got if e[0] == "duplicate_batch"]
        if len(dups) < 2:
            more, _ = await s.until(lambda e: e[0] == "duplicate_batch", 2)
            dups += [e[2] for e in more if e[0] == "duplicate_batch"]
        assert {"batch_id": "b1", "first_seq": 1, "last_seq": 3, "duplicates": 3} in dups
        assert any(d["batch_id"] == "b2" and d["duplicates"] == 2 and d["first_seq"] == 4 for d in dups)
        st, _ = await s.until(lambda e: e[0] == "stats" and e[2]["duplicates_ignored"] == 5, 3)
        assert st
    async with Stream(c, f"/runs/{rid}/stream?after_seq=0") as s2:   # a new connection must not see old duplicate_batch
        got, _ = await s2.until(lambda e: e[0] == "metric" and e[2]["seq"] == 4)
        assert not [e for e in got if e[0] == "duplicate_batch"]
        assert got[0][0] == "stats" and got[0][2]["duplicates_ignored"] == 5
    await c.aclose()


async def test_closed_comes_after_the_last_event(live):
    app, url, c, p, rid = await setup(live)
    async with Stream(c, f"/runs/{rid}/stream") as s:
        await ingest(c, p, rid, "b1", range(4))
        await c.post(f"/runs/{rid}/finish", json={"status": "finished"}, headers=p.ingest)
        got, ok = await s.until(lambda e: e[0] == "closed")
        assert ok
        seqs = [e[2]["seq"] for e in got if e[0] == "metric"]
        assert seqs == [1, 2, 3, 4]
        assert got[-1][2]["status"] == "finished" and got[-1][2]["finished_at"]
        assert await s.next(2) is None  # server closed the stream
    async with Stream(c, f"/runs/{rid}/stream?after_seq=2") as s:   # connecting to a finished run: replay, then closed
        got, ok = await s.until(lambda e: e[0] == "closed")
        assert ok and [e[2]["seq"] for e in got if e[0] == "metric"] == [3, 4]
    await c.aclose()


async def test_resync_when_too_far_behind_or_cursor_ahead(live):
    app, url, c, p, rid = await setup(live, resync_threshold=5)
    await ingest(c, p, rid, "b1", range(10))
    async with Stream(c, f"/runs/{rid}/stream?after_seq=0") as s:
        got, ok = await s.until(lambda e: e[0] == "resync")
        assert ok and got[-1][2] == {"last_seq": 10}
        assert not [e for e in got if e[0] == "metric"]
        assert await s.next(2) is None
    async with Stream(c, f"/runs/{rid}/stream?after_seq=99") as s:   # cursor ahead of the server (e.g. DB restored)
        got, ok = await s.until(lambda e: e[0] == "resync")
        assert ok
    async with Stream(c, f"/runs/{rid}/stream?after_seq=5") as s:    # exactly at the threshold: still replayed
        got, ok = await s.until(lambda e: e[0] == "metric" and e[2]["seq"] == 10)
        assert ok
    await c.aclose()


async def test_slow_reader_gets_resync_and_ingestion_is_not_slowed(live):
    app, url, c, p, rid = await setup(live, resync_threshold=2000, stream_page=500)
    reader = httpx.AsyncClient(base_url=url, timeout=60)
    assert (await sign_in(reader, p.read_key)).status_code == 200
    # baseline ingest time without any reader
    base_rid = (await c.post("/runs", json={"name": "base"}, headers=p.ingest)).json()["id"]
    t0 = time.monotonic()
    for i in range(40):
        await c.post(f"/runs/{base_rid}/metrics", headers=p.ingest, json={"batch_id": f"x{i}",
                     "points": [pt("loss", i * 1000 + j, 0.1) for j in range(1000)]})
    base = time.monotonic() - t0
    async with Stream(reader, f"/runs/{rid}/stream") as s:
        assert (await s.next())[0] == "stats"
        # the reader now stops reading while 40,000 events arrive
        t0 = time.monotonic()
        for i in range(40):
            r = await c.post(f"/runs/{rid}/metrics", headers=p.ingest, json={"batch_id": f"b{i}",
                             "points": [pt("loss", i * 1000 + j, 0.1) for j in range(1000)]})
            assert r.status_code == 200
        with_reader = (time.monotonic() - t0) / 40
        # now read everything it was sent
        seqs, resync = [], None
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:   # heartbeats never stop, so without a deadline a missing resync would hang
            e = await s.next(15)
            if e is None:
                break
            if e[0] == "metric":
                seqs.append(e[2]["seq"])
            if e[0] == "resync":
                resync = e[2]
                break
    assert resync is not None, f"no resync; reader got {len(seqs)} events"
    assert seqs == list(range(1, len(seqs) + 1)), "events before resync must be a gap-free prefix"
    assert len(seqs) < 40000
    # ingestion with a stuck reader must not be much slower per batch than without one
    print(f"ingest per 1000-point batch: no reader {base / 40:.4f}s, stuck reader {with_reader:.4f}s")
    assert with_reader < (base / 40) * 3 + 0.05, (with_reader, base / 40)
    await reader.aclose()
    await c.aclose()


async def test_no_events_lost_or_duplicated_across_random_disconnects(live):
    app, url, c, p, rid = await setup(live)
    total = 600
    rnd = random.Random(7)

    async def producer():
        for i in range(total // 20):
            await ingest(c, p, rid, f"b{i}", range(i * 20, i * 20 + 20))
            await asyncio.sleep(0.01)
        await c.post(f"/runs/{rid}/finish", json={"status": "finished"}, headers=p.ingest)

    prod = asyncio.create_task(producer())
    cursor, got, connects = 0, [], 0
    while True:
        connects += 1
        stop_after = rnd.randint(1, 60)           # drop the connection at a random point
        n, closed = 0, False
        async with Stream(c, f"/runs/{rid}/stream?after_seq={cursor}") as s:
            while n < stop_after:
                e = await s.next(10)
                if e is None:
                    break
                if e[0] == "metric":
                    got.append(e[2]["seq"])
                    cursor = e[2]["seq"]
                    n += 1
                if e[0] == "closed":
                    closed = True
                    break
        if closed:
            break
    await prod
    assert got == list(range(1, total + 1)), f"after {connects} connections: not gap-free / has duplicates"
    assert connects > 5
    await c.aclose()


async def test_unauthenticated_foreign_and_wrong_credentials(live):
    app, url, c, p, rid = await setup(live)
    other = httpx.AsyncClient(base_url=url)
    q = await new_project(other)
    assert (await sign_in(other, q.read_key)).status_code == 200
    missing = await other.get("/runs/run_" + "A" * 22 + "/stream")
    foreign = await other.get(f"/runs/{rid}/stream?after_seq=0")
    foreign2 = await other.get(f"/runs/{rid}/stream", headers={"Last-Event-ID": "3"})
    assert foreign.status_code == foreign2.status_code == missing.status_code == 404
    strip = lambda r: {k: v for k, v in r.json().items() if k != "request_id"}
    assert strip(foreign) == strip(missing) == strip(foreign2)
    anon = httpx.AsyncClient(base_url=url)
    assert (await anon.get(f"/runs/{rid}/stream")).status_code == 401
    assert (await anon.get(f"/runs/{rid}/stream", headers=p.ingest)).status_code == 401  # ingest key is not a session
    r = await c.get(f"/runs/{rid}/stream?after_seq=-1")
    assert r.status_code == 422
    for x in (other, anon, c):
        await x.aclose()


async def test_revoking_the_key_closes_open_streams(live):
    app, url, c, p, rid = await setup(live)
    second = (await c.post("/keys", json={"label": "second", "scope": "read"}, headers=csrf(url))).json()
    viewer = httpx.AsyncClient(base_url=url)
    assert (await sign_in(viewer, second["key"])).status_code == 200
    async with Stream(viewer, f"/runs/{rid}/stream") as s:
        assert (await s.next())[0] == "stats"
        t0 = time.monotonic()
        assert (await c.post(f"/keys/{second['id']}/revoke", headers=csrf(url))).status_code == 200
        ended = False
        while time.monotonic() - t0 < 3:     # a stream that never closes must FAIL here, not hang the suite
            if await s.next(1) is None:
                ended = True
                break
        assert ended, "stream still open 3 s after its key was revoked"
    r = await viewer.get(f"/runs/{rid}/stream")
    assert r.status_code == 401 and r.json()["code"] == "key_revoked"
    await viewer.aclose()
    await c.aclose()


async def test_rotating_the_key_and_session_expiry_close_open_streams(live, db):
    app, url, c, p, rid = await setup(live)
    v1, v2 = httpx.AsyncClient(base_url=url), httpx.AsyncClient(base_url=url)
    k1 = (await c.post("/keys", json={"label": "a", "scope": "read"}, headers=csrf(url))).json()
    k2 = (await c.post("/keys", json={"label": "b", "scope": "read"}, headers=csrf(url))).json()
    await sign_in(v1, k1["key"])
    await sign_in(v2, k2["key"])
    async with Stream(v1, f"/runs/{rid}/stream") as s1, Stream(v2, f"/runs/{rid}/stream") as s2:
        await s1.next(), await s2.next()
        assert (await c.post(f"/keys/{k1['id']}/rotate", headers=csrf(url))).status_code == 201
        async with db.begin() as conn:
            await conn.execute(text("UPDATE sessions SET expires_at = now() - interval '1 second' WHERE key_id = :k"),
                               {"k": k2["id"]})
        for s in (s1, s2):
            end = time.monotonic() + 4
            ended = False
            while time.monotonic() < end:
                if await s.next(max(0.1, end - time.monotonic())) is None:
                    ended = True
                    break
            assert ended, "stream still open 4 s after rotation / session expiry"
    for x in (v1, v2, c):
        await x.aclose()


async def test_stream_still_delivers_when_the_listener_connection_dies(live, db):
    app, url, c, p, rid = await setup(live)
    async with Stream(c, f"/runs/{rid}/stream") as s:
        await s.next()
        await ingest(c, p, rid, "b0", [0])
        await s.until(lambda e: e[0] == "metric")
        hub = app.state.hub
        pid = hub.conn.get_server_pid()
        async with db.begin() as conn:
            await conn.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
        await ingest(c, p, rid, "b1", [1])     # notify is lost or the listener is down: the next tick must find it
        got, ok = await s.until(lambda e: e[0] == "metric" and e[2]["seq"] == 2, 4)
        assert ok, "event not delivered after the listener connection died"
        await asyncio.sleep(1.5)
        assert hub.reconnects >= 1
        await ingest(c, p, rid, "b2", [2])
        got, ok = await s.until(lambda e: e[0] == "metric" and e[2]["seq"] == 3, 4)
        assert ok
    await c.aclose()


async def test_stream_headers(live):
    app, url, c, p, rid = await setup(live)
    async with Stream(c, f"/runs/{rid}/stream") as s:
        h = s.r.headers
        assert h["content-type"].startswith("text/event-stream")
        assert "no-store" in h["cache-control"] and h["x-accel-buffering"] == "no" and "cookie" in h["vary"].lower()
    await c.aclose()


# ---------- events / seqs / integrity ----------

async def test_events_paging_and_limits(live):
    app, url, c, p, rid = await setup(live)
    await ingest(c, p, rid, "b1", range(12))
    r = await c.get(f"/runs/{rid}/events?after_seq=3&limit=4")
    j = r.json()
    assert [e["seq"] for e in j["events"]] == [4, 5, 6, 7] and j["last_seq"] == 12
    assert (await c.get(f"/runs/{rid}/events?limit=5001")).status_code == 422
    assert (await c.get(f"/runs/{rid}/events?limit=0")).status_code == 422
    allr = (await c.get(f"/runs/{rid}/events")).json()["events"]
    assert [e["seq"] for e in allr] == list(range(1, 13))
    await c.aclose()


def independent_checksum(rows):
    """Second implementation, written separately from the server's: rows = (point_id, metric, step, value)."""
    lines = []
    for pid, m, st, v in sorted(rows, key=lambda r: r[0].encode("utf-8")):
        lines.append(pid + "|" + m + "|" + str(st) + "|" + "".join("%02x" % b for b in struct.pack(">d", v)))
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest() if lines else hashlib.sha256(b"").hexdigest()


async def test_integrity_matches_an_independent_computation_and_seqs_have_no_missing(live, db):
    app, url, c, p, rid = await setup(live)
    pts = [pt("loss", 3, 0.1 + 0.2), pt("loss", 1, 1e-7), pt("acc", 1, -0.0), pt("acc", 2, 5e300, point_id="Zeta"),
           pt("acc", 3, 1.0, point_id="alpha"), pt("loss", 9, 123456789.123456789, point_id="_under"), pt("loss", 10, 2.5, point_id="a:b/c.d-e")]
    posted = await c.post(f"/runs/{rid}/metrics", headers=p.ingest, json={"batch_id": "b", "points": pts})
    assert posted.status_code == 200, posted.text
    j = (await c.get(f"/runs/{rid}/integrity")).json()
    async with db.connect() as conn:
        rows = (await conn.execute(text("SELECT point_id, metric, step, value FROM metric_events WHERE run_id=:r"), {"r": rid})).all()
    assert j["count"] == 7 and j["last_seq"] == 7
    assert j["checksum"] == independent_checksum([tuple(r) for r in rows])
    part = (await c.get(f"/runs/{rid}/integrity?up_to_seq=3")).json()
    assert part["count"] == 3 and part["last_seq"] == 3
    assert part["checksum"] != j["checksum"]
    assert (await c.get(f"/runs/{rid}/seqs")).json() == {"ranges": [[1, 7]]}
    # a hole (made directly in the DB, as a corruption would) shows up in /seqs
    async with db.begin() as conn:
        await conn.execute(text("DELETE FROM metric_events WHERE run_id=:r AND seq IN (2, 5)"), {"r": rid})
    assert (await c.get(f"/runs/{rid}/seqs")).json() == {"ranges": [[1, 1], [3, 4], [6, 7]]}
    await c.aclose()


async def test_events_seqs_integrity_are_404_for_foreign_runs(live):
    app, url, c, p, rid = await setup(live)
    other = httpx.AsyncClient(base_url=url)
    q = await new_project(other)
    await sign_in(other, q.read_key)
    for path in ("events", "seqs", "integrity"):
        r = await other.get(f"/runs/{rid}/{path}")
        miss = await other.get(f"/runs/run_{'B' * 22}/{path}")
        assert r.status_code == miss.status_code == 404 and r.json().keys() == miss.json().keys()
        assert {k: v for k, v in r.json().items() if k != "request_id"} == {k: v for k, v in miss.json().items() if k != "request_id"}
    await other.aclose()
    await c.aclose()
