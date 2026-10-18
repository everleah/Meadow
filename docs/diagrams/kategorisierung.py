"""Source: plan/detail/kategorisierung.md, dimensions, taxonomies and class features."""

from _shared import Cluster, Core, Database, Python, diagram, link


def build() -> None:
    with diagram("Kategorisierung | Gemeinsamer Baum, unabhängige Dimensionen", "kategorisierung"):
        product = Database("Produkt / Fachobjekt")
        with Cluster("Gemeinsame Taxonomie | normale object-Datensätze"):
            parent = Database("Kategorie / Klasse\nobject_type.taxonomy = true")
            child = Database("Unterkategorie / Unterklasse")
            child >> link("depends_on\nKind → Elternteil") >> parent
        definitions = Database("relation_definition\nrole = category\nprimär + eigene Dimensionen")
        features = Database("category_attribute\nKlassen-Merkmale / Pflicht / Position")
        core = Core("PIM Core\nZähler / Filter / Vollständigkeit")
        ui = Python("Kategorienavigation / Grid\nDimension wählen")

        product >> link("category_member\nprimäre Zuordnung n:m") >> child
        product >> link("Zusatz-Kategorisierung n:m\nkeine Wertvererbung") >> parent
        definitions >> link("Bestimmt Zuordnungsdimension") >> core
        parent >> link("Deklariert") >> features
        features >> link("Pflichtmerkmale ergänzen") >> core
        ui >> link("Kategorie + definition_id\nBaum bleibt derselbe") >> core
        core >> link("Passende Objekte / Zähler") >> ui


if __name__ == "__main__":
    build()
