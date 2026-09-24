from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import settings
from . import usage_ledger

PROVIDERS = {
    "disabled": "Disabled",
    "aeroapi": "FlightAware AeroAPI",
    "fr24": "Flightradar24 API",
    "adsbx": "ADS-B Exchange",
}
ENV_KEYS = {
    "aeroapi": "AEROAPI_KEY",
    "fr24": "FR24_API_TOKEN",
    "adsbx": "ADSBX_API_KEY",
}
FILE_ENV_KEYS = {provider: f"{env_name}_FILE" for provider, env_name in ENV_KEYS.items()}
DEFAULTS = {
    "provider": "disabled",
    "api_keys": {},
    "poll_seconds": 600,
    "monthly_budget_usd": 4.50,
    "public_delay_minutes": 10,
    "local_clock_name": "Jerome",
}


def settings_path() -> Path:
    return settings.app_data_dir / "tracking-settings.json"


def load() -> dict:
    cfg = dict(DEFAULTS)
    cfg["api_keys"] = {}
    path = settings_path()
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            if isinstance(raw, dict):
                cfg.update(raw)
                cfg["api_keys"] = dict(raw.get("api_keys") or {})
                # Migrate the v3 single-key format.
                old_key = str(raw.get("api_key") or "").strip()
                old_provider = str(raw.get("provider") or "").strip()
                if old_key and old_provider in ENV_KEYS and old_provider not in cfg["api_keys"]:
                    cfg["api_keys"][old_provider] = old_key
        except Exception:
            pass

    for provider, env_name in ENV_KEYS.items():
        file_env = FILE_ENV_KEYS[provider]
        file_path = os.getenv(file_env, "").strip()
        if file_path:
            try:
                value = Path(file_path).read_text().strip()
            except OSError:
                value = ""
            if value:
                cfg["api_keys"][provider] = value
                continue
        value = os.getenv(env_name, "").strip()
        if value:
            cfg["api_keys"][provider] = value
    return cfg


def key_for(provider: str, cfg: dict | None = None) -> str:
    cfg = cfg or load()
    return str((cfg.get("api_keys") or {}).get(provider, "")).strip()


def save(provider: str, api_key: str | None, poll_seconds: int, monthly_budget_usd: float, public_delay_minutes: int = 10, local_clock_name: str = "Jerome") -> dict:
    cfg = load()
    cfg["provider"] = provider
    # Preserve only credentials that were explicitly saved in /data. Environment
    # or Docker-secret credentials are runtime-only and are never copied into the
    # JSON settings file merely because an admin changed another setting.
    keys = {}
    path = settings_path()
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            keys = dict(raw.get("api_keys") or {}) if isinstance(raw, dict) else {}
        except Exception:
            keys = {}
    if api_key is not None and api_key.strip() and provider in ENV_KEYS:
        keys[provider] = api_key.strip()
    cfg["api_keys"] = keys
    cfg["poll_seconds"] = max(60, int(poll_seconds))
    cfg["monthly_budget_usd"] = max(0.0, float(monthly_budget_usd))
    cfg["public_delay_minutes"] = max(0, min(120, int(public_delay_minutes)))
    cfg["local_clock_name"] = (local_clock_name or "Jerome").strip()[:40] or "Jerome"

    path = settings_path()
    path.write_text(json.dumps(cfg, indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return cfg


def public(cfg: dict | None = None) -> dict:
    cfg = cfg or load()
    provider = cfg.get("provider", "disabled")
    usage = usage_ledger.load(provider) if provider in ENV_KEYS else {"estimated_spend_usd": 0.0, "calls": {}}
    budget = float(cfg.get("monthly_budget_usd", 4.50))
    return {
        "provider": provider,
        "provider_label": PROVIDERS.get(provider, provider),
        "providers": PROVIDERS,
        "api_key_set": bool(key_for(provider, cfg)) if provider in ENV_KEYS else False,
        "poll_seconds": int(cfg.get("poll_seconds", 600)),
        "monthly_budget_usd": budget,
        "public_delay_minutes": int(cfg.get("public_delay_minutes", 10)),
        "local_clock_name": str(cfg.get("local_clock_name") or "Jerome"),
        "local_estimated_spend_usd": float(usage.get("estimated_spend_usd", 0.0)),
        "local_estimated_remaining_usd": max(0.0, budget - float(usage.get("estimated_spend_usd", 0.0))),
        "local_call_counts": usage.get("calls", {}),
        "implementation": {
            "aeroapi": "live-polling-ready",
            "fr24": "configuration-ready",
            "adsbx": "configuration-ready",
        },
    }


def account_usage_cache_path() -> Path:
    return settings.app_data_dir / "aeroapi-account-usage.json"


def aeroapi_account_usage(*, refresh: bool = False) -> dict:
    """Return cached/real FlightAware month-to-date usage.

    FlightAware says the source itself updates about every 10 minutes, so avoid
    repeatedly calling even though the account endpoint is priced at $0.00.
    """
    cfg = load()
    key = key_for("aeroapi", cfg)
    if not key:
        return {"available": False, "error": "No AeroAPI key configured"}
    path = account_usage_cache_path()
    now = datetime.now(timezone.utc)
    if path.exists() and not refresh:
        try:
            cached = json.loads(path.read_text())
            fetched = datetime.fromisoformat(str(cached.get("fetched_at") or "").replace("Z", "+00:00"))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            if now - fetched <= timedelta(minutes=10):
                return cached
        except Exception:
            pass
    from .aeroapi import get_account_usage, AeroApiError
    try:
        payload = get_account_usage(key)
    except AeroApiError as exc:
        return {"available": False, "error": str(exc)}
    result = {
        "available": True,
        "fetched_at": now.isoformat(),
        "total_calls": int(payload.get("total_calls") or 0),
        "total_pages": int(payload.get("total_pages") or 0),
        "total_cost": float(payload.get("total_cost") or 0.0),
        "discounted_total_cost": float(payload.get("total_discount_cost") or 0.0),
        "resource_details": list(payload.get("resource_details") or []),
    }
    try:
        path.write_text(json.dumps(result, indent=2))
    except OSError:
        pass
    # The budget guard uses list-price consumption because that is the safest
    # way to stay inside the Personal plan's free monthly allowance.
    usage_ledger.reconcile_minimum("aeroapi", result["total_cost"])
    return result
