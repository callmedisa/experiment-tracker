"""Batch ingestion: idempotency, conflicts, seq contiguity, atomic rollback, late flags (TZ_dev.md §5.1, §8.3)."""
import asyncio

from sqlalchemy import text

from conftest import post_batch, pt


async def events(db, run_id):
    async with db.connect() as c:
        r = await c.execute(text(
            "SELECT seq, point_id, metric, step, value, is_late, client_ts FROM metric_events "
            "WHERE run_id=:r ORDER BY seq"), {"r": run_id})
        return r.all()


async def run_row(db, run_id):
    async with db.connect() as c:
        r = await c.execute(text("SELECT status, last_seq, duplicates_ignored FROM runs WHERE id=:r"), {"r": run_id})
        return r.one()


async def batch_rows(db, run_id):
    async with db.connect() as c:
        r = await c.execute(text("SELECT batch_id, accepted, duplicates, first_seq, last_seq FROM ingest_batches WHERE run_id=:r"),
                            {"r": run_id})
        return r.all()


async def assert_contiguous(db, run_id):
    ev = await events(db, run_id)
    run = await run_row(db, run_id)
    assert [e.seq for e in ev] == list(range(1, len(ev) + 1)), "stored seqs must be exactly 1..n"
    assert run.last_seq == len(ev), "runs.last_seq must equal the number of stored events"
    assert len({e.point_id for e in ev}) == len(ev)


# ---------- basics ----------

async def test_accepts_batch_and_assigns_consecutive_seq(client, proj, run_id, db):
    r = await post_batch(client, proj, run_id, "b1", [pt(step=0), pt(step=1), pt("acc", 0, 0.5)])
    assert r.status_code == 200
    assert r.json() == {"accepted": 3, "duplicates": 0, "first_seq": 1, "last_seq": 3}
    ev = await events(db, run_id)
    assert [(e.seq, e.point_id) for e in ev] == [(1, "loss:0"), (2, "loss:1"), (3, "acc:0")]  # array order, default ids
    await assert_contiguous(db, run_id)


async def test_second_batch_continues_numbering(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b1", [pt(step=0), pt(step=1)])
    r = await post_batch(client, proj, run_id, "b2", [pt(step=2), pt(step=3)])
    assert r.json() == {"accepted": 2, "duplicates": 0, "first_seq": 3, "last_seq": 4}
    await assert_contiguous(db, run_id)


async def test_ts_is_optional_and_never_replaced_by_server_time(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b1", [pt(step=0), pt(step=1, ts="2026-11-12T10:00:00Z")])
    ev = await events(db, run_id)
    assert ev[0].client_ts is None
    assert ev[1].client_ts.isoformat().startswith("2026-11-12T10:00:00")


# ---------- idempotency: batch_id ----------

async def test_batch_retry_returns_original_and_stores_nothing(client, proj, run_id, db):
    pts = [pt(step=0), pt(step=1), pt(step=2)]
    first = await post_batch(client, proj, run_id, "b1", pts)
    again = await post_batch(client, proj, run_id, "b1", pts)
    assert again.status_code == 200
    assert again.json() == first.json() == {"accepted": 3, "duplicates": 0, "first_seq": 1, "last_seq": 3}
    run = await run_row(db, run_id)
    assert run.last_seq == 3 and run.duplicates_ignored == 3
    assert len(await batch_rows(db, run_id)) == 1  # no second batch row
    await assert_contiguous(db, run_id)


async def test_batch_retry_many_times_counts_every_retry(client, proj, run_id, db):
    pts = [pt(step=i) for i in range(5)]
    await post_batch(client, proj, run_id, "b1", pts)
    for _ in range(4):
        assert (await post_batch(client, proj, run_id, "b1", pts)).status_code == 200
    run = await run_row(db, run_id)
    assert run.last_seq == 5 and run.duplicates_ignored == 4 * 5
    await assert_contiguous(db, run_id)


async def test_batch_retry_after_run_closed_still_gets_original_answer(client, proj, run_id):
    pts = [pt(step=0)]
    first = await post_batch(client, proj, run_id, "b1", pts)
    assert (await client.post(f"/runs/{run_id}/finish", json={"status": "finished"}, headers=proj.ingest)).status_code == 200
    again = await post_batch(client, proj, run_id, "b1", pts)
    assert again.status_code == 200 and again.json() == first.json()
    new = await post_batch(client, proj, run_id, "b2", [pt(step=1)])
    assert new.status_code == 409 and new.json()["code"] == "run_closed"


async def test_same_batch_id_with_different_points_is_batch_conflict(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b1", [pt(step=0), pt(step=1)])
    r = await post_batch(client, proj, run_id, "b1", [pt(step=0), pt(step=1), pt(step=2)])
    assert r.status_code == 409 and r.json()["code"] == "batch_conflict"
    run = await run_row(db, run_id)
    assert run.last_seq == 2 and run.duplicates_ignored == 0  # nothing stored, nothing counted
    await assert_contiguous(db, run_id)


async def test_batch_id_is_scoped_to_the_run(client, proj, db):
    ids = []
    for _ in range(2):
        ids.append((await client.post("/runs", json={"name": "x"}, headers=proj.ingest)).json()["id"])
    for rid in ids:
        r = await post_batch(client, proj, rid, "same-id", [pt(step=0)])
        assert r.json()["accepted"] == 1


# ---------- idempotency: point_id ----------

async def test_resplit_batch_under_new_batch_id_is_deduped_by_point_id(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b1", [pt(step=0), pt(step=1), pt(step=2)])
    r = await post_batch(client, proj, run_id, "b2", [pt(step=1), pt(step=2), pt(step=3)])
    assert r.json() == {"accepted": 1, "duplicates": 2, "first_seq": 4, "last_seq": 4}
    run = await run_row(db, run_id)
    assert run.last_seq == 4 and run.duplicates_ignored == 2
    await assert_contiguous(db, run_id)


async def test_fully_duplicate_batch_has_no_seq_range(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b1", [pt(step=0), pt(step=1)])
    r = await post_batch(client, proj, run_id, "b2", [pt(step=0), pt(step=1)])
    assert r.status_code == 200
    assert r.json() == {"accepted": 0, "duplicates": 2, "first_seq": None, "last_seq": None}
    assert (await run_row(db, run_id)).duplicates_ignored == 2
    # and a retry of that all-duplicate batch is again a no-op that counts its 2 points
    await post_batch(client, proj, run_id, "b2", [pt(step=0), pt(step=1)])
    assert (await run_row(db, run_id)).duplicates_ignored == 4
    await assert_contiguous(db, run_id)


async def test_same_point_repeated_inside_one_batch(client, proj, run_id, db):
    r = await post_batch(client, proj, run_id, "b1", [pt(step=0), pt(step=0), pt(step=1)])
    assert r.json() == {"accepted": 2, "duplicates": 1, "first_seq": 1, "last_seq": 2}
    await assert_contiguous(db, run_id)


async def test_point_conflict_rejects_whole_batch_and_stores_nothing(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b1", [pt(step=0, value=1.0)])
    r = await post_batch(client, proj, run_id, "b2", [pt(step=5), pt(step=0, value=9.9), pt(step=6)])
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "point_conflict" and body["point_ids"] == ["loss:0"]
    run = await run_row(db, run_id)
    assert run.last_seq == 1 and run.duplicates_ignored == 0
    assert len(await batch_rows(db, run_id)) == 1  # b2 left no trace, so it can be corrected and resent under the same id
    fixed = await post_batch(client, proj, run_id, "b2", [pt(step=5), pt(step=6)])
    assert fixed.json() == {"accepted": 2, "duplicates": 0, "first_seq": 2, "last_seq": 3}
    await assert_contiguous(db, run_id)


async def test_point_conflict_on_metric_or_step_change_with_custom_id(client, proj, run_id):
    await post_batch(client, proj, run_id, "b1", [pt(step=0, point_id="p1")])
    for bad in (pt("acc", 0, 1.0, point_id="p1"), pt(step=1, point_id="p1"), pt(step=0, value=2.0, point_id="p1")):
        r = await post_batch(client, proj, run_id, f"x-{bad['metric']}-{bad['step']}-{bad['value']}", [bad])
        assert r.status_code == 409 and r.json()["code"] == "point_conflict"


async def test_conflict_inside_one_batch(client, proj, run_id, db):
    r = await post_batch(client, proj, run_id, "b1", [pt(step=0, value=1.0, point_id="p"), pt(step=0, value=2.0, point_id="p")])
    assert r.status_code == 409 and r.json()["code"] == "point_conflict"
    assert (await run_row(db, run_id)).last_seq == 0


async def test_custom_point_ids_may_share_metric_and_step(client, proj, run_id, db):
    r = await post_batch(client, proj, run_id, "b1",
                         [pt(step=3, value=1.0, point_id="a"), pt(step=3, value=2.0, point_id="b")])
    assert r.json()["accepted"] == 2
    await assert_contiguous(db, run_id)


async def test_minus_zero_is_not_silently_treated_as_same_as_zero(client, proj, run_id):
    await post_batch(client, proj, run_id, "b1", [pt(step=0, value=0.0)])
    r = await client.post(f"/runs/{run_id}/metrics", headers={**proj.ingest, "Content-Type": "application/json"},
                          content='{"batch_id":"b2","points":[{"metric":"loss","step":0,"value":-0.0}]}')
    assert r.status_code == 409 and r.json()["code"] == "point_conflict"


# ---------- late flags ----------

async def test_is_late_across_batches_and_within_a_batch(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b1", [pt(step=10), pt(step=12)])
    await post_batch(client, proj, run_id, "b2", [pt(step=11), pt(step=12, point_id="again"), pt(step=9), pt(step=13), pt(step=13, point_id="x")])
    flags = {e.point_id: e.is_late for e in await events(db, run_id)}
    assert flags == {
        "loss:10": False, "loss:12": False,
        "loss:11": True,    # below stored max 12
        "again": False,     # equal to the max is not late
        "loss:9": True,
        "loss:13": False, "x": False,
    }


async def test_is_late_is_decided_per_metric(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b1", [pt("loss", 100), pt("acc", 1)])
    await post_batch(client, proj, run_id, "b2", [pt("acc", 2), pt("loss", 50)])
    flags = {e.point_id: e.is_late for e in await events(db, run_id)}
    assert flags == {"loss:100": False, "acc:1": False, "acc:2": False, "loss:50": True}


async def test_late_point_still_gets_a_new_seq(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b1", [pt(step=5)])
    r = await post_batch(client, proj, run_id, "b2", [pt(step=1)])
    assert r.json()["first_seq"] == 2
    assert [e.is_late for e in await events(db, run_id)] == [False, True]


async def test_duplicate_does_not_change_late_state(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b1", [pt(step=5), pt(step=6)])
    await post_batch(client, proj, run_id, "b2", [pt(step=5), pt(step=7)])  # step 5 is a dup; 7 is new and not late
    flags = [(e.point_id, e.is_late) for e in await events(db, run_id)]
    assert flags == [("loss:5", False), ("loss:6", False), ("loss:7", False)]


# ---------- seq contiguity under concurrency ----------

async def test_concurrent_distinct_batches_keep_seq_contiguous_and_blocks_unbroken(client, proj, run_id, db):
    n_batches, size = 25, 8
    async def send(i):
        pts = [pt(step=i * size + j) for j in range(size)]
        return i, await post_batch(client, proj, run_id, f"b{i}", pts)
    results = await asyncio.gather(*[send(i) for i in range(n_batches)])
    assert all(r.status_code == 200 for _, r in results)
    await assert_contiguous(db, run_id)
    # each batch got one unbroken block of seqs (batches never interleave)
    ev = await events(db, run_id)
    by_batch = {}
    for e in ev:
        by_batch.setdefault(int(e.step) // size, []).append(e.seq)
    for i, seqs in by_batch.items():
        assert seqs == list(range(seqs[0], seqs[0] + size)), f"batch {i} interleaved with another"
    # responses agree with storage
    for i, r in results:
        assert by_batch[i][0] == r.json()["first_seq"] and by_batch[i][-1] == r.json()["last_seq"]


async def test_concurrent_identical_batch_is_stored_exactly_once(client, proj, run_id, db):
    pts = [pt(step=i) for i in range(10)]
    rs = await asyncio.gather(*[post_batch(client, proj, run_id, "same", pts) for _ in range(12)])
    assert all(r.status_code == 200 for r in rs)
    assert len({tuple(r.json().items()) for r in rs}) == 1  # everyone got the same answer
    run = await run_row(db, run_id)
    assert run.last_seq == 10 and run.duplicates_ignored == 11 * 10
    assert len(await batch_rows(db, run_id)) == 1
    await assert_contiguous(db, run_id)


async def test_concurrent_overlapping_batches_under_different_ids_do_not_duplicate_or_leave_holes(client, proj, run_id, db):
    # 16 batches that all contain the same 20 points (new batch_id each time): the points must be stored once.
    pts = [pt(step=i) for i in range(20)]
    rs = await asyncio.gather(*[post_batch(client, proj, run_id, f"b{i}", pts) for i in range(16)])
    assert all(r.status_code == 200 for r in rs)
    assert sum(r.json()["accepted"] for r in rs) == 20
    assert sum(r.json()["duplicates"] for r in rs) == 15 * 20
    run = await run_row(db, run_id)
    assert run.last_seq == 20 and run.duplicates_ignored == 15 * 20
    await assert_contiguous(db, run_id)


async def test_concurrent_conflicting_writers_one_wins_others_get_409(client, proj, run_id, db):
    rs = await asyncio.gather(*[post_batch(client, proj, run_id, f"b{i}", [pt(step=0, value=float(i), point_id="p")])
                                for i in range(8)])
    codes = sorted(r.status_code for r in rs)
    assert codes == [200] + [409] * 7
    await assert_contiguous(db, run_id)
    assert (await run_row(db, run_id)).last_seq == 1


# ---------- atomicity / fault injection ----------

async def test_failure_mid_batch_stores_nothing_and_keeps_seq_contiguous(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b0", [pt(step=0), pt(step=1)])
    pts = [pt(step=i) for i in range(2, 12)]
    r = await post_batch(client, proj, run_id, "b1", pts, **{"X-Fault": "mid-batch"})
    assert r.status_code == 500 and r.json()["code"] == "internal_error"
    run = await run_row(db, run_id)
    assert run.last_seq == 2, "counter must roll back with the rows"
    assert len(await events(db, run_id)) == 2, "half-written rows must not survive"
    assert [b.batch_id for b in await batch_rows(db, run_id)] == ["b0"]
    await assert_contiguous(db, run_id)
    retry = await post_batch(client, proj, run_id, "b1", pts)  # the client retries the same batch
    assert retry.json() == {"accepted": 10, "duplicates": 0, "first_seq": 3, "last_seq": 12}
    await assert_contiguous(db, run_id)


async def test_response_lost_after_commit_then_retry_does_not_double_count(client, proj, run_id, db):
    pts = [pt(step=i) for i in range(5)]
    lost = await post_batch(client, proj, run_id, "b1", pts, **{"X-Fault": "after-commit"})
    assert lost.status_code == 500  # the client never sees the real answer...
    assert (await run_row(db, run_id)).last_seq == 5  # ...but the batch IS stored
    retry = await post_batch(client, proj, run_id, "b1", pts)
    assert retry.json() == {"accepted": 5, "duplicates": 0, "first_seq": 1, "last_seq": 5}
    run = await run_row(db, run_id)
    assert run.last_seq == 5 and run.duplicates_ignored == 5
    await assert_contiguous(db, run_id)


async def test_many_random_failures_never_break_contiguity(client, proj, run_id, db):
    import random
    rnd = random.Random(20261111)
    step = 0
    for i in range(30):
        pts = [pt(step=step + j) for j in range(rnd.randint(1, 15))]
        fault = rnd.choice([None, None, "mid-batch", "after-commit"])
        hdr = {"X-Fault": fault} if fault else {}
        r = await post_batch(client, proj, run_id, f"b{i}", pts, **hdr)
        if r.status_code != 200:
            r = await post_batch(client, proj, run_id, f"b{i}", pts)  # retry, as the client library does
        assert r.status_code == 200
        step += len(pts)
        await assert_contiguous(db, run_id)
    assert len(await events(db, run_id)) == step


async def test_fault_header_is_ignored_when_hooks_are_disabled(make_app, proj_factory):
    from conftest import client_for
    app = await make_app(fault_hooks=False)
    async with client_for(app) as c:
        p, rid = await proj_factory(c)
        r = await post_batch(c, p, rid, "b1", [pt(step=0)], **{"X-Fault": "mid-batch"})
        assert r.status_code == 200 and r.json()["accepted"] == 1


# ---------- validation and limits ----------

async def test_nan_and_infinity_are_rejected_naming_the_field(client, proj, run_id, db):
    for lit in ("NaN", "Infinity", "-Infinity"):
        r = await client.post(f"/runs/{run_id}/metrics", headers={**proj.ingest, "Content-Type": "application/json"},
                              content='{"batch_id":"b","points":[{"metric":"loss","step":0,"value":%s}]}' % lit)
        assert r.status_code == 422, lit
        assert r.json()["code"] == "validation_error" and "value" in r.json()["message"]
        assert lit not in r.text
    assert (await run_row(db, run_id)).last_seq == 0


async def test_rejects_bad_shapes(client, proj, run_id):
    bad = [
        {"batch_id": "b", "points": []},
        {"batch_id": "", "points": [pt()]},
        {"batch_id": "b", "points": [pt(metric="has space")]},
        {"batch_id": "b", "points": [pt(metric="a|b")]},
        {"batch_id": "b", "points": [pt(step=-1)]},
        {"batch_id": "b", "points": [pt(step=1.5)]},
        {"batch_id": "b", "points": [pt(value="1.0")]},
        {"batch_id": "b", "points": [pt(value=True)]},
        {"batch_id": "b", "points": [pt(metric="m" * 65)]},
        {"batch_id": "b", "points": [pt(point_id="p" * 129)]},
        {"batch_id": "b", "points": [pt()], "extra": 1},
        {"batch_id": "b", "points": [pt(ts="not a time")]},
    ]
    for body in bad:
        r = await client.post(f"/runs/{run_id}/metrics", json=body, headers=proj.ingest)
        assert r.status_code == 422, body
        assert r.json()["code"] == "validation_error"


async def test_batch_size_limits(client, proj, run_id, db):
    ok = await post_batch(client, proj, run_id, "ok", [pt(step=i) for i in range(1000)])
    assert ok.status_code == 200 and ok.json()["accepted"] == 1000
    too_many = await post_batch(client, proj, run_id, "many", [pt(step=i) for i in range(1000, 2001)])
    assert too_many.status_code == 422
    big = await client.post(f"/runs/{run_id}/metrics", headers={**proj.ingest, "Content-Type": "application/json"},
                            content=b'{"batch_id":"big","points":[],"pad":"' + b"x" * 1_000_001 + b'"}')
    assert big.status_code == 413 and big.json()["code"] == "payload_too_large"
    await assert_contiguous(db, run_id)


async def test_closed_run_rejects_metrics(client, proj, run_id, db):
    await post_batch(client, proj, run_id, "b1", [pt(step=0)])
    f = await client.post(f"/runs/{run_id}/finish", json={"status": "failed"}, headers=proj.ingest)
    assert f.status_code == 200 and f.json()["status"] == "failed"
    r = await post_batch(client, proj, run_id, "b2", [pt(step=1)])
    assert r.status_code == 409 and r.json()["code"] == "run_closed"
    assert (await run_row(db, run_id)).last_seq == 1
    # finishing again with the same status is fine, with another status is not
    assert (await client.post(f"/runs/{run_id}/finish", json={"status": "failed"}, headers=proj.ingest)).status_code == 200
    again = await client.post(f"/runs/{run_id}/finish", json={"status": "finished"}, headers=proj.ingest)
    assert again.status_code == 409 and again.json()["code"] == "run_closed"


async def test_batch_racing_a_finish_is_all_or_nothing(client, proj, run_id, db):
    pts = [pt(step=i) for i in range(50)]
    batch, fin = await asyncio.gather(
        post_batch(client, proj, run_id, "b1", pts),
        client.post(f"/runs/{run_id}/finish", json={"status": "finished"}, headers=proj.ingest))
    assert fin.status_code == 200
    run = await run_row(db, run_id)
    if batch.status_code == 200:
        assert run.last_seq == 50
    else:
        assert batch.status_code == 409 and run.last_seq == 0
    await assert_contiguous(db, run_id)


# ---------- runs ----------

async def test_run_id_format_and_creation(client, proj):
    r = await client.post("/runs", json={"name": "my run"}, headers=proj.ingest)
    assert r.status_code == 201
    rid = r.json()["id"]
    assert rid.startswith("run_") and len(rid) == 26 and rid[4:].isalnum()
    assert r.json()["name"] == "my run"
    assert (await client.post("/runs", json={"name": ""}, headers=proj.ingest)).status_code == 422
