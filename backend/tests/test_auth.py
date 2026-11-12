"""Projects, keys, sessions, CSRF, rate limits, basic 404 uniformity (TZ_dev.md §4, §5, §7)."""
import asyncio
import hashlib
import hmac

from sqlalchemy import text

from conftest import CSRF, client_for, new_project, post_batch, pt, sign_in


# ---------- projects and keys ----------

async def test_create_project_returns_both_keys_in_the_specified_format(client):
    r = await client.post("/projects", json={"name": "my-project-1"})
    assert r.status_code == 201
    k = r.json()["keys"]
    assert k["ingest"]["key"].startswith("trk_ing_") and len(k["ingest"]["key"]) == 40
    assert k["read"]["key"].startswith("trk_rd_") and len(k["read"]["key"]) == 40
    assert k["ingest"]["scope"] == "ingest" and k["read"]["scope"] == "read"
    assert k["ingest"]["key"].removeprefix("trk_ing_")[:4] == k["ingest"]["prefix"]
    assert r.headers["cache-control"] == "private, no-store"


async def test_project_name_rules(client):
    for bad in ("ab", "a" * 41, "has space", "under_score", "", "名前名前"):
        r = await client.post("/projects", json={"name": bad})
        assert r.status_code == 422, bad
    assert (await client.post("/projects", json={"name": "abc"})).status_code == 201
    assert (await client.post("/projects", json={"name": "a" * 40})).status_code == 201


async def test_keys_are_stored_only_as_hmac_hashes(client, proj, db, app):
    async with db.connect() as c:
        rows = (await c.execute(text("SELECT key_hash, prefix FROM api_keys WHERE project_id=:p"), {"p": proj.id})).all()
    assert len(rows) == 2
    pepper = app.state.settings.pepper.encode()
    expected = {hmac.new(pepper, k.encode(), hashlib.sha256).hexdigest() for k in (proj.ingest_key, proj.read_key)}
    assert {r.key_hash for r in rows} == expected
    async with db.connect() as c:  # the plaintext key must be nowhere in the table
        dump = (await c.execute(text("SELECT to_jsonb(a)::text FROM api_keys a WHERE project_id=:p"), {"p": proj.id})).scalars().all()
    assert not any(proj.ingest_key in d or proj.read_key in d for d in dump)


# ---------- key checks on ingest routes ----------

async def test_bad_keys_all_look_the_same(client, proj):
    wrong_scope = {"Authorization": f"Bearer {proj.read_key}"}
    cases = {
        "no header": {},
        "garbage": {"Authorization": "Bearer nope"},
        "right prefix, wrong secret": {"Authorization": "Bearer trk_ing_" + "a" * 32},
        "not bearer": {"Authorization": f"Basic {proj.ingest_key}"},
        "read key on ingest route": wrong_scope,
    }
    bodies = set()
    for name, h in cases.items():
        r = await client.post("/runs", json={"name": "x"}, headers=h)
        assert r.status_code == 401 and r.json()["code"] == "invalid_key", name
        b = r.json()
        b.pop("request_id")
        bodies.add(str(b))
    assert len(bodies) == 1, "bodies differ between kinds of bad key"


async def test_ingest_key_cannot_sign_in_and_read_key_cannot_ingest(client, proj):
    r = await sign_in(client, proj.ingest_key)
    assert r.status_code == 401 and r.json()["code"] == "invalid_key"
    r = await client.post("/runs", json={"name": "x"}, headers={"Authorization": f"Bearer {proj.read_key}"})
    assert r.status_code == 401


async def test_ingest_routes_ignore_cookies(client, proj):
    assert (await sign_in(client, proj.read_key)).status_code == 200  # cookie is now set on the client
    r = await client.post("/runs", json={"name": "x"})  # cookie but no Bearer key
    assert r.status_code == 401 and r.json()["code"] == "invalid_key"


# ---------- sessions ----------

async def test_sign_in_sets_a_strict_httponly_cookie_and_whoami_works(client, proj):
    r = await sign_in(client, proj.read_key)
    assert r.status_code == 200
    assert r.json()["project"] == {"id": proj.id, "name": proj.name}
    assert r.json()["key"]["scope"] == "read"
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc and "max-age=43200" in sc
    me = await client.get("/session")
    assert me.status_code == 200 and me.json()["project"]["id"] == proj.id


async def test_session_cookie_value_is_not_stored_in_the_database(client, proj, db):
    r = await sign_in(client, proj.read_key)
    token = client.cookies.get("tracker_session")
    async with db.connect() as c:
        ids = (await c.execute(text("SELECT id FROM sessions WHERE project_id=:p"), {"p": proj.id})).scalars().all()
    assert token and token not in ids and len(ids) == 1


async def test_no_cookie_and_expired_session(client, proj, db):
    r = await client.get("/session")
    assert r.status_code == 401 and r.json()["code"] == "session_expired"
    await sign_in(client, proj.read_key)
    async with db.begin() as c:
        await c.execute(text("UPDATE sessions SET expires_at = now() - interval '1 second' WHERE project_id=:p"), {"p": proj.id})
    r = await client.get("/session")
    assert r.status_code == 401 and r.json()["code"] == "session_expired"


async def test_sign_out_needs_csrf_and_kills_the_session(client, proj):
    await sign_in(client, proj.read_key)
    assert (await client.delete("/session")).status_code == 403
    assert (await client.delete("/session", headers=CSRF)).status_code == 204
    assert (await client.get("/session")).status_code == 401


async def test_csrf_rules_for_cookie_mutations(client, proj):
    await sign_in(client, proj.read_key)
    body = {"label": "ci", "scope": "ingest"}
    for headers in ({}, {"X-Requested-With": "tracker"}, {"Origin": "http://test"},
                    {"X-Requested-With": "tracker", "Origin": "http://evil.example"},
                    {"X-Requested-With": "xhr", "Origin": "http://test"}):
        r = await client.post("/keys", json=body, headers=headers)
        assert r.status_code == 403 and r.json()["code"] == "csrf_failed", headers
    assert (await client.post("/keys", json=body, headers=CSRF)).status_code == 201
    assert (await client.get("/keys")).status_code == 200  # reads need no CSRF header


async def test_revoked_read_key_ends_its_sessions(client, proj):
    await sign_in(client, proj.read_key)
    other = await client.post("/keys", json={"label": "second", "scope": "read"}, headers=CSRF)
    assert other.status_code == 201
    r = await client.post(f"/keys/{proj.read_key_id}/revoke", headers=CSRF)  # own key; another read key exists
    assert r.status_code == 200 and r.json()["revoked_at"]
    me = await client.get("/session")
    assert me.status_code == 401 and me.json()["code"] == "key_revoked"
    again = await sign_in(client, proj.read_key)
    assert again.status_code == 401 and again.json()["code"] == "key_revoked"


# ---------- key management ----------

async def test_keys_list_never_contains_secrets(client, proj):
    await sign_in(client, proj.read_key)
    r = await client.get("/keys")
    assert r.status_code == 200 and len(r.json()["keys"]) == 2
    assert proj.ingest_key not in r.text and proj.read_key not in r.text and "key_hash" not in r.text
    assert all("key" not in k for k in r.json()["keys"])


async def test_create_key_returns_the_secret_once_and_it_works(client, proj):
    await sign_in(client, proj.read_key)
    r = await client.post("/keys", json={"label": "ci", "scope": "ingest"}, headers=CSRF)
    assert r.status_code == 201
    new = r.json()["key"]
    assert new.startswith("trk_ing_") and len(new) == 40
    ok = await client.post("/runs", json={"name": "x"}, headers={"Authorization": f"Bearer {new}"})
    assert ok.status_code == 201
    assert "key" not in next(k for k in (await client.get("/keys")).json()["keys"] if k["id"] == r.json()["id"])
    assert (await client.post("/keys", json={"label": "x", "scope": "admin"}, headers=CSRF)).status_code == 422


async def test_rotate_revokes_old_key_immediately_and_new_key_works(client, proj):
    await sign_in(client, proj.read_key)
    r = await client.post(f"/keys/{proj.ingest_key_id}/rotate", headers=CSRF)
    assert r.status_code == 201 and r.json()["scope"] == "ingest" and r.json()["label"] == "Default ingest"
    new = r.json()["key"]
    old = await client.post("/runs", json={"name": "x"}, headers=proj.ingest)
    assert old.status_code == 401 and old.json()["code"] == "key_revoked"
    assert (await client.post("/runs", json={"name": "x"}, headers={"Authorization": f"Bearer {new}"})).status_code == 201
    again = await client.post(f"/keys/{proj.ingest_key_id}/rotate", headers=CSRF)  # old one is already revoked
    assert again.status_code == 409 and again.json()["code"] == "key_revoked"


async def test_revoke_ingest_key(client, proj):
    await sign_in(client, proj.read_key)
    r = await client.post(f"/keys/{proj.ingest_key_id}/revoke", headers=CSRF)
    assert r.status_code == 200
    bad = await client.post("/runs", json={"name": "x"}, headers=proj.ingest)
    assert bad.status_code == 401 and bad.json()["code"] == "key_revoked"
    assert (await client.post(f"/keys/{proj.ingest_key_id}/revoke", headers=CSRF)).json()["code"] == "already_revoked"


async def test_cannot_revoke_the_last_active_read_key(client, proj):
    await sign_in(client, proj.read_key)
    r = await client.post(f"/keys/{proj.read_key_id}/revoke", headers=CSRF)
    assert r.status_code == 409 and r.json()["code"] == "last_read_key"
    assert (await client.get("/session")).status_code == 200  # nothing was revoked


async def test_two_concurrent_revokes_cannot_remove_the_last_two_read_keys(client, proj, app, db):
    # Two read keys A (default) and B, each with its own session. Each session tries to revoke the OTHER key at once.
    # Whichever commits first may also kill the other request's own session (401), or hit the guard (409).
    # What must never happen: both revokes succeed, leaving the project with no read key.
    await sign_in(client, proj.read_key)
    b = (await client.post("/keys", json={"label": "second", "scope": "read"}, headers=CSRF)).json()
    async with client_for(app) as c2:
        assert (await sign_in(c2, b["key"])).status_code == 200
        rs = await asyncio.gather(
            client.post(f"/keys/{b['id']}/revoke", headers=CSRF),
            c2.post(f"/keys/{proj.read_key_id}/revoke", headers=CSRF))
    codes = sorted(r.status_code for r in rs)
    assert codes != [200, 200], codes
    assert all(c in (200, 401, 409) for c in codes), codes
    async with db.connect() as c:
        active = (await c.execute(text("SELECT count(*) FROM api_keys WHERE project_id=:p AND scope='read' AND revoked_at IS NULL"),
                                  {"p": proj.id})).scalar_one()
    assert active >= 1


async def test_rotating_the_only_read_key_is_allowed(client, proj, app):
    await sign_in(client, proj.read_key)
    r = await client.post(f"/keys/{proj.read_key_id}/rotate", headers=CSRF)
    assert r.status_code == 201
    new = r.json()["key"]
    c2 = client_for(app)  # fresh cookie jar
    async with c2:
        assert (await sign_in(c2, new)).status_code == 200


async def test_foreign_and_malformed_key_ids_are_plain_404(client, proj):
    other = await new_project(client)
    await sign_in(client, proj.read_key)
    bodies = set()
    for kid in (other.ingest_key_id, "00000000-0000-0000-0000-000000000000", "not-a-uuid", "1"):
        for path in ("rotate", "revoke"):
            r = await client.post(f"/keys/{kid}/{path}", headers=CSRF)
            assert r.status_code == 404, (kid, path)
            b = r.json()
            b.pop("request_id")
            bodies.add(str(b))
    assert len(bodies) == 1
    # and the other project's key was not touched
    ok = await client.post("/runs", json={"name": "x"}, headers=other.ingest)
    assert ok.status_code == 201


# ---------- last_used_at ----------

async def test_last_used_at_is_written_at_most_once_a_minute(client, proj, db):
    async def last_used():
        async with db.connect() as c:
            return (await c.execute(text("SELECT last_used_at FROM api_keys WHERE id=:i"), {"i": proj.ingest_key_id})).scalar_one()
    assert await last_used() is None
    await client.post("/runs", json={"name": "a"}, headers=proj.ingest)
    first = await last_used()
    assert first is not None
    await asyncio.sleep(0.05)
    for _ in range(5):
        await client.post("/runs", json={"name": "b"}, headers=proj.ingest)
    assert await last_used() == first


# ---------- headers and uniform errors ----------

async def test_authenticated_and_error_responses_are_never_cacheable(client, proj, run_id):
    await sign_in(client, proj.read_key)
    resps = [
        await client.get("/session"), await client.get("/keys"), await client.get(f"/runs/{run_id}"),
        await client.get("/runs/run_doesnotexist"), await client.get("/nope"),
        await client.post("/runs", json={"name": "x"}, headers=proj.ingest),
        await client.post("/runs", json={"name": "x"}), await client.post("/projects", json={"name": "x"}),
    ]
    for r in resps:
        assert r.headers["cache-control"] == "private, no-store", r.request.url
        assert r.headers["vary"] == "Cookie, Authorization", r.request.url
        assert r.headers["x-request-id"]


async def test_error_bodies_have_the_uniform_shape(client, proj):
    for r in (await client.get("/nope"), await client.get("/session"), await client.post("/projects", json={})):
        assert set(r.json()) >= {"code", "message", "request_id"}
        assert r.json()["request_id"] == r.headers["x-request-id"]


async def test_foreign_missing_and_malformed_run_ids_are_indistinguishable(client, proj, proj_factory):
    other, other_run = await proj_factory(client)
    await sign_in(client, proj.read_key)
    probes = [other_run, "run_" + "A" * 22, "run_short", "../etc/passwd", "1", "x" * 500, "run_%00"]
    seen = []
    for rid in probes:
        for req in (lambda i: client.get(f"/runs/{i}"),
                    lambda i: client.post(f"/runs/{i}/finish", json={"status": "finished"}, headers=proj.ingest),
                    lambda i: post_batch(client, proj, i, "b", [pt()])):
            r = await req(rid)
            assert r.status_code == 404, (rid, r.text)
            b = r.json()
            b.pop("request_id")
            seen.append((str(b), tuple(sorted((k, v) for k, v in r.headers.items() if k in ("cache-control", "vary", "content-type")))))
    assert len(set(seen)) == 1
    # the foreign run is untouched
    assert (await client.post(f"/runs/{other_run}/metrics", json={"batch_id": "b", "points": [pt()]}, headers=other.ingest)).status_code == 200


async def test_get_run_shape_and_stale(client, proj, run_id, db):
    await sign_in(client, proj.read_key)
    await post_batch(client, proj, run_id, "b1", [pt(step=0), pt(step=1)])
    await post_batch(client, proj, run_id, "b2", [pt(step=1)])
    r = await client.get(f"/runs/{run_id}")
    j = r.json()
    assert j["id"] == run_id and j["status"] == "running" and j["last_seq"] == 2 and j["count"] == 2
    assert j["duplicates_ignored"] == 1 and j["stale"] is False and j["stale_after_s"] == 300
    assert j["last_event_at"] and j["server_time"] and j["finished_at"] is None
    async with db.begin() as c:
        await c.execute(text("UPDATE runs SET last_event_at = now() - interval '6 minutes' WHERE id=:r"), {"r": run_id})
    assert (await client.get(f"/runs/{run_id}")).json()["stale"] is True


# ---------- rate limits ----------

async def test_rate_limit_on_project_creation(make_app):
    app = await make_app(rl_projects_per_min=3)
    async with client_for(app) as c:
        codes = [(await c.post("/projects", json={"name": f"rl-{i}-{id(c)}"})).status_code for i in range(5)]
        assert codes == [201, 201, 201, 429, 429]
        r = await c.post("/projects", json={"name": "rl-last"})
        assert r.json()["code"] == "rate_limited" and int(r.headers["retry-after"]) >= 1


async def test_failed_key_lookups_are_limited_per_ip(make_app, proj_factory):
    app = await make_app(rl_auth_fail_per_min=4)
    async with client_for(app) as c:
        p, _ = await proj_factory(c)
        bad = {"Authorization": "Bearer trk_ing_" + "z" * 32}
        codes = [(await c.post("/runs", json={"name": "x"}, headers=bad)).status_code for _ in range(6)]
        assert codes == [401, 401, 401, 401, 429, 429]
        r = await c.post("/runs", json={"name": "x"}, headers=p.ingest)  # even a good key waits: the limit is per IP
        assert r.status_code == 429 and "retry-after" in r.headers


async def test_good_keys_do_not_consume_the_failure_budget(make_app, proj_factory):
    app = await make_app(rl_auth_fail_per_min=2)
    async with client_for(app) as c:
        p, _ = await proj_factory(c)
        for _ in range(10):
            assert (await c.post("/runs", json={"name": "x"}, headers=p.ingest)).status_code == 201


async def test_session_attempts_are_limited(make_app, proj_factory):
    app = await make_app(rl_session_per_min=3)
    async with client_for(app) as c:
        p, _ = await proj_factory(c)
        codes = [(await sign_in(c, p.read_key)).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]


async def test_ingest_rate_limit_is_per_project(make_app, proj_factory):
    app = await make_app(rl_ingest_per_s=5)
    async with client_for(app) as c:
        a, run_a = await proj_factory(c)   # uses 1 request of A's budget
        b, run_b = await proj_factory(c)
        codes_a = [(await post_batch(c, a, run_a, f"b{i}", [pt(step=i)])).status_code for i in range(6)]
        assert codes_a.count(429) >= 1 and codes_a[:4] == [200] * 4
        assert (await post_batch(c, b, run_b, "bb", [pt()])).status_code == 200  # B is unaffected


async def test_trusted_proxy_setting_uses_forwarded_for(make_app):
    app = await make_app(rl_projects_per_min=1, trusted_proxy_hops=1)
    async with client_for(app) as c:
        h1, h2 = {"X-Forwarded-For": "1.1.1.1"}, {"X-Forwarded-For": "2.2.2.2"}
        assert (await c.post("/projects", json={"name": "ip-one-aaa"}, headers=h1)).status_code == 201
        assert (await c.post("/projects", json={"name": "ip-one-bbb"}, headers=h1)).status_code == 429
        assert (await c.post("/projects", json={"name": "ip-two-aaa"}, headers=h2)).status_code == 201
        # a client-supplied extra hop on the left does not let it pick its own bucket
        spoof = {"X-Forwarded-For": "9.9.9.9, 1.1.1.1"}
        assert (await c.post("/projects", json={"name": "ip-one-ccc"}, headers=spoof)).status_code == 429


# ---------- GET /runs (list with aggregates) ----------

async def test_runs_list_aggregates_and_isolation(client, proj, proj_factory):
    other, other_run = await proj_factory(client)
    await post_batch(client, other, other_run, "o", [pt("zzz", 1, 9.0)])
    r1 = (await client.post("/runs", json={"name": "first"}, headers=proj.ingest)).json()["id"]
    r2 = (await client.post("/runs", json={"name": "second"}, headers=proj.ingest)).json()["id"]
    r3 = (await client.post("/runs", json={"name": "empty"}, headers=proj.ingest)).json()["id"]
    await post_batch(client, proj, r1, "b1", [pt("loss", s, 1.0 / (s + 1)) for s in range(100)] + [pt("acc", 0, 0.5)])
    await post_batch(client, proj, r2, "b1", [pt("acc", 3, 0.7), pt("acc", 1, 0.1), pt("loss", 1, 2.0)])
    await sign_in(client, proj.read_key)
    j = (await client.get("/runs")).json()
    assert [r["name"] for r in j["runs"]] == ["empty", "second", "first"]  # newest first; the other project's run is absent
    assert other_run not in {r["id"] for r in j["runs"]}
    by = {r["id"]: r for r in j["runs"]}
    assert by[r1]["primary_metric"] == "loss" and by[r1]["events_count"] == 101
    assert by[r1]["latest_value"] == 1.0 / 100                      # highest step of the primary metric
    assert 1 < len(by[r1]["spark"]) <= 30 and by[r1]["spark"][0] == 1.0
    assert by[r2]["primary_metric"] == "acc" and by[r2]["latest_value"] == 0.7 and by[r2]["spark"] == [0.1, 0.7]
    assert by[r3]["primary_metric"] is None and by[r3]["latest_value"] is None and by[r3]["spark"] == []
    assert by[r3]["last_event_at"] is None and by[r1]["last_event_at"]
