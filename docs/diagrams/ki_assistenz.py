"""Source: plan/detail/ki-assistenz.md, sections 3-6 and 13."""

from _shared import Cluster, Core, Database, External, Nats, Operation, Python, diagram, link


def build() -> None:
    with diagram("KI-Assistenz | Vorschlagen, prüfen, bewusst speichern", "ki_assistenz"):
        editor = Python("NiceGUI-Editor\nBeschreibung / Vorschlag")
        core = Core("PIM Core\nOpt-in / Rechte / Settings / Audit")
        bus = Nats("NATS\nai.generate / ai.test")
        with Cluster("Python-Worker"):
            worker = Python("Kontextprojektion\nSchema / Metadaten\nkeine Objektwerte")
            checks = Operation(
                "Prüfkette je Bereich\nAST + Sandbox / Kernvalidator\nImport-Mapping-Prüfung"
            )
        provider = External("Konfigurierter LLM-Provider\nOpenAI-kompatibles HTTPS")
        review = Operation("Mensch prüft Entwurf\nkein automatisches Speichern")
        audit = Database("audit_log\nMetadaten / Ziel / Tokens\nkein Prompt / Skript / Secret")

        editor >> link("REST: Generieren") >> core
        core >> link("Konfiguration + Key intern\nniemals im Prompt") >> bus
        bus >> link("Request / Reply") >> worker
        worker >> link("Projizierter Kontext") >> provider
        provider >> link("Entwurf") >> checks
        checks >> link("Max. ein Korrekturversuch") >> worker
        checks >> link("Geprüfter Vorschlag via NATS") >> core
        core >> link("Ergebnis") >> editor
        core >> link("Jeden Aufruf protokollieren") >> audit
        editor >> link("Übernehmen ins Feld") >> review
        review >> link("Normaler Speicherpfad") >> core


if __name__ == "__main__":
    build()
