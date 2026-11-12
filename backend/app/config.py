import os
from dataclasses import dataclass, fields


def _b(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


DEFAULT_PEPPER = "dev-pepper-change-me"   # public (it is in the repo): anyone who knows it and reads the database can check key guesses


@dataclass(frozen=True)
class Settings:
    database_url: str
    pepper: str
    session_ttl_s: int = 12 * 3600
    cookie_secure: bool = False
    stale_after_s: int = 300
    max_body_bytes: int = 1_000_000
    max_points: int = 1000
    resync_threshold: int = 10_000
    stats_interval_s: float = 5.0
    revalidate_s: float = 2.0
    stream_page: int = 500
    # Rate limits (the dev spec §5 defaults). Env-configurable; each results/ run must record the values.
    rl_projects_per_min: int = 20  # POST /projects, per IP
    rl_session_per_min: int = 10  # POST /session attempts, per IP
    rl_auth_fail_per_min: int = 10  # failed key lookups, per IP
    rl_ingest_per_s: int = 200  # ingest requests, per project
    trusted_proxy_hops: int = 0  # >0: take client IP from X-Forwarded-For (that many hops from the right)
    allowed_origins: tuple = ()  # extra allowed Origins; otherwise Origin must match the Host header
    # Test-only fault injection (JOURNAL: owner approved). MUST be off in load/verdict runs.
    fault_hooks: bool = False

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            database_url=os.environ["DATABASE_URL"],
            pepper=os.environ.get("SERVER_PEPPER", DEFAULT_PEPPER),
            session_ttl_s=int(os.environ.get("SESSION_TTL_S", 12 * 3600)),
            cookie_secure=_b("COOKIE_SECURE", "0"),
            stale_after_s=int(os.environ.get("STALE_AFTER_S", 300)),
            rl_projects_per_min=int(os.environ.get("RL_PROJECTS_PER_MIN", 20)),
            rl_session_per_min=int(os.environ.get("RL_SESSION_PER_MIN", 10)),
            rl_auth_fail_per_min=int(os.environ.get("RL_AUTH_FAIL_PER_MIN", 10)),
            rl_ingest_per_s=int(os.environ.get("RL_INGEST_PER_S", 200)),
            trusted_proxy_hops=int(os.environ.get("TRUSTED_PROXY_HOPS", 0)),
            allowed_origins=tuple(o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o),
            resync_threshold=int(os.environ.get("RESYNC_THRESHOLD", 10_000)),
            stats_interval_s=float(os.environ.get("STATS_INTERVAL_S", 5)),
            revalidate_s=float(os.environ.get("REVALIDATE_S", 2)),
            fault_hooks=_b("FAULT_HOOKS", "0"),
        )

    def describe(self) -> list[str]:
        """Settings worth recording next to raw results (pepper and DB password are left out)."""
        skip = {"database_url", "pepper"}
        return [f"{f.name}={getattr(self, f.name)}" for f in fields(self) if f.name not in skip]
