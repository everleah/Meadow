"""Source: plan/detail/bmecat-export.md, mapping and implemented export variants."""

from _shared import Artifact, Cluster, Core, Database, External, Operation, Python, diagram, link


def build() -> None:
    with diagram("BMEcat | Vom PIM-Modell zum validierten Katalog", "bmecat_export"):
        with Cluster("Eingaben aus Meadow PIM"):
            products = Database("Produkte / Varianten\nBusiness-Keys / Werte / Preise")
            taxonomy = Database("Klassifikation / Klassen\nMerkmale / Einheiten / Enums")
            profile = Database("Exportprofil + Kanal\nMapping / Sprache / Transaktion")
        core = Core("PIM Core\nExportauftrag / Daten-API")
        worker = Python("Export-Worker\nMapping / Serialisierung\nBMEcat 2005 oder 1.2")
        xml = Artifact("BMEcat-XML\nKatalog / Produkte / Features\nkein BMEcat-Import")
        validation = Operation(
            "Separate Abnahme\npassende lokale XSD / DTD\nkein automatischer Laufzeit-Schritt"
        )
        partner = External("Katalogempfänger")

        [products, taxonomy, profile] >> link("Aufgelöste Daten / Konfiguration") >> core
        core >> link("Job über NATS; Daten per REST") >> worker
        worker >> link("Serialisieren") >> xml
        xml >> link("Download / Zustellung") >> partner
        xml >> link("Gegen Zielprofil prüfen", optional=True) >> validation


if __name__ == "__main__":
    build()
