# v27 changes

## FlightAware tracking fix

The supplied `tracker.db` showed the completed DFW→SDF deadhead as `UPS0751`. The tracker had polled it (`last_provider_poll_utc` was populated) but never obtained a `provider_flight_id`, actual times, or positions. A later `UPS76` row did obtain a provider match.

UPS internal pages can keep a leading zero in the flight number while FlightAware uses the canonical designator without it (`UPS0751` → `UPS751`). v27 keeps the UPS number for display but strips display-only leading zeros only when querying AeroAPI. Existing rows can therefore recover on the next eligible provider poll after upgrading.

## Edge extension v3

- compact one-button popup with settings behind ⚙;
- brief supported-page notification and `SYNC` badge;
- Work Schedule / Time Detail sync unchanged in principle: user presses Sync; screenshot first; Ctrl+A/Ctrl+C fallback if needed;
- Crew Jumpseat confirmed rows can be synchronized as DH;
- DFW-only jumpseat filter enabled by default, configurable in settings;
- Test connection button;
- GitHub Action packages a store-ready extension ZIP.

The extension still does not automate login, MFA, UPS navigation, searches, booking, Autobook, form submission, cookies, or background UPS requests.

## PWA / mobile

- root-scoped service worker at `/sw.js`;
- obsolete `/static/` service-worker registration is removed automatically;
- manifest `id`/`scope` and maskable icons;
- current blue-background favicon served at `/favicon.ico` and asset versions bumped to v27;
- in-app Install app button when Chromium exposes the install prompt;
- phone current-trip view hides the large top header, keeps the full status card visible, and uses a small hamburger menu.

## Weather replay

- normal flights: up to 5 archived radar snapshots;
- planned flights 8 hours or longer: up to 7 snapshots;
- global `WEATHER_ARCHIVE_MAX_MB` cap remains in force.
