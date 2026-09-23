from __future__ import annotations

from datetime import datetime, time, timezone

from sqlalchemy.orm import Session

from ..models import Flight, Trip


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
    active = [f for f in rows if f.schedule_active or f.actual_departure_utc or f.actual_arrival_utc]
    active.sort(key=chronology_key)
    for seq, flight in enumerate(active, start=1):
        flight.sequence = seq
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


def deactivate_from(db: Session, trip: Trip, flight: Flight, include_selected: bool = True) -> int:
    """Remove the selected unflown leg and later unflown legs from the current schedule.

    Awarded rows are retained internally (inactive) so the immutable awarded view can
    still be restored. Purely manual/replacement rows are physically deleted so the
    schedule editor never accumulates tombstones.
    """
    cutoff = chronology_key(flight)
    count = 0
    for f in list(trip_flights(db, trip)):
        if not f.schedule_active:
            continue
        key = chronology_key(f)
        if key > cutoff or (include_selected and f.id == flight.id):
            if f.actual_departure_utc or f.actual_arrival_utc:
                continue
            if (f.schedule_added or trip.source == "manual") and not f.awarded_flight_number:
                db.delete(f)
            else:
                f.schedule_active = False
                f.schedule_change_note = "Removed during schedule rebuild"
            count += 1
    db.flush()
    resequence_trip(db, trip)
    return count
