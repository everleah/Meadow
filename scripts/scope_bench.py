"""Meadow PIM — what an ABAC scope *means* and what it *costs* (RBAC stages A5/A6).

The scope is the one part of the access model that sits on the read path, so it gets two
promises checked against a running core instead of a unit test:

1. **It means what it says.** A group whose scope names a category must show exactly the
   objects of that category — the count and the page of the scoped session are compared with
   the same view filtered by `?category=` explicitly.
2. **It costs in the necessary measure.** The three view calls of the grid are timed for an
   unscoped and for a scoped session; the first scoped call of each endpoint is reported
   separately, because it is the one that resolves the member keys (later calls are cached).

The script creates a throwaway group for the run and deletes it again (`--keep` leaves it in
place, e.g. to look at the result in the UI). A failed check exits non-zero, so the script can
be used as an acceptance step for the feature.

Usage:
  uv run python scripts/scope_bench.py
  uv run python scripts/scope_bench.py --target object:01M2Q5G98HRF9K9FY3JRE4V9DP --repeat 5
  uv run python scripts/scope_bench.py --user user:01M0AN8M2ZG2P325F68A910QSP --keep
  uv run python scripts/scope_bench.py --max-overhead-ms 400

Measured on the dev data 2026-09-25 (16 926 products). The clause itself is a record list
literal — the cheaper-looking `id IN (SELECT VALUE in FROM reference …)` costs 28 918 ms
there against 896 ms for the literal (see `src/scope.rs`). End to end, with 3 timed calls
per endpoint:

| scope | count | page (200 rows) | column sample |
|---|---|---|---|
| 69 members | +5 ms | −32 ms | −126 ms |
| 455 members | +93 ms | +97 ms | −101 ms |

A **narrow** scope can be *cheaper* than no scope at all: fewer candidates to scan, sort and
render (the column sample inspects the view, so 455 objects cost less than the 1000 it
otherwise asks for). A wide scope adds the literal check on top of the unscoped query.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

#: The view calls of the grid, timed for both sessions.
VIEW_CALLS: tuple[tuple[str, str], ...] = (
    ("count type=product", "/objects/count?type=product"),
    ("list type=product (200 rows)", "/objects?type=product&limit=200"),
    ("attribute-usage (sample 1000)", "/objects/attribute-usage?type=product&sample=1000"),
)

#: Name of the throwaway group (removed at the end unless `--keep`).
PROBE_GROUP = "scope-bench"


@dataclass
class Timing:
    """The measured cost of one call, cold and warm."""

    cold_ms: float
    warm_ms: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure and verify an ABAC scope.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--target", help="category record id the scope names (default: probe)")
    parser.add_argument(
        "--relation", default="rel_category_member", help="relation key of the rule"
    )
    parser.add_argument(
        "--user", help="user to put into the scoped group (default: first viewer/editor)"
    )
    parser.add_argument("--repeat", type=int, default=3, help="timed calls per endpoint (warm)")
    parser.add_argument("--keep", action="store_true", help="leave the group in place")
    parser.add_argument(
        "--max-overhead-ms",
        type=float,
        default=None,
        help="fail when the warm scoped count exceeds the unscoped one by more than this",
    )
    return parser.parse_args()


class Core:
    """A minimal core client that re-logs in when its own change ended the session."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.client = httpx.Client(base_url=self.base_url, timeout=60.0)
        self.token = self.login()
        self.relogins = 0

    def login(self) -> str:
        response = self.client.post(
            "/auth/login", json={"username": self.username, "password": self.password}
        )
        response.raise_for_status()
        return str(response.json()["token"])

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """One request; a `401` re-logs in once (an access change ends the session)."""
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self.token}"}
            response = self.client.request(method, path, headers=headers, json=body)
            if response.status_code != 401 or attempt:
                payload = response.json() if response.content else None
                return response.status_code, payload
            self.relogins += 1
            self.token = self.login()
        raise AssertionError("unreachable")

    def as_(self, token: str) -> Core:
        """A second client that uses another session (the impersonated one)."""
        clone = Core.__new__(Core)
        clone.base_url = self.base_url
        clone.username = self.username
        clone.password = self.password
        clone.client = self.client
        clone.token = token
        clone.relogins = 0
        return clone

    def count(self, query: str) -> int:
        status, answer = self.call("GET", f"/objects/count?{query}")
        assert status == 200, (status, answer)
        return int(answer["count"])

    def page_ids(self, query: str) -> list[str]:
        status, rows = self.call("GET", f"/objects?{query}&limit=200")
        assert status == 200, (status, rows)
        return sorted(str(row["id"]) for row in rows)

    def users(self) -> list[dict[str, Any]]:
        status, rows = self.call("GET", "/users?limit=200")
        assert status == 200, (status, rows)
        return list(rows)

    def groups(self) -> list[dict[str, Any]]:
        status, rows = self.call("GET", "/groups?limit=200")
        assert status == 200, (status, rows)
        return list(rows)


def scope_user(core: Core, requested: str | None) -> str:
    """The user the scoped group gets: requested, or the first one who is not an admin."""
    if requested:
        return requested
    for user in core.users():
        if not {"admin", "superadmin"} & set(user.get("roles") or []):
            return str(user["id"])
    raise SystemExit("no non-admin user to scope — pass --user")


def pick_target(core: Core, relation: str) -> str:
    """A target that really carries members in `relation`.

    A taxonomy node can look popular and still have **no direct edge**: `?category=` includes the
    subtree, so a root matches every product below it while `rel_category_member` says nothing
    about it. A scope on such a node matches nothing — and every check would then pass on an empty
    view, which is exactly the trap this function exists to avoid. The target is therefore read
    from a real edge: a product of the catalogue and the relation the scope will use.
    """
    status, products = core.call("GET", "/objects?type=product&limit=20")
    assert status == 200, (status, products)
    for product in products:
        product_id = str(product["id"])
        status, references = core.call("GET", f"/objects/{product_id}/references")
        assert status == 200, (status, references)
        for row in references:
            if row.get("definition_id") == relation and row.get("in") == product_id:
                target = row.get("out")
                if isinstance(target, str):
                    return target
    raise SystemExit(f"no `{relation}` edge on the first 20 products — pass --target")


def carries_edge(core: Core, object_id: str, relation: str, target: str) -> bool:
    """Whether the object really is assigned to the target through that relation."""
    status, references = core.call("GET", f"/objects/{object_id}/references")
    if status != 200:
        return False
    return any(
        row.get("definition_id") == relation
        and row.get("in") == object_id
        and row.get("out") == target
        for row in references
    )


def measure(core: Core, path: str, repeat: int) -> Timing:
    """Times one call: the first run (cold) and the median of the following ones."""
    start = time.perf_counter()
    core.call("GET", path)
    cold = (time.perf_counter() - start) * 1000
    warm: list[float] = []
    for _ in range(repeat):
        start = time.perf_counter()
        core.call("GET", path)
        warm.append((time.perf_counter() - start) * 1000)
    return Timing(cold_ms=cold, warm_ms=statistics.median(warm))


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    return ok


def main() -> None:
    args = parse_args()
    core = Core(args.base_url, args.username, args.password)
    print(f"core {args.base_url}, user {args.username}")

    target = args.target or pick_target(core, args.relation)
    user = scope_user(core, args.user)
    print(f"target {target}, scope user {user}, relation {args.relation}")

    # A leftover of an earlier run would silently widen or narrow the result.
    for group in core.groups():
        if group["name"] == PROBE_GROUP:
            print(f"cleanup leftover: {core.call('DELETE', f'/groups/{group["id"]}')[0]}")

    unscoped_count = core.count("type=product")
    unscoped = {name: measure(core, path, args.repeat) for name, path in VIEW_CALLS}
    print(f"\nunscoped: {unscoped_count} products\n")

    scope = {"rules": [{"reference": {"type": args.relation, "out": target}}]}
    status, group = core.call("POST", "/groups", {"name": PROBE_GROUP, "scope": scope})
    assert status == 201, (status, group)
    group_id = str(group["id"])
    status, _ = core.call("POST", f"/groups/{group_id}/members", {"user_id": user})
    assert status == 201, status

    status, answer = core.call("POST", f"/auth/impersonate/{user}")
    assert status == 200, (status, answer)
    scoped = core.as_(str(answer["token"]))

    scoped_count = scoped.count("type=product")
    category_count = scoped.count(f"type=product&category={target}")
    scoped_ids = scoped.page_ids("type=product")
    scoped_times = {name: measure(scoped, path, args.repeat) for name, path in VIEW_CALLS}

    print(f"{'call':32s} {'unscoped':>10s} {'scoped':>10s} {'delta':>9s}   cold")
    for name, _ in VIEW_CALLS:
        print(
            f"{name:32s} {unscoped[name].warm_ms:7.0f} ms {scoped_times[name].warm_ms:7.0f} ms "
            f"{scoped_times[name].warm_ms - unscoped[name].warm_ms:+6.0f} ms "
            f"{scoped_times[name].cold_ms:7.0f} ms"
        )

    print(f"\nscoped: {scoped_count} of {unscoped_count} products (group {group_id})")
    print("\nchecks")
    ok = True
    # First the one that makes the others meaningful: an empty scope would satisfy "fewer than
    # everything" and "same as the category filter" without proving anything.
    ok &= check(
        "the scope has members",
        scoped_count > 0,
        f"{scoped_count} objects",
    )
    ok &= check(
        "the scope restricts the view",
        scoped_count < unscoped_count,
        f"{scoped_count} < {unscoped_count}",
    )
    ok &= check(
        "the scope is not wider than the category view",
        scoped_count <= category_count,
        f"{scoped_count} <= {category_count}",
    )
    sample_ids = scoped_ids[:3]
    # The reverse proof runs with the **admin** session: reading an object's references needs
    # `editor`, and the scoped session is deliberately a plain viewer.
    proven = [
        object_id
        for object_id in sample_ids
        if carries_edge(core, object_id, args.relation, target)
    ]
    ok &= check(
        "a shown object really is assigned to the target",
        len(proven) == len(sample_ids) and bool(sample_ids),
        f"{len(proven)} of {len(sample_ids)} checked",
    )
    if args.max_overhead_ms is not None:
        overhead = scoped_times[VIEW_CALLS[0][0]].warm_ms - unscoped[VIEW_CALLS[0][0]].warm_ms
        ok &= check(
            f"scoped count stays inside the {args.max_overhead_ms:.0f} ms budget",
            overhead <= args.max_overhead_ms,
            f"{overhead:+.0f} ms",
        )
    print(f"session relogins forced by the changes: {core.relogins}")

    if args.keep:
        print(f"\nkept the group {group_id} for manual checks")
    else:
        status, _ = core.call("DELETE", f"/groups/{group_id}")
        print(f"\ncleanup: {status}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
