# Betrieb & Deployment

- **Hauptdokument:** Abschnitt 12 ([Betrieb & Deployment](../meadow-pim-architektur.md))
- **Relevante Phasen:** Phase 8
- **Stand:** Konkretisiert für das erste Produktions-Deployment (VPS, example.com); Umsetzung erfolgt in Phase 8

Alle Komponenten werden als Docker-Container betrieben. Die Standardauslieferung erfolgt als **Docker-Compose-Setup**. Konkretes Ziel für das erste Deployment ist der **VPS unter `example.com`**, auf dem bereits Traefik als Reverse Proxy läuft. Die Deployment-Organisation folgt dabei dem erprobten VPS-Muster von **the predecessor project** (`deploy-vps-*.sh`, `.env.vps`, Traefik-Labels, Healthchecks, Backup/Restore). Eine spätere Orchestrierung über Kubernetes (z. B. k3s) ist vorbereitet, aber nicht Bestandteil des MVP.

## 1. Zielumgebung: VPS (example.com)

- **Server:** VPS; betreibt bereits mehrere Stacks unter `example.com` (u. a. the predecessor project).
- **Traefik:** läuft dort als eigenständiger Stack (Reverse Proxy) mit TLS-Terminierung via Let’s Encrypt (certresolver `letsencrypt`).
- **Externes Docker-Netz:** `traefik-net` — gemeinsames Netz zwischen Traefik und allen öffentlich gerouteten Stacks (the predecessor project nutzt dasselbe Netz; das Netz existiert daher bereits).
- **Meadow:** eigener Compose-Stack (`meadow`-Namensraum), der sich über das externe Netz `traefik-net` an Traefik anbindet.
- **Muster:** Skript-Reihe, Compose-Dateien und Env-Handling analog the predecessor project (`deploy-vps-chub.sh`, `docker-compose.vps.yml`, `.env.vps`).

## 2. DNS & öffentliche Routen (Traefik)

| Hostname                          | Compose-Service | Container-Port | Zweck                                  |
|-----------------------------------|-----------------|----------------|----------------------------------------|
| `meadow.pim.example.com`        | `pim-frontend`  | 8081           | Web-Oberfläche (NiceGUI)               |
| `meadow.api.example.com`        | `pim-core`      | 8080           | REST-API (inkl. OpenAPI-Dokumentation) |

- **DNS:** A-Records für beide Subdomains auf die IP des VPS.
- **TLS:** ausschließlich am Traefik terminiert; beide Router über Entrypoint `websecure` + certresolver `letsencrypt`.
- **Redirect:** HTTP → HTTPS über eine gemeinsame Middleware (`meadow-https-redirect`), analog the predecessor project.
- **Nicht öffentlich routen:** `surrealdb`, `nats`, `object-storage`, `redis` und `pim-worker` bleiben ausschließlich im internen Netz.

## 3. Stack-Architektur (docker-compose.vps.yml)

### 3.1 Netzwerke

- `traefik-net` (external) — nur an `pim-frontend` und `pim-core` (Traefik-Routing). Name über `TRAEFIK_NETWORK` parametrisiert.
- `meadow-backend` (bridge, intern) — an alle Dienste.

### 3.2 Dienste

| Service           | Image                                        | Ports (intern)            | Netzwerke                       | Traefik                        |
|-------------------|----------------------------------------------|---------------------------|---------------------------------|--------------------------------|
| `pim-core`        | `${PIM_REGISTRY}/pim-core:${PIM_CORE_TAG}`   | 8080                      | traefik-net + meadow-backend  | `meadow.api.example.com`     |
| `pim-frontend`    | `${PIM_REGISTRY}/pim-frontend:${PIM_FRONTEND_TAG}` | 8081                | traefik-net + meadow-backend  | `meadow.pim.example.com`     |
| `pim-worker`      | `${PIM_REGISTRY}/pim-worker:${PIM_WORKER_TAG}` | –                        | meadow-backend                  | nein                           |
| `surrealdb`       | `surrealdb/surrealdb:latest`                 | 8000                      | meadow-backend                  | nein                           |
| `nats`            | `nats:latest` (JetStream)                    | 4222, 8222 (Monitoring)   | meadow-backend                  | nein                           |
| `object-storage`  | `minio/minio:latest`                         | 9000 (S3), 9001 (Console) | meadow-backend                  | nein                           |
| `redis` (optional)| `redis:7-alpine`                             | 6379                      | meadow-backend                  | nein                           |

- Container-Namen mit `meadow-`-Präfix (z. B. `meadow-core`, `meadow-frontend`, `meadow-surrealdb`), um auf dem gemeinsamen Server Kollisionen zu vermeiden.
- **Kommunikation (intern):** Frontend → Core: REST (JWT) · Worker → Core: REST (JWT) · Core → NATS: Publish · Worker → NATS: Subscribe · Core → SurrealDB: interner Treiber. Alle Dienste nur über das interne `meadow-backend`-Netz, öffentlich erreichbar sind ausschließlich `pim-frontend` und `pim-core` über Traefik.

### 3.3 Traefik-Labels (pim-core / pim-frontend)

Beispiel für `pim-core` (analog the predecessor project; Router-Präfix `meadow-api`):

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.docker.network=${TRAEFIK_NETWORK:-traefik-net}"
  - "traefik.http.routers.meadow-api.rule=Host(`${PIM_API_HOST}`)"
  - "traefik.http.routers.meadow-api.entrypoints=websecure"
  - "traefik.http.routers.meadow-api.tls=true"
  - "traefik.http.routers.meadow-api.tls.certresolver=letsencrypt"
  - "traefik.http.routers.meadow-api-redirect.rule=Host(`${PIM_API_HOST}`)"
  - "traefik.http.routers.meadow-api-redirect.entrypoints=web"
  - "traefik.http.routers.meadow-api-redirect.middlewares=meadow-https-redirect"
  - "traefik.http.middlewares.meadow-https-redirect.redirectscheme.scheme=https"
  - "traefik.http.services.meadow-api.loadbalancer.server.port=8080"
```

Für `pim-frontend` entsprechend mit Router-Präfix `meadow-ui` und Server-Port `8081`.

### 3.4 Healthchecks & Restart-Politik

- `pim-core`: `GET /health` → HTTP 200
- `pim-frontend`: HTTP-Check auf lokalen Port
- `pim-worker`: Verbindungs-Check zu NATS (bzw. Prozess-Healthcheck)
- `surrealdb`: `GET /health` (Port 8000)
- `nats`: `nats server check` (bzw. TCP-Check 4222)
- `object-storage`: `GET /minio/health/live`
- `redis`: `redis-cli ping`
- `restart: unless-stopped`; `depends_on` mit `condition: service_healthy` für Datenbank-abhängige Dienste (Muster wie the predecessor project).

## 4. Konfiguration (.env.vps)

Eine `.env.vps` auf dem Server (aus `.env.vps.example`, **nicht** in der Versionskontrolle) hält alle Secrets und Parameter:

```bash
# Image-Tags & Registry
PIM_REGISTRY=registry.example.com/meadow
PIM_CORE_TAG=latest
PIM_FRONTEND_TAG=latest
PIM_WORKER_TAG=latest

# Traefik-Hostnamen
PIM_UI_HOST=meadow.pim.example.com
PIM_API_HOST=meadow.api.example.com
TRAEFIK_NETWORK=traefik-net

# SurrealDB
SURREALDB_URL=http://surrealdb:8000
SURREALDB_USER=CHANGE_ME
SURREALDB_PASS=CHANGE_ME
SURREALDB_NS=meadow
SURREALDB_DB=meadow

# NATS
NATS_URL=nats://nats:4222
NATS_USER=CHANGE_ME
NATS_PASS=CHANGE_ME

# Objektstorage (MinIO)
S3_ENDPOINT=http://object-storage:9000
S3_ACCESS_KEY=CHANGE_ME
S3_SECRET_KEY=CHANGE_ME
S3_BUCKET=meadow

# JWT
JWT_SECRET=CHANGE_ME
JWT_EXPIRATION=3600

# Core-URL für Frontend/Worker
PIM_CORE_URL=http://pim-core:8080

# NiceGUI-Session-Storage
UI_STORAGE_SECRET=CHANGE_ME

# Logging / Webhooks
LOG_LEVEL=INFO
WEBHOOK_RETRY_MAX=5
WEBHOOK_RETRY_BACKOFF=300

# Bootstrap (nur beim Erst-Deployment; danach entfernen)
BOOTSTRAP_SUPERADMIN_USERNAME=superadmin
BOOTSTRAP_SUPERADMIN_PASSWORD=CHANGE_ME
BOOTSTRAP_SUPERADMIN_EMAIL=CHANGE_ME

# Backup
BACKUP_DIR=backups/db
BACKUP_RETENTION_DAYS=14

# Diagnostics / Log-Triage
DIAGNOSTICS_OUTPUT_ROOT=diagnostics
DIAGNOSTICS_LOG_LINES=300
ERROR_TAIL_LINES=400

# Deploy-Verhalten
RUN_POST_DEPLOY_HEALTHCHECK=true
AUTO_RUN_DB_MIGRATIONS=true
AUTO_BASELINE_DB_MIGRATIONS=true
```

## 5. Deployment-Skripte (nach Connector-Hub-Muster)

Die Skripte werden in Phase 8 angelegt und spiegeln 1:1 die Connector-Hub-Skripte:

| Skript (Meadow)          | Vorbild (the predecessor project)   | Aufgabe                                                            |
|--------------------------|---------------------------|--------------------------------------------------------------------|
| `deploy-vps-meadow.sh`   | `deploy-vps-chub.sh`      | Registry-Login, Compose-Validierung, Netz-Check, Pull, Migrationen, `up -d --remove-orphans` (kein `down`), Status + optional Healthcheck |
| `healthcheck-vps.sh`     | `healthcheck-vps.sh`      | Wartezeit, Container-Health je Dienst, interne Endpoint-Checks     |
| `backup-vps-db.sh`       | `backup-vps-db.sh`        | SurrealDB-Export nach `$BACKUP_DIR`                                |
| `restore-vps-db.sh`      | `restore-vps-db.sh`       | SurrealDB-Import aus Backup (interaktiv / `--force`)               |
| `cleanup-backups.sh`     | `cleanup-backups.sh`      | Löscht Backups älter als `BACKUP_RETENTION_DAYS`                   |
| `bootstrap-vps-admin.sh` | `bootstrap-vps-admin.sh`  | Einmaliger Superadmin (Rolle `superadmin`) über `pim-core`         |
| `collect-vps-diagnostics.sh` | `collect-vps-diagnostics.sh` | Zeitgestempeltes Diagnose-Bundle (Compose-Config, Health, Inspect, Logs, Host-Infos) |
| `tail-vps-errors.sh`     | `tail-vps-errors.sh`      | Fehlerzeilen-Triage aus den Logs (optional `--follow`)             |

**Wichtige Verträge des Deploy-Skripts (aus the predecessor project übernommen):**

- `set -e`; `compose()`-Helfer (Docker-Compose-V2-Fallback).
- Preflight der Traefik-Hostnamen: leere Hosts → Abbruch.
- Externes Netz prüfen (`docker network inspect $TRAEFIK_NETWORK`), bei Bedarf erzeugen (`docker network create traefik-net`).
- Erst DB/Abhängigkeiten starten, dann Migrationen, dann restliche Dienste — Volumes bleiben über Redeploys stabil.
- Übernahmefähigkeit von Env-Overrides (`AUTO_RUN_DB_MIGRATIONS`, `AUTO_BASELINE_DB_MIGRATIONS`, `RUN_POST_DEPLOY_HEALTHCHECK`, …).

## 6. Migration & Schema (SurrealDB)

Meadow nutzt SurrealDB; das Migrationsmuster von the predecessor project (SQL + `applied_migrations`) wird wie folgt abgebildet:

- **Migrationsdateien:** nummerierte `.surql`-Dateien unter `migrations/` (z. B. `001_init.surql`, `002_*.surql`, …), lexikografisch sortiert angewendet.
- **Runner:** `run-migration.sh` führt eine Datei per `surreal sql --file` (bzw. über den SurrealDB-Container) aus; Fortschritt wird in der Tabelle `schema_migrations` (Name, `applied_at`) festgehalten.
- **Baseline:** Ist `schema_migrations` leer, das Ziel-Schema aber bereits vorhanden (Marker-Prüfung), werden alle vorhandenen Migrationsdateien als angewendet gebucht statt erneut ausgeführt (`AUTO_BASELINE_DB_MIGRATIONS`).
- **Startreihenfolge:** Migrationen laufen im Deploy-Skript vor dem Start von `pim-core`/`pim-frontend`.

## 7. Erst-Deployment (Runbook example.com)

1. **DNS:** A-Records `meadow.pim.example.com` und `meadow.api.example.com` → IP des VPS.
2. **Netz prüfen:** `docker network inspect traefik-net` (existiert, da Traefik/the predecessor project läuft; sonst `docker network create traefik-net`).
3. **Repo auf den Server bringen** (z. B. `/opt/meadow`), `.env.vps` aus `.env.vps.example` erzeugen und alle Secrets setzen.
4. **Registry-Login:** `docker login $PIM_REGISTRY`.
5. **Deploy:** `./deploy-vps-meadow.sh`.
6. **Bootstrap:** `./bootstrap-vps-admin.sh` (einmalig), danach `BOOTSTRAP_*`-Variablen aus `.env.vps` entfernen.
7. **Healthcheck:** `./healthcheck-vps.sh`.
8. **Verifikation:** `https://meadow.pim.example.com` (UI) und `https://meadow.api.example.com` (OpenAPI) im Browser prüfen.
9. **Cron** für Backup/Cleanup einrichten (siehe Abschnitt 8).

## 8. Betriebs-Runbook (Day-2)

- **Vor jedem Deployment:** `./backup-vps-db.sh`.
- **Deployment:** `./deploy-vps-meadow.sh`.
- **Nach Deployment:** `./tail-vps-errors.sh`; `./collect-vps-diagnostics.sh` nur bei Problemen.
- **Wöchentlich:** Restore-Test gegen Staging, `./cleanup-backups.sh`.
- **Cron-Beispiel:**

```cron
0 2 * * * /opt/meadow/backup-vps-db.sh >> /var/log/meadow-backup.log 2>&1
30 2 * * 0 /opt/meadow/cleanup-backups.sh >> /var/log/meadow-backup.log 2>&1
```

## 9. Persistenz & Volumes

- **SurrealDB:** Datenbank in benanntem Volume (`meadow_surrealdb_data`, Mount `/data`).
- **NATS JetStream:** Persistenter Store in Volume (`meadow_nats_data`, `/data`).
- **Objektstorage (MinIO):** Datenverzeichnis in Volume (`meadow_minio_data`, `/data`).
- **Frontend/Worker:** gemeinsames Storage-Volume (`meadow_storage`) für Logs/Importe/Exporte sowie `meadow_nicegui` für den NiceGUI-Session-Storage.
- **Redis (optional):** Persistenz konfigurierbar (AOF/RDB), Volume `meadow_redis_data`.

## 10. Backup & Wiederherstellung

- **SurrealDB:** regelmäßige Exporte (`surreal export` gegen den Container, z. B. `--conn http://surrealdb:8000 --ns meadow --db meadow`), Zeitplan über Host-Cron.
- **Objektstorage:** Synchronisation der Buckets auf einen zweiten Speicherort (z. B. `mc mirror`/rclone bei MinIO).
- **NATS JetStream:** Stream-Snapshots optional; für den MVP genügt Wiederherstellung aus der Quelle, da Events rekonstruierbar sind.
- **Wiederherstellungstests:** mindestens vierteljährlich durchführen.

## 11. Monitoring & Logging

- **Logging:** Rust-Kern mit `tracing` (JSON optional), Python-Dienste mit `structlog`; Ausgabe auf `stdout`, Sammlung über Docker-Logging-Treiber (max. 10 MB/5 Dateien je Container, wie the predecessor project) oder zentral (Loki/ELK).
- **Health-Checks:** jeder Container stellt einen Health-Endpoint bereit (siehe 3.4); Compose startet Container bei Ausfall neu.
- **Metriken (optional, ab Phase 8):** Prometheus-Endpunkte im Kern und Worker (Jobs, Event-Durchsatz, API-Latenz), Grafana-Dashboards.

## 12. Skalierung

- **Rust-Kern:** horizontal skalierbar; Scheduler nur in einer Instanz aktiv (`scheduler.enabled=true` oder Leader-Election).
- **Python-Worker:** mehrere Container über eine NATS-Queue-Group (Lastverteilung).
- **Frontend:** mehrere Instanzen nur mit Sticky Sessions (NiceGUI ist zustandsbehaftet); im MVP eine Instanz.
- **SurrealDB:** im MVP Einzelinstanz; Cluster-Modus erst bei größerem Datenaufkommen.
- **Hinweis:** Der example.com-Server teilt sich Ressourcen mit anderen Stacks (z. B. the predecessor project); Skalierung dort nur nach Kapazitätsprüfung.

## 13. Ressourcenbedarf (Richtwerte für example.com)

| Dienst          | CPU     | RAM   | Speicher (Volumen) |
|-----------------|---------|-------|--------------------|
| pim-core        | 1–2 vCPU| 512 MB| –                  |
| pim-frontend    | 1 vCPU  | 512 MB| –                  |
| pim-worker      | 1 vCPU  | 512 MB| –                  |
| surrealdb       | 1–2 vCPU| 1 GB  | 10–50 GB           |
| nats            | 0.5 vCPU| 256 MB| 5–20 GB            |
| object-storage  | 1 vCPU  | 512 MB| 50–500 GB          |
| redis (opt.)    | 0.5 vCPU| 256 MB| 1–5 GB             |

Diese Werte sind als Ausgangspunkt gedacht; auf dem gemeinsamen Server ist die tatsächlich verfügbare Kapazität (VPS-Paket) zu berücksichtigen.

## 14. Offene Punkte & Annahmen

- **Hostnamen:** Es gelten `meadow.pim.example.com` (UI) und `meadow.api.example.com` (Core); die im Chat genannte Schreibweise `meadow.pim.claudiapss.de` wurde als Tippfehler zu `example.com` korrigiert.
- **Registry:** Vorgeschlagen wird die bestehende Forgejo-Registry `registry.example.com/meadow` (wie the predecessor project); alternativ GHCR (`ghcr.io`). Festzulegen bei Umsetzung.
- **SurrealDB:** Für das MVP als separater Container (nicht embedded) festgelegt.
- **Objektstorage:** MinIO als separater Container; alternativ Host-Pfad-Mount.
- **Schema-Migration:** Mechanismus (`.surql` + `schema_migrations`) ist festgelegt; konkreter Runner wird mit dem Core implementiert.
