"""Source: plan/detail/gespeicherte-suchen-collections.md, execution and refresh."""

from _shared import Artifact, Core, Database, Operation, Python, diagram, link


def build() -> None:
    with diagram(
        "Suchen und Collections | Dynamische Query, materialisierte Mitgliedschaft",
        "gespeicherte_suchen_collections",
    ):
        editor = Python("Admin-Sucheditor\nFormular / JSON")
        search = Database("saved_search\nvalidierte Query-DSL")
        execute = Core("Rust Query-Ausführer\nFilter / Sortierung / Scope")
        result = Artifact("Aktuelle Treffer-IDs")
        refresh = Python("collection_refresh-Job\nmanuell oder per Cron\nWorker ruft Kern-API")
        members = Database("collection + collection_member\nmaterialisierte Objektmenge")
        consume = Operation("Grid / Export / Business Rules\nCollection als Quelle")

        editor >> link("Speichern") >> search
        search >> link("Ausführen") >> execute
        execute >> link("Dynamisches Ergebnis") >> result
        search >> link("Query-Quelle") >> refresh
        refresh >> link("Suche ausführen lassen") >> execute
        result >> link("Neue Mitgliedschaft") >> members
        refresh >> link("Atomar ersetzen lassen\nalte Menge bleibt bis dahin lesbar") >> members
        members >> link("Stabile Auswahl bis Refresh") >> consume


if __name__ == "__main__":
    build()
