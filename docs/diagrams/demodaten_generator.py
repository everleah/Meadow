"""Source: plan/detail/demodaten-generator.md, sections 4, 6 and 7."""

from _shared import Artifact, Cluster, Core, Operation, Python, diagram, link


def build() -> None:
    with diagram(
        "Demodaten | Deterministisch erzeugen und geordnet einspielen", "demodaten_generator"
    ):
        catalog = Artifact("Eigener Automotive-Katalog\nSeed + tiny / demo / perf")
        generator = Python("scripts/demo_data.py\nsynthetische Daten erzeugen")
        with Cluster("Importreihenfolge | REST / Jobs, kein direkter DB-Write"):
            model = Artifact("1. model.mcf.json\nModell")
            masters = Artifact("2. masters.mef.json\nGeteilte Objekte; abwarten")
            chunks = Artifact(
                "3. data-<nn>.mef.json\nGeschlossene Chunks\nperf: sequenziell / Release-Kern"
            )
            features = Artifact("4. class-features.mcf.json\nKlassen-Merkmale")
            extras = Artifact("5. extras.json\nFreigaben / Suchen / Jobs / Regeln")
        core = Core("Meadow PIM\nMCF direkt; MEF über Worker\ngebündelte Schreibpfade")
        verify = Operation(
            "6. validate_demo.py\nreport.json gegen Ist-Daten\noptional BMEcat / XSD"
        )

        catalog >> link("Gepflegte Fakten + Parameter") >> generator
        generator >> link("Artefakte erzeugen") >> model
        model >> link("Danach") >> masters
        masters >> link("Fertig abwarten") >> chunks
        chunks >> link("Danach") >> features
        features >> link("Danach") >> extras
        [model, masters, chunks, features, extras] >> link("Einspielen") >> core
        core >> link("Rein lesende Abnahme") >> verify
        generator >> link("report.json als Soll") >> verify


if __name__ == "__main__":
    build()
