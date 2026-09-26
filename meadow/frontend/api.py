"""Thin async REST client for the PIM core."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable, Mapping

import httpx
from pydantic import BaseModel

#: Message the core returns when the bearer token is invalid or expired (as
#: opposed to `invalid credentials`, which a failed login returns).
SESSION_EXPIRED_MESSAGE = "invalid or expired token"

#: Callback invoked when the core rejects a call because the session is gone;
#: the frontend registers the redirect to the login page (`auth.register`).
_session_expired_handler: Callable[[], None] | None = None

#: Reused across every CoreClient call so the connection pool stays warm and
#: no new `httpx.AsyncClient` is built per request (which was the main source
#: of per-call latency). An optional (single-process) singleton.
_shared_client: httpx.AsyncClient | None = None
_SHARED_CLIENT_TIMEOUT = 30.0

#: Sentinel distinguishing "field not provided at all" from an explicit
#: ``None`` (e.g. clearing an ``enum_set_id`` on an attribute definition).
_UNSET = object()


@contextlib.asynccontextmanager
async def _api_client() -> AsyncIterator[httpx.AsyncClient]:
    """Yields the shared ASGI HTTP client without closing it.

    Using an asynccontextmanager keeps the existing `async with ... as client:`
    call sites unchanged (their indentation and body are untouched) while
    reusing a single long-lived connection pool. `trust_env=False` ignores
    proxy/`NO_PROXY` env vars that could otherwise be picked up during a
    request on Windows. The response hook watches for a lost session so every
    page returns to the login page instead of failing half-loaded.
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=_SHARED_CLIENT_TIMEOUT,
            trust_env=False,
            event_hooks={"response": [_watch_session]},
        )
    yield _shared_client


def set_session_expired_handler(handler: Callable[[], None] | None) -> None:
    """Registers (or with `None` clears) the callback that gets the user back
    to the login page."""
    global _session_expired_handler
    _session_expired_handler = handler


async def _watch_session(response: httpx.Response) -> None:
    """Response hook: reports an invalid/expired token to the frontend.

    A *failed login* also answers with 401 (`invalid credentials`), so the
    message decides: only a lost session triggers the handler. The body is not
    read yet at hook time, so it is awaited explicitly (httpx `aread`); only
    401 responses are inspected, so no other download is buffered here.
    """
    if response.status_code != 401:
        return
    try:
        await response.aread()
        body = response.text
    except httpx.HTTPError, ValueError:  # a body that cannot be read carries no verdict
        return
    if SESSION_EXPIRED_MESSAGE in body and _session_expired_handler is not None:
        _session_expired_handler()


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


def _loopback_host(base_url: str) -> str:
    """Rewrites `localhost` to `127.0.0.1` so every connection is instant.

    On Windows `localhost` resolves to `::1` (IPv6) before `127.0.0.1`. The PIM
    core binds IPv4 only (e.g. `0.0.0.0:8080`), so httpx waits ~2 s for the IPv6
    attempt to time out and then falls back to IPv4. Every new HTTP connection
    therefore adds ~2 s — the main cause of the "page starts loading" delay
    (every CoreClient call opens a fresh connection). Using the explicit IPv4
    loopback makes each new connection fast (~ms), so the client does not have
    to rely on the env `PIM_CORE_URL` using the right host.
    """
    return base_url.replace("://localhost", "://127.0.0.1")


class LoginResponse(BaseModel):
    """Response of `POST /auth/login`."""

    token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    """Response of `GET /auth/me`."""

    sub: str
    username: str
    roles: list[str]
    #: Whether the user still has to set their own password (the UI shows a
    #: hint/banner then). Absent counts as `true`.
    must_change_password: bool = True
    #: When the user last changed their own password (absent for seeded or
    #: admin-set passwords).
    password_changed_at: str | None = None
    #: Present when the session impersonates another user (superadmin).
    impersonator: dict | None = None


class CoreClient:
    """HTTP client for the headless PIM core."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = _loopback_host(base_url).rstrip("/")
        self.timeout = timeout

    async def login(self, username: str, password: str) -> LoginResponse:
        """Exchanges credentials for a JWT."""
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/auth/login",
                json={"username": username, "password": password},
            )
            response.raise_for_status()
            return LoginResponse.model_validate(response.json())

    async def me(self, token: str) -> MeResponse:
        """Returns the current user's claims for a bearer token."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return MeResponse.model_validate(response.json())

    async def change_password(
        self,
        token: str,
        *,
        current_password: str,
        new_password: str,
    ) -> LoginResponse:
        """Changes the caller's own password (``POST /auth/password``).

        Returns a fresh token that replaces the current session token (the core
        bumps the user's token version).
        """
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/auth/password",
                json={"current_password": current_password, "new_password": new_password},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return LoginResponse.model_validate(response.json())

    async def list_objects(
        self,
        token: str,
        *,
        object_type: str | None = None,
        ancestor: str | None = None,
        category: str | None = None,
        definition_id: str | None = None,
        collection: str | None = None,
        uncategorized: bool = False,
        completeness_lt: int | None = None,
        missing_required: bool = False,
        ids: list[str] | None = None,
        where: str | None = None,
        released_for: str | None = None,
        release_status: str | None = None,
        releases: bool = False,
        locale: str | None = None,
        resolve_inheritance: bool = False,
        sort: str | None = None,
        order: str | None = None,
        limit: int = 100,
        start: int = 0,
    ) -> list[dict]:
        """Lists objects, optionally filtered by type, ancestor, category or collection.

        With ``ancestor`` the result is limited to that composition subtree; with
        ``category`` to objects assigned to that category or its descendants;
        ``definition_id`` selects the category relation type used by
        ``category``/``uncategorized`` (default: the built-in primary type); with
        ``collection`` to objects that are members of the materialized collection
        (flat, no hierarchy); with ``uncategorized`` to objects with no category
        assignment; with ``released_for`` to objects **released for that channel**
        (``release_status``, default ``published``); with ``releases`` the response
        carries the channel releases of every object; with ``ids`` to exactly those
        objects
        (used by the grid search). With ``where`` (a compact JSON document of the
        query DSL — the grid's advanced search) the core renders the condition
        tree server-side against the search field catalog. With ``locale``, each
        object carries a ``completeness`` value for that language (computed in the
        core); with ``completeness_lt``/``missing_required`` the core only returns
        incomplete objects (filtered before ``start``/``limit`` apply). With
        ``sort``/``order`` the core orders the whole view (a sortable field of the
        search catalog, ``id`` as tiebreaker) before it applies ``start``/``limit`` —
        the grid pages through sorted views with it.
        """
        params = self._object_params(
            {
                "object_type": object_type,
                "ancestor": ancestor,
                "category": category,
                "definition_id": definition_id,
                "collection": collection,
                "uncategorized": uncategorized,
                "completeness_lt": completeness_lt,
                "missing_required": missing_required,
                "ids": ids,
                "where": where,
                "released_for": released_for,
                "release_status": release_status,
                "releases": releases,
                "locale": locale,
                "resolve_inheritance": resolve_inheritance,
                "sort": sort,
                "order": order,
                "limit": limit,
                "start": start,
            }
        )
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/objects",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def count_objects(self, token: str, **filters: object) -> dict:
        """Counts the objects a view contains (``GET /objects/count``).

        Takes the filters of `list_objects` (`**filters`, the grid builds them once for
        a page, its total and its column set — see `grid_query.py`) and answers
        ``{"count": n}``. The core ignores ``limit``/``start``/``sort`` here; the grid
        still sends the sort so a page and its total share one completeness walk.
        """
        return await self._object_query(token, "objects/count", filters)

    async def attribute_usage(self, token: str, **filters: object) -> dict:
        """The attribute names a view carries data for (``GET /objects/attribute-usage``).

        Same filters as `list_objects` plus ``sample`` (how many objects of the view are
        inspected, default in the core: 1000); answers ``{"names": [...], "sampled": n}``.
        """
        return await self._object_query(token, "objects/attribute-usage", filters)

    @staticmethod
    def _object_params(filters: Mapping[str, object]) -> dict[str, str]:
        """The query parameters of an object query.

        One builder for ``GET /objects``, ``/objects/count`` and
        ``/objects/attribute-usage``: the grid asks all three with the **same** filter
        arguments, so a parameter that is dropped here would silently change what a view
        means. ``object_type`` is the API's ``type`` (``type`` is a builtin in Python),
        a list of ids becomes a comma list, ``True`` becomes ``true`` and unset values
        (``None``/``False``/empty) are left out.
        """
        params: dict[str, str] = {}
        for key, value in filters.items():
            if value is None or (isinstance(value, str) and not value):
                continue
            name = "type" if key == "object_type" else key
            if isinstance(value, bool):
                if value:
                    params[name] = "true"
                continue
            if key == "ids":
                entries = value if isinstance(value, list | tuple | set) else [value]
                params[name] = ",".join(str(entry) for entry in entries)
                continue
            params[name] = str(value)
        return params

    async def _object_query(self, token: str, path: str, filters: Mapping[str, object]) -> dict:
        """Runs an object query that answers one object (count, attribute usage)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/{path}",
                params=self._object_params(filters),
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}

    async def get_object_stats(
        self,
        token: str,
        *,
        locale: str | None = None,
    ) -> dict:
        """Returns server-side dashboard stats (`GET /objects/stats`).

        Aggregates object counts by type/status and the number of incomplete
        objects per type for a working locale, so the dashboard does not have to
        load up to `limit` objects client-side.
        """
        params: dict[str, str] = {}
        if locale:
            params["locale"] = locale
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/objects/stats",
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
        """Lists one page of attribute definitions (``GET /attribute-definitions``).

        **Capped and codepoint-ordered:** the page is limited to ``limit`` rows and
        sorted by id, so with an imported standard installed (17 377 ``EF…``
        features) the first page holds nothing but standard vocabulary and a
        late-sorted own attribute is unreachable. No page uses this any more — UI
        pages that need the definitions load ``list_attribute_vocabulary``, which
        answers with **every** definition in one request. Kept for callers that
        really want a page (e.g. an id-sorted inspection).
        """
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/attribute-definitions",
                params={"limit": str(limit), "start": str(start)},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_attribute_vocabulary(self, token: str) -> list[dict]:
        """Returns the vocabulary of **all** attribute definitions.

        One request instead of 18 pages of `list_attribute_definitions`: the export
        wizard's field selection, the catalog page, the object grid's column picker
        and the product form all need every definition (ETIM 10.0 carries 17 377 of
        them, 17 463 in total here), and the core serves the projection from its
        metadata cache (`GET /attribute-definitions/vocabulary`). The projection
        carries label, data type, group ids, applicability, the reference-data flag,
        `is_key`/`inheritable`/`computed`/`required_types`, `enum_set_id` and
        `constraints` — everything a field renderer needs, minus the descriptions,
        default values, standard codes and inline option lists.
        """
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/attribute-definitions/vocabulary",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def get_attribute_definition(self, token: str, definition_id: str) -> dict:
        """Loads one attribute definition in full (``GET /attribute-definitions/{id}``).

        The catalog page and the product form list the vocabulary (all definitions
        in one request); the editor dialog needs the complete record — units,
        standard bindings, descriptions — so it loads it on open instead of saving a
        truncated payload back.
        """
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/attribute-definitions/{definition_id}",
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
        enum_remap: dict[str, str] | None = None,
        enum_set_id: str | None | object = _UNSET,
        **fields: object,
    ) -> dict:
        """Updates an attribute definition (partial PATCH).

        ``constraints`` is kept for backward compatibility (the product form
        adds enum options); additional fields (``label``, ``group_ids``,
        ``applicable_types``, ``required_types``, ...) are merged in.
        ``enum_remap`` (``{old: new}``) re-assigns objects when an enum option
        is removed while still in use. ``enum_set_id`` references a reusable
        enum set; pass an explicit ``None`` to clear it.
        """
        payload: dict[str, object] = {k: v for k, v in fields.items() if v is not None}
        if constraints is not None:
            payload["constraints"] = constraints
        if enum_remap:
            payload["enum_remap"] = enum_remap
        if enum_set_id is not _UNSET:
            payload["enum_set_id"] = enum_set_id
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/attribute-definitions/{definition_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def change_attribute_type(
        self,
        token: str,
        definition_id: str,
        *,
        definition_type: str,
        enum_set_id: str | None = None,
        unit_id: str | None = None,
        display_unit_id: str | None = None,
        drop_incompatible: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Changes the data type and migrates the values.

        ``POST /attribute-definitions/{id}/type-change`` converts every existing
        value; values that do not fit the new type block the change unless
        ``drop_incompatible`` removes them. With ``dry_run`` the core only
        returns the report (`checked`/`convertible`/`incompatible`/`samples`).
        """
        payload: dict[str, object] = {"type": definition_type}
        for key, value in (
            ("enum_set_id", enum_set_id),
            ("unit_id", unit_id),
            ("display_unit_id", display_unit_id),
        ):
            if value:
                payload[key] = value
        if drop_incompatible:
            payload["drop_incompatible"] = True
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/attribute-definitions/{definition_id}/type-change",
                json=payload,
                params={"dry_run": "true"} if dry_run else None,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def enum_usage(
        self,
        token: str,
        definition_id: str,
        value: str,
        *,
        limit: int = 50,
    ) -> dict:
        """Reports how many objects use a given enum value (Phase 9).

        Returns ``{value, count, items:[{id, type}]}``.
        """
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/attribute-definitions/{definition_id}/enum-usage",
                params={"value": value, "limit": str(limit)},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
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
        standard_codes: dict | None = None,
        reference_data: bool | None = None,
        enum_set_id: str | None = None,
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
            "standard_codes": standard_codes,
            "reference_data": reference_data,
            "enum_set_id": enum_set_id,
            "default_value": default_value,
            "description": description,
        }
        for key, value in optional.items():
            if value is not None:
                payload[key] = value
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/attribute-definitions",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_attribute_definition(
        self, token: str, definition_id: str, *, force: bool = False
    ) -> None:
        """Deletes an attribute definition (``DELETE /attribute-definitions/{id}``).

        Raises :class:`CoreError` with status 409 if the attribute is in use
        and ``force`` is not set; ``force=True`` removes values asynchronously.
        """
        params = {"force": "true"} if force else {}
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/attribute-definitions/{definition_id}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

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
        async with _api_client() as client:
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
        remove_attributes: list[str] | None = None,
    ) -> dict:
        """Applies a partial object update (attributes and/or status).

        ``remove_attributes`` deletes those attribute keys from the object, so
        the value is inherited again ("back to inherited").
        """
        params: dict[str, str] = {}
        if locale:
            params["locale"] = locale
        payload: dict = {}
        if attributes is not None:
            payload["attributes"] = attributes
        if status is not None:
            payload["status"] = status
        if remove_attributes:
            payload["remove_attributes"] = list(remove_attributes)
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/objects/{object_id}",
                params=params,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def create_object(
        self,
        token: str,
        *,
        object_type: str,
        attributes: dict | None = None,
        status: str = "draft",
        parent: str | None = None,
        source_id: str | None = None,
        source: str | None = None,
        locale: str | None = None,
        emit_events: bool = True,
    ) -> dict:
        """Creates an object (``POST /objects``).

        Categories are ordinary objects of ``type: "category"``; ``attributes``
        may carry a multilingual ``name`` map (``{en: ..., de: ...}``). ``parent``
        is the **composition parent** of the new object (``object:<key>``); it is
        part of the write, so a create never lands at the wrong place first and
        gets moved afterwards (`crates/AGENTS.md`: the parent is validated against
        the composition definition before anything is written).
        """
        params: dict[str, str] = {}
        if locale:
            params["locale"] = locale
        if not emit_events:
            params["emit_events"] = "false"
        payload: dict[str, object] = {
            "type": object_type,
            "attributes": attributes or {},
            "status": status,
        }
        if parent:
            payload["parent"] = parent
        if source_id:
            payload["source_id"] = source_id
        if source:
            payload["source"] = source
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/objects",
                params=params,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_object(self, token: str, object_id: str) -> None:
        """Deletes an object (soft delete with cascade, ``DELETE /objects/{id}``).

        Deleting a category also removes it and its sub-categories from every
        category tree and from member assignments.
        """
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/objects/{object_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def list_languages(self, token: str) -> list[dict]:
        """Lists the data languages from the language-tree table.

        Used by the product form to offer multilingual editing across every
        configured data language. Each node carries ``code``, ``parent``,
        ``label``, ``fallback_order``, ``active`` and ``is_ui``.
        """
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/languages",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return [data]
            return data

    async def create_language(
        self,
        token: str,
        *,
        code: str,
        parent: str | None = None,
        label: dict | None = None,
        active: bool = True,
        is_ui: bool = False,
    ) -> dict:
        """Creates a language tree node (``POST /languages``, admin only).

        ``parent`` is the parent record key (e.g. ``lang_de``) or ``None``/empty
        for the universal root.
        """
        payload: dict[str, object] = {
            "code": code,
            "parent": parent or "",
            "label": label or {},
            "active": active,
            "is_ui": is_ui,
        }
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/languages",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_language(
        self,
        token: str,
        code: str,
        *,
        parent: str | None | object = None,
        label: dict | None = None,
        active: bool | None = None,
        is_ui: bool | None = None,
    ) -> dict:
        """Partially updates a language tree node (``PATCH /languages/{code}``).

        ``parent`` is a special sentinel: omit it to keep the parent, pass an
        empty string to reset to the universal root, or pass a parent record key.
        """
        payload: dict[str, object] = {}
        if parent is not None:
            payload["parent"] = "" if parent == "" else str(parent)
        if label is not None:
            payload["label"] = label
        if active is not None:
            payload["active"] = active
        if is_ui is not None:
            payload["is_ui"] = is_ui
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/languages/{code}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_language(self, token: str, code: str) -> None:
        """Deletes a language tree node (``DELETE /languages/{code}``, admin)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/languages/{code}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def list_attribute_groups(self, token: str) -> list[dict]:
        """Lists attribute groups (used for the product form's sections)."""
        async with _api_client() as client:
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
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/attribute-groups",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
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
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/attribute-groups/{group_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_attribute_group(self, token: str, group_id: str) -> None:
        """Deletes an attribute group (``DELETE /attribute-groups/{id}``)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/attribute-groups/{group_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def list_object_children(
        self,
        token: str,
        object_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
        count_members: bool = False,
        definition_id: str | None = None,
        scope: str | None = None,
        object_type: str | None = None,
    ) -> list[dict]:
        """Lists an object's composition children (``rel_depends_on``).

        Each child carries ``hasChildren`` so the tree can show the expand arrow
        without loading the whole subtree; ``limit``/``offset`` paginate nodes
        with many direct children. With ``count_members``, category children also
        carry a ``memberCount`` (objects assigned through the category relation
        type given by ``definition_id``; default: the primary type).

        ``scope`` selects which children are returned: ``composition`` = the
        product structure (**without** the taxonomy nodes) or ``taxonomy`` = only
        the nodes of a category/classification tree. ``object_type`` narrows to
        one type (one taxonomy at a time). Both are filtered in the core, so
        paging and counting apply to the filtered set — the universal root carries
        thousands of taxonomy nodes.
        """
        params: dict[str, str] = {}
        if limit is not None:
            params["limit"] = str(limit)
        if offset:
            params["offset"] = str(offset)
        if count_members:
            params["count_members"] = "true"
        if definition_id:
            params["definition_id"] = definition_id
        if scope:
            params["scope"] = scope
        if object_type:
            params["type"] = object_type
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/objects/{object_id}/children",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def fetch_composition_types(self, token: str) -> dict:
        """The type-level edges of the composition tree (`GET /objects/composition-types`).

        Returns `{"root_type": <type of the universal root>, "edges": [{"parent",
        "child", "count"}]}`: which object type hangs under which along a composition
        edge. A picker needs it to know whether a target type is reachable in the
        composition tree at all (see `object_picker.composition_reachable`).
        """
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/objects/composition-types",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def get_object_tree(self, token: str) -> list[dict]:
        """Returns the composition tree (``{id, children}``) from the Universal-Root."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/objects/tree",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_object_ancestors(self, token: str, object_id: str) -> list[dict]:
        """Returns the path from the outermost ancestor down to the object.

        Each entry is the object document plus ``hasChildren`` — the same shape as
        ``list_object_children``, so a tree panel can render the path with
        ``tree_node_from_object``. The universal root is not part of the path: it
        is the parent of every tree's top level, not a node of it.
        """
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/objects/{object_id}/ancestors",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def batch_update(self, token: str, items: list[dict]) -> list[dict]:
        """Applies staged grid edits in a single batch call."""
        async with _api_client() as client:
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
        async with _api_client() as client:
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
        code_prefix: bool = False,
        label_prefix: bool = True,
        completeness: bool = True,
    ) -> list[dict]:
        """Searches objects by label for the reference picker.

        With ``locale`` each hit also carries a ``completeness`` value for that
        language (the picker badges how complete the target is); a caller that
        lists many hits without a badge passes ``completeness=False`` (the
        per-hit computation costs ~0.3 s in the core).

        ``code_prefix=True`` additionally matches a **partial** ``code``
        (``EC0123`` → every class ``EC0123…``): the core's full-text index only
        matches whole tokens, so a partial code finds nothing otherwise. Used by
        the taxonomy tree's search.

        ``label_prefix`` (default on) additionally matches a **partial label word**
        (``brake clean`` → *Brake cleaner*): without it a type-ahead that has not
        typed the word to its end returns nothing at all (2026-09-17). The core
        scans the languages of the fallback chain for it, so the typing caller
        debounces the field (measured ~75 ms with a limit, ~0.25 s worst case).
        """
        params: dict[str, str] = {"q": q, "limit": str(limit)}
        if types:
            params["types"] = ",".join(types)
        if locale:
            params["locale"] = locale
        if code_prefix:
            params["code_prefix"] = "true"
        if label_prefix:
            params["label_prefix"] = "true"
        if not completeness:
            params["completeness"] = "false"
        async with _api_client() as client:
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
        *,
        attributes: dict | None = None,
    ) -> dict:
        """Creates a reference between two objects."""
        payload: dict[str, object] = {
            "source": source,
            "target": target,
            "definition_id": definition_id,
        }
        if attributes:
            payload["attributes"] = attributes
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/references",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_reference(self, token: str, reference_id: str) -> None:
        """Deletes a reference."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/references/{reference_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()

    async def reparent_object(self, token: str, object_id: str, parent: str) -> dict:
        """Re-parents an object to a new composition parent (full record id)."""
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/objects/{object_id}/parent",
                json={"parent": parent},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_relation_definitions(self, token: str) -> list[dict]:
        """Lists relation definitions (e.g. for the reference picker)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/relation-definitions",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_relation_definition(
        self,
        token: str,
        *,
        name: str,
        label: dict | None = None,
        source_types: list[str] | None = None,
        target_types: list[str] | None = None,
        attributes: list[str] | None = None,
        role: str | None = None,
    ) -> dict:
        """Creates a custom relation type (aggregation, admin).

        `role` is the usage of the type (`link`, `category`).
        """
        payload: dict[str, object] = {"name": name}
        if label:
            payload["label"] = label
        if source_types:
            payload["source_types"] = source_types
        if target_types:
            payload["target_types"] = target_types
        if attributes:
            payload["attributes"] = attributes
        if role:
            payload["role"] = role
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/relation-definitions",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_relation_definition(
        self,
        token: str,
        id: str,
        *,
        label: dict | None = None,
        source_types: list[str] | None = None,
        target_types: list[str] | None = None,
        attributes: list[str] | None = None,
        role: str | None = None,
    ) -> dict:
        """Partially updates a relation type (admin; `role` for custom types)."""
        payload: dict[str, object] = {}
        if label is not None:
            payload["label"] = label
        if source_types is not None:
            payload["source_types"] = source_types
        if target_types is not None:
            payload["target_types"] = target_types
        if attributes is not None:
            payload["attributes"] = attributes
        if role is not None:
            payload["role"] = role
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/relation-definitions/{id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_relation_definition(self, token: str, id: str) -> None:
        """Deletes a custom relation type (admin)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/relation-definitions/{id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def update_reference(self, token: str, reference_id: str, attributes: dict) -> dict:
        """Updates a reference's edge metadata (``attributes``)."""
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/references/{reference_id}",
                json={"attributes": attributes},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def list_system_logs(
        self,
        token: str,
        *,
        limit: int = 10,
        start: int = 0,
        service: str | None = None,
        category: str | None = None,
        level: str | None = None,
        from_dt: str | None = None,
        to_dt: str | None = None,
        q: str | None = None,
    ) -> list[dict]:
        """Lists curated system log entries (admin only).

        Filters (all optional): ``service``, ``category``, ``level``, ``from_dt``/
        ``to_dt`` (ISO-8601), ``q`` (substring match on ``message``).
        """
        params: dict[str, str] = {"limit": str(limit), "start": str(start)}
        if service:
            params["service"] = service
        if category:
            params["category"] = category
        if level:
            params["level"] = level
        if from_dt:
            params["from"] = from_dt
        if to_dt:
            params["to"] = to_dt
        if q:
            params["q"] = q
        async with _api_client() as client:
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
        include_result: bool = True,
    ) -> list[dict]:
        """Lists job runs (newest first) for the dashboard / polling.

        `include_result=False` keeps the `result` payload out of the answer: a bulk
        import stores its full report there (tens of thousands of entries), an export
        its whole document (megabytes), so a listing of several runs pulls megabytes
        and the database resets the connection (`500 Connection reset`). List views
        pass `False` and fetch a single run when a detail dialog needs the report.

        `result_summary` is in the answer either way: the core stores the top-level
        fields of the result up to 2 KB (`format`, `exported`, `columns`, …), which is
        what a list shows about a run — see `export_page.recent_exports`.
        """
        params: dict[str, str] = {"limit": str(limit)}
        if ids:
            params["ids"] = ",".join(ids)
        if status:
            params["status"] = status
        if not include_result:
            params["include_result"] = "false"
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/jobs/runs",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def job_schema(self, token: str) -> dict:
        """Loads the parameter contract of every job type (``GET /jobs/schema``).

        The core owns the contract (it is written next to the job types the queue
        accepts), so the job form takes its type list and parameter hints from it —
        the same document the AI assistant reads. A failing call degrades to the
        page's fallback list.
        """
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/jobs/schema",
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
        async with _api_client() as client:
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
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/jobs",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
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
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/jobs/{definition_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def run_job(self, token: str, definition_id: str) -> dict:
        """Manually triggers a job definition (``POST /jobs/{id}/run``)."""
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/jobs/{definition_id}/run",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_job_definition(self, token: str, definition_id: str) -> None:
        """Deletes a job definition and its runs (``DELETE /jobs/{id}``, admin only)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/jobs/{definition_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def delete_job_run(self, token: str, run_id: str) -> None:
        """Deletes a single job run (``DELETE /jobs/runs/{run_id}``, admin only)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/jobs/runs/{run_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

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
        async with _api_client() as client:
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
        async with _api_client() as client:
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
        async with _api_client() as client:
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
        must_change_password: bool = True,
    ) -> dict:
        """Creates a user (admin only)."""
        payload: dict[str, object] = {
            "username": username,
            "email": email,
            "password": password,
            "roles": roles or [],
            "locale": locale,
            "status": status,
            "must_change_password": must_change_password,
        }
        async with _api_client() as client:
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
        must_change_password: bool | None = None,
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
                "must_change_password": must_change_password,
            }.items()
            if v is not None
        }
        async with _api_client() as client:
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
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/users/{user_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def list_groups(self, token: str, *, limit: int = 100, start: int = 0) -> list[dict]:
        """Lists groups (admin only)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/groups",
                params={"limit": str(limit), "start": str(start)},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_permissions(self, token: str) -> dict:
        """The permission catalog: areas, roles and one entry per permission.

        It is the input of the group editor (the core asks for `group.read`): the
        matrix renders exactly the keys the write path accepts, so a typo cannot
        reach the database and `deny` becomes visible instead of a second list.
        """
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/permissions",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def preview_scope(self, token: str, scope: dict) -> dict:
        """Validates a scope document and counts the objects it shows.

        The group editor builds a scope from blocks and cannot count it itself: the
        editing admin is normally not a member of the group, and the core is the only
        place that knows the rules. ``{"count", "total"}`` comes back; a scope the
        write path would refuse is a `CoreError` here too, with the same message.
        """
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/groups/preview-scope",
                json={"scope": scope},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
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
        async with _api_client() as client:
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
        async with _api_client() as client:
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
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/groups/{group_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def impersonate_user(self, token: str, user_id: str) -> dict:
        """Impersonates a user (superadmin only) and returns a token for them."""
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/auth/impersonate/{user_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    # --- System settings ----------------------------------------------------
    async def list_system_settings(
        self,
        token: str,
        *,
        include_source: bool = True,
    ) -> dict:
        """Lists runtime settings (admin). ``include_source`` returns
        ``{key: {value, source}}`` (source `default`|`env`|`db`)."""
        params: dict[str, str] = {}
        if include_source:
            params["include_source"] = "true"
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/system/settings",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def put_setting(self, token: str, key: str, value: object) -> dict:
        """Persists a runtime setting (``PUT /system/settings/{key}``, admin)."""
        async with _api_client() as client:
            response = await client.put(
                f"{self.base_url}/system/settings/{key}",
                json={"value": value},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_setting(self, token: str, key: str) -> None:
        """Resets a runtime setting to its default (``DELETE /system/settings/{key}``)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/system/settings/{key}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    # --- AI assistance -----------------------------------------------------
    async def ai_generate(self, token: str, payload: dict) -> dict:
        """Asks for a suggestion (``POST /ai/generate``, admin).

        The reply is ``{ok, script, ...}`` on success and ``{ok: false, errors}``
        when the model or the policy check refused — a refusal is a normal answer,
        not an HTTP error.
        """
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/ai/generate",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def ai_test(self, token: str) -> dict:
        """Tests the configured provider (``POST /ai/test``, admin)."""
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/ai/test",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    # --- Configuration export/import (MCF) --------------------------------
    async def export_config(
        self,
        token: str,
        *,
        sections: list[str] | None = None,
        include_system: bool = False,
    ) -> dict:
        """Exports the Meadow Configuration Format document
        (``GET /config/export``, admin). ``sections`` filters the sections;
        ``include_system`` includes seeded/system entries."""
        params: dict[str, str] = {}
        if sections:
            params["sections"] = ",".join(sections)
        if include_system:
            params["include_system"] = "true"
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/config/export",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def import_config(
        self,
        token: str,
        document: dict,
        *,
        mode: str = "merge",
        async_: bool = False,
    ) -> dict:
        """Imports an MCF document (``POST /config/import``, admin). With
        ``async_`` the document is stored and a ``config_import`` job queued
        (202 + ``job_run_id``); otherwise the report is returned."""
        params: dict[str, str] = {"mode": mode}
        if async_:
            params["async"] = "true"
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/config/import",
                params=params,
                json=document,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    # --- Business rules ---------------------------------------------------
    async def list_rules(
        self,
        token: str,
        *,
        rule_type: str | None = None,
        state: str | None = None,
        applies_to: str | None = None,
        limit: int = 1000,
        start: int = 0,
    ) -> list[dict]:
        """Lists business rules, optionally filtered by type, state, applies_to."""
        params: dict[str, str] = {"limit": str(limit), "start": str(start)}
        if rule_type:
            params["type"] = rule_type
        if state:
            params["state"] = state
        if applies_to:
            params["applies_to"] = applies_to
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/rules",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def get_rule(self, token: str, rule_id: str) -> dict:
        """Loads a single rule (``GET /rules/{id}``)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/rules/{rule_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_rule(
        self,
        token: str,
        *,
        name: str,
        rule_type: str,
        script: str,
        description: str | None = None,
        applies_to: list[str] | None = None,
    ) -> dict:
        """Creates a business rule (``POST /rules``, admin)."""
        payload: dict[str, object] = {
            "name": name,
            "type": rule_type,
            "script": script,
        }
        if description is not None:
            payload["description"] = description
        if applies_to:
            payload["applies_to"] = applies_to
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/rules",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_rule(
        self,
        token: str,
        rule_id: str,
        *,
        name: str | None = None,
        script: str | None = None,
        description: str | None | object = None,
        applies_to: list[str] | None = None,
    ) -> dict:
        """Partially updates a rule (``PATCH /rules/{id}``, admin).

        ``description`` uses a sentinel: omit it to keep the current value, pass
        ``None`` to clear it, or pass a string to set it.
        """
        payload: dict[str, object] = {}
        if name is not None:
            payload["name"] = name
        if script is not None:
            payload["script"] = script
        if description is not None:
            payload["description"] = description
        if applies_to is not None:
            payload["applies_to"] = applies_to
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/rules/{rule_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def activate_rule(self, token: str, rule_id: str) -> dict:
        """Activates a rule (``POST /rules/{id}/activate``, admin)."""
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/rules/{rule_id}/activate",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def disable_rule(self, token: str, rule_id: str) -> dict:
        """Disables a rule (``POST /rules/{id}/disable``, admin)."""
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/rules/{rule_id}/disable",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_rule(self, token: str, rule_id: str) -> None:
        """Deletes a rule (``DELETE /rules/{id}``, admin)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/rules/{rule_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def dry_run_rule(
        self,
        token: str,
        rule_id: str,
        *,
        object_id: str | None = None,
        object_data: dict | None = None,
    ) -> dict:
        """Runs a rule read-only (``POST /rules/{id}/dry-run``, admin).

        Provide either ``object_id`` (a real object) or ``object_data`` (a
        constructed candidate ``{type, status, attributes}``).
        """
        payload: dict[str, object] = {}
        if object_id:
            payload["object_id"] = object_id
        elif object_data:
            payload["object"] = object_data
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/rules/{rule_id}/dry-run",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def test_batch_rule(self, token: str, rule_id: str, object_ids: list[str]) -> list[dict]:
        """Runs a rule against a selection of objects (``POST /rules/{id}/test-batch``)."""
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/rules/{rule_id}/test-batch",
                json={"object_ids": object_ids},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def recompute_rule(
        self, token: str, rule_id: str, *, object_ids: list[str] | None = None
    ) -> dict:
        """Queues a recompute job (``POST /rules/{id}/recompute``, admin)."""
        params: dict[str, str] = {}
        if object_ids:
            params["object_ids"] = ",".join(object_ids)
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/rules/{rule_id}/recompute",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    # --- Connectors --------------------------------------------------------
    async def list_connector_types(self, token: str) -> list[dict]:
        """Loads the parameter schema of every connector type (``GET /connector-types``).

        The schema describes groups, fields, types, defaults, enums, conditional
        visibility and i18n keys; the admin UI renders its configuration form
        from it and the core validates the parameters against it.
        """
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/connector-types",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_connectors(
        self,
        token: str,
        *,
        connector_type: str | None = None,
        enabled: bool | None = None,
        limit: int = 1000,
        start: int = 0,
    ) -> list[dict]:
        """Lists connector configurations, optionally filtered by type/enabled."""
        params: dict[str, str] = {"limit": str(limit), "start": str(start)}
        if connector_type:
            params["type"] = connector_type
        if enabled is not None:
            params["enabled"] = "true" if enabled else "false"
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/connectors",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def get_connector(self, token: str, connector_id: str) -> dict:
        """Loads a single connector (``GET /connectors/{id}``)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/connectors/{connector_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_connector(
        self,
        token: str,
        *,
        name: str,
        connector_type: str,
        parameters: dict | None = None,
        enabled: bool = True,
    ) -> dict:
        """Creates a connector (``POST /connectors``, admin)."""
        payload: dict[str, object] = {
            "name": name,
            "type": connector_type,
            "enabled": bool(enabled),
            "parameters": parameters or {},
        }
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/connectors",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_connector(
        self,
        token: str,
        connector_id: str,
        *,
        name: str | None = None,
        connector_type: str | None = None,
        parameters: dict | None = None,
        parameters_mode: str | None = None,
        enabled: bool | None = None,
    ) -> dict:
        """Partially updates a connector (``PATCH /connectors/{id}``, admin).

        `parameters_mode` is `replace` (default, whole object) or `merge`
        (recursive, `null` deletes a key) — the guided form sends only the
        changed fields with `merge`.
        """
        payload: dict[str, object] = {}
        if name is not None:
            payload["name"] = name
        if connector_type is not None:
            payload["type"] = connector_type
        if parameters is not None:
            payload["parameters"] = parameters
        if parameters_mode is not None:
            payload["parameters_mode"] = parameters_mode
        if enabled is not None:
            payload["enabled"] = enabled
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/connectors/{connector_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_connector(self, token: str, connector_id: str) -> None:
        """Deletes a connector (``DELETE /connectors/{id}``, admin)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/connectors/{connector_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def test_connector(self, token: str, connector_id: str) -> str:
        """Queues a connection test (``POST /connectors/{id}/test``, admin).

        The core has no HTTP client of its own, so the test runs in the worker;
        the returned `job_run` id is polled with `list_job_runs` until the
        result (`{ok, …}`) is available.
        """
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/connectors/{connector_id}/test",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return str(response.json().get("job_run_id") or "")

    async def list_units(
        self,
        token: str,
        *,
        symbol: str | None = None,
        dimension: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Lists the unit catalog (``GET /units``)."""
        params: dict[str, str] = {"limit": str(limit)}
        if symbol:
            params["symbol"] = symbol
        if dimension:
            params["dimension"] = dimension
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/units",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_unit(
        self,
        token: str,
        *,
        symbol: str,
        canonical_unit_id: str,
        factor: float,
        offset: float = 0.0,
        label: dict | None = None,
    ) -> dict:
        """Creates a custom unit (``POST /units``, admin)."""
        payload: dict[str, object] = {
            "symbol": symbol,
            "canonical_unit_id": canonical_unit_id,
            "factor": factor,
            "offset": offset,
        }
        if label:
            payload["label"] = label
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/units",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_unit(
        self,
        token: str,
        unit_id: str,
        *,
        symbol: str | None = None,
        canonical_unit_id: str | None = None,
        factor: float | None = None,
        offset: float | None = None,
        label: dict | None = None,
    ) -> dict:
        """Partially updates a custom unit (``PATCH /units/{id}``, admin)."""
        payload: dict[str, object] = {}
        if symbol is not None:
            payload["symbol"] = symbol
        if canonical_unit_id is not None:
            payload["canonical_unit_id"] = canonical_unit_id
        if factor is not None:
            payload["factor"] = factor
        if offset is not None:
            payload["offset"] = offset
        if label is not None:
            payload["label"] = label
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/units/{unit_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_unit(self, token: str, unit_id: str) -> None:
        """Deletes a custom unit (``DELETE /units/{id}``, admin)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/units/{unit_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def convert_units(
        self,
        token: str,
        *,
        value: float,
        from_unit: str,
        to_unit: str | None = None,
    ) -> dict:
        """Converts a value between units (``GET /units/convert``)."""
        params: dict[str, str] = {"value": str(value), "from": from_unit}
        if to_unit:
            params["to"] = to_unit
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/units/convert",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_channels(
        self,
        token: str,
        *,
        active: bool | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Lists the channel catalog (``GET /channels``)."""
        params: dict[str, str] = {"limit": str(limit)}
        if active is not None:
            params["active"] = "true" if active else "false"
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/channels",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def create_channel(
        self,
        token: str,
        *,
        code: str,
        label: dict | None = None,
        description: str | None = None,
        active: bool = True,
        position: int = 0,
    ) -> dict:
        """Creates a channel (``POST /channels``, admin)."""
        payload: dict[str, object] = {"code": code, "active": active, "position": position}
        if label:
            payload["label"] = label
        if description:
            payload["description"] = description
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/channels",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_channel(
        self,
        token: str,
        channel_id: str,
        *,
        label: dict | None = None,
        description: str | None = None,
        active: bool | None = None,
        position: int | None = None,
    ) -> dict:
        """Partially updates a channel (``PATCH /channels/{id}``, admin)."""
        payload: dict[str, object] = {}
        if label is not None:
            payload["label"] = label
        if description is not None:
            payload["description"] = description
        if active is not None:
            payload["active"] = active
        if position is not None:
            payload["position"] = position
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/channels/{channel_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_channel(self, token: str, channel_id: str) -> None:
        """Deletes a channel (``DELETE /channels/{id}``, admin; 409 while in use)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/channels/{channel_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def list_object_releases(self, token: str, object_id: str) -> list[dict]:
        """Lists the channel releases of an object (``GET /objects/{id}/releases``)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/objects/{object_id}/releases",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def set_object_release(
        self,
        token: str,
        object_id: str,
        *,
        channel: str,
        status: str,
    ) -> dict:
        """Sets or changes a channel release (``PUT /objects/{id}/releases``)."""
        async with _api_client() as client:
            response = await client.put(
                f"{self.base_url}/objects/{object_id}/releases",
                json={"channel": channel, "status": status},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def withdraw_object_release(self, token: str, object_id: str, channel: str) -> None:
        """Withdraws a channel release (``DELETE /objects/{id}/releases/{channel}``)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/objects/{object_id}/releases/{channel}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def list_category_attributes(self, token: str, category_id: str) -> list[dict]:
        """Lists a class's features, own and inherited (``GET /categories/{id}/attributes``)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/categories/{category_id}/attributes",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def attach_category_attribute(
        self,
        token: str,
        category_id: str,
        *,
        attribute: str,
        required: bool = False,
        position: int | None = None,
    ) -> dict:
        """Attaches an attribute definition to a class (``POST /categories/{id}/attributes``)."""
        payload: dict[str, object] = {"attribute": attribute, "required": required}
        if position is not None:
            payload["position"] = position
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/categories/{category_id}/attributes",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_category_attribute(
        self,
        token: str,
        category_id: str,
        attribute: str,
        *,
        required: bool | None = None,
        position: int | None = None,
    ) -> dict:
        """Updates a class feature (``PATCH /categories/{id}/attributes/{attribute}``)."""
        payload: dict[str, object] = {}
        if required is not None:
            payload["required"] = required
        if position is not None:
            payload["position"] = position
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/categories/{category_id}/attributes/{attribute}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def detach_category_attribute(self, token: str, category_id: str, attribute: str) -> None:
        """Detaches a class feature (``DELETE /categories/{id}/attributes/{attribute}``)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/categories/{category_id}/attributes/{attribute}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def list_object_category_attributes(
        self, token: str, object_id: str, *, locale: str | None = None
    ) -> dict:
        """Resolved class features of an object (``GET /objects/{id}/category-attributes``)."""
        params: dict[str, str] = {}
        if locale:
            params["locale"] = locale
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/objects/{object_id}/category-attributes",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def list_enum_sets(self, token: str) -> list[dict]:
        """Lists enum sets with their options resolved (``GET /enum-sets``)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/enum-sets",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def create_enum_set(
        self,
        token: str,
        *,
        name: str,
        label: dict | None = None,
        options: list[str] | None = None,
        position: int = 0,
    ) -> dict:
        """Creates an enum set (``POST /enum-sets``, admin)."""
        payload: dict[str, object] = {"name": name, "position": position}
        if label:
            payload["label"] = label
        if options:
            payload["options"] = options
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/enum-sets",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_enum_set(
        self,
        token: str,
        set_id: str,
        *,
        label: dict | None = None,
        options: list[str] | None = None,
        position: int | None = None,
        option_remap: dict[str, str] | None = None,
    ) -> dict:
        """Partially updates an enum set (``PATCH /enum-sets/{id}``, admin)."""
        payload: dict[str, object] = {}
        if label is not None:
            payload["label"] = label
        if options is not None:
            payload["options"] = options
        if position is not None:
            payload["position"] = position
        if option_remap:
            payload["option_remap"] = option_remap
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/enum-sets/{set_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_enum_set(self, token: str, set_id: str) -> None:
        """Deletes an enum set (``DELETE /enum-sets/{id}``, admin)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/enum-sets/{set_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def list_enum_options(self, token: str, *, set_id: str | None = None) -> list[dict]:
        """Lists enum options (``GET /enum-options``), optionally by set."""
        params: dict[str, str] = {}
        if set_id:
            params["set_id"] = set_id
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/enum-options",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def create_enum_option(
        self,
        token: str,
        *,
        code: str,
        labels: dict | None = None,
        position: int = 0,
        active: bool = True,
    ) -> dict:
        """Creates an enum option (``POST /enum-options``, admin)."""
        payload: dict[str, object] = {
            "code": code,
            "position": position,
            "active": active,
        }
        if labels:
            payload["labels"] = labels
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/enum-options",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_enum_option(
        self,
        token: str,
        option_id: str,
        *,
        labels: dict | None = None,
        position: int | None = None,
        active: bool | None = None,
    ) -> dict:
        """Partially updates an enum option (``PATCH /enum-options/{id}``, admin)."""
        payload: dict[str, object] = {}
        if labels is not None:
            payload["labels"] = labels
        if position is not None:
            payload["position"] = position
        if active is not None:
            payload["active"] = active
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/enum-options/{option_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_enum_option(self, token: str, option_id: str) -> None:
        """Deletes an enum option (``DELETE /enum-options/{id}``, admin)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/enum-options/{option_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    def _error_detail(self, response: httpx.Response) -> str:
        """Extracts the core's ``error`` field (or the response text)."""
        try:
            return response.json().get("error") or response.text
        except ValueError, AttributeError:  # a body that is not a JSON object has no `error`
            return response.text

    async def list_saved_searches(
        self,
        token: str,
        *,
        limit: int = 1000,
        start: int = 0,
    ) -> list[dict]:
        """Lists saved searches (``GET /saved-searches``)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/saved-searches",
                params={"limit": str(limit), "start": str(start)},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_saved_search(
        self,
        token: str,
        *,
        name: str,
        query: dict,
        refresh_interval: str | None = None,
    ) -> dict:
        """Creates a saved search (``POST /saved-searches``, admin)."""
        payload: dict[str, object] = {"name": name, "query": query}
        if refresh_interval:
            payload["refresh_interval"] = refresh_interval
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/saved-searches",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_saved_search(
        self,
        token: str,
        search_id: str,
        *,
        name: str | None = None,
        query: dict | None = None,
        refresh_interval: str | None | object = None,
    ) -> dict:
        """Partially updates a saved search (``PATCH /saved-searches/{id}``, admin)."""
        payload: dict[str, object] = {}
        if name is not None:
            payload["name"] = name
        if query is not None:
            payload["query"] = query
        if refresh_interval is not None:
            payload["refresh_interval"] = refresh_interval
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/saved-searches/{search_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_saved_search(self, token: str, search_id: str) -> None:
        """Deletes a saved search (``DELETE /saved-searches/{id}``, admin)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/saved-searches/{search_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def execute_saved_search(self, token: str, search_id: str) -> dict:
        """Executes a saved search (``POST /saved-searches/{id}/execute``)."""
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/saved-searches/{search_id}/execute",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def list_search_fields(self, token: str) -> dict:
        """Returns the search field/operator catalog (``GET /search-fields``)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/search-fields",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def preview_saved_search(self, token: str, query: dict) -> dict:
        """Runs a query without storing it (``POST /saved-searches/preview``)."""
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/saved-searches/preview",
                json=query,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def list_collections(
        self,
        token: str,
        *,
        limit: int = 1000,
        start: int = 0,
    ) -> list[dict]:
        """Lists collections (``GET /collections``)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/collections",
                params={"limit": str(limit), "start": str(start)},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_collection(
        self,
        token: str,
        *,
        name: str,
        saved_search_id: str,
        refresh_interval: str | None = None,
    ) -> dict:
        """Creates a collection (``POST /collections``, admin)."""
        payload: dict[str, object] = {
            "name": name,
            "saved_search_id": saved_search_id,
        }
        if refresh_interval:
            payload["refresh_interval"] = refresh_interval
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/collections",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_collection(
        self,
        token: str,
        collection_id: str,
        *,
        name: str | None = None,
        saved_search_id: str | None = None,
        refresh_interval: str | None | object = None,
    ) -> dict:
        """Partially updates a collection (``PATCH /collections/{id}``, admin)."""
        payload: dict[str, object] = {}
        if name is not None:
            payload["name"] = name
        if saved_search_id is not None:
            payload["saved_search_id"] = saved_search_id
        if refresh_interval is not None:
            payload["refresh_interval"] = refresh_interval
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/collections/{collection_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_collection(self, token: str, collection_id: str) -> None:
        """Deletes a collection (``DELETE /collections/{id}``, admin)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/collections/{collection_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def refresh_collection(self, token: str, collection_id: str) -> dict:
        """Refreshes a collection (``POST /collections/{id}/refresh``)."""
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/collections/{collection_id}/refresh",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def list_collection_members(
        self,
        token: str,
        collection_id: str,
        *,
        limit: int = 1000,
        start: int = 0,
    ) -> list[dict]:
        """Lists the members of a collection (``GET /collections/{id}/members``)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/collections/{collection_id}/members",
                params={"limit": str(limit), "start": str(start)},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def add_collection_members(
        self,
        token: str,
        collection_id: str,
        *,
        object_ids: list[str],
    ) -> dict:
        """Bulk-adds members to a collection (``POST /collections/{id}/members``, editor+).

        Returns ``{"added": N, "skipped": M}``; existing members are skipped.
        """
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/collections/{collection_id}/members",
                json={"object_ids": object_ids},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def list_object_types(self, token: str, *, limit: int = 1000) -> list[dict]:
        """Lists the managed object types (``GET /object-types``)."""
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/object-types",
                params={"limit": str(limit)},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_classification_standards(
        self, token: str, *, counts: bool = False
    ) -> list[dict]:
        """Lists the registered classification standards (`GET /classification-standards`).

        ``counts`` adds the live number of class nodes and feature rows of the
        standard's node type (two extra queries in the core).
        """
        params = {"counts": "true"} if counts else {}
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/classification-standards",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def list_export_profiles(self, token: str, *, format: str | None = None) -> list[dict]:
        """Lists the saved export profiles (``GET /export-profiles``, editor+).

        A profile is the export configuration without the ``source``: the format
        plus the field list, locales, options and ``bmecat`` block. The wizard
        loads one to prefill its form and can save the current settings back.
        """
        params = {"format": format} if format else {}
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/export-profiles",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

    async def create_export_profile(
        self,
        token: str,
        *,
        name: str,
        format: str,
        notes: str | None = None,
        parameters: dict | None = None,
    ) -> dict:
        """Creates a saved export profile (``POST /export-profiles``, admin)."""
        payload: dict[str, object] = {"name": name, "format": format}
        if notes:
            payload["notes"] = notes
        if parameters is not None:
            payload["parameters"] = parameters
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/export-profiles",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_export_profile(
        self,
        token: str,
        profile_id: str,
        *,
        format: str | None = None,
        notes: str | None = None,
        parameters: dict | None = None,
    ) -> dict:
        """Updates a saved export profile (``PATCH /export-profiles/{id}``, admin).

        ``name`` is immutable; an empty ``notes`` clears the field.
        """
        payload: dict[str, object] = {}
        if format is not None:
            payload["format"] = format
        if notes is not None:
            payload["notes"] = notes
        if parameters is not None:
            payload["parameters"] = parameters
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/export-profiles/{profile_id}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_export_profile(self, token: str, profile_id: str) -> None:
        """Deletes a saved export profile (``DELETE /export-profiles/{id}``, admin)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/export-profiles/{profile_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)

    async def create_export(
        self,
        token: str,
        *,
        source: dict,
        fields: list[str] | None = None,
        format: str = "csv",
        locales: list[str] | None = None,
        options: dict | None = None,
        bmecat: dict | None = None,
    ) -> dict:
        """Queues a structured export (``POST /exports``, editor+).

        Returns ``{"job_run_id": ...}``. The worker resolves the ``source`` and
        stores the serializer-ready output in ``job_run.result``; the file is
        fetched later via :meth:`get_export_file`. ``bmecat`` carries the catalogue
        metadata of a BMEcat export (only used with ``format="bmecat"``).
        """
        payload: dict[str, object] = {"source": source, "format": format}
        if fields:
            payload["fields"] = fields
        if locales:
            payload["locales"] = locales
        if options:
            payload["options"] = options
        if bmecat:
            payload["bmecat"] = bmecat
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/exports",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def get_export_file(
        self,
        token: str,
        run_id: str,
        *,
        format: str = "csv",
    ) -> tuple[bytes, str]:
        """Downloads an export file (``GET /exports/{run_id}/file``, editor+).

        Returns ``(content, filename)``. The core reads ``job_run.result`` and
        serialises the file on the fly; for large exports it may ``302`` to a
        presigned object-storage URL, which this method follows.
        """
        async with _api_client() as client:
            response = await client.get(
                f"{self.base_url}/exports/{run_id}/file",
                params={"format": format},
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=True,
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            filename = f"meadow-export.{format}"
            disposition = response.headers.get("content-disposition", "")
            if "filename=" in disposition:
                filename = disposition.split("filename=")[-1].rstrip('"').split('"')[0]
            return response.content, filename

    async def create_import_mef(
        self,
        token: str,
        document: dict,
        *,
        match_by_key: str | None = None,
        match_by_id: bool = False,
        source: str | None = None,
        emit_events: bool = True,
    ) -> dict:
        """Submits a MEF document (``POST /imports``, editor+) and queues an import.

        Returns ``{"job_run_id": ...}``; the ``inbound_import`` worker upserts the
        objects (by business key, object id or source reference).
        """
        params: dict[str, str] = {}
        if match_by_key:
            params["match_by_key"] = match_by_key
        if match_by_id:
            params["match_by_id"] = "true"
        if source:
            params["source"] = source
        if not emit_events:
            params["emit_events"] = "false"
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/imports",
                params=params,
                json=document,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def create_object_type(
        self,
        token: str,
        *,
        name: str,
        label: dict | None = None,
        description: str | None = None,
        key_attributes: list[str] | None = None,
        label_attribute: str | None = None,
        taxonomy: bool = False,
    ) -> dict:
        """Creates an object type (``POST /object-types``, admin)."""
        payload: dict[str, object] = {"name": name}
        if label:
            payload["label"] = label
        if description:
            payload["description"] = description
        if key_attributes:
            payload["key_attributes"] = key_attributes
        if label_attribute:
            payload["label_attribute"] = label_attribute
        if taxonomy:
            payload["taxonomy"] = True
        async with _api_client() as client:
            response = await client.post(
                f"{self.base_url}/object-types",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def update_object_type(
        self,
        token: str,
        name: str,
        *,
        label: dict | None = None,
        description: str | None = None,
        key_attributes: list[str] | None = None,
        label_attribute: str | None | object = None,
        taxonomy: bool | None = None,
    ) -> dict:
        """Partially updates an object type (``PATCH /object-types/{name}``, admin)."""
        payload: dict[str, object] = {}
        if label is not None:
            payload["label"] = label
        if description is not None:
            payload["description"] = description
        if key_attributes is not None:
            payload["key_attributes"] = key_attributes
        if label_attribute is not None:
            payload["label_attribute"] = label_attribute
        if taxonomy is not None:
            payload["taxonomy"] = taxonomy
        async with _api_client() as client:
            response = await client.patch(
                f"{self.base_url}/object-types/{name}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
            return response.json()

    async def delete_object_type(self, token: str, name: str) -> None:
        """Deletes an object type (``DELETE /object-types/{name}``, admin)."""
        async with _api_client() as client:
            response = await client.delete(
                f"{self.base_url}/object-types/{name}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.is_error:
                raise CoreError(self._error_detail(response), status=response.status_code)
