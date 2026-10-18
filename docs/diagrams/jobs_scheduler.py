"""Source: plan/detail/jobs-scheduler.md, scheduling, processing and retries."""

from _shared import Artifact, Core, Database, Nats, Operation, Python, diagram, link


def build() -> None:
    with diagram("Jobs | Definition, Ausführung, Ergebnis und Fehlerzustellung", "jobs_scheduler"):
        definition = Database("job_definition\nTyp / Schema / Parameter\nCron mit 6 Feldern")
        scheduler = Core("PIM Core / Scheduler\nCron oder manueller Start")
        run = Database("job_run\npending → running\n→ success / failed\nresult + result_summary")
        bus = Nats("NATS JetStream\njobs.* / at-least-once")
        worker = Python("Worker / asyncio\nkonfigurierbare Parallelität\nidempotente Handler")
        retry = Operation("Fehler / Retry\nbegrenzte Zustellversuche")
        dlq = Nats("Dead-Letter-Stream\nnach ausgeschöpften Versuchen")
        report = Artifact("UI: Status + result_summary\nReport / Download: Einzellauf")

        definition >> link("Plan laden") >> scheduler
        scheduler >> link("Lauf anlegen") >> run
        scheduler >> link("Publizieren", async_=True) >> bus
        bus >> link("Zustellen", async_=True) >> worker
        worker >> link("REST zum Kern\nFortschritt / Ergebnis") >> scheduler
        worker >> link("Fehlgeschlagen") >> retry
        retry >> link("Erneut zustellen", async_=True) >> bus
        retry >> link("Endgültig fehlgeschlagen", async_=True) >> dlq
        run >> link("Polling ohne result") >> report


if __name__ == "__main__":
    build()
