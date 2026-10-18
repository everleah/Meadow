"""Source: plan/meadow-pim-architektur.md, sections 2 and 3."""

from _shared import (
    Browser,
    Cluster,
    Core,
    Database,
    External,
    Nats,
    ObjectStorage,
    Python,
    diagram,
    link,
)


def build() -> None:
    with diagram("Meadow PIM | Subsysteme und Hauptbeziehungen", "overview"):
        browser = Browser("Web-Browser")
        with Cluster("Meadow PIM | Docker Compose"):
            frontend = Python("Frontend\nNiceGUI")
            core = Core("PIM Core\nRust / Axum / OpenAPI")
            worker = Python("Worker\nasyncio / httpx / RestrictedPython")
            database = Database("SurrealDB\nDokumente + Graph")
            bus = Nats("NATS JetStream\nJobs / Events / Rule-RPC")
            storage = ObjectStorage("S3-kompatibler\nObjektstorage")
        middleware = External("Externe Systeme\nMiddleware")

        browser >> link("HTTPS / WebSocket") >> frontend
        frontend >> link("REST / JWT") >> core
        core >> link("Lesen / Schreiben") >> database
        core >> link("Binärdaten") >> storage
        core >> link("Jobs / Events (async)\nRegeln (Request / Reply)") >> bus
        bus >> link("Jobs / Events abonnieren\nRegeln ausführen + antworten") >> worker
        worker >> link("REST / Maschinen-API-Key") >> core
        worker >> link("Import- / Exportdateien") >> storage
        worker >> link("HTTP / Webhooks") >> middleware


if __name__ == "__main__":
    build()
