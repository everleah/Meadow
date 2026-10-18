"""Source: plan/detail/benutzer-gruppen-rechte.md, stages A-D and performance rules."""

from _shared import Artifact, Cluster, Core, Database, Operation, User, diagram, link


def build() -> None:
    with diagram(
        "Rechte | Beim Login auflösen, im Datenzugriff erzwingen", "benutzer_gruppen_rechte"
    ):
        user = User("Benutzer")
        graph = Database("user / group\nmember_of / child_of\nRechte + Daten-Scope")
        with Cluster("Rust-Kern | autoritative Prüfung"):
            login = Core("Login / Token-Erneuerung\nGruppen und Rechte ableiten")
            jwt = Artifact("JWT-Claims\nGruppen / Rechte / auth_version")
            permission = Core("RBAC\nAktion erlaubt?\nfail-closed")
            scope = Core("ABAC\nScope kompilieren / cachen")
            query = Operation(
                "Objektzugriff\nScope in derselben WHERE-Klausel\nkein nachträglicher Filter"
            )
        change = Operation("Rechteänderung\nauth_version erhöhen\nSitzungen invalidieren")

        user >> link("Anmelden") >> login
        graph >> link("Einmal auflösen") >> login
        login >> link("Token ausstellen") >> jwt
        jwt >> link("Je Anfrage") >> permission
        permission >> link("Erlaubte Operation") >> scope
        scope >> link("Superadmin unbeschränkt\nsonst wirksamer Scope") >> query
        graph >> link("Mutation") >> change
        change >> link("Token des Ändernden erneuern\nandere Sitzungen verwerfen") >> jwt


if __name__ == "__main__":
    build()
