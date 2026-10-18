"""Source: plan/detail/einheiten.md, canonical storage and conversion."""

from _shared import Artifact, Cluster, Core, Database, Python, diagram, link


def build() -> None:
    with diagram("Einheiten | Eine Umrechnung, kanonische Speicherung", "einheiten"):
        ui = Python("NiceGUI\nEingabe / Darstellung\ngewählte Einheit")
        incoming = Artifact("MEF-Eingabe\nWert + Einheit")
        with Cluster("Rust-Kern | autoritative Umrechnung"):
            convert = Core(
                "Einheiten-Umrechnung\nDimension prüfen\nKanonisch = Wert × Faktor + Offset"
            )
            units = Database(
                "unit + attribute_definition\n"
                "Dimension / Faktor / Offset\nunit_id / display_unit_id"
            )
            stored = Database("object.attributes\nreine Zahl in kanonischer Einheit")
        output = Artifact(
            "Darstellung / Export\ngewählte oder Standard-Einheit\nkeine Währungsumrechnung"
        )

        ui >> link("REST: umrechnen") >> convert
        incoming >> link("Worker nutzt Kern-Umrechnung") >> convert
        units >> link("Umrechnungsvertrag") >> convert
        convert >> link("Normalisierter Wert") >> stored
        stored >> link("Ausgabe umrechnen") >> convert
        convert >> link("Wert in Zieleinheit") >> output
        output >> link("Anzeigen") >> ui


if __name__ == "__main__":
    build()
