"""Source: plan/detail/sprachbaum-mehrsprachigkeit.md, fallback and administration."""

from _shared import Artifact, Cluster, Core, Database, Python, diagram, link


def build() -> None:
    with diagram(
        "Datensprachen | Exakter Sprachwert vor dem Fallback", "sprachbaum_mehrsprachigkeit"
    ):
        admin = Python("Sprachbaum-Administration\nactive / is_ui / Elternknoten")
        with Cluster("language_tree | Fallback-Kette als Beispiel"):
            regional = Database("de-AT")
            language = Database("de")
            root = Database("root\nsprachneutraler Wert")
            regional >> link("Sprach-Elternteil") >> language
            language >> link("Sprach-Elternteil") >> root
        values = Database("object.attributes.name\nSprachmap: de-AT / de / root")
        resolver = Core("Sprachresolver\nfallback_order\nexakten Wert zuerst prüfen")
        result = Artifact("Effektiver Wert\nUI / Export / API")

        admin >> link("Baum ändern; fallback_order neu berechnen") >> regional
        regional >> link("Angefragte Locale") >> resolver
        values >> link("Kandidaten") >> resolver
        resolver >> link("Erster vorhandener Wert\nsonst leer") >> result


if __name__ == "__main__":
    build()
