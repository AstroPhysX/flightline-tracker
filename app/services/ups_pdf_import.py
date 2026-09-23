from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Flight, Trip
from .airport_resolver import ensure_airport
from .ups_pdf_tools import build_awarded_line
from .schedule_service import resequence_trip, snapshot_awarded


def _utc(value: str | None):
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def import_awarded_line_pdfs(
    db: Session,
    lines_pdf_path: str | Path,
    trips_pdf_path: str | Path,
    line_number: int,
) -> list[Trip]:
    parsed = build_awarded_line(lines_pdf_path, trips_pdf_path, line_number)
    bid_period = parsed["bid_period"]
    line_number = int(parsed["line_number"])

    # Re-importing the same award replaces only that award's PDF-derived trips.
    old = (
        db.query(Trip)
        .filter(
            Trip.source == "ups_pdf",
            Trip.bid_period == bid_period,
            Trip.line_number == line_number,
        )
        .all()
    )
    for trip in old:
        db.delete(trip)
    db.flush()

    imported: list[Trip] = []
    for assignment in parsed["assignments"]:
        records = assignment.get("flights") or []
        if not records:
            continue

        for rec in records:
            ensure_airport(db, rec["departure"])
            ensure_airport(db, rec["arrival"])

        start_dt = _utc(assignment.get("trip_start") or records[0].get("start_datetime"))
        end_dt = _utc(assignment.get("trip_end") or records[-1].get("end_datetime"))
        ups_trip_id = int(assignment["trip_id"])
        trip = Trip(
            name=f"BP {bid_period} · Line {line_number} · Trip {ups_trip_id}",
            bid_period=bid_period,
            line_number=line_number,
            ups_trip_id=ups_trip_id,
            start_date=start_dt.date() if start_dt else None,
            end_date=end_dt.date() if end_dt else None,
            active=True,
            source="ups_pdf",
        )
        db.add(trip)
        db.flush()

        seq = 1
        for rec in records:
            # BUS legs are useful itinerary context but are not flight-trackable.
            flight = Flight(
                trip_id=trip.id,
                sequence=seq,
                flight_number=rec["flight_number"],
                flight_date=_utc(rec.get("start_datetime")).date(),
                origin=rec["departure"],
                destination=rec["arrival"],
                deadhead=bool(rec.get("deadhead")),
                scheduled_departure_utc=_utc(rec.get("start_datetime")),
                scheduled_arrival_utc=_utc(rec.get("end_datetime")),
                scheduled_rest_minutes=rec.get("rest_minutes"),
                status="scheduled",
                notes="BUS" if rec.get("is_bus") else None,
            )
            db.add(flight)
            db.flush()
            snapshot_awarded(flight)
            seq += 1

        resequence_trip(db, trip)
        imported.append(trip)

    db.commit()
    return imported
