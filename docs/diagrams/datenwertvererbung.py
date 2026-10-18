"""Source: plan/detail/datenwertvererbung.md, resolution and composition batching."""

from _shared import Artifact, Cluster, Core, Database, Decision, diagram, link


def build() -> None:
    with diagram("Wertvererbung | Eigener Wert vor dem nächsten Vorfahren", "datenwertvererbung"):
        request = Artifact("Lesen mit Daten-Locale\nauflösen je Attribut")
        with Cluster("Rust-Kern | keine Vererbung über Aggregationen"):
            resolver = Core("Resolver\nnur inheritable; nie Business-Keys")
            own = Decision("Eigener Wert vorhanden?\nSprachauflösung im Objekt zuerst")
            parent = Database("Nächster Kompositionsvorfahr\nüber depends_on")
            stop = Decision("Wert gefunden?\nNächster Vorfahr bis Root")
        output = Artifact("Effektiver Wert + Herkunft\nkeine Kopie im Kind speichern")
        empty = Artifact("Explizit leer / null\nVererbung unterbrochen")
        missing = Artifact("Kein effektiver Wert\nauch am Root nicht gepflegt")

        request >> link("Auflösen") >> resolver
        resolver >> link("Kind prüfen") >> own
        own >> link("Ja: gesetzter Wert") >> output
        own >> link("Bewusst leer") >> empty
        own >> link("Schlüssel fehlt") >> parent
        parent >> link("Sprachwert auflösen") >> stop
        stop >> link("Gesetzter Wert") >> output
        stop >> link("Bewusst leer") >> empty
        stop >> link("Fehlt; weiterer Vorfahr vorhanden") >> parent
        stop >> link("Fehlt am Root") >> missing


if __name__ == "__main__":
    build()
