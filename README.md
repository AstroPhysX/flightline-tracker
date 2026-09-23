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

It also publishes version tags such as `:v14.0` when you push a Git tag and an immutable commit-SHA tag for rollback/debugging.

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
git tag v14.0
git push origin v14.0
```

That gives you a fixed `:v14.0` image that can be used for rollback even after `:stable` moves forward.

## Watchtower updates

The tracker container has:

```text
com.centurylinklabs.watchtower.enable=true
```

If your existing Watchtower uses label-only mode, it can update the tracker automatically when GHCR publishes a new digest for `:stable`.

For a **public GHCR package**, no registry login is needed. For a **private GHCR package**, Docker/Watchtower must have GHCR credentials. A common setup is to `docker login ghcr.io` with a token that has `read:packages`, then mount/share the resulting Docker `config.json` with Watchtower.

Your `/data` bind mount is not replaced when Watchtower recreates the application container.

## Theme and maps

The browser remembers the selected theme. **Light** intentionally restores the classic v12 appearance: the original dark translucent control panels over the bright map, with the original blue/orange/yellow/grey flight palette. **Dark** uses a softer slate UI and the OpenFreeMap Liberty basemap rather than the nearly-black Dark style so the day/night terminator remains visible.

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
