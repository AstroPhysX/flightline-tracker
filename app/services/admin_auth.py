from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request, Response

from ..config import settings

COOKIE_NAME = "ups_tracker_admin"
PBKDF2_ROUNDS = 310_000
REMEMBER_SECONDS = 30 * 24 * 60 * 60
_LOCK = threading.Lock()
_FAILURES: dict[str, list[float]] = {}


@dataclass(frozen=True)
class PasswordRecord:
    salt: bytes
    digest: bytes


def _record_path() -> Path:
    return settings.app_data_dir / "admin-auth.json"


def _session_secret_path() -> Path:
    return settings.app_data_dir / "admin-session-secret.bin"


def _password_from_config() -> str | None:
    file_path = os.getenv("ADMIN_PASSWORD_FILE", "").strip()
    if file_path:
        try:
            value = Path(file_path).read_text().strip()
            if value:
                return value
        except OSError:
            return None
    value = os.getenv("ADMIN_PASSWORD", "").strip()
    return value or None


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)


def _load_stored_record() -> PasswordRecord | None:
    path = _record_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        return PasswordRecord(
            salt=base64.b64decode(raw["salt"]),
            digest=base64.b64decode(raw["digest"]),
        )
    except Exception:
        return None


def _save_record(password: str) -> PasswordRecord:
    salt = secrets.token_bytes(16)
    digest = _hash_password(password, salt)
    path = _record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "algorithm": "pbkdf2-sha256",
        "rounds": PBKDF2_ROUNDS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "digest": base64.b64encode(digest).decode("ascii"),
    }, indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return PasswordRecord(salt=salt, digest=digest)


def _session_secret() -> bytes:
    path = _session_secret_path()
    try:
        value = path.read_bytes()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    value = secrets.token_bytes(48)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


def ensure_admin_password() -> str | None:
    """Ensure an admin credential and persistent session-signing key exist."""
    _session_secret()
    configured = _password_from_config()
    if configured:
        return None
    if _load_stored_record() is not None:
        return None
    generated = secrets.token_urlsafe(18)
    _save_record(generated)
    return generated


def verify_password(candidate: str) -> bool:
    configured = _password_from_config()
    if configured is not None:
        return hmac.compare_digest(candidate, configured)
    record = _load_stored_record()
    if record is None:
        return False
    digest = _hash_password(candidate, record.salt)
    return hmac.compare_digest(digest, record.digest)


def _client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return host or "unknown"


def _prune_failures(key: str, now: float) -> list[float]:
    recent = [ts for ts in _FAILURES.get(key, []) if now - ts < 15 * 60]
    _FAILURES[key] = recent
    return recent


def check_login_rate_limit(request: Request) -> None:
    now = time.time()
    key = _client_key(request)
    with _LOCK:
        recent = _prune_failures(key, now)
        if len(recent) >= 8:
            retry = int(max(1, 15 * 60 - (now - recent[0])))
            raise HTTPException(429, f"Too many failed admin unlock attempts. Try again in {retry} seconds.")


def record_failed_login(request: Request) -> None:
    now = time.time()
    key = _client_key(request)
    with _LOCK:
        recent = _prune_failures(key, now)
        recent.append(now)
        _FAILURES[key] = recent


def clear_failed_logins(request: Request) -> None:
    with _LOCK:
        _FAILURES.pop(_client_key(request), None)



def _credential_marker() -> str:
    configured = _password_from_config()
    if configured is not None:
        material = hashlib.sha256(configured.encode("utf-8")).digest()
    else:
        record = _load_stored_record()
        material = record.digest if record else b"missing"
    return hashlib.sha256(material).hexdigest()[:16]


def _sign(payload: str) -> str:
    digest = hmac.new(_session_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _make_token(expires: int, remembered: bool) -> str:
    payload = f"v1.{expires}.{1 if remembered else 0}.{_credential_marker()}.{secrets.token_urlsafe(18)}"
    return f"{payload}.{_sign(payload)}"


def _decode_token(token: str) -> tuple[int, bool] | None:
    try:
        parts = token.split(".")
        if len(parts) != 6 or parts[0] != "v1":
            return None
        payload = ".".join(parts[:5])
        if not hmac.compare_digest(parts[5], _sign(payload)):
            return None
        if not hmac.compare_digest(parts[3], _credential_marker()):
            return None
        expires = int(parts[1])
        remembered = parts[2] == "1"
        if expires <= int(time.time()):
            return None
        return expires, remembered
    except Exception:
        return None


def create_session(response: Response, remember: bool = True) -> int:
    seconds = REMEMBER_SECONDS if remember else settings.admin_session_minutes * 60
    expires = int(time.time()) + seconds
    token = _make_token(expires, remember)
    kwargs = dict(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="strict",
        path="/",
    )
    # A remembered device survives browser restarts.  A normal unlock uses a
    # session cookie and still carries a cryptographic expiry inside the token.
    if remember:
        kwargs["max_age"] = seconds
    response.set_cookie(**kwargs)
    return seconds


def destroy_session(request: Request, response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def session_status(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    decoded = _decode_token(token) if token else None
    if not decoded:
        return {"authenticated": False, "remembered": False, "expires_at": None}
    expires, remembered = decoded
    return {"authenticated": True, "remembered": remembered, "expires_at": expires}


def is_admin(request: Request) -> bool:
    return bool(session_status(request)["authenticated"])


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(401, "Admin unlock required")
