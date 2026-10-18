"""Source: plan/detail/konfiguration-export-import.md, sections 2-9."""

from _shared import Artifact, Cluster, Core, Database, Operation, diagram, link


def build() -> None:
    with diagram(
        "MCF | Modellübertragung und Reihenfolge eines vollständigen Restores",
        "konfiguration_export_import",
    ):
        source = Core("Quellumgebung\nGET /config/export")
        mcf = Artifact(
            "MCF-Dokument\nstabile Namen / Codes / Symbole\nkeine lokalen ULIDs / Secrets"
        )
        with Cluster("Zielumgebung | Rust-Kern schreibt"):
            validate = Core("POST /config/import\nPrüfen / IDs auflösen\ndry_run | merge | replace")
            model = Database("1. Modell / Kataloge\nAbhängigkeitsreihenfolge")
            data = Operation("2. MEF-Datenimport\nObjekte / Relationen / Freigaben")
            features = Database("3. category_attributes\nKlassen-Merkmale")
        report = Artifact("Report / Audit\nasync=true: config_import-Job")

        source >> link("Export") >> mcf
        mcf >> link("Import; Admin") >> validate
        validate >> link("Anwenden; dry_run schreibt nichts") >> model
        model >> link("Vor den Daten") >> data
        data >> link("Taxonomieknoten existieren jetzt") >> features
        mcf >> link("Sektion nach dem MEF einspielen") >> features
        validate >> link("Ergebnis / Konflikte") >> report


if __name__ == "__main__":
    build()
