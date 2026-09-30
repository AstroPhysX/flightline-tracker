from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Flight, Trip
from .airport_resolver import ensure_airport
from .schedule_service import clear_provider_tracking, resequence_trip


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _norm_number(value: str) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _active_rows(db: Session, trip: Trip) -> list[Flight]:
    rows = db.query(Flight).filter(Flight.trip_id == trip.id, Flight.schedule_active.is_(True)).all()
    rows.sort(key=lambda f: (_utc(f.scheduled_departure_utc) or datetime.combine(f.flight_date, datetime.min.time(), tzinfo=timezone.utc), f.id))
    return rows


def _find_existing(db: Session, incoming: dict[str, Any]) -> Flight | None:
    fdate = incoming["flight_date"]
    origin = incoming["origin"]
    destination = incoming["destination"]
    number = _norm_number(incoming["flight_number"])
    rows = (
        db.query(Flight)
        .join(Trip, Flight.trip_id == Trip.id)
        .filter(Trip.active.is_(True), Flight.schedule_active.is_(True), Flight.flight_date == fdate)
        .all()
    )
    exact = [f for f in rows if _norm_number(f.flight_number) == number and f.origin == origin and f.destination == destination]
    if exact:
        return sorted(exact, key=lambda f: f.id)[0]
    route = [f for f in rows if f.origin == origin and f.destination == destination and not f.actual_departure_utc]
    return route[0] if len(route) == 1 else None


def _connection_trip(db: Session, dep: datetime, arr: datetime, origin: str, destination: str) -> Trip | None:
    best: tuple[float, Trip] | None = None
    for trip in db.query(Trip).filter(Trip.active.is_(True)).all():
        rows = _active_rows(db, trip)
        if not rows:
            continue
        first, last = rows[0], rows[-1]
        first_dep = _utc(first.scheduled_departure_utc)
        last_arr = _utc(last.scheduled_arrival_utc)

        # Positioning into a trip: jumpseat arrives where the first scheduled leg begins.
        if first_dep and destination == first.origin and arr <= first_dep:
            gap = (first_dep - arr).total_seconds()
            if 0 <= gap <= 48 * 3600 and (best is None or gap < best[0]):
                best = (gap, trip)

        # Positioning home after a trip: jumpseat leaves where the final scheduled leg ends.
        if last_arr and origin == last.destination and dep >= last_arr:
            gap = (dep - last_arr).total_seconds()
            if 0 <= gap <= 48 * 3600 and (best is None or gap < best[0]):
                best = (gap, trip)
    return best[1] if best else None


def apply_jumpseats(payload: dict[str, Any], db: Session) -> dict[str, int]:
    entries = payload.get("entries") or []
    if not isinstance(entries, list):
        raise HTTPException(400, "Jumpseat sync entries are invalid.")

    counts = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}
    incoming_keys: set[tuple] = set()
    touched: set[int] = set()
    hub = str(payload.get("hub_airport") or "").strip().upper()

    for raw in entries:
        if not isinstance(raw, dict):
            continue
        try:
            fdate = date.fromisoformat(str(raw.get("flight_date"))[:10])
            dep = datetime.fromisoformat(str(raw.get("scheduled_departure_utc")).replace("Z", "+00:00")).astimezone(timezone.utc)
            arr = datetime.fromisoformat(str(raw.get("scheduled_arrival_utc")).replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue
        number = _norm_number(raw.get("flight_number"))
        origin = str(raw.get("origin") or "").strip().upper()
        destination = str(raw.get("destination") or "").strip().upper()
        if not number or len(origin) < 3 or len(destination) < 3 or arr <= dep:
            continue
        if hub and origin != hub and destination != hub:
            continue
        ensure_airport(db, origin)
        ensure_airport(db, destination)
        key = (number, fdate, origin, destination)
        incoming_keys.add(key)

        row = _find_existing(db, {"flight_number": number, "flight_date": fdate, "origin": origin, "destination": destination})
        if row is not None:
            before = (_norm_number(row.flight_number), row.flight_date, row.origin, row.destination, bool(row.deadhead), _utc(row.scheduled_departure_utc), _utc(row.scheduled_arrival_utc))
            after = (number, fdate, origin, destination, True, dep, arr)
            if before == after:
                counts["unchanged"] += 1
                continue
            if not row.actual_departure_utc:
                clear_provider_tracking(row)
                row.flight_number = number
                row.flight_date = fdate
                row.origin = origin
                row.destination = destination
                row.scheduled_departure_utc = dep
                row.scheduled_arrival_utc = arr
            row.deadhead = True
            row.schedule_active = True
            row.schedule_change_note = "Updated by UPS Jumpseat sync"
            touched.add(row.trip_id)
            counts["updated"] += 1
            continue

        trip = _connection_trip(db, dep, arr, origin, destination)
        if trip is None:
            trip = Trip(
                name=f"UPS Jumpseat {origin}-{destination}",
                bid_period=None,
                line_number=None,
                ups_trip_id=None,
                start_date=fdate,
                end_date=fdate,
                active=True,
                source="jumpseat_sync",
            )
            db.add(trip)
            db.flush()
        existing = db.query(Flight).filter(Flight.trip_id == trip.id).all()
        row = Flight(
            trip_id=trip.id,
            sequence=max((f.sequence for f in existing), default=0) + 1,
            flight_number=number,
            flight_date=fdate,
            origin=origin,
            destination=destination,
            deadhead=True,
            scheduled_departure_utc=dep,
            scheduled_arrival_utc=arr,
            status="scheduled",
            schedule_active=True,
            schedule_added=True,
            schedule_change_note="Added by UPS Jumpseat sync",
        )
        db.add(row)
        db.flush()
        touched.add(trip.id)
        counts["added"] += 1

    # When the page is known to contain the complete confirmed list, remove only
    # future rows that were created by this jumpseat integration and disappeared.
    # Never remove an awarded/schedule-sync row merely because a jumpseat booking changed.
    if payload.get("complete_view") and hub:
        now = datetime.now(timezone.utc)
        candidates = (
            db.query(Flight)
            .join(Trip, Flight.trip_id == Trip.id)
            .filter(Trip.active.is_(True), Flight.schedule_active.is_(True), Flight.deadhead.is_(True))
            .all()
        )
        for row in candidates:
            if row.actual_departure_utc:
                continue
            note = str(row.schedule_change_note or "")
            if "UPS Jumpseat sync" not in note:
                continue
            dep = _utc(row.scheduled_departure_utc)
            if dep and dep < now - timedelta(hours=2):
                continue
            if row.origin != hub and row.destination != hub:
                continue
            key = (_norm_number(row.flight_number), row.flight_date, row.origin, row.destination)
            if key in incoming_keys:
                continue
            trip = row.trip
            if trip.source == "jumpseat_sync" or row.schedule_added:
                db.delete(row)
                touched.add(trip.id)
                counts["removed"] += 1

    db.flush()
    for trip_id in touched:
        trip = db.get(Trip, trip_id)
        if trip is not None:
            resequence_trip(db, trip)
    db.commit()
    return counts
