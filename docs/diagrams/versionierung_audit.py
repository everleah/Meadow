"""Source: plan/detail/versionierung-audit.md, version fields and event distinction."""

from _shared import Cluster, Core, Database, External, Nats, Operation, User, diagram, link


def build() -> None:
    with diagram(
        "Versionierung und Audit | Zustand, Historie und Nachrichten", "versionierung_audit"
    ):
        actor = User("Benutzer / System")
        core = Core("PIM Core\nfachliche Änderung")
        with Cluster("Persistente Daten"):
            obj = Database("object / attribute_definition\nversion + Zeitstempel + Akteur")
            audit = Database("audit_log\nwer / was / wann\nfachliche Änderungshistorie")
            outbox = Database("outbox_event\natomar mit dem Write")
        relay = Operation("Outbox-Relay\nkein Ersatz für Audit")
        bus = Nats("NATS JetStream\nbegrenzte Nachrichten-Retention")
        consumer = External("Asynchrone Verbraucher\nWebhooks / Integration")

        actor >> link("Ändern") >> core
        core >> link("Zustand / Version fortschreiben") >> obj
        core >> link("Dauerhaft protokollieren") >> audit
        core >> link("Ereignis vormerken") >> outbox
        outbox >> link("Nach Transaktion") >> relay
        relay >> link("Publizieren", async_=True) >> bus
        bus >> link("Zustellen", async_=True) >> consumer


if __name__ == "__main__":
    build()
