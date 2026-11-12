"""Isolation checks that need the real stack: shared-cache paths through nginx, a statistical timing test, secrets in
real container logs, and open streams closed on rotation through nginx (TZ_dev.md §8.2, §8.3.5)."""
import json
import random
import statistics
import threading
import time
from pathlib import Path

import httpx

from conftest import CHAOS_GATEWAY, GATEWAY, REPO, Proj, compose_logs, hdr

OUT = REPO / "results" / "stack"


# ---------------------------------------------------------------- cache paths
def test_no_shared_cache_in_the_compose_config():
    conf = (REPO / "web" / "nginx.conf").read_text()
    assert "proxy_cache " not in conf.replace("proxy_cache off", "") and "proxy_cache_path" not in conf
    compose = (REPO / "docker-compose.yml").read_text().lower()
    for cache in ("varnish", "squid", "traefik", "redis", "memcached", "cdn"):
        assert cache not in compose


def test_same_url_with_a_and_b_cookies_never_serves_the_other_projects_body(two):
    for base in (GATEWAY, CHAOS_GATEWAY):
        a, b = Proj(base), Proj(base)
        url = f"/runs/{a.run_id}"
        seen = []
        for i in range(30):                    # alternate rapidly, as a cache would be hit
            for who, client in (("A", a.signed), ("B", b.signed), ("none", httpx.Client(base_url=base + "/api"))):
                r = client.get(url, headers={"Accept": "application/json"})
                seen.append((who, r.status_code, r.headers.get("cache-control"), r.headers.get("vary")))
                if who == "A":
                    assert r.status_code == 200 and r.json()["id"] == a.run_id
                elif who == "B":
                    assert r.status_code == 404 and a.run_id not in r.text and "stack-marker-metric" not in r.text
                else:
                    assert r.status_code == 401
        assert all(cc == "private, no-store" and "Cookie" in v for _, _, cc, v in seen), "cache headers missing on some response"
        # list endpoints too
        la, lb = a.signed.get("/runs").json()["runs"], b.signed.get("/runs").json()["runs"]
        assert [x["id"] for x in la] == [a.run_id] and [x["id"] for x in lb] == [b.run_id]


def test_conditional_requests_and_cache_headers_do_not_leak_or_revalidate(two):
    a, b = two
    r = a.signed.get(f"/runs/{a.run_id}")
    for h in ({"If-None-Match": "*"}, {"If-Modified-Since": "Wed, 21 Oct 2037 07:28:00 GMT"}, {"Cache-Control": "max-age=3600"},
              {"Pragma": "cache"}, {"Range": "bytes=0-5"}):
        rb = b.signed.get(f"/runs/{a.run_id}", headers=h)
        assert rb.status_code == 404, h
        ra = a.signed.get(f"/runs/{a.run_id}", headers=h)
        assert ra.status_code == 200 and ra.json()["id"] == a.run_id, h
    for banned in ("age", "x-cache", "via", "etag", "last-modified", "expires"):
        assert banned not in r.headers, f"unexpected caching header {banned}: {r.headers.get(banned)}"


def test_static_app_shell_is_not_cached_and_api_errors_are_private(stack_ok):
    r = httpx.get(GATEWAY + "/runs/run_anything")                     # SPA route: index.html
    assert r.status_code == 200 and "no-store" in r.headers.get("cache-control", "")
    e = httpx.get(GATEWAY + "/api/runs/run_x")
    assert e.status_code == 401 and e.headers["cache-control"] == "private, no-store"


def test_csrf_through_the_gateway(two):
    a, _ = two
    assert a.signed.post("/keys", json={"label": "x", "scope": "read"}).status_code == 403
    assert a.signed.post("/keys", json={"label": "x", "scope": "read"}, headers={"X-Requested-With": "tracker", "Origin": "http://evil.example"}).status_code == 403
    assert a.signed.post("/keys", json={"label": "x", "scope": "read"}, headers=a.csrf()).status_code == 201


def test_malformed_ids_are_the_same_404_through_the_gateway_as_missing_ids(two):
    """Includes ids that nginx itself rejects (invalid %-escapes): they must not get nginx's HTML 400 page."""
    a, b = two
    def shape(r):
        j = r.json()
        j.pop("request_id", None)
        return (r.status_code, tuple(sorted(j.items())), r.headers.get("content-type", "").split(";")[0], r.headers.get("cache-control"), r.headers.get("vary"))
    base = shape(b.signed.get("/runs/run_" + "A" * 22))
    assert base[0] == 404
    for rid in [a.run_id, "run_%00", "%", "%zz", "run_%zz", "run_x", "x" * 600, "run_%20x", "%C3%A9", "run_'%20OR%20'1'='1", "run_%0d%0aX-Injected:%201"]:
        for path in (f"/runs/{rid}", f"/runs/{rid}/events", f"/runs/{rid}/stream"):
            r = b.signed.get(path)
            assert shape(r) == base, f"{path}: {r.status_code} {r.text[:80]!r}"
    assert "server" not in {k.lower() for k in b.signed.get("/runs/x").headers} or "nginx/" not in b.signed.get("/runs/x").headers.get("server", "")


# ---------------------------------------------------------------- timing (statistical)
def perm_p(x, y, n=4000, seed=1):
    """Two-sided permutation test on the difference of medians."""
    rnd = random.Random(seed)
    obs = abs(statistics.median(x) - statistics.median(y))
    pool, k, more = list(x) + list(y), len(x), 0
    for _ in range(n):
        rnd.shuffle(pool)
        if abs(statistics.median(pool[:k]) - statistics.median(pool[k:])) >= obs:
            more += 1
    return (more + 1) / (n + 1)


def test_unknown_vs_foreign_vs_malformed_run_ids_take_the_same_time(two):
    """Interleaved samples over one keep-alive connection, GET /runs/{id} as project B.
    Tolerance (documented): a variant passes if |median difference to the 'foreign id' baseline| <= max(0.25 ms, 3x the
    A/A noise, i.e. the median difference between two random halves of the baseline itself). p-values are reported, not
    used as the only criterion, because with thousands of samples a 0.02 ms difference is 'significant' and irrelevant."""
    a, b = two
    N = 400
    variants = {
        "foreign (A's real id)": a.run_id,
        "random well-formed, missing": "run_" + "".join(random.Random(5).choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(22)),
        "neighbour of A's id": a.run_id[:-1] + ("A" if a.run_id[-1] != "A" else "B"),
        "malformed (short)": "run_x",
        "malformed (space)": "run_%20x",
        "malformed (600 chars)": "x" * 600,
    }
    samples = {k: [] for k in variants}
    cl = b.signed
    for k, v in variants.items():            # warm-up
        for _ in range(20):
            cl.get(f"/runs/{v}")
    order = list(variants)
    rnd = random.Random(11)
    for _ in range(N):
        rnd.shuffle(order)                    # interleave and randomise order so drift hits every variant alike
        for k in order:
            t0 = time.perf_counter()
            r = cl.get(f"/runs/{variants[k]}")
            samples[k].append((time.perf_counter() - t0) * 1000)
            assert r.status_code == 404
    base = samples["foreign (A's real id)"]
    shuffled = base[:]
    random.Random(3).shuffle(shuffled)
    noise = abs(statistics.median(shuffled[: N // 2]) - statistics.median(shuffled[N // 2:]))
    tol = max(0.25, 3 * noise)
    rows = []
    for k, v in samples.items():
        d = statistics.median(v) - statistics.median(base)
        rows.append({"variant": k, "n": len(v), "median_ms": round(statistics.median(v), 3), "p95_ms": round(sorted(v)[int(0.95 * len(v))], 3),
                     "median_diff_vs_foreign_ms": round(d, 3), "perm_p": None if k.startswith("foreign") else round(perm_p(v, base, 1500), 4)})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "timing-samples.json").write_text(json.dumps({"tolerance_ms": tol, "aa_noise_ms": noise, "rows": rows, "samples_ms": samples}))
    (OUT / "timing-summary.md").write_text(
        f"A/A noise (two halves of the baseline): {noise:.3f} ms; tolerance used: {tol:.3f} ms\n\n| variant | n | median ms | p95 ms | median diff vs foreign | permutation p |\n|---|---|---|---|---|---|\n"
        + "\n".join(f"| {r['variant']} | {r['n']} | {r['median_ms']} | {r['p95_ms']} | {r['median_diff_vs_foreign_ms']} | {r['perm_p']} |" for r in rows) + "\n")
    bad = [r for r in rows if abs(r["median_diff_vs_foreign_ms"]) > tol]
    assert not bad, f"timing differs beyond tolerance {tol:.3f} ms: {bad}"


# ---------------------------------------------------------------- logs
def test_container_logs_contain_no_secrets_cookies_or_payloads(two):
    a, b = two
    marker = "LOGMARKER_" + "".join(random.Random(9).choice("0123456789abcdef") for _ in range(8))
    a.c.post(f"/runs/{a.run_id}/metrics", headers=a.bearer, json={"batch_id": "lg", "points": [{"metric": marker, "step": 0, "value": 98765.4321}]})
    new = a.signed.post("/keys", json={"label": "n", "scope": "ingest"}, headers=a.csrf()).json()
    a.signed.post(f"/keys/{a.ingest['id']}/rotate", headers=a.csrf())
    a.c.post("/runs", json={"name": "x"}, headers=a.bearer)                         # revoked key
    a.c.post("/runs", json={"name": "x"}, headers={"Authorization": "Bearer trk_ing_" + "q" * 32})
    token = a.signed.cookies.get("tracker_session")
    time.sleep(1)
    logs = compose_logs()
    assert "POST /runs" in logs or "/api/runs" in logs, "no request lines in the container logs: the check would be vacuous"
    secrets_ = [a.ingest["key"], a.read["key"], b.ingest["key"], b.read["key"], new["key"], token, marker, "98765.4321",
                a.read["key"][-12:], a.ingest["key"][-12:]]
    for s in secrets_:
        assert s not in logs, f"{s[:10]}... found in container logs"


# ---------------------------------------------------------------- streams through nginx
def read_stream(client, path, out, stop_after=30.0):
    t0 = time.monotonic()
    try:
        with client.stream("GET", path, timeout=stop_after) as r:
            out["status"] = r.status_code
            out["headers"] = dict(r.headers)
            for line in r.iter_lines():
                out.setdefault("lines", []).append((time.monotonic() - t0, line))
    except Exception as e:  # noqa: BLE001
        out["error"] = repr(e)
    out["ended_at"] = time.monotonic() - t0


def test_stream_is_not_buffered_by_nginx_and_has_the_right_headers(two):
    a, _ = two
    out = {}
    t = threading.Thread(target=read_stream, args=(a.signed, f"/runs/{a.run_id}/stream", out, 4.0), daemon=True)
    t.start()
    time.sleep(1.2)
    a.c.post(f"/runs/{a.run_id}/metrics", headers=a.bearer, json={"batch_id": "s2", "points": [{"metric": "x", "step": 99, "value": 1.0}]})
    t.join(6)
    h = out["headers"]
    assert out["status"] == 200 and h["content-type"].startswith("text/event-stream")
    assert "no-store" in h["cache-control"]   # X-Accel-Buffering is consumed by nginx itself; its effect is checked below
    first_stats = next(t_ for t_, l in out["lines"] if l.startswith("event: stats"))
    assert first_stats < 1.0, f"first stats took {first_stats:.2f}s: nginx is buffering the stream"
    live = [t_ for t_, l in out["lines"] if l.startswith("event: metric")]
    assert any(t_ > 1.0 for t_ in live), "the live event never arrived"


def test_rotating_or_revoking_a_key_closes_open_streams_through_nginx(two):
    a, _ = two
    ks = {}
    for name in ("rot", "rev"):
        k = a.signed.post("/keys", json={"label": name, "scope": "read"}, headers=a.csrf()).json()
        c = httpx.Client(base_url=GATEWAY + "/api", timeout=15)
        assert c.post("/session", json={"key": k["key"]}).status_code == 200
        ks[name] = (k, c)
    outs, threads = {}, []
    for name, (k, c) in ks.items():
        outs[name] = {}
        th = threading.Thread(target=read_stream, args=(c, f"/runs/{a.run_id}/stream", outs[name], 25.0), daemon=True)
        th.start(); threads.append(th)
    time.sleep(1.5)
    t_cut = time.monotonic()
    assert a.signed.post(f"/keys/{ks['rot'][0]['id']}/rotate", headers=a.csrf()).status_code == 201
    assert a.signed.post(f"/keys/{ks['rev'][0]['id']}/revoke", headers=a.csrf()).status_code == 200
    for th in threads:
        th.join(10)
    for name, o in outs.items():
        # the stream's own clock started ~1.5 s before the cut; it must end within a few seconds after it
        assert "ended_at" in o and o["ended_at"] - 1.5 < 5.0, f"{name}: stream still open {o.get('ended_at')}"
        assert o["ended_at"] < 20, name
    # and the old credentials are dead on REST
    for name, (k, c) in ks.items():
        r = c.get(f"/runs/{a.run_id}")
        assert r.status_code == 401 and r.json()["code"] == "key_revoked", name


def test_old_ingest_key_is_dead_after_rotation_through_the_gateway(two):
    a, _ = two
    r = a.signed.post(f"/keys/{a.ingest['id']}/rotate", headers=a.csrf())
    assert r.status_code == 201
    new = {"Authorization": "Bearer " + r.json()["key"]}
    old = a.c.post(f"/runs/{a.run_id}/metrics", headers=a.bearer, json={"batch_id": "z", "points": [{"metric": "m", "step": 1, "value": 1.0}]})
    assert old.status_code == 401 and old.json()["code"] == "key_revoked"
    ok = a.c.post(f"/runs/{a.run_id}/metrics", headers=new, json={"batch_id": "z", "points": [{"metric": "m", "step": 1, "value": 1.0}]})
    assert ok.status_code == 200
