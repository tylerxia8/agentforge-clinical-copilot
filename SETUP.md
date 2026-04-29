# AgentForge — Local Development Setup

This is a forked OpenEMR for the AgentForge Clinical Co-Pilot project.
The instructions below get you from a fresh clone to a running OpenEMR with
demo data in roughly 5 minutes (on first boot — subsequent boots are seconds).

## Requirements

- **Docker Desktop** (Windows/Mac) or Docker Engine 24+ (Linux)
- 4 GB free RAM, 6 GB free disk
- Ports `8080`, `8081`, `8443`, `3306` available on the host

> Windows note: the bind mount in `docker-compose.yml` works on Docker Desktop
> with WSL2 enabled. If file sync feels slow, see the troubleshooting section
> below.

## First-time setup

From the repository root (where `docker-compose.yml` lives):

```bash
docker compose up -d
docker compose logs -f openemr
```

The first boot does the heavy lifting inside the container:

1. `composer install` — pulls ~200 MB of PHP dependencies
2. `npm install && npm run build` — builds frontend assets
3. Auto-installs the OpenEMR schema into MariaDB
4. Creates the `admin` user
5. Generates OAuth2/JWT keypairs into the `sites/` volume

You'll see `OpenEMR is ready` in the logs when it's done. Tail `Ctrl-C` and
open http://localhost:8080.

| Service    | URL                         | Credentials                  |
|------------|-----------------------------|------------------------------|
| OpenEMR    | http://localhost:8080       | `admin` / `pass`             |
| phpMyAdmin | http://localhost:8081       | `root` / `root`              |
| MariaDB    | `localhost:3306` (internal) | `openemr` / `openemr`        |

## Loading demo patient data

OpenEMR ships demo data via the in-app admin. After login:

1. **Administration → Other → External Data Loads**
2. Pick a demo dataset and click `Install`

Alternatively, the `contrib/util/installScripts/InstallerAuto.php` script can
seed a clean install scriptably — useful for CI / fresh tear-downs.

## Common operations

```bash
# Watch logs (boot can take 5+ min on first start)
docker compose logs -f openemr

# Run an arbitrary command inside the container
docker compose exec openemr bash

# Re-run composer/npm after editing dependencies
docker compose exec openemr composer install
docker compose exec openemr npm install && docker compose exec openemr npm run build

# Reset everything (DELETES the database)
docker compose down -v
docker compose up -d

# Restart just the app, keep the DB
docker compose restart openemr
```

## Troubleshooting

**"Composer install fails / out of memory"** — bump Docker Desktop's memory
allowance to at least 4 GB (Settings → Resources).

**"OpenEMR keeps restarting"** — almost always the DB isn't healthy yet on
first boot. Tail `docker compose logs mysql`; the `openemr` service has a
`depends_on: condition: service_healthy` guard but slow disks can still race.

**"Permission denied" on `sites/`** — Linux only. Set the owner to UID 100:
`sudo chown -R 100:100 sites/`.

**Slow file sync on Windows** — the bind mount on `c:/Users` paths can be
sluggish under Docker Desktop. Move the repo into your WSL2 home (e.g.
`\\wsl$\Ubuntu\home\you\openemr`) for ~10x faster I/O.

## What lives where

| Path                    | What it is                                      |
|-------------------------|-------------------------------------------------|
| `interface/`            | Legacy PHP page tier (Smarty templates, jQuery) |
| `src/`                  | Modern PSR-4 OpenEMR namespace (services, FHIR) |
| `library/`              | Pre-namespace helpers, still widely used        |
| `apis/` + `_rest_routes.inc.php` | REST + FHIR API entry points          |
| `oauth2/`               | OAuth2 / SMART-on-FHIR server                   |
| `sites/default/`        | Per-tenant config + uploads (volume-mounted)    |
| `sql/`                  | Schema + migrations                             |
| `docker-compose.yml`    | Local dev stack (added by AgentForge)           |
| `AUDIT.md`              | Codebase audit findings (Stage 3 deliverable)   |
| `USERS.md`              | Target user + use cases (Stage 4 deliverable)   |
| `ARCHITECTURE.md`       | AI integration plan (Stage 5 deliverable)       |
