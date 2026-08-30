"""Thin async REST client for the PIM core."""

from __future__ import annotations

import httpx
from pydantic import BaseModel


def is_session_expired(results: list[dict]) -> bool:
    """True if any batch result signals an invalid/expired bearer token.

    The core returns HTTP 200 for a rejected batch item, carrying the failure
    in the per-item `error` field. Treating this as a session problem lets the
    UI redirect to the login page instead of reporting a failed save.
    """
    return any(
        not result.get("success") and "invalid or expired token" in (result.get("error") or "")
        for result in results
    )


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
        ancestor: str | None = None,
        category: str | None = None,
        locale: str | None = None,
        limit: int = 100,
        start: int = 0,
    ) -> list[dict]:
        """Lists objects, optionally filtered by type, composition ancestor or category.

        With ``ancestor`` the result is limited to that composition subtree; with
        ``category`` to objects assigned (via ``category_member``) to that category
        or its descendants. With ``locale``, each object carries a ``completeness``
        value for that language (computed in the core).
        """
        params: dict[str, str] = {"limit": str(limit), "start": str(start)}
        if object_type:
            params["type"] = object_type
        if ancestor:
            params["ancestor"] = ancestor
        if category:
            params["category"] = category
        if locale:
            params["locale"] = locale
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/objects",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_attribute_definitions(
        self,
        token: str,
        *,
        limit: int = 1000,
        start: int = 0,
    ) -> list[dict]:
        """Lists attribute definitions (used to offer grid columns)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/attribute-definitions",
                params={"limit": str(limit), "start": str(start)},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_object_children(
        self,
        token: str,
        object_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """Lists an object's composition children (``rel_depends_on``).

        Each child carries ``hasChildren`` so the tree can show the expand arrow
        without loading the whole subtree; ``limit``/``offset`` paginate nodes
        with many direct children.
        """
        params: dict[str, str] = {}
        if limit is not None:
            params["limit"] = str(limit)
        if offset:
            params["offset"] = str(offset)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/objects/{object_id}/children",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def get_object_tree(self, token: str) -> list[dict]:
        """Returns the composition tree (``{id, children}``) from the Universal-Root."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/objects/tree",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def batch_update(self, token: str, items: list[dict]) -> list[dict]:
        """Applies staged grid edits in a single batch call."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/objects/batch-update",
                json=items,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()
