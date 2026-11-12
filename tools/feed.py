#!/usr/bin/env python3
"""Throwaway data feeder for manual UI checks (stdlib only). The real client library is client/tracker.py (later step).
usage: feed.py BASE_URL INGEST_KEY RUN_NAME [events_per_s] [seconds] [--finish]"""
import json, math, sys, time, urllib.request

base, key, name = sys.argv[1], sys.argv[2], sys.argv[3]
rate = float(sys.argv[4]) if len(sys.argv) > 4 else 5
secs = float(sys.argv[5]) if len(sys.argv) > 5 else 60
H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

def call(path, body):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), H, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

rid = call("/api/runs", {"name": name})["id"]
print("run", rid, flush=True)
step, t0, b = 0, time.time(), 0
while time.time() - t0 < secs:
    pts = []
    for _ in range(max(1, int(rate))):
        pts += [{"metric": "loss", "step": step, "value": 1 / (1 + step * 0.05) + 0.02 * math.sin(step)},
                {"metric": "accuracy", "step": step, "value": 1 - 1 / (1 + step * 0.03)}]
        step += 1
    b += 1
    call(f"/api/runs/{rid}/metrics", {"batch_id": f"feed-{rid}-{b}", "points": pts})
    time.sleep(1)
if "--finish" in sys.argv:
    call(f"/api/runs/{rid}/finish", {"status": "finished"})
print("done", step, flush=True)
