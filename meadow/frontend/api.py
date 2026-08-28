"""Thin async REST client for the PIM core."""

from __future__ import annotations

import httpx
from pydantic import BaseModel


class LoginResponse(BaseModel):
    """Response of `POST /auth/login`."""

    token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    """Response of `GET /auth/me`."""

    sub: str
    username: str
    roles: list[str]


class CoreClient:
    """HTTP client for the headless PIM core."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def login(self, username: str, password: str) -> LoginResponse:
        """Exchanges credentials for a JWT."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/auth/login",
                json={"username": username, "password": password},
            )
            response.raise_for_status()
            return LoginResponse.model_validate(response.json())

    async def me(self, token: str) -> MeResponse:
        """Returns the current user's claims for a bearer token."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return MeResponse.model_validate(response.json())

    async def list_objects(
        self,
        token: str,
        *,
        object_type: str | None = None,
        limit: int = 100,
        start: int = 0,
    ) -> list[dict]:
        """Lists objects, optionally filtered by type."""
        params: dict[str, str] = {"limit": str(limit), "start": str(start)}
        if object_type:
            params["type"] = object_type
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/objects",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()
