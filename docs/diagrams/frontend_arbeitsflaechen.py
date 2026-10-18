"""Source: plan/detail/frontend-arbeitsflaechen.md, sections 1-5."""

from _shared import Cluster, Core, Python, diagram, link


def build() -> None:
    with diagram(
        "Arbeitsflächen | Eine Auswahl, drei Bearbeitungswege", "frontend_arbeitsflaechen"
    ):
        with Cluster("NiceGUI"):
            dashboard = Python("Dashboard\nVollständigkeit / Aufgaben")
            grid = Python("Grid\nBaum / Filter / Auswahl")
            form = Python("Produktformular\nEinzelobjekt / Sprachwerte")
            bulk = Python("Bulk-Wizard\nOperation / Scope")
            report = Python("Fortschritt und Bericht\nREST-Polling")
        core = Core("PIM Core\nValidierung / Rechte / Vollständigkeit")
        worker = Python("Worker\nMassenoperationen")

        dashboard >> link("Gezielter Einstieg") >> grid
        grid >> link("Drill-down") >> form
        grid >> link("Auswahl / Filtermenge") >> bulk
        grid >> link("Inline-Batch-Save\nmax. 500 Zeilen") >> core
        form >> link("Einzelobjekt speichern") >> core
        bulk >> link("Hintergrundjob starten") >> core
        core >> link("Job über NATS", async_=True) >> worker
        worker >> link("REST: Writes / Status") >> core
        core >> link("Status / result_summary\nBericht separat") >> report


if __name__ == "__main__":
    build()
