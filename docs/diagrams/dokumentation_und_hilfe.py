"""Source: plan/detail/dokumentation-und-hilfe.md, sections 3-5."""

from _shared import Artifact, Browser, Cluster, Python, diagram, link


def build() -> None:
    with diagram("Hilfe | Eine Textquelle, kein Dokumentations-Build", "dokumentation_und_hilfe"):
        chapters = Artifact("Markdown-Kapitel de / en\nsprachneutrale Kapitel-IDs")
        assets = Artifact("Erzeugte Screenshots\ndocs/user/assets")
        with Cluster("NiceGUI-Frontend"):
            catalog = Python("help_content\nMarkdown / Suchindex\npro Prozess gecacht")
            help_page = Python("/help\nKapitel / Suche / Sprachwahl")
            surfaces = Python("Arbeitsflächen\nTooltip + Kapitel-Link")
        browser = Browser("Browser")
        screenshots = Python("Playwright\nscripts/docs_screenshots.py")

        chapters >> link("Einmal laden") >> catalog
        catalog >> link("HTML / Treffer") >> help_page
        surfaces >> link("/help?page=<id>") >> help_page
        help_page >> link("Serverseitig ausliefern") >> browser
        assets >> link("/help-assets") >> browser
        browser >> link("Laufende Dev-Instanz\nvorbereitete Demo-Daten") >> screenshots
        screenshots >> link("Fotografieren; kein Seeding") >> assets


if __name__ == "__main__":
    build()
