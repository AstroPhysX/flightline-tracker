from __future__ import annotations

import shutil
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine, get_db
from .migrations import ensure_schema_extensions
from .models import Flight, Trip, LogbookEntry
from .schemas import ManualTripCreate, ManualFlightCreate, TrackingSettingsUpdate, FlightScheduleUpdate, ScheduleRemoveRequest, ViewerHeartbeat, AdminLoginRequest
from .services.aeroapi import AeroApiError, test_connection as test_aeroapi_connection
from .services.airport_resolver import ensure_airport
from .services.dashboard import build_dashboard, select_trip
from .services.schedule_service import resequence_trip, snapshot_awarded, mark_added_after_award, deactivate_from
from .services import tracker_settings, tracking_worker, viewer_presence, admin_auth
from .services.flight_timing import timing_summary
from .services.database_backup import backup_database
from .services.ups_pdf_import import import_awarded_line_pdfs
from .services.logbook_service import import_logbook_csv, logbook_options, build_logbook_map, clear_logbook, backfill_completed_tracker_flights

BASE_DIR = Path(__file__).resolve().parent
backup_database()
Base.metadata.create_all(bind=engine)
ensure_schema_extensions(engine)

app = FastAPI(title="UPS Family Flight Tracker", version="1.3.0")


@app.on_event("startup")
def _start_tracking_worker():
    generated = admin_auth.ensure_admin_password()
    if generated:
        print("\n" + "=" * 72)
        print("UPS TRACKER ADMIN PASSWORD GENERATED (shown only on first creation):")
        print(generated)
        print("Save this password. Only its salted hash is stored under /data.")
        print("=" * 72 + "\n")
    # Backfill any already-completed operating flights from older tracker
    # versions into the lifetime logbook map. This is idempotent.
    from .db import SessionLocal
    db = SessionLocal()
    try:
        backfill_completed_tracker_flights(db)
    finally:
        db.close()
    tracking_worker.start()


@app.on_event("shutdown")
def _stop_tracking_worker():
    tracking_worker.stop()
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["timing_summary"] = timing_summary


@app.get("/health")
def health():
    return {"ok": True, "version": "1.3.0"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/history", response_class=HTMLResponse)
def history(request: Request, db: Session = Depends(get_db)):
    trips = db.query(Trip).order_by(Trip.start_date.desc(), Trip.id.desc()).all()
    count, hours, first_date, last_date = (
        db.query(
            func.count(LogbookEntry.id),
            func.coalesce(func.sum(LogbookEntry.duration_hours), 0.0),
            func.min(LogbookEntry.flight_date),
            func.max(LogbookEntry.flight_date),
        )
        .filter(func.upper(func.coalesce(LogbookEntry.aircraft_ident, "")) != "SIM")
        .filter(~func.upper(func.coalesce(LogbookEntry.aircraft_model, "")).like("%SIM%"))
        .one()
    )
    logbook_summary = {
        "count": int(count or 0), "hours": round(float(hours or 0.0), 1),
        "first_date": first_date, "last_date": last_date,
    }
    return templates.TemplateResponse(request=request, name="history.html", context={"trips": trips, "logbook_summary": logbook_summary})


@app.get("/logbook", response_class=HTMLResponse)
def logbook_page(request: Request):
    return templates.TemplateResponse(request=request, name="logbook.html", context={})


@app.get("/api/logbook/options")
def api_logbook_options(db: Session = Depends(get_db)):
    return logbook_options(db)


@app.get("/api/logbook/map")
def api_logbook_map(
    aircraft_model: str | None = None,
    aircraft_ident: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    db: Session = Depends(get_db),
):
    return build_logbook_map(
        db, aircraft_model=aircraft_model or None, aircraft_ident=aircraft_ident or None,
        start_date=start_date, end_date=end_date,
    )


@app.post("/api/logbook/import")
async def api_logbook_import(
    logbook_csv: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin: None = Depends(admin_auth.require_admin),
):
    if not logbook_csv.filename or not logbook_csv.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Choose a Logbook Pro CSV export.")
    raw = await logbook_csv.read()
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(413, "The logbook CSV is unexpectedly large.")
    try:
        return import_logbook_csv(db, raw)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/logbook")
def api_logbook_clear(
    db: Session = Depends(get_db),
    _admin: None = Depends(admin_auth.require_admin),
):
    return clear_logbook(db)


@app.get("/api/dashboard")
def dashboard(request: Request, trip_id: int | None = None, view: str = "current", db: Session = Depends(get_db)):
    delay = 0
    if not admin_auth.is_admin(request):
        delay = int(tracker_settings.load().get("public_delay_minutes", 10))
    return build_dashboard(db, trip_id=trip_id, view=view, public_delay_minutes=delay)


@app.post("/api/viewers/heartbeat")
def viewer_heartbeat(req: ViewerHeartbeat):
    return {"ok": True, "active_viewers": viewer_presence.heartbeat(req.viewer_id)}


@app.get("/api/settings/tracking/usage")
def tracking_usage(refresh: bool = False, _admin: None = Depends(admin_auth.require_admin)):
    return tracker_settings.aeroapi_account_usage(refresh=refresh)


@app.get("/api/admin/status")
def admin_status(request: Request):
    status = admin_auth.session_status(request)
    return {
        **status,
        "session_minutes": settings.admin_session_minutes,
        "remember_days": 30,
    }


@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest, request: Request, response: Response):
    admin_auth.check_login_rate_limit(request)
    if not admin_auth.verify_password(req.password):
        admin_auth.record_failed_login(request)
        raise HTTPException(401, "Incorrect admin password")
    admin_auth.clear_failed_logins(request)
    expires_in = admin_auth.create_session(response, remember=req.remember)
    return {"ok": True, "authenticated": True, "remembered": req.remember, "expires_in_seconds": expires_in}


@app.post("/api/admin/logout")
def admin_logout(request: Request, response: Response):
    admin_auth.destroy_session(request, response)
    return {"ok": True, "authenticated": False}


@app.post("/api/import-awarded-line-pdfs")
def import_awarded_line_files(
    _admin: None = Depends(admin_auth.require_admin),
    line_number: int = Form(...),
    lines_pdf: UploadFile = File(...),
    trips_pdf: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not lines_pdf.filename or not trips_pdf.filename:
        raise HTTPException(400, "Both the Lines PDF and Trips PDF are required.")
    if not lines_pdf.filename.lower().endswith(".pdf") or not trips_pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Both uploaded files must be PDFs.")

    with tempfile.TemporaryDirectory(prefix="ups-tracker-import-") as tmp:
        lines_path = Path(tmp) / "lines.pdf"
        trips_path = Path(tmp) / "trips.pdf"
        with lines_path.open("wb") as f:
            shutil.copyfileobj(lines_pdf.file, f)
        with trips_path.open("wb") as f:
            shutil.copyfileobj(trips_pdf.file, f)

        try:
            imported = import_awarded_line_pdfs(
                db,
                lines_path,
                trips_path,
                int(line_number),
            )
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, f"Could not import awarded line: {exc}") from exc

    if not imported:
        raise HTTPException(400, "The line was found but no trackable trip flights were extracted.")
    return {
        "ok": True,
        "line_number": int(line_number),
        "trips_imported": len(imported),
        "trip_ids": [t.id for t in imported],
        "names": [t.name for t in imported],
    }


@app.post("/api/manual-trip")
def create_manual_trip(req: ManualTripCreate, db: Session = Depends(get_db), _admin: None = Depends(admin_auth.require_admin)):
    trip = Trip(name=req.name.strip(), active=True, source="manual")
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return {"ok": True, "trip_id": trip.id, "name": trip.name}


@app.post("/api/manual-flight")
def manual_flight(req: ManualFlightCreate, db: Session = Depends(get_db), _admin: None = Depends(admin_auth.require_admin)):
    trip = db.get(Trip, req.trip_id) if req.trip_id else select_trip(db)
    if req.trip_id and not trip:
        raise HTTPException(404, "Trip not found")
    if not trip:
        trip = Trip(name=req.trip_name, active=True, source="manual")
        db.add(trip)
        db.flush()

    try:
        ensure_airport(db, req.origin)
        ensure_airport(db, req.destination)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc

    sequence = max((f.sequence for f in trip.flights), default=0) + 1
    flight = Flight(
        trip_id=trip.id,
        sequence=sequence,
        flight_number=req.flight_number.upper().strip(),
        flight_date=req.flight_date,
        origin=req.origin.upper().strip(),
        destination=req.destination.upper().strip(),
        deadhead=req.deadhead,
        scheduled_departure_utc=req.scheduled_departure_utc,
        scheduled_arrival_utc=req.scheduled_arrival_utc,
        schedule_active=True,
    )
    db.add(flight)
    db.flush()
    if trip.source == "ups_pdf":
        mark_added_after_award(flight)
    resequence_trip(db, trip)
    db.commit()
    db.refresh(flight)
    return {"flight_id": flight.id, "trip_id": trip.id}


@app.get("/api/schedule")
def get_schedule(trip_id: int | None = None, db: Session = Depends(get_db), _admin: None = Depends(admin_auth.require_admin)):
    trip = select_trip(db, trip_id)
    if not trip:
        return {"trip": None, "flights": []}
    rows = sorted([f for f in trip.flights if f.schedule_active or f.actual_departure_utc or f.actual_arrival_utc], key=lambda f: (f.sequence if f.sequence > 0 else 9999, f.flight_date, f.id))
    return {
        "trip": {"id": trip.id, "name": trip.name, "has_awarded_baseline": any(f.awarded_flight_number for f in trip.flights)},
        "flights": [{
            "id": f.id, "sequence": f.sequence, "flight_number": f.flight_number,
            "flight_date": f.flight_date.isoformat(), "origin": f.origin, "destination": f.destination,
            "deadhead": f.deadhead, "scheduled_departure_utc": f.scheduled_departure_utc.isoformat() if f.scheduled_departure_utc else None,
            "scheduled_arrival_utc": f.scheduled_arrival_utc.isoformat() if f.scheduled_arrival_utc else None,
            "schedule_active": f.schedule_active, "schedule_added": f.schedule_added,
            "has_awarded_baseline": bool(f.awarded_flight_number), "change_note": f.schedule_change_note,
            "actual_departure_utc": f.actual_departure_utc.isoformat() if f.actual_departure_utc else None,
            "actual_arrival_utc": f.actual_arrival_utc.isoformat() if f.actual_arrival_utc else None,
        } for f in rows]
    }


@app.put("/api/flight/{flight_id}/schedule")
def update_flight_schedule(flight_id: int, req: FlightScheduleUpdate, db: Session = Depends(get_db), _admin: None = Depends(admin_auth.require_admin)):
    flight = db.get(Flight, flight_id)
    if not flight:
        raise HTTPException(404, "Flight not found")
    if flight.actual_departure_utc:
        raise HTTPException(400, "A flight that has already departed cannot have its schedule identity rewritten. Add a replacement leg instead if needed.")
    try:
        ensure_airport(db, req.origin)
        ensure_airport(db, req.destination)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    snapshot_awarded(flight)
    flight.flight_number = req.flight_number.upper().strip()
    flight.flight_date = req.flight_date
    flight.origin = req.origin.upper().strip()
    flight.destination = req.destination.upper().strip()
    flight.deadhead = req.deadhead
    flight.scheduled_departure_utc = req.scheduled_departure_utc
    flight.scheduled_arrival_utc = req.scheduled_arrival_utc
    flight.schedule_active = True
    flight.schedule_change_note = req.note or "Schedule edited manually"
    resequence_trip(db, flight.trip)
    db.commit()
    return {"ok": True, "flight_id": flight.id}


@app.post("/api/flight/{flight_id}/remove-from-schedule")
def remove_from_schedule(flight_id: int, req: ScheduleRemoveRequest, db: Session = Depends(get_db), _admin: None = Depends(admin_auth.require_admin)):
    flight = db.get(Flight, flight_id)
    if not flight:
        raise HTTPException(404, "Flight not found")
    if flight.actual_departure_utc or flight.actual_arrival_utc:
        raise HTTPException(400, "Already-flown legs are kept in the actual history and cannot be removed from it.")
    trip = flight.trip
    snapshot_awarded(flight)
    if req.remove_later_flights:
        removed = deactivate_from(db, trip, flight, include_selected=True)
    else:
        if (flight.schedule_added or trip.source == "manual") and not flight.awarded_flight_number:
            db.delete(flight)
            db.flush()
        else:
            flight.schedule_active = False
            flight.schedule_change_note = "Removed from current schedule"
        resequence_trip(db, trip)
        removed = 1
    db.commit()
    return {"ok": True, "removed": removed}


@app.post("/api/flight/{flight_id}/restore-awarded")
def restore_awarded(flight_id: int, db: Session = Depends(get_db), _admin: None = Depends(admin_auth.require_admin)):
    flight = db.get(Flight, flight_id)
    if not flight or not flight.awarded_flight_number:
        raise HTTPException(404, "No awarded baseline is available for this flight")
    if flight.actual_departure_utc:
        raise HTTPException(400, "Already-flown legs are not rewritten.")
    flight.flight_number = flight.awarded_flight_number
    flight.flight_date = flight.awarded_flight_date
    flight.origin = flight.awarded_origin
    flight.destination = flight.awarded_destination
    flight.deadhead = bool(flight.awarded_deadhead)
    flight.scheduled_departure_utc = flight.awarded_scheduled_departure_utc
    flight.scheduled_arrival_utc = flight.awarded_scheduled_arrival_utc
    flight.schedule_active = True
    flight.schedule_added = False
    flight.schedule_change_note = None
    resequence_trip(db, flight.trip)
    db.commit()
    return {"ok": True}


@app.post("/api/schedule/restore-awarded")
def restore_full_awarded_schedule(trip_id: int | None = None, db: Session = Depends(get_db), _admin: None = Depends(admin_auth.require_admin)):
    trip = select_trip(db, trip_id)
    if not trip:
        raise HTTPException(404, "Trip not found")

    restored = 0
    removed_added = 0
    has_awarded = False
    for flight in db.query(Flight).filter(Flight.trip_id == trip.id).all():
        if flight.awarded_flight_number:
            has_awarded = True
            if flight.actual_departure_utc or flight.actual_arrival_utc:
                continue
            flight.flight_number = flight.awarded_flight_number
            flight.flight_date = flight.awarded_flight_date
            flight.origin = flight.awarded_origin
            flight.destination = flight.awarded_destination
            flight.deadhead = bool(flight.awarded_deadhead)
            flight.scheduled_departure_utc = flight.awarded_scheduled_departure_utc
            flight.scheduled_arrival_utc = flight.awarded_scheduled_arrival_utc
            flight.schedule_active = True
            flight.schedule_added = False
            flight.schedule_change_note = None
            restored += 1
        elif flight.schedule_added and not flight.actual_departure_utc and not flight.actual_arrival_utc:
            removed_added += 1
            db.delete(flight)

    if not has_awarded:
        raise HTTPException(404, "No awarded baseline is available for this trip")
    resequence_trip(db, trip)
    db.commit()
    return {"ok": True, "restored": restored, "removed_added": removed_added}


@app.delete("/api/trip/{trip_id}/history")
def delete_trip_history(trip_id: int, db: Session = Depends(get_db), _admin: None = Depends(admin_auth.require_admin)):
    trip = db.get(Trip, trip_id)
    if not trip:
        raise HTTPException(404, "Trip not found")
    name = trip.name
    db.delete(trip)
    db.commit()
    return {"ok": True, "trip_id": trip_id, "name": name}


@app.get("/api/settings/tracking")
def get_tracking_settings(_admin: None = Depends(admin_auth.require_admin)):
    return tracker_settings.public()


@app.post("/api/settings/tracking")
def save_tracking_settings(req: TrackingSettingsUpdate, _admin: None = Depends(admin_auth.require_admin)):
    provider = req.provider.strip().lower()
    if provider not in tracker_settings.PROVIDERS:
        raise HTTPException(400, "Unsupported tracking provider")
    cfg = tracker_settings.save(provider, req.api_key, req.poll_seconds, req.monthly_budget_usd, req.public_delay_minutes)
    return tracker_settings.public(cfg)


@app.post("/api/settings/tracking/sync-now")
def sync_tracking_now(_admin: None = Depends(admin_auth.require_admin)):
    result = tracking_worker.run_once(force=True)
    return {"ok": True, **result, "settings": tracker_settings.public()}


@app.post("/api/settings/tracking/test")
def test_tracking_settings(_admin: None = Depends(admin_auth.require_admin)):
    cfg = tracker_settings.load()
    provider = cfg.get("provider")
    if provider == "aeroapi":
        try:
            return test_aeroapi_connection(tracker_settings.key_for("aeroapi", cfg))
        except AeroApiError as exc:
            raise HTTPException(400, str(exc)) from exc
    if provider in {"fr24", "adsbx"}:
        if not tracker_settings.key_for(provider, cfg):
            raise HTTPException(400, "No API credential is configured for this provider.")
        raise HTTPException(
            501,
            "Credential saved. Live/test calls for this provider are intentionally not enabled in this build yet, so the tracker will not spend API quota unexpectedly.",
        )
    raise HTTPException(400, "Select a tracking provider first.")


@app.post("/api/flight/{flight_id}/mark-departed")
def mark_departed(flight_id: int, db: Session = Depends(get_db), _admin: None = Depends(admin_auth.require_admin)):
    flight = db.get(Flight, flight_id)
    if not flight:
        raise HTTPException(404, "Flight not found")
    flight.actual_departure_utc = datetime.now(timezone.utc)
    flight.status = "airborne"
    db.commit()
    return {"ok": True}


@app.post("/api/flight/{flight_id}/mark-arrived")
def mark_arrived(flight_id: int, db: Session = Depends(get_db), _admin: None = Depends(admin_auth.require_admin)):
    flight = db.get(Flight, flight_id)
    if not flight:
        raise HTTPException(404, "Flight not found")
    flight.actual_arrival_utc = datetime.now(timezone.utc)
    flight.status = "arrived"
    db.commit()
    return {"ok": True}
