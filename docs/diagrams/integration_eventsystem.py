"""Source: plan/detail/integration-eventsystem.md, MEF, outbox and webhooks."""

from _shared import Cluster, Core, Database, External, Nats, Python, diagram, link


def build() -> None:
    with diagram(
        "Integration | MEF-Inbound und zuverlässiger Event-Outbound", "integration_eventsystem"
    ):
        external = External("Externe Middleware")
        imports = Core("POST /imports\nMEF persistieren\nsofortige job_run-ID")
        jobs = Nats("NATS JetStream\ninbound_import")
        worker = Python("Import-Worker\nNormalisierung / Upsert\nEltern vor Kindern")
        with Cluster("Rust-Kern | eine Schreibtransaktion"):
            core = Core("Objekt- / Referenz-API")
            data = Database("Objekte / Relationen\nFreigaben")
            outbox = Database("outbox_event\natomar mit Fachdaten")
        relay = Core("Outbox-Relay")
        events = Nats("NATS JetStream\nCRUD-Events")
        webhook = Python("Webhook-Worker\nFilter / Coalescing / Retry")

        external >> link("MEF-Dokument") >> imports
        imports >> link("Job", async_=True) >> jobs
        jobs >> link("Verarbeiten", async_=True) >> worker
        worker >> link("REST / match_by_key") >> core
        core >> link("Schreiben") >> data
        core >> link("Ereignis vormerken") >> outbox
        outbox >> link("Offene Events") >> relay
        relay >> link("At-least-once; Reihenfolge je Objekt", async_=True) >> events
        events >> link("Abonnieren", async_=True) >> webhook
        webhook >> link("HTTP / event_id zur Deduplizierung") >> external


if __name__ == "__main__":
    build()
