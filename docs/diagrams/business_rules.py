"""Source: plan/detail/business-rules.md, rule execution and lifecycle."""

from _shared import Cluster, Core, Database, Nats, Operation, Python, diagram, link


def build() -> None:
    with diagram(
        "Business Rules | Ein Skriptvertrag, definierte Ausführungskontexte", "business_rules"
    ):
        author = Python(
            "Admin / Rule Lab\nBearbeiten / Compile / Dry-Run\nLab schreibt keine Objekte"
        )
        rules = Database("business_rule\ndraft / active / disabled\nnur active wird ausgeführt")
        core = Core("Rust-Schreibpfad\nvalidation / computed\nworkflow_guard")
        bus = Nats("NATS\nrules.compile / rules.evaluate")
        with Cluster("Worker | keine externe I/O in Regeln"):
            sandbox = Python("RestrictedPython\nrun(obj, ctx, result)\nLimits / Circuit Breaker")
            imports = Operation("Import-Pipeline\nnormalization")
            bulk = Operation("business_rule-Job\nbulk / Recompute")
        write = Database("Atomarer Objekt-Write\nFehler blockieren\ncomputed-Werte speichern")

        author >> link("Prüfen / verwalten") >> rules
        rules >> link("Aktive Regeln laden") >> core
        core >> link("Synchrones Request / Reply") >> bus
        bus >> link("Compile / Ausführen") >> sandbox
        [imports, bulk] >> link("Gleiche Sandbox") >> sandbox
        sandbox >> link("Fehler / Warnungen / Werte\nAntwort über NATS") >> core
        core >> link("Fail-closed; kein On-Read-Rechnen") >> write


if __name__ == "__main__":
    build()
