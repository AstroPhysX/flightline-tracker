# Flightline Tracker

**v24** uses the approved blue-background top-down aircraft icon and tightens schedule editing so removed legs stay removed.

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

## Future UPS schedule extension

The server already has a disabled-by-default staging endpoint for a future Edge/Chromium extension. The intended design is conservative: **you sign in to UPS and complete MFA normally**, then the extension reads only the schedule page you are already viewing and sends normalized schedule data to Flightline Tracker for review.

The future extension should not automate login/MFA, read authentication cookies, or silently change the tracker schedule. It should use minimal host permissions and a separate `SCHEDULE_SYNC_TOKEN` for the tracker API.

Because UPS or your employer may have rules restricting browser extensions or automated extraction on internal systems, verify that such use is permitted before deploying the extension against a production UPS site.

## Data providers

Maps: OpenFreeMap · Weather: RainViewer · Live tracking: FlightAware AeroAPI when configured.
