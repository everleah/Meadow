"""Source: plan/detail/datenmodell.md, generic objects and metadata tables."""

from _shared import Cluster, Core, Database, diagram, link


def build() -> None:
    with diagram("Datenmodell | Generische Objekte und ihre Metadaten", "datenmodell"):
        core = Core("PIM Core\nalle Datenbank-Writes")
        with Cluster("SurrealDB | logische Entitäten, keine getrennten Dienste"):
            objects = Database("object\nTyp + attributes (JSON)")
            types = Database("object_type\nflache Registry / Business-Keys")
            attributes = Database("attribute_definition\nTyp / Pflicht / Vererbung")
            groups = Database("attribute_group\nDarstellungsgruppen")
            references = Database("reference\nnative Graph-Kanten")
            definitions = Database("relation_definition\nArt / Rolle / Einschränkungen")
            release = Database("release + channel\nObjekt × Kanal")
            languages = Database("language_tree\nSprach-Fallback")
            audit = Database("audit_log\nfachliche Änderungen")

        core >> link("Lesen / Schreiben") >> objects
        objects >> link("Gehört zu genau einem Typ") >> types
        attributes >> link("applicable_types / required_types") >> types
        attributes >> link("group_ids") >> groups
        attributes >> link("Beschreibt Werte in attributes") >> objects
        objects >> link("Quelle / Ziel") >> references
        references >> link("Definition") >> definitions
        objects >> link("Kanalspezifische Freigaben") >> release
        languages >> link("Auflösung lokalisierter Werte") >> objects
        core >> link("Änderungshistorie") >> audit


if __name__ == "__main__":
    build()
