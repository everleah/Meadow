"""The configuration area of the bulk-edit wizard (`/products`).

The page builds the wizard dialog and starts the run; which *fields* an operation
needs, which target pickers it opens and which values were typed in is a
self-contained block with its own state. It lives here so the page keeps layout
only — the payload built from it is `bulk_edit.py`, the report it produces is
`bulk_report.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from nicegui import ui

from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.bulk_edit import (
    APPROVAL_STATUS,
    approval_supported,
    build_parameters,
    missing_parameters,
)
from meadow.frontend.bulk_report import read_bulk_report
from meadow.frontend.display import DEFAULT_RELEASE_STATUS, OBJECT_STATUSES, status_options
from meadow.frontend.i18n import t
from meadow.frontend.object_picker import open_object_picker
from meadow.frontend.relation_roles import PRIMARY_CATEGORY_DEF, effective_target_types

#: Review statuses of the workflow state machine, offered as plain codes.
STATUS_OPTIONS = OBJECT_STATUSES
#: Number of 2-second polls a run may stay `pending` before the progress dialog
#: warns that no worker seems to be connected (~20 s).
PENDING_WARN_POLLS = 10


class BulkConfigArea:
    """Renders the wizard's config area for one operation and keeps its values.

    :param client: core client (the target pickers load through it).
    :param token: bearer token of the session.
    :param locale: working locale (picker titles and attribute labels).
    :param attribute_options: own attributes offered as targets (name → label).
    :param relation_options: relation types with a dedicated bulk operation.
    :param relation_definitions: the definition per relation option key.
    :param category_options: categorisations offered for category operations.
    :param taxonomy_types: node types the category picker may target (`None` = any).
    :param channel_labels: channels offered for release/withdraw.
    """

    def __init__(
        self,
        *,
        client: CoreClient,
        token: str,
        locale: str,
        attribute_options: dict[str, str],
        relation_options: dict[str, str],
        relation_definitions: Mapping[str, Mapping[str, object]],
        category_options: dict[str, str],
        taxonomy_types: Sequence[str] | None,
        channel_labels: dict[str, str],
    ) -> None:
        self.client = client
        self.token = token
        self.locale = locale
        self.attribute_options = attribute_options
        self.relation_options = relation_options
        self.relation_definitions = relation_definitions
        self.category_options = category_options
        self.taxonomy_types = taxonomy_types
        self.channel_labels = channel_labels
        self.status_options = status_options()
        #: The field widgets the current operation rendered, by field key.
        self.fields: dict[str, ui.input | ui.select | ui.number | ui.switch] = {}
        #: The object chosen per picker key (`category`/`parent`/`relation_target`).
        self.picked: dict[str, dict] = {}

    def values(self) -> dict[str, Any]:
        """The current value per rendered field (the input of `build_parameters`)."""
        return {key: widget.value for key, widget in self.fields.items()}

    def render(self, container: ui.element, op: str) -> None:
        """Clears the area and renders the configuration of `op` into it."""
        container.clear()
        self.fields.clear()
        with container:
            if op in ("set_attribute", "clear_attribute"):
                select = (
                    ui.select(self.attribute_options, label=t("grid.bulk_attribute"))
                    .props("filled dense")
                    .mark("bulk-field-attribute")
                    .classes("w-full")
                )
                self.fields["attribute"] = select
                if op == "set_attribute":
                    value = (
                        ui.input(label=t("grid.bulk_value"))
                        .props("filled dense")
                        .mark("bulk-field-value")
                        .classes("w-full")
                    )
                    self.fields["value"] = value
            elif op == "change_status":
                select = (
                    ui.select(self.status_options, value=None, label=t("grid.bulk_status_label"))
                    .props("filled dense")
                    .mark("bulk-field-status")
                    .classes("w-full")
                )
                self.fields["status"] = select
            elif op == "send_for_approval":
                select = (
                    ui.select(
                        self.status_options,
                        value=APPROVAL_STATUS,
                        label=t("grid.approval_status"),
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                self.fields["approval_status"] = select
            elif op in ("add_category", "remove_category", "change_parent"):
                field_key = "category" if op != "change_parent" else "parent"
                label_key = "grid.bulk_parent" if op == "change_parent" else "grid.bulk_category"
                target_types = self.taxonomy_types if op != "change_parent" else None

                async def choose_target(field_key=field_key, target_types=target_types):
                    async def on_pick(hit: dict) -> None:
                        self.picked[field_key] = hit
                        label.set_text(f"{hit.get('label')} · {hit.get('type')}")

                    await open_object_picker(
                        client=self.client,
                        token=self.token,
                        locale=self.locale,
                        title=t("form.pick_target"),
                        on_pick=on_pick,
                        target_types=target_types,
                    )

                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    label = ui.label(t("grid.bulk_nothing_picked")).classes(
                        "grow text-caption text-gray-500"
                    )
                    ui.button(icon="account_tree", on_click=choose_target).props(
                        "outline dense"
                    ).tooltip(t("grid.bulk_pick_label").format(label=t(label_key)))
                if op in ("add_category", "remove_category") and len(self.category_options) > 1:
                    # Several categorisations may exist; the primary type is
                    # preselected so the common case stays a single click.
                    category_type = (
                        ui.select(
                            self.category_options,
                            value=(
                                PRIMARY_CATEGORY_DEF
                                if PRIMARY_CATEGORY_DEF in self.category_options
                                else next(iter(self.category_options))
                            ),
                            label=t("grid.bulk_category_type"),
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                    self.fields["category_definition"] = category_type
                    ui.label(t("grid.bulk_category_type_hint")).classes(
                        "text-caption text-gray-400"
                    )
            elif op in ("add_relation", "remove_relation"):
                relation = (
                    ui.select(
                        self.relation_options,
                        value=None,
                        label=t("grid.bulk_relation"),
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                self.fields["relation"] = relation

                async def choose_relation_target():
                    """Picks the target object the relation points to."""
                    definition = self.relation_definitions.get(str(relation.value or "")) or {}

                    async def on_pick(hit: dict) -> None:
                        self.picked["relation_target"] = hit
                        relation_label.set_text(f"{hit.get('label')} · {hit.get('type')}")

                    await open_object_picker(
                        client=self.client,
                        token=self.token,
                        locale=self.locale,
                        title=t("form.pick_target"),
                        on_pick=on_pick,
                        target_types=effective_target_types(definition),
                    )

                def reset_relation_target() -> None:
                    """A different relation type needs a new target object."""
                    self.picked.pop("relation_target", None)
                    relation_label.set_text(t("grid.bulk_nothing_picked"))

                relation.on_value_change(lambda _event: reset_relation_target())
                with ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap"):
                    relation_label = ui.label(t("grid.bulk_nothing_picked")).classes(
                        "grow text-caption text-gray-500"
                    )
                    ui.button(icon="account_tree", on_click=choose_relation_target).props(
                        "outline dense"
                    ).tooltip(
                        t("grid.bulk_pick_label").format(label=t("grid.bulk_relation_target"))
                    )
                ui.label(t("grid.bulk_relation_hint")).classes("text-caption text-gray-400")
            elif op == "delete":
                ui.label(t("grid.bulk_delete_warn")).classes("text-caption text-negative")
            elif op in ("release_for_channel", "withdraw_from_channel"):
                select = (
                    ui.select(self.channel_labels, value=None, label=t("grid.bulk_channel"))
                    .props("filled dense")
                    .classes("w-full")
                )
                self.fields["channel"] = select
                if op == "release_for_channel":
                    status_select = (
                        ui.select(
                            self.status_options,
                            value=DEFAULT_RELEASE_STATUS,
                            label=t("grid.release_status"),
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                    self.fields["release_status"] = status_select
                ui.label(t("grid.bulk_channel_hint")).classes("text-caption text-gray-400")
            if approval_supported(op):
                self.fields["send_for_approval"] = ui.switch(
                    t("grid.bulk_send_for_approval")
                ).tooltip(f"{t('grid.approval_status')}: {APPROVAL_STATUS}")


@dataclass(frozen=True)
class WizardCatalogs:
    """The page's catalogs the wizard renders its configuration from."""

    #: The rows of the current grid view (count of the "filtered" scope).
    rows: Sequence[object]
    #: Own attributes offered as targets (name → label).
    attributes: dict[str, str]
    #: Relation types with a dedicated bulk operation (key → label).
    relation_options: dict[str, str]
    #: The definition per relation option key.
    relation_definitions: Mapping[str, Mapping[str, object]]
    #: Categorisations offered for the category operations (key → label).
    category_options: dict[str, str]
    #: Node types the category picker may target (`None` = any).
    taxonomy_types: Sequence[str] | None
    #: Channels offered for release/withdraw (key → label).
    channel_labels: dict[str, str]


class BulkWizard:
    """The bulk-edit dialog: selection scope, operation, run and report.

    :param client: core client (posts the run, polls it, loads picker data).
    :param token: bearer token of the session.
    :param locale: working locale of the page.
    :param catalogs: the page's catalogs (attributes, relations, categories…).
    :param resolve_ids: how the page resolves a scope to object ids — only the
        page knows the grid page and the loaded rows.
    """

    def __init__(
        self,
        *,
        client: CoreClient,
        token: str,
        locale: str,
        catalogs: WizardCatalogs,
        resolve_ids: Callable[[str], Awaitable[list[str]]],
    ) -> None:
        self.client = client
        self.token = token
        self.locale = locale
        self.catalogs = catalogs
        self.resolve_ids = resolve_ids
        self.selected_ids: set[str] = set()
        self.config = BulkConfigArea(
            client=client,
            token=token,
            locale=locale,
            attribute_options=catalogs.attributes,
            relation_options=catalogs.relation_options,
            relation_definitions=catalogs.relation_definitions,
            category_options=catalogs.category_options,
            taxonomy_types=catalogs.taxonomy_types,
            channel_labels=catalogs.channel_labels,
        )
        self.dialog: ui.dialog | None = None
        self.scope_select: ui.select | None = None
        self.selected_label: ui.label | None = None
        self.op_select: ui.select | None = None
        self.config_area: ui.column | None = None

    def build(self) -> ui.dialog:
        """Builds the dialog; the page calls this once while building its page."""
        with (
            ui.dialog() as dialog,
            ui.card().classes("w-[44rem] max-w-[94vw] max-h-[85vh] overflow-y-auto"),
        ):
            ui.label(t("grid.bulk_title")).classes("text-h6")
            ui.label(t("grid.bulk_selection")).classes("text-caption text-gray-600")
            self.scope_select = (
                ui.select(
                    {
                        "filtered": t("grid.bulk_scope_filtered").format(
                            count=len(self.catalogs.rows)
                        ),
                        "page": t("grid.bulk_scope_page"),
                        "selected": t("grid.bulk_scope_selected"),
                    },
                    value="selected" if self.selected_ids else "filtered",
                )
                .props("filled dense")
                .mark("bulk-scope")
                .classes("w-full")
            )
            self.selected_label = ui.label("").classes("text-caption")
            ui.label(t("grid.bulk_operation")).classes("text-caption text-gray-600")
            self.op_select = (
                ui.select(
                    {
                        "set_attribute": t("grid.op_set_attribute"),
                        "clear_attribute": t("grid.op_clear_attribute"),
                        "change_status": t("grid.op_change_status"),
                        "send_for_approval": t("grid.op_send_for_approval"),
                        "add_category": t("grid.op_add_category"),
                        "remove_category": t("grid.op_remove_category"),
                        "add_relation": t("grid.op_add_relation"),
                        "remove_relation": t("grid.op_remove_relation"),
                        "change_parent": t("grid.op_change_parent"),
                        "release_for_channel": t("grid.op_release_for_channel"),
                        "withdraw_from_channel": t("grid.op_withdraw_from_channel"),
                        "delete": t("grid.op_delete"),
                    },
                    value=None,
                    on_change=self.refresh_config,
                )
                .props("filled dense")
                .mark("bulk-op")
                .classes("w-full")
            )
            ui.label(t("grid.bulk_areas_hint")).classes("text-caption text-gray-400")
            self.config_area = ui.column().classes("w-full mt-2 gap-2")
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=dialog.close).props("flat")
                ui.button(
                    t("grid.bulk_start"), icon="play_arrow", on_click=lambda: self.start()
                ).props("flat").mark("bulk-start")
        self.dialog = dialog
        return dialog

    def open(self) -> None:
        """Opens the dialog (the page's bulk button calls this)."""
        if self.dialog is not None:
            self.dialog.open()

    def set_selection(self, ids: Iterable[str]) -> None:
        """Takes over the grid selection (counter label and default scope)."""
        self.selected_ids = set(ids)
        if self.selected_label is not None:
            self.selected_label.set_text(
                t("grid.bulk_selected").format(count=len(self.selected_ids))
            )
        if self.scope_select is not None:
            self.scope_select.set_value("selected" if self.selected_ids else "filtered")

    async def refresh_config(self, _event: Any = None) -> None:
        """Re-renders the config area for the chosen operation."""
        if self.config_area is not None and self.op_select is not None:
            self.config.render(self.config_area, str(self.op_select.value or ""))

    async def start(self) -> None:
        """Posts the configured operation and follows the run."""
        op = str(self.op_select.value or "") if self.op_select is not None else ""
        if not op:
            ui.notify(t("grid.bulk_op_required"), color="warning")
            return
        scope = str(self.scope_select.value or "filtered") if self.scope_select else "filtered"
        ids = await self.resolve_ids(scope)
        if not ids:
            ui.notify(t("grid.bulk_no_objects"), color="warning")
            return
        parameters = build_parameters(op, self.config.values(), self.config.picked)
        if missing_parameters(parameters):
            ui.notify(t("grid.bulk_config_incomplete"), color="warning")
            return
        try:
            run = await self.client.post_bulk(
                self.token, op=op, object_ids=ids, parameters=parameters
            )
        except CoreError, httpx.HTTPError:  # show a friendly message
            ui.notify(t("grid.bulk_start_failed"), color="negative")
            return
        run_id = str(run.get("id") or "")
        if self.dialog is not None:
            self.dialog.close()
        ui.notify(t("grid.bulk_started"), color="positive")
        if run_id:
            await self.poll(run_id)

    async def poll(self, run_id: str) -> None:
        """Shows the progress dialog and the report of a posted run."""
        with ui.dialog() as progress_dialog, ui.card().classes("w-[32rem]"):
            ui.label(t("grid.bulk_running")).classes("text-subtitle1")
            status_label = ui.label("").classes("text-caption")
            pending_hint = ui.label("").classes("text-caption text-warning")
            ui.linear_progress(value=0.5).classes("w-full")
            with ui.row().classes("w-full justify-end"):
                ui.button(t("grid.close"), on_click=progress_dialog.close).props("flat")
        progress_dialog.open()
        runs: list[dict] = []
        pending_polls = 0
        for _ in range(120):
            try:
                runs = await self.client.list_job_runs(self.token, ids=[run_id], limit=1)
            except CoreError, httpx.HTTPError:  # retry on transient errors
                runs = []
            if not runs:
                break
            status = str(runs[0].get("status") or "")
            status_label.set_text(t("grid.bulk_status").format(status=status))
            # A queued job is only picked up by a running worker; without one it
            # would stay `pending` forever, so say so explicitly.
            pending_polls = pending_polls + 1 if status == "pending" else 0
            if pending_polls == PENDING_WARN_POLLS:
                pending_hint.set_text(t("grid.bulk_pending_hint"))
            if status in ("success", "failed"):
                break
            await asyncio.sleep(2)
        progress_dialog.close()
        if runs and runs[0].get("status") == "success":
            self.show_report(runs[0].get("result") or {})
        else:
            ui.notify(t("grid.bulk_failed"), color="negative")

    def show_report(self, result: Mapping[str, Any]) -> None:
        """Shows the report of a finished run (see `bulk_report.py`)."""
        report = read_bulk_report(result)
        with (
            ui.dialog() as report_dialog,
            ui.card().classes("w-[32rem] max-h-[60vh] overflow-y-auto"),
        ):
            ui.label(t("grid.bulk_report")).classes("text-subtitle1")
            ui.label(
                t("grid.bulk_report_summary").format(
                    processed=report.processed,
                    succeeded=report.succeeded,
                    failed=report.failed,
                )
            ).classes("text-caption")
            if report.is_replace:
                ui.label(
                    t("grid.replace_summary").format(
                        replacements=report.replacements, skipped=report.skipped
                    )
                ).classes("text-caption")
            if report.is_relation:
                ui.label(
                    t("grid.relation_summary").format(
                        relations=report.relations, skipped=report.skipped
                    )
                ).classes("text-caption")
            if report.errors:
                with ui.column().classes("w-full mt-2 gap-1"):
                    for object_id, error in report.errors:
                        ui.label(f"{object_id} · {error}").classes("text-caption text-negative")
            if report.blocked:
                # The data change is saved; only the workflow transition was
                # rejected (e.g. by a `workflow_guard` rule).
                with ui.column().classes("w-full mt-2 gap-1"):
                    ui.label(
                        t("grid.bulk_approval_blocked").format(count=len(report.blocked))
                    ).classes("text-caption text-warning")
                    for object_id, error in report.blocked:
                        ui.label(f"{object_id} · {error}").classes("text-caption text-warning")
            with ui.row().classes("w-full justify-end mt-2"):
                ui.button(t("grid.close"), on_click=report_dialog.close).props("flat")
        report_dialog.open()
