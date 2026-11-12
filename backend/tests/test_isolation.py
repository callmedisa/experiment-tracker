"""Isolation matrix (the dev spec §8.2). Two projects, A and B, each with runs and keys. Acting as A, nothing of B's may be
readable or writable, and a foreign id must be indistinguishable from a missing or malformed one.
(Timing, shared-cache and log checks need the real stack: see tests/stack/ in the repo root.)"""
import asyncio
import logging
from urllib.parse import quote

import pytest
from sqlalchemy import text

from conftest import CSRF, new_project, post_batch, pt, sign_in

BAD_IDS = [
    "run_" + "A" * 22,                 # well-formed, does not exist
    "run_" + "B" * 21,                 # wrong length
    "run_short", "1", "0", "-1", "null", "undefined", "run_",
    "../../etc/passwd", "run_%00", "run_\u0000", "x" * 600, "éè", "run_' OR '1'='1", "%", "_", "run_1; DROP TABLE runs",
]

# (name, needs, call) - `needs` = which credential the route requires
READ_ROUTES = [
    ("get run", lambda c, i: c.get(f"/runs/{enc(i)}")),
    ("events", lambda c, i: c.get(f"/runs/{enc(i)}/events")),
    ("events after_seq", lambda c, i: c.get(f"/runs/{enc(i)}/events?after_seq=0&limit=10")),
    ("seqs", lambda c, i: c.get(f"/runs/{enc(i)}/seqs")),
    ("integrity", lambda c, i: c.get(f"/runs/{enc(i)}/integrity")),
    ("integrity up_to", lambda c, i: c.get(f"/runs/{enc(i)}/integrity?up_to_seq=1")),
    ("stream", lambda c, i: asgi_stream(c, f"/runs/{enc(i)}/stream")),
    ("stream after_seq", lambda c, i: asgi_stream(c, f"/runs/{enc(i)}/stream?after_seq=0")),
    ("stream Last-Event-ID", lambda c, i: asgi_stream(c, f"/runs/{enc(i)}/stream", {"Last-Event-ID": "5"})),
]


class _Resp:
    def __init__(self, status, headers, body):
        self.status_code, self.headers, self._body = status, headers, body
        self.text = body.decode(errors="replace")

    def json(self):
        import json
        return json.loads(self._body)


async def asgi_stream(c, path, headers=None):
    """GET a (possibly streaming) route by calling the ASGI app directly and disconnecting as soon as the response starts,
    so a route that wrongly opens a stream FAILS the comparison instead of hanging the test."""
    app = c._transport.app
    path_, _, qs = path.partition("?")
    cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    hdrs = [(b"host", b"test"), (b"cookie", cookie.encode())] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http", "path": path_, "raw_path": path_.encode(), "query_string": qs.encode(),
             "headers": hdrs, "client": ("127.0.0.1", 1), "server": ("test", 80), "root_path": ""}
    msgs, started = [], asyncio.Event()

    async def receive():
        await started.wait()
        await asyncio.sleep(0.05)
        return {"type": "http.disconnect"}

    async def send(m):
        msgs.append(m)
        if m["type"] == "http.response.start":
            started.set()

    await asyncio.wait_for(app(scope, receive, send), 8)
    start = next(m for m in msgs if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in msgs if m["type"] == "http.response.body")
    return _Resp(start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, body)


def enc(rid: str) -> str:
    """Percent-encode like a real client would (httpx refuses raw control characters); an existing %xx is kept."""
    return quote(rid, safe="%")


def shape(r):
    """What a caller could use to tell two 404s apart: status, body (minus the per-request id), the headers that matter."""
    b = r.json()
    b.pop("request_id", None)
    return (r.status_code, tuple(sorted(b.items())), r.headers.get("cache-control"), r.headers.get("vary"), r.headers.get("content-type"))


async def two_projects(client):
    a, b = await new_project(client), await new_project(client)
    run_a = (await client.post("/runs", json={"name": "a-run"}, headers=a.ingest)).json()["id"]
    run_b = (await client.post("/runs", json={"name": "b-run"}, headers=b.ingest)).json()["id"]
    await post_batch(client, b, run_b, "b1", [pt("secret-metric", i, 42.0 + i) for i in range(5)])
    await post_batch(client, a, run_a, "a1", [pt("loss", i, 1.0) for i in range(3)])
    return a, b, run_a, run_b


def sibling_ids(rid: str):
    """'Guessing' B's id: neighbours of a real id (sequential-style) and its prefix."""
    last = rid[-1]
    other = "A" if last != "A" else "B"
    return [rid[:-1] + other, rid[:-2] + "AA", rid[:12], rid.upper(), rid.lower()]


# ---------- reading B's data as A ----------

async def test_every_read_route_gives_the_same_404_for_foreign_missing_and_malformed_ids(client):
    a, b, run_a, run_b = await two_projects(client)
    await sign_in(client, a.read_key)
    for name, call in READ_ROUTES:
        baseline = shape(await call(client, "run_" + "Z" * 22))
        assert baseline[0] == 404, name
        for rid in [run_b, *sibling_ids(run_b), *BAD_IDS]:
            r = await asyncio.wait_for(call(client, rid), 5)   # a leaking stream never ends: fail instead of hanging
            assert shape(r) == baseline, f"{name}: id {rid!r} is distinguishable from a missing id: {r.status_code} {r.text}"
        own = await call(client, run_a)           # sanity: the same call works on A's own run
        assert own.status_code == 200, name


async def test_b_marker_data_never_appears_in_any_response_to_a(client):
    a, b, run_a, run_b = await two_projects(client)
    await sign_in(client, a.admin_key)
    blobs = []
    for _, call in READ_ROUTES:
        for rid in [run_b, *sibling_ids(run_b)]:
            blobs.append((await call(client, rid)).text)
    blobs.append((await client.get("/runs")).text)
    blobs.append((await client.get("/keys")).text)
    blobs.append((await client.get("/session")).text)
    joined = "\n".join(blobs)
    for secret in ("secret-metric", run_b, b.id, b.name, b.ingest_key, b.read_key, b.admin_key, b.ingest_key_id, b.read_key_id, b.admin_key_id):
        assert secret not in joined, f"B's {secret!r} leaked into a response seen by A"


async def test_runs_list_and_keys_list_contain_only_the_callers_project(client):
    a, b, run_a, run_b = await two_projects(client)
    for _ in range(4):
        await client.post("/runs", json={"name": "b-extra"}, headers=b.ingest)
    await sign_in(client, a.admin_key)
    runs = (await client.get("/runs")).json()["runs"]
    assert [r["id"] for r in runs] == [run_a]
    keys = (await client.get("/keys")).json()["keys"]
    assert {k["id"] for k in keys} == {a.ingest_key_id, a.read_key_id, a.admin_key_id}


# ---------- writing to B's run as A ----------

async def test_a_cannot_write_to_or_finish_bs_run_with_any_id_variant(client, db):
    a, b, run_a, run_b = await two_projects(client)
    base_w = shape(await post_batch(client, a, "run_" + "Z" * 22, "x", [pt()]))
    base_f = shape(await client.post("/runs/run_" + "Z" * 22 + "/finish", json={"status": "finished"}, headers=a.ingest))
    assert base_w[0] == base_f[0] == 404
    for rid in [run_b, *sibling_ids(run_b), *BAD_IDS]:
        w = await post_batch(client, a, enc(rid), "attack", [pt("pwn", 1, 666.0)])
        f = await client.post(f"/runs/{enc(rid)}/finish", json={"status": "failed"}, headers=a.ingest)
        assert shape(w) == base_w, rid
        assert shape(f) == base_f, rid
    async with db.connect() as c:
        row = (await c.execute(text("SELECT status, last_seq, duplicates_ignored FROM runs WHERE id=:r"), {"r": run_b})).one()
        n_pwn = (await c.execute(text("SELECT count(*) FROM metric_events WHERE metric='pwn'"))).scalar_one()
        batches = (await c.execute(text("SELECT count(*) FROM ingest_batches WHERE batch_id='attack'"))).scalar_one()
    assert tuple(row) == ("running", 5, 0), "B's run was modified by A"
    assert n_pwn == 0 and batches == 0


async def test_a_cannot_pass_a_project_id_anywhere(client):
    a, b, run_a, run_b = await two_projects(client)
    await sign_in(client, a.read_key)
    # routes take no project id; extra body fields are rejected, extra query params are ignored, header tricks do nothing
    r = await client.post("/runs", json={"name": "x", "project_id": b.id}, headers=a.ingest)
    assert r.status_code == 422
    r = await client.get(f"/runs?project_id={b.id}")
    assert [x["id"] for x in r.json()["runs"]] == [run_a]
    r = await client.get("/runs", headers={"X-Project-Id": b.id, "X-Forwarded-For": "1.2.3.4"})
    assert [x["id"] for x in r.json()["runs"]] == [run_a]


# ---------- credentials: scope, revocation, CSRF ----------

SESSION_ROUTES = [("get", "/runs"), ("get", "/keys"), ("get", "/session"), ("get", "/runs/{r}"), ("get", "/runs/{r}/events"),
                  ("get", "/runs/{r}/seqs"), ("get", "/runs/{r}/integrity"), ("get", "/runs/{r}/stream")]
INGEST_ROUTES = [("post", "/runs", {"name": "x"}), ("post", "/runs/{r}/metrics", {"batch_id": "b", "points": [pt()]}),
                 ("post", "/runs/{r}/finish", {"status": "finished"})]


async def test_ingest_key_cannot_read_and_read_key_cannot_ingest_on_any_route(client):
    a, b, run_a, run_b = await two_projects(client)
    for method, path in SESSION_ROUTES:   # no cookie, ingest key as Bearer: not a session credential
        r = await client.request(method, path.format(r=run_a), headers=a.ingest)
        assert r.status_code == 401 and r.json()["code"] == "session_expired", path
    for method, path, body in INGEST_ROUTES:   # read key as Bearer on ingest routes
        r = await client.request(method, path.format(r=run_a), json=body, headers={"Authorization": f"Bearer {a.read_key}"})
        assert r.status_code == 401 and r.json()["code"] == "invalid_key", path
    await sign_in(client, a.read_key)          # now a valid read session cookie
    for method, path, body in INGEST_ROUTES:   # a cookie is not an ingest credential either
        r = await client.request(method, path.format(r=run_a), json=body)
        assert r.status_code == 401 and r.json()["code"] == "invalid_key", path


async def test_revoked_ingest_key_fails_on_every_ingest_route_and_revoked_session_key_on_every_session_route(client):
    a, b, run_a, run_b = await two_projects(client)
    await sign_in(client, a.admin_key)
    extra = (await client.post("/keys", json={"label": "keep", "scope": "admin"}, headers=CSRF)).json()
    assert (await client.post(f"/keys/{a.ingest_key_id}/revoke", headers=CSRF)).status_code == 200
    for method, path, body in INGEST_ROUTES:
        r = await client.request(method, path.format(r=run_a), json=body, headers=a.ingest)
        assert r.status_code == 401 and r.json()["code"] == "key_revoked", path
    assert (await client.post(f"/keys/{a.admin_key_id}/revoke", headers=CSRF)).status_code == 200   # the session's own key
    for method, path in SESSION_ROUTES:
        r = await client.request(method, path.format(r=run_a))
        assert r.status_code == 401 and r.json()["code"] == "key_revoked", path
    assert (await sign_in(client, a.admin_key)).json()["code"] == "key_revoked"
    assert (await sign_in(client, extra["key"])).status_code == 200      # the kept key still works


async def test_every_cookie_mutation_needs_the_csrf_header_and_a_matching_origin(client):
    a, b, run_a, run_b = await two_projects(client)
    await sign_in(client, a.admin_key)
    other_key = (await client.post("/keys", json={"label": "o", "scope": "ingest"}, headers=CSRF)).json()
    muts = [("post", "/keys", {"label": "x", "scope": "read"}), ("post", f"/keys/{other_key['id']}/rotate", None),
            ("post", f"/keys/{other_key['id']}/revoke", None), ("delete", "/session", None)]
    bad_headers = [{}, {"X-Requested-With": "tracker"}, {"Origin": "http://test"}, {"X-Requested-With": "tracker", "Origin": "http://evil.example"},
                   {"X-Requested-With": "tracker", "Origin": "null"}, {"X-Requested-With": "tracker", "Origin": "http://test.evil.example"}]
    for method, path, body in muts:
        for h in bad_headers:
            r = await client.request(method, path, json=body, headers=h)
            assert r.status_code == 403 and r.json()["code"] == "csrf_failed", (method, path, h, r.status_code)
    keys = (await client.get("/keys")).json()["keys"]          # nothing happened
    assert len(keys) == 4 and all(k["revoked_at"] is None for k in keys)
    assert (await client.get("/session")).status_code == 200


async def test_foreign_key_ids_cannot_be_rotated_or_revoked(client):
    a, b, run_a, run_b = await two_projects(client)
    await sign_in(client, a.admin_key)
    for kid in (b.ingest_key_id, b.read_key_id, b.admin_key_id):
        for action in ("rotate", "revoke"):
            r = await client.post(f"/keys/{kid}/{action}", headers=CSRF)
            assert r.status_code == 404 and r.json()["code"] == "not_found"
    ok = await client.post("/runs", json={"name": "still-works"}, headers=b.ingest)
    assert ok.status_code == 201
    await sign_in(client, b.read_key)  # b's read key untouched too
    await sign_in(client, b.admin_key)


async def test_a_session_cookie_is_bound_to_its_own_project(client, app):
    from conftest import client_for
    a, b, run_a, run_b = await two_projects(client)
    async with client_for(app) as ca, client_for(app) as cb:
        await sign_in(ca, a.read_key)
        await sign_in(cb, b.read_key)
        assert (await ca.get(f"/runs/{run_a}")).status_code == 200 and (await cb.get(f"/runs/{run_a}")).status_code == 404
        assert (await cb.get(f"/runs/{run_b}")).status_code == 200 and (await ca.get(f"/runs/{run_b}")).status_code == 404
        # swapping cookies between clients (the cache-poisoning shape) yields the other project's view, never a mix
        cb.cookies.set("tracker_session", ca.cookies.get("tracker_session"))
        assert (await cb.get(f"/runs/{run_a}")).status_code == 200
        assert (await cb.get(f"/runs/{run_b}")).status_code == 404


# ---------- logs never contain secrets ----------

async def test_logs_contain_no_keys_cookies_or_metric_payloads(client, caplog):
    caplog.set_level(logging.DEBUG)
    a, b, run_a, run_b = await two_projects(client)
    r = await sign_in(client, a.admin_key)
    token = client.cookies.get("tracker_session")
    new_ing = (await client.post("/keys", json={"label": "n", "scope": "ingest"}, headers=CSRF)).json()
    rot = (await client.post(f"/keys/{a.ingest_key_id}/rotate", headers=CSRF)).json()
    marker = "MARKER_METRIC_f00dcafe"
    new_auth = {"Authorization": f"Bearer {rot['key']}"}      # the rotated key (the old one is revoked now)
    r500 = await post_batch(client, a, run_a, "bm", [pt(marker, 0, 123456.789)], **{"X-Fault": "mid-batch"}, **new_auth)
    assert r500.status_code == 500                                                                          # a 500 with a stack path
    await post_batch(client, a, run_a, "bm2", [pt(marker, 1, 123456.789)], **new_auth)
    await client.post("/runs", json={"name": "x"}, headers={"Authorization": "Bearer trk_ing_" + "q" * 32})  # unknown key
    await client.post("/runs", json={"name": "x"}, headers=a.ingest)                                          # revoked key
    await client.get("/runs", headers={"Authorization": f"Bearer {b.ingest_key}"})
    text_ = "\n".join(f"{rec.name}|{rec.getMessage()}|{rec.exc_text or ''}" for rec in caplog.records if rec.name != "httpx")
    assert text_.strip(), "no application log lines captured: the check would be vacuous"
    for secret in (a.ingest_key, a.read_key, a.admin_key, b.ingest_key, b.read_key, b.admin_key, new_ing["key"], rot["key"], token, marker, "123456.789", a.admin_key[-12:], a.read_key[-12:], a.ingest_key[-12:]):
        assert secret not in text_, f"secret {secret[:12]}... found in logs"
