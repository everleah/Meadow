"""Source: plan/detail/suche-formular.md, stages A-C and sections 4-6."""

from _shared import Artifact, Cluster, Core, Database, Operation, Python, diagram, link


def build() -> None:
    with diagram("Suchformular | Ein Katalog und eine gemeinsame Query-DSL", "suche_formular"):
        metadata = Database("Metadaten\nAttribute / Typen / Sprachen\nEnums / Relationstypen")
        with Cluster("Rust-Kern"):
            catalog = Core("GET /search-fields\nFelder / Operatoren / Widgets")
            validator = Core("DSL validieren\nPfadgenaue Fehlermeldungen")
            execute = Core("Gemeinsamer Query-Ausführer\nScope erzwingen")
        editor = Python("NiceGUI Query-Builder\nFormular / JSON")
        query = Artifact("where-Baum\nUND / ODER / Typwerte\nReferenzen / Sortierung")
        saved = Database("Gespeicherte Suche\nquery")
        grid = Operation("Grid-Ergebnis / Vorschau\nGlobaler Volltext; kein Feldindex")

        metadata >> link("Server-Vertrag") >> catalog
        catalog >> link("Formular steuern") >> editor
        editor >> link("Erzeugen / bearbeiten") >> query
        query >> link("Vorschau / Speichern / Grid") >> validator
        validator >> link("Gültige Definition speichern") >> saved
        validator >> link("Vorschau oder Grid-Abfrage") >> execute
        saved >> link("Ausführen") >> execute
        execute >> link("Treffer") >> grid


if __name__ == "__main__":
    build()
