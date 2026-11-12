"""Client tests with a fake transport (no server needed): python3 -m pytest client/test_tracker.py"""
import json
import random

import httpx
import pytest

import tracker


class Fake(httpx.BaseTransport):
    """Behaves like the server's idempotency for batch_id; `script` lists failures to inject, in order."""

    def __init__(self, script=()):
        self.script, self.seen, self.stored = list(script), {}, 0
        self.requests = []

    def handle_request(self, request):
        body = json.loads(request.content or b"{}")
        self.requests.append((request.url.path, body))
        if request.url.path == "/runs":
            return httpx.Response(201, json={"id": "run_x", "name": body["name"]})
        action = self.script.pop(0) if self.script else "ok"
        if action == "reset-before":
            raise httpx.ConnectError("reset before the request reached the server")
        if request.url.path.endswith("/metrics"):
            if body["batch_id"] not in self.seen:  # the server "commits"
                self.seen[body["batch_id"]] = {"accepted": len(body["points"]), "duplicates": 0, "first_seq": 1, "last_seq": len(body["points"])}
                self.stored += len(body["points"])
            result = self.seen[body["batch_id"]]
        else:
            result = {"status": body["status"]}
        if action == "lost-after":
            raise httpx.ReadError("response lost after commit")
        if action == "500":
            return httpx.Response(500, json={"code": "internal_error"})
        if action == "429":
            return httpx.Response(429, json={"code": "rate_limited"}, headers={"Retry-After": "1"})
        if action == "409":
            return httpx.Response(409, json={"code": "run_closed", "message": "closed"})
        return httpx.Response(200, json=result)


def make(script=(), **kw):
    t = Fake(script)
    sleeps = []
    run = tracker.start("trk_ing_x", "r", url="http://t", transport=t, sleep=sleeps.append, rng=random.Random(1), **kw)
    return run, t, sleeps


def test_batches_by_size_and_flushes_the_rest():
    run, t, _ = make(batch_size=4)
    for s in range(5):
        run.log(s, loss=1.0, acc=0.5)           # 10 points -> batches of 4, 4, 2
    run.flush()
    sizes = [len(b["points"]) for p, b in t.requests if p.endswith("/metrics")]
    assert sizes == [4, 4, 2] and t.stored == 10


def test_batch_id_is_stable_for_identical_content_and_differs_otherwise():
    run, _, _ = make()
    a = [{"metric": "loss", "step": 1, "value": 0.5}]
    assert run.batch_id_for(a) == run.batch_id_for([dict(a[0])])
    assert run.batch_id_for(a) != run.batch_id_for([{"metric": "loss", "step": 1, "value": 0.6}])


@pytest.mark.parametrize("fault", ["reset-before", "lost-after", "500", "429"])
def test_retry_reuses_the_batch_id_and_never_double_counts(fault):
    run, t, sleeps = make([fault, fault])
    run.send_batch([{"metric": "loss", "step": 0, "value": 1.0}])
    metric_reqs = [b for p, b in t.requests if p.endswith("/metrics")]
    assert len(metric_reqs) == 3 and len({b["batch_id"] for b in metric_reqs}) == 1
    assert t.stored == 1                      # stored exactly once, however many times it was sent
    assert run.stats["retries"] == 2 and len(sleeps) == 2


def test_retry_after_header_is_respected():
    run, _, sleeps = make(["429"])
    run.send_batch([{"metric": "loss", "step": 0, "value": 1.0}])
    assert sleeps[0] >= 1.0


def test_non_retryable_errors_raise_immediately():
    run, t, sleeps = make(["409"])
    with pytest.raises(tracker.TrackerError) as e:
        run.send_batch([{"metric": "loss", "step": 0, "value": 1.0}])
    assert e.value.code == "run_closed" and not sleeps


def test_gives_up_after_max_attempts():
    run, _, sleeps = make(["500"] * 10, max_attempts=3)
    with pytest.raises(tracker.TrackerError) as e:
        run.send_batch([{"metric": "loss", "step": 0, "value": 1.0}])
    assert e.value.code == "gave_up" and len(sleeps) == 3


def test_finish_flushes_first():
    run, t, _ = make(batch_size=100)
    run.log(0, loss=1.0)
    run.finish()
    assert [p for p, _ in t.requests] == ["/runs", "/runs/run_x/metrics", "/runs/run_x/finish"]


def test_unsent_points_stay_buffered_after_giving_up_and_go_out_on_the_next_flush():
    run, t, _ = make(["500"] * 3, batch_size=2, max_attempts=3)
    run.log(0, loss=1.0)
    with pytest.raises(tracker.TrackerError):
        run.log(1, loss=0.9)  # batch of 2 fills up, the server is down, the client gives up
    assert len(run._buf) == 2  # nothing was dropped
    run.flush()  # the server is back
    assert t.stored == 2 and run._buf == []
