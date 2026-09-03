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
    #: Present when the session impersonates another user (superadmin).
    impersonator: dict | None = None


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
        **fields: object,
    ) -> dict:
        """Updates an attribute definition (partial PATCH).

        ``constraints`` is kept for backward compatibility (the product form
        adds enum options); additional fields (``label``, ``group_ids``,
        ``applicable_types``, ``required_types``, ...) are merged in.
        """
        payload: dict[str, object] = {k: v for k, v in fields.items() if v is not None}
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

    async def create_attribute_definition(
        self,
        token: str,
        *,
        name: str,
        definition_type: str,
        label: dict | None = None,
        group_ids: list[str] | None = None,
        inheritable: bool = False,
        is_key: bool = False,
        applicable_types: list[str] | None = None,
        required_types: list[str] | None = None,
        computed: bool = False,
        computed_rule: str | None = None,
        dependencies: list[str] | None = None,
        computed_reads_inherited: bool = False,
        multilingual: bool = False,
        unit_id: str | None = None,
        display_unit_id: str | None = None,
        constraints: dict | None = None,
        default_value: object | None = None,
        description: str | None = None,
    ) -> dict:
        """Creates an attribute definition (``POST /attribute-definitions``)."""
        payload: dict[str, object] = {
            "name": name,
            "type": definition_type,
            "label": label or {},
            "group_ids": group_ids or [],
            "inheritable": bool(inheritable),
            "is_key": bool(is_key),
            "applicable_types": applicable_types or [],
            "required_types": required_types or [],
            "computed": bool(computed),
            "computed_reads_inherited": bool(computed_reads_inherited),
            "multilingual": bool(multilingual),
        }
        optional = {
            "computed_rule": computed_rule,
            "dependencies": dependencies,
            "unit_id": unit_id,
            "display_unit_id": display_unit_id,
            "constraints": constraints,
            "default_value": default_value,
            "description": description,
        }
        for key, value in optional.items():
            if value is not None:
                payload[key] = value
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/attribute-definitions",
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

    async def delete_attribute_definition(
        self, token: str, definition_id: str, *, force: bool = False
    ) -> None:
        """Deletes an attribute definition (``DELETE /attribute-definitions/{id}``).

        Raises :class:`CoreError` with status 409 if the attribute is in use
        and ``force`` is not set; ``force=True`` removes values asynchronously.
        """
        params = {"force": "true"} if force else {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(
                f"{self.base_url}/attribute-definitions/{definition_id}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                detail = None
                try:
                    detail = response.json().get("error")
                except Exception:  # noqa: BLE001 - body may not be JSON
                    detail = None
                raise CoreError(detail or response.text, status=response.status_code)

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

    async def create_attribute_group(
        self,
        token: str,
        *,
        name: str,
        label: dict | None = None,
        position: int = 0,
        applicable_types: list[str] | None = None,
    ) -> dict:
        """Creates an attribute group (``POST /attribute-groups``)."""
        payload: dict[str, object] = {
            "name": name,
            "label": label or {},
            "position": position,
            "applicable_types": applicable_types or [],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/attribute-groups",
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

    async def update_attribute_group(
        self,
        token: str,
        group_id: str,
        *,
        name: str | None = None,
        label: dict | None = None,
        position: int | None = None,
        applicable_types: list[str] | None = None,
    ) -> dict:
        """Partially updates an attribute group (``PATCH /attribute-groups/{id}``)."""
        payload: dict[str, object] = {
            k: v
            for k, v in {
                "name": name,
                "label": label,
                "position": position,
                "applicable_types": applicable_types,
            }.items()
            if v is not None
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.patch(
                f"{self.base_url}/attribute-groups/{group_id}",
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

    async def delete_attribute_group(self, token: str, group_id: str) -> None:
        """Deletes an attribute group (``DELETE /attribute-groups/{id}``)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(
                f"{self.base_url}/attribute-groups/{group_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                detail = None
                try:
                    detail = response.json().get("error")
                except Exception:  # noqa: BLE001 - body may not be JSON
                    detail = None
                raise CoreError(detail or response.text, status=response.status_code)

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
        ids: list[str] | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Lists job runs (newest first) for the dashboard / polling."""
        params: dict[str, str] = {"limit": str(limit)}
        if ids:
            params["ids"] = ",".join(ids)
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

    async def list_job_definitions(
        self,
        token: str,
        *,
        job_type: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
        start: int = 0,
    ) -> list[dict]:
        """Lists job definitions (admin only)."""
        params: dict[str, str] = {"limit": str(limit), "start": str(start)}
        if job_type:
            params["type"] = job_type
        if enabled is not None:
            params["enabled"] = "true" if enabled else "false"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/jobs",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_job_definition(
        self,
        token: str,
        *,
        name: str,
        job_type: str,
        schedule: str | None = None,
        parameters: dict | None = None,
        enabled: bool = True,
    ) -> dict:
        """Creates a job definition (``POST /jobs``, admin only)."""
        payload: dict[str, object] = {
            "name": name,
            "type": job_type,
            "parameters": parameters or {},
            "enabled": enabled,
        }
        if schedule:
            payload["schedule"] = schedule
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/jobs",
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

    async def update_job_definition(
        self,
        token: str,
        definition_id: str,
        *,
        name: str,
        schedule: str | None,
        enabled: bool,
        parameters: dict,
    ) -> dict:
        """Partially updates a job definition (``PATCH /jobs/{id}``, admin only).

        ``schedule=None`` clears the schedule (manual only); ``parameters`` is
        always sent as the full JSON object.
        """
        payload: dict[str, object] = {
            "name": name,
            "schedule": schedule,
            "enabled": enabled,
            "parameters": parameters,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.patch(
                f"{self.base_url}/jobs/{definition_id}",
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

    async def run_job(self, token: str, definition_id: str) -> dict:
        """Manually triggers a job definition (``POST /jobs/{id}/run``)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/jobs/{definition_id}/run",
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

    async def list_definition_runs(
        self,
        token: str,
        definition_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Lists job runs for a definition (``GET /jobs/{id}/runs``)."""
        params: dict[str, str] = {"limit": str(limit)}
        if status:
            params["status"] = status
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/jobs/{definition_id}/runs",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def post_bulk(
        self,
        token: str,
        *,
        op: str,
        object_ids: list[str],
        parameters: dict,
    ) -> dict:
        """Queues a mass operation (`POST /objects/bulk`) and returns the job run."""
        payload = {"op": op, "object_ids": object_ids, "parameters": parameters}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/objects/bulk",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    # --- Users / Groups / Impersonation ------------------------------------
    async def list_users(self, token: str, *, limit: int = 100, start: int = 0) -> list[dict]:
        """Lists users (admin only)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/users",
                params={"limit": str(limit), "start": str(start)},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_user(
        self,
        token: str,
        *,
        username: str,
        email: str,
        password: str,
        roles: list[str] | None = None,
        locale: str = "en",
        status: str = "active",
    ) -> dict:
        """Creates a user (admin only)."""
        payload: dict[str, object] = {
            "username": username,
            "email": email,
            "password": password,
            "roles": roles or [],
            "locale": locale,
            "status": status,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/users",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_user(
        self,
        token: str,
        user_id: str,
        *,
        email: str | None = None,
        password: str | None = None,
        roles: list[str] | None = None,
        status: str | None = None,
        locale: str | None = None,
    ) -> dict:
        """Partially updates a user (admin only)."""
        payload: dict[str, object] = {
            k: v
            for k, v in {
                "email": email,
                "password": password,
                "roles": roles,
                "status": status,
                "locale": locale,
            }.items()
            if v is not None
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.patch(
                f"{self.base_url}/users/{user_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_user(self, token: str, user_id: str) -> None:
        """Deletes a user (admin only)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(
                f"{self.base_url}/users/{user_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def list_groups(self, token: str, *, limit: int = 100, start: int = 0) -> list[dict]:
        """Lists groups (admin only)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/groups",
                params={"limit": str(limit), "start": str(start)},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_group(
        self,
        token: str,
        *,
        name: str,
        grant_permissions: list[str] | None = None,
        deny_permissions: list[str] | None = None,
        scope: dict | None = None,
        branding: dict | None = None,
    ) -> dict:
        """Creates a group (admin only)."""
        payload: dict[str, object] = {
            "name": name,
            "grant_permissions": grant_permissions or [],
            "deny_permissions": deny_permissions or [],
            "scope": scope or {},
            "branding": branding or {},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/groups",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_group(
        self,
        token: str,
        group_id: str,
        *,
        name: str | None = None,
        grant_permissions: list[str] | None = None,
        deny_permissions: list[str] | None = None,
        scope: dict | None = None,
        branding: dict | None = None,
    ) -> dict:
        """Partially updates a group (admin only)."""
        payload: dict[str, object] = {
            k: v
            for k, v in {
                "name": name,
                "grant_permissions": grant_permissions,
                "deny_permissions": deny_permissions,
                "scope": scope,
                "branding": branding,
            }.items()
            if v is not None
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.patch(
                f"{self.base_url}/groups/{group_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_group(self, token: str, group_id: str) -> None:
        """Deletes a group (admin only)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(
                f"{self.base_url}/groups/{group_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def impersonate_user(self, token: str, user_id: str) -> dict:
        """Impersonates a user (superadmin only) and returns a token for them."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/auth/impersonate/{user_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    def _error_detail(self, response: httpx.Response) -> str:
        """Extracts the core's ``error`` field (or the response text)."""
        try:
            return response.json().get("error") or response.text
        except Exception:  # noqa: BLE001 - body may not be JSON
            return response.text
