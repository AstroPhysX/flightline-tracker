from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Flight, Trip
from .airport_resolver import ensure_airport
from .schedule_service import clear_provider_tracking, resequence_trip, snapshot_awarded

MAX_HISTORY_FILES = 20


def _dir() -> Path:
    path = settings.app_data_dir / "schedule_sync"
    path.mkdir(parents=True, exist_ok=True)
    return path


def enabled() -> bool:
    return bool((settings.schedule_sync_token or "").strip())


def require_sync_token(request: Request) -> None:
    expected = (settings.schedule_sync_token or "").strip()
    if not expected:
        raise HTTPException(503, "Schedule sync endpoint is disabled. Configure SCHEDULE_SYNC_TOKEN first.")

    auth = request.headers.get("authorization", "")
    scheme, _, supplied = auth.partition(" ")
    if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(supplied.strip(), expected):
        raise HTTPException(401, "Invalid schedule sync token", headers={"WWW-Authenticate": "Bearer"})


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_flight_number(number: str | None) -> str:
    value = (number or "").strip().upper().replace(" ", "")
    if value.startswith("UPS"):
        return value
    # UPS Work Schedule commonly omits the carrier prefix. Preserve explicit
    # carrier codes on commercial deadheads, but normalize numeric UPS flights
    # to the ident format expected by AeroAPI and the rest of the tracker.
    simple = value.replace("-", "")
    if simple.isdigit() and value:
        return f"UPS{value}"
    return value


def _flight_identity(number: str | None, flight_date: date | None, origin: str | None, destination: str | None) -> tuple:
    return (
        _normalize_flight_number(number),
        flight_date,
        (origin or "").strip().upper(),
        (destination or "").strip().upper(),
    )


def _overlap_days(a_start: date | None, a_end: date | None, b_start: date | None, b_end: date | None) -> int:
    if not all((a_start, a_end, b_start, b_end)):
        return 0
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    return max(0, (end - start).days + 1)


def _trip_for_external_id(
    db: Session,
    payload: dict[str, Any],
    trip_payload: dict[str, Any],
    claimed_trip_ids: set[int],
) -> Trip:
    work_period = str(payload.get("bid_period") or "").strip() or None
    ext = str(trip_payload.get("external_id") or "").strip()
    numeric_id = int(ext) if ext.isdigit() else None
    incoming_start = _parse_date(trip_payload.get("start_date"))
    incoming_end = _parse_date(trip_payload.get("end_date"))

    active = db.query(Trip).filter(Trip.active.is_(True)).all()
    available = [t for t in active if t.id not in claimed_trip_ids]

    # Sometimes the Work Schedule pairing identifier matches the bid-package
    # trip ID; when it does, this is the strongest match. Do not assume the Work
    # Schedule pay-period number equals the bid-package period number.
    if numeric_id is not None:
        candidates = [t for t in available if t.ups_trip_id == numeric_id]
        if candidates:
            return sorted(candidates, key=lambda t: (t.source != "ups_pdf", t.id))[0]

    # Otherwise map a revised pairing to the existing awarded trip by date
    # overlap. This handles a reroute/reassignment whose Work Schedule pairing
    # number is unrelated to the original bid-package trip ID.
    overlap = [
        (_overlap_days(incoming_start, incoming_end, t.start_date, t.end_date), t)
        for t in available
        if t.source in {"ups_pdf", "ups_sync"}
    ]
    overlap = [(days, t) for days, t in overlap if days > 0]
    if overlap:
        overlap.sort(key=lambda item: (-item[0], item[1].source != "ups_pdf", item[1].id))
        return overlap[0][1]

    sync_name = f"UPS WS PP {work_period or '?'} · Pair {ext or 'Unassigned'}"
    for trip in available:
        if trip.source == "ups_sync" and trip.name == sync_name:
            return trip

    trip = Trip(
        name=sync_name,
        bid_period=None,
        ups_trip_id=numeric_id,
        start_date=incoming_start,
        end_date=incoming_end,
        active=True,
        source="ups_sync",
    )
    db.add(trip)
    db.flush()
    return trip


def _find_match(rows: list[Flight], incoming: dict[str, Any]) -> Flight | None:
    flight_date = _parse_date(incoming.get("flight_date"))
    number = _normalize_flight_number(incoming.get("flight_number"))
    origin = str(incoming.get("origin") or "").strip().upper()
    destination = str(incoming.get("destination") or "").strip().upper()

    # First choice: exact operational identity.
    exact = [f for f in rows if _flight_identity(f.flight_number, f.flight_date, f.origin, f.destination) == (number, flight_date, origin, destination)]
    if exact:
        return sorted(exact, key=lambda f: (not f.schedule_active, f.id))[0]

    # A schedule revision commonly keeps the date/route while changing the
    # flight number. Matching this way avoids creating a duplicate replacement.
    route_day = [f for f in rows if f.flight_date == flight_date and f.origin == origin and f.destination == destination and not f.actual_departure_utc]
    if len(route_day) == 1:
        return route_day[0]

    # Or the flight number/date stays the same while the route/times change.
    number_day = [f for f in rows if f.flight_date == flight_date and _normalize_flight_number(f.flight_number) == number and not f.actual_departure_utc]
    if len(number_day) == 1:
        return number_day[0]

    return None


def _apply_payload(payload: dict[str, Any], db: Session) -> dict[str, int]:
    coverage_start = _parse_date(payload.get("coverage_start_date"))
    coverage_end = _parse_date(payload.get("coverage_end_date") or payload.get("pay_period_end"))
    if coverage_start is None or coverage_end is None or coverage_end < coverage_start:
        raise HTTPException(400, "UPS schedule sync is missing a valid coverage date range.")

    incoming_trips = payload.get("trips")
    if not isinstance(incoming_trips, list) or not incoming_trips:
        raise HTTPException(400, "UPS schedule sync contained no trips.")

    counts = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "trips": 0}
    claimed_trip_ids: set[int] = set()
    matched_ids: set[int] = set()
    incoming_identities: set[tuple] = set()

    for trip_payload in incoming_trips:
        if not isinstance(trip_payload, dict):
            continue
        incoming_flights = [f for f in (trip_payload.get("flights") or []) if isinstance(f, dict)]
        if not incoming_flights:
            continue

        trip = _trip_for_external_id(db, payload, trip_payload, claimed_trip_ids)
        claimed_trip_ids.add(trip.id)
        counts["trips"] += 1
        existing = db.query(Flight).filter(Flight.trip_id == trip.id).all()

        for incoming in incoming_flights:
            fdate = _parse_date(incoming.get("flight_date"))
            number = _normalize_flight_number(incoming.get("flight_number"))
            origin = str(incoming.get("origin") or "").strip().upper()
            destination = str(incoming.get("destination") or "").strip().upper()
            dep = _parse_dt(incoming.get("scheduled_departure_utc"))
            arr = _parse_dt(incoming.get("scheduled_arrival_utc"))
            deadhead = bool(incoming.get("deadhead"))
            if not fdate or not number or len(origin) < 3 or len(destination) < 3 or dep is None or arr is None:
                continue
            if fdate < coverage_start or fdate > coverage_end:
                continue

            identity = _flight_identity(number, fdate, origin, destination)
            incoming_identities.add(identity)

            try:
                ensure_airport(db, origin)
                ensure_airport(db, destination)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

            row = _find_match(existing, {
                **incoming,
                "flight_number": number,
                "flight_date": fdate,
                "origin": origin,
                "destination": destination,
            })
            if row is None:
                row = Flight(
                    trip_id=trip.id,
                    sequence=max((x.sequence for x in existing), default=0) + 1,
                    flight_number=number,
                    flight_date=fdate,
                    origin=origin,
                    destination=destination,
                    deadhead=deadhead,
                    scheduled_departure_utc=dep,
                    scheduled_arrival_utc=arr,
                    schedule_active=True,
                    schedule_added=(trip.source == "ups_pdf"),
                    schedule_change_note="Added by UPS schedule sync",
                )
                db.add(row)
                db.flush()
                existing.append(row)
                matched_ids.add(row.id)
                counts["added"] += 1
                continue

            matched_ids.add(row.id)
            before = (
                _normalize_flight_number(row.flight_number), row.flight_date, row.origin, row.destination, row.deadhead,
                _parse_dt(row.scheduled_departure_utc), _parse_dt(row.scheduled_arrival_utc), row.schedule_active,
            )
            after = (number, fdate, origin, destination, deadhead, dep, arr, True)

            if before == after:
                counts["unchanged"] += 1
                continue

            # Never rewrite the schedule identity of a flight that has already
            # departed. It stays in history; any later replacement becomes a
            # separate row when present in the sync data.
            if row.actual_departure_utc and _flight_identity(row.flight_number, row.flight_date, row.origin, row.destination) != identity:
                counts["unchanged"] += 1
                continue

            if trip.source == "ups_pdf":
                snapshot_awarded(row)
            if not row.actual_departure_utc:
                clear_provider_tracking(row)
            row.flight_number = number
            row.flight_date = fdate
            row.origin = origin
            row.destination = destination
            row.deadhead = deadhead
            row.scheduled_departure_utc = dep
            row.scheduled_arrival_utc = arr
            row.schedule_active = True
            if not row.awarded_flight_number and trip.source == "ups_pdf":
                row.schedule_added = True
            row.schedule_change_note = "Updated by UPS schedule sync"
            counts["updated"] += 1

        db.flush()
        resequence_trip(db, trip)

    # Reconcile Current globally inside the coverage window, not just within
    # pairings that happened to survive the revision. This lets a completely
    # replaced awarded trip disappear from Current. Already-flown legs remain
    # historical facts and are never removed by a later schedule sync.
    candidates = (
        db.query(Flight)
        .join(Trip, Flight.trip_id == Trip.id)
        .filter(
            Trip.active.is_(True),
            Flight.schedule_active.is_(True),
            Flight.flight_date >= coverage_start,
            Flight.flight_date <= coverage_end,
        )
        .all()
    )
    touched_trips: set[int] = set()
    for row in candidates:
        if row.id in matched_ids:
            continue
        if _flight_identity(row.flight_number, row.flight_date, row.origin, row.destination) in incoming_identities:
            continue
        if row.actual_departure_utc or row.actual_arrival_utc:
            continue

        trip = row.trip
        if trip.source == "ups_pdf" and row.awarded_flight_number:
            row.schedule_active = False
            row.sequence = 0
            row.schedule_change_note = "Removed by UPS schedule sync"
        else:
            db.delete(row)
        touched_trips.add(trip.id)
        counts["removed"] += 1

    db.flush()
    for trip_id in touched_trips:
        trip = db.get(Trip, trip_id)
        if trip is not None:
            resequence_trip(db, trip)

    db.commit()
    return counts


def _store(payload: dict[str, Any], *, applied: bool, apply_result: dict[str, int] | None = None) -> dict[str, Any]:
    sync_dir = _dir()
    canonical = _canonical_bytes(payload)
    digest = hashlib.sha256(canonical).hexdigest()
    now = datetime.now(timezone.utc)

    latest_path = sync_dir / "latest.json"
    previous = _load_json(latest_path)
    previous_digest = previous.get("sha256") if isinstance(previous, dict) else None
    changed = previous_digest != digest

    envelope = {
        "received_at": now.isoformat(),
        "sha256": digest,
        "changed": changed,
        "applied": applied,
        "apply": apply_result or {},
        "payload": payload,
    }

    tmp = sync_dir / ".latest.json.tmp"
    tmp.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(latest_path)

    if changed:
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        history_path = sync_dir / f"sync-{stamp}-{digest[:10]}.json"
        history_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(history_path, 0o600)
        history = sorted(sync_dir.glob("sync-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in history[MAX_HISTORY_FILES:]:
            try:
                old.unlink()
            except OSError:
                pass

    return {
        "ok": True,
        "accepted": True,
        "changed": changed,
        "sha256": digest,
        "received_at": envelope["received_at"],
        "applied": applied,
        "apply": apply_result or {},
        "message": "UPS schedule synchronized into Current." if applied else "Schedule snapshot stored.",
    }


def store(payload: dict[str, Any]) -> dict[str, Any]:
    """Store a normalized schedule snapshot without applying it."""
    return _store(payload, applied=False)


def store_and_apply(payload: dict[str, Any], db: Session) -> dict[str, Any]:
    """Apply a user-triggered normalized UPS snapshot to Current, then archive it.

    The extension sends no UPS credentials/cookies/HTML. Deletions are bounded by
    the explicit coverage range so a Time Detail page opened from a later date is
    safe: omitted earlier rows are left untouched.
    """
    try:
        result = _apply_payload(payload, db)
    except Exception:
        db.rollback()
        raise
    return _store(payload, applied=True, apply_result=result)


def status() -> dict[str, Any]:
    latest = _load_json(_dir() / "latest.json")
    payload = latest.get("payload") if isinstance(latest, dict) else None
    trips = payload.get("trips") if isinstance(payload, dict) else None
    return {
        "enabled": enabled(),
        "has_pending_snapshot": bool(latest),
        "received_at": latest.get("received_at") if isinstance(latest, dict) else None,
        "sha256": latest.get("sha256") if isinstance(latest, dict) else None,
        "source": payload.get("source") if isinstance(payload, dict) else None,
        "captured_at": payload.get("captured_at") if isinstance(payload, dict) else None,
        "trip_count": len(trips) if isinstance(trips, list) else 0,
        "auto_apply": bool(latest.get("applied")) if isinstance(latest, dict) else False,
        "apply": latest.get("apply") if isinstance(latest, dict) else {},
    }


def latest() -> dict[str, Any] | None:
    return _load_json(_dir() / "latest.json")
