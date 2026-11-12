"""SSE stream, replay, seqs and integrity (the dev spec §5.2, §5.3; DESIGN.md §3)."""
import asyncio
import hashlib
import json
import struct
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text

from .auth import Principal, session_principal
from .errors import not_found
from .models import ApiKey, MetricEvent, Run, Session
from .routes_runs import _lookup_id

router = APIRouter()


def _iso(d):
    return d.isoformat() if d else None


def sse(event: str, data: dict, id_: Optional[int] = None) -> bytes:
    head = f"event: {event}\n" + (f"id: {id_}\n" if id_ is not None else "")
    return (head + "data: " + json.dumps(data, separators=(",", ":")) + "\n\n").encode()


def event_json(e) -> dict:
    return {"seq": e.seq, "point_id": e.point_id, "metric": e.metric, "step": e.step, "value": e.value,
            "ts": _iso(e.client_ts), "received_at": _iso(e.received_at), "is_late": e.is_late}


async def _run_state(st, project_id, run_id):
    async with st.sessionmaker() as s:
        return (await s.execute(select(Run.last_seq, Run.duplicates_ignored, Run.status, Run.finished_at, Run.last_event_at,
                                       func.now().label("server_time"))
                                .where(Run.id == _lookup_id(run_id), Run.project_id == project_id))).first()


def _stats(r) -> dict:
    return {"last_seq": r.last_seq, "count": r.last_seq, "duplicates_ignored": r.duplicates_ignored, "status": r.status,
            "last_event_at": _iso(r.last_event_at), "server_time": _iso(r.server_time)}


async def _fetch(st, run_id, after, limit):
    async with st.sessionmaker() as s:
        return (await s.execute(
            select(MetricEvent.seq, MetricEvent.point_id, MetricEvent.metric, MetricEvent.step, MetricEvent.value,
                   MetricEvent.client_ts, MetricEvent.received_at, MetricEvent.is_late)
            .where(MetricEvent.run_id == run_id, MetricEvent.seq > after).order_by(MetricEvent.seq).limit(limit))).all()


async def _session_valid(st, session_id) -> bool:
    async with st.sessionmaker() as s:
        row = (await s.execute(
            select((Session.expires_at > func.now()).label("live"), ApiKey.revoked_at)
            .join(ApiKey, ApiKey.id == Session.key_id).where(Session.id == session_id))).first()
    return bool(row and row.live and row.revoked_at is None)


@router.get("/runs/{run_id}/stream")
async def stream(run_id: str, request: Request, after_seq: Optional[int] = Query(None, ge=0),
                 last_event_id: Optional[str] = Header(None), p: Principal = Depends(session_principal)):
    st = request.app.state
    cfg = st.settings
    if await _run_state(st, p.project_id, run_id) is None:
        raise not_found()
    cursor = after_seq  # the query wins over the header
    if cursor is None:
        cursor = int(last_event_id) if last_event_id and last_event_id.strip().isdigit() else 0

    async def gen():
        sub = await st.hub.subscribe(run_id)  # subscribe first: nothing can fall between replay and live
        cur = cursor
        try:
            run = await _run_state(st, p.project_id, run_id)
            yield sse("stats", _stats(run))  # before replay, so the client knows how much it must catch up
            if cur > run.last_seq or run.last_seq - cur > cfg.resync_threshold:
                yield sse("resync", {"last_seq": run.last_seq})
                return
            last_stats = last_check = time.monotonic()
            sent_since_stats = False
            while True:
                sub.wake.clear()  # clear BEFORE reading, so a notify during the read is not lost
                rows = await _fetch(st, run_id, cur, cfg.stream_page)
                for e in rows:
                    yield sse("metric", event_json(e), e.seq)
                    cur = e.seq
                    sent_since_stats = True
                while sub.dups:
                    yield sse("duplicate_batch", sub.dups.popleft())
                run = await _run_state(st, p.project_id, run_id)
                if run is None:
                    return
                lag = run.last_seq - cur
                if lag > cfg.resync_threshold:
                    yield sse("resync", {"last_seq": run.last_seq})
                    return
                if lag == 0 and run.status != "running":
                    yield sse("stats", _stats(run))
                    yield sse("closed", {"status": run.status, "finished_at": _iso(run.finished_at)})
                    return
                now = time.monotonic()
                if now - last_stats >= cfg.stats_interval_s or (sent_since_stats and lag == 0 and now - last_stats >= 1):
                    yield sse("stats", _stats(run))
                    last_stats, sent_since_stats = now, False
                if now - last_check >= cfg.revalidate_s:
                    last_check = now
                    if not await _session_valid(st, p.session_id):
                        return  # revoked / rotated / expired: just end, say nothing about why
                if lag > 0:
                    continue
                try:
                    await asyncio.wait_for(sub.wake.wait(), timeout=min(cfg.revalidate_s, cfg.stats_interval_s))
                except asyncio.TimeoutError:
                    pass
        finally:
            st.hub.unsubscribe(run_id, sub)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no",
                                      "Vary": "Cookie, Authorization"})


@router.get("/runs/{run_id}/events")
async def events(run_id: str, request: Request, after_seq: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=5000),
                 p: Principal = Depends(session_principal)):
    st = request.app.state
    run = await _run_state(st, p.project_id, run_id)
    if run is None:
        raise not_found()
    rows = await _fetch(st, run_id, after_seq, limit)
    return {"events": [event_json(e) for e in rows], "last_seq": run.last_seq}


@router.get("/runs/{run_id}/seqs")
async def seqs(run_id: str, request: Request, p: Principal = Depends(session_principal)):
    st = request.app.state
    if await _run_state(st, p.project_id, run_id) is None:
        raise not_found()
    async with st.sessionmaker() as s:
        rows = (await s.execute(text(
            "SELECT min(seq) AS a, max(seq) AS b FROM (SELECT seq, seq - row_number() OVER (ORDER BY seq) AS g "
            "FROM metric_events WHERE run_id = :r) t GROUP BY g ORDER BY a"), {"r": run_id})).all()
    return {"ranges": [[r.a, r.b] for r in rows]}


def canonical_line(point_id: str, metric: str, step: int, value: float) -> str:
    # value = 16 hex digits of the IEEE-754 big-endian bits: identical in Python and JavaScript (JOURNAL: checksum spec-gap)
    return f"{point_id}|{metric}|{step}|{struct.pack('>d', value).hex()}\n"


@router.get("/runs/{run_id}/integrity")
async def integrity(run_id: str, request: Request, up_to_seq: Optional[int] = Query(None, ge=0),
                    p: Principal = Depends(session_principal)):
    """count/checksum over seq <= up_to_seq (default: all). The checksum sorts by UTF-8 bytes of point_id in Python,
    never by SQL collation."""
    st = request.app.state
    run = await _run_state(st, p.project_id, run_id)
    if run is None:
        raise not_found()
    limit_seq = run.last_seq if up_to_seq is None else min(up_to_seq, run.last_seq)
    async with st.sessionmaker() as s:
        rows = (await s.execute(select(MetricEvent.point_id, MetricEvent.metric, MetricEvent.step, MetricEvent.value)
                                .where(MetricEvent.run_id == run_id, MetricEvent.seq <= limit_seq))).all()
    rows.sort(key=lambda r: r.point_id.encode())
    h = hashlib.sha256()
    for r in rows:
        h.update(canonical_line(r.point_id, r.metric, r.step, r.value).encode())
    return {"count": len(rows), "checksum": h.hexdigest(), "last_seq": limit_seq}
