from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from ..config import settings

MAX_HISTORY_FILES = 20


def _dir() -> Path:
    path = settings.app_data_dir / "schedule_sync"
    path.mkdir(parents=True, exist_ok=True)
    return path


def enabled() -> bool:
    return bool((settings.schedule_sync_token or "").strip())


def require_sync_token(request: Request) -> None:
    expected = (settings.schedule_sync_token or "").strip()
    if not expected:
        raise HTTPException(503, "Schedule sync endpoint is disabled. Configure SCHEDULE_SYNC_TOKEN first.")

    auth = request.headers.get("authorization", "")
    scheme, _, supplied = auth.partition(" ")
    if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(supplied.strip(), expected):
        raise HTTPException(401, "Invalid schedule sync token", headers={"WWW-Authenticate": "Bearer"})


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def store(payload: dict[str, Any]) -> dict[str, Any]:
    """Store a schedule snapshot without applying it to the live itinerary.

    This is intentionally an inbox/staging layer for a future browser extension.
    It stores normalized schedule data only; no UPS cookies, credentials, HTML,
    or MFA material should ever be submitted here.
    """
    sync_dir = _dir()
    canonical = _canonical_bytes(payload)
    digest = hashlib.sha256(canonical).hexdigest()
    now = datetime.now(timezone.utc)

    latest_path = sync_dir / "latest.json"
    previous = _load_json(latest_path)
    previous_digest = previous.get("sha256") if isinstance(previous, dict) else None
    changed = previous_digest != digest

    envelope = {
        "received_at": now.isoformat(),
        "sha256": digest,
        "changed": changed,
        "payload": payload,
    }

    tmp = sync_dir / ".latest.json.tmp"
    tmp.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(latest_path)

    if changed:
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        history_path = sync_dir / f"sync-{stamp}-{digest[:10]}.json"
        history_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(history_path, 0o600)
        history = sorted(sync_dir.glob("sync-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in history[MAX_HISTORY_FILES:]:
            try:
                old.unlink()
            except OSError:
                pass

    return {
        "ok": True,
        "accepted": True,
        "changed": changed,
        "sha256": digest,
        "received_at": envelope["received_at"],
        "applied": False,
        "message": "Schedule snapshot staged for future compare/apply support.",
    }


def status() -> dict[str, Any]:
    latest = _load_json(_dir() / "latest.json")
    payload = latest.get("payload") if isinstance(latest, dict) else None
    trips = payload.get("trips") if isinstance(payload, dict) else None
    return {
        "enabled": enabled(),
        "has_pending_snapshot": bool(latest),
        "received_at": latest.get("received_at") if isinstance(latest, dict) else None,
        "sha256": latest.get("sha256") if isinstance(latest, dict) else None,
        "source": payload.get("source") if isinstance(payload, dict) else None,
        "captured_at": payload.get("captured_at") if isinstance(payload, dict) else None,
        "trip_count": len(trips) if isinstance(trips, list) else 0,
        "auto_apply": False,
    }


def latest() -> dict[str, Any] | None:
    return _load_json(_dir() / "latest.json")
