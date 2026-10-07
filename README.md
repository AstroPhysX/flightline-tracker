# Flightline Tracker

**v29** focuses on mobile/PWA polish, a date-by-date Actual-vs-Initial history comparison, and more reliable user-triggered UPS schedule synchronization.

A self-hosted flight tracker and lifetime logbook map for pilots and their families.

Flightline Tracker shows the current trip on a world map, follows live flights with FlightAware AeroAPI, saves the flown track after landing, replays completed flights, imports Logbook Pro history, and can accept UPS Work Schedule / confirmed jumpseat data from the companion Edge extension.

## Highlights

- Live current/upcoming trip map
- FlightAware status, delay, aircraft and position data
- Exact completed tracks saved locally for replay
- English / French / Russian UI
- Light / dark themes, mobile layout and map-only mode
- Immutable awarded schedule alongside edited/current schedule
- Date-by-date **Actual / Current vs Initial Schedule** history comparison
- Automatic rest detection for 10+ hour gaps
- Lifetime logbook map with route/airport statistics and aircraft filters
- Logbook Pro CSV and native `.lbk` import
- 5 replay-weather snapshots on normal flights and up to 7 on long-haul flights
- Admin-only editing while normal viewers need no login
- Persistent data under `/data`
- GitHub → GHCR → Watchtower deployment support
- User-triggered Edge extension for UPS Work Schedule and confirmed jumpseat sync

## v29 status and display changes

- Takeoff/landing symbols are larger and clearer on desktop and mobile.
- Dallas/local clock values stay clean (no `CDT`, `CST`, or `UTC(+5)` suffixes), while labels keep their location context.
- The custom clock now includes the active airport, for example **JEROME TIME - ONT**.
- Airborne status starts with **Flying** while retaining the flight/route and telemetry information.
- While airborne, **Next flight in** becomes **Flight time left** and counts down to the current estimated landing time.
- During the first half of a rest, the Delay / ahead metric keeps the previous flight's landing result (for example **Landed 12 min late**) until useful timing information becomes available for the next flight.
- The local-clock display name remains editable in **Tracker settings** (for example Jerome → Jerry).
- The compact Next Flight card now uses the layout: heading, Dallas date/time, flight + FR24 + route/destination city, and estimated flight time.

## v29 history comparison

The main History view remains a vertical date timeline with parallel **Actual / Current** and **Initial Schedule** lanes, but flights belonging to the same trip are now visually grouped inside a light green trip band with a vertical **Trip / Pair** label. The repetitive full trip name is no longer printed on every individual flight card.

Flight types are color coded: **Operating** is green and **Deadhead** is orange.

When Admin mode is unlocked, History now supports granular cleanup:

- delete an individual Actual / Current flight while preserving its Initial Schedule baseline when one exists;
- delete an individual Initial Schedule item without removing the current/actual flight;
- delete an entire displayed day from both lanes;
- whole-trip delete controls remain under **Saved trip tools**.

Removing an Actual / Current history item also discards its saved provider track, tracker-generated logbook copy, and archived weather for that flight.

## v29 UPS Work Schedule sync

The Edge extension lives in the separate `edge-extension/` directory. The popup is intentionally small and settings remain behind ⚙.

The default v4 sync path is:

1. You manually open a supported UPS page.
2. You explicitly press **Sync**.
3. The extension briefly performs Ctrl+A / Ctrl+C on that active tab through Edge's debugger API, sends Escape to clear selection, and immediately detaches.
4. It parses Zscaler's copied text locally and sends normalized data to your tracker.
5. If that user-triggered copy operation is unavailable, it tries visible-tab OCR; if OCR is uncertain it asks for the manual Ctrl+A / Ctrl+C fallback.

The extension does not automate login/MFA, UPS navigation, search, jumpseat booking, Autobook, schedule adjustments, form submission, cookies, or background UPS requests. One-click text capture can be disabled in extension settings. Managed Edge policy can block or report the debugger permission; the extension does not bypass those controls.

`SCHEDULE_SYNC_TOKEN` is required in Docker/Portainer.

### Complete-coverage deletion safeguard

To establish a complete pay-period snapshot, press Sync once on the Work Schedule calendar, then manually open Time Detail from the first scheduled date and press Sync again. When the two lists agree, the extension sends `coverage_complete=true`.

Only a complete-coverage sync may remove a previously tracked flight that UPS no longer lists. When that happens, Flightline Tracker removes its Actual / Current provider state, positions, tracker-generated logbook copy and archived weather. A PDF-awarded baseline remains intact and visible in **Initial Schedule**.

A partial or date-forward Time Detail sync cannot erase an older tracked leg.

### Confirmed jumpseats

The Crew Jumpseat **Upcoming Jumpseats Confirmed** table can be synced too. Jumpseats are stored as **DH**. The default filter imports only confirmed rows touching DFW; this is configurable. No jumpseat search or booking control is automated.

### Tracking fix retained from v27

UPS internal pages can display `UPS0751` while FlightAware publishes `UPS751`. Flightline Tracker preserves the UPS display number but strips display-only leading zeros for the AeroAPI query.

## PWA

- root-scoped service worker at `/sw.js`;
- explicit manifest id/scope;
- normal and dedicated maskable 192/512 icons;
- current blue-background favicon;
- in-app **Install app** button when Chromium exposes the install prompt.

After changing maskable icons, an already-installed Android PWA may need to be uninstalled/reinstalled once for the launcher to refresh its cached icon.

## Run locally

```bash
./run-local.sh
```

Then open `http://127.0.0.1:8080`.

## Docker / Portainer

The included `docker-compose.yml` expects an image such as:

```text
ghcr.io/YOUR_GITHUB_USER/flightline-tracker:stable
```

Typical variables:

```text
TRACKER_IMAGE=ghcr.io/YOUR_GITHUB_USER/flightline-tracker:stable
TRACKER_PORT=8765
TRACKER_DATA_PATH=/volume1/docker/flightline-tracker/data
ADMIN_PASSWORD=choose-a-strong-admin-password
ADMIN_COOKIE_SECURE=auto
WEATHER_ARCHIVE_MAX_MB=100
SCHEDULE_SYNC_TOKEN=generate-a-long-random-token
```

Keep `/data` mounted persistently. It contains the database, flight history, saved tracks, logbook, settings, schedule-sync snapshots and weather archive.

## Live tracking

The default live polling interval is about 10 minutes while somebody is viewing the map. Going from no viewers to an active viewer triggers one freshness check, with a recent-result guard to avoid API bursts. When nobody is viewing, tracking falls back to a slower cadence. After landing, one final detailed track is saved locally.

## Weather replay

Normal flights retain up to five compact RainViewer snapshots. Flights planned for 8 hours or more retain up to seven, distributed through the flight. The global archive remains bounded by `WEATHER_ARCHIVE_MAX_MB` (default 100 MB).

## GitHub → GHCR → Watchtower

A push to `main` runs the Docker-publish workflow. A separate workflow packages `edge-extension/` into a store-ready ZIP when extension files change.

Typical update:

```bash
git add -A
git commit -m "Flightline Tracker v29"
git push origin main
```

Your persistent `/data` bind mount is reused when Watchtower replaces the application container.

## Data providers

Maps: OpenFreeMap / OpenMapTiles / OpenStreetMap data · Weather: RainViewer · Live tracking: FlightAware AeroAPI when configured.
