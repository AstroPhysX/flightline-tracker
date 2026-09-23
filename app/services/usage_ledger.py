from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from ..config import settings

_LOCK = Lock()


def _path() -> Path:
    return settings.app_data_dir / "tracking-local-usage.json"


def _month_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def load(provider: str) -> dict:
    month = _month_key()
    with _LOCK:
        path = _path()
        raw = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text())
            except Exception:
                raw = {}
        entry = ((raw.get(month) or {}).get(provider) or {})
        return {
            "month": month,
            "provider": provider,
            "estimated_spend_usd": round(float(entry.get("estimated_spend_usd", 0.0)), 4),
            "calls": dict(entry.get("calls") or {}),
        }


def add(provider: str, call_name: str, estimated_cost_usd: float) -> dict:
    month = _month_key()
    with _LOCK:
        path = _path()
        raw = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text())
            except Exception:
                raw = {}
        month_data = raw.setdefault(month, {})
        entry = month_data.setdefault(provider, {"estimated_spend_usd": 0.0, "calls": {}})
        entry["estimated_spend_usd"] = round(float(entry.get("estimated_spend_usd", 0.0)) + float(estimated_cost_usd), 4)
        calls = entry.setdefault("calls", {})
        calls[call_name] = int(calls.get(call_name, 0)) + 1
        path.write_text(json.dumps(raw, indent=2))
        return {
            "month": month,
            "provider": provider,
            "estimated_spend_usd": entry["estimated_spend_usd"],
            "calls": dict(calls),
        }


def can_spend(provider: str, budget_usd: float, next_cost_usd: float) -> bool:
    if budget_usd <= 0:
        return False
    current = load(provider)["estimated_spend_usd"]
    return current + next_cost_usd <= float(budget_usd) + 1e-9


def reconcile_minimum(provider: str, actual_cost_usd: float) -> dict:
    """Ensure the local guard is never below provider-reported usage."""
    month = _month_key()
    with _LOCK:
        path = _path()
        raw = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text())
            except Exception:
                raw = {}
        month_data = raw.setdefault(month, {})
        entry = month_data.setdefault(provider, {"estimated_spend_usd": 0.0, "calls": {}})
        entry["estimated_spend_usd"] = round(max(float(entry.get("estimated_spend_usd", 0.0)), float(actual_cost_usd or 0.0)), 4)
        path.write_text(json.dumps(raw, indent=2))
        return {
            "month": month, "provider": provider,
            "estimated_spend_usd": entry["estimated_spend_usd"],
            "calls": dict(entry.get("calls") or {}),
        }
