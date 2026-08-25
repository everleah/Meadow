# meadow — Python Source Code

## Purpose

Owns the Python package `meadow`: the shared business-rules engine (`meadow.rules`), and later the worker and frontend services. Python is the home of flexible business logic and the UI (see root AGENTS.md).

## Ownership

- Owns all files under `meadow/`.
- `meadow/rules/` — RestrictedPython sandbox and the script contract `run(obj, ctx, result)`.
- `meadow/worker/` — async NATS request/reply worker serving `rules.evaluate` (`python -m meadow.worker`).

## Local Contracts

- Implementation stays consistent with `plan/detail/business-rules.md` and the root AGENTS.md (package manager `uv`, `.venv/`, `pyproject.toml`).
- The rules engine is a shared library; its consumers are the Rust core (synchronous evaluation endpoint, Phase 4) and the Python worker/frontend.
- `meadow.rules` public API: `execute_rule`, `compile_rule`, `Context`, `Result`, `DEFAULT_WHITELIST`, and the `RuleError` hierarchy (`RuleCompileError`, `RuleTimeoutError`, `RuleMemoryError`).
- Rule scripts use `def run(obj, ctx, result)`; injected globals are `obj` (read-only view), `ctx`, `result`, `log`, plus whitelisted modules (default: `json`, `datetime`, `math`, `re`, `statistics`, `decimal`, `typing`, `uuid`, `collections`).
- `meadow.worker` (`protocol.py`, `rules_service.py`, `log_sender.py`): NATS request/reply service on subjects `rules.evaluate` and `rules.compile` (defaults) that evaluates `EvaluationRequest` payloads and validates scripts via `EvaluationCompileRequest`; rule exceptions are fail-closed (recorded in `failed`). Curated worker logs (rule failures, lifecycle) are buffered (`LogSender`, bounded, drop-oldest) and flushed to `POST /system/logs`; logging config (`log_persist_level`/`log_persist_categories`) is refreshed from `GET /system/settings`. Entry point `python -m meadow.worker`; env `NATS_URL` (default `nats://localhost:4222`), `RULES_EVALUATE_SUBJECT`, `RULES_COMPILE_SUBJECT`, `PIM_CORE_URL` (default `http://localhost:8080`), `LOG_INGEST_API_KEY`, `LOG_FLUSH_INTERVAL_S`, `LOG_CONFIG_REFRESH_INTERVAL_S`.
- `meadow.worker.jobs` (`jobs.py`): JetStream job consumer on `jobs.*` (stream `JOBS`, durable pull consumer `job-worker`). `JobRunner` parses `JobMessage`, reports `job_run` status via `PATCH /jobs/runs/{run_id}` (`X-Api-Key` = `LOG_INGEST_API_KEY`), and dispatches to handlers registered by job type; unknown types are marked `failed` + terminated, handler failures are negative-acked for retry; on the final delivery attempt (`num_delivered >= max_deliver`) a failing job is published to the `JOBS_DLQ` stream (`jobs.dlq.<type>`) and terminated. Messages are processed with bounded concurrency (`JOBS_CONCURRENCY`, default 4; `process_messages` + `asyncio.Semaphore`). Env `JOBS_SUBJECT`, `JOBS_STREAM`, `JOBS_CONSUMER_DURABLE`, `JOBS_ACK_WAIT_S`, `JOBS_MAX_DELIVER`, `JOBS_CONCURRENCY`.
- `meadow.worker.business_rules` (`business_rules.py`): the `business_rule` job handler (recompute, Phase 5 step 4). It loads the rule's objects (`object_ids` or `applies_to` via `GET /objects`), evaluates the embedded rule script per object in the sandbox (bulk mode, ~30 s budget), persists declared-computed values via `PATCH /objects/{id}/computed`, and returns a per-object report stored in `job_run.result` (per-object failures are isolated). Sandbox evaluations are serialized by a module-level `asyncio.Lock` (`sys.settrace`/`tracemalloc` are not safe concurrently), while object I/O runs in parallel.
- `meadow.worker.imports` (`imports.py`): the `inbound_import` job handler (MEF import, Phase 6). It fetches the stored MEF document (`GET /imports/{document_id}`), upserts objects by business key (`match_by_key`, via `GET /objects/by-key` then `POST /objects`/`PATCH /objects/{id}`), creates references (mapping the MEF `type` name to the definition key `rel_<name>`), and returns a per-object/per-reference report stored in `job_run.result` (failures are isolated). Writes use the `X-Api-Key` machine-to-machine path.

## Work Guidance

- Code, comments, and docstrings are written in English (root Coding Guidelines).
- Sandbox limits default to the blocking write path (~1.5 s budget, 256 MiB, 64 KB source); bulk jobs may pass looser limits.

## Verification

- `ruff check meadow tests conftest.py`
- `pytest` (asyncio mode auto via `pyproject.toml`)

## Child DOX Index

_(none yet)_
