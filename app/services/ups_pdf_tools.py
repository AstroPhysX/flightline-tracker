from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from . import ups_master_lines as master
from . import ups_pdf_extractors as extractors


def extract_awarded_line_from_pdf(lines_pdf_path: str | Path, line_number: int, first_calendar_page: int = 3) -> dict:
    """Parse only enough of the Lines package to find one awarded line."""
    wanted = int(line_number)
    first_index = first_calendar_page - 1

    with pdfplumber.open(lines_pdf_path) as pdf:
        if first_index >= len(pdf.pages):
            raise ValueError(f"Lines PDF has only {len(pdf.pages)} pages; calendar page {first_calendar_page} does not exist.")

        metadata_text = pdf.pages[first_index].extract_text(x_tolerance=1, y_tolerance=2) or ""
        bid_start, bid_end = extractors.parse_bid_range(metadata_text)
        domicile = extractors.parse_domicile(metadata_text)
        package = extractors.build_package_metadata(bid_start, bid_end)

        for page in pdf.pages[first_index:]:
            for parsed_line in extractors.parse_line_report_page(page, bid_start, domicile):
                if int(parsed_line.get("line_number")) == wanted:
                    package["lines"] = [parsed_line]
                    return package

    raise ValueError(f"Line {wanted} was not found in the Lines package.")


def trip_ids_for_line(line_package: dict) -> list[int]:
    ids: list[int] = []
    for line in line_package.get("lines", []):
        for pp in line.get("pay_periods", []):
            for assignment in pp.get("assignments", []):
                if assignment.get("type") == "trip" and assignment.get("value") is not None:
                    trip_id = int(assignment["value"])
                    if trip_id not in ids:
                        ids.append(trip_id)
    return ids


def extract_selected_trips_from_pdf(
    trips_pdf_path: str | Path,
    trip_ids: list[int] | set[int],
    first_page: int = 2,
) -> dict[int, dict]:
    """Extract only the Trip IDs used by the awarded line, then stop."""
    wanted = {int(x) for x in trip_ids}
    found: dict[int, dict] = {}
    if not wanted:
        return found

    with pdfplumber.open(trips_pdf_path) as pdf:
        for page in pdf.pages[max(0, first_page - 1):]:
            anchors = extractors.find_trip_anchors(page)
            if not anchors:
                continue
            crops = extractors.make_trip_crops(page)
            for anchor, bbox in zip(anchors, crops):
                trip_id = int(anchor["trip_id"])
                if trip_id not in wanted or trip_id in found:
                    continue
                text = page.crop(bbox).extract_text(x_tolerance=1, y_tolerance=3) or ""
                if "Trip Id:" not in text:
                    continue
                parsed = extractors.parse_trip_text(text)
                found[int(parsed["trip_id"])] = parsed
            if wanted.issubset(found):
                break

    missing = sorted(wanted.difference(found))
    if missing:
        raise ValueError("Trips package is missing required Trip ID(s): " + ", ".join(map(str, missing)))
    return found


def normalize_flight_number(raw: str | None) -> tuple[str | None, bool, bool]:
    """Return (tracking ident, deadhead, bus).

    Ordinary numeric UPS flights are normalized from e.g. ``067`` to ``UPS67``.
    ``DH AA194`` becomes ``AA194`` and is marked deadhead. BUS is kept as a
    non-trackable ground movement.
    """
    text = str(raw or "").upper().strip()
    if not text:
        return None, False, False

    deadhead = text.startswith("DH ")
    if deadhead:
        text = text[3:].strip()

    if "BUS" in text:
        return "BUS", True, True

    if re.fullmatch(r"(?:SBA|SBG)\s*\d*", text):
        return None, deadhead, False

    # The Trips package can append a segment marker such as ``076-2``.
    # That suffix is not part of the airline flight designator; AeroAPI should
    # receive UPS76, with date/origin/destination used to identify the leg.
    numeric = re.fullmatch(r"(\d+)(?:-\d+)?", text)
    if numeric:
        return f"UPS{int(numeric.group(1))}", deadhead, False

    compact = re.sub(r"\s+", "", text)
    compact = re.sub(r"-\d+$", "", compact)
    return compact, deadhead, False


def duration_minutes(value: str | None) -> int | None:
    if not value or value == "-":
        return None
    m = re.fullmatch(r"(\d+)h(\d{2})[A-Z]?", str(value).strip(), re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def build_awarded_line(lines_pdf_path: str | Path, trips_pdf_path: str | Path, line_number: int) -> dict:
    bid_period = extractors.matching_bid_period(str(lines_pdf_path), str(trips_pdf_path))
    if bid_period is None:
        raise ValueError("The Lines and Trips PDFs do not have the same Bid Period.")

    line_package = extract_awarded_line_from_pdf(lines_pdf_path, line_number)
    trip_ids = trip_ids_for_line(line_package)
    trips = extract_selected_trips_from_pdf(trips_pdf_path, trip_ids)
    master_lines = master.creating_master_line(trips, line_package)

    line_key = int(line_number)
    line_data = master_lines.get(line_key) or master_lines.get(str(line_key))
    if line_data is None:
        raise ValueError(f"Could not create master data for line {line_number}.")

    assignments: list[dict] = []
    for pp in line_data.get("PPs", []):
        for assignment in pp.get("assignments", []):
            if assignment.get("trip_id") is None or assignment.get("error") or not assignment.get("flights"):
                continue
            flights = []
            for record in assignment.get("flights", []):
                ident, deadhead, is_bus = normalize_flight_number(record.get("flight"))
                if not ident:
                    continue
                flights.append({
                    **record,
                    "flight_number": ident,
                    "deadhead": deadhead,
                    "is_bus": is_bus,
                    "rest_minutes": duration_minutes(record.get("rest")),
                })
            if flights:
                assignments.append({**assignment, "flights": flights})

    return {
        "bid_period": str(bid_period),
        "line_number": line_key,
        "trip_ids": trip_ids,
        "assignments": assignments,
    }
