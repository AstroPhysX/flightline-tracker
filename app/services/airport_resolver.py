from __future__ import annotations

import json
from pathlib import Path

import httpx
try:
    import airportsdata
except ImportError:  # development fallback; requirements install it in the tracker venv
    airportsdata = None
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Airport

# Common UPS / long-haul airports available offline immediately.
SEED_AIRPORTS = {
    "SDF": (38.1744, -85.7360, "America/Kentucky/Louisville", "Louisville Muhammad Ali International", "Louisville"),
    "DFW": (32.8998, -97.0403, "America/Chicago", "Dallas/Fort Worth International", "Dallas"),
    "ANC": (61.1743, -149.9983, "America/Anchorage", "Ted Stevens Anchorage International", "Anchorage"),
    "ICN": (37.4602, 126.4407, "Asia/Seoul", "Incheon International", "Seoul"),
    "CGN": (50.8659, 7.1427, "Europe/Berlin", "Cologne Bonn Airport", "Cologne"),
    "HNL": (21.3187, -157.9225, "Pacific/Honolulu", "Daniel K. Inouye International", "Honolulu"),
    "ONT": (34.0560, -117.6012, "America/Los_Angeles", "Ontario International", "Ontario"),
    "SYD": (-33.9399, 151.1753, "Australia/Sydney", "Sydney Kingsford Smith", "Sydney"),
    "SIN": (1.3644, 103.9915, "Asia/Singapore", "Singapore Changi", "Singapore"),
    "HAN": (21.2212, 105.8072, "Asia/Bangkok", "Noi Bai International", "Hanoi"),
    "DEL": (28.5562, 77.1000, "Asia/Kolkata", "Indira Gandhi International", "Delhi"),
    "PVG": (31.1443, 121.8083, "Asia/Shanghai", "Shanghai Pudong International", "Shanghai"),
    "HKG": (22.3080, 113.9185, "Asia/Hong_Kong", "Hong Kong International", "Hong Kong"),
    "DXB": (25.2532, 55.3657, "Asia/Dubai", "Dubai International", "Dubai"),
    "DWC": (24.8964, 55.1614, "Asia/Dubai", "Al Maktoum International", "Dubai"),
    "FRA": (50.0379, 8.5622, "Europe/Berlin", "Frankfurt Airport", "Frankfurt"),
    "CDG": (49.0097, 2.5479, "Europe/Paris", "Paris Charles de Gaulle", "Paris"),
    "SGN": (10.8188, 106.6519, "Asia/Ho_Chi_Minh", "Tan Son Nhat International", "Ho Chi Minh City"),
    "PHL": (39.8744, -75.2424, "America/New_York", "Philadelphia International", "Philadelphia"),
    "MSP": (44.8848, -93.2223, "America/Chicago", "Minneapolis-Saint Paul International", "Minneapolis"),
    "SJU": (18.4394, -66.0018, "America/Puerto_Rico", "Luis Munoz Marin International", "San Juan"),
    "MIA": (25.7959, -80.2870, "America/New_York", "Miami International", "Miami"),
    "LAX": (33.9416, -118.4085, "America/Los_Angeles", "Los Angeles International", "Los Angeles"),
    "JFK": (40.6413, -73.7781, "America/New_York", "John F. Kennedy International", "New York"),
    "NRT": (35.7720, 140.3929, "Asia/Tokyo", "Narita International", "Tokyo"),
    "KIX": (34.4347, 135.2440, "Asia/Tokyo", "Kansai International", "Osaka"),
    "TPE": (25.0797, 121.2342, "Asia/Taipei", "Taiwan Taoyuan International", "Taipei"),
    "SZX": (22.6393, 113.8107, "Asia/Shanghai", "Shenzhen Bao'an International", "Shenzhen"),
    "BLR": (13.1986, 77.7066, "Asia/Kolkata", "Kempegowda International", "Bengaluru"),
}

REMOTE_URL = "https://raw.githubusercontent.com/mwgg/Airports/master/airports.json"


def _cache_path() -> Path:
    return settings.app_data_dir / "airports-cache.json"


_IATA_AIRPORTS = None
_ICAO_AIRPORTS = None


def _offline_airport(code: str) -> dict | None:
    global _IATA_AIRPORTS, _ICAO_AIRPORTS
    if airportsdata is None:
        return None
    if _IATA_AIRPORTS is None:
        _IATA_AIRPORTS = airportsdata.load("IATA")
    if _ICAO_AIRPORTS is None:
        _ICAO_AIRPORTS = airportsdata.load("ICAO")
    # Current UPS schedules normally use 3-letter IATA codes while historical
    # logbooks commonly use 4-letter ICAO identifiers (KSDF, LFFP, RKSI, etc.).
    # Support both without forcing the logbook importer to rewrite the source data.
    rec = (_ICAO_AIRPORTS.get(code) if len(code) == 4 else None) or _IATA_AIRPORTS.get(code) or _ICAO_AIRPORTS.get(code)
    if not rec:
        return None
    return {
        "lat": rec.get("lat"),
        "lon": rec.get("lon"),
        "tz": rec.get("tz"),
        "name": rec.get("name"),
        "city": rec.get("city"),
    }


def _remote_map() -> dict[str, dict]:
    cache = _cache_path()
    raw = None
    if cache.exists():
        try:
            raw = json.loads(cache.read_text())
        except Exception:
            raw = None
    if raw is None:
        try:
            response = httpx.get(
                REMOTE_URL,
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": "UPS-Family-Flight-Tracker/0.3"},
            )
            response.raise_for_status()
            raw = response.json()
            cache.write_text(json.dumps(raw))
        except Exception:
            return {}

    records = raw.values() if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    result: dict[str, dict] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        iata = str(rec.get("iata") or "").strip().upper()
        if not iata:
            continue
        try:
            lat = float(rec.get("lat"))
            lon = float(rec.get("lon"))
        except (TypeError, ValueError):
            continue
        result[iata] = {
            "lat": lat,
            "lon": lon,
            "tz": rec.get("tz") or rec.get("timezone"),
            "name": rec.get("name"),
            "city": rec.get("city"),
        }
    return result


def ensure_airport(db: Session, code: str) -> Airport:
    code = code.strip().upper()
    existing = db.get(Airport, code)
    if existing:
        return existing

    if code in SEED_AIRPORTS:
        lat, lon, tz, name, city = SEED_AIRPORTS[code]
        airport = Airport(code=code, latitude=lat, longitude=lon, timezone_name=tz, name=name, city=city)
        db.add(airport)
        db.flush()
        return airport

    rec = _offline_airport(code) or _remote_map().get(code)
    if not rec:
        raise ValueError(f"Airport {code} could not be resolved. Check the airport code.")

    airport = Airport(
        code=code,
        latitude=rec["lat"],
        longitude=rec["lon"],
        timezone_name=rec.get("tz"),
        name=rec.get("name"),
        city=rec.get("city"),
    )
    db.add(airport)
    db.flush()
    return airport
