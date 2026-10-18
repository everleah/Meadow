"""Source: plan/detail/frontend.md, architecture and UI internationalization."""

from _shared import Browser, Cluster, Core, Database, Python, diagram, link


def build() -> None:
    with diagram("Frontend | Ein Dienst, REST und getrennte Sprachwelten", "frontend"):
        browser = Browser("Browser\nHTTPS / WebSocket")
        with Cluster("Ein NiceGUI-Dienst für alle Benutzer"):
            ui = Python("Seiten und Navigation\nrollenabhängige Anzeige")
            session = Database("Browserbezogener Speicher\nJWT-Sitzung | Präferenzen")
            locale = Python("UI-i18n\nSchlüssel + Babel\nLocale → Englisch → Schlüssel")
        core = Core("PIM Core\nJWT + autoritative Rechteprüfung")
        data_languages = Database("language_tree\nfachliche Datensprachen")

        browser >> link("Interaktion") >> ui
        session >> link("Token je Anfrage") >> ui
        ui >> link("Nur Anmeldung löschen\nbei Logout / Ablauf") >> session
        locale >> link("UI-Texte / Formate") >> ui
        ui >> link("REST; Jobstatus per Polling\nkeine direkte NATS-Anbindung") >> core
        core >> link("Daten-Locale / Fallback") >> data_languages


if __name__ == "__main__":
    build()
