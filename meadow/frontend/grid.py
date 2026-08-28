"""Object list grid (AG Grid): the main work area (Phase 7, step 3).

The first stage is a clean, read-only object listing with human-friendly
display (resolved name, status badge). Later stages add inline editing /
batch save, category & structure navigation, and the column picker.
"""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import config
from meadow.frontend.api import CoreClient, is_session_expired
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import build_shell


def resolve_value(value: object, locale: str) -> str:
    """Resolves a (possibly multilingual) attribute value to a display string."""
    if isinstance(value, dict):
        if locale in value:
            return str(value[locale])
        if value:
            return str(next(iter(value.values())))
        return ""
    return "" if value is None else str(value)


def status_badge(status: str) -> str:
    """Returns a coloured badge for a workflow status."""
    classes = {
        "published": "bg-primary text-white",
        "draft": "bg-neutral text-gray-600",
        "archived": "bg-info text-white",
    }.get(status, "bg-secondary text-white")
    return f'<span class="px-2 py-0.5 rounded text-xs {classes}">{status}</span>'


def row_data(object_: dict, locale: str) -> dict:
    """Maps a core object to an AG Grid row."""
    attributes = object_.get("attributes") or {}
    return {
        "_id": str(object_.get("id", "")),
        "_name": resolve_value(attributes.get("name"), locale),
        "_type": str(object_.get("type", "")),
        "_status": status_badge(str(object_.get("status", ""))),
    }


def build_patch(object_id: str, client_name: object, original: dict, locale: str) -> dict:
    """Builds a batch-update item for an edited name value.

    Preserves other locales when the attribute is multilingual; otherwise the
    scalar name is replaced.
    """
    original_name = (original.get("attributes") or {}).get("name")
    if isinstance(original_name, dict):
        new_name = dict(original_name)
        new_name[locale] = client_name
    else:
        new_name = client_name
    return {"id": object_id, "attributes": {"name": new_name}}


@ui.page("/products")
async def products_page() -> None:
    """Lists objects in the main grid with inline editing + batch save."""
    build_shell(active="/products")
    locale = current_locale()
    with ui.column().classes("w-full p-6"):
        ui.label(t("nav.products")).classes("text-h4")
        client = CoreClient(config.CORE_URL)
        token = app.storage.user.get("token", "")
        try:
            objects = await client.list_objects(token, limit=100)
        except Exception:  # noqa: BLE001 - show a friendly message on failure
            ui.label(t("grid.load_failed")).classes("text-subtitle1 text-negative")
            return
        if not objects:
            ui.label(t("grid.no_objects")).classes("text-subtitle1")
            return

        original_by_id = {str(object_.get("id")): object_ for object_ in objects}
        staged_ids: set[str] = set()

        def mark_changed(event) -> None:
            data = event.args.get("data") if isinstance(event.args, dict) else None
            oid = data.get("_id") if isinstance(data, dict) else None
            if oid:
                staged_ids.add(oid)
                staged_label.set_text(t("grid.staged_changes").format(count=len(staged_ids)))

        async def save() -> None:
            client_rows = await grid.get_client_data()
            patches = []
            for row in client_rows:
                oid = row.get("_id")
                original = original_by_id.get(oid)
                if original is None:
                    continue
                original_name = resolve_value(
                    (original.get("attributes") or {}).get("name"), locale
                )
                if row.get("_name") != original_name:
                    patches.append(build_patch(oid, row.get("_name"), original, locale))
            if not patches:
                ui.notify(t("grid.no_changes"))
                return
            try:
                results = await client.batch_update(token, patches)
            except Exception:  # noqa: BLE001 - show a friendly message on failure
                ui.notify(t("grid.save_failed"), color="negative")
                return
            if is_session_expired(results):
                ui.notify(t("grid.session_expired"), color="warning")
                app.storage.user.clear()
                ui.navigate.to("/login")
                return
            ok = sum(1 for result in results if result.get("success"))
            ui.notify(t("grid.saved_count").format(ok=ok, total=len(results)))
            ui.navigate.reload()

        def discard() -> None:
            ui.navigate.reload()

        with ui.row().classes("items-center gap-3"):
            ui.button(t("grid.save"), icon="save", on_click=save).classes("bg-primary text-white")
            ui.button(t("grid.discard"), icon="undo", on_click=discard).props("outline")
            staged_label = ui.label(t("grid.staged_changes").format(count=0)).classes(
                "text-caption"
            )

        grid = ui.aggrid(
            {
                "columnDefs": [
                    {"headerName": t("grid.id"), "field": "_id", "width": 100},
                    {"headerName": t("grid.name"), "field": "_name", "editable": True},
                    {"headerName": t("grid.type"), "field": "_type", "width": 120},
                    {"headerName": t("grid.status"), "field": "_status", "width": 140},
                ],
                "rowData": [row_data(object_, locale) for object_ in objects],
                "rowSelection": {"mode": "multiRow"},
                "pagination": True,
                "paginationPageSize": 50,
            },
            html_columns=[3],
        ).classes("w-full h-96")
        grid.on("cellValueChanged", mark_changed, ["data"])
