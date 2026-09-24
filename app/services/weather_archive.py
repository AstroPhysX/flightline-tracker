from __future__ import annotations

import json
import math
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..config import settings
from ..models import Flight

RAINVIEWER_META = "https://api.rainviewer.com/public/weather-maps.json"
ARCHIVE_INTERVAL = timedelta(minutes=30)
ARCHIVE_ZOOM = 4


def _root() -> Path:
    path = settings.app_data_dir / "weather"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _flight_dir(flight_id: int) -> Path:
    path = _root() / str(int(flight_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path(flight_id: int) -> Path:
    return _flight_dir(flight_id) / "index.json"


def _load_index(flight_id: int) -> list[dict]:
    path = _index_path(flight_id)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
        return list(raw) if isinstance(raw, list) else []
    except Exception:
        return []


def _save_index(flight_id: int, rows: list[dict]) -> None:
    _index_path(flight_id).write_text(json.dumps(rows, indent=2))


def _tile_xy(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n) % n
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    y = max(0, min(n - 1, y))
    return x, y


def _tile_bounds(x: int, y: int, zoom: int) -> dict:
    n = 2 ** zoom
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0

    def lat_for(tile_y: int) -> float:
        v = math.pi * (1 - 2 * tile_y / n)
        return math.degrees(math.atan(math.sinh(v)))

    north = lat_for(y)
    south = lat_for(y + 1)
    return {"south": south, "west": west, "north": north, "east": east}


def archive_for_flight(flight: Flight) -> dict | None:
    """Archive one compact RainViewer radar tile near an airborne aircraft.

    This intentionally stores one low-resolution tile no more often than every
    30 minutes. It is replay context, not a complete meteorological archive.
    Any network/weather failure is non-fatal and should never affect tracking.
    """
    if not flight.actual_departure_utc or flight.actual_arrival_utc:
        return None
    if flight.current_latitude is None or flight.current_longitude is None:
        return None

    now = datetime.now(timezone.utc)
    rows = _load_index(flight.id)
    if rows:
        try:
            last = datetime.fromisoformat(str(rows[-1].get("captured_utc") or "").replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < ARCHIVE_INTERVAL:
                return None
        except Exception:
            pass

    try:
        meta_res = httpx.get(RAINVIEWER_META, timeout=12.0, follow_redirects=True, headers={"User-Agent": "Flightline-Tracker/1.8"})
        meta_res.raise_for_status()
        payload = meta_res.json()
        frames = list((payload.get("radar") or {}).get("past") or [])
        frame = frames[-1] if frames else None
        host = payload.get("host")
        if not host or not frame or not frame.get("path"):
            return None
        frame_time = int(frame.get("time") or payload.get("generated") or now.timestamp())
        if rows and any(int(r.get("radar_time") or 0) == frame_time for r in rows[-3:]):
            return None

        x, y = _tile_xy(float(flight.current_latitude), float(flight.current_longitude), ARCHIVE_ZOOM)
        url = f"{host}{frame['path']}/256/{ARCHIVE_ZOOM}/{x}/{y}/2/1_1.png"
        tile_res = httpx.get(url, timeout=15.0, follow_redirects=True, headers={"User-Agent": "Flightline-Tracker/1.8"})
        tile_res.raise_for_status()
        ctype = (tile_res.headers.get("content-type") or "").lower()
        if "image" not in ctype and not tile_res.content.startswith(b"\x89PNG"):
            return None

        filename = f"{frame_time}_z{ARCHIVE_ZOOM}_{x}_{y}.png"
        path = _flight_dir(flight.id) / filename
        path.write_bytes(tile_res.content)
        row = {
            "captured_utc": now.isoformat(),
            "radar_time": frame_time,
            "radar_time_utc": datetime.fromtimestamp(frame_time, tz=timezone.utc).isoformat(),
            "aircraft_lat": float(flight.current_latitude),
            "aircraft_lon": float(flight.current_longitude),
            "zoom": ARCHIVE_ZOOM,
            "x": x,
            "y": y,
            "bounds": _tile_bounds(x, y, ARCHIVE_ZOOM),
            "file": filename,
            "bytes": len(tile_res.content),
        }
        rows.append(row)
        _save_index(flight.id, rows)
        return row
    except Exception:
        return None


def list_snapshots(flight_id: int) -> list[dict]:
    result = []
    for row in _load_index(flight_id):
        filename = str(row.get("file") or "")
        if not filename or "/" in filename or "\\" in filename:
            continue
        path = _flight_dir(flight_id) / filename
        if not path.exists():
            continue
        result.append({**row, "url": f"/api/flight/{int(flight_id)}/weather-replay/{filename}"})
    return result


def snapshot_file(flight_id: int, filename: str) -> Path | None:
    if not filename or Path(filename).name != filename or not filename.lower().endswith(".png"):
        return None
    path = _flight_dir(flight_id) / filename
    return path if path.exists() and path.is_file() else None


def delete_flight_weather(flight_id: int) -> None:
    path = _root() / str(int(flight_id))
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
