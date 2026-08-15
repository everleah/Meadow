# Betrieb & Deployment

- **Hauptdokument:** Abschnitt 12 ([Betrieb & Deployment](../meadow-pim-architektur.md))
- **Relevante Phasen:** Phase 8
- **Stand:** Entwurf

Alle Komponenten werden als Docker-Container betrieben. Die Standardauslieferung erfolgt als **Docker-Compose-Setup**, das für KMU-Umgebungen ausreicht. Eine spätere Orchestrierung über Kubernetes (z. B. k3s) ist vorbereitet, aber nicht Bestandteil des MVP.

## Container-Übersicht

| Dienst               | Image                                | Beschreibung                                           |
|----------------------|--------------------------------------|--------------------------------------------------------|
| `pim-core`           | Rust (eigenes Image, multi-stage)    | Headless REST-API + Scheduler + Eventsystem            |
| `pim-frontend`       | Python + NiceGUI                     | Web-Oberfläche                                         |
| `pim-worker`         | Python + nats-py                     | Hintergrundverarbeitung (Jobs, Webhooks)               |
| `surrealdb`          | Offizielles SurrealDB-Image          | Datenbank (kann auch embedded im Kern laufen)          |
| `nats`               | Offizielles NATS-Image mit JetStream | Message Queue / Event-Bus                              |
| `object-storage`     | MinIO oder S3-kompatibel             | Binärdaten (Bilder, Exportdateien)                     |
| `redis` (optional)   | Offizielles Redis-Image              | Cache für Vererbungs-/Sprachauflösung und Suche        |

## Netzwerk & Sicherheit

- **Öffentlicher Zugang:** Nur der `pim-frontend`-Container ist über HTTPS erreichbar (Reverse Proxy, z. B. Traefik, Caddy oder Nginx).
- **Internes Netz:** `pim-core`, `surrealdb`, `nats`, `object-storage`, `redis` und `pim-worker` befinden sich in einem privaten Docker-Netzwerk und sind nicht direkt aus dem Internet erreichbar.
- **Kommunikation:**
  - Frontend → Core: REST (JWT)
  - Worker → Core: REST (JWT)
  - Core → NATS: Publish (intern)
  - Worker → NATS: Subscribe (intern)
  - Core → SurrealDB: interner Treiber / Netzwerk
- **Secrets:** Zugangsdaten (Datenbank, NATS, Objektstorage, JWT-Secret) werden über Umgebungsvariablen oder Docker Secrets injiziert und nicht in Images eingebacken.
- **TLS:** Die externe Kommunikation läuft ausschließlich über HTTPS (TLS-Terminierung am Reverse Proxy).

## Persistenz & Volumes

- **SurrealDB:** Datenbankdatei in einem benannten Volume oder Host-Pfad (`/data`).
- **NATS JetStream:** Persistenter Store in einem Volume (`/data/jetstream`).
- **Objektstorage:** Datenverzeichnis in einem Volume (bei MinIO: `/data`).
- **Redis (optional):** Persistenz konfigurierbar; für Cache-Nutzung kann AOF/RDB aktiviert oder deaktiviert werden.

## Konfiguration

Jeder Dienst wird über eine eigene Konfigurationsdatei (z. B. `config.yaml` oder Umgebungsvariablen) parametrisiert. Zentrale Einstellungen im Compose-File:

- `PIM_CORE_URL` (für Frontend und Worker)
- `SURREALDB_URL`, `SURREALDB_USER`, `SURREALDB_PASS`
- `NATS_URL`
- `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`
- `JWT_SECRET`, `JWT_EXPIRATION`
- `LOG_LEVEL` (für alle Dienste)
- `WEBHOOK_RETRY_MAX`, `WEBHOOK_RETRY_BACKOFF` (Worker)

## Skalierung

- **Rust-Kern:** Kann horizontal skaliert werden (mehrere Instanzen hinter Load Balancer). Achtung: Scheduler darf nur in einer Instanz aktiv sein (Leader-Election oder dedizierte Instanz mit `scheduler.enabled=true`).
- **Python-Worker:** Skalierung über mehrere Container, die sich eine NATS-Queue-Group teilen (automatische Lastverteilung).
- **Frontend:** Bei Bedarf mehrere Instanzen hinter Load Balancer (sticky sessions erforderlich, da NiceGUI zustandsbehaftet ist).
- **SurrealDB:** Im MVP als Einzelinstanz (embedded oder separater Dienst). Cluster-Modus ist möglich, wird aber erst bei größerem Datenaufkommen relevant.

## Backup & Wiederherstellung

- **SurrealDB:** Regelmäßige Exporte (`SURREAL EXPORT`) oder Dateisicherung des Volumes. Zeitplan über Host-Cron oder externes Tool.
- **Objektstorage:** Synchronisation der Buckets auf einen zweiten Speicherort (z. B. S3-Replikation, rsync bei MinIO).
- **NATS JetStream:** Snapshots des Streams optional; für den MVP genügt die Wiederherstellung aus der Quelle (z. B. erneuter Export), da Events rekonstruierbar sind.
- **Wiederherstellungstests:** Mindestens vierteljährlich durchführen.

## Monitoring & Logging

- **Logging:**
  - Rust-Kern: strukturierte Logs mit `tracing` (JSON-Format optional)
  - Python-Dienste: `structlog` mit einheitlichem Format
  - Logs werden auf `stdout` geschrieben und können über Docker-Logging-Treiber oder zentrale Systeme (z. B. Loki, ELK) gesammelt werden.
- **Health-Checks:**
  - Jeder Container stellt einen Health-Endpoint bereit (z. B. `GET /health` im Kern, einfache HTTP-Checks für NATS/SurrealDB).
  - Docker Compose überwacht die Container und startet sie bei Ausfall neu.
- **Metriken (optional, ab Phase 8):**
  - Prometheus-Endpunkte im Kern und Worker (z. B. Anzahl Jobs, Event-Durchsatz, API-Latenz)
  - Grafana-Dashboards für Betriebsübersicht

## Deployment-Varianten

- **MVP (Standard):** Docker Compose auf einer einzelnen VM oder einem kleinen Server. Alle Dienste laufen gemeinsam, getrennt durch interne Netzwerke.
- **Erweiterte Variante:** Aufteilung auf mehrere VMs (z. B. Datenbank auf separatem Server, Worker auf zweitem Server) mittels Docker Swarm oder manuellem Setup.
- **Zukunft:** Migration zu Kubernetes (k3s) für automatische Skalierung und Self-Healing.

## Ressourcenbedarf (Richtwerte für KMU)

| Dienst          | CPU     | RAM   | Speicher (Volumen) |
|-----------------|---------|-------|--------------------|
| pim-core        | 1–2 vCPU| 512 MB| –                  |
| pim-frontend    | 1 vCPU  | 512 MB| –                  |
| pim-worker      | 1 vCPU  | 512 MB| –                  |
| surrealdb       | 1–2 vCPU| 1 GB  | 10–50 GB           |
| nats            | 0.5 vCPU| 256 MB| 5–20 GB            |
| object-storage  | 1 vCPU  | 512 MB| 50–500 GB          |
| redis (opt.)    | 0.5 vCPU| 256 MB| 1–5 GB             |

Diese Werte sind als Ausgangspunkt gedacht und hängen stark von Objektanzahl, Suchlast und Exportvolumen ab.
