"""Tracker client: batching, retries, stable batch_id/point_id. The UI's Python snippet must match this API exactly:

    import tracker
    run = tracker.start(key="trk_ing_...", run="my-first-run", url="http://localhost:8080/api")
    run.log(step, loss=0.5, accuracy=0.9)
    run.finish()

Retry rule: a batch gets its id from its CONTENT (sha256), so resending it, even after a crash or a lost response, carries the
same batch_id and the server answers with the original result instead of storing it twice. Known limitation: `start()` is
not idempotent (the server makes the run id), so a lost response to `start()` can leave one empty extra run.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from typing import Any, Callable, Iterable, Optional

import httpx

__all__ = ["start", "Run", "TrackerError"]


class TrackerError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code = status, code


def _retryable(exc: Optional[Exception], resp: Optional[httpx.Response]) -> bool:
    if exc is not None:
        return isinstance(exc, httpx.TransportError)  # connection reset, read error, timeout, ...
    return resp is not None and (resp.status_code >= 500 or resp.status_code == 429)


class Run:
    def __init__(self, http: httpx.Client, run_id: str, name: str, batch_size: int = 50, max_attempts: int = 8,
                 sleep: Callable[[float], None] = time.sleep, rng: Optional[random.Random] = None):
        self._http, self.id, self.name = http, run_id, name
        self.batch_size, self.max_attempts, self._sleep = batch_size, max_attempts, sleep
        self._rng = rng or random.Random()
        self._buf: list[dict] = []
        self.stats = {"batches": 0, "retries": 0, "accepted": 0, "duplicates": 0}

    # ---- public API ----
    def log(self, step: int, **metrics: float) -> None:
        for metric, value in metrics.items():
            self._buf.append({"metric": metric, "step": step, "value": float(value)})
        if len(self._buf) >= self.batch_size:
            self.flush()

    def log_points(self, points: Iterable[dict]) -> None:
        """Raw points (may carry point_id and ts). Used by the simulator and for corrections."""
        self._buf.extend(points)
        if len(self._buf) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        while self._buf:
            chunk, self._buf = self._buf[: self.batch_size], self._buf[self.batch_size:]
            self.send_batch(chunk)

    def send_batch(self, points: list[dict], batch_id: Optional[str] = None) -> dict:
        """Send one batch, retrying with the SAME batch_id until it is acknowledged."""
        bid = batch_id or self.batch_id_for(points)
        result = self._request("POST", f"/runs/{self.id}/metrics", {"batch_id": bid, "points": points})
        self.stats["batches"] += 1
        self.stats["accepted"] += result["accepted"]
        self.stats["duplicates"] += result["duplicates"]
        return result

    def finish(self, status: str = "finished") -> None:
        self.flush()
        self._request("POST", f"/runs/{self.id}/finish", {"status": status})

    def batch_id_for(self, points: list[dict]) -> str:
        blob = json.dumps(points, sort_keys=True, separators=(",", ":"))
        return "b-" + hashlib.sha256((self.id + blob).encode()).hexdigest()[:24]

    # ---- plumbing ----
    def _request(self, method: str, path: str, body: Any) -> dict:
        return _request(self._http, method, path, body, self.max_attempts, self._sleep, self._rng, self.stats)


def _request(http: httpx.Client, method: str, path: str, body: Any, max_attempts: int, sleep, rng, stats=None) -> dict:
    last: Optional[Exception] = None
    for attempt in range(max_attempts):
        resp, exc = None, None
        try:
            resp = http.request(method, path, json=body)
        except httpx.HTTPError as e:  # noqa: PERF203
            exc = e
        if exc is None and resp is not None and resp.status_code < 400:
            return resp.json()
        if not _retryable(exc, resp):
            try:
                j = resp.json() if resp is not None else {}
            except ValueError:
                j = {}
            raise TrackerError(resp.status_code if resp is not None else 0, j.get("code", "error"), j.get("message", ""))
        last = exc or TrackerError(resp.status_code, "retryable")
        if stats is not None:
            stats["retries"] += 1
        wait = min(5.0, 0.2 * 2 ** attempt) * (0.5 + rng.random())
        if resp is not None and resp.headers.get("retry-after"):
            wait = max(wait, float(resp.headers["retry-after"]))
        sleep(wait)
    raise TrackerError(0, "gave_up", f"{max_attempts} attempts failed: {last}")


def start(key: str, run: str, url: Optional[str] = None, batch_size: int = 50, max_attempts: int = 8,
          transport: Optional[httpx.BaseTransport] = None, sleep: Callable[[float], None] = time.sleep,
          rng: Optional[random.Random] = None, timeout: float = 10.0) -> Run:
    base = (url or os.environ.get("TRACKER_URL") or "http://localhost:8080/api").rstrip("/")
    # one request per connection: a cut connection can only ever affect one request (also what the chaos tests rely on)
    http = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=timeout, transport=transport,
                        limits=httpx.Limits(max_keepalive_connections=0))
    rng = rng or random.Random()
    created = _request(http, "POST", "/runs", {"name": run}, max_attempts, sleep, rng)
    return Run(http, created["id"], created["name"], batch_size, max_attempts, sleep, rng)
