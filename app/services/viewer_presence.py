from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock

_LOCK = Lock()
_VIEWERS: dict[str, datetime] = {}
ACTIVE_FOR = timedelta(seconds=90)


def heartbeat(viewer_id: str) -> int:
    viewer_id = (viewer_id or "").strip()[:160]
    if not viewer_id:
        return count_active()
    now = datetime.now(timezone.utc)
    with _LOCK:
        _VIEWERS[viewer_id] = now
        _prune_locked(now)
        return len(_VIEWERS)


def _prune_locked(now: datetime) -> None:
    stale = [key for key, seen in _VIEWERS.items() if now - seen > ACTIVE_FOR]
    for key in stale:
        _VIEWERS.pop(key, None)


def count_active() -> int:
    now = datetime.now(timezone.utc)
    with _LOCK:
        _prune_locked(now)
        return len(_VIEWERS)


def has_active_viewers() -> bool:
    return count_active() > 0
