from __future__ import annotations

import io
import re
from typing import Any

from fastapi import HTTPException
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError
import pytesseract

MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 30_000_000

_FLIGHT_DATE_RE = re.compile(r"\b[A-Z0-9-]+-\d{1,2}/\d{1,2}/\d{2}\b", re.I)


def _prepare_image(raw: bytes) -> Image.Image:
    if not raw:
        raise HTTPException(400, "Empty screenshot")
    if len(raw) > MAX_SCREENSHOT_BYTES:
        raise HTTPException(413, "Schedule screenshot is too large")

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(400, "The uploaded file is not a readable image") from exc

    width, height = image.size
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise HTTPException(400, "Schedule screenshot dimensions are not supported")

    # Zscaler's rendered UPS pages use small, high-contrast table text. A mild
    # grayscale/autocontrast/resize pass improves Tesseract without altering
    # layout enough to break row grouping.
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image)
    image = ImageEnhance.Contrast(image).enhance(1.25)
    if width < 2400:
        scale = min(1.5, 2400 / max(width, 1))
        image = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
    return image


def _quality(text: str) -> int:
    score = 0
    lowered = text.lower()
    if "time detail" in lowered:
        score += 6
    if "upcoming jumpseats confirmed" in lowered:
        score += 8
    if "search flights" in lowered and "jumpseat" in lowered:
        score += 3
    if "pairing detail for pay period" in lowered:
        score += 8
    if "schd out" in lowered or "schdout" in lowered:
        score += 2
    if "domicile" in lowered:
        score += 2
    if "trip summary" in lowered:
        score += 2
    score += min(12, len(_FLIGHT_DATE_RE.findall(text)))
    return score


def _classify(text: str) -> str:
    lowered = text.lower()
    if "time detail" in lowered or "pairing detail for pay period" in lowered:
        return "time_detail"
    if "unofficial schedule" in lowered or "crew access roster has official schedule" in lowered:
        return "calendar"
    if "upcoming jumpseats confirmed" in lowered or ("jumpseat" in lowered and "search flights" in lowered):
        return "jumpseat"
    return "unknown"


def ocr_schedule_screenshot(raw: bytes) -> dict[str, Any]:
    image = _prepare_image(raw)

    # PSM 6 is consistently strongest for this UPS table because it preserves
    # each visual table row as one text line. If the result looks weak, retry
    # with sparse-text mode and keep the stronger result.
    first = pytesseract.image_to_string(image, config="--oem 3 --psm 6", lang="eng")
    candidates = [first]
    if _quality(first) < 16:
        candidates.append(pytesseract.image_to_string(image, config="--oem 3 --psm 11", lang="eng"))

    text = max(candidates, key=_quality)
    score = _quality(text)
    page_type = _classify(text)

    lowered = text.lower()
    complete_view = (
        (page_type == "time_detail"
         and "pairing detail for pay period" in lowered
         and ("attention crewmember" in lowered or "copyright" in lowered))
        or
        (page_type == "jumpseat"
         and ("upcoming jumpseats standby" in lowered or "no standby jumpseats" in lowered))
    )

    return {
        "ok": True,
        "page_type": page_type,
        "quality_score": score,
        "flight_row_candidates": len(_FLIGHT_DATE_RE.findall(text)),
        "complete_view": complete_view,
        "text": text,
    }
