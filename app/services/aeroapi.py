from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from ..models import Flight, FlightPosition
from . import usage_ledger
from .schedule_service import snapshot_awarded

BASE_URL = "https://aeroapi.flightaware.com/aeroapi"
# Current published Personal-tier query fees (Sept 2026). The local ledger is
# deliberately conservative and is a guardrail, not a substitute for the
# account's actual FlightAware billing/usage page.
FLIGHT_INFO_COST = 0.005
POSITION_COST = 0.010
TRACK_COST = 0.012
AIRPORT_DEPARTURES_COST = 0.005
ALLOWED_OPERATING_TYPES = {"B744", "B748"}


class AeroApiError(RuntimeError):
    pass


def _as_utc(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _airport_matches(payload: dict | None, expected: str) -> bool:
    if not payload:
        return False
    wanted = expected.upper().strip()
    values = {
        str(payload.get(k) or "").upper().strip()
        for k in ("code", "code_iata", "code_icao", "code_lid")
    }
    return wanted in values


def _candidate_time(row: dict) -> datetime | None:
    for key in ("scheduled_off", "scheduled_out", "estimated_off", "estimated_out", "actual_off", "actual_out"):
        dt = _as_utc(row.get(key))
        if dt:
            return dt
    return None


def _provider_airport_missing(payload: dict | None) -> bool:
    if not payload:
        return True
    return not any(payload.get(k) for k in ("code", "code_iata", "code_icao", "code_lid"))


def _is_allowed_operating_type(row: dict) -> bool:
    aircraft = str(row.get("aircraft_type") or "").upper().strip()
    return not aircraft or aircraft in ALLOWED_OPERATING_TYPES


def _select_matching_flight(flight: Flight, rows: list[dict]) -> dict | None:
    """Match the planned leg without requiring FlightAware to know destination.

    UPS operating legs are constrained to B744/B748. Deadheads deliberately do
    not use that filter. A missing provider destination is neutral because the
    UPS Trips package remains our itinerary source of truth.
    """
    if not rows:
        return None
    scheduled = _as_utc(flight.scheduled_departure_utc)

    candidates = []
    for row in rows:
        if not flight.deadhead and not _is_allowed_operating_type(row):
            continue
        origin_payload = row.get("origin")
        if not _provider_airport_missing(origin_payload) and not _airport_matches(origin_payload, flight.origin):
            continue
        dest_payload = row.get("destination")
        # A known conflicting destination is a different operation. Missing is OK.
        if not _provider_airport_missing(dest_payload) and not _airport_matches(dest_payload, flight.destination):
            continue
        candidate = _candidate_time(row)
        delta = abs((candidate - scheduled).total_seconds()) if scheduled and candidate else 0.0
        missing_penalty = 1 if _provider_airport_missing(dest_payload) else 0
        candidates.append((missing_penalty, delta, row))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]


def _request(api_key: str, path: str, *, params: dict | None = None) -> dict:
    try:
        response = httpx.get(
            f"{BASE_URL}{path}",
            headers={"x-apikey": api_key},
            params=params,
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise AeroApiError(f"Could not reach FlightAware: {exc}") from exc

    if response.status_code in {401, 403}:
        raise AeroApiError("FlightAware rejected the API key or this endpoint is not available on the current tier.")
    if response.status_code == 429:
        raise AeroApiError("FlightAware rate limit reached; tracking will retry later.")
    if response.status_code >= 400:
        detail = ""
        try:
            detail = str(response.json())[:300]
        except Exception:
            detail = response.text[:300]
        raise AeroApiError(f"FlightAware returned HTTP {response.status_code}: {detail}")
    return response.json() if response.content else {}


def test_connection(api_key: str) -> dict:
    """Validate an AeroAPI key using FlightAware's zero-cost usage endpoint."""
    if not api_key:
        raise AeroApiError("No AeroAPI key is configured.")
    payload = _request(api_key, "/account/usage")
    return {"ok": True, "message": "AeroAPI connection successful.", "usage": payload}


def get_account_usage(api_key: str, *, start: datetime | None = None, end: datetime | None = None) -> dict:
    """Fetch FlightAware's zero-cost account usage for a bounded period."""
    if not api_key:
        raise AeroApiError("No AeroAPI key is configured.")
    now = datetime.now(timezone.utc)
    start = start or now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = end or now
    return _request(
        api_key,
        "/account/usage",
        params={
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "all_keys": "false",
        },
    )


def lookup_flight_info(api_key: str, flight: Flight, monthly_budget_usd: float) -> dict | None:
    if not usage_ledger.can_spend("aeroapi", monthly_budget_usd, FLIGHT_INFO_COST):
        raise AeroApiError("Local AeroAPI monthly budget guard reached.")

    scheduled = _as_utc(flight.scheduled_departure_utc)
    if scheduled:
        start = scheduled - timedelta(hours=12)
        end = scheduled + timedelta(hours=36)
    else:
        start = datetime.combine(flight.flight_date, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=12)
        end = start + timedelta(days=2)

    # AeroAPI current-flight lookup supports a bounded start/end window. Keep
    # it narrow enough that a repeated daily flight number returns few records.
    payload = _request(
        api_key,
        f"/flights/{quote(flight.flight_number, safe='')}",
        params={
            "ident_type": "designator",
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "max_pages": 1,
        },
    )
    usage_ledger.add("aeroapi", "flight_info", FLIGHT_INFO_COST)
    return _select_matching_flight(flight, list(payload.get("flights") or []))


def _candidate_ident(row: dict) -> str | None:
    for key in ("ident_icao", "ident"):
        value = str(row.get(key) or "").upper().strip()
        if value:
            return value
    return None


def _747_reassignment_candidates(api_key: str, flight: Flight, monthly_budget_usd: float) -> list[dict]:
    """Rare IROPS fallback: look for a unique UPS 747 departure from the origin.

    This uses the inexpensive airport departure resources only after the planned
    ident no longer yields a valid B744/B748 match. It is throttled to once per
    hour by sync_flight.
    """
    scheduled = _as_utc(flight.scheduled_departure_utc)
    if scheduled is None:
        scheduled = datetime.combine(flight.flight_date, datetime.min.time(), tzinfo=timezone.utc)
    start = scheduled - timedelta(hours=4)
    end = scheduled + timedelta(hours=12)
    now = datetime.now(timezone.utc)
    endpoint = "scheduled_departures" if now <= scheduled + timedelta(hours=3) else "departures"
    if not usage_ledger.can_spend("aeroapi", monthly_budget_usd, AIRPORT_DEPARTURES_COST):
        return []
    payload = _request(
        api_key,
        f"/airports/{quote(flight.origin, safe='')}/flights/{endpoint}",
        params={
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "type": "Airline",
            "max_pages": 1,
        },
    )
    usage_ledger.add("aeroapi", f"airport_{endpoint}", AIRPORT_DEPARTURES_COST)
    rows = list(payload.get(endpoint) or payload.get("departures") or payload.get("scheduled_departures") or payload.get("flights") or [])
    candidates = []
    for row in rows:
        ident = _candidate_ident(row)
        if not ident or not ident.startswith("UPS"):
            continue
        aircraft = str(row.get("aircraft_type") or "").upper().strip()
        if aircraft not in ALLOWED_OPERATING_TYPES:
            continue
        dest = row.get("destination")
        if not _provider_airport_missing(dest) and not _airport_matches(dest, flight.destination):
            continue
        candidate_time = _candidate_time(row)
        if candidate_time and not (start <= candidate_time <= end):
            continue
        row = dict(row)
        row["_candidate_ident"] = ident
        row["_candidate_time"] = candidate_time
        candidates.append(row)
    candidates.sort(key=lambda r: abs(((r.get("_candidate_time") or scheduled) - scheduled).total_seconds()))
    return candidates


def _maybe_adopt_unique_reassignment(api_key: str, flight: Flight, monthly_budget_usd: float) -> tuple[dict | None, list[str]]:
    warnings: list[str] = []
    candidates = _747_reassignment_candidates(api_key, flight, monthly_budget_usd)
    if len(candidates) == 1:
        candidate = candidates[0]
        new_ident = candidate.get("_candidate_ident")
        if new_ident and new_ident != flight.flight_number:
            snapshot_awarded(flight)
            old_ident = flight.flight_number
            flight.flight_number = new_ident
            flight.schedule_change_note = f"Auto-detected probable 747 reassignment: {old_ident} → {new_ident}"
            warnings.append(f"Probable UPS 747 reassignment detected: {old_ident} → {new_ident}.")
        return candidate, warnings
    if len(candidates) > 1:
        ids = ", ".join(str(c.get("_candidate_ident")) for c in candidates[:4])
        warnings.append(f"Multiple plausible UPS 747 replacements found ({ids}); schedule was not changed automatically.")
    return None, warnings


def _position_values(position: dict | None):
    if not position:
        return None
    lat = position.get("latitude")
    lon = position.get("longitude")
    if lat is None or lon is None:
        return None
    altitude = position.get("altitude")
    altitude_ft = int(round(float(altitude) * 100)) if altitude is not None else None
    speed = position.get("groundspeed")
    speed_kt = int(round(float(speed))) if speed is not None else None
    captured = _as_utc(position.get("timestamp")) or datetime.now(timezone.utc)
    return float(lat), float(lon), altitude_ft, speed_kt, captured


def _append_position(db: Session, flight: Flight, position: dict | None) -> bool:
    values = _position_values(position)
    if not values:
        return False
    lat, lon, altitude_ft, speed_kt, captured = values
    latest = (
        db.query(FlightPosition)
        .filter(FlightPosition.flight_id == flight.id)
        .order_by(FlightPosition.captured_utc.desc())
        .first()
    )
    if latest and _as_utc(latest.captured_utc) == captured:
        return False

    flight.current_latitude = lat
    flight.current_longitude = lon
    flight.altitude_ft = altitude_ft
    flight.groundspeed_kt = speed_kt
    flight.last_position_utc = captured
    db.add(FlightPosition(
        flight_id=flight.id,
        latitude=lat,
        longitude=lon,
        altitude_ft=altitude_ft,
        groundspeed_kt=speed_kt,
        captured_utc=captured,
    ))
    return True


def _apply_info(db: Session, flight: Flight, info: dict) -> bool:
    flight.provider_flight_id = info.get("fa_flight_id") or flight.provider_flight_id
    flight.registration = info.get("registration") or flight.registration
    flight.aircraft_type = info.get("aircraft_type") or flight.aircraft_type

    # Use runway OFF/ON values because the family display describes actual
    # takeoff/landing rather than gate OUT/IN. Keep FlightAware's own runway
    # schedule separately from the UPS/PDF schedule so live status can mirror
    # the provider without rewriting the awarded/current itinerary.
    flight.provider_scheduled_departure_utc = _as_utc(info.get("scheduled_off") or info.get("scheduled_out"))
    flight.provider_scheduled_arrival_utc = _as_utc(info.get("scheduled_on") or info.get("scheduled_in"))
    flight.estimated_departure_utc = _as_utc(info.get("estimated_off") or info.get("estimated_out"))
    flight.estimated_arrival_utc = _as_utc(info.get("estimated_on") or info.get("estimated_in"))
    flight.actual_departure_utc = _as_utc(info.get("actual_off") or info.get("actual_out"))
    flight.actual_arrival_utc = _as_utc(info.get("actual_on") or info.get("actual_in"))

    # AeroAPI exposes these as seconds; they generally align more closely with
    # FlightAware's own On time/Delayed/Ahead presentation than recomputing
    # against the PDF schedule.
    try:
        flight.provider_departure_delay_seconds = int(info.get("departure_delay")) if info.get("departure_delay") is not None else None
    except (TypeError, ValueError):
        flight.provider_departure_delay_seconds = None
    try:
        flight.provider_arrival_delay_seconds = int(info.get("arrival_delay")) if info.get("arrival_delay") is not None else None
    except (TypeError, ValueError):
        flight.provider_arrival_delay_seconds = None

    if info.get("cancelled"):
        flight.status = "cancelled"
    elif flight.actual_arrival_utc:
        flight.status = "arrived"
    elif flight.actual_departure_utc:
        flight.status = "airborne"
    else:
        flight.status = "scheduled"

    return _append_position(db, flight, info.get("last_position"))


def fetch_current_position(api_key: str, db: Session, flight: Flight, monthly_budget_usd: float) -> bool:
    """Fetch the dedicated AeroAPI current-position resource for an airborne leg."""
    if not flight.provider_flight_id:
        return False
    if not usage_ledger.can_spend("aeroapi", monthly_budget_usd, POSITION_COST):
        return False
    payload = _request(
        api_key,
        f"/flights/{quote(flight.provider_flight_id, safe='')}/position",
    )
    usage_ledger.add("aeroapi", "current_position", POSITION_COST)
    # AeroAPI revisions have represented this resource both as a position-like
    # object and inside a named field.  Accept either shape defensively.
    position = payload.get("last_position") or payload.get("position") or payload
    return _append_position(db, flight, position)


def fetch_full_track(
    api_key: str,
    db: Session,
    flight: Flight,
    monthly_budget_usd: float,
    *,
    final: bool = False,
) -> bool:
    """Replace sparse samples with AeroAPI's actual track snapshot.

    During flight this is used once to seed the real path immediately.  After
    landing it is fetched once more as the final historical track.
    """
    if not flight.provider_flight_id:
        return False
    if final and flight.provider_track_fetched:
        return False
    if not usage_ledger.can_spend("aeroapi", monthly_budget_usd, TRACK_COST):
        return False
    payload = _request(
        api_key,
        f"/flights/{quote(flight.provider_flight_id, safe='')}/track",
        params={"include_estimated_positions": "false", "include_surface_positions": "false"},
    )
    usage_ledger.add("aeroapi", "full_track", TRACK_COST)
    positions = list(payload.get("positions") or [])
    if positions:
        # Flush a just-added current-position sample first so the replacement
        # delete removes it as well; otherwise SQLAlchemy can leave one pending
        # duplicate in the session.
        db.flush()
        db.query(FlightPosition).filter(FlightPosition.flight_id == flight.id).delete(synchronize_session=False)
        for p in positions:
            values = _position_values(p)
            if not values:
                continue
            lat, lon, altitude_ft, speed_kt, captured = values
            db.add(FlightPosition(
                flight_id=flight.id,
                latitude=lat,
                longitude=lon,
                altitude_ft=altitude_ft,
                groundspeed_kt=speed_kt,
                captured_utc=captured,
            ))
        # Keep the aircraft marker aligned with the newest track point without
        # inserting a duplicate row after the snapshot replacement.
        last_values = _position_values(positions[-1])
        if last_values:
            lat, lon, altitude_ft, speed_kt, captured = last_values
            flight.current_latitude = lat
            flight.current_longitude = lon
            flight.altitude_ft = altitude_ft
            flight.groundspeed_kt = speed_kt
            flight.last_position_utc = captured
    flight.last_track_poll_utc = datetime.now(timezone.utc)
    if final:
        flight.provider_track_fetched = True
    return bool(positions)


def sync_flight(
    api_key: str,
    db: Session,
    flight: Flight,
    monthly_budget_usd: float,
    *,
    live_viewers: bool = True,
    force_live_position: bool = False,
) -> dict:
    info = lookup_flight_info(api_key, flight, monthly_budget_usd)
    now = datetime.now(timezone.utc)
    flight.last_provider_poll_utc = now
    warnings: list[str] = []
    if info is None and not flight.deadhead:
        last_search = _as_utc(flight.last_reassignment_search_utc)
        if last_search is None or (now - last_search) >= timedelta(hours=1):
            flight.last_reassignment_search_utc = now
            try:
                info, reassignment_warnings = _maybe_adopt_unique_reassignment(api_key, flight, monthly_budget_usd)
                warnings.extend(reassignment_warnings)
            except AeroApiError as exc:
                warnings.append(f"747 reassignment search: {exc}")
    if info is None:
        db.commit()
        result = {"matched": False, "flight_id": flight.id}
        if warnings:
            result["warnings"] = warnings
        return result

    info_had_position = _apply_info(db, flight, info)

    if flight.actual_departure_utc and not flight.actual_arrival_utc and live_viewers:
        # First use the last_position bundled into the cheaper flight-info call.
        # Only pay for the dedicated position resource when that bundled value
        # was absent or stale. This keeps the wall display useful without paying
        # twice for essentially the same position on every poll.
        last_pos = _as_utc(flight.last_position_utc)
        stale_position = last_pos is None or (now - last_pos) > timedelta(minutes=12)
        if force_live_position or (not info_had_position) or stale_position:
            try:
                fetch_current_position(api_key, db, flight, monthly_budget_usd)
            except AeroApiError as exc:
                warnings.append(f"position: {exc}")

        # Seed the actual flown path only while somebody is looking at the map.
        # If nobody is viewing, the final track is still fetched after landing
        # so History remains complete.
        if flight.last_track_poll_utc is None:
            try:
                fetch_full_track(api_key, db, flight, monthly_budget_usd, final=False)
            except AeroApiError as exc:
                warnings.append(f"track: {exc}")

    if flight.actual_arrival_utc:
        # One final inexpensive full-track request gives History the detailed
        # flown path instead of only our sparse live samples.
        try:
            fetch_full_track(api_key, db, flight, monthly_budget_usd, final=True)
        except AeroApiError as exc:
            warnings.append(f"final track: {exc}")

    db.commit()
    result = {"matched": True, "flight_id": flight.id, "provider_flight_id": flight.provider_flight_id}
    if warnings:
        result["warnings"] = warnings
    return result
