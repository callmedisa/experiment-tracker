"""Not collected by default (name does not match test_*). Run: pytest tests/probe_ingest_latency.py -s
Prints the time of each 1000-point batch into one run, to see whether cost grows with run size."""
import time

from conftest import post_batch, pt


async def test_probe(client, proj, run_id):
    times = []
    for i in range(40):
        t0 = time.monotonic()
        r = await post_batch(client, proj, run_id, f"b{i}", [pt("loss", i * 1000 + j, 0.1) for j in range(1000)])
        assert r.status_code == 200
        times.append(time.monotonic() - t0)
    print("per-batch seconds:", " ".join(f"{t:.3f}" for t in times))
    small = []
    for i in range(40):
        t0 = time.monotonic()
        await post_batch(client, proj, run_id, f"s{i}", [pt("loss", 10_000_000 + i * 10 + j, 0.1) for j in range(10)])
        small.append(time.monotonic() - t0)
    print("then 40 batches of 10 points, seconds:", " ".join(f"{t:.4f}" for t in small))


async def test_probe_plans(client, proj, run_id, db):
    """Prints EXPLAIN of the two lookups made by every batch, in the slow window (after 20 x 1000 rows)."""
    from sqlalchemy import text
    for i in range(20):
        await post_batch(client, proj, run_id, f"b{i}", [pt("loss", i * 1000 + j, 0.1) for j in range(1000)])
    ids = ",".join(f"'loss:{20000 + j}'" for j in range(1000))
    async with db.connect() as c:
        for name, q in (
            ("point_id lookup", f"EXPLAIN SELECT point_id FROM metric_events WHERE run_id='{run_id}' AND point_id IN ({ids})"),
            ("max step", f"EXPLAIN SELECT step FROM metric_events WHERE run_id='{run_id}' AND metric='loss' ORDER BY step DESC LIMIT 1"),
        ):
            print(f"--- {name}")
            for row in (await c.execute(text(q))).scalars():
                print(row)
        st = (await c.execute(text("SELECT reltuples, relpages FROM pg_class WHERE relname='metric_events'"))).one()
        print("pg_class reltuples/relpages:", tuple(st))
        la = (await c.execute(text("SELECT last_autoanalyze, n_live_tup FROM pg_stat_user_tables WHERE relname='metric_events'"))).one()
        print("last_autoanalyze, n_live_tup:", tuple(la))
