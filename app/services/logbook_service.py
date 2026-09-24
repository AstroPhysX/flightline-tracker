from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict, OrderedDict
from datetime import date, datetime
import threading
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from ..models import Airport, Flight, LogbookAirportVisit, LogbookEntry, LogbookLeg
from .airport_resolver import ensure_airport, canonical_airport_groups

_CACHE_LOCK = threading.RLock()
_MAP_CACHE: OrderedDict[tuple, dict] = OrderedDict()
_OPTIONS_CACHE: dict | None = None
_ROUTE_TIME_CACHE: dict[tuple, dict] = {}
_MAP_CACHE_LIMIT = 12


def invalidate_logbook_caches() -> None:
    """Clear derived in-memory logbook data after an import or new flight.

    The lifetime logbook changes rarely, while dashboard/map endpoints are read
    constantly. Keeping these derived results in RAM is much cheaper on a NAS
    than rebuilding them from thousands of ORM rows on every browser refresh.
    """
    global _OPTIONS_CACHE
    with _CACHE_LOCK:
        _MAP_CACHE.clear()
        _ROUTE_TIME_CACHE.clear()
        _OPTIONS_CACHE = None


EXPECTED_COLUMNS = [
    "DATE", "AIRCRAFT MAKE & MODEL", "AIRCRAFT IDENT", "FLIGHT #", "ROUTE OF FLIGHT",
    "LEGS", "DURATION", "NIGHT", "INSTRUMENT", "APPROACHES & TYPE", "LANDINGS DAY",
    "LANDINGS NIGHT", "REMARKS", "SIMULATED INSTRUMENT", "SIMULATOR", "CROSS COUNTRY",
    "FE", "INSTRUCTOR", "SOLO", "DUAL", "SECOND IN COMMAND", "PILOT IN COMMAND",
    "FLIGHT COST", "EXPENSES",
]


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _float(value: str | None) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    value_float = _float(value)
    return int(value_float) if value_float is not None else None


def _date(value: str) -> date:
    return datetime.strptime(value.strip(), "%m/%d/%Y").date()


def _repair_row(row: list[str], expected: int) -> list[str]:
    """Repair Logbook Pro exports with unquoted commas in REMARKS.

    The export has 24 fixed columns.  When REMARKS contains a comma, some
    Logbook Pro versions emit it without CSV quoting, shifting every trailing
    field right.  REMARKS is column 13 (index 12), so fold any excess fields
    back into that column.
    """
    if len(row) == expected:
        return row
    if len(row) > expected:
        extra = len(row) - expected
        return row[:12] + [",".join(row[12:13 + extra])] + row[13 + extra:]
    return row + [""] * (expected - len(row))


def _route_tokens(route: str | None) -> list[str]:
    text = _clean(route).upper().replace(" ", "")
    if not text or text in {"-", "SIM", "SIM-"}:
        return []
    # Double dashes and a few hand-entered spacing/case glitches occur in old
    # entries.  Preserve only plausible airport identifiers and let the airport
    # resolver decide whether they are real IATA/ICAO codes.
    tokens = [part for part in re.split(r"-+", text) if part]
    return [token for token in tokens if re.fullmatch(r"[A-Z0-9]{3,4}", token)]


def _base_key(row: dict[str, str]) -> str:
    # Stable identity intentionally excludes remarks/cost fields so reimporting
    # an updated Logbook Pro export updates rather than duplicates old flights.
    fields = [
        row.get("DATE", ""), row.get("AIRCRAFT MAKE & MODEL", ""), row.get("AIRCRAFT IDENT", ""),
        row.get("FLIGHT #", ""), row.get("ROUTE OF FLIGHT", ""), row.get("DURATION", ""),
        row.get("NIGHT", ""), row.get("INSTRUMENT", ""), row.get("LANDINGS DAY", ""),
        row.get("LANDINGS NIGHT", ""), row.get("SECOND IN COMMAND", ""), row.get("PILOT IN COMMAND", ""),
    ]
    return "\x1f".join(_clean(v) for v in fields)


def _source_key(base_key: str, occurrence: int) -> str:
    return hashlib.sha256(f"{base_key}\x1e{occurrence}".encode("utf-8")).hexdigest()




def _normalized_flight_number(value: str | None) -> str:
    return re.sub(r"\s+", "", _clean(value).upper())


def _is_sim_record(row: dict[str, str]) -> bool:
    model = _clean(row.get("AIRCRAFT MAKE & MODEL")).upper()
    ident = _clean(row.get("AIRCRAFT IDENT")).upper()
    simulator = _float(row.get("SIMULATOR"))
    route = _clean(row.get("ROUTE OF FLIGHT")).upper()
    return ident == "SIM" or "SIM" in model or bool(simulator) or route in {"SIM", "SIM-"}


def _is_sim_entry(entry: LogbookEntry) -> bool:
    return (entry.aircraft_ident or "").strip().upper() == "SIM" or "SIM" in (entry.aircraft_model or "").upper()


def _tracker_model(value: str | None) -> str | None:
    text = _clean(value).upper().replace(" ", "")
    aliases = {
        "B744": "B-744",
        "B748": "B-748",
        "B74R": "B-744",
        "MD11": "MD-11",
    }
    return aliases.get(text, _clean(value) or None)


def _tracker_source_key(flight_id: int) -> str:
    return f"tracker:{flight_id}"


def _tracker_duplicate_for_csv(db: Session, row: dict[str, str]) -> list[LogbookEntry]:
    flight_date = _date(row["DATE"])
    ident = _normalized_flight_number(row.get("FLIGHT #"))
    route = _clean(row.get("ROUTE OF FLIGHT")).upper().replace(" ", "")
    registration = _clean(row.get("AIRCRAFT IDENT")).upper().replace(" ", "")
    candidates = (
        db.query(LogbookEntry)
        .filter(LogbookEntry.flight_date == flight_date, LogbookEntry.source_key.like("tracker:%"))
        .all()
    )
    matches = []
    for candidate in candidates:
        if ident and _normalized_flight_number(candidate.flight_number) == ident:
            matches.append(candidate)
            continue
        candidate_route = _clean(candidate.route_raw).upper().replace(" ", "")
        candidate_reg = _clean(candidate.aircraft_ident).upper().replace(" ", "")
        if route and candidate_route == route and registration and candidate_reg == registration:
            matches.append(candidate)
    return matches


def _set_entry_route(db: Session, entry: LogbookEntry, tokens: list[str], unresolved: Counter[str] | None = None) -> None:
    entry.legs.clear()
    entry.visits.clear()
    resolved: list[tuple[str, Airport | None]] = []
    for token in tokens:
        try:
            airport = ensure_airport(db, token)
        except ValueError:
            airport = None
        if airport is None and unresolved is not None:
            unresolved[token] += 1
        resolved.append((token, airport))

    visit_sequence = 1
    for token, airport in resolved:
        if airport is None:
            continue
        entry.visits.append(LogbookAirportVisit(sequence=visit_sequence, airport_code=token))
        visit_sequence += 1

    leg_sequence = 1
    for (origin_code, origin_airport), (dest_code, dest_airport) in zip(resolved, resolved[1:]):
        if origin_airport is None or dest_airport is None:
            continue
        entry.legs.append(LogbookLeg(sequence=leg_sequence, origin=origin_code, destination=dest_code))
        leg_sequence += 1


def sync_completed_flight_to_logbook(db: Session, flight: Flight, *, commit: bool = True) -> bool:
    """Persist a completed operating tracker flight into the lifetime map.

    Deadheads are intentionally excluded because they are not pilot logbook
    flights. If a later Logbook Pro CSV contains the same dated flight number,
    the CSV import supersedes this automatically-created entry.
    """
    if flight.deadhead or not flight.actual_arrival_utc:
        return False

    # If the authoritative Logbook Pro archive already contains this flight, do
    # not create a second copy. Date + normalized flight number is strong enough
    # for the UPS operating flights tracked by this application.
    flight_ident = _normalized_flight_number(flight.flight_number)
    same_day = db.query(LogbookEntry).filter(LogbookEntry.flight_date == flight.flight_date).all()
    for candidate in same_day:
        if candidate.source_key.startswith("tracker:"):
            continue
        if flight_ident and _normalized_flight_number(candidate.flight_number) == flight_ident:
            auto = db.query(LogbookEntry).filter(LogbookEntry.source_key == _tracker_source_key(flight.id)).one_or_none()
            if auto is not None:
                db.delete(auto)
                if commit:
                    db.commit()
            return False

    key = _tracker_source_key(flight.id)
    entry = db.query(LogbookEntry).filter(LogbookEntry.source_key == key).one_or_none()
    if entry is None:
        entry = LogbookEntry(source_key=key, flight_date=flight.flight_date)
        db.add(entry)
        db.flush()

    entry.flight_date = flight.flight_date
    entry.aircraft_model = _tracker_model(flight.aircraft_type)
    entry.aircraft_ident = _clean(flight.registration) or None
    entry.flight_number = _clean(flight.flight_number) or None
    entry.route_raw = f"{flight.origin}-{flight.destination}"
    if flight.actual_departure_utc and flight.actual_arrival_utc:
        seconds = (flight.actual_arrival_utc - flight.actual_departure_utc).total_seconds()
        entry.duration_hours = round(max(0.0, seconds / 3600.0), 2)
    elif flight.scheduled_departure_utc and flight.scheduled_arrival_utc:
        seconds = (flight.scheduled_arrival_utc - flight.scheduled_departure_utc).total_seconds()
        entry.duration_hours = round(max(0.0, seconds / 3600.0), 2)
    entry.remarks = "Automatically added by UPS Family Flight Tracker"
    _set_entry_route(db, entry, [flight.origin.upper(), flight.destination.upper()])
    if commit:
        db.commit()
    invalidate_logbook_caches()
    return True


def backfill_completed_tracker_flights(db: Session) -> int:
    flights = (
        db.query(Flight)
        .filter(Flight.actual_arrival_utc.is_not(None), Flight.deadhead.is_(False))
        .order_by(Flight.flight_date.asc(), Flight.id.asc())
        .all()
    )
    added = 0
    for flight in flights:
        if sync_completed_flight_to_logbook(db, flight, commit=False):
            added += 1
    db.commit()
    return added


def parse_logbook_csv(raw: bytes) -> tuple[list[dict[str, str]], dict]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration as exc:
        raise ValueError("The logbook CSV is empty.") from exc

    if header != EXPECTED_COLUMNS:
        missing = [c for c in EXPECTED_COLUMNS if c not in header]
        if missing:
            raise ValueError("This does not look like the expected Logbook Pro CSV export. Missing: " + ", ".join(missing[:5]))

    rows: list[dict[str, str]] = []
    repaired = 0
    skipped = 0
    for row_number, raw_row in enumerate(reader, start=2):
        if not any(_clean(v) for v in raw_row):
            continue
        if len(raw_row) != len(header):
            repaired += 1
        row = _repair_row(raw_row, len(header))
        record = dict(zip(header, row))
        try:
            _date(record["DATE"])
        except Exception:
            skipped += 1
            continue
        rows.append(record)
    return rows, {"repaired_rows": repaired, "skipped_rows": skipped}


def import_logbook_csv(db: Session, raw: bytes) -> dict:
    rows, diagnostics = parse_logbook_csv(raw)
    existing = {e.source_key: e for e in db.query(LogbookEntry).all()}
    occurrence_counter: Counter[str] = Counter()
    airport_cache: dict[str, Airport | None] = {}
    unresolved: Counter[str] = Counter()

    inserted = 0
    updated = 0
    mapped_entries = 0
    simulator_entries = 0

    # v11 imported simulator rows into lifetime totals even though they cannot
    # represent a geographic flight. Remove those legacy rows and ignore SIM
    # records entirely from new imports.
    for existing_entry in list(existing.values()):
        if _is_sim_entry(existing_entry):
            db.delete(existing_entry)
            existing.pop(existing_entry.source_key, None)

    for row in rows:
        if _is_sim_record(row):
            simulator_entries += 1
            continue
        base = _base_key(row)
        occurrence_counter[base] += 1
        key = _source_key(base, occurrence_counter[base])
        entry = existing.get(key)
        if entry is None:
            entry = LogbookEntry(source_key=key, flight_date=_date(row["DATE"]))
            db.add(entry)
            db.flush()
            existing[key] = entry
            inserted += 1
        else:
            updated += 1
            entry.legs.clear()
            entry.visits.clear()

        entry.flight_date = _date(row["DATE"])
        entry.aircraft_model = _clean(row.get("AIRCRAFT MAKE & MODEL")) or None
        entry.aircraft_ident = _clean(row.get("AIRCRAFT IDENT")) or None
        entry.flight_number = _clean(row.get("FLIGHT #")) or None
        entry.route_raw = _clean(row.get("ROUTE OF FLIGHT")) or None
        entry.duration_hours = _float(row.get("DURATION"))
        entry.night_hours = _float(row.get("NIGHT"))
        entry.instrument_hours = _float(row.get("INSTRUMENT"))
        entry.landings_day = _int(row.get("LANDINGS DAY"))
        entry.landings_night = _int(row.get("LANDINGS NIGHT"))
        entry.second_in_command_hours = _float(row.get("SECOND IN COMMAND"))
        entry.pilot_in_command_hours = _float(row.get("PILOT IN COMMAND"))
        entry.remarks = _clean(row.get("REMARKS")) or None

        tokens = _route_tokens(entry.route_raw)
        # Reuse the resolver cache during large imports, but never fabricate a
        # route when Logbook Pro itself contains "-" or no airport codes.
        resolved_tokens = []
        for token in tokens:
            if token not in airport_cache:
                try:
                    airport_cache[token] = ensure_airport(db, token)
                except ValueError:
                    airport_cache[token] = None
            if airport_cache[token] is None:
                unresolved[token] += 1
            resolved_tokens.append(token)
        _set_entry_route(db, entry, resolved_tokens, unresolved=None)

        # A completed tracker flight is only a temporary lifetime-logbook entry.
        # Once the user's Logbook Pro export contains that same flight, the CSV
        # becomes authoritative and the automatic copy is removed.
        for tracker_entry in _tracker_duplicate_for_csv(db, row):
            if tracker_entry.id != entry.id:
                db.delete(tracker_entry)

        if entry.visits:
            mapped_entries += 1

    db.commit()
    invalidate_logbook_caches()
    non_sim_rows = [r for r in rows if not _is_sim_record(r)]
    dates = [_date(r["DATE"]) for r in non_sim_rows]
    return {
        "ok": True,
        "rows": len(rows),
        "inserted": inserted,
        "updated": updated,
        "mapped_entries": mapped_entries,
        "simulator_entries": simulator_entries,
        "simulator_entries_skipped": simulator_entries,
        "date_start": min(dates).isoformat() if dates else None,
        "date_end": max(dates).isoformat() if dates else None,
        "unresolved_airports": [{"code": code, "count": count} for code, count in unresolved.most_common(25)],
        **diagnostics,
    }


def _filtered_entries(
    db: Session,
    aircraft_models: list[str] | None = None,
    aircraft_ident: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
):
    q = (
        db.query(LogbookEntry)
        .options(selectinload(LogbookEntry.legs), selectinload(LogbookEntry.visits))
        .filter(func.upper(func.coalesce(LogbookEntry.aircraft_ident, "")) != "SIM")
        .filter(~func.upper(func.coalesce(LogbookEntry.aircraft_model, "")).like("%SIM%"))
    )
    models = [m.strip() for m in (aircraft_models or []) if m and m.strip()]
    if models:
        q = q.filter(LogbookEntry.aircraft_model.in_(models))
    if aircraft_ident:
        q = q.filter(LogbookEntry.aircraft_ident == aircraft_ident)
    if start_date:
        q = q.filter(LogbookEntry.flight_date >= start_date)
    if end_date:
        q = q.filter(LogbookEntry.flight_date <= end_date)
    return q.order_by(LogbookEntry.flight_date.asc(), LogbookEntry.id.asc()).all()


def logbook_options(db: Session) -> dict:
    global _OPTIONS_CACHE
    with _CACHE_LOCK:
        if _OPTIONS_CACHE is not None:
            return _OPTIONS_CACHE
    q = (
        db.query(LogbookEntry)
        .filter(func.upper(func.coalesce(LogbookEntry.aircraft_ident, "")) != "SIM")
        .filter(~func.upper(func.coalesce(LogbookEntry.aircraft_model, "")).like("%SIM%"))
    )
    entries = q.order_by(LogbookEntry.flight_date.asc(), LogbookEntry.id.asc()).all()
    models = sorted({e.aircraft_model for e in entries if e.aircraft_model}, key=str.casefold)
    idents = sorted({e.aircraft_ident for e in entries if e.aircraft_ident}, key=str.casefold)
    result = {
        "models": models,
        "idents": idents,
        "date_start": entries[0].flight_date.isoformat() if entries else None,
        "date_end": entries[-1].flight_date.isoformat() if entries else None,
        "entry_count": len(entries),
    }
    with _CACHE_LOCK:
        _OPTIONS_CACHE = result
    return result


def _haversine_nm_coords(a: tuple[float, float], b: tuple[float, float]) -> float:
    from math import asin, cos, radians, sin, sqrt
    lat1, lon1 = radians(a[0]), radians(a[1])
    lat2, lon2 = radians(b[0]), radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 3440.065 * 2 * asin(min(1.0, sqrt(h)))


def _entry_route_speed_check(entry: LogbookEntry, alias_map: dict[str, str], airport_meta: dict[str, dict]) -> dict | None:
    """Return an explanation when a resolved historical route is physically implausible.

    Old logbook exports sometimes contain legacy/internal three-letter codes that
    collide with modern IATA codes.  We never rewrite the source route.  Instead,
    if the resolved geography plus logged duration would require an impossible
    average groundspeed, the entry is omitted from the map and reported.
    """
    duration = float(entry.duration_hours or 0.0)
    if duration <= 0 or not entry.legs:
        return None
    total_nm = 0.0
    mapped_legs = 0
    for leg in entry.legs:
        origin = alias_map.get(leg.origin, leg.origin)
        destination = alias_map.get(leg.destination, leg.destination)
        a, b = airport_meta.get(origin), airport_meta.get(destination)
        if not a or not b:
            continue
        total_nm += _haversine_nm_coords((a["lat"], a["lon"]), (b["lat"], b["lon"]))
        mapped_legs += 1
    if not mapped_legs or total_nm < 500:
        return None
    implied_kt = total_nm / duration
    # 900 kt is intentionally generous. This is not trying to judge normal
    # performance; it only catches unmistakable code-collision / source errors.
    if implied_kt <= 900:
        return None
    return {
        "entry_id": entry.id,
        "date": entry.flight_date.isoformat(),
        "aircraft_model": entry.aircraft_model,
        "route_raw": entry.route_raw,
        "duration_hours": duration,
        "resolved_distance_nm": round(total_nm),
        "implied_speed_kt": round(implied_kt),
        "reason": "Resolved airport codes imply an impossible average groundspeed; source route left unchanged and hidden from map.",
    }


def _route_time_estimate_uncached(db: Session, origin: str, destination: str, aircraft_model: str | None = None) -> dict:
    """Historical average flight time for a city-pair from the imported logbook.

    Direction is respected first (winds matter).  If there are no same-direction
    samples, the reverse direction is used as a fallback. Multi-leg Logbook Pro
    entries are divided evenly because the export has one duration for the entry.
    """
    origin = (origin or "").strip().upper()
    destination = (destination or "").strip().upper()
    alias_map, _ = canonical_airport_groups(db, {origin, destination})
    ocanon = alias_map.get(origin, origin)
    dcanon = alias_map.get(destination, destination)

    # Pull only entries that contain either endpoint pair, then canonicalize in
    # Python so DFW/KDFW and similar aliases contribute to the same estimate.
    entries = (
        db.query(LogbookEntry)
        .options(selectinload(LogbookEntry.legs))
        .join(LogbookLeg)
        .filter(LogbookLeg.origin.in_({origin, destination, ocanon, dcanon}) | LogbookLeg.destination.in_({origin, destination, ocanon, dcanon}))
        .distinct()
        .all()
    )
    normalized_model = _tracker_model(aircraft_model) if aircraft_model else None

    def samples(reverse: bool = False):
        values = []
        estimated = False
        for entry in entries:
            if normalized_model and entry.aircraft_model and entry.aircraft_model != normalized_model:
                continue
            legs = list(entry.legs)
            if not legs or not entry.duration_hours:
                continue
            share = float(entry.duration_hours) / len(legs)
            for leg in legs:
                lm, _ = canonical_airport_groups(db, {leg.origin, leg.destination, origin, destination})
                lo = lm.get(leg.origin, leg.origin)
                ld = lm.get(leg.destination, leg.destination)
                want_o, want_d = (dcanon, ocanon) if reverse else (ocanon, dcanon)
                if lo == want_o and ld == want_d:
                    values.append(share)
                    estimated |= len(legs) > 1
        return values, estimated

    vals, estimated = samples(False)
    basis = "same_direction"
    if not vals:
        vals, estimated = samples(True)
        basis = "reverse_direction"
    if not vals and normalized_model:
        # If aircraft-specific history is unavailable, fall back to all types.
        return _route_time_estimate_uncached(db, origin, destination, None)
    if not vals:
        return {"average_hours": None, "samples": 0, "estimated": False, "basis": None}
    return {
        "average_hours": round(sum(vals) / len(vals), 2),
        "samples": len(vals),
        "estimated": estimated,
        "basis": basis,
        "aircraft_model": normalized_model,
    }



def route_time_estimate(db: Session, origin: str, destination: str, aircraft_model: str | None = None) -> dict:
    key = ((origin or "").strip().upper(), (destination or "").strip().upper(), _tracker_model(aircraft_model) if aircraft_model else None)
    with _CACHE_LOCK:
        cached = _ROUTE_TIME_CACHE.get(key)
        if cached is not None:
            return cached
    result = _route_time_estimate_uncached(db, origin, destination, aircraft_model)
    with _CACHE_LOCK:
        _ROUTE_TIME_CACHE[key] = result
    return result


def _build_logbook_map_uncached(
    db: Session,
    aircraft_models: list[str] | None = None,
    aircraft_ident: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    entries = _filtered_entries(db, aircraft_models, aircraft_ident, start_date, end_date)

    raw_codes: set[str] = set()
    for entry in entries:
        raw_codes.update(v.airport_code for v in entry.visits)
        for leg in entry.legs:
            raw_codes.add(leg.origin)
            raw_codes.add(leg.destination)
    alias_map, airport_meta = canonical_airport_groups(db, raw_codes)

    airport_counts: Counter[str] = Counter()
    airport_entry_counts: Counter[str] = Counter()
    airport_departures: Counter[str] = Counter()
    airport_arrivals: Counter[str] = Counter()
    airport_hours: defaultdict[str, float] = defaultdict(float)
    airport_models: defaultdict[str, Counter[str]] = defaultdict(Counter)
    airport_idents: defaultdict[str, Counter[str]] = defaultdict(Counter)
    airport_connections: defaultdict[str, Counter[str]] = defaultdict(Counter)
    airport_first_date: dict[str, date] = {}
    airport_last_date: dict[str, date] = {}
    airport_aliases: defaultdict[str, set[str]] = defaultdict(set)

    route_counts: Counter[tuple[str, str]] = Counter()
    route_hours: defaultdict[tuple[str, str], float] = defaultdict(float)
    route_duration_samples: Counter[tuple[str, str]] = Counter()
    route_duration_estimated: set[tuple[str, str]] = set()
    route_model_counts: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    route_model_hours: defaultdict[tuple[str, str], defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))
    route_ident_counts: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    model_counts: Counter[str] = Counter()
    model_mapped_counts: Counter[str] = Counter()
    model_hours: defaultdict[str, float] = defaultdict(float)
    total_hours = 0.0
    total_landings = 0
    map_entries = 0
    suspicious: list[dict] = []

    for raw, canonical in alias_map.items():
        airport_aliases[canonical].add(raw)
    for canonical, meta in airport_meta.items():
        airport_aliases[canonical].update(meta.get("aliases") or [])

    for entry in entries:
        hours = float(entry.duration_hours or 0.0)
        total_hours += hours
        total_landings += int(entry.landings_day or 0) + int(entry.landings_night or 0)
        if entry.aircraft_model:
            model_counts[entry.aircraft_model] += 1
            model_hours[entry.aircraft_model] += hours

        issue = _entry_route_speed_check(entry, alias_map, airport_meta)
        if issue:
            suspicious.append(issue)
            continue

        visited_codes = []
        for visit in entry.visits:
            code = alias_map.get(visit.airport_code, visit.airport_code)
            if code not in airport_meta:
                continue
            airport_counts[code] += 1
            visited_codes.append(code)
            airport_aliases[code].add(visit.airport_code)
            airport_first_date[code] = min(airport_first_date.get(code, entry.flight_date), entry.flight_date)
            airport_last_date[code] = max(airport_last_date.get(code, entry.flight_date), entry.flight_date)
        for code in set(visited_codes):
            airport_entry_counts[code] += 1
            airport_hours[code] += hours
            if entry.aircraft_model:
                airport_models[code][entry.aircraft_model] += 1
            if entry.aircraft_ident:
                airport_idents[code][entry.aircraft_ident] += 1

        if visited_codes:
            map_entries += 1
            if entry.aircraft_model:
                model_mapped_counts[entry.aircraft_model] += 1
        legs = list(entry.legs)
        share = hours / len(legs) if legs and hours > 0 else 0.0
        for leg in legs:
            origin = alias_map.get(leg.origin, leg.origin)
            destination = alias_map.get(leg.destination, leg.destination)
            if origin not in airport_meta or destination not in airport_meta:
                continue
            if origin == destination:
                continue
            key = (origin, destination)
            route_counts[key] += 1
            airport_departures[origin] += 1
            airport_arrivals[destination] += 1
            airport_connections[origin][destination] += 1
            airport_connections[destination][origin] += 1
            if share > 0:
                route_hours[key] += share
                route_duration_samples[key] += 1
                if len(legs) > 1:
                    route_duration_estimated.add(key)
            if entry.aircraft_model:
                route_model_counts[key][entry.aircraft_model] += 1
                if share > 0:
                    route_model_hours[key][entry.aircraft_model] += share
            if entry.aircraft_ident:
                route_ident_counts[key][entry.aircraft_ident] += 1

    points = []
    for code, visits in airport_counts.most_common():
        a = airport_meta.get(code)
        if not a:
            continue
        points.append({
            "code": code, "aliases": sorted(airport_aliases[code], key=lambda x: (len(x), x)),
            "name": a.get("name"), "city": a.get("city"),
            "lat": a["lat"], "lon": a["lon"], "visits": visits,
            "flights": airport_entry_counts[code],
            "departures": airport_departures[code],
            "arrivals": airport_arrivals[code],
            "hours": round(airport_hours[code], 1),
            "date_first": airport_first_date.get(code).isoformat() if airport_first_date.get(code) else None,
            "date_last": airport_last_date.get(code).isoformat() if airport_last_date.get(code) else None,
            "aircraft_models": [
                {"model": model, "flights": count}
                for model, count in airport_models[code].most_common(10)
            ],
            "registrations": [
                {"ident": ident, "flights": count}
                for ident, count in airport_idents[code].most_common(8)
            ],
            "connections": [
                {
                    "code": other,
                    "city": airport_meta.get(other, {}).get("city"),
                    "name": airport_meta.get(other, {}).get("name"),
                    "flights": count,
                }
                for other, count in airport_connections[code].most_common(10)
            ],
        })

    routes = []
    for (origin, destination), count in route_counts.most_common():
        a = airport_meta.get(origin)
        b = airport_meta.get(destination)
        if not a or not b:
            continue
        key = (origin, destination)
        duration_samples = route_duration_samples[key]
        hours = route_hours[key]
        model_breakdown = [
            {
                "model": model,
                "flights": flights,
                "hours": round(route_model_hours[key][model], 1),
            }
            for model, flights in route_model_counts[key].most_common()
        ]
        ident_breakdown = [
            {"ident": ident, "flights": flights}
            for ident, flights in route_ident_counts[key].most_common(10)
        ]
        routes.append({
            "origin": origin, "destination": destination,
            "origin_city": a.get("city"), "destination_city": b.get("city"),
            "origin_name": a.get("name"), "destination_name": b.get("name"),
            "origin_aliases": sorted(airport_aliases[origin], key=lambda x: (len(x), x)),
            "destination_aliases": sorted(airport_aliases[destination], key=lambda x: (len(x), x)),
            "count": count, "hours": round(hours, 1),
            "duration_samples": duration_samples,
            "average_hours": round(hours / duration_samples, 2) if duration_samples else None,
            "duration_estimated": key in route_duration_estimated,
            "aircraft_models": model_breakdown,
            "registrations": ident_breakdown,
            "origin_lat": a["lat"], "origin_lon": a["lon"],
            "destination_lat": b["lat"], "destination_lon": b["lon"],
        })

    top_models = [
        {"model": model, "flights": count, "mapped": model_mapped_counts[model], "hours": round(model_hours[model], 1)}
        for model, count in model_counts.most_common(12)
    ]
    top_airports = [
        {"code": code, "visits": count, "city": airport_meta.get(code, {}).get("city")}
        for code, count in airport_counts.most_common(12)
    ]
    top_routes = [
        {
            "origin": o, "destination": d,
            "origin_city": airport_meta.get(o, {}).get("city"),
            "destination_city": airport_meta.get(d, {}).get("city"),
            "flights": count,
            "hours": round(route_hours[(o, d)], 1),
            "average_hours": (
                round(route_hours[(o, d)] / route_duration_samples[(o, d)], 2)
                if route_duration_samples[(o, d)] else None
            ),
        }
        for (o, d), count in route_counts.most_common(12)
    ]

    return {
        "filters": {
            "aircraft_models": aircraft_models or [], "aircraft_ident": aircraft_ident,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
        "summary": {
            "entries": len(entries), "mapped_entries": map_entries,
            "unmapped_entries": max(0, len(entries) - map_entries - len(suspicious)),
            "suspicious_entries": len(suspicious),
            "hours": round(total_hours, 1), "landings": total_landings,
            "unique_airports": len(airport_counts), "unique_routes": len(route_counts),
            "date_start": entries[0].flight_date.isoformat() if entries else None,
            "date_end": entries[-1].flight_date.isoformat() if entries else None,
        },
        "airports": points,
        "routes": routes,
        "top_models": top_models,
        "top_airports": top_airports,
        "top_routes": top_routes,
        "suspicious_routes": suspicious[:25],
    }


def build_logbook_map(
    db: Session,
    aircraft_models: list[str] | None = None,
    aircraft_ident: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    models_key = tuple(sorted(m.strip() for m in (aircraft_models or []) if m and m.strip()))
    key = (models_key, (aircraft_ident or "").strip(), start_date.isoformat() if start_date else "", end_date.isoformat() if end_date else "")
    with _CACHE_LOCK:
        cached = _MAP_CACHE.get(key)
        if cached is not None:
            _MAP_CACHE.move_to_end(key)
            return cached
    result = _build_logbook_map_uncached(db, list(models_key) or None, aircraft_ident, start_date, end_date)
    with _CACHE_LOCK:
        _MAP_CACHE[key] = result
        _MAP_CACHE.move_to_end(key)
        while len(_MAP_CACHE) > _MAP_CACHE_LIMIT:
            _MAP_CACHE.popitem(last=False)
    return result


def clear_logbook(db: Session) -> dict:
    count = db.query(LogbookEntry).count()
    db.query(LogbookEntry).delete(synchronize_session=False)
    db.commit()
    invalidate_logbook_caches()
    return {"ok": True, "deleted_entries": count}
