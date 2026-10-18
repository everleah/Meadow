"""Source: plan/detail/betrieb-deployment.md, sections 1-7 and 10."""

from _shared import (
    Artifact,
    Browser,
    Cluster,
    Core,
    Database,
    External,
    Nats,
    ObjectStorage,
    Python,
    Traefik,
    diagram,
    link,
)


def build() -> None:
    with diagram(
        "Deployment | Lokal bauen, VPS pullt, nur UI und API öffentlich", "betrieb_deployment"
    ):
        workstation = External("Entwicklungsrechner\nDocker-Images bauen")
        registry = External("Container-Registry\nunveränderlicher Git-Tag")
        browser = Browser("Browser / API-Client")
        with Cluster("VPS"):
            proxy = Traefik("Traefik\nTLS / externes Netz")
            with Cluster("Meadow | Docker Compose / meadow-backend"):
                frontend = Python("pim-frontend\nNiceGUI :8081")
                core = Core("pim-core\nAxum :8080")
                worker = Python("pim-worker\nkein öffentlicher Port")
                db = Database("SurrealDB\nVolume")
                bus = Nats("NATS JetStream\nVolume")
                storage = ObjectStorage("S3 / MinIO\nVolume")
            backup = Artifact("Backups\nDB-Export + S3-Sicherung\nRestore: SQL in Stücken")

        workstation >> link("Build + Push") >> registry
        registry >> link("Pull → Infrastruktur → Migrationen\n→ Anwendung starten") >> core
        browser >> link("HTTPS") >> proxy
        proxy >> link("UI-Host / WebSocket") >> frontend
        proxy >> link("API-Host / REST") >> core
        frontend >> link("REST") >> core
        worker >> link("REST / Maschinen-API-Key") >> core
        core >> link("Intern") >> db
        core >> link("Jobs / Events", async_=True) >> bus
        bus >> link("Verarbeiten", async_=True) >> worker
        worker >> link("Dateien") >> storage
        [db, storage] >> link("Sichern") >> backup


if __name__ == "__main__":
    build()
