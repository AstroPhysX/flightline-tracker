# Flightline Tracker

**v27** adds a simplified UPS sync extension, confirmed-jumpseat import as DH, a fix for zero-padded UPS flight numbers in FlightAware tracking, improved PWA/mobile behavior, and richer weather replay.

A self-hosted flight tracker and lifetime logbook map for pilots and their families.

Flightline Tracker shows the current trip on a world map, follows live flights with FlightAware AeroAPI, saves the exact flown track after landing, replays completed flights, and imports Logbook Pro history.

## Highlights

- Live current/upcoming trip map
- FlightAware status, delay, aircraft and position data
- Exact completed tracks saved locally for replay
- English / French / Russian UI
- Light / dark themes, mobile layout and map-only mode
- Awarded vs. edited/current schedule views
- Automatic rest detection for 10+ hour gaps
- Lifetime logbook map with route/airport statistics and aircraft filters
- Logbook Pro CSV and native `.lbk` import
- 5 replay-weather snapshots on normal flights and up to 7 on long-haul flights
- Admin-only editing while normal viewers need no login
- Persistent data under `/data`
- GitHub → GHCR → Watchtower deployment support
- Manual-trigger Edge extension for UPS Work Schedule → Current schedule sync


## v27 UPS Work Schedule + Jumpseat sync

The `edge-extension/` folder contains the desktop Edge extension. The normal popup is intentionally small: one Sync button plus a hidden settings menu. A background service worker only recognizes supported tabs and can show a brief “ready to sync” notification; it does not scrape or interact with UPS automatically.

Schedule sync remains screen-capture-first with a Ctrl+A / Ctrl+C fallback if OCR is incomplete or uncertain. Confirmed jumpseats can now be synchronized from the Crew Jumpseat screen and are stored as deadheads. The default filter imports only rows touching DFW; this can be changed in extension settings.

The extension never automates login/MFA, search, jumpseat booking, Autobook, schedule adjustments, form submission, UPS cookies, or background UPS requests. Nothing is sent until the user presses Sync.

`SCHEDULE_SYNC_TOKEN` is still required in Docker/Portainer.

### Tracking fix for zero-padded UPS identifiers

UPS internal pages may display a flight such as `UPS0751` while FlightAware publishes the provider designator as `UPS751`. v27 preserves the UPS display number in the schedule but removes display-only leading zeros when querying AeroAPI. This fixes a failure mode where the tracker repeatedly polled a real flight but could not match it.

### PWA / phone / icons

- service worker is now served from `/sw.js` with root scope so the main app is actually controlled by the PWA service worker;
- manifest adds explicit scope/id and maskable icons;
- favicon/PWA asset versions are bumped and `/favicon.ico` serves the current blue-background icon;
- Chromium can show an in-app **Install app** button when installation is available;
- on phones the large top bar is replaced by a small hamburger menu and the full status panel stays visible.

## v24 schedule-state fix

`Current` now means exactly **the legs that are currently in your schedule**. FlightAware data can add live/actual information to a current leg, but it can no longer make a removed flight reappear.

When upgrading an older database, v24 automatically repairs legacy schedule state:

- removed manual/replacement legs are cleaned out rather than kept as hidden tombstones;
- provider tracks/actual times attached to an already-removed leg are discarded;
- manual trips no longer inherit a fake "awarded" baseline;
- active legs are renumbered chronologically;
- the tracking worker only polls active flights.

If you remove a leg that already has FlightAware tracking data, the editor now allows it after a warning and discards that leg's saved provider track/actual times. PDF-awarded rows can still remain internally as the separate Awarded baseline.

## Run locally

```bash
./run-local.sh
```

Then open `http://127.0.0.1:8080`.

The script creates a project-local `.venv`; it does not install Python packages system-wide.

## Docker / Portainer

The included `docker-compose.yml` expects an image such as:

```text
ghcr.io/YOUR_GITHUB_USER/flightline-tracker:stable
```

Typical Portainer variables:

```text
TRACKER_IMAGE=ghcr.io/YOUR_GITHUB_USER/flightline-tracker:stable
TRACKER_PORT=8765
TRACKER_DATA_PATH=/volume1/docker/flightline-tracker/data
ADMIN_PASSWORD=choose-a-strong-admin-password
ADMIN_COOKIE_SECURE=auto
WEATHER_ARCHIVE_MAX_MB=100
SCHEDULE_SYNC_TOKEN=generate-a-long-random-token
```

Keep `/data` mounted to persistent storage. It contains the database, flight history, saved tracks, logbook, settings and weather snapshots, so container updates do not erase your history.

## Live tracking behavior

The default live polling interval is about 10 minutes while somebody is viewing the map.

When the site goes from **no viewers → at least one viewer**, Flightline Tracker requests one fresh provider update. If the current/next flight was already refreshed within the previous 10 minutes, it reuses that data instead. Refreshing the browser therefore cannot create a burst of paid API calls.

When nobody is viewing, tracking falls back to a slower cadence. After landing, one final detailed track is saved locally so replay/history no longer needs AeroAPI.

## Weather replay

Normal flights keep up to five compact RainViewer replay snapshots; flights planned for 8 hours or more keep up to seven, distributed through the flight. The archive still uses the configurable global cap (`WEATHER_ARCHIVE_MAX_MB`, default 100 MB).

The live Weather on/off preference is saved separately in each browser and defaults to **On**.

## GitHub → GHCR → Watchtower

A push to `main` runs `.github/workflows/docker-publish.yml`:

```text
git push
  ↓
GitHub Actions builds the Docker image
  ↓
GHCR publishes flightline-tracker:stable
  ↓
Watchtower sees the new image digest
  ↓
Synology replaces the container
  ↓
the same /data directory is mounted again
```

For a normal release:

```bash
git add -A
git commit -m "Describe the update"
git push origin main
```

## UPS schedule extension

The schedule-sync receiver is active when `SCHEDULE_SYNC_TOKEN` is configured. The Edge extension remains in the separate `edge-extension/` directory in this repository.

v27 uses a conservative user-triggered workflow:

1. You manually open UPS **Time Detail** from the first scheduled flight/date.
2. You explicitly click **Sync current page** in the extension.
3. Edge captures only the visible tab and sends that image to your own Flightline Tracker `/api/integrations/ups-schedule/ocr` endpoint.
4. The tracker runs local Tesseract OCR in memory and returns text; it does not save the screenshot.
5. The extension validates every in-pay-period row before sending normalized schedule JSON to `/api/integrations/ups-schedule`.
6. If OCR is unclear or the whole table is not visible, nothing is applied and the extension requests the existing Ctrl+A / Ctrl+C copied-text fallback.

There is no UPS login/MFA automation, no UPS form submission, no cookies/debugger permission, no background polling, and no automated clicking of the UPS site. The integration remains disabled when `SCHEDULE_SYNC_TOKEN` is blank.

The Docker image includes `tesseract-ocr`, Pillow, and `pytesseract` for local screenshot reading. The extension ZIP can be produced by `.github/workflows/package-edge-extension.yml` for Edge Add-ons or manual testing.

## Data providers

Maps: OpenFreeMap · Weather: RainViewer · Live tracking: FlightAware AeroAPI when configured.
