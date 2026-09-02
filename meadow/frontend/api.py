"""Thin async REST client for the PIM core."""

from __future__ import annotations

import httpx
from pydantic import BaseModel


class CoreError(Exception):
    """Raised when the core returns a non-success response.

    Carries the human-readable message the core put in its ``error`` field
    (e.g. a validation failure such as ``height: Height is required``) plus the
    HTTP status code, so the UI can show the actual reason instead of a generic
    "could not save" notice.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


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
        uncategorized: bool = False,
        locale: str | None = None,
        limit: int = 100,
        start: int = 0,
    ) -> list[dict]:
        """Lists objects, optionally filtered by type, composition ancestor or category.

        With ``ancestor`` the result is limited to that composition subtree; with
        ``category`` to objects assigned (via ``category_member``) to that category
        or its descendants; with ``uncategorized`` to objects with no category
        assignment. With ``locale``, each object carries a ``completeness`` value
        for that language (computed in the core).
        """
        params: dict[str, str] = {"limit": str(limit), "start": str(start)}
        if object_type:
            params["type"] = object_type
        if ancestor:
            params["ancestor"] = ancestor
        if category:
            params["category"] = category
        if uncategorized:
            params["uncategorized"] = "true"
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

    async def update_attribute_definition(
        self,
        token: str,
        definition_id: str,
        *,
        constraints: dict | None = None,
    ) -> dict:
        """Updates an attribute definition (currently only ``constraints``).

        Used by the product form to add a new enum option via ``PATCH
        /attribute-definitions/{id}`` (the whole ``constraints`` object is
        replaced, so pass the full set including any other constraint keys).
        """
        payload: dict = {}
        if constraints is not None:
            payload["constraints"] = constraints
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.patch(
                f"{self.base_url}/attribute-definitions/{definition_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                detail = None
                try:
                    detail = response.json().get("error")
                except Exception:  # noqa: BLE001 - body may not be JSON
                    detail = None
                raise CoreError(detail or response.text, status=response.status_code)
            return response.json()

    async def get_object(
        self,
        token: str,
        object_id: str,
        *,
        locale: str | None = None,
        resolve_inheritance: bool = False,
    ) -> dict:
        """Loads a single object in detail (used by the product form)."""
        params: dict[str, str] = {}
        if locale:
            params["locale"] = locale
        if resolve_inheritance:
            params["resolve_inheritance"] = "true"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/objects/{object_id}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def update_object(
        self,
        token: str,
        object_id: str,
        *,
        attributes: dict | None = None,
        status: str | None = None,
        locale: str | None = None,
    ) -> dict:
        """Applies a partial object update (attributes and/or status)."""
        params: dict[str, str] = {}
        if locale:
            params["locale"] = locale
        payload: dict = {}
        if attributes is not None:
            payload["attributes"] = attributes
        if status is not None:
            payload["status"] = status
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.patch(
                f"{self.base_url}/objects/{object_id}",
                params=params,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                detail = None
                try:
                    detail = response.json().get("error")
                except Exception:  # noqa: BLE001 - response body may not be JSON
                    detail = None
                raise CoreError(detail or response.text, status=response.status_code)
            return response.json()

    async def list_languages(self, token: str) -> list[dict]:
        """Lists the active data languages from the language-tree table.

        Used by the product form to offer multilingual editing across every
        configured data language, not just the three UI languages.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/languages",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return [data]
            return data

    async def list_attribute_groups(self, token: str) -> list[dict]:
        """Lists attribute groups (used for the product form's sections)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/attribute-groups",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            data = response.json()
            # Defensive: a single group is returned as a bare object.
            if isinstance(data, dict):
                return [data]
            return data

    async def list_object_children(
        self,
        token: str,
        object_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
        count_members: bool = False,
    ) -> list[dict]:
        """Lists an object's composition children (``rel_depends_on``).

        Each child carries ``hasChildren`` so the tree can show the expand arrow
        without loading the whole subtree; ``limit``/``offset`` paginate nodes
        with many direct children. With ``count_members``, category children also
        carry a ``memberCount`` (objects assigned via ``category_member``).
        """
        params: dict[str, str] = {}
        if limit is not None:
            params["limit"] = str(limit)
        if offset:
            params["offset"] = str(offset)
        if count_members:
            params["count_members"] = "true"
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

    async def list_object_references(
        self,
        token: str,
        object_id: str,
        *,
        locale: str | None = None,
    ) -> list[dict]:
        """Lists an object's references (incoming + outgoing).

        Each reference carries ``in_object``/``out_object`` (``{id, type, label,
        status}``) so the UI can render chips with a resolved label.
        """
        params: dict[str, str] = {}
        if locale:
            params["locale"] = locale
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/objects/{object_id}/references",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def picker_search(
        self,
        token: str,
        q: str,
        *,
        types: list[str] | None = None,
        locale: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Searches objects by label for the reference picker."""
        params: dict[str, str] = {"q": q, "limit": str(limit)}
        if types:
            params["types"] = ",".join(types)
        if locale:
            params["locale"] = locale
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/objects/picker",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_reference(
        self,
        token: str,
        source: str,
        target: str,
        definition_id: str,
    ) -> dict:
        """Creates a reference between two objects."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/references",
                json={"source": source, "target": target, "definition_id": definition_id},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def delete_reference(self, token: str, reference_id: str) -> None:
        """Deletes a reference."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(
                f"{self.base_url}/references/{reference_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()

    async def reparent_object(self, token: str, object_id: str, parent: str) -> dict:
        """Re-parents an object to a new composition parent (full record id)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.patch(
                f"{self.base_url}/objects/{object_id}/parent",
                json={"parent": parent},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_relation_definitions(self, token: str) -> list[dict]:
        """Lists relation definitions (e.g. for the reference picker)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/relation-definitions",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_system_logs(self, token: str, *, limit: int = 10, start: int = 0) -> list[dict]:
        """Lists curated system log entries (admin only)."""
        params: dict[str, str] = {"limit": str(limit), "start": str(start)}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/system/logs",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_job_runs(
        self,
        token: str,
        *,
        status: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Lists job runs (newest first) for the dashboard / polling."""
        params: dict[str, str] = {"limit": str(limit)}
        if status:
            params["status"] = status
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/jobs/runs",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()
