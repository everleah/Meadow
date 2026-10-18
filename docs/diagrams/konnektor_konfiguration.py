"""Source: plan/detail/konnektor-konfiguration.md, stages 1-3 and section 14."""

from _shared import Artifact, Cluster, Core, Database, External, Python, diagram, link


def build() -> None:
    with diagram(
        "Konnektoren | Schemagesteuert konfigurieren, Secrets getrennt halten",
        "konnektor_konfiguration",
    ):
        schema = Artifact(
            "GET /connector-types\nSchema / Defaults / Richtung\nEigene Typen: wired=false"
        )
        form = Python("NiceGUI-Formular\nSchemafelder / JSON-Modus")
        with Cluster("Rust-Kern"):
            core = Core("Validieren / Speichern / Testen\nFeldfehler; strict optional")
            config = Database("connector.parameters\nnur Secret-Referenzen")
            secrets = Database("connector_secret\nnur Maschinen-Aufrufer\nkann auflösen")
            audit = Database("audit_log\nsecrets_changed: nur Pfade")
        worker = Python("Worker\nLive-Reload / connector_test\nZustand nur bei Wechsel melden")
        target = External("Webhook-Ziel / Import-Exportprofil")

        schema >> link("Formular steuern") >> form
        form >> link("REST; leer = Secret unverändert\nLöschen explizit") >> core
        core >> link("Normale Parameter") >> config
        core >> link("Secret-Felder auslagern") >> secrets
        core >> link("Ohne Secret-Werte") >> audit
        [config, secrets] >> link("Über Kern-API; nur Maschine") >> worker
        core >> link("Testjob über NATS", async_=True) >> worker
        worker >> link("Test / Zustellung") >> target
        worker >> link("REST: last_success / last_error") >> core


if __name__ == "__main__":
    build()
