from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import Airport, Flight, Trip
from .flight_timing import timing_summary

REST_RELEASE_BUFFER_MINUTES = 45


def _iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _effective_status_values(actual_arrival, actual_departure, status, scheduled_arrival, flight_date, now):
    if actual_arrival:
        return "completed"
    if actual_departure:
        return "current"
    if status in {"cancelled", "diverted"}:
        return status
    if scheduled_arrival is not None:
        dt = scheduled_arrival
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        if dt < now:
            return "past"
    elif flight_date and flight_date < now.date():
        return "past"
    return "scheduled"


def _effective_status(f: Flight, now: datetime) -> str:
    return _effective_status_values(
        f.actual_arrival_utc,
        f.actual_departure_utc,
        f.status,
        f.scheduled_arrival_utc,
        f.flight_date,
        now,
    )


def select_trip(db: Session, trip_id: int | None = None) -> Trip | None:
    if trip_id is not None:
        return db.get(Trip, int(trip_id))

    trips = db.query(Trip).filter(Trip.active.is_(True)).order_by(Trip.start_date.asc(), Trip.id.asc()).all()
    if not trips:
        return None

    today = datetime.now(timezone.utc).date()
    current = [t for t in trips if t.start_date and t.end_date and t.start_date <= today <= t.end_date]
    if current:
        return max(current, key=lambda t: t.id)
    future = [t for t in trips if t.start_date and t.start_date > today]
    if future:
        return min(future, key=lambda t: (t.start_date, t.id))
    dated = [t for t in trips if t.end_date]
    if dated:
        return max(dated, key=lambda t: (t.end_date, t.id))
    return max(trips, key=lambda t: t.id)


def _current_rows(trip: Trip):
    # A deactivated flight that actually operated remains visible in the
    # "Current / actually flown" history. A deactivated future plan does not.
    rows = [f for f in trip.flights if f.schedule_active or f.actual_departure_utc or f.actual_arrival_utc]
    return sorted(rows, key=lambda f: (f.sequence if f.sequence > 0 else 9999, f.flight_date, f.id))


def _awarded_rows(trip: Trip):
    rows = [f for f in trip.flights if f.awarded_flight_number]
    return sorted(rows, key=lambda f: (f.awarded_sequence or 9999, f.awarded_flight_date or f.flight_date, f.id))


def build_dashboard(db: Session, trip_id: int | None = None, view: str = "current", public_delay_minutes: int = 0):
    now = datetime.now(timezone.utc)
    trip = select_trip(db, trip_id)
    view = "awarded" if view == "awarded" else "current"
    if not trip:
        return {
            "trip": None,
            "flights": [],
            "status": {"state": "empty"},
            "diagnostics": {"missing_airports": []},
            "view": view,
        }

    source_rows = _awarded_rows(trip) if view == "awarded" else _current_rows(trip)
    airport_codes = set()
    for f in source_rows:
        if view == "awarded":
            if f.awarded_origin: airport_codes.add(f.awarded_origin)
            if f.awarded_destination: airport_codes.add(f.awarded_destination)
        else:
            airport_codes.add(f.origin)
            airport_codes.add(f.destination)
    airports = {a.code: a for a in db.query(Airport).filter(Airport.code.in_(airport_codes)).all()} if airport_codes else {}

    flights = []
    missing_airports: set[str] = set()
    for f in source_rows:
        if view == "awarded":
            sequence = f.awarded_sequence or f.sequence
            flight_number = f.awarded_flight_number or f.flight_number
            flight_date = f.awarded_flight_date or f.flight_date
            origin_code = f.awarded_origin or f.origin
            destination_code = f.awarded_destination or f.destination
            deadhead = bool(f.awarded_deadhead)
            sched_dep = f.awarded_scheduled_departure_utc
            sched_arr = f.awarded_scheduled_arrival_utc
            status = _effective_status_values(None, None, "scheduled", sched_arr, flight_date, now)
            track = []
            actual_dep = actual_arr = est_dep = est_arr = None
            latitude = longitude = altitude = speed = last_pos = None
            aircraft_type = registration = None
        else:
            sequence = f.sequence
            flight_number = f.flight_number
            flight_date = f.flight_date
            origin_code = f.origin
            destination_code = f.destination
            deadhead = f.deadhead
            sched_dep = f.scheduled_departure_utc
            sched_arr = f.scheduled_arrival_utc
            status = _effective_status(f, now)
            visible_positions = list(f.positions)
            if status == "current" and public_delay_minutes > 0:
                cutoff = now - timedelta(minutes=public_delay_minutes)
                visible_positions = [
                    p for p in visible_positions
                    if (p.captured_utc.replace(tzinfo=timezone.utc) if p.captured_utc.tzinfo is None else p.captured_utc.astimezone(timezone.utc)) <= cutoff
                ]
            track = [{
                "lat": p.latitude,
                "lon": p.longitude,
                "altitude_ft": p.altitude_ft,
                "groundspeed_kt": p.groundspeed_kt,
                "captured_utc": _iso(p.captured_utc),
            } for p in visible_positions]
            actual_dep, actual_arr = f.actual_departure_utc, f.actual_arrival_utc
            est_dep, est_arr = f.estimated_departure_utc, f.estimated_arrival_utc
            if status == "current" and public_delay_minutes > 0:
                if visible_positions:
                    last_visible = visible_positions[-1]
                    latitude, longitude = last_visible.latitude, last_visible.longitude
                    altitude, speed, last_pos = last_visible.altitude_ft, last_visible.groundspeed_kt, last_visible.captured_utc
                else:
                    latitude = longitude = altitude = speed = last_pos = None
            else:
                latitude, longitude = f.current_latitude, f.current_longitude
                altitude, speed, last_pos = f.altitude_ft, f.groundspeed_kt, f.last_position_utc
            aircraft_type, registration = f.aircraft_type, f.registration

        origin = airports.get(origin_code)
        destination = airports.get(destination_code)
        if origin is None: missing_airports.add(origin_code)
        if destination is None: missing_airports.add(destination_code)

        flights.append({
            "id": f.id,
            "sequence": sequence,
            "flight_number": flight_number,
            "date": flight_date.isoformat(),
            "origin": origin_code,
            "destination": destination_code,
            "deadhead": deadhead,
            "status": status,
            "scheduled_departure_utc": _iso(sched_dep),
            "scheduled_arrival_utc": _iso(sched_arr),
            "estimated_departure_utc": _iso(est_dep),
            "estimated_arrival_utc": _iso(est_arr),
            "actual_departure_utc": _iso(actual_dep),
            "actual_arrival_utc": _iso(actual_arr),
            "scheduled_rest_minutes": f.scheduled_rest_minutes,
            "aircraft_type": aircraft_type,
            "registration": registration,
            "latitude": latitude,
            "longitude": longitude,
            "altitude_ft": altitude,
            "groundspeed_kt": speed,
            "last_position_utc": _iso(last_pos),
            "track": track,
            "schedule_active": f.schedule_active,
            "schedule_added": f.schedule_added,
            "schedule_changed": bool(
                f.awarded_flight_number and (
                    f.flight_number != f.awarded_flight_number or
                    f.flight_date != f.awarded_flight_date or
                    f.origin != f.awarded_origin or
                    f.destination != f.awarded_destination or
                    bool(f.deadhead) != bool(f.awarded_deadhead) or
                    f.scheduled_departure_utc != f.awarded_scheduled_departure_utc or
                    f.scheduled_arrival_utc != f.awarded_scheduled_arrival_utc or
                    not f.schedule_active
                )
            ),
            "schedule_change_note": f.schedule_change_note,
            "timing": timing_summary(f) if view == "current" else {"flag": "unknown", "minutes": None, "basis": None},
            "origin_airport": None if not origin else {
                "lat": origin.latitude, "lon": origin.longitude, "tz": origin.timezone_name,
                "name": origin.name, "city": origin.city, "code": origin.code,
            },
            "destination_airport": None if not destination else {
                "lat": destination.latitude, "lon": destination.longitude, "tz": destination.timezone_name,
                "name": destination.name, "city": destination.city, "code": destination.code,
            },
        })

    current = next((f for f in flights if f["status"] == "current"), None)
    past_or_completed = [f for f in flights if f["status"] in {"completed", "past"}]
    last_completed = past_or_completed[-1] if past_or_completed else None
    next_flight = next((f for f in flights if f["status"] == "scheduled"), None)

    if current:
        local_tz = (current.get("destination_airport") or {}).get("tz")
        local_label = current["destination"]
        state = "airborne"
    elif last_completed:
        local_tz = (last_completed.get("destination_airport") or {}).get("tz")
        local_label = last_completed["destination"]
        state = "ground"
    elif next_flight:
        local_tz = (next_flight.get("origin_airport") or {}).get("tz")
        local_label = next_flight["origin"]
        state = "pretrip"
    else:
        local_tz = None
        local_label = None
        state = "complete"

    rest_start = None
    if view == "current" and not current and last_completed and last_completed.get("actual_arrival_utc"):
        arrival = datetime.fromisoformat(last_completed["actual_arrival_utc"])
        rest_start = arrival + timedelta(minutes=REST_RELEASE_BUFFER_MINUTES)

    has_awarded = any(f.awarded_flight_number for f in trip.flights)
    has_changes = any(
        f.schedule_added or not f.schedule_active or (
            f.awarded_flight_number and (
                f.flight_number != f.awarded_flight_number or f.flight_date != f.awarded_flight_date or
                f.origin != f.awarded_origin or f.destination != f.awarded_destination or
                bool(f.deadhead) != bool(f.awarded_deadhead) or
                f.scheduled_departure_utc != f.awarded_scheduled_departure_utc or
                f.scheduled_arrival_utc != f.awarded_scheduled_arrival_utc
            )
        ) for f in trip.flights
    )

    return {
        "trip": {
            "id": trip.id, "name": trip.name, "bid_period": trip.bid_period,
            "line_number": trip.line_number, "ups_trip_id": trip.ups_trip_id,
            "start_date": trip.start_date.isoformat() if trip.start_date else None,
            "end_date": trip.end_date.isoformat() if trip.end_date else None,
            "source": trip.source, "historical_view": trip_id is not None,
            "has_awarded_view": has_awarded, "has_schedule_changes": has_changes,
        },
        "flights": flights,
        "status": {
            "state": state, "local_timezone": local_tz or "UTC", "local_label": local_label,
            "rest_start_utc": _iso(rest_start), "rest_release_buffer_minutes": REST_RELEASE_BUFFER_MINUTES,
            "last_completed_flight_id": last_completed["id"] if last_completed else None,
            "next_flight_id": next_flight["id"] if next_flight else None,
            "position_delay_minutes": int(public_delay_minutes),
        },
        "diagnostics": {"missing_airports": sorted(missing_airports)},
        "view": view,
    }
