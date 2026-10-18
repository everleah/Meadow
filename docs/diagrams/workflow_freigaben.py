"""Source: plan/detail/workflow-freigaben.md, guards and per-channel releases."""

from _shared import Cluster, Core, Database, Operation, Python, User, diagram, link


def build() -> None:
    with diagram(
        "Workflow | Globaler Status und Kanalfreigabe sind unabhängig", "workflow_freigaben"
    ):
        user = User("Redakteur")
        core = Core("PIM Core\nStatuswechsel / Freigabe anfordern")
        guard = Python(
            "Worker / RestrictedPython\nworkflow_guard über NATS\nctx.channel / ctx.release_status"
        )
        with Cluster("Unabhängige Statusspeicher"):
            obj = Database("object.status\nglobaler Workflow")
            release = Database(
                "release.status\nje Objekt × Kanal eindeutig\nZurückziehen entfernt Zeile"
            )
            channel = Database("channel\nKonfigurationskatalog")
        audit = Database("audit_log\nStatus- / Freigabeänderung")
        export = Operation("Export / Outbound\nnach Status oder Kanal filtern")

        user >> link("Aktion") >> core
        core >> link("Blockierend prüfen") >> guard
        guard >> link("Erlaubt / abgelehnt") >> core
        core >> link("Bei erlaubtem globalem Übergang") >> obj
        core >> link("Bei erlaubter Kanaländerung") >> release
        release >> link("Referenziert") >> channel
        core >> link("Protokollieren") >> audit
        [obj, release] >> link("Auswahlgrundlage") >> export


if __name__ == "__main__":
    build()
