# Meadow PIM — HTTP load test for the headless core (Phase 8).
#
# Exercises the hot read paths (`GET /objects`, `GET /objects/stats`,
# `POST /search`, `GET /objects/{id}`, `GET /objects/{id}/children`) against a
# running `pim-core` instance and reports throughput, latency percentiles and
# error rate. Optionally seeds a disposable `loadtest` dataset first and cleans
# it up afterwards.
#
# Usage:
#   python scripts/load_test.py --duration 15 --concurrency 20
#   python scripts/load_test.py --duration 10 --concurrency 10 --queries list,stats,search
#   python scripts/load_test.py --seed 300 --duration 15 --concurrency 20 --cleanup
#
# The core's per-IP rate limit should be disabled (default) for stable numbers.

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass

import httpx

DEFAULT_LIMIT = 100


@dataclass
class Sample:
    """One completed request measurement."""

    endpoint: str
    latency_ms: float
    status: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load-test the Meadow PIM core.")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration", type=float, default=15.0, help="seconds")
    parser.add_argument(
        "--queries",
        default="list,stats,search,get,children",
        help="comma-separated endpoint keys: list,stats,search,get,children",
    )
    parser.add_argument("--seed", type=int, default=0, help="create N loadtest objects")
    parser.add_argument("--cleanup", action="store_true", help="delete seeded loadtest objects")
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser.parse_args()


async def login(base_url: str, username: str, password: str) -> str:
    """Exchange credentials for a JWT (used as the bearer on every request)."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{base_url}/auth/login",
            json={"username": username, "password": password},
        )
        response.raise_for_status()
        return response.json()["token"]


async def seed_objects(base_url: str, token: str, count: int, concurrency: int) -> list[str]:
    """Create `count` disposable `loadtest` objects and return their ids."""
    created: list[str] = []
    sem = asyncio.Semaphore(concurrency)

    async def create_one() -> str:
        async with sem, httpx.AsyncClient(timeout=20.0) as client:
            payload = {
                "type": "loadtest",
                "attributes": {
                    "name": f"Load-Test-{random.randrange(10**9):09d}",
                    "description": "seeded by scripts/load_test.py",
                },
                "status": "draft",
            }
            response = await client.post(
                f"{base_url}/objects",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()["id"]

    tasks = [create_one() for _ in range(count)]
    for batch in asyncio.as_completed(tasks):
        created.append(await batch)
    return created


async def delete_objects(base_url: str, token: str, ids: list[str]) -> None:
    """Soft-delete the seeded objects."""
    sem = asyncio.Semaphore(10)

    async def delete_one(object_id: str) -> None:
        async with sem, httpx.AsyncClient(timeout=20.0) as client:
            await client.delete(
                f"{base_url}/objects/{object_id}",
                headers={"Authorization": f"Bearer {token}"},
            )

    await asyncio.gather(*(delete_one(i) for i in ids))


async def collect_object_ids(base_url: str, token: str) -> list[str]:
    """Pull a pool of existing object ids (used as `{id}` targets)."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{base_url}/objects",
            params={"limit": DEFAULT_LIMIT, "start": 0},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return [obj["id"] for obj in response.json()]


async def run_workers(
    args: argparse.Namespace,
    token: str,
    object_ids: list[str],
    samples: list[Sample],
    stop: asyncio.Event,
    client: httpx.AsyncClient,
) -> None:
    """Issue requests round-robin over the selected endpoints until `stop`.

    A single shared `client` (connection pool) keeps per-request overhead low so
    the measurements reflect endpoint performance, not client setup.
    """
    endpoints = args.queries.split(",")
    sem = asyncio.Semaphore(args.concurrency)

    async def one_request() -> None:
        endpoint = random.choice(endpoints)
        method, path, payload = resolve_endpoint(endpoint, object_ids)
        start = time.perf_counter()
        try:
            if method == "GET":
                response = await client.get(
                    f"{args.base_url}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            else:
                response = await client.post(
                    f"{args.base_url}{path}",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
            latency_ms = (time.perf_counter() - start) * 1000.0
            samples.append(Sample(endpoint, latency_ms, response.status_code))
        except Exception as exc:  # noqa: BLE001 - a load test never dies on one request
            samples.append(Sample(endpoint, (time.perf_counter() - start) * 1000.0, 0))
            if isinstance(exc, httpx.HTTPStatusError):
                raise

    async def worker() -> None:
        while not stop.is_set():
            async with sem:
                await one_request()

    await asyncio.gather(*(worker() for _ in range(args.concurrency)))


def resolve_endpoint(key: str, object_ids: list[str]) -> tuple[str, str, dict | None]:
    """Return `(method, path, json_payload)` for an endpoint key."""
    if key == "list":
        return "GET", "/objects?limit=100&locale=", None
    if key == "stats":
        return "GET", "/objects/stats?locale=", None
    if key == "search":
        return "POST", "/search", {"q": "Produkt", "limit": 100}
    if key in ("get", "children"):
        target = random.choice(object_ids) if object_ids else None
        if key == "get":
            path = f"/objects/{target}?locale=&resolve_inheritance=true"
        else:
            path = f"/objects/{target}/children?limit=100"
        return "GET", path, None
    raise ValueError(f"unknown endpoint key: {key}")


def percentiles(values: list[float], *percentile: int) -> list[float]:
    """Compute the given percentiles (e.g. 50, 95, 99) over a sorted sample."""
    if not values:
        return [0.0] * len(percentile)
    ordered = sorted(values)
    out = []
    for p in percentile:
        index = min(len(ordered) - 1, round((p / 100.0) * (len(ordered) - 1)))
        out.append(ordered[index])
    return out


def report(samples: list[Sample], duration: float) -> None:
    """Print an aggregated summary."""
    total = len(samples)
    errors = sum(1 for s in samples if s.status == 0 or s.status >= 500)
    print(f"\n=== Load test summary ({duration:.1f}s) ===")
    print(f"requests      : {total}")
    print(f"throughput    : {total / duration:.1f} req/s")
    print(f"errors        : {errors} ({errors / max(total, 1) * 100:.2f}%)")
    if total:
        latencies = [s.latency_ms for s in samples]
        p50, p95, p99 = percentiles(latencies, 50, 95, 99)
        print(f"latency mean  : {statistics.fmean(latencies):.2f} ms")
        print(f"latency p50   : {p50:.2f} ms")
        print(f"latency p95   : {p95:.2f} ms")
        print(f"latency p99   : {p99:.2f} ms")

    by_endpoint: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        by_endpoint[sample.endpoint].append(sample)
    print("\nPer endpoint:")
    for endpoint in sorted(by_endpoint):
        group = by_endpoint[endpoint]
        lat = [s.latency_ms for s in group]
        ok = sum(1 for s in group if s.status == 200)
        p50, p95, p99 = percentiles(lat, 50, 95, 99)
        print(
            f"  {endpoint:<9} n={len(group):<5} ok={ok:<4} mean={statistics.fmean(lat):.2f} "
            f"p50={p50:.2f} p95={p95:.2f} p99={p99:.2f}"
        )


async def main() -> int:
    args = parse_args()
    token = await login(args.base_url, args.username, args.password)
    print(f"logged in as {args.username}")

    seeded_ids: list[str] = []
    if args.seed > 0:
        print(f"seeding {args.seed} loadtest objects ...")
        seeded_ids = await seed_objects(args.base_url, token, args.seed, args.concurrency)
        print(f"seeded {len(seeded_ids)} objects")

    object_ids = await collect_object_ids(args.base_url, token)
    if not object_ids:
        print("no objects found; falling back to list/stats/search only", file=sys.stderr)
        # The get/children endpoints are skipped when there is no target object.

    samples: list[Sample] = []
    stop = asyncio.Event()
    async with httpx.AsyncClient(
        timeout=args.timeout, limits=httpx.Limits(max_connections=100)
    ) as client:
        runner = asyncio.create_task(run_workers(args, token, object_ids, samples, stop, client))
        await asyncio.sleep(args.duration)
        stop.set()
        await runner

    report(samples, args.duration)

    if args.cleanup and seeded_ids:
        print(f"\ncleaning up {len(seeded_ids)} seeded objects ...")
        await delete_objects(args.base_url, token, seeded_ids)
        print("cleanup done")

    return 0 if not any(s.status == 0 for s in samples) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
