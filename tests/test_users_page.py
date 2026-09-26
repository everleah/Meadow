"""Page-level tests for `/users` — the net for the group editor.

The group editor edits two things that the core would not survive losing: the
permission lists and the ABAC scope. Both were a **round trip** the unit tests
missed — saving the seeded group once wiped its `object.publish` because the grid
hands its *row* (rights joined into display strings) to the dialog and the form
started empty (found live 2026-09-26, fixed in `_group_for`). These tests drive the
real page through NiceGUI's user simulation and read what actually reaches
`PATCH /groups`.

Replaced, like in `test_products_page.py`: the core client (`FakeCore`) and the
auth middleware; `auth.is_admin()` answers `True`, so the page builds.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing import User, user_simulation

from meadow.frontend.i18n import t

EN = "en"
MAIN_FILE = Path(__file__).resolve().parents[1] / "meadow" / "frontend" / "__main__.py"

GROUP_ID = "group:webshop"
#: A second group below the edited one: its members are inherited.
CHILD_ID = "group:team-a"
#: A user to put into a group (viewer).
USER_ID = "user:dryviewer"
GROUP: dict[str, Any] = {
    "id": GROUP_ID,
    "name": "webshop",
    "grant_permissions": ["object.publish"],
    "deny_permissions": [],
    "scope": {
        "rules": [
            {"field": "type", "operator": "in", "value": ["product"]},
            {"reference": {"type": "rel_category_member", "out": "object:webshop"}},
        ]
    },
    "branding": {},
}
#: Two permissions, so a matrix row can be found by its area.
CATALOG: dict[str, Any] = {
    "roles": [{"key": "viewer", "label_key": "role.viewer"}],
    "areas": [{"key": "object", "label_key": "perm.area.object"}],
    "permissions": [
        {
            "key": "object.read",
            "area": "object",
            "action": "read",
            "index": 0,
            "roles": ["viewer"],
            "label_key": "perm.object.read",
            "description": "",
        },
        {
            "key": "object.publish",
            "area": "object",
            "action": "publish",
            "index": 1,
            "roles": ["editor"],
            "label_key": "perm.object.publish",
            "description": "",
        },
    ],
}
OBJECT_TYPES: list[dict[str, Any]] = [
    {"id": "object_type:product", "name": "product", "label": {"en": "Product", "de": "Produkt"}},
]


class FakeCore:
    """Stands in for `CoreClient`: records every call and answers with fixed data."""

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    data: dict[str, Any] = {}

    def __init__(self, base_url: str, **kwargs: Any) -> None:
        self.base_url = base_url
        self.timeout = kwargs.get("timeout", 10.0)

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        async def call(*args: Any, **kwargs: Any) -> Any:
            FakeCore.calls.append((name, args, kwargs))
            if name in FakeCore.data:
                answer = FakeCore.data[name]
                if isinstance(answer, Exception):
                    raise answer
                return answer
            return [] if name.startswith("list_") else {}

        return call

    @classmethod
    def reset(cls, data: dict[str, Any] | None = None) -> None:
        cls.calls = []
        cls.data = data or {}

    @classmethod
    def payload(cls, name: str) -> dict[str, Any]:
        """The keyword arguments of the last call of `name`."""
        found = [kwargs for method, _, kwargs in cls.calls if method == name]
        assert found, f"`{name}` was never called"
        return found[-1]

    @classmethod
    def args(cls, name: str) -> tuple[Any, ...]:
        """The positional arguments of the last call of `name`."""
        found = [args for method, args, _ in cls.calls if method == name]
        assert found, f"`{name}` was never called"
        return found[-1]


def _drop_frontend_modules() -> None:
    """Forgets the frontend modules so the simulation imports them fresh."""
    for name in [name for name in sys.modules if name.startswith("meadow.frontend")]:
        del sys.modules[name]


@pytest.fixture
async def users_user(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[User]:
    """Boots the real app for one test, with a stub core and an admin session."""
    _drop_frontend_modules()
    async with user_simulation(main_file=MAIN_FILE) as user:
        from meadow.frontend import auth, layout, users

        FakeCore.reset(
            {
                "list_users": [
                    {
                        "id": USER_ID,
                        "username": "dryviewer",
                        "roles": ["viewer"],
                        "status": "active",
                    }
                ],
                "list_groups": [GROUP],
                "list_permissions": CATALOG,
                "list_object_types": OBJECT_TYPES,
                # The scope card resolves its stored target ids to labels with one read.
                "list_objects": [
                    {
                        "id": "object:webshop",
                        "attributes": {"name": {"en": "Webshop", "de": "Webshop"}},
                    }
                ],
            }
        )
        monkeypatch.setattr(users, "CoreClient", FakeCore)
        monkeypatch.setattr(layout, "CoreClient", FakeCore)
        monkeypatch.setattr(auth, "is_admin", lambda: True)
        monkeypatch.setattr(auth, "is_superadmin", lambda: True)

        async def allow(  # the middleware calls `self.dispatch(request, call_next)`
            self: Any, request: Any, call_next: Callable[[Any], Awaitable[Any]]
        ) -> Any:
            return await call_next(request)

        monkeypatch.setattr(auth.AuthMiddleware, "dispatch", allow)
        yield user


async def _wait_until(condition: Callable[[], bool], tries: int = 60) -> None:
    """Waits for recorded state (a click on an async handler only schedules it)."""
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition was not reached")


async def _open_group_editor(user: User) -> None:
    """Opens the group editor the way the grid does: with its own row payload.

    The handler is **async** (`open_group_editor` fills the form, resolves the scope
    labels and asks the core for the counter), and the dialog's widgets exist from
    page build time — so the only reliable "it is filled" signal is the core call the
    fill ends with (`preview_scope`).
    """
    await user.open("/users")
    user.find(marker="groups-grid").trigger(
        "cellDoubleClicked",
        {"data": {"id": GROUP_ID, "name": "webshop", "grant": "object.publish"}},
    )
    await _wait_until(lambda: any(name == "preview_scope" for name, _, _ in FakeCore.calls))


def _texts(user: User, marker: str) -> list[str]:
    """The rendered texts of every element with that marker (empty when there is none).

    `User.find` raises `AssertionError` instead of returning nothing, so "no member yet" has
    to be read as an empty list here rather than as a failed lookup.
    """
    try:
        elements = user.find(marker=marker).elements
    except AssertionError:
        return []
    return [str(getattr(element, "text", "")) for element in elements]


def _pick(user: User, marker: str, value: str) -> None:
    """Chooses an option the way a user does, so the change handlers run.

    NiceGUI's interaction helper types into inputs but has no equivalent for selects,
    so this sets the value inside the client context — exactly what `type()` does.
    """
    assert user.client is not None
    element = next(iter(user.find(marker=marker).elements))
    assert isinstance(element, ui.select)
    with user.client:
        element.value = value


async def test_a_member_can_be_added_and_removed(users_user: User) -> None:
    """The members are edges, not form fields: adding one writes immediately.

    The write ends **every** session (`auth_version`), so the page must survive it — the
    core hands a re-derived token back and the client sends the token that is current
    (`api._send_current_token`, `auth.handle_renewed_token`).
    """
    await _open_group_editor(users_user)
    assert _texts(users_user, "member-label") == []

    # The core answers with the membership after the write; the card re-reads the edges.
    FakeCore.data["memberships"] = {
        "members": [{"user": USER_ID, "group": GROUP_ID}],
        "parents": [],
    }
    _pick(users_user, "member-add-select", USER_ID)
    users_user.find(marker="member-add").click()
    await _wait_until(lambda: any(name == "add_group_member" for name, _, _ in FakeCore.calls))
    called = FakeCore.args("add_group_member")
    assert called[1] == GROUP_ID and called[2] == USER_ID
    await _wait_until(lambda: _texts(users_user, "member-label") == ["dryviewer · viewer"])
    # The confirmation has to arrive **before** the section re-renders itself: the re-render
    # deletes the button this handler belongs to, and a notify afterwards raises (found live
    # 2026-09-26 - every member change logged "parent element ... has been deleted").
    assert users_user.notify.contains(t("groups.members_saved", locale=EN)), (
        users_user.notify.messages
    )
    # The list comes back from the core, not from the local state.
    assert [name for name, _, _ in FakeCore.calls].count("memberships") == 2


async def test_a_member_of_a_child_group_is_shown_as_inherited(users_user: User) -> None:
    """Rights are inherited upwards, so the member list has to name the child group.

    Without the inherited rows the dialog would understate who carries these rights.
    """
    FakeCore.data["memberships"] = {
        "members": [{"user": USER_ID, "group": CHILD_ID}],
        "parents": [{"child": CHILD_ID, "parent": GROUP_ID}],
    }
    FakeCore.data["list_groups"] = [GROUP, {"id": CHILD_ID, "name": "team-a"}]
    await _open_group_editor(users_user)

    assert _texts(users_user, "member-label") == ["dryviewer · viewer"]
    assert _texts(users_user, "member-inherited") == [t("groups.members_inherited", group="team-a")]
    # An inherited membership is not removable here — it belongs to the other group.
    assert _texts(users_user, "hierarchy-chip") == ["team-a"]


async def test_a_cycle_in_the_hierarchy_is_reported_from_the_core(users_user: User) -> None:
    """The core refuses a cycle with `400`; the dialog shows why instead of failing."""
    from meadow.frontend.api import CoreError

    FakeCore.data["add_group_child"] = CoreError(
        "linking `group:team-a` below `group:webshop` would close a cycle", status=400
    )
    FakeCore.data["list_groups"] = [GROUP, {"id": CHILD_ID, "name": "team-a"}]
    await _open_group_editor(users_user)

    _pick(users_user, "hierarchy-add-child", CHILD_ID)
    users_user.find(marker="hierarchy-link-child").click()
    await _wait_until(lambda: users_user.notify.contains(t("groups.hierarchy_failed", locale=EN)))
    assert users_user.notify.contains(t("groups.hierarchy_failed", locale=EN)), (
        users_user.notify.messages
    )


async def test_the_two_special_cases_are_named(users_user: User) -> None:
    """The dialog says what the list cannot show.

    User decision 2026-09-26: **only a superadmin** is exempt from every check, so their
    membership is inert — while an `admin` **is** restricted by the group's scope. Both are
    invisible in a plain member list and both are what an admin needs to know before
    handing out rights.
    """
    FakeCore.data["list_users"] = [
        {"id": "user:root", "username": "root", "roles": ["superadmin"]},
        {"id": "user:boss", "username": "boss", "roles": ["admin"]},
    ]
    FakeCore.data["memberships"] = {
        "members": [
            {"user": "user:root", "group": GROUP_ID},
            {"user": "user:boss", "group": GROUP_ID},
        ],
        "parents": [],
    }
    await _open_group_editor(users_user)

    assert sorted(_texts(users_user, "member-label")) == ["boss · admin", "root · superadmin"]
    assert _texts(users_user, "members-superadmin-hint") == [
        t("groups.members_superadmin_hint", locale=EN)
    ]
    # `GROUP` carries a scope, so the admin in it is restricted by that scope.
    assert _texts(users_user, "members-scope-hint") == [t("groups.members_scope_hint", locale=EN)]


async def test_the_editor_shows_the_rights_of_the_group(users_user: User) -> None:
    """The matrix starts from the **group**, not from the grid row.

    The row carries `grant`/`deny` as display strings; reading it as if it were the
    group made every toggle start at `inherit`.
    """
    await _open_group_editor(users_user)

    grants = users_user.find(marker="permission-toggle")
    assert grants.elements, "the matrix must render one toggle per permission"
    values = {
        str(element.props.get("data-key")): getattr(element, "value", None)
        for element in grants.elements
    }
    assert values == {"object.read": "inherit", "object.publish": "grant"}


async def test_saving_keeps_the_rights_that_are_shown(users_user: User) -> None:
    """The round trip the live check found: what the form shows is what it sends."""
    await _open_group_editor(users_user)

    users_user.find(marker="group-save").click()
    await _wait_until(lambda: any(name == "update_group" for name, _, _ in FakeCore.calls))

    payload = FakeCore.payload("update_group")
    assert payload["grant_permissions"] == ["object.publish"]
    assert payload["deny_permissions"] == []


async def test_the_scope_card_shows_the_stored_scope_in_words(users_user: User) -> None:
    """The blocks come from the document, the sentence from the labels.

    A rule stores record ids; the card shows names — the picker fills the cache for
    what it hands over, `_resolve_target_labels` covers a document written earlier.
    """
    await _open_group_editor(users_user)

    expected = t(
        "groups.scope_sentence",
        parts=f"{t('groups.scope_phrase_type', values='Product')} "
        f"{t('groups.scope_and')} "
        f"{t('groups.scope_phrase_category', values='Webshop')}",
        locale=EN,
    )
    await _wait_until(
        lambda: any(
            getattr(element, "text", None) == expected
            for element in users_user.find(marker="scope-sentence").elements
        )
    )
    assert [name for name, _, _ in FakeCore.calls].count("preview_scope") == 1


async def test_an_added_block_is_not_counted_before_it_has_values(users_user: User) -> None:
    """An empty block is not a scope yet: the card says so instead of asking the core.

    The core refuses a rule without values (`400`), so the added block must not turn
    the counter into an error message — it says what is missing.
    """
    await _open_group_editor(users_user)
    before = [name for name, _, _ in FakeCore.calls].count("preview_scope")

    users_user.find(marker="scope-add-status").click()
    await _wait_until(
        lambda: any(
            getattr(element, "text", None) == t("groups.scope_count_incomplete", locale=EN)
            for element in users_user.find(marker="scope-count").elements
        )
    )

    assert [name for name, _, _ in FakeCore.calls].count("preview_scope") == before
