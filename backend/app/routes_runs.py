import hashlib
import re
import struct
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import json

from sqlalchemy import func, insert, select, text, update

from .auth import Principal, ingest_principal, session_principal
from .errors import AppError, not_found
from .models import IngestBatch, MetricEvent, Run
from .security import new_run_id

router = APIRouter()

ID_CHARS = r"^[A-Za-z0-9._:/\-]+$"  # no '|' (checksum separator), no whitespace


class InjectedFault(RuntimeError):
    """Raised only by the env-gated test hook."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunIn(Strict):
    name: str = Field(min_length=1, max_length=100)


class FinishIn(Strict):
    status: str = Field(pattern=r"^(finished|failed)$")


class PointIn(Strict):
    point_id: Optional[str] = Field(default=None, min_length=1, max_length=128, pattern=ID_CHARS)
    metric: str = Field(min_length=1, max_length=64, pattern=ID_CHARS)
    step: int = Field(ge=0, le=2**53 - 1, strict=True)  # JS-safe integer: the browser parses JSON numbers as doubles
    value: float = Field(allow_inf_nan=False, strict=True)  # NaN / Infinity -> 422 naming the field
    ts: Optional[datetime] = None


class BatchIn(Strict):
    batch_id: str = Field(min_length=1, max_length=128, pattern=ID_CHARS)
    points: list[PointIn] = Field(min_length=1)


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def _lookup_id(raw: str) -> str:
    """Anything that cannot be a run id (NUL bytes, huge strings, ...) is looked up as '' instead of being
    rejected early, so malformed ids take the same code path and DB round trip as unknown ones."""
    return raw if _RUN_ID_RE.match(raw) else ""


def _bits(v: float) -> bytes:
    return struct.pack(">d", v)  # identical doubles <=> identical bytes (also tells 0.0 from -0.0)


def _iso(d):
    return d.isoformat() if d else None


async def _notify(s, run_id: str, dup: dict | None = None) -> None:
    """Delivered by Postgres only if the transaction commits. A wake-up, never the data."""
    payload = json.dumps({"r": run_id, **({"dup": dup} if dup else {})})
    await s.execute(select(func.pg_notify("run_events", payload)))


def _summary(accepted, duplicates, first_seq, last_seq) -> dict:
    return {"accepted": accepted, "duplicates": duplicates, "first_seq": first_seq, "last_seq": last_seq}


@router.post("/runs", status_code=201)
async def create_run(body: RunIn, request: Request, p: Principal = Depends(ingest_principal)):
    rid = new_run_id()
    async with request.app.state.sessionmaker() as s, s.begin():
        await s.execute(insert(Run).values(id=rid, project_id=p.project_id, name=body.name))
    return {"id": rid, "name": body.name}


async def _lock_run(s, project_id, run_id: str):
    """The only way routes load a run: project_id is required, so a foreign run is simply absent."""
    row = (await s.execute(
        select(Run.id, Run.status, Run.last_seq, Run.finished_at)
        .where(Run.id == _lookup_id(run_id), Run.project_id == project_id).with_for_update())).first()
    if row is None:
        raise not_found()
    return row


@router.post("/runs/{run_id}/finish")
async def finish_run(run_id: str, body: FinishIn, request: Request, p: Principal = Depends(ingest_principal)):
    async with request.app.state.sessionmaker() as s, s.begin():
        run = await _lock_run(s, p.project_id, run_id)
        if run.status == "running":
            await s.execute(update(Run).where(Run.id == run_id).values(status=body.status, finished_at=func.now()))
        elif run.status != body.status:
            raise AppError(409, "run_closed", "This run is already closed")
        await _notify(s, run_id)
        row = (await s.execute(select(Run.id, Run.status, Run.finished_at).where(Run.id == run_id))).one()
    return {"id": row.id, "status": row.status, "finished_at": _iso(row.finished_at)}


_LIST_SQL = text("""
SELECT r.id, r.name, r.status, r.created_at, r.finished_at, r.last_seq, r.duplicates_ignored, r.last_event_at,
       now() AS server_time, pm.metric AS primary_metric, lv.value AS latest_value, sp.spark
FROM (SELECT * FROM runs WHERE project_id = :p ORDER BY created_at DESC, id LIMIT 500) r
LEFT JOIN LATERAL (SELECT metric FROM metric_events WHERE run_id = r.id AND seq = 1) pm ON true
LEFT JOIN LATERAL (SELECT value FROM metric_events WHERE run_id = r.id AND metric = pm.metric
                   ORDER BY step DESC, seq DESC LIMIT 1) lv ON true
LEFT JOIN LATERAL (
    SELECT array_agg(value ORDER BY step) AS spark FROM (
        SELECT value, step, (row_number() OVER (ORDER BY step, seq) - 1) AS rn, count(*) OVER () AS c
        FROM metric_events WHERE run_id = r.id AND metric = pm.metric) t
    WHERE rn % GREATEST(c / 30, 1) = 0) sp ON true
ORDER BY r.created_at DESC, r.id
""")


@router.get("/runs")
async def list_runs(request: Request, p: Principal = Depends(session_principal)):
    """Up to 500 newest runs with aggregates, one query. Sparkline = every n-th point of the primary metric
    (approximation: it ignores the 'highest seq per step' rule). Cost with many runs is unmeasured (JOURNAL concern)."""
    st = request.app.state
    async with st.sessionmaker() as s:
        rows = (await s.execute(_LIST_SQL, {"p": p.project_id})).all()
    out = []
    for r in rows:
        baseline = r.last_event_at or r.created_at
        out.append({
            "id": r.id, "name": r.name, "status": r.status, "created_at": _iso(r.created_at),
            "finished_at": _iso(r.finished_at), "events_count": r.last_seq, "duplicates_ignored": r.duplicates_ignored,
            "last_event_at": _iso(r.last_event_at), "primary_metric": r.primary_metric, "latest_value": r.latest_value,
            "spark": list(r.spark or [])[:30],
            "stale": r.status == "running" and (r.server_time - baseline).total_seconds() >= st.settings.stale_after_s,
        })
    return {"runs": out, "server_time": _iso(rows[0].server_time) if rows else None,
            "stale_after_s": st.settings.stale_after_s}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request, p: Principal = Depends(session_principal)):
    st = request.app.state
    async with st.sessionmaker() as s:
        r = (await s.execute(
            select(Run, func.now().label("server_time")).where(Run.id == _lookup_id(run_id), Run.project_id == p.project_id))).first()
    if r is None:
        raise not_found()
    run, now = r
    baseline = run.last_event_at or run.created_at
    stale = run.status == "running" and (now - baseline).total_seconds() >= st.settings.stale_after_s
    return {
        "id": run.id, "name": run.name, "status": run.status, "created_at": _iso(run.created_at),
        "finished_at": _iso(run.finished_at), "last_seq": run.last_seq, "count": run.last_seq,
        "duplicates_ignored": run.duplicates_ignored, "last_event_at": _iso(run.last_event_at),
        "server_time": _iso(now), "stale": stale, "stale_after_s": st.settings.stale_after_s,
    }


@router.post("/runs/{run_id}/metrics")
async def ingest_metrics(run_id: str, request: Request, p: Principal = Depends(ingest_principal)):
    st = request.app.state
    cfg = st.settings
    raw = await request.body()
    if len(raw) > cfg.max_body_bytes:
        raise AppError(413, "payload_too_large", "Batch is larger than 1 MB")
    try:
        batch = BatchIn.model_validate_json(raw)
    except ValidationError as e:
        from fastapi.exceptions import RequestValidationError
        raise RequestValidationError(e.errors())
    if len(batch.points) > cfg.max_points:
        raise AppError(422, "validation_error", "Invalid field: points", extra={"fields": ["points"]})

    fault = request.headers.get("x-fault") if cfg.fault_hooks else None

    # Normalise: effective point_id, and a content hash of the batch (ts is not part of the content).
    pts = []
    h = hashlib.sha256()
    for pt in batch.points:
        pid = pt.point_id or f"{pt.metric}:{pt.step}"
        pts.append((pid, pt.metric, pt.step, pt.value, pt.ts))
        h.update(f"{pid}|{pt.metric}|{pt.step}|".encode() + _bits(pt.value).hex().encode() + b"\n")
    payload_hash = h.hexdigest()
    received_at = datetime.now(timezone.utc)

    async with st.sessionmaker() as s:
        async with s.begin():
            # 1. Lock the run row first. Everything below is decided while holding it.
            run = await _lock_run(s, p.project_id, run_id)

            # 2. Same batch_id? (checked before the closed-run test: a retry gets its original answer.)
            b = (await s.execute(select(IngestBatch).where(
                IngestBatch.run_id == run_id, IngestBatch.batch_id == batch.batch_id))).scalar_one_or_none()
            if b is not None:
                if b.payload_hash != payload_hash:
                    raise AppError(409, "batch_conflict", "This batch_id was already used with different points")
                await s.execute(update(Run).where(Run.id == run_id).values(
                    duplicates_ignored=Run.duplicates_ignored + b.accepted + b.duplicates))
                result = _summary(b.accepted, b.duplicates, b.first_seq, b.last_seq)
                await _notify(s, run_id, {"batch_id": batch.batch_id, "first_seq": b.first_seq,
                                          "last_seq": b.last_seq, "duplicates": b.accepted + b.duplicates})
            else:
                if run.status != "running":
                    raise AppError(409, "run_closed", "This run is already closed")
                result = await _store_new(s, run_id, batch.batch_id, payload_hash, pts, received_at, fault)
        if fault == "after-commit":
            raise InjectedFault("response lost after commit")
    return result


async def _store_new(s, run_id, batch_id, payload_hash, pts, received_at, fault) -> dict:
    # 3. Classify every point in array order: new, identical duplicate, or conflict.
    ids = [x[0] for x in pts]
    # One index probe per id (LATERAL = nested loop by construction). A plain "point_id = ANY(...)" lets the planner pick
    # the (run_id, metric, step) index when table statistics are missing, which scans the whole run (see JOURNAL).
    found = (await s.execute(text(
        "SELECT u.pid AS point_id, e.metric, e.step, e.value FROM unnest(CAST(:ids AS text[])) AS u(pid) "
        "CROSS JOIN LATERAL (SELECT metric, step, value FROM metric_events "
        "WHERE run_id = :r AND point_id = u.pid LIMIT 1) e"), {"ids": ids, "r": run_id})).all()
    known = {r.point_id: (r.metric, r.step, _bits(r.value)) for r in found}
    accepted, duplicates, conflicts = [], 0, []
    for pid, metric, step, value, ts in pts:
        sig = (metric, step, _bits(value))
        if pid in known:
            if known[pid] == sig:
                duplicates += 1
            else:
                conflicts.append(pid)
        else:
            known[pid] = sig  # a repeat inside this batch is judged against the first occurrence
            accepted.append((pid, metric, step, value, ts))
    if conflicts:
        shown = sorted(set(conflicts))
        raise AppError(409, "point_conflict", "Some point_ids were already stored with different content",
                       extra={"point_ids": shown[:100], "conflicts": len(shown)})

    # 4. is_late: step below the highest step already stored for that metric (running, in array order).
    highest: dict[str, int | None] = {}
    for metric in {a[1] for a in accepted}:
        highest[metric] = (await s.execute(
            select(MetricEvent.step).where(MetricEvent.run_id == run_id, MetricEvent.metric == metric)
            .order_by(MetricEvent.step.desc()).limit(1))).scalar_one_or_none()
    rows = []
    for pid, metric, step, value, ts in accepted:
        top = highest[metric]
        late = top is not None and step < top
        if top is None or step > top:
            highest[metric] = step
        rows.append({"run_id": run_id, "point_id": pid, "metric": metric, "step": step, "value": value,
                     "client_ts": (ts.replace(tzinfo=timezone.utc) if ts and ts.tzinfo is None else ts),
                     "received_at": received_at, "is_late": late})

    # 5. Take n seqs and insert. Counter bump and rows are one transaction.
    n = len(rows)
    first = last = None
    if n:
        last = (await s.execute(update(Run).where(Run.id == run_id)
                .values(last_seq=Run.last_seq + n, last_event_at=received_at)
                .returning(Run.last_seq))).scalar_one()
        first = last - n + 1
        for i, r in enumerate(rows):
            r["seq"] = first + i
        if fault == "mid-batch":
            if n // 2:
                await s.execute(insert(MetricEvent), rows[: n // 2])
            raise InjectedFault("failure in the middle of a batch")
        await s.execute(insert(MetricEvent), rows)
    if duplicates:
        await s.execute(update(Run).where(Run.id == run_id).values(duplicates_ignored=Run.duplicates_ignored + duplicates))
    await s.execute(insert(IngestBatch).values(
        run_id=run_id, batch_id=batch_id, payload_hash=payload_hash, accepted=n, duplicates=duplicates,
        first_seq=first, last_seq=last))
    await _notify(s, run_id, {"batch_id": batch_id, "first_seq": first, "last_seq": last, "duplicates": duplicates}
                  if duplicates else None)
    return _summary(n, duplicates, first, last)
