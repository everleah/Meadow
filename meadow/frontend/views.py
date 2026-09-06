"""Page views: home/dashboard and placeholder pages for later Phase 7 steps."""

from __future__ import annotations

from nicegui import app, ui

from meadow.frontend import (
    attribute_definitions,  # noqa: F401  (registers the /attribute-definitions page)
    categories,  # noqa: F401  (registers the /categories page)
    config,
    connectors,  # noqa: F401  (registers the /connectors page)
    form,  # noqa: F401  (registers the product form page)
    grid,  # noqa: F401  (registers the /products page)
    groups,  # noqa: F401  (registers the /groups page)
    jobs,  # noqa: F401  (registers the /jobs page)
    languages,  # noqa: F401  (registers the /languages page)
    logs,  # noqa: F401  (registers the /system-logs page)
    rules,  # noqa: F401  (registers the /rules page)
    settings,  # noqa: F401  (registers the /system-settings page)
    users,  # noqa: F401  (registers the /users page)
)
from meadow.frontend.api import CoreClient
from meadow.frontend.grid import status_badge
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import NAV_ITEMS, build_shell

#: Routes that have a real page and must be skipped by the placeholder
#: registration.
_IMPLEMENTED_ROUTES = {
    "/products",
    "/attribute-definitions",
    "/categories",
    "/connectors",
    "/jobs",
    "/users",
    "/groups",
    "/languages",
    "/rules",
    "/system-settings",
    "/system-logs",
}


@ui.page("/", response_timeout=15)
async def home() -> None:
    """Dashboard: work overview (objects, completeness, recent activity/jobs)."""
    await build_shell(active="/")
    locale = current_locale()
    client = CoreClient(config.CORE_URL)
    token = app.storage.user.get("token", "")
    with ui.column().classes("w-full p-6"):
        ui.label(t("nav.dashboard")).classes("text-h4")
        ui.label(t("dashboard.subtitle")).classes("text-subtitle1")

        try:
            stats = await client.get_object_stats(token, locale=locale)
        except Exception:  # noqa: BLE001 - degrade to an empty overview
            stats = {}
        try:
            logs = await client.list_system_logs(token, limit=10)
        except Exception:  # noqa: BLE001 - degrade
            logs = []
        try:
            job_runs = await client.list_job_runs(token, limit=5)
        except Exception:  # noqa: BLE001 - degrade
            job_runs = []

        counts_by_type: dict[str, int] = stats.get("by_type") or {}
        counts_by_status: dict[str, int] = stats.get("by_status") or {}
        incomplete_by_type: dict[str, int] = stats.get("incomplete_by_type") or {}

        # Objects by type (compact pills).
        with ui.card().classes("w-full mt-4"):
            ui.label(t("dashboard.objects")).classes("text-subtitle1")
            with ui.row().classes("gap-2 w-full mt-2 flex-wrap"):
                if not counts_by_type:
                    ui.label(t("grid.no_objects")).classes("text-caption text-gray-500")
                else:
                    for otype in sorted(counts_by_type):
                        with ui.row().classes("items-center gap-1 px-2 py-0.5 rounded bg-neutral"):
                            ui.label(otype).classes("text-caption font-medium")
                            ui.label(str(counts_by_type[otype])).classes(
                                "text-caption text-gray-500"
                            )

        # Completeness shortcuts (clickable).
        with ui.card().classes("w-full mt-4"):
            ui.label(t("dashboard.completeness")).classes("text-subtitle1")
            with ui.row().classes("gap-4 w-full mt-2 flex-wrap"):
                for otype in sorted(incomplete_by_type):
                    count = incomplete_by_type[otype]
                    if count <= 0:
                        continue
                    with (
                        ui.button(
                            on_click=lambda t=otype: ui.navigate.to(
                                f"/products?incomplete=1&type={t}"
                            )
                        )
                        .props("flat")
                        .classes("p-3 rounded bg-orange-1 min-w-[6rem]")
                    ):
                        label = t("dashboard.incomplete").format(type=otype)
                        ui.html(
                            '<div class="flex flex-col items-center gap-1">'
                            f'<span class="text-h4 text-orange-9">{count}</span>'
                            f'<span class="text-caption text-gray-600 text-center">{label}</span>'
                            "</div>"
                        )
                if not incomplete_by_type:
                    ui.label(t("dashboard.no_incomplete")).classes("text-caption text-gray-500")

        # Status breakdown.
        with ui.card().classes("w-full mt-4"):
            ui.label(t("dashboard.status")).classes("text-subtitle1")
            with ui.row().classes("gap-2 w-full mt-2 flex-wrap"):
                for status in sorted(counts_by_status):
                    with ui.column().classes("items-center gap-1"):
                        ui.html(status_badge(status))
                        ui.label(str(counts_by_status[status])).classes(
                            "text-caption text-gray-500"
                        )

        # Recent activity.
        with ui.card().classes("w-full mt-4"):
            ui.label(t("dashboard.recent_activity")).classes("text-subtitle1")
            if not logs:
                ui.label(t("dashboard.no_activity")).classes("text-caption text-gray-500")
            with ui.column().classes("w-full gap-1 mt-2"):
                for log in logs:
                    with ui.row().classes("items-center gap-2 w-full"):
                        level = str(log.get("level") or "")
                        if level == "error":
                            level_cls = "bg-negative text-white"
                        elif level == "warn":
                            level_cls = "bg-orange text-white"
                        else:
                            level_cls = "bg-secondary text-white"
                        ui.html(
                            '<span class="px-2 py-0.5 rounded text-xs w-20 text-center '
                            f'{level_cls}">{level}</span>'
                        ).classes("shrink-0")
                        ui.label(str(log.get("created_at") or "")).classes(
                            "text-caption text-gray-400 w-44 shrink-0"
                        )
                        ui.label(str(log.get("message") or "")).classes("flex-1 text-caption")

        # Recent jobs.
        with ui.card().classes("w-full mt-4"):
            ui.label(t("dashboard.recent_jobs")).classes("text-subtitle1")
            if not job_runs:
                ui.label(t("dashboard.no_jobs")).classes("text-caption text-gray-500")
            with ui.column().classes("w-full gap-1 mt-2"):
                for run in job_runs:
                    with ui.row().classes("items-center gap-2 w-full"):
                        ui.html(status_badge(str(run.get("status") or ""))).classes("w-24 shrink-0")
                        def_label = str(run.get("job_definition_id") or "").split(":")[-1]
                        ui.label(def_label).classes("flex-1 text-caption")
                        ui.label(str(run.get("created_at") or "")).classes(
                            "text-caption text-gray-400"
                        )


def register_placeholders() -> None:
    """Registers placeholder pages for routes built in later steps."""
    for key, path, _admin_only in NAV_ITEMS:
        if path == "/" or path in _IMPLEMENTED_ROUTES:
            continue

        @ui.page(path)
        async def page(path: str = path, key: str = key) -> None:
            await build_shell(active=path)
            with ui.column().classes("w-full p-6"):
                ui.label(t(key)).classes("text-h4")
                ui.label(t("placeholder.under_construction")).classes("text-subtitle1")


def register_pages() -> None:
    """Registers all frontend views."""
    register_placeholders()
