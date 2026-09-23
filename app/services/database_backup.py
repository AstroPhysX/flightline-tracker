from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings


def backup_database(*, keep: int = 10) -> Path | None:
    """Create a small rotating safety copy before app startup/migrations.

    The primary database remains /data/tracker.db. Backups live in /data/backups,
    so both survive container/image replacement when /data is bind-mounted.
    """
    source = settings.app_data_dir / "tracker.db"
    if not source.exists() or source.stat().st_size == 0:
        return None
    backup_dir = settings.app_data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"tracker-{stamp}.db"
    shutil.copy2(source, target)
    backups = sorted(backup_dir.glob("tracker-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[max(1, int(keep)):]:
        try:
            old.unlink()
        except OSError:
            pass
    return target
