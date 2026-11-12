#!/usr/bin/env python3
"""Deterministic, shuffled workload for the E2E consistency test. Uses client/tracker.py (retries, stable batch_id).

usage: simulator.py --url http://localhost:8082 --key trk_ing_... --seed 1 --steps 150 --delay 3 --out sim.json

Workload (all driven by one seeded RNG, so a seed reproduces the same sequence of requests):
  * metrics loss and acc for steps 0..steps-1 (default point ids "metric:step")
  * corrections: for some steps an extra point with a custom point_id and a DIFFERENT value for the same (metric, step),
    which only the "highest seq wins" chart rule resolves (default ids alone can never reach that rule)
  * order: local shuffles inside a sliding window, plus some points held back and sent much later (late points)
  * duplicates: the same batch sent again (same batch_id), and "re-split" resends: some earlier points again inside a NEW batch
Prints "RUN <id>" first, waits --delay seconds (the page connects), then sends. Writes the intended unique points to --out.
"""
import argparse
import json
import random
import struct
import sys
import time

sys.path.insert(0, __file__.rsplit("/web/e2e/", 1)[0] + "/client")
import tracker  # noqa: E402


def hexbits(v: float) -> str:
    return struct.pack(">d", v).hex()


def build(seed: int, steps: int):
    rnd = random.Random(seed)
    points = []
    for s in range(steps):
        for m, base in (("loss", 1.0 / (1 + s * 0.05)), ("acc", 1 - 1.0 / (1 + s * 0.03))):
            points.append({"metric": m, "step": s, "value": base + rnd.uniform(-0.01, 0.01)})
    for s in rnd.sample(range(steps), max(1, steps // 10)):
        points.append({"metric": "loss", "step": s, "value": rnd.uniform(0.2, 0.9), "point_id": f"fix-{s}"})
    # local shuffle in windows of 40
    out = []
    for i in range(0, len(points), 40):
        w = points[i:i + 40]
        rnd.shuffle(w)
        out.extend(w)
    # hold back ~8 %: they are sent near the end, so they arrive late
    held = [out.pop(rnd.randrange(len(out) // 2)) for _ in range(max(2, len(out) // 12))]
    return rnd, out, held


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--steps", type=int, default=150)
    ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--pace", type=float, default=0.35, help="max seconds between batches")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    rnd, ordered, held = build(a.seed, a.steps)
    run = tracker.start(key=a.key, run=f"e2e-seed-{a.seed}", url=a.url, max_attempts=12, rng=random.Random(a.seed))
    print("RUN", run.id, flush=True)
    time.sleep(a.delay)

    queue = ordered[:]
    # insert the held-back points into the last 15 % of the stream
    for p in held:
        queue.insert(rnd.randrange(int(len(queue) * 0.85), len(queue) + 1), p)

    sent_batches, resends, resplits = 0, 0, 0
    sent_points = []          # points that went out in some batch (for re-split resends)
    i = 0
    while i < len(queue):
        size = rnd.randint(5, 40)
        batch = queue[i:i + size]
        i += size
        run.send_batch(batch)
        sent_batches += 1
        sent_points.extend(batch)
        r = rnd.random()
        if r < 0.15:                      # same batch again (a client that retried too eagerly)
            run.send_batch(batch)
            resends += 1
        elif r < 0.30 and len(sent_points) > 10:   # re-split: earlier points inside a new batch
            run.send_batch(rnd.sample(sent_points, rnd.randint(3, 10)))
            resplits += 1
        time.sleep(rnd.uniform(0, a.pace))
    run.finish()

    intended = {}
    for p in queue:
        pid = p.get("point_id") or f"{p['metric']}:{p['step']}"
        intended[pid] = [pid, p["metric"], p["step"], hexbits(p["value"])]
    json.dump({"run_id": run.id, "seed": a.seed, "intended": sorted(intended.values()), "stats": run.stats,
               "batches": sent_batches, "same_batch_resends": resends, "resplit_resends": resplits,
               "held_back_late": len(held)}, open(a.out, "w"))
    print("DONE", json.dumps({"stats": run.stats, "unique_points": len(intended), "resends": resends, "resplits": resplits}), flush=True)


if __name__ == "__main__":
    main()
