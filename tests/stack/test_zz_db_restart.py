"""A Postgres restart must not leave the API with dead pooled connections. Named test_zz_* so it runs last (it restarts postgres).
Found while trying to reproduce a lost session (results/session-repro/): the first request after the restart returned 500."""
import subprocess
import time

import httpx

from conftest import GATEWAY, REPO, Proj


def test_signed_in_session_survives_a_postgres_restart(stack_ok):
    p = Proj(GATEWAY)
    assert p.signed.get("/session").status_code == 200
    subprocess.run(["docker", "compose", "restart", "postgres"], cwd=REPO, check=True, capture_output=True)
    for _ in range(60):                                                  # wait until postgres accepts connections again
        if subprocess.run(["docker", "compose", "exec", "-T", "postgres", "pg_isready", "-U", "tracker"], cwd=REPO, capture_output=True).returncode == 0:
            break
        time.sleep(1)
    time.sleep(1)
    r = p.signed.get("/session")                                         # the FIRST request after the restart, on a pool that holds dead connections
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    assert httpx.Client(base_url=GATEWAY + "/api").post("/runs", json={"name": "x"}, headers=p.bearer).status_code == 201
