#!/usr/bin/env python3
"""Load test (TZ_dev.md §8.4). Run against the compose stack through the nginx gateway (the shipped topology).

  50 runs x 10 events/s = 500 events/s, batches of 10-50 points, for --duration seconds
  + one normal SSE reader per run that drops its connection every 20-60 s and resumes from its cursor
  + --slow-readers extra readers that read ~2 events/s while ingestion runs, then drain
  + 2 pollers of GET /runs (what the runs list does)
  + a sampler: docker stats (CPU, memory) of postgres/api/web and the number of Postgres connections

Measured, not assumed:
  * ingest latency per request (p50/p95/p99/max), failures and retries
  * stream delay = time a reader received an event minus the moment the producer started the POST that carried it
    (this INCLUDES the ingest time; it is an upper bound on commit-to-delivery delay), p50/p95/p99/max
  * lost events    = seqs stored in Postgres that a reader never received (per reader, after it drained to `closed`)
  * duplicate events = seqs a reader received more than once (a resumed connection must not repeat events)
  * lost on ingest = points a producer meant to send that are not in Postgres (counted straight from the database)
Everything raw goes to --out: samples, per-run reconciliation, resource samples, settings. No verdict is computed here.
"""
import argparse
import asyncio
import hashlib
import json
import os
import random
import statistics
import subprocess
import time
from pathlib import Path

import httpx

# docker compose names containers <project>-<service>-1, and the project name is the repo folder unless overridden
PROJECT = os.environ.get("COMPOSE_PROJECT_NAME") or Path(__file__).resolve().parent.parent.name

REPO = Path(__file__).resolve().parents[1]


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def summarize(xs):
    return {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95), "p99": pct(xs, 99), "max": max(xs) if xs else None,
            "mean": statistics.fmean(xs) if xs else None}


class Shared:
    def __init__(self):
        self.post_start = {}          # point_id -> monotonic time the first POST attempt carrying it started
        self.ingest = []              # (t_since_start, latency_ms, outcome)
        self.errors = {}              # outcome -> count
        self.retries = 0
        self.gave_up = 0
        self.intended = {}            # run_id -> set(point_id)
        self.runs_list = []           # (t, latency_ms, status)
        self.producers_done = asyncio.Event()
        self.t0 = time.monotonic()


async def post_batch(sh, client, run_id, points, rnd, max_attempts=8):
    blob = json.dumps(points, sort_keys=True, separators=(",", ":"))
    batch_id = "b-" + hashlib.sha256((run_id + blob).encode()).hexdigest()[:24]    # content hash: a retry is the same id
    t_first = time.monotonic()
    for p in points:
        sh.post_start.setdefault(p["point_id"], t_first)
    for attempt in range(max_attempts):
        t = time.monotonic()
        outcome = "ok"
        try:
            r = await client.post(f"/api/runs/{run_id}/metrics", json={"batch_id": batch_id, "points": points}, timeout=30)
            if r.status_code != 200:
                outcome = f"http_{r.status_code}"
        except httpx.TimeoutException:
            outcome = "timeout"
        except httpx.HTTPError as e:
            outcome = "transport_" + type(e).__name__
        ms = (time.monotonic() - t) * 1000
        sh.ingest.append((t - sh.t0, ms, outcome))
        if outcome == "ok":
            return True
        sh.errors[outcome] = sh.errors.get(outcome, 0) + 1
        sh.retries += 1
        await asyncio.sleep(min(5.0, 0.2 * 2 ** attempt) * (0.5 + rnd.random()))
    sh.gave_up += 1
    return False


async def producer(i, sh, client, args, run_ids, end_at):
    rnd = random.Random(1000 + i)
    await asyncio.sleep(rnd.uniform(0, 5))                      # stagger the start
    r = await client.post("/api/runs", json={"name": f"load-{i}"})
    run_id = r.json()["id"]
    run_ids[i] = run_id
    sh.intended[run_id] = set()
    per_tick = max(1, args.rate // 2)                           # steps per second; 2 metrics per step
    step, buf, target = 0, [], rnd.randint(10, 50)
    next_tick = time.monotonic()
    while time.monotonic() < end_at:
        next_tick += 1.0
        for _ in range(per_tick):
            for m, v in (("loss", 1 / (1 + step * 0.01)), ("accuracy", 1 - 1 / (1 + step * 0.01))):
                pid = f"{m}:{step}"
                buf.append({"metric": m, "step": step, "value": v + rnd.uniform(-1e-3, 1e-3), "point_id": f"{run_id}/{pid}"})
            step += 1
        while len(buf) >= target:
            batch, buf = buf[:target], buf[target:]
            if await post_batch(sh, client, run_id, batch, rnd):
                sh.intended[run_id].update(p["point_id"] for p in batch)
            target = rnd.randint(10, 50)
        delay = next_tick - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
    while buf:
        batch, buf = buf[:50], buf[50:]
        if await post_batch(sh, client, run_id, batch, rnd):
            sh.intended[run_id].update(p["point_id"] for p in batch)
    for _ in range(8):
        try:
            rr = await client.post(f"/api/runs/{run_id}/finish", json={"status": "finished"}, timeout=30)
            if rr.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1)


async def reader(name, run_idx, sh, client, run_ids, slow, rnd, results, deadline_after_producers):
    while run_idx not in run_ids:
        await asyncio.sleep(0.2)
    run_id = run_ids[run_idx]
    seen, raw, cursor, reconnects, delays, closed = set(), 0, 0, 0, [], False
    lag_at_end = None
    next_disc = time.monotonic() + rnd.uniform(20, 60)
    give_up_at = None
    while not closed:
        if sh.producers_done.is_set() and give_up_at is None:
            give_up_at = time.monotonic() + deadline_after_producers
        if give_up_at and time.monotonic() > give_up_at:
            break
        try:
            async with client.stream("GET", f"/api/runs/{run_id}/stream", params={"after_seq": cursor}, timeout=httpx.Timeout(30, read=30)) as resp:
                if resp.status_code != 200:
                    raise httpx.HTTPError(f"status {resp.status_code}")
                ev = None
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        ev = line[7:]
                    elif line.startswith("data: ") and ev:
                        d = json.loads(line[6:])
                        if ev == "metric":
                            now = time.monotonic()
                            raw += 1
                            if d["seq"] in seen:
                                continue
                            seen.add(d["seq"])
                            cursor = max(cursor, d["seq"])
                            t_post = sh.post_start.get(d["point_id"])
                            if t_post is not None and not slow:
                                delays.append((now - t_post) * 1000)
                            if slow and not sh.producers_done.is_set():
                                await asyncio.sleep(0.5)          # ~2 events/s while 10/s are produced
                        elif ev == "closed":
                            closed = True
                            break
                    if not slow and time.monotonic() > next_disc:
                        break                                      # periodic disconnect; resume from the cursor
                    if give_up_at and time.monotonic() > give_up_at:
                        break
        except (httpx.HTTPError, json.JSONDecodeError):
            await asyncio.sleep(1)
        if not closed:
            reconnects += 1
            next_disc = time.monotonic() + rnd.uniform(20, 60)
    results[name] = {"run_id": run_id, "slow": slow, "seen": seen, "raw": raw, "reconnects": reconnects, "delays": delays, "closed": closed}


async def list_poller(sh, client, end_at):
    while time.monotonic() < end_at:
        t = time.monotonic()
        try:
            r = await client.get("/api/runs", timeout=30)
            sh.runs_list.append((t - sh.t0, (time.monotonic() - t) * 1000, r.status_code))
        except httpx.HTTPError:
            sh.runs_list.append((t - sh.t0, (time.monotonic() - t) * 1000, 0))
        await asyncio.sleep(2)


def sh_cmd(cmd, **kw):
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, **kw)


def psql(sql):
    r = sh_cmd(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "tracker", "-d", "tracker", "-At", "-F", "|", "-c", sql])
    return [l.split("|") for l in r.stdout.strip().split("\n") if l]


async def sampler(out_csv, stop):
    rows = []
    while not stop.is_set():
        t = time.time()
        try:
            # run the blocking docker/psql calls in a worker thread: calling subprocess.run directly inside the event loop froze
            # the generator for 1-3 s every cycle and inflated every latency it measured (found in the first full run)
            def sample():
                s = sh_cmd(["docker", "stats", "--no-stream", "--format", "{{.Name}},{{.CPUPerc}},{{.MemUsage}}"], timeout=30)
                conns = psql("select count(*) from pg_stat_activity where datname='tracker'")[0][0]
                active = psql("select count(*) from pg_stat_activity where datname='tracker' and state='active'")[0][0]
                return s, conns, active
            s, conns, active = await asyncio.to_thread(sample)
            for line in s.stdout.strip().split("\n"):
                name, cpu, mem = line.split(",")
                if name.startswith(PROJECT + "-"):
                    rows.append({"t": t, "name": name, "cpu_pct": float(cpu.rstrip("%")), "mem_used": mem.split("/")[0].strip(), "pg_connections": int(conns), "pg_active": int(active)})
        except Exception as e:  # noqa: BLE001
            rows.append({"t": t, "error": repr(e)})
        try:
            await asyncio.wait_for(stop.wait(), 8)
        except asyncio.TimeoutError:
            pass
    out_csv.write_text(json.dumps(rows))
    return rows


def mem_mb(s):
    n = float("".join(c for c in s if c.isdigit() or c == "."))
    return n * (1024 if "GiB" in s else 1 if "MiB" in s else 1 / 1024)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--runs", type=int, default=50)
    ap.add_argument("--rate", type=int, default=10, help="events per second per run")
    ap.add_argument("--duration", type=int, default=600)
    ap.add_argument("--slow-readers", type=int, default=5)
    ap.add_argument("--drain", type=int, default=180, help="seconds readers may take to drain after the producers finish")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    limits = httpx.Limits(max_connections=400, max_keepalive_connections=100)
    admin = httpx.AsyncClient(base_url=args.base, limits=limits, timeout=30)
    created = (await admin.post("/api/projects", json={"name": f"load-{int(time.time()) % 100000}"})).json()
    ingest_key, read_key = created["keys"]["ingest"]["key"], created["keys"]["read"]["key"]
    pc = httpx.AsyncClient(base_url=args.base, limits=limits, headers={"Authorization": f"Bearer {ingest_key}"}, timeout=30)
    rc = httpx.AsyncClient(base_url=args.base, limits=limits, timeout=30)
    assert (await rc.post("/api/session", json={"key": read_key})).status_code == 200

    sh = Shared()
    run_ids, results = {}, {}
    stop_sampler = asyncio.Event()
    start_wall = time.time()
    sample_task = asyncio.create_task(sampler(out / "resources.json", stop_sampler))
    end_at = time.monotonic() + args.duration
    prods = [asyncio.create_task(producer(i, sh, pc, args, run_ids, end_at)) for i in range(args.runs)]
    rnd = random.Random(7)
    readers = [asyncio.create_task(reader(f"reader-{i}", i, sh, rc, run_ids, False, random.Random(2000 + i), results, args.drain)) for i in range(args.runs)]
    readers += [asyncio.create_task(reader(f"slow-{i}", i, sh, rc, run_ids, True, random.Random(3000 + i), results, args.drain)) for i in range(args.slow_readers)]
    pollers = [asyncio.create_task(list_poller(sh, rc, end_at)) for _ in range(2)]

    await asyncio.gather(*prods)
    sh.producers_done.set()
    t_prod_done = time.monotonic() - sh.t0
    await asyncio.gather(*pollers)
    await asyncio.gather(*readers)
    stop_sampler.set()
    await sample_task
    t_total = time.monotonic() - sh.t0

    # ---------- reconciliation against Postgres (independent of the API) ----------
    ids_sql = ",".join(f"'{r}'" for r in run_ids.values())
    db = {r[0]: {"count": int(r[1]), "distinct": int(r[2]), "max_seq": int(r[3]), "min_seq": int(r[4])} for r in psql(
        f"select run_id, count(*), count(distinct point_id), max(seq), min(seq) from metric_events where run_id in ({ids_sql}) group by run_id")}
    runs_tbl = {r[0]: {"status": r[1], "last_seq": int(r[2]), "dups": int(r[3])} for r in psql(f"select id, status, last_seq, duplicates_ignored from runs where id in ({ids_sql})")}
    stored_ids = {}
    for rid in run_ids.values():
        stored_ids[rid] = {r[0] for r in psql(f"select point_id from metric_events where run_id='{rid}'")}
    per_run, lost_ingest_total, dup_db_total = [], 0, 0
    for i, rid in sorted(run_ids.items()):
        d = db.get(rid, {"count": 0, "distinct": 0, "max_seq": 0, "min_seq": 0})
        intended = sh.intended.get(rid, set())
        lost_on_ingest = len(intended - stored_ids[rid])
        extra = len(stored_ids[rid] - intended)
        lost_ingest_total += lost_on_ingest
        dup_db_total += d["count"] - d["distinct"]
        per_run.append({"run": rid, "intended": len(intended), "stored": d["count"], "distinct": d["distinct"], "contiguous": d["count"] == d["max_seq"] and d["min_seq"] == 1,
                        "lost_on_ingest": lost_on_ingest, "stored_not_intended": extra, "status": runs_tbl.get(rid, {}).get("status"), "duplicates_ignored": runs_tbl.get(rid, {}).get("dups")})
    rd_rows, lost_total, dup_total, undrained = [], 0, 0, 0
    for name, r in sorted(results.items()):
        d = db.get(r["run_id"], {"count": 0})
        want = set(range(1, d["count"] + 1))
        lost = len(want - r["seen"])
        dups = r["raw"] - len(r["seen"])
        lost_total += lost
        dup_total += dups
        undrained += 0 if r["closed"] else 1
        rd_rows.append({"reader": name, "run": r["run_id"], "slow": r["slow"], "received_unique": len(r["seen"]), "received_raw": r["raw"], "stored": d["count"],
                        "lost": lost, "duplicate_deliveries": dups, "reconnects": r["reconnects"], "saw_closed": r["closed"]})

    delays = [x for r in results.values() if not r["slow"] for x in r["delays"]]
    ing_ok = [ms for _, ms, o in sh.ingest if o == "ok"]
    ing_all = [ms for _, ms, _ in sh.ingest]
    # per-minute ingest p95 and delay are written so a drift over the 10 minutes is visible
    per_min = {}
    for t, ms, o in sh.ingest:
        per_min.setdefault(int(t // 60), []).append(ms)
    res = json.loads((out / "resources.json").read_text())
    by_name = {}
    for row in res:
        if "name" in row:
            by_name.setdefault(row["name"], []).append(row)
    resources = {}
    for n, rows in by_name.items():
        mems = [mem_mb(x["mem_used"]) for x in rows]
        resources[n] = {"samples": len(rows), "cpu_pct_mean": statistics.fmean(x["cpu_pct"] for x in rows), "cpu_pct_max": max(x["cpu_pct"] for x in rows),
                        "mem_mb_first": mems[0], "mem_mb_max": max(mems), "mem_mb_last": mems[-1]}
    pg = [row["pg_connections"] for row in res if "pg_connections" in row]
    pga = [row["pg_active"] for row in res if "pg_active" in row]
    inspect = sh_cmd(["docker", "inspect", "-f", "{{.Name}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}", f"{PROJECT}-api-1", f"{PROJECT}-postgres-1", f"{PROJECT}-web-1"]).stdout.strip()
    summary = {
        "args": vars(args), "started_at_epoch": start_wall, "producers_finished_after_s": round(t_prod_done, 1), "total_s": round(t_total, 1),
        "events_intended": sum(len(v) for v in sh.intended.values()), "events_stored": sum(d["count"] for d in db.values()),
        "throughput_events_per_s_over_ingest_window": round(sum(len(v) for v in sh.intended.values()) / max(1.0, t_prod_done), 1),
        "ingest_requests": len(sh.ingest), "ingest_latency_ms_ok": summarize(ing_ok), "ingest_latency_ms_all_attempts": summarize(ing_all),
        "ingest_errors": sh.errors, "ingest_retries": sh.retries, "ingest_gave_up": sh.gave_up,
        "ingest_p95_per_minute_ms": {str(k): pct(v, 95) for k, v in sorted(per_min.items())},
        "stream_delay_ms_normal_readers": summarize(delays),
        "lost_on_ingest_total": lost_ingest_total, "duplicates_in_db_total": dup_db_total,
        "lost_at_readers_total": lost_total, "duplicate_deliveries_total": dup_total, "readers_that_never_saw_closed": undrained,
        "reader_reconnects_total": sum(r["reconnects"] for r in results.values()),
        "runs_not_contiguous": [r["run"] for r in per_run if not r["contiguous"]],
        "runs_list_latency_ms": summarize([ms for _, ms, s in sh.runs_list if s == 200]), "runs_list_non200": sum(1 for _, _, s in sh.runs_list if s != 200),
        "resources": resources, "pg_connections": {"max": max(pg) if pg else None, "mean": statistics.fmean(pg) if pg else None, "active_max": max(pga) if pga else None},
        "containers": inspect,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "per_run.json").write_text(json.dumps(per_run, indent=2))
    (out / "readers.json").write_text(json.dumps(rd_rows, indent=2))
    (out / "ingest_samples.json").write_text(json.dumps(sh.ingest))
    (out / "stream_delay_samples_ms.json").write_text(json.dumps([round(x, 2) for x in delays]))
    (out / "runs_list_samples.json").write_text(json.dumps(sh.runs_list))
    print(json.dumps({k: summary[k] for k in ("events_intended", "events_stored", "ingest_latency_ms_ok", "ingest_errors", "stream_delay_ms_normal_readers",
                                              "lost_on_ingest_total", "lost_at_readers_total", "duplicate_deliveries_total", "duplicates_in_db_total", "runs_not_contiguous")}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
