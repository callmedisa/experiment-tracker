import hashlib
import hmac
import secrets
import string

BASE62 = string.ascii_letters + string.digits


def base62(n: int) -> str:
    return "".join(secrets.choice(BASE62) for _ in range(n))


def new_run_id() -> str:
    return "run_" + base62(22)  # 22 base62 chars ~ 131 bits


def new_key(scope: str) -> tuple[str, str]:
    """Return (full_key, display_prefix). Both formats are 40 characters (TZ_dev.md §4)."""
    if scope == "ingest":
        random_part = base62(32)
        full = "trk_ing_" + random_part
    elif scope == "admin":
        random_part = base62(32)
        full = "trk_adm_" + random_part
    else:
        random_part = base62(33)
        full = "trk_rd_" + random_part
    return full, random_part[:4]


def hash_secret(pepper: str, secret: str) -> str:
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
