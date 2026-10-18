"""Source: plan/detail/relationen.md, composition and aggregation contracts."""

from _shared import Cluster, Core, Database, diagram, link


def build() -> None:
    with diagram("Relationen | Ein Kompositionsbaum, viele Aggregationen", "relationen"):
        with Cluster("Komposition | Wertvererbung und Löschkaskade"):
            root = Database("Universal-Root\nstrukturell unveränderlich")
            parent = Database("Elternobjekt")
            child = Database("Kindobjekt\nmit Elternteil anlegen")
            child >> link("depends_on\nKind → Elternteil") >> parent
            parent >> link("depends_on") >> root
        with Cluster("Aggregation | n:m, keine Vererbung / Kaskade"):
            category = Database("Taxonomieknoten\ncategory_member / eigene Dimension")
            collection = Database("Collection\ncollection_member")
            related = Database("Weiteres Fachobjekt\nrelated / eigener Linktyp")
        core = Core("PIM Core\nZyklus- / Typprüfung\nRe-Parenting atomar + Audit")

        child >> link("Rolle category") >> category
        child >> link("Rolle collection") >> collection
        child >> link("Rolle link; ggf. Kantenattribute") >> related
        core >> link("PATCH parent\nkein DELETE der Kompositionskante") >> child


if __name__ == "__main__":
    build()
