"""Stack tests run against the RUNNING compose stack (make stack-tests), over real HTTP, with the repo's .venv python.
Raise the rate limits first (everything shares one IP); the Makefile target does that and records the settings."""
import os
import secrets
import subprocess
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
GATEWAY = os.environ.get("STACK_GATEWAY", "http://localhost:8080")      # nginx: static app + /api
CHAOS_GATEWAY = os.environ.get("STACK_CHAOS_GATEWAY", "http://localhost:8081")  # same, through the chaos proxy


def hdr(base, extra=None):
    host = base.split("://", 1)[1]
    return {"X-Requested-With": "tracker", "Origin": base, **(extra or {})}


class Proj:
    def __init__(self, base):
        self.base = base
        self.c = httpx.Client(base_url=base + "/api", timeout=15)
        r = self.c.post("/projects", json={"name": "stack-" + secrets.token_hex(4)})
        assert r.status_code == 201, r.text
        d = r.json()
        self.id, self.ingest, self.read = d["project"]["id"], d["keys"]["ingest"], d["keys"]["read"]
        self.bearer = {"Authorization": "Bearer " + self.ingest["key"]}
        self.run_id = self.c.post("/runs", json={"name": "r"}, headers=self.bearer).json()["id"]
        self.c.post(f"/runs/{self.run_id}/metrics", headers=self.bearer,
                    json={"batch_id": "b1", "points": [{"metric": "stack-marker-metric", "step": i, "value": 7.0 + i} for i in range(5)]})
        self.signed = httpx.Client(base_url=base + "/api", timeout=15)       # a client holding this project's read session
        assert self.signed.post("/session", json={"key": self.read["key"]}).status_code == 200

    def csrf(self):
        return hdr(self.base)


@pytest.fixture(scope="session")
def stack_ok():
    try:
        assert httpx.get(GATEWAY + "/api/nope", timeout=5).status_code == 404
    except Exception as e:  # noqa: BLE001
        pytest.exit(f"the compose stack is not reachable at {GATEWAY}: {e}", returncode=2)


@pytest.fixture()
def two(stack_ok):
    return Proj(GATEWAY), Proj(GATEWAY)


def compose_logs() -> str:
    return subprocess.run(["docker", "compose", "logs", "--no-color", "api", "web", "chaos", "postgres"], cwd=REPO,
                          capture_output=True, text=True, check=True).stdout
