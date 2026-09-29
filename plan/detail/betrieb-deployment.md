# Betrieb & Deployment

- **Hauptdokument:** Abschnitt 12 ([Betrieb & Deployment](../meadow-pim-architektur.md#12-betrieb--deployment))
- **Relevante Phasen:** Phase 2, 4–5, 7, 8
- **Stand:** Konkretisiert für das erste Produktions-Deployment (VPS, example.com); Umsetzung erfolgt in Phase 8. Logging-Spezifikation (kuratierte System-Logs in SurrealDB + Runtime-Konfiguration) ergänzt. **Phase 8:** Request-Limits (Body-/Rate-Limit) und Backup/Monitoring präzisiert – inkl. der **verifizierten SurrealDB-CLI** (`/surreal`, `--namespace`/`--database`, `is-ready`) für diesen Image-Stand und der lokalen Dev-Skripte `scripts/backup-db.ps1`/`restore-db.ps1`. **Deployment-Dateien erstellt:** `docker-compose.vps.yml`, `.env.vps.example`, `docker/Dockerfile.*`, VPS-Bash-Skripte (`scripts/build-images.sh`, `deploy-vps-meadow.sh`, `backup-vps-db.sh`, `restore-vps-db.sh`, `cleanup-backups.sh`, `healthcheck-vps.sh`, `collect-vps-diagnostics.sh`, `tail-vps-errors.sh`, `bootstrap-vps-admin.sh`). *Hinweis: Der Kern sät derzeit `admin`/`admin`; der **Passwort-Änderungs-Flow ist implementiert** (`POST /auth/password`, Schlüssel-Button im UI-Kopf; ein Banner mahnt an, solange das Passwort nie geändert wurde) — die Seed-Zugangsdaten nach dem ersten Deployment ändern.*
- **Stand 2026-09-29 (Deployment fertiggestellt und geprüft):** Bilder werden **auf dem Entwicklungsrechner gebaut und gepusht** (`build-images.sh`/`build-images.ps1`, unveränderlicher Git-Kurzhash-Tag plus `latest`), der Server pullt nur. Neu dazu: der **Migrations-Runner** `run-migration.sh` samt `migrations/`-Vertrag (Abschnitt 6), der **gestückelte Restore** über `POST /sql` (Abschnitt 10, weil `surreal import` an jeder realistischen Exportgröße scheitert), ein `.dockerignore` (der Kontext enthielt vorher `target/` und `.venv/`), Log-Rotation je Container, `.gitattributes` gegen CRLF in Skripten und Migrationsdateien sowie die Registry `registry.example.com/meadow`. Alle Befunde stammen aus Läufen gegen die laufende Dev-Instanz; die Kern-Gates prüfen zusätzlich `tests/test_deployment.py`.

Alle Komponenten werden als Docker-Container betrieben. Die Standardauslieferung erfolgt als **Docker-Compose-Setup**. Konkretes Ziel für das erste Deployment ist der **VPS unter `example.com`**, auf dem bereits Traefik als Reverse Proxy läuft. Die Deployment-Organisation folgt dabei dem erprobten VPS-Muster von **the predecessor project** (`deploy-vps-*.sh`, `.env.vps`, Traefik-Labels, Healthchecks, Backup/Restore). Eine spätere Orchestrierung über Kubernetes (z. B. k3s) ist vorbereitet, aber nicht Bestandteil des MVP.

## 1. Zielumgebung: VPS (example.com)

- **Server:** VPS; betreibt bereits mehrere Stacks unter `example.com` (u. a. the predecessor project).
- **Traefik:** läuft dort als eigenständiger Stack (Reverse Proxy) mit TLS-Terminierung via Let’s Encrypt (certresolver `letsencrypt`).
- **Externes Docker-Netz:** `traefik-net` — gemeinsames Netz zwischen Traefik und allen öffentlich gerouteten Stacks (the predecessor project nutzt dasselbe Netz; das Netz existiert daher bereits).
- **Meadow:** eigener Compose-Stack (`meadow`-Namensraum), der sich über das externe Netz `traefik-net` an Traefik anbindet.
- **Muster:** Skript-Reihe, Compose-Dateien und Env-Handling analog the predecessor project (`deploy-vps-chub.sh`, `docker-compose.vps.yml`, `.env.vps`).

## 2. DNS & öffentliche Routen (Traefik)


| Hostname                   | Compose-Service | Container-Port | Zweck                                  |
| -------------------------- | --------------- | -------------- | -------------------------------------- |
| `meadow.pim.example.com` | `pim-frontend`  | 8081           | Web-Oberfläche (NiceGUI)               |
| `meadow.api.example.com` | `pim-core`      | 8080           | REST-API (inkl. OpenAPI-Dokumentation) |

- **DNS:** A-Records für beide Subdomains auf die IP des VPS.
- **TLS:** ausschließlich am Traefik terminiert; beide Router über Entrypoint `websecure` + certresolver `letsencrypt`.
- **Redirect:** HTTP → HTTPS über eine gemeinsame Middleware (`meadow-https-redirect`), analog the predecessor project.
- **Nicht öffentlich routen:** `surrealdb`, `nats`, `object-storage`, `redis` und `pim-worker` bleiben ausschließlich im internen Netz.

## 3. Stack-Architektur (docker-compose.vps.yml)

### 3.1 Netzwerke

- `traefik-net` (external) — nur an `pim-frontend` und `pim-core` (Traefik-Routing). Name über `TRAEFIK_NETWORK` parametrisiert.
- `meadow-backend` (bridge, intern) — an alle Dienste.

### 3.2 Dienste

| Service            | Image                                              | Ports (intern)            | Netzwerke                      | Traefik                    |
| ------------------ | -------------------------------------------------- | ------------------------- | ------------------------------ | -------------------------- |
| `pim-core`         | `${PIM_REGISTRY}/pim-core:${PIM_CORE_TAG}`         | 8080                      | traefik-net + meadow-backend | `meadow.api.example.com` |
| `pim-frontend`     | `${PIM_REGISTRY}/pim-frontend:${PIM_FRONTEND_TAG}` | 8081                      | traefik-net + meadow-backend | `meadow.pim.example.com` |
| `pim-worker`       | `${PIM_REGISTRY}/pim-worker:${PIM_WORKER_TAG}`     | –                         | meadow-backend                 | nein                       |
| `surrealdb`        | `surrealdb/surrealdb:latest`                       | 8000                      | meadow-backend                 | nein                       |
| `nats`             | `nats:latest` (JetStream)                          | 4222, 8222 (Monitoring)   | meadow-backend                 | nein                       |
| `object-storage`   | `minio/minio:latest`                               | 9000 (S3), 9001 (Console) | meadow-backend                 | nein                       |
| `redis` (optional) | `redis:7-alpine`                                   | 6379                      | meadow-backend                 | nein                       |

- Container-Namen mit `meadow-`-Präfix (z. B. `meadow-core`, `meadow-frontend`, `meadow-surrealdb`), um auf dem gemeinsamen Server Kollisionen zu vermeiden.
- **Images:** gebaut wird auf dem Entwicklungsrechner (`scripts/build-images.sh --push`, Windows `scripts/build-images.ps1 -Push`) und zweifach getaggt — unveränderlich (Git-Kurzhash, `-dirty` bei schmutzigem Arbeitsbaum) plus `latest`. Der Server pullt nur; in `.env.vps` wird der unveränderliche Tag gepinnt (Rollback = älteren Tag setzen und erneut deployen).
- **Loopback-Ports (nicht über Traefik geroutet):** `surrealdb` 8000 (der Migrations-Runner und der Restore sprechen `POST /sql`), NATS-Monitoring 8222, `pim-core` 8080. Die übrigen Dienste sind ausschließlich im internen Netz erreichbar.
- **Kommunikation (intern):** Frontend → Core: REST (JWT) · Worker → Core: REST (JWT) · Core → NATS: Publish · Worker → NATS: Subscribe · Core ↔ Worker (Rules): NATS Request/Reply (`rules.evaluate`) · Core → SurrealDB: interner Treiber. Alle Dienste nur über das interne `meadow-backend`-Netz, öffentlich erreichbar sind ausschließlich `pim-frontend` und `pim-core` über Traefik.

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

| Dienst | Check | Anmerkung |
| ------ | ----- | --------- |
| `pim-core` | `GET /health` (im Container) | |
| `pim-frontend` | `urllib` auf `http://127.0.0.1:8081/` **im Container** | Der Port ist nicht veröffentlicht, ein `curl` vom Host läuft ins Leere |
| `pim-worker` | TCP-Verbindung zu `nats:4222` (Python) | Der Worker hat keinen HTTP-Endpunkt; der Check zeigt Prozess + NATS |
| `surrealdb` | `/surreal is-ready` | im Image liegt das Binary unter `/surreal` |
| `object-storage` | `curl /minio/health/live` | das MinIO-Image bringt `curl` mit |
| `nats` | **kein** Container-Check | Das Image hat keine Shell und kein `curl`/`wget`; geprüft wird stattdessen vom Host über den Monitoring-Port (`/healthz`), siehe `healthcheck-vps.sh` |
| `redis` (Profil) | `redis-cli ping` | |

- `restart: unless-stopped` für jeden Dienst; `depends_on` mit `condition: service_healthy` (Kern wartet auf SurrealDB, Frontend auf den Kern, Worker auf NATS + SurrealDB).
- **Log-Rotation:** jeder Dienst `max-size: 10m`, `max-file: 5` (`x-logging` in der Compose-Datei) – ohne das füllt sich die Platte eines kleinen VPS über Monate.

## 4. Konfiguration (.env.vps)

Eine `.env.vps` auf dem Server (aus `.env.vps.example`, **nicht** in der Versionskontrolle) hält alle Secrets und Parameter. **Die Liste steht genau einmal: in `.env.vps.example`** — sie hier zu wiederholen hat schon einmal dazu geführt, dass ein Wert (`S3_ENDPOINT` vs. `S3_ENDPOINT_URL`) im Dokument richtig und im Container leer war.

- **Registry:** `registry.example.com/meadow` (Forgejo desselben Servers, wie the reference stack); der Pfad muss kleingeschrieben sein, Forgejo weist Großbuchstaben ab.
- **Nicht offensichtliche Werte:**
  - `SURREALDB_URL=surrealdb:8000` (Dienstname, nicht `127.0.0.1`); `SURREALDB_HTTP_URL=http://127.0.0.1:8000` ist die **Host-Sicht** für `run-migration.sh` und `restore-vps-db.sh`.
  - `S3_ACCESS_KEY_ID`/`S3_SECRET_ACCESS_KEY` sind gleichzeitig die MinIO-Root-Zugangsdaten und das, was Frontend **und** Worker lesen (`meadow/frontend/exports.py` importiert das Storage-Modul des Workers).
  - `LOG_INGEST_API_KEY` muss auf beiden Seiten derselbe Wert sein: ohne ihn antwortet der Kern dem Worker mit `401`, und jeder Worker-Schreibpfad (Bulk, Import, Export, Regeln) läuft ins Leere.
  - `AUTO_RUN_DB_MIGRATIONS`, `AUTO_BASELINE_DB_MIGRATIONS`, `RUN_POST_DEPLOY_HEALTHCHECK` steuern das Deploy-Skript; `RESTORE_CHUNK_BYTES` die Batch-Größe des Restores.
  - `DOCKER_REGISTRY_USER`/`DOCKER_REGISTRY_PASSWORD` sind optional und erlauben einen Registry-Login ohne Rückfrage (Cron/Automatisierung).
- **Bewusst nicht in der Datei:**
  - **KI-Assistenz:** konfiguriert wird sie in der Oberfläche (`/system-settings`, Gruppe „KI-Assistenz“, Opt-in über `ai_enabled`). Ein `AI_*`-ENV-Wert im Container würde die Einstellung sperren — dafür müssten die Variablen in `docker-compose.vps.yml` ergänzt werden; ein **leerer** Wert wäre falsch (`std::env::var` sieht ihn als gesetzt).
  - **Webhook-Konnektoren:** liegen als Daten in der Tabelle `connector` und werden über die API/UI gepflegt (Live-Reload). Die `WEBHOOK_*`-ENV-Variablen des Workers sind nur ein Dev-Bootstrap und gehören nicht auf den Server.
  - **`BOOTSTRAP_*`** gibt es nicht: der Kern sät `admin`/`admin`, das Passwort wird über `POST /auth/password` geändert (`scripts/bootstrap-vps-admin.sh`).
  - **`DIAGNOSTICS_*`/`ERROR_TAIL_LINES`** gibt es nicht; `collect-vps-diagnostics.sh` und `tail-vps-errors.sh` haben ihre Werte im Skript. Der Diagnose-Bundle lädt `.env.vps` **absichtlich nicht**, damit `compose config` keine Secrets in den Bericht schreibt.
- **BMEcat-Validierung** braucht ein Artefakt, das nicht in Git und nicht im Image liegt (`resources/`): ohne Bind-Mount plus `BMECAT_XSD` meldet der Export `validated: false` mit Begründung (ehrlich, kein stiller Fehlschlag).


## 5. Deployment-Skripte

Die Skripte folgen dem VPS-Muster von the reference stack („bauen und pushen auf dem Entwicklungsrechner, auf dem Server nur pullen“) und liegen unter `scripts/`:

| Skript                        | Aufgabe                                                                                                                                                        |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `build-images.sh` / `.ps1`    | Baut `pim-core`/`pim-frontend`/`pim-worker` und taggt sie zweifach (Git-Kurzhash + `latest`); `--push` pusht beide Tags und druckt die Tag-Zeilen zum Pinnen aus |
| `deploy-vps-meadow.sh`        | Preflight → Registry-Login → Compose-Validierung → Netz-Check → Pull → Infrastruktur → Migrationen → Anwendung → Healthcheck                                     |
| `run-migration.sh`            | Wendet die Migrationen an (Abschnitt 6): `--status`, `--baseline`, `--file`                                                                                     |
| `healthcheck-vps.sh`          | Wartet auf gesunde Container; prüft Kern `/health`, NATS-Monitoring und das Frontend **im Container**                                                          |
| `backup-vps-db.sh`            | SurrealDB-Export nach `$BACKUP_DIR` (im Container, per `docker cp` herausgeholt)                                                                                |
| `restore-vps-db.sh`           | Gestückelter Restore über `POST /sql` (Abschnitt 10), interaktiv / `--force`                                                                                    |
| `cleanup-backups.sh`          | Löscht Backups älter als `BACKUP_RETENTION_DAYS`                                                                                                               |
| `bootstrap-vps-admin.sh`      | Ersetzt das Seed-Passwort `admin`/`admin` über `POST /auth/password` und verifiziert den neuen Login                                                             |
| `collect-vps-diagnostics.sh`  | Zeitgestempeltes Diagnose-Bundle (Compose-Config, Health, Inspect, Logs, Host-Infos); **ohne** Secrets                                                          |
| `tail-vps-errors.sh`          | Fehlerzeilen-Triage aus den Logs (optional `--follow`)                                                                                                         |

**Verträge der Skripte (implementiert und geprüft):**

- `set -euo pipefail`, `cd` auf die Repo-Wurzel, Laden von `.env.vps` mit **CRLF-Strip** (`tr -d '\r'`): eine auf Windows geschriebene Datei würde sonst jeden Wert zum Parser-Fehler machen. `.gitattributes` hält Skripte und `.surql` zusätzlich auf LF.
- Preflight: fehlende oder auf `CHANGE_ME` stehende Secrets → Abbruch, bevor irgendetwas startet.
- Registry-Login ohne Rückfrage, sobald `DOCKER_REGISTRY_USER`/`DOCKER_REGISTRY_PASSWORD` gesetzt sind.
- Externes Netz prüfen (`docker network inspect $TRAEFIK_NETWORK`), bei Bedarf erzeugen.
- Reihenfolge: Infrastruktur (`surrealdb nats object-storage`) → auf `healthy` der Datenbank warten → Migrationen → `up -d --remove-orphans` (kein `down`; benannte Volumes bleiben unberührt) → Status → Healthcheck.
- Env-Overrides: `AUTO_RUN_DB_MIGRATIONS`, `AUTO_BASELINE_DB_MIGRATIONS`, `RUN_POST_DEPLOY_HEALTHCHECK`.
- `tests/test_deployment.py` hält die stillen Verträge fest: jede in der Compose gelesene Variable ist in `.env.vps.example` dokumentiert (und umgekehrt), jeder Dienst rotiert seine Logs, die Migrationen laufen **vor** der Anwendung, der Healthcheck fragt keinen unveröffentlichten Port ab.

## 6. Migration & Schema (SurrealDB)

Das Basisschema gehört dem **Kern**: `crates/pim-core/src/db/schema.rs` definiert alle Tabellen mit `DEFINE … IF NOT EXISTS` und wendet sie beim Start an. `migrations/` ist deshalb **kein zweites Schema**, sondern die Historie der Schritte, die das nicht ausdrücken kann (Daten­nachträge, Umbenennungen, Einmalreparaturen) — Vertrag in `migrations/AGENTS.md`.

- **Dateien:** `NNN_snake_case.surql`, lexikografisch angewendet; `001_baseline.surql` ist der Startpunkt (bewusst ohne Statements). Nie eine angewendete Datei ändern, nur ergänzen.
- **Runner:** `scripts/run-migration.sh` spricht `POST /sql` und wertet die **Antwort je Statement** aus (`{"status":"OK"|"ERR"}`; ein nicht parsebarer Batch beantwortet der Server mit HTTP 400). Deshalb bricht der Lauf beim ersten Fehler ab und bucht die Datei **nicht** — `surreal import` wäre hier falsch: es verlangt `OPTION IMPORT;` als erste Anweisung und schaltet dabei Feldverarbeitung und Events ab.
- **Buchhaltung:** Tabelle `schema_migrations` (`name`, `applied_at`), vom Runner selbst angelegt; `--status` zeigt angewendet/ausstehend, ohne etwas zu verändern.
- **Baseline:** ist `schema_migrations` leer, der Kern-Marker (`system_setting`) aber vorhanden, werden alle Dateien als angewendet gebucht statt erneut ausgeführt (`AUTO_BASELINE_DB_MIGRATIONS=false` schaltet das ab) — der Fall „Datenbank lief schon einmal ohne Runner“.
- **Frische Installation:** Namespace und Datenbank existieren noch nicht, und SurrealDB 3.x verweigert jeden Schreibzugriff auf eine nie definierte Datenbank (`The database 'x' does not exist`). Der Runner legt beides deshalb zuerst an (`DEFINE NAMESPACE/DATABASE IF NOT EXISTS`) — **ohne diesen Schritt scheitert das allererste Deployment** (verifiziert).
- **Startreihenfolge:** Migrationen laufen im Deploy-Skript vor dem Start von `pim-core`/`pim-frontend`.
- **Ausführung:** `bash scripts/run-migration.sh --status` (bzw. `--baseline`), gegen eine lokale Datenbank mit `SURREALDB_HTTP_URL=http://127.0.0.1:8000`.

## 7. Erst-Deployment (Runbook example.com)

1. **DNS:** A-Records `meadow.pim.example.com` und `meadow.api.example.com` → IP des VPS.
2. **Images bauen und pushen** (auf dem Entwicklungsrechner): `./scripts/build-images.sh --registry registry.example.com/meadow --push` bzw. unter Windows `powershell -File scripts/build-images.ps1 -Registry registry.example.com/meadow -Push` (der Entwicklungsrechner hat kein `.env.vps`; alternativ `PIM_REGISTRY` in der Umgebung setzen). Vorher einmalig `docker login registry.example.com`. Der Lauf druckt den unveränderlichen Tag aus.
3. **Repo auf den Server bringen** (z. B. `/opt/meadow`).
4. **`.env.vps`** aus `.env.vps.example` erzeugen, Secrets setzen und in `PIM_CORE_TAG`/`PIM_FRONTEND_TAG`/`PIM_WORKER_TAG` den **gepushten Tag** eintragen (nicht `latest` — nur so sind Rollback und Reproduzierbarkeit gegeben).
5. **Netz prüfen:** `docker network inspect traefik-net` (existiert, da Traefik/the reference stack läuft; das Deploy-Skript legt es sonst an).
6. **Deploy:** `./scripts/deploy-vps-meadow.sh` (Preflight, Login, Pull, Infrastruktur, Migrationen, Anwendung, Healthcheck).
7. **Bootstrap:** `./scripts/bootstrap-vps-admin.sh` — ersetzt das Seed-Passwort `admin`/`admin` über `POST /auth/password` und prüft den neuen Login. Danach ein persönliches Superadmin-Konto anlegen und `admin` nur als Notzugang behalten.
8. **Verifikation:** `https://meadow.pim.example.com` (UI) und `https://meadow.api.example.com` (OpenAPI) im Browser; `./scripts/healthcheck-vps.sh`.
9. **Cron** für Backup/Cleanup einrichten (Abschnitt 8).
10. **Standardpakete (optional):** Paketordner nach `resources/standards/<name>/` kopieren und `uv run python scripts/install_standard_bundle.py --all --bulk` ausführen (idempotent, mit Einheiten-Vorprüfung). Für den Vollbestand danach **erneut** laufen lassen, damit die Klassen-Merkmale nachgezogen werden; ohne Pakete im Ordner ist der Aufruf ein No-op. Details: [bmecat-export.md](bmecat-export.md) §11.9.

## 8. Betriebs-Runbook (Day-2)

- **Vor jedem Deployment:** `./scripts/backup-vps-db.sh`.
- **Deployment:** `./scripts/deploy-vps-meadow.sh`.
- **Nach Deployment:** `./scripts/tail-vps-errors.sh`; `./scripts/collect-vps-diagnostics.sh` nur bei Problemen.
- **Rollback:** den vorherigen unveränderlichen Tag in `.env.vps` setzen und erneut deployen (ein Schema-Rückschritt ist **nicht** enthalten — Migrationen laufen nur vorwärts; für einen echten Rückschritt das Backup von vor dem Deploy zurückspielen).
- **Wöchentlich:** Restore-Test gegen Staging, `./scripts/cleanup-backups.sh`.
- **Cron-Beispiel:**

```cron
0 2 * * * /opt/meadow/scripts/backup-vps-db.sh >> /var/log/meadow-backup.log 2>&1
30 2 * * 0 /opt/meadow/scripts/cleanup-backups.sh >> /var/log/meadow-backup.log 2>&1
```

## 9. Persistenz & Volumes

- **SurrealDB:** Datenbank in benanntem Volume (`meadow_surrealdb_data`, Mount `/data`).
- **NATS JetStream:** Persistenter Store in Volume (`meadow_nats_data`, `/data`).
- **Objektstorage (MinIO):** Datenverzeichnis in Volume (`meadow_object_storage_data`, `/data`).
- **Frontend:** `meadow_nicegui` für den NiceGUI-Session-Storage (Pflicht — der Storage liegt sonst im Container-Layer und jede Sitzung wäre nach einem Recreate weg).
- **`meadow_storage` (`/tmp/meadow`)** ist für Frontend und Worker eingerichtet, aber **reserviert**: heute schreibt dort nichts (Exporte laufen über `job_run.result` bzw. das Bucket), keine Datei hängt von dem Volume ab.
- **Redis (optional):** Persistenz konfigurierbar (AOF/RDB), Volume `meadow_redis_data`.

## 10. Backup & Wiederherstellung

- **SurrealDB:** regelmäßige Exporte über den Container, Zeitplan über Host-Cron.
  - **CLI-Version beachten (verifiziert):** Im `surrealdb/surrealdb:latest`-Image liegt das Binary unter **`/surreal`** (nicht auf `$PATH`); die Export-/Import-Subcommands heißen **`export`/`import`** und verwenden **`--namespace`/`--database`** (Aliase `--ns`/`--db`) sowie **`-e/--endpoint`** und **`-u/--username`/`-p/--password`** (Aliase `--user`/`--pass`). Die alten Schreibweisen `--conn`, `isready` (statt `is-ready`) und der bloße Befehl `surreal` sind in dieser Version **ungültig**.
  - **Beispiel:** `/surreal export --namespace meadow --database meadow --endpoint http://surrealdb:8000 --username root --password root /tmp/backup.surql`; Wiederherstellung über `/surreal import … /tmp/backup.surql` (Datei-Upload in den Container, da kein Shell/kein stdin-`-`-Support).
  - **Kodierung:** Export **im Container** in eine Datei schreiben und per `docker cp` holen — eine PowerShell-`>`-Umleitung kodiert UTF-16 und erzeugt einen ungültigen Import.
- **Restore läuft nicht über `surreal import` (gemessen 2026-09-29):** `import` scheitert an jeder realistischen Exportgröße mit `error sending request for url (.../import)` — ein Batch mit 100 Anweisungen geht in 3 s durch, ein 0,6-MB-Batch (9 500 Anweisungen) bricht nach 32 s ab, die produktive Datei kommt nie an. `scripts/restore-vps-db.sh` spielt den Export deshalb in **Stücken über `POST /sql`** ein:
  - `split -C` stückelt an Zeilengrenzen (ein Export schreibt eine Anweisung je Zeile); jede Charge bekommt `OPTION IMPORT;` vorangestellt. Die Stückgröße wird automatisch auf die **längste Zeile** angehoben, damit keine Anweisung zerschnitten wird; eine Zeile über ~3,5 MiB bricht ab (der Endpunkt weist Körper über ~4 MiB mit `HTTP 413 length limit exceeded` ab — gemessen).
  - Vorher wird der Scope angelegt (Namespace/Datenbank); jede Charge wird auf `"status":"ERR"` und HTTP 400 geprüft, der Lauf endet mit klarer Meldung in der ersten fehlerhaften Charge (die Datenbank ist dann teilweise geschrieben, der Text sagt das auch).
  - Der Restore **mischt** über Record-IDs, er leert die Datenbank nicht: vorher ein frisches Backup ziehen.
  - Verifiziert gegen die Dev-Instanz: Export → Restore in eine **nicht existierende** Datenbank → Zeilen im Ziel vorhanden; eine kaputte Charge bricht mit Exit ≠ 0 ab.
- **Lokale Dev-Äquivalente (Windows):** `scripts/backup-db.ps1` (Export nach `backups/`, Retention `-RetentionDays`, Default 14) und `scripts/restore-db.ps1` (`-File`). **Offener Punkt:** `restore-db.ps1` nutzt noch `surreal import` und trägt damit dieselbe Schwäche wie oben; für eine Dev-Datenbank ist der Weg über den VPS-Restore (oder ein Neuaufsetzen mit den Demo-Skripten) der verlässliche.
- **Objektstorage:** die Datenbank ist maßgeblich; Export-/Download-Artefakte im Bucket sind aus ihr reproduzierbar, ein Bucket-Backup ist daher optional (z. B. `mc mirror`).
- **NATS JetStream:** Stream-Snapshots optional; für den MVP genügt Wiederherstellung aus der Quelle, da Events rekonstruierbar sind.
- **Wiederherstellungstests:** mindestens vierteljährlich durchführen — gegen die laufende Dev-Instanz geht das mit `bash scripts/restore-vps-db.sh <backup> --force` (Namespace/Datenbank dabei auf einen Prüfbestand zeigen lassen).

## 11. Monitoring & Logging

### 11.1 Logging (verbindliche Spezifikation)

**Grundprinzip (12-Factor):** Jede Komponente schreibt strukturierte Logs als Event-Stream auf `stdout` – Rust-Kern mit `tracing` (JSON), Python-Dienste mit `structlog`. Sammlung über den Docker-Logging-Treiber (**umgesetzt:** `max-size: 10m`, `max-file: 5` je Container über `x-logging` in `docker-compose.vps.yml`) für den Ops-Workflow (Diagnose-Skripte). DEBUG/TRACE bleiben ausschließlich hier.

**Kuratierte System-Logs in SurrealDB (`log_entry`):** Für die Admin-UI wird ein kuratierter Teil persistiert:

- **Kategorien & Schwelle (implementiert):** einheitlicher Schwellwert `log_persist_level` (Standard `WARN`) + Kategorie-Whitelist `log_persist_categories` (Standard `['app']`); `job`/`rule`/`auth`/`integration` lassen sich per Konfiguration zuschalten. Die pro-Kategorie-Schwellen des Entwurfs (`app` ab WARN, übrige ab INFO) sind eine spätere Verfeinerung. `rule`-Logs des Kerns (Circuit Breaker, Warnungen, `log()`) werden direkt persistiert (ohne Whitelist). DEBUG/TRACE werden nie in die DB geschrieben.
- **Transport (verbindlich):** Komponenten senden Einträge als **Batch** über `POST /system/logs` an den Kern (Flush ≈ alle 2 s oder ≥ N Einträge; non-blocking, bounded Buffer, drop-oldest). Logging **blockiert nie den Geschäftspfad**; Fehler beim Senden werden nur auf `stdout` geloggt (bei `ERROR`/`CRITICAL` nach einem Retry verworfen). Direkter DB-Zugriff ist nur dem Kern erlaubt.
- **Schema (`log_entry`):** `id`, `created_at` (UTC, ISO-8601), `service` (`pim-core`, `pim-worker`, `pim-frontend`), `level` (`DEBUG|INFO|WARN|ERROR|CRITICAL`, einheitlich gemappt), `category`, `message`, `trace_id`/`request_id` (Korrelation über Dienste; beim REST-Request bzw. der NATS-Message gesetzt), `context` (JSON, optional: `object_id`, `job_run_id`, `rule_id`, `user_id`, …). Reine Append-Tabelle; Indizes `(service, level, created_at)` und `trace_id`; keine Volltextsuche im MVP (Filter + `CONTAINS` auf `message`).
- **Aufbewahrung:** `log_entry` ist **flüchtig** – Standard **30 Tage** (konfigurierbar 7–90), periodischer Cleanup-Job im Kern (batch-weise, außerhalb der Lastspitze). **Rate-Limit-Cap** pro Dienst gegen Log-Fluten (bei Überschreitung nur auf `stdout`).
- **RBAC:** Anzeige/Lesen nur `admin`/`superadmin`; Rechteprüfung im Kern. Kontext-Links zu Job/Objekt/Rule, wo `context` IDs trägt.
- **Datenschutz (verbindlich):** keine Passwörter/Tokens/JWT-Secrets/API-Keys in Meldungen oder `context`; Benutzer als `user_id` (kein Klarname/E-Mail); IP-Adressen nur bei Bedarf (z. B. wiederholte fehlgeschlagene Logins). Auth-Events ohne Passwort/Token-Payload; fehlgeschlagene Logins ohne enumerierbare Benutzerliste.
- **Abgrenzung:** `audit_log` (fachliche, dauerhafte Änderungshistorie, [versionierung-audit.md](versionierung-audit.md)) ≠ `log_entry` (technisch/operativ, flüchtig) ≠ NATS-Events (Nachrichten für asynchrone Verarbeitung). Fachliche Datenänderungen → nur `audit_log`; technische Fehler/Ops → nur `log_entry`.

**Runtime-Konfiguration (`system_setting`):** Logging-Einstellungen sind im Betrieb per Admin-UI änderbar und persistiert:

- Einstellungen: `log_persist_level`, `log_persist_categories`, `log_retention_days`, `log_rate_limit` (als Key/Value in `system_setting`, `value` JSON).
- **Auflösung:** Code-Default < ENV (Inbetriebnahme) < DB (Runtime). „Auf Default zurücksetzen“ löscht den DB-Eintrag. `LOG_LEVEL` (stdout-Schwelle) bleibt ENV-only.
- **Verteilung:** Worker/Frontend laden die Konfiguration beim Start und periodisch per REST vom Kern; der Cleanup-Job liest `log_retention_days` bei jedem Lauf. Änderungen werden im Audit-Log protokolliert.
- **Env-Variablen (Default/Inbetriebnahme):** `LOG_LEVEL`, `LOG_PERSIST_LEVEL`, `LOG_PERSIST_CATEGORIES`, `LOG_RETENTION_DAYS`, `LOG_RATE_LIMIT`.

**Optionale Ausbaustufe (nicht im MVP):** Zentrale Log-Aggregation mit **Loki + Grafana** (Promtail/Alloy-Collector auf `stdout`) für Volltextsuche und Dashboards, falls später benötigt – ersetzt die DB-Sicht dann nicht, sondern ergänzt sie.

### 11.2 Health-Checks, Metriken & Queue-Tiefe

- **Health-Checks:** jeder Container stellt einen Health-Endpoint bereit (siehe 3.4); Compose startet Container bei Ausfall neu. Im Dev-Compose ist der SurrealDB-Healthcheck auf `/surreal is-ready` korrigiert (siehe Abschnitt 10 CLI-Hinweis).
- **Request-Limits (Phase 8):** API-Body-Limit (`PIM_MAX_BODY_BYTES`, Default 10 MiB) und per-IP-Rate-Limit (`PIM_RATE_LIMIT_PER_MINUTE`, Default 0 = aus) begrenzen Eingabe-Fluten; Überschreitungen antworten `413`/`429` und sind über `log_entry` bzw. die 8222-Queue-Tiefe beobachtbar. Details in [api-design.md](api-design.md#inbound-import--request-limits).
- **Metriken (optional, ab Phase 8):** Prometheus-Endpunkte im Kern und Worker (Jobs, Event-Durchsatz, API-Latenz), Grafana-Dashboards.
- **Queue-/Stream-Tiefe (verbindlich):** Die NATS-JetStream-Tiefe wird über den Monitoring-Port 8222 beobachtet (Pending-Messages je Consumer, Stream-Belegung vs. Max-Bytes, Outbox-Rückstau im Kern); Alarmschwellen bei anhaltender Queue-Tiefe (Worker-Stau) sind dokumentiert.

## 12. Skalierung

- **Rust-Kern:** horizontal skalierbar; Scheduler nur in einer Instanz aktiv (`scheduler.enabled=true` oder Leader-Election).
- **Python-Worker:** ein Container verarbeitet parallel (asyncio; getrennte Konsumenten für `jobs.*` und `events.*`, [jobs-scheduler.md](jobs-scheduler.md)); mehrere Container über eine NATS-Queue-Group (Lastverteilung).
- **Frontend:** mehrere Instanzen nur mit Sticky Sessions (NiceGUI ist zustandsbehaftet); im MVP eine Instanz.
- **SurrealDB:** im MVP Einzelinstanz; Cluster-Modus erst bei größerem Datenaufkommen.
- **Hinweis:** Der example.com-Server teilt sich Ressourcen mit anderen Stacks (z. B. the predecessor project); Skalierung dort nur nach Kapazitätsprüfung.

## 13. Ressourcenbedarf (Richtwerte für example.com)

| Dienst         | CPU       | RAM    | Speicher (Volumen) |
| -------------- | --------- | ------ | ------------------ |
| pim-core       | 1–2 vCPU  | 512 MB | –                  |
| pim-frontend   | 1 vCPU    | 512 MB | –                  |
| pim-worker     | 1 vCPU    | 512 MB | –                  |
| surrealdb      | 1–2 vCPU  | 1 GB   | 10–50 GB           |
| nats           | 0.5 vCPU  | 256 MB | 5–20 GB            |
| object-storage | 1 vCPU    | 512 MB | 50–500 GB          |
| redis (opt.)   | 0.5 vCPU  | 256 MB | 1–5 GB             |

Diese Werte sind als Ausgangspunkt gedacht; auf dem gemeinsamen Server ist die tatsächlich verfügbare Kapazität (VPS-Paket) zu berücksichtigen.

## 14. Offene Punkte & Annahmen

- **Hostnamen:** Es gelten `meadow.pim.example.com` (UI) und `meadow.api.example.com` (Core).
- **Registry: entschieden** — `registry.example.com/meadow` (Forgejo desselben Servers, wie the reference stack); der Pfad ist kleingeschrieben, Forgejo weist Großbuchstaben ab. GHCR bleibt eine Alternative.
- **Rust-Toolchain: entschieden** — `rust:1.97-bookworm`; die zuvor deklarierte MSRV 1.90 trägt `diskann` 0.54 (über `surrealdb`) nicht: der erste Produktions-Image-Build brach dort mit 11 × E0311 ab. `Cargo.toml` deklariert jetzt 1.97.
- **NATS-Healthcheck:** Das `nats`-Image hat keine Shell, also ist kein Container-eigener Check möglich; geprüft wird der Monitoring-Port 8222 vom Host (`healthcheck-vps.sh`). Ein `nats-server`-Check wäre erst mit einem anderen Image möglich.
- **Dev-Restore:** `scripts/restore-db.ps1` hängt noch an `surreal import` (Abschnitt 10) — für die Dev-Datenbank bewusst offen.
- **BMEcat-Validierung:** braucht `resources/` (nicht in Git/Image) plus `BMECAT_XSD` und `lxml` im Image; ohne beides meldet der Export `validated: false` mit Begründung.
- **`meadow_storage`** ist reserviert und heute ungenutzt (Abschnitt 9).
- **CI:** `.forgejo/workflows/ci.yml` existiert, aber es ist **kein Runner registriert** (bewusst zurückgestellt) — die Pipeline läuft nicht; bis dahin schützen die lokalen Gates plus `tests/test_deployment.py`.
- **SurrealDB:** Für das MVP als separater Container (nicht embedded) festgelegt.
- **Objektstorage:** MinIO als separater Container; alternativ Host-Pfad-Mount.
- **Schema-Migration:** Umgesetzt über `migrations/` + `scripts/run-migration.sh` (Abschnitt 6); das Basisschema bleibt beim Kern.
