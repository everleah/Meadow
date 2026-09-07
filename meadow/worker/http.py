"""Shared HTTP helpers for the worker (loopback host normalisation)."""

from __future__ import annotations


def loopback_host(url: str) -> str:
    """Rewrites `localhost` to `127.0.0.1` so every new connection is instant.

    On Windows `localhost` resolves to `::1` (IPv6) before `127.0.0.1`. The PIM
    core binds IPv4 only (e.g. `0.0.0.0:8080`), so a new httpx connection waits
    ~2 s for the IPv6 attempt to time out and then falls back to IPv4 — the main
    source of per-connection latency. Using the explicit IPv4 loopback makes
    every new connection fast. Apply this to any core/base URL before issuing
    HTTP requests.
    """
    return url.replace("://localhost", "://127.0.0.1")
