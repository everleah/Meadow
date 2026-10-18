"""Source: plan/detail/datenexport-import-ui.md, export/import and delivery paths."""

from _shared import Artifact, Core, Database, External, Nats, ObjectStorage, Python, diagram, link


def build() -> None:
    with diagram(
        "Daten-Import und -Export | Geführter Auftrag mit Bericht", "datenexport_import_ui"
    ):
        wizard = Python(
            "NiceGUI-Assistenten\nQuelle / Felder / Sprache / Format\nImport-Mapping / Vorschau"
        )
        core = Core("PIM Core\n/imports / /exports\nPlanen oder sofort starten")
        run = Database("job_run\nStatus / result_summary\nvollständiges Resultat separat")
        bus = Nats("NATS JetStream\nImport- / Exportjob")
        worker = Python("Worker\nTabellenformate / MEF / BMEcat\nDatenzugriff nur per REST")
        download = Artifact("Download / Importbericht\nCSV / XLSX / MEF / BMEcat")
        storage = ObjectStorage("Optionales S3-Archiv\nExportdatei / Download-URL")
        smtp = External("Optionaler SMTP-Versand\nExport als Anhang")

        wizard >> link("REST; editor+") >> core
        core >> link("Lauf anlegen / aktualisieren") >> run
        core >> link("Auftrag", async_=True) >> bus
        bus >> link("Verarbeiten", async_=True) >> worker
        worker >> link("REST: Lesen / Schreiben / Ergebnis") >> core
        core >> link("Polling / einzelner Report") >> wizard
        run >> link("Einzellauf abrufen") >> download
        worker >> link("Archivieren", optional=True) >> storage
        worker >> link("Zustellen", optional=True) >> smtp


if __name__ == "__main__":
    build()
