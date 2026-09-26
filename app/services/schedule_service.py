from __future__ import annotations

from datetime import datetime, time, timezone

from sqlalchemy.orm import Session

from ..models import Flight, Trip, LogbookEntry


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


REST_THRESHOLD_MINUTES = 10 * 60


def infer_scheduled_rests(flights: list[Flight]) -> None:
    """Infer rest stops from the schedule itself.

    If the gap from one leg's scheduled arrival to the next leg's scheduled
    departure is at least 10 hours, the destination of the first leg is treated
    as a rest stop.  This is intentionally based on explicit UTC datetimes so
    overnight and International Date Line trips need no special handling.
    """
    for flight in flights:
        flight.scheduled_rest_minutes = None
    for current, nxt in zip(flights, flights[1:]):
        arrival = _utc(current.scheduled_arrival_utc)
        departure = _utc(nxt.scheduled_departure_utc)
        if arrival is None or departure is None or departure <= arrival:
            continue
        gap_minutes = int((departure - arrival).total_seconds() // 60)
        if gap_minutes >= REST_THRESHOLD_MINUTES:
            current.scheduled_rest_minutes = gap_minutes


def chronology_key(f: Flight):
    """Sort the current schedule by schedule chronology, never insertion order."""
    dt = _utc(f.scheduled_departure_utc)
    if dt is None:
        dt = datetime.combine(f.flight_date, time.min, tzinfo=timezone.utc)
    return (dt, f.flight_date, f.id or 0)


def trip_flights(db: Session, trip: Trip) -> list[Flight]:
    # Query the database rather than trusting a possibly already-loaded ORM
    # relationship collection. This is important immediately after a manual add.
    return db.query(Flight).filter(Flight.trip_id == trip.id).all()


def resequence_trip(db: Session, trip: Trip) -> None:
    rows = trip_flights(db, trip)
    # v24 invariant: the user-controlled current schedule is defined only by
    # schedule_active. Provider data must never resurrect a removed leg.
    active = [f for f in rows if f.schedule_active]
    active.sort(key=chronology_key)
    for seq, flight in enumerate(active, start=1):
        flight.sequence = seq
    infer_scheduled_rests(active)
    active_ids = {f.id for f in active}
    for flight in rows:
        if flight.id not in active_ids:
            flight.sequence = 0
    if active:
        trip.start_date = min(f.flight_date for f in active)
        trip.end_date = max(f.flight_date for f in active)
    else:
        trip.start_date = None
        trip.end_date = None
    db.flush()


def clear_provider_tracking(flight: Flight) -> None:
    """Clear provider-derived state after an unflown schedule identity changes."""
    flight.provider_flight_id = None
    flight.provider_scheduled_departure_utc = None
    flight.provider_scheduled_arrival_utc = None
    flight.provider_departure_delay_seconds = None
    flight.provider_arrival_delay_seconds = None
    flight.estimated_departure_utc = None
    flight.estimated_arrival_utc = None
    flight.current_latitude = None
    flight.current_longitude = None
    flight.altitude_ft = None
    flight.groundspeed_kt = None
    flight.last_position_utc = None
    flight.last_provider_poll_utc = None
    flight.last_track_poll_utc = None
    flight.last_reassignment_search_utc = None
    flight.provider_track_fetched = False


def discard_tracking_data(db: Session, flight: Flight) -> None:
    """Discard provider/actual data for a leg the user removed from their trip.

    FlightAware reports whether the public flight operated; that is not proof the
    tracker owner actually travelled on it. In v24 ``schedule_active`` is the
    authoritative membership flag. Removing a leg therefore removes any provider
    observations that were attached to that schedule row, including the locally
    saved track and any auto-generated lifetime-logbook copy.
    """
    clear_provider_tracking(flight)
    flight.actual_departure_utc = None
    flight.actual_arrival_utc = None
    flight.status = "scheduled"
    flight.aircraft_type = None
    flight.registration = None
    flight.positions.clear()
    auto = (
        db.query(LogbookEntry)
        .filter(LogbookEntry.source_key == f"tracker:{flight.id}")
        .one_or_none()
    )
    if auto is not None:
        db.delete(auto)


def repair_legacy_schedule_state(db: Session) -> list[int]:
    """Normalize schedule state created by pre-v24 releases.

    Returns flight IDs whose optional weather archive should be deleted.

    Older releases could (1) seed an awarded baseline onto manually-created
    trips and (2) continue tracking a row after it had been removed because
    actual/provider timestamps were treated as schedule membership. Both
    behaviors are repaired here, once per startup and idempotently.
    """
    weather_cleanup: list[int] = []
    trips = db.query(Trip).all()
    for trip in trips:
        rows = trip_flights(db, trip)

        if trip.source == "manual":
            # Manual trips have no immutable PDF award. Some old migrations
            # accidentally populated awarded_* fields on them.
            for f in rows:
                f.awarded_sequence = None
                f.awarded_flight_number = None
                f.awarded_flight_date = None
                f.awarded_origin = None
                f.awarded_destination = None
                f.awarded_deadhead = None
                f.awarded_scheduled_departure_utc = None
                f.awarded_scheduled_arrival_utc = None

        for f in list(rows):
            if f.schedule_active:
                continue

            if (
                f.actual_departure_utc
                or f.actual_arrival_utc
                or f.provider_flight_id
                or f.positions
            ):
                discard_tracking_data(db, f)
                weather_cleanup.append(f.id)

            # Removed manual/replacement rows should not survive as tombstones.
            if trip.source == "manual" or (f.schedule_added and not f.awarded_flight_number):
                db.delete(f)

        db.flush()
        resequence_trip(db, trip)

    db.commit()
    return weather_cleanup


def snapshot_awarded(flight: Flight) -> None:
    """Capture the immutable PDF award once. Manual trips never acquire an awarded baseline."""
    if getattr(flight, "trip", None) is not None and flight.trip.source != "ups_pdf":
        return
    if flight.awarded_flight_number is not None or flight.schedule_added:
        return
    flight.awarded_sequence = flight.sequence
    flight.awarded_flight_number = flight.flight_number
    flight.awarded_flight_date = flight.flight_date
    flight.awarded_origin = flight.origin
    flight.awarded_destination = flight.destination
    flight.awarded_deadhead = flight.deadhead
    flight.awarded_scheduled_departure_utc = flight.scheduled_departure_utc
    flight.awarded_scheduled_arrival_utc = flight.scheduled_arrival_utc


def mark_added_after_award(flight: Flight) -> None:
    flight.schedule_added = True
    flight.schedule_active = True


def deactivate_from(db: Session, trip: Trip, flight: Flight, include_selected: bool = True) -> tuple[int, list[int]]:
    """Remove the selected leg and later legs from the user's current schedule.

    Provider data never makes a row undeletable. If a selected/later leg has
    FlightAware observations, those observations are discarded because the user
    is explicitly saying that leg is not part of their trip. PDF-awarded rows
    remain only as an inactive awarded baseline; manual and replacement rows are
    physically deleted.
    """
    cutoff = chronology_key(flight)
    count = 0
    weather_cleanup: list[int] = []
    for f in list(trip_flights(db, trip)):
        if not f.schedule_active:
            continue
        key = chronology_key(f)
        if key > cutoff or (include_selected and f.id == flight.id):
            if (
                f.actual_departure_utc
                or f.actual_arrival_utc
                or f.provider_flight_id
                or f.positions
            ):
                discard_tracking_data(db, f)
                weather_cleanup.append(f.id)

            if trip.source == "manual" or (f.schedule_added and not f.awarded_flight_number):
                db.delete(f)
            else:
                f.schedule_active = False
                f.sequence = 0
                f.schedule_change_note = "Removed from current schedule"
            count += 1

    db.flush()
    resequence_trip(db, trip)
    return count, weather_cleanup

