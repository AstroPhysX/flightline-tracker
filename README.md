# Flightline Tracker

**v26** adds a user-triggered one-click UPS Time Detail screen reader with a conservative Ctrl+A / Ctrl+C fallback, while retaining the v24 schedule-state protections.

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
- Three small radar snapshots per tracked flight for replay context
- Admin-only editing while normal viewers need no login
- Persistent data under `/data`
- GitHub → GHCR → Watchtower deployment support
- Manual-trigger Edge extension for UPS Work Schedule → Current schedule sync


## v26 hybrid UPS Work Schedule sync

The `edge-extension/` folder contains the desktop Microsoft Edge extension. It remains deliberately user-triggered and narrowly scoped:

- no UPS login or MFA automation;
- no content script injected into UPS/Zscaler;
- no cookie access;
- no debugger permission;
- no background polling or automatic clicking;
- no jumpseat or schedule-adjustment automation;
- nothing happens until the user presses **Sync current page**.

Normal workflow: manually open **Time Detail** from the first scheduled flight/date and press **Sync current page**. Edge captures only the visible tab, sends the image to the user's own Flightline Tracker, and the tracker runs Tesseract OCR in memory. The screenshot is not stored. The extension then validates every in-pay-period row before sending normalized schedule data into **Current**.

The screen reader is intentionally conservative. If the full Time Detail table does not fit in the visible viewport, OCR quality is low, or any in-period row cannot be parsed safely, **no schedule change is made**. The extension remembers a short-lived fallback state and asks the user to press **Ctrl+A / Ctrl+C**, reopen the popup, and click **Sync copied text**. The older calendar-plus-Time-Detail copied-text cross-check remains available as an optional fallback.

The full signed Zscaler URL, UPS cookies, MFA information, and employee identity fields are not included in normalized schedule payloads.

On the tracker side, synchronization updates **Current** while preserving PDF **Awarded** rows. Completed/actually-departed legs are not deleted or rewritten by later schedule synchronization.

Enable the receiver with a long random `SCHEDULE_SYNC_TOKEN` in Portainer/Compose, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then enter the same token and tracker URL in the Edge extension. See `edge-extension/README.md` for installation and use.

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

At most three compact RainViewer radar tiles are saved for a tracked flight: beginning, middle and end. The archive has a configurable global cap (`WEATHER_ARCHIVE_MAX_MB`, default 100 MB).

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

v26 uses a conservative hybrid workflow:

1. You manually open UPS **Time Detail** from the first scheduled flight/date.
2. You explicitly click **Sync current page** in the extension.
3. Edge captures only the visible tab and sends that image to your own Flightline Tracker `/api/integrations/ups-schedule/ocr` endpoint.
4. The tracker runs local Tesseract OCR in memory and returns text; it does not save the screenshot.
5. The extension validates every in-pay-period row before sending normalized schedule JSON to `/api/integrations/ups-schedule`.
6. If OCR is unclear or the whole table is not visible, nothing is applied and the extension requests the existing Ctrl+A / Ctrl+C copied-text fallback.

There is no UPS login/MFA automation, no UPS form submission, no cookies/debugger permission, no background polling, and no automated clicking of the UPS site. The integration remains disabled when `SCHEDULE_SYNC_TOKEN` is blank.

The Docker image now includes `tesseract-ocr`, Pillow, and `pytesseract` for the local screenshot reader.

## Data providers

Maps: OpenFreeMap · Weather: RainViewer · Live tracking: FlightAware AeroAPI when configured.
