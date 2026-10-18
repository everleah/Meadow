"""Source: plan/detail/vererbung-konsolidierung.md, preview and application."""

from _shared import Artifact, Cluster, Core, Decision, Operation, Python, diagram, link


def build() -> None:
    with diagram(
        "Konsolidierung | Erst Vorschau, dann Elternwert vor Kindkopien", "vererbung_konsolidierung"
    ):
        ui = Python("Bulk-Wizard\nTeilbaum / Attribute")
        with Cluster("Worker | expliziter Job, kein automatisches Aufräumen"):
            worker = Python("Direkte Kinder lesen\nTop-down je Elternobjekt")
            check = Decision(
                "Alle Kinder identisch gepflegt?\n"
                "inheritable, kein Key / computed\nmin_children = 2"
            )
            report = Artifact("Dry-Run-Report\nmoves[] am job_run\nkeine Objektänderungen")
        apply = Operation("Nutzer bestätigt\nAnwendungsjob starten")
        parent = Core("1. Elternwert schreiben\nnormaler PATCH-Pfad")
        children = Core("2. Kindkopien entfernen\nremove_attributes")

        ui >> link("Vorschaujob") >> worker
        worker >> link("Kandidaten vergleichen") >> check
        check >> link("Vorschläge; sonst überspringen") >> report
        report >> link("Prüfen") >> apply
        apply >> link("Werte erneut prüfen") >> parent
        parent >> link("Erst nach erfolgreichem Eltern-Write") >> children


if __name__ == "__main__":
    build()
