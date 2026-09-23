from __future__ import annotations

from datetime import datetime, timezone


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def timing_summary(flight) -> dict:
    """Return a simple schedule-performance summary for UI/history.

    Completed legs use arrival performance when available. Airborne/upcoming
    legs use departure performance. This avoids pretending an estimated arrival
    was an observed historical result.
    """
    actual_arr = _utc(getattr(flight, "actual_arrival_utc", None))
    sched_arr = _utc(getattr(flight, "scheduled_arrival_utc", None))
    est_arr = _utc(getattr(flight, "estimated_arrival_utc", None))
    actual_dep = _utc(getattr(flight, "actual_departure_utc", None))
    sched_dep = _utc(getattr(flight, "scheduled_departure_utc", None))
    est_dep = _utc(getattr(flight, "estimated_departure_utc", None))

    basis = None
    scheduled = None
    effective = None
    if actual_arr and sched_arr:
        basis, scheduled, effective = "arrival", sched_arr, actual_arr
    elif actual_dep and sched_dep:
        basis, scheduled, effective = "departure", sched_dep, actual_dep
    elif est_dep and sched_dep:
        basis, scheduled, effective = "departure", sched_dep, est_dep
    elif est_arr and sched_arr:
        basis, scheduled, effective = "arrival", sched_arr, est_arr

    if not scheduled or not effective:
        return {"flag": "unknown", "minutes": None, "basis": basis}

    minutes = int(round((effective - scheduled).total_seconds() / 60.0))
    if minutes > 0:
        flag = "delayed"
    elif minutes < 0:
        flag = "ahead"
    else:
        flag = "on_time"
    return {"flag": flag, "minutes": minutes, "basis": basis}
