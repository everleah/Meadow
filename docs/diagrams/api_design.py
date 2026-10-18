"""Source: plan/detail/api-design.md, API contracts and async endpoints."""

from _shared import Artifact, Cluster, Core, Database, Nats, Python, diagram, link


def build() -> None:
    with diagram("REST-API | Autoritativer Kern, synchrone Writes und Jobs", "api_design"):
        ui = Python("NiceGUI / API-Client\nBearer-JWT")
        worker = Python("Worker\nMaschinen-API-Key")
        with Cluster("PIM Core | Axum"):
            auth = Core("Authentifizierung\nRBAC + ABAC-Scope")
            data = Core("Objekte / Metadaten\nValidierung / Regeln\natomare Writes")
            jobs = Core("Asynchrone Endpunkte\nImport / Export / Bulk")
            schema = Artifact("utoipa / OpenAPI\nAPI-Vertrag")
        db = Database("SurrealDB\nFachdaten / Audit / job_run")
        bus = Nats("NATS JetStream\nJobs")

        [ui, worker] >> link("REST") >> auth
        auth >> link("Berechtigter Datenzugriff") >> data
        auth >> link("Auftrag starten") >> jobs
        schema >> link("Beschreibt") >> auth
        [data, jobs] >> link("Nur der Kern schreibt") >> db
        jobs >> link("Auftrag publizieren", async_=True) >> bus
        bus >> link("Ausführen", async_=True) >> worker
        ui >> link("Polling ohne result\nBericht / Download einzeln") >> jobs


if __name__ == "__main__":
    build()
