from __future__ import annotations

from datetime import datetime, timezone


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _delay_result(seconds: int | None, basis: str | None) -> dict | None:
    if seconds is None:
        return None
    minutes = int(round(float(seconds) / 60.0))
    if minutes > 0:
        flag = "delayed"
    elif minutes < 0:
        flag = "ahead"
    else:
        flag = "on_time"
    return {"flag": flag, "minutes": minutes, "basis": basis}


def timing_summary(flight) -> dict:
    """Return the status-card/history timing summary.

    Prefer FlightAware's own departure/arrival delay values when present. They
    are expressed in seconds and are what best mirrors the provider's status
    presentation. When those are unavailable, compare actual/estimated runway
    OFF/ON values to FlightAware's scheduled runway times, then fall back to the
    UPS/PDF schedule.

    Context rules:
      * completed -> arrival performance
      * airborne  -> expected/actual arrival performance when possible
      * upcoming  -> departure performance
    """
    actual_arr = _utc(getattr(flight, "actual_arrival_utc", None))
    actual_dep = _utc(getattr(flight, "actual_departure_utc", None))
    est_arr = _utc(getattr(flight, "estimated_arrival_utc", None))
    est_dep = _utc(getattr(flight, "estimated_departure_utc", None))

    provider_sched_arr = _utc(getattr(flight, "provider_scheduled_arrival_utc", None))
    provider_sched_dep = _utc(getattr(flight, "provider_scheduled_departure_utc", None))
    sched_arr = provider_sched_arr or _utc(getattr(flight, "scheduled_arrival_utc", None))
    sched_dep = provider_sched_dep or _utc(getattr(flight, "scheduled_departure_utc", None))

    provider_arr_delay = getattr(flight, "provider_arrival_delay_seconds", None)
    provider_dep_delay = getattr(flight, "provider_departure_delay_seconds", None)

    # Completed flight: arrival result is what people generally care about.
    if actual_arr:
        provider = _delay_result(provider_arr_delay, "arrival")
        if provider:
            return provider
        if sched_arr:
            minutes = int(round((actual_arr - sched_arr).total_seconds() / 60.0))
            return _minutes_result(minutes, "arrival")

    # Airborne: show expected arrival performance, not stale departure delay.
    if actual_dep and not actual_arr:
        provider = _delay_result(provider_arr_delay, "arrival")
        if provider:
            return provider
        if est_arr and sched_arr:
            minutes = int(round((est_arr - sched_arr).total_seconds() / 60.0))
            return _minutes_result(minutes, "arrival")
        provider = _delay_result(provider_dep_delay, "departure")
        if provider:
            return provider
        if sched_dep:
            minutes = int(round((actual_dep - sched_dep).total_seconds() / 60.0))
            return _minutes_result(minutes, "departure")

    # Upcoming: departure delay/status.
    provider = _delay_result(provider_dep_delay, "departure")
    if provider:
        return provider
    if est_dep and sched_dep:
        minutes = int(round((est_dep - sched_dep).total_seconds() / 60.0))
        return _minutes_result(minutes, "departure")

    # Last fallback for partially-populated historical data.
    if est_arr and sched_arr:
        minutes = int(round((est_arr - sched_arr).total_seconds() / 60.0))
        return _minutes_result(minutes, "arrival")

    return {"flag": "unknown", "minutes": None, "basis": None}


def _minutes_result(minutes: int, basis: str) -> dict:
    if minutes > 0:
        flag = "delayed"
    elif minutes < 0:
        flag = "ahead"
    else:
        flag = "on_time"
    return {"flag": flag, "minutes": minutes, "basis": basis}
