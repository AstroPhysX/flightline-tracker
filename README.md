# Flightline Tracker

Self-hosted family flight tracker for UPS trips, live AeroAPI tracking, detailed flown-track history, and a lifetime Logbook Pro map.

## What is in the repository

```text
.github/workflows/docker-publish.yml   Build/publish the production image to GHCR
app/                                   FastAPI application and web UI
Dockerfile                             Production container image
requirements.txt                       Python dependencies
docker-compose.yml                     Synology/Portainer production stack
.env.example                           Example stack variables
run-local.sh                           Isolated local Linux test launcher
README.md                              This file
```

Runtime data is **not** part of Git. Keep `/data` persistent. It contains the SQLite database, flight history, saved AeroAPI tracks, settings, logbook archive, admin-session secret, and backups.

## Local development

```bash
./run-local.sh
```

Then open `http://127.0.0.1:8080`.

The script creates `.venv` inside the repository and refuses to install packages unless it is using that isolated Python environment.

## Production with Portainer / Synology

The production image is expected at:

```text
ghcr.io/YOUR_GITHUB_USER/flightline-tracker:stable
```

Create a Portainer stack from `docker-compose.yml` and set these variables:

```text
TRACKER_IMAGE=ghcr.io/YOUR_GITHUB_USER/flightline-tracker:stable
TRACKER_PORT=8765
TRACKER_DATA_PATH=/volume1/docker/flightline-tracker/data
ADMIN_PASSWORD=choose-a-strong-password
```

The application remains on port `8080` inside the container. `TRACKER_PORT` controls the NAS-side port. Point your HTTPS reverse proxy at `NAS-IP:TRACKER_PORT`.

If the tracker is exposed to the internet, keep HTTPS enabled at the reverse proxy. Production Compose sets secure admin cookies.

## GitHub / GHCR in plain English

There are three different things:

1. **GitHub repository** — stores the source code you edit and `git push`.
2. **GitHub Actions** — a cloud build machine that reads the repository and builds the Docker image.
3. **GHCR (GitHub Container Registry)** — stores the finished Docker image that the Synology actually runs.

Think of it as:

```text
source code in GitHub
        ↓
GitHub Actions builds it
        ↓
GHCR stores the finished container
        ↓
Watchtower downloads that container to the NAS
```

The included `.github/workflows/docker-publish.yml` runs whenever `main` is updated. For a repository named `flightline-tracker`, it publishes:

```text
ghcr.io/YOUR_GITHUB_USER/flightline-tracker:stable
```

It also publishes version tags such as `:v18.0` when you push a Git tag and an immutable commit-SHA tag for rollback/debugging.

### First-time GHCR setup

1. Copy this project into your cloned `flightline-tracker` repository.
2. Commit and push to `main`.
3. Open the GitHub repository → **Actions** and wait for **Build and publish tracker image** to finish successfully.
4. GitHub will create a container package named `flightline-tracker` under your account's **Packages** section.
5. In Portainer set:

```text
TRACKER_IMAGE=ghcr.io/YOUR_GITHUB_USER/flightline-tracker:stable
```

6. Deploy the stack once manually. After that, Watchtower can monitor that exact image tag and replace the tracker whenever the `stable` image digest changes.

The workflow publishes using GitHub's built-in `GITHUB_TOKEN`; you do not need to create a publishing password for GitHub Actions.

GHCR packages are private by default. The easiest first deployment is to make the container package public. If you keep it private, the NAS/Watchtower must authenticate to `ghcr.io` with a GitHub token that can read packages.

### Normal update workflow

```bash
./run-local.sh                 # test
git add -A
git commit -m "Describe change"
git push origin main
```

After the push:

```text
GitHub Actions → new :stable image → GHCR → Watchtower → NAS
```

Do not use `main` as a scratch branch once Watchtower is auto-deploying `:stable`. Use a development/feature branch for unfinished changes.

### Optional release tag

Once a version is known-good:

```bash
git tag v18.0
git push origin v18.0
```

That gives you a fixed `:v18.0` image that can be used for rollback even after `:stable` moves forward.

## Watchtower updates

The tracker container has:

```text
com.centurylinklabs.watchtower.enable=true
```

If your existing Watchtower uses label-only mode, it can update the tracker automatically when GHCR publishes a new digest for `:stable`.

For a **public GHCR package**, no registry login is needed. For a **private GHCR package**, Docker/Watchtower must have GHCR credentials. A common setup is to `docker login ghcr.io` with a token that has `read:packages`, then mount/share the resulting Docker `config.json` with Watchtower.

Your `/data` bind mount is not replaced when Watchtower recreates the application container.

## Theme and maps

The browser remembers the selected theme. **Light** keeps the classic translucent Flightline UI but now uses OpenFreeMap **Liberty**, which is the map palette preferred during testing. **Dark** uses the darker OpenFreeMap **Fiord** basemap: clearly darker than Liberty without returning to the nearly-black style that made the day/night terminator hard to see.

Dark Reader is locked out because the application manages its own route colors and theme.

On the lifetime logbook map, reciprocal connections (`A→B` and `B→A`) are combined into one `A ↔ B` connection. Rare routes are hairline-thin; frequently flown connections progressively become thicker and more opaque. Airport markers use a contrasting blue/cyan fill rather than white-on-white circles.

Click a logbook connection to see:

- times flown
- total route time
- average flight time
- direction counts
- aircraft-type breakdown
- most-used registrations

If a Logbook Pro record contains several legs but only one total duration, the per-leg average is explicitly marked as an estimate.


## v16 map interaction and rest inference

- Manual/rebuilt schedules automatically mark a **rest stop** when the gap from one scheduled arrival to the next scheduled departure is **10 hours or more**. The destination gets the existing star marker, and the schedule editor shows the calculated rest duration.
- The lifetime logbook map uses wide invisible hit targets so very thin routes remain easy to click.
- Clicking a route dims unrelated routes/airports and highlights only that connection and its endpoints. Clicking an airport highlights every connection associated with that location. Click empty map space to restore the full map.
- Airport popups show visits, flights touching the airport, arrivals/departures, logged hours, first/last visit, aircraft breakdown, registrations, and most-used connections.
- Aircraft filtering supports selecting **multiple types at once**.
- A **Map only** control hides the UI overlays on both current-trip and lifetime-logbook maps.

### Historical weather

Flightline Tracker now supports lightweight replay weather. For recent flights, replay can use RainViewer's short public historical window directly. For future tracked flights, v18 also saves one low-resolution radar tile near the aircraft about every 30 minutes while airborne. These snapshots live under persistent `/data/weather/<flight_id>/` and are used only for replay context; the tracker does **not** archive an entire global radar mosaic. A 12-hour flight normally produces about 24 small PNG snapshots. Day/night geometry is still reconstructed mathematically from the saved track timestamps and requires no stored imagery.

## Admin cookie / reverse-proxy behavior

`ADMIN_COOKIE_SECURE` defaults to `auto`. In automatic mode Flightline Tracker issues a Secure admin cookie when the browser is using HTTPS (including through a reverse proxy that sends `X-Forwarded-Proto: https`) and a normal HttpOnly cookie when testing directly over `http://NAS-IP:port`. You can still force `true` or `false`, but normally leave it at `auto`.

If upgrading from an older stack that explicitly sets `ADMIN_COOKIE_SECURE: "true"`, either remove that line or change it to `auto`; otherwise direct HTTP testing will repeatedly ask for the admin password because browsers correctly refuse to send a Secure cookie over HTTP.


## v17 live-status and replay refinements

- Flight numbers on the current map link to FlightAware, with a nearby Flightradar24 link. UPS ICAO identifiers such as `UPS2998` are translated to the FR24/IATA form `5X2998` for that link.
- The live card now keeps FlightAware runway scheduled OFF/ON times and provider delay values separate from the UPS/PDF schedule. Before a flight it shows the last landing and next takeoff; airborne it shows actual takeoff and expected landing; after landing it shows the landing and next takeoff.
- Completed deadheads remain dashed instead of becoming solid grey routes.
- Leg-number markers are clickable and show route, timing, aircraft/registration, external tracking links, and saved-track replay when available.
- Saved AeroAPI tracks can be replayed entirely in the browser with no new AeroAPI calls; the historical day/night terminator follows the saved track timestamps.
- Map palette: Liberty is now the normal/light basemap; Fiord is the darker basemap.
- The B747-specific aircraft-filter shortcut was removed; multi-select remains available for any combination of aircraft types.


## v18 airport/history cleanup

- Logbook airport identities are canonicalized by physical location. IATA/ICAO duplicates such as `DFW`/`KDFW` and `ANC`/`PANC` collapse to one map node while the popup retains the aliases.
- Old logbook routes that resolve to geography requiring more than 900 kt average groundspeed are treated as suspicious code/source collisions. They remain in lifetime totals but are hidden from the geographic map; the original Logbook Pro text is never rewritten.
- Airport codes shown throughout the current map, schedule editor, history, logbook route popups, and top-route summaries include city/location names where available.
- The ground status now says **On ground at …** rather than “Last scheduled stop.”
- The old elapsed rest timer is now a live **Next flight in** countdown to the next planned/provider-adjusted takeoff.
- Current-leg popups show scheduled duration plus a typical historical flight time derived from the imported logbook when samples exist.
- Replay uses saved radar snapshots when available and falls back to RainViewer's recent public archive when the replay timestamp is still inside that window.
