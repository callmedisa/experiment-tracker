"""The single authorization point (TZ_dev.md §7). Every route gets its project from here, never from the request."""
import hmac
import time
from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from sqlalchemy import func, select, text, update

from .errors import AppError
from .models import ApiKey, Session
from .security import hash_secret

COOKIE = "tracker_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "tracker"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Principal:
    project_id: UUID
    key_id: UUID
    scope: str
    session_id: str | None = None


def client_ip(request: Request) -> str:
    hops = request.app.state.settings.trusted_proxy_hops
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


def rate_limited(retry_after: int) -> AppError:
    return AppError(429, "rate_limited", "Too many requests", headers={"Retry-After": str(retry_after)})


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if h[:7].lower() == "bearer " and len(h) <= 300:
        return h[7:].strip()
    return None


async def lookup_key(request: Request, raw: str | None, scope: str | tuple[str, ...]) -> Principal:
    """Bearer-key lookup. Unknown key, wrong secret, malformed header and wrong scope all give the same 401."""
    st = request.app.state
    ip = client_ip(request)
    retry = st.fail_limiter.retry_after(ip)
    if retry:
        raise rate_limited(retry)
    digest = hash_secret(st.settings.pepper, raw or "")  # always computed: same work for every kind of bad key
    async with st.sessionmaker() as s:
        row = (
            await s.execute(
                select(ApiKey.id, ApiKey.project_id, ApiKey.scope, ApiKey.revoked_at, ApiKey.key_hash).where(
                    ApiKey.key_hash == digest
                )
            )
        ).first()
    if raw is None or row is None or not hmac.compare_digest(row.key_hash, digest):
        st.fail_limiter.hit(ip)
        raise AppError(401, "invalid_key", "Invalid key")
    if row.revoked_at is not None:
        st.fail_limiter.hit(ip)
        raise AppError(401, "key_revoked", "This key has been revoked")
    if row.scope not in ((scope,) if isinstance(scope, str) else scope):
        st.fail_limiter.hit(ip)
        raise AppError(401, "invalid_key", "Invalid key")
    await _touch_last_used(st, row.id)
    return Principal(row.project_id, row.id, row.scope)


async def _touch_last_used(st, key_id: UUID) -> None:
    """At most one write per key per minute (no hot row). The WHERE clause keeps it true across processes too."""
    now = time.monotonic()
    if now - st.last_touch.get(key_id, -1e9) < 60:
        return
    st.last_touch[key_id] = now
    async with st.sessionmaker() as s, s.begin():
        await s.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id, (ApiKey.last_used_at.is_(None)) | (ApiKey.last_used_at < func.now() - text("interval '1 minute'")))
            .values(last_used_at=func.now())
        )


async def ingest_principal(request: Request) -> Principal:
    p = await lookup_key(request, _bearer(request), "ingest")
    retry = request.app.state.ingest_limiter.hit(str(p.project_id))
    if retry:
        raise rate_limited(retry)
    return p


def _check_csrf(request: Request) -> None:
    st = request.app.state
    origin = request.headers.get("origin")
    ok = request.headers.get(CSRF_HEADER) == CSRF_VALUE and origin is not None
    if ok:
        host = request.headers.get("host", "")
        ok = origin in st.settings.allowed_origins or origin.split("://", 1)[-1] == host
    if not ok:
        raise AppError(403, "csrf_failed", "Request rejected")


async def session_principal(request: Request) -> Principal:
    st = request.app.state
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 200:
        raise AppError(401, "session_expired", "Your session has expired")
    sid = hash_secret(st.settings.pepper, token)
    async with st.sessionmaker() as s:
        row = (
            await s.execute(
                select(
                    Session.id,
                    (Session.expires_at > func.now()).label("live"),
                    ApiKey.id.label("key_id"),
                    ApiKey.project_id,
                    ApiKey.scope,
                    ApiKey.revoked_at,
                )
                .join(ApiKey, ApiKey.id == Session.key_id)
                .where(Session.id == sid)
            )
        ).first()
    if row is None or not row.live:
        raise AppError(401, "session_expired", "Your session has expired")
    if row.revoked_at is not None:
        raise AppError(401, "key_revoked", "This key has been revoked")
    if request.method not in SAFE_METHODS:
        _check_csrf(request)
    return Principal(row.project_id, row.key_id, row.scope, session_id=row.id)


async def admin_principal(request: Request) -> Principal:
    """Key management needs a session opened with an ADMIN key. A read key can look at runs but cannot touch keys."""
    p = await session_principal(request)
    if p.scope != "admin":
        raise AppError(403, "admin_required", "This action needs an admin key")
    return p
