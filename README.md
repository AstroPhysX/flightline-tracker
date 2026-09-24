# Flightline Tracker

A self-hosted flight tracker and lifetime logbook map built for pilots and their families.

It can display a current trip on a world map, follow live flights through FlightAware AeroAPI, preserve the exact flown track after landing, replay completed flights, import Logbook Pro history, and automatically add completed operating flights to the lifetime map.

## Highlights

- Current / upcoming trip map with actual flown track
- FlightAware live status, delay, aircraft and position data
- Exact completed tracks saved locally for replay
- Light / dark themes and English / French / Russian UI
- Mobile-friendly layout and map-only mode
- Awarded vs. current/rebuilt schedule views
- Automatic rest detection for gaps of 10 hours or more
- Lifetime logbook map with route frequency, airport statistics and aircraft filters
- Logbook Pro `.csv` and native `.lbk` import
- Three lightweight weather snapshots per tracked flight for replay context
- Persistent SQLite data under `/data`
- Admin-only editing without requiring visitors to log in
- Docker / Portainer / GHCR / Watchtower friendly

## Run locally

Python 3.12+ is recommended.

```bash
./run-local.sh
```

The script creates a project-local `.venv`; it does not install Python packages system-wide.

Then open:

```text
http://127.0.0.1:8080
```

CSV logbook import works everywhere. Native `.lbk` import uses `mdbtools`; the Docker image already includes it. If you want `.lbk` import while running directly on Linux, install `mdbtools` through your operating system first.

## Docker / Portainer

The included `docker-compose.yml` expects a published image such as:

```text
ghcr.io/YOUR_GITHUB_USER/flightline-tracker:stable
```

Copy `.env.example` to `.env` or define the same variables in Portainer:

```text
TRACKER_IMAGE=ghcr.io/YOUR_GITHUB_USER/flightline-tracker:stable
TRACKER_PORT=8765
TRACKER_DATA_PATH=/volume1/docker/flightline-tracker/data
ADMIN_PASSWORD=choose-a-strong-admin-password
ADMIN_COOKIE_SECURE=auto
WEATHER_ARCHIVE_MAX_MB=100
```

Then deploy:

```bash
docker compose up -d
```

The web app will be available on the host port you selected, for example:

```text
http://NAS-IP:8765
```

Use an HTTPS reverse proxy before exposing the tracker to the internet.

## Persistent data

Everything that should survive container updates lives under `/data`, including:

- SQLite database
- trip and flight history
- saved AeroAPI tracks
- imported logbook history
- tracking settings
- admin-session secrets
- database backups
- the small replay-weather archive

Keep `/data` mounted to a persistent Synology folder when replacing or auto-updating the container.

## GitHub → GHCR → Watchtower

The repository includes `.github/workflows/docker-publish.yml`.

A push to `main` works like this:

```text
git push
   ↓
GitHub Actions builds the Docker image
   ↓
GHCR publishes flightline-tracker:stable
   ↓
Watchtower sees a new image digest
   ↓
Synology container is replaced
   ↓
the same /data directory is mounted again
```

For a normal release:

```bash
git add -A
git commit -m "Describe the update"
git push origin main
```

If your GHCR package is public, Portainer and Watchtower can pull it without GitHub registry credentials.

## FlightAware usage

The tracker is designed to reduce AeroAPI spending:

- no paid query merely because the page was opened
- slower status polling when nobody is watching
- live position polling only when useful
- one final detailed track fetch after landing
- historical replay uses the locally saved track and does not query AeroAPI again

The default local monthly guard is `$4.50` and can be changed in Admin → Tracking API.

## Weather replay

Flightline Tracker stores at most **three** small RainViewer radar snapshots for a tracked flight:

1. near the beginning
2. around the middle
3. near the end / landing

The archive has a default global cap of **100 MB**. Older v18/v19 hourly snapshots are automatically reduced to three representative frames per flight after upgrading to v20.

Day/night replay is calculated from the saved timestamps and requires no stored imagery.

## Logbook Pro

History → Import logbook accepts:

- Logbook Pro CSV exports
- native `.lbk` Access/JET database files

SIM entries are ignored. Re-importing a newer complete logbook updates existing records instead of intentionally duplicating them. Airport aliases such as `DFW/KDFW` and `ANC/PANC` are collapsed to one physical airport on the map.

## License / data providers

Map tiles/styles: OpenFreeMap. Weather radar: RainViewer. Live tracking can use FlightAware AeroAPI when configured by the administrator.
