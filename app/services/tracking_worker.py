from __future__ import annotations

import threading
import time
from datetime import datetime, time as dt_time, timedelta, timezone

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Flight, Trip
from . import tracker_settings, viewer_presence
from .aeroapi import AeroApiError, sync_flight
from .logbook_service import sync_completed_flight_to_logbook
from .weather_archive import archive_for_flight

_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_RUN_LOCK = threading.Lock()
_VIEWER_KICK_LOCK = threading.Lock()
_LAST_VIEWER_KICK_UTC: datetime | None = None
VIEWER_KICK_COOLDOWN = timedelta(minutes=10)


def _utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _due_candidates(db: Session, now: datetime, poll_seconds: int, *, force: bool = False) -> list[Flight]:
    flights = (
        db.query(Flight)
        .join(Trip, Flight.trip_id == Trip.id)
        .filter(
            Flight.schedule_active.is_(True),
            Trip.active.is_(True),
            Flight.actual_arrival_utc.is_(None),
        )
        .order_by(Flight.flight_date.asc(), Flight.sequence.asc())
        .all()
    )
    eligible: list[Flight] = []
    for f in flights:
        if f.flight_number.upper() == "BUS":
            continue
        last_poll = _utc(f.last_provider_poll_utc)
        if not force and last_poll and (now - last_poll).total_seconds() < poll_seconds:
            continue

        sched = _utc(f.scheduled_departure_utc)
        if f.actual_departure_utc:
            eligible.append(f)
            continue
        if sched:
            # Start watching before departure and keep looking for a long IROPS
            # delay rather than abandoning the scheduled flight too early.
            if sched - timedelta(hours=6) <= now <= sched + timedelta(hours=36):
                eligible.append(f)
        else:
            day_start = datetime.combine(f.flight_date, dt_time.min, tzinfo=timezone.utc)
            if day_start - timedelta(hours=6) <= now <= day_start + timedelta(days=2):
                eligible.append(f)

    airborne = [f for f in eligible if f.actual_departure_utc]
    if airborne:
        return airborne[:1]
    # Normally only the immediate next leg needs a paid query.
    return eligible[:1]


def _run_once_unlocked(*, force: bool = False, force_live_position: bool = False) -> dict:
    cfg = tracker_settings.load()
    provider = str(cfg.get("provider") or "disabled")
    if provider != "aeroapi":
        return {"provider": provider, "polled": 0}
    key = tracker_settings.key_for("aeroapi", cfg)
    if not key:
        return {"provider": provider, "polled": 0, "error": "No API key configured"}

    configured_poll_seconds = max(60, int(cfg.get("poll_seconds", 600)))
    active_viewers = viewer_presence.count_active()
    # With no live map viewers, keep status/delay data reasonably fresh but
    # avoid spending money on frequent polling. Dedicated position calls are
    # disabled entirely until a viewer returns.
    poll_seconds = configured_poll_seconds if active_viewers else max(1800, configured_poll_seconds)
    budget = max(0.0, float(cfg.get("monthly_budget_usd", 4.5)))
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        candidates = _due_candidates(db, now, poll_seconds, force=force)
        results = []
        for flight in candidates:
            try:
                # Re-check membership immediately before any paid provider call.
                # A schedule edit in another request may have removed this leg
                # after the candidate list was built.
                db.refresh(flight)
                if not flight.schedule_active:
                    continue
                result = sync_flight(key, db, flight, budget, live_viewers=active_viewers > 0, force_live_position=force_live_position)
                # Completed operating flights automatically become part of the
                # lifetime logbook map. Later Logbook Pro imports supersede the
                # automatic copy rather than duplicating it.
                refreshed = db.get(Flight, flight.id)
                if refreshed is not None:
                    db.refresh(refreshed)
                if refreshed is not None and not refreshed.schedule_active:
                    continue
                # Replay weather is deliberately tiny: exactly three local
                # snapshots per flight (begin/middle/end). It is independent of
                # viewer presence so replay still has context if nobody watched live.
                if refreshed and refreshed.actual_departure_utc:
                    archive_for_flight(refreshed)
                if refreshed and refreshed.actual_arrival_utc and not refreshed.deadhead:
                    sync_completed_flight_to_logbook(db, refreshed)
                results.append(result)
            except AeroApiError as exc:
                db.rollback()
                # Stamp the poll time after non-budget API failures to prevent a
                # tight retry loop. Budget failures are naturally checked again
                # but no paid call is made.
                if "budget guard" not in str(exc).lower():
                    flight = db.get(Flight, flight.id)
                    if flight:
                        flight.last_provider_poll_utc = now
                        db.commit()
                results.append({"flight_id": flight.id, "error": str(exc)})
        return {"provider": provider, "polled": len(candidates), "active_viewers": active_viewers, "results": results}
    finally:
        db.close()


def run_once(*, force: bool = False, force_live_position: bool = False) -> dict:
    # Serialize background/manual syncs so duplicate paid API calls cannot run
    # at the same time.
    if not _RUN_LOCK.acquire(blocking=False):
        return {"provider": "busy", "polled": 0, "skipped": "tracking sync already running"}
    try:
        return _run_once_unlocked(force=force, force_live_position=force_live_position)
    finally:
        _RUN_LOCK.release()


def kick_for_viewer() -> bool:
    """Request one fresh provider sync when viewers return.

    This is intentionally edge-triggered by the heartbeat endpoint and guarded
    by a global 10-minute cooldown. A browser refresh therefore cannot create a
    burst of paid calls. Once a viewer remains present, the normal configured
    polling cadence (10 minutes by default) takes over.
    """
    global _LAST_VIEWER_KICK_UTC
    now = datetime.now(timezone.utc)
    with _VIEWER_KICK_LOCK:
        if _LAST_VIEWER_KICK_UTC and now - _LAST_VIEWER_KICK_UTC < VIEWER_KICK_COOLDOWN:
            return False

        # Also respect the real provider-poll timestamp. If the worker already
        # refreshed the current/next flight less than 10 minutes ago, a page
        # open or refresh should reuse that fresh data instead of paying again.
        db = SessionLocal()
        try:
            if not _due_candidates(db, now, int(VIEWER_KICK_COOLDOWN.total_seconds()), force=False):
                return False
        finally:
            db.close()

        _LAST_VIEWER_KICK_UTC = now

    def _kick() -> None:
        try:
            # Force one current status lookup. If airborne, also force the
            # dedicated current-position resource so the newly opened map gets
            # the freshest available aircraft position.
            run_once(force=True, force_live_position=True)
        except Exception:
            pass

    threading.Thread(target=_kick, name="viewer-tracking-kick", daemon=True).start()
    return True


def _loop() -> None:
    while not _STOP.is_set():
        try:
            run_once()
        except Exception:
            # Never let a tracking-provider problem take down the family web UI.
            pass
        _STOP.wait(20)


def start() -> None:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="flight-tracking-worker", daemon=True)
    _THREAD.start()


def stop() -> None:
    _STOP.set()
    if _THREAD and _THREAD.is_alive():
        _THREAD.join(timeout=2.0)
