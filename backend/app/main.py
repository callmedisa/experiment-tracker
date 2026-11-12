import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import MutableHeaders

from . import errors, routes_auth, routes_runs, streaming
from .hub import Hub
from .config import DEFAULT_PEPPER, Settings
from .ratelimit import SlidingWindow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class RequestMeta:
    """Pure ASGI middleware: request id on every request; no-store/Vary on every response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = rid

        t0 = time.monotonic()
        status = {"code": 0}

        async def send2(msg):
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
                h = MutableHeaders(scope=msg)
                h["X-Request-Id"] = rid
                if "cache-control" not in h:
                    h["Cache-Control"] = "private, no-store"
                if "vary" not in h:
                    h["Vary"] = "Cookie, Authorization"
            await send(msg)

        try:
            await self.app(scope, receive, send2)
        finally:
            # One line per request: id, method, path, status, time. No headers, no query string, no bodies, no keys.
            logging.getLogger("tracker.access").info("%s %s %s %s %.0fms", rid, scope["method"], scope["path"], status["code"], (time.monotonic() - t0) * 1000)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.pepper == DEFAULT_PEPPER:
        logging.getLogger("tracker").warning(
            "SERVER_PEPPER is the public default from the repository. Key and session hashes can be checked by anyone who has a copy of the "
            "database. Set a random SERVER_PEPPER (make pepper). Changing it later invalidates every key and session.")
    engine = create_async_engine(settings.database_url, pool_size=10, max_overflow=20, pool_pre_ping=True)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await app.state.hub.stop()
        await engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    st = app.state
    st.settings = settings
    st.engine = engine
    st.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    st.projects_limiter = SlidingWindow(settings.rl_projects_per_min, 60)
    st.session_limiter = SlidingWindow(settings.rl_session_per_min, 60)
    st.fail_limiter = SlidingWindow(settings.rl_auth_fail_per_min, 60)
    st.ingest_limiter = SlidingWindow(settings.rl_ingest_per_s, 1)
    st.hub = Hub(settings.database_url)
    st.last_touch = {}
    errors.install(app)
    app.include_router(routes_auth.router)
    app.include_router(routes_runs.router)
    app.include_router(streaming.router)
    app.add_middleware(RequestMeta)
    if settings.fault_hooks:
        logging.getLogger("tracker").warning("FAULT_HOOKS are ENABLED: test use only")
    return app


def app_factory() -> FastAPI:  # uvicorn --factory target
    return create_app()
