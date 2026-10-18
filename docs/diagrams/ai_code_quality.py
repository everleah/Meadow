"""Source: plan/detail/ai-code-quality.md, sections 2 and 5-8."""

from _shared import Artifact, Cluster, Operation, diagram, link


def build() -> None:
    with diagram("Codequalität | Manuelle Signale, verbindliche Gates", "ai_code_quality"):
        scope = Artifact("Bewusst gewählter Scope\nall / changed / staged")
        with Cluster("Manuelle Analyse | kein Ersatz für Korrektheits-Gates"):
            scan = Operation("stopslop\nPython / Rust / Text\nlokal, deterministisch")
            deep = Operation("ai-slop-detector\noptionaler Python-Deep-Dive")
            findings = Artifact("Befunde unter .tmp/deslop\nHinweise, kein Laufzeitbeweis")
        triage = Operation("Triage\nFehler / berechtigt / Fehlalarm")
        repair = Operation("Kleiner Reparaturpass\nkeine automatische Reparatur")
        gates = Operation(
            "Engster Verhaltensnachweis\nRuff / mypy / pytest\nrustfmt / Clippy / cargo test"
        )
        review = Operation("Review und Abnahme\nverbleibende Befunde begründen")

        scope >> link("Primärscan") >> scan
        scope >> link("--fix-plan", optional=True) >> deep
        [scan, deep] >> link("Analysebericht") >> findings
        findings >> link("Mensch bewertet") >> triage
        triage >> link("Belegten Fehler wählen") >> repair
        repair >> link("Erst fokussiert, dann Gates") >> gates
        gates >> link("Nachweis") >> review


if __name__ == "__main__":
    build()
