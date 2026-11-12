import asyncio
import dataclasses
import os
import socket
import subprocess
import sys
from pathlib import Path

import asyncpg
import httpx
import pytest_asyncio
import uvicorn
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.main import create_app

BACKEND = Path(__file__).resolve().parent.parent
TEST_DB = "tracker_test"

# Rate limits are relaxed for the whole suite (many projects/bad keys come from one IP).
# The rate-limit tests build their own app with tiny limits. Values are printed in the report header.
BASE = dict(
    rl_projects_per_min=100000, rl_session_per_min=100000, rl_auth_fail_per_min=100000, rl_ingest_per_s=100000,
    fault_hooks=True,  # the fault hooks are exercised on purpose; load/verdict runs use FAULT_HOOKS=0
)


def _admin_dsn() -> str:
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    return url.rsplit("/", 1)[0] + "/postgres"


def _test_url() -> str:
    return os.environ["DATABASE_URL"].rsplit("/", 1)[0] + "/" + TEST_DB


def _settings(**over) -> Settings:
    s = Settings.from_env()
    return dataclasses.replace(s, database_url=_test_url(), **{**BASE, **over})


def pytest_report_header(config):
    return ["tracker settings used by this run: " + ", ".join(_settings().describe()),
            f"database: {TEST_DB} (fresh, created by the test session)"]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _db():
    conn = await asyncpg.connect(_admin_dsn())
    await conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await conn.execute(f"CREATE DATABASE {TEST_DB}")
    await conn.close()
    env = {**os.environ, "DATABASE_URL": _test_url()}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True)
    yield


@pytest_asyncio.fixture
async def make_app(_db):
    apps = []

    async def _make(**over):
        app = create_app(_settings(**over))
        apps.append(app)
        return app

    yield _make
    for a in apps:
        await a.state.hub.stop()
        await a.state.engine.dispose()


@pytest_asyncio.fixture
async def app(make_app):
    return await make_app()


def client_for(app, **kw):
    # raise_app_exceptions=False: an injected crash must look like an HTTP 500, as it would to a real client.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                             base_url="http://test", **kw)


@pytest_asyncio.fixture
async def client(app):
    async with client_for(app) as c:
        yield c


@pytest_asyncio.fixture
async def db(app):
    """Direct DB access for ground truth (does not go through the API under test)."""
    engine = create_async_engine(_test_url())
    yield engine
    await engine.dispose()


CSRF = {"X-Requested-With": "tracker", "Origin": "http://test"}


class Project:
    def __init__(self, data):
        self.id = data["project"]["id"]
        self.name = data["project"]["name"]
        self.ingest_key = data["keys"]["ingest"]["key"]
        self.read_key = data["keys"]["read"]["key"]
        self.ingest_key_id = data["keys"]["ingest"]["id"]
        self.read_key_id = data["keys"]["read"]["id"]
        self.ingest = {"Authorization": f"Bearer {self.ingest_key}"}


_n = 0


async def new_project(client) -> Project:
    global _n
    _n += 1
    r = await client.post("/projects", json={"name": f"proj-{os.getpid()}-{_n}"})
    assert r.status_code == 201, r.text
    return Project(r.json())


async def sign_in(client, key) -> httpx.Response:
    return await client.post("/session", json={"key": key})


@pytest_asyncio.fixture
async def proj(client) -> Project:
    return await new_project(client)


@pytest_asyncio.fixture
async def run_id(client, proj) -> str:
    r = await client.post("/runs", json={"name": "r1"}, headers=proj.ingest)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest_asyncio.fixture
async def proj_factory():
    """Returns an async function: client -> (project, run_id)."""
    async def _f(c):
        p = await new_project(c)
        r = await c.post("/runs", json={"name": "r"}, headers=p.ingest)
        assert r.status_code == 201, r.text
        return p, r.json()["id"]
    return _f


def pt(metric="loss", step=0, value=1.0, **kw):
    return {"metric": metric, "step": step, "value": value, **kw}


async def post_batch(client, proj, run_id, batch_id, points, **headers):
    return await client.post(f"/runs/{run_id}/metrics", json={"batch_id": batch_id, "points": points},
                             headers={**proj.ingest, **headers})


@pytest_asyncio.fixture
async def live(make_app):
    """Starts the app on a real localhost port (uvicorn in this event loop). ASGITransport cannot stream SSE."""
    started = []

    async def _start(**over):
        app = await make_app(**over)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        started.append((server, task))
        return app, f"http://127.0.0.1:{port}"

    yield _start
    for server, task in started:
        server.should_exit = True
        await task
