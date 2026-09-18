"""Admin page for categories & relations (admin/superadmin only).

Categories are ordinary objects of a **taxonomy** object type: the built-in
`category` plus every custom type flagged with `taxonomy: true` (e.g. a
classification standard's class hierarchy). This page manages the taxonomy
trees: create, hierarchise (re-parent), edit and delete nodes.
The **assignment** of objects is product-centric (see the product form) and runs
over the category relation types of a type's *usage* ``category``.

The page has **two independent axes**:

- **Taxonomy (node type)** — *which objects* are the nodes of the tree being
  edited (`category`, `grouping`, …). Switching it re-loads the tree of that
  type; new nodes are created with the selected type.
- **Dimension (relation type)** — *which assignments* are counted in the tree and
  used by "show in grid" (the built-in primary categorisation plus custom
  category relation types). The tree itself is shared by all dimensions.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from nicegui import app, ui

from meadow.frontend import config, taxonomy_tree
from meadow.frontend.api import CoreClient, CoreError
from meadow.frontend.display import dimension_options, taxonomy_options, taxonomy_type_names
from meadow.frontend.grid import (
    SEARCH_LIMIT,
    filter_tree_nodes,
    resolve_value,
    status_badge,
    tree_node_from_object,
)
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.layout import PAGE_TIMEOUT, build_shell
from meadow.frontend.lazy_tree import LazyTree
from meadow.frontend.object_picker import OBJECT_ROOT_ID, open_object_picker
from meadow.frontend.relation_roles import (
    PRIMARY_CATEGORY_DEF,
    category_dimension_bindings,
    primary_category_key,
)

ROOT_ID = OBJECT_ROOT_ID


async def _category_objects(
    client: CoreClient, token: str, locale: str, node_types: list[str]
) -> dict[str, dict]:
    """Returns every taxonomy node keyed by id (for the editor/move pickers)."""
    objects: dict[str, dict] = {}
    for node_type in node_types:
        try:
            rows = await client.list_objects(token, object_type=node_type, limit=1000)
        except Exception:  # noqa: BLE001 - degrade to the types that worked
            continue
        objects.update({str(o.get("id")): o for o in rows if isinstance(o, dict) and o.get("id")})
    return objects


async def _category_roots(
    client: CoreClient, token: str, locale: str, definition_id: str, taxonomy: str, offset: int = 0
) -> list[dict]:
    """Returns **one page** of the top-level nodes of a taxonomy.

    ``taxonomy`` is the node type shown (`category`, `etim_class`, …) and
    ``definition_id`` the category relation type whose members are counted
    (`memberCount`). Both filters run in the core: the root carries the nodes of
    *every* taxonomy (with a classification standard installed that is thousands
    of nodes), so filtering client-side would silently truncate the list to the
    first page. The page size is `taxonomy_tree.PAGE_SIZE` — the panel offers a
    "load more" node and a server search instead of one truncated page.
    """
    top = await client.list_object_children(
        token,
        ROOT_ID,
        limit=taxonomy_tree.PAGE_SIZE,
        offset=offset,
        count_members=True,
        definition_id=definition_id,
        scope="taxonomy",
        object_type=taxonomy,
    )
    return [tree_node_from_object(child, locale) for child in top]


@ui.page("/categories", response_timeout=PAGE_TIMEOUT)
async def categories_page() -> None:
    await build_shell(active="/categories")
    locale = current_locale()
    client = CoreClient(config.CORE_URL)
    token = app.storage.user.get("token", "")

    with ui.column().classes("w-full p-6"):
        ui.label(t("categories.title")).classes("text-h4")
        ui.label(t("categories.subtitle")).classes("text-subtitle1")

        # Taxonomy node types: the built-in `category` plus every custom object
        # type flagged `taxonomy: true`. They are the *nodes* of the trees.
        try:
            object_types = await client.list_object_types(token, limit=1000)
        except Exception:  # noqa: BLE001 - degrade to the built-in taxonomy
            object_types = []
        taxonomy_types = taxonomy_type_names(object_types)
        # `all` feeds the node-id lookup (labels of every taxonomy), `key` is the
        # taxonomy currently edited (the tree shows one taxonomy at a time).
        nodes: dict = {"all": taxonomy_types, "key": taxonomy_types[0]}
        # Attribute definitions: candidates for the class features (Stage B). The
        # whole vocabulary in one request — a class may demand an own attribute *or*
        # a feature of the installed standard, and the capped paged list offered
        # nothing but the standard's first 1000 `EF…` rows.
        try:
            attribute_definitions = await client.list_attribute_vocabulary(token)
        except Exception:  # noqa: BLE001 - the feature picker stays empty
            attribute_definitions = []

        try:
            category_roots = await _category_roots(
                client, token, locale, PRIMARY_CATEGORY_DEF, nodes["key"]
            )
        except Exception:  # noqa: BLE001 - show a friendly message on failure
            ui.label(t("categories.load_failed")).classes("text-subtitle1 text-negative")
            return
        category_objects = await _category_objects(client, token, locale, taxonomy_types)

        # Category dimensions: the built-in categorisation plus every custom
        # category relation type. All dimensions share the taxonomy nodes; the
        # dimension drives the counters and the relation used by "show in grid".
        try:
            relation_definitions = await client.list_relation_definitions(token)
        except Exception:  # noqa: BLE001 - degrade to the primary taxonomy only
            relation_definitions = []
        dimensions = dimension_options(
            relation_definitions, locale, primary_label=t("categories.dimension_primary")
        )
        # A dimension with an explicit `target_types` is bound to that taxonomy (the
        # core rejects every other target with `400`), so it decides the shown tree:
        # the taxonomy switcher follows it and is disabled while it is bound. Generic
        # dimensions (empty `target_types`) leave the switcher to the user.
        dimension_bindings = category_dimension_bindings(relation_definitions, taxonomy_types)
        # Taxonomies shown/edited on this page (node types; switcher labels).
        taxonomies = taxonomy_options(object_types, locale)

        # --- state -----------------------------------------------------------
        dimension: dict = {"key": primary_category_key(relation_definitions)}
        #: The taxonomy switcher (built later in this scope; `apply_dimension_taxonomy`
        #: disables it while the chosen dimension is bound to one taxonomy).
        taxonomy_select: ui.select | None = None
        selected_category: dict = {"id": None, "label": "", "type": "", "status": ""}
        selected_tree: dict = {"id": None}
        current_tree: dict = {"tree": None, "nodes": []}
        #: More roots of this taxonomy are unloaded (the tree carries a "load more"
        #: node then) — a classification standard has thousands of them.
        roots_has_more: dict = {"value": taxonomy_tree.has_more(len(category_roots))}
        #: Server-side search hits shown instead of the tree (empty = show the tree).
        tree_override: list[dict] = []
        #: The query the current `tree_override` was loaded for.
        tree_query: dict = {"value": ""}
        #: Ancestor branch of a hit the user opened in the tree (empty = normal tree).
        tree_path: list[dict] = []

        # --- multilingual name editing (product-form style) ------------------
        # The category `name` is a ``{lang: value}`` map. The inline field edits
        # the value for the current UI language (the "main edit language"), and a
        # language icon opens a dialog to edit it in every active data language.
        try:
            languages = await client.list_languages(token)
        except Exception:  # noqa: BLE001 - fall back to the UI languages only
            languages = []
        name_langs = [
            lang
            for lang in languages
            if str(lang.get("code")) != "root" and lang.get("active", True)
        ]
        if not any(str(lang.get("code")) == locale for lang in name_langs):
            name_langs = [{"code": locale, "label": {locale: locale}}] + name_langs

        edit_state: dict[str, Any] = {
            "id": None,
            "name": {},
            "status": "draft",
            "parent": None,
        }
        name_input_ref: dict[str, ui.input | None] = {"el": None}

        name_dialog = ui.dialog()
        with name_dialog, ui.card().classes("w-[34rem] max-w-[90vw]"):
            ui.label(t("categories.name_edit")).classes("text-h6")
            name_inputs: dict[str, ui.input] = {}
            with ui.column().classes("w-full gap-2 mt-2"):
                for lang in name_langs:
                    code = str(lang.get("code"))
                    name_inputs[code] = (
                        ui.input(resolve_value(lang.get("label"), locale) or code)
                        .props("filled dense")
                        .classes("w-full")
                    )
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=name_dialog.close).props("flat")
                ui.button(t("grid.save"), icon="save", on_click=lambda: save_name()).props("flat")

        def open_name() -> None:
            name = dict(edit_state["name"] or {})
            for code, inp in name_inputs.items():
                inp.set_value(str(name.get(code) or ""))
            name_dialog.open()

        def save_name() -> None:
            name: dict[str, str] = {}
            for code, inp in name_inputs.items():
                if inp.value:
                    name[code] = inp.value
            edit_state["name"] = name
            el = name_input_ref.get("el")
            if el is not None:
                el.set_value(str(name.get(locale) or ""))
            name_dialog.close()

        def _set_name_locale(value: str) -> None:
            name = dict(edit_state["name"] or {})
            if value:
                name[locale] = value
            else:
                name.pop(locale, None)
            edit_state["name"] = name

        # --- tree helpers ----------------------------------------------------
        async def load_category_children(node_id: str) -> list[dict]:
            children = await client.list_object_children(
                token,
                node_id,
                limit=100,
                count_members=True,
                definition_id=dimension["key"],
                scope="taxonomy",
                object_type=nodes["key"],
            )
            return [tree_node_from_object(child, locale) for child in children]

        async def select_category(node_id: str | None) -> None:
            """Selects a tree node (also used by the search hit list)."""
            if not node_id:
                selected_category.update({"id": None, "label": "", "type": "", "status": ""})
                await render_detail()
                return
            try:
                obj = await client.get_object(token, node_id)
            except Exception:  # noqa: BLE001 - degrade to a bare selection
                obj = {"id": node_id}
            selected_category.update(
                {
                    "id": str(obj.get("id")),
                    "label": resolve_value((obj.get("attributes") or {}).get("name"), locale)
                    or str(obj.get("type"))
                    or str(obj.get("id")),
                    "type": str(obj.get("type") or ""),
                    "status": str(obj.get("status") or ""),
                }
            )
            await render_detail()

        async def handle_category_select(e) -> None:
            node_id = e.value if hasattr(e, "value") else None
            if node_id == taxonomy_tree.MORE_ID:
                # The trailing node of a paged taxonomy: append the next page.
                await load_more_roots()
                return
            await select_category(node_id)

        def render_tree() -> None:
            if host is None:
                return
            host.clear()
            # A tree is only built when the panel shows the hierarchy; the flat
            # search hit list is rendered as rows (select a hit or open it in the
            # tree).
            tree: LazyTree | None = None
            hit_list = bool(tree_override) and not tree_path
            if tree_path:
                node_list = tree_path
            elif hit_list:
                # Server-side search hits: flat, no hierarchy to load.
                node_list = tree_override
            else:
                roots = category_roots
                if selected_tree["id"]:
                    filtered = [r for r in category_roots if r["id"] == selected_tree["id"]]
                    if filtered:
                        roots = filtered
                node_list = taxonomy_tree.tree_nodes(
                    roots,
                    more_label=(
                        t("grid.taxonomy_more")
                        if roots_has_more["value"] and not selected_tree["id"]
                        else None
                    ),
                )
            with host:
                chain = taxonomy_tree.path_chain(tree_path) if tree_path else []
                if chain:
                    ui.label(t("grid.taxonomy_path").format(label=chain[-1]["label"])).classes(
                        "text-caption text-orange-600"
                    )
                elif hit_list:
                    ui.label(
                        t("grid.taxonomy_results").format(
                            count=len(tree_override), query=tree_query["value"]
                        )
                    ).classes("text-caption text-orange-600")

                async def pick_hit(hit: dict, _: object) -> None:
                    """Picks one search hit; the second argument is NiceGUI's click event."""
                    await select_category(str(hit["id"]))

                async def reveal_hit(hit: dict, _: object) -> None:
                    """Shows one hit in the tree; the second argument is the click event."""
                    await show_in_tree(str(hit["id"]))

                if hit_list:
                    with ui.column().classes("w-full gap-0"):
                        for hit in tree_override:
                            with (
                                ui.row()
                                .classes("items-center gap-1 w-full py-0")
                                .style("flex-wrap: nowrap")
                            ):
                                ui.button(
                                    str(hit["label"]),
                                    on_click=partial(pick_hit, hit),
                                ).props("flat dense no-caps align=left").classes(
                                    "grow justify-start text-left"
                                )
                                ui.button(
                                    icon="account_tree",
                                    on_click=partial(reveal_hit, hit),
                                ).props("flat dense").tooltip(t("grid.taxonomy_in_tree"))
                else:
                    tree = LazyTree(
                        node_list or [],
                        on_select=handle_category_select,
                        load_children=load_category_children,
                        virtual_scroll=True,
                    ).classes("w-full")
                    tree.add_slot(
                        "default-header",
                        '<span :props="props">{{ props.node.label }}'
                        '<small v-if="props.node.type" class="text-caption text-gray-500">'
                        " &middot; {{ props.node.type }}</small>"
                        '<small v-if="props.node.count !== undefined" '
                        'class="text-caption text-gray-500">'
                        " &middot; {{ props.node.count }}</small></span>",
                    )
                    if tree_path:
                        tree.set_path(node_list, [str(node["id"]) for node in chain[:-1]])
                    elif not node_list:
                        ui.label(t("categories.no_categories")).classes(
                            "text-caption text-gray-500"
                        )
            current_tree["tree"] = tree
            current_tree["nodes"] = node_list

        def collapse_all() -> None:
            if current_tree["tree"] is not None:
                current_tree["tree"].collapse()

        async def search_taxonomy(query: str) -> None:
            """Loads the server-side search hits of the shown taxonomy.

            `GET /objects/picker` searches the whole object set, so a node is found
            by its code **or** description even when it sits far beyond the loaded
            page: the full-text index matches whole tokens, `code_prefix=true` adds
            the partial code (`EC0123`). `completeness=false` skips the badge (it
            costs ~0.3 s per hit in the core and the tree shows none).
            """
            current = nodes["key"]
            try:
                hits = await client.picker_search(
                    token,
                    query,
                    types=[current] if current else None,
                    locale=locale,
                    limit=SEARCH_LIMIT,
                    code_prefix=True,
                    completeness=False,
                )
            except Exception:  # noqa: BLE001 - keep the tree on failure
                ui.notify(t("grid.taxonomy_search_failed"), color="negative")
                return
            tree_override[:] = taxonomy_tree.search_nodes(hits)
            tree_path.clear()
            tree_query["value"] = query
            render_tree()

        async def show_in_tree(node_id: str) -> None:
            """Shows a search hit in its hierarchy (`GET .../ancestors`).

            A hit can sit below a class that is far beyond the loaded page (or
            outside the shown taxonomy) — a flat hit list cannot say where it
            belongs. The tree renders the hit's ancestor branch then, expanded down
            to the node; clearing the filter brings the normal tree back.
            """
            try:
                path = await client.list_object_ancestors(token, node_id)
            except Exception:  # noqa: BLE001 - keep the hit list on failure
                ui.notify(t("grid.taxonomy_path_failed"), color="negative")
                return
            nodes_list = [tree_node_from_object(node, locale) for node in path]
            tree_path[:] = taxonomy_tree.path_nodes(nodes_list)
            render_tree()

        async def apply_tree_filter(value: str) -> None:
            """Filters the tree — server-side for a **paged** taxonomy."""
            query = (value or "").strip()
            if taxonomy_tree.uses_server_search(query, paged=roots_has_more["value"]):
                await search_taxonomy(query)
                return
            if tree_override or tree_query["value"] or tree_path:
                tree_override.clear()
                tree_path.clear()
                tree_query["value"] = ""
                render_tree()
                return
            tree = current_tree["tree"]
            if tree is not None:
                tree.set_nodes(filter_tree_nodes(current_tree["nodes"], query))

        async def on_filter_change(event) -> None:
            """The NiceGUI `value_change` callback of the filter input."""
            await apply_tree_filter(str(getattr(event, "value", "") or ""))

        async def load_more_roots() -> None:
            """Appends the next page of taxonomy roots (the "load more" node)."""
            nonlocal category_roots
            page = await _category_roots(
                client, token, locale, dimension["key"], nodes["key"], offset=len(category_roots)
            )
            category_roots = taxonomy_tree.append_page(category_roots, page)
            roots_has_more["value"] = taxonomy_tree.has_more(len(category_roots))
            refresh_tree_options()
            render_tree()

        async def reload_tree() -> None:
            nonlocal category_roots
            try:
                category_roots = await _category_roots(
                    client, token, locale, dimension["key"], nodes["key"]
                )
            except Exception:  # noqa: BLE001 - keep the cached roots on failure
                category_roots = category_roots
            roots_has_more["value"] = taxonomy_tree.has_more(len(category_roots))
            refresh_tree_options()
            render_tree()

        def refresh_tree_options() -> None:
            """Refills the root select (the loaded pages of the shown taxonomy)."""
            tree_select.options = {
                "all": t("grid.all_trees"),
                **{r["id"]: r["label"] for r in category_roots},
            }
            tree_select.update()

        async def change_taxonomy(value: str) -> None:
            """Switches the taxonomy **node type** and re-loads its tree."""
            key = str(value or nodes["all"][0])
            if key == nodes["key"]:
                return
            nodes["key"] = key
            selected_category.update({"id": None, "label": "", "type": "", "status": ""})
            selected_tree["id"] = None
            tree_override.clear()
            tree_path.clear()
            tree_query["value"] = ""
            filter_input.set_value("")
            await reload_tree()
            await render_detail()

        async def change_dimension(value: str) -> None:
            """Switches the counted category relation type and re-renders the tree."""
            key = str(value or PRIMARY_CATEGORY_DEF)
            if key == dimension["key"]:
                return
            dimension["key"] = key
            apply_dimension_taxonomy(key)
            selected_category.update({"id": None, "label": "", "type": "", "status": ""})
            selected_tree["id"] = None
            tree_override.clear()
            tree_path.clear()
            tree_query["value"] = ""
            filter_input.set_value("")
            await reload_tree()
            await render_detail()

        def apply_dimension_taxonomy(key: str) -> None:
            """Follows a bound dimension: its taxonomy becomes the shown tree."""
            bound = dimension_bindings.get(key)
            if taxonomy_select is not None:
                taxonomy_select.set_enabled(not bound)
                taxonomy_select.props(f'hint="{t("grid.taxonomy_bound")}"' if bound else 'hint=""')
                if bound:
                    taxonomy_select.set_value(bound)
                taxonomy_select.update()
            if bound:
                nodes["key"] = bound

        # --- detail panel -----------------------------------------------------
        def grid_url() -> str:
            """Grid link for the selected category in the current dimension.

            The extra categorisation is passed along, so the grid shows (and
            counts) the members of the very same relation type.
            """
            url = f"/products?category={selected_category['id']}"
            if dimension["key"] != PRIMARY_CATEGORY_DEF:
                url += f"&definition_id={dimension['key']}"
            return url

        async def render_detail() -> None:
            detail_host.clear()
            category_id = selected_category["id"]
            if not category_id:
                with detail_host, ui.card().classes("w-full h-[60vh]"):
                    ui.label(t("categories.select_hint")).classes("text-subtitle1 text-gray-500")
                return
            category = category_objects.get(str(category_id)) or {}
            raw_name = (category.get("attributes") or {}).get("name") or {}
            edit_state["id"] = category_id
            edit_state["name"] = dict(raw_name) if isinstance(raw_name, dict) else {}
            edit_state["status"] = str(category.get("status") or "draft")
            with (
                detail_host,
                ui.column().classes("w-full gap-3"),
            ):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label(selected_category["label"]).classes("text-h6 grow")
                    if selected_category.get("status"):
                        ui.html(status_badge(selected_category["status"]))
                ui.label(selected_category["id"]).classes("text-caption text-gray-500")
                with ui.row().classes("w-full mt-2 gap-2"):
                    ui.button(
                        t("categories.new_sub"),
                        icon="add",
                        on_click=lambda: open_create(selected_category["id"]),
                    ).props("flat")
                    ui.button(
                        t("categories.move"),
                        icon="drive_file_move",
                        on_click=lambda: open_move(),
                    ).props("flat")
                    ui.button(
                        t("categories.delete"),
                        icon="delete",
                        on_click=lambda: open_delete(),
                    ).props("flat color=negative")
                with ui.row().classes("items-center gap-2 w-full mt-2"):
                    ui.button(
                        t("categories.view_in_grid"),
                        icon="open_in_new",
                        on_click=lambda: ui.navigate.to(grid_url()),
                    ).props("outline")
                    ui.label(t("categories.view_in_grid_hint")).classes(
                        "text-caption text-gray-500"
                    )
                # Inline edit form (name in the current UI language + multilingual).
                with ui.column().classes("w-full mt-2 gap-2"):
                    ui.label(t("categories.edit")).classes("text-subtitle2")
                    with ui.row().classes("w-full items-center gap-2 mt-1"):
                        name_input = (
                            ui.input(t("categories.name") + f" ({locale.upper()})")
                            .props("filled dense")
                            .classes("grow")
                        )
                        name_input.set_value(str((edit_state["name"] or {}).get(locale) or ""))
                        name_input.on_value_change(lambda e: _set_name_locale(str(e.value)))
                        name_input_ref["el"] = name_input
                        ui.button(icon="translate").props("flat round dense").on_click(
                            open_name
                        ).tooltip(t("form.languages_edit"))
                    status_select = (
                        ui.select(
                            {
                                "draft": "draft",
                                "in_review": "in_review",
                                "published": "published",
                                "archived": "archived",
                            },
                            label=t("categories.status"),
                            value=str(edit_state["status"] or "draft"),
                        )
                        .props("filled dense")
                        .classes("w-full")
                    )
                    status_select.on_value_change(
                        lambda e: edit_state.__setitem__("status", str(e.value))
                    )
                    with ui.row().classes("w-full justify-end mt-2 gap-2"):
                        ui.button(
                            t("categories.save"), icon="save", on_click=lambda: save_inline()
                        ).props("flat")
                # Class features (Stage B): the attributes this class requires or
                # offers. They apply to every object assigned to the class (and to
                # its descendants); see `plan/detail/kategorisierung.md`.
                with ui.column().classes("w-full mt-4 gap-1"):
                    ui.label(t("categories.features")).classes("text-subtitle2")
                    ui.label(t("categories.features_hint")).classes("text-caption text-gray-500")
                    category_id = selected_category["id"]
                    try:
                        features = await client.list_category_attributes(token, category_id)
                    except Exception:  # noqa: BLE001 - degrade to an empty list
                        features = []
                    attached = {str(row.get("attribute")) for row in features}

                    async def attach(value: str | None = None) -> None:
                        name = str(value or "").strip()
                        if not name:
                            ui.notify(t("categories.features_pick"), color="warning")
                            return
                        try:
                            await client.attach_category_attribute(
                                token, category_id, attribute=name
                            )
                        except CoreError as exc:
                            ui.notify(f"{t('categories.feature_failed')}: {exc}", color="negative")
                            return
                        await render_detail()
                        ui.notify(t("categories.feature_added"), color="positive")

                    async def set_required(name: str, value: bool) -> None:
                        try:
                            await client.update_category_attribute(
                                token, category_id, name, required=value
                            )
                        except CoreError as exc:
                            ui.notify(f"{t('categories.feature_failed')}: {exc}", color="negative")
                        await render_detail()

                    async def detach(name: str) -> None:
                        try:
                            await client.detach_category_attribute(token, category_id, name)
                        except CoreError as exc:
                            ui.notify(f"{t('categories.feature_failed')}: {exc}", color="negative")
                            return
                        await render_detail()
                        ui.notify(t("categories.feature_removed"), color="positive")

                    if not features:
                        ui.label(t("categories.features_none")).classes(
                            "text-caption text-gray-400 italic"
                        )
                    for row in features:
                        name = str(row.get("attribute") or "")
                        inherited = bool(row.get("inherited"))
                        with (
                            ui.row().classes("items-center gap-2 w-full").style("flex-wrap: nowrap")
                        ):
                            ui.label(name).classes("grow text-body2")
                            ui.html(
                                '<span class="px-2 py-0.5 rounded text-xs bg-primary text-white">'
                                + t("categories.feature_required")
                                + "</span>"
                                if row.get("required")
                                else '<span class="px-2 py-0.5 rounded text-xs bg-neutral '
                                'text-gray-600">' + t("categories.feature_optional") + "</span>"
                            )
                            if inherited:
                                ui.label(
                                    t("categories.feature_inherited").format(
                                        node=str(row.get("from") or "")
                                    )
                                ).classes("text-caption text-gray-500")
                            else:
                                ui.switch(
                                    value=bool(row.get("required")),
                                    on_change=lambda e, n=name: set_required(n, bool(e.value)),
                                ).props("dense").tooltip(t("categories.feature_required_toggle"))
                                ui.button(icon="close", on_click=lambda n=name: detach(n)).props(
                                    "flat dense round"
                                ).tooltip(t("categories.feature_remove"))
                    available = {
                        str(d.get("name")): str(d.get("name"))
                        for d in attribute_definitions
                        if d.get("name") and str(d.get("name")) not in attached
                    }
                    with ui.row().classes("items-center gap-2 w-full"):
                        feature_select = (
                            ui.select(
                                available,
                                label=t("categories.features_add"),
                            )
                            # A standard's vocabulary alone is 17 377 names: the list
                            # is only usable with the type-to-filter switch. Quasar
                            # keeps the *selected* option as the value (free text
                            # needs `new_value_mode`), so nothing unintended is added.
                            .props("outline dense use-input input-debounce=200")
                            .classes("grow")
                            .set_enabled(bool(available))
                        )
                        ui.button(
                            icon="add",
                            on_click=lambda: attach(feature_select.value),
                        ).props("flat dense round").set_enabled(bool(available))
                    if not available:
                        ui.label(t("categories.features_all_attached")).classes(
                            "text-caption text-gray-400 italic"
                        )

        async def save_inline() -> None:
            category_id = str(edit_state.get("id") or "")
            name = {k: v for k, v in (edit_state.get("name") or {}).items() if v}
            if not name:
                ui.notify(t("categories.name_required"), color="warning")
                return
            try:
                current = await client.get_object(token, category_id)
                attributes = dict(current.get("attributes") or {})
                attributes["name"] = name
                await client.update_object(
                    token,
                    category_id,
                    attributes=attributes,
                    status=str(edit_state.get("status") or "draft"),
                )
            except CoreError as exc:
                ui.notify(f"{t('categories.save_failed')}: {exc}", color="negative")
                return
            category_objects.clear()
            category_objects.update(await _category_objects(client, token, locale, nodes["all"]))
            selected_category["label"] = resolve_value(name, locale) or selected_category["label"]
            await reload_tree()
            await render_detail()
            ui.notify(t("categories.saved"), color="positive")

        # --- create dialog (multilingual name, product-form style) ------------
        create_dialog = ui.dialog()
        with create_dialog, ui.card().classes("w-[36rem] max-w-[92vw]"):
            title_label = ui.label("").classes("text-h6")
            with ui.column().classes("w-full mt-2 gap-2"):
                with ui.row().classes("w-full items-center gap-2"):
                    create_name_input = (
                        ui.input(t("categories.name") + f" ({locale.upper()})")
                        .props("filled dense")
                        .classes("grow")
                    )
                    ui.button(icon="translate").props("flat round dense").on_click(
                        open_name
                    ).tooltip(t("form.languages_edit"))
                create_status_select = (
                    ui.select(
                        {
                            "draft": "draft",
                            "in_review": "in_review",
                            "published": "published",
                            "archived": "archived",
                        },
                        label=t("categories.status"),
                        value="draft",
                    )
                    .props("filled dense")
                    .classes("w-full")
                )
                create_status_select.on_value_change(
                    lambda e: edit_state.__setitem__("status", str(e.value))
                )
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=create_dialog.close).props("flat")
                ui.button(
                    t("categories.save"), icon="save", on_click=lambda: save_category()
                ).props("flat")

        def open_create(parent_id: str | None) -> None:
            edit_state["id"] = None
            edit_state["name"] = {}
            edit_state["status"] = "draft"
            #: Parent of the new node: the selected category (a sub-category) or
            #: the universal root (`None` — the core's default, nothing is sent).
            edit_state["parent"] = parent_id
            title_label.set_text(
                t("categories.create_sub") if parent_id else t("categories.create_root")
            )
            create_name_input.set_value("")
            create_name_input.on_value_change(lambda e: _set_name_locale(str(e.value)))
            create_status_select.set_value("draft")
            name_input_ref["el"] = create_name_input
            create_dialog.open()

        async def save_category() -> None:
            name: dict[str, str] = {k: v for k, v in (edit_state.get("name") or {}).items() if v}
            if not name:
                ui.notify(t("categories.name_required"), color="warning")
                return
            try:
                # The parent is part of the write (`POST /objects`): a sub-category
                # is created **under** the selected node. The core validates the
                # composition type restriction before anything is written, so a
                # rejected parent leaves no orphan at the root behind (the earlier
                # "create at the root, then re-parent" left one behind on failure).
                await client.create_object(
                    token,
                    object_type=nodes["key"],
                    attributes={"name": name},
                    status=str(edit_state.get("status") or "draft"),
                    parent=edit_state.get("parent"),
                )
            except CoreError as exc:
                ui.notify(f"{t('categories.save_failed')}: {exc}", color="negative")
                return
            create_dialog.close()
            category_objects.clear()
            category_objects.update(await _category_objects(client, token, locale, nodes["all"]))
            await reload_tree()
            ui.notify(t("categories.saved"), color="positive")

        # --- move dialog ------------------------------------------------------
        # The new parent is picked with the shared object picker (search +
        # structure tree, restricted to `category` and excluding the moved node).
        move_dialog = ui.dialog()
        with move_dialog, ui.card().classes("w-[32rem] max-w-[92vw]"):
            ui.label(t("categories.move_title")).classes("text-h6")
            ui.label(t("categories.move_hint")).classes("text-caption text-gray-600")
            move_target = ui.label(t("grid.bulk_nothing_picked")).classes(
                "text-caption text-gray-500"
            )
            picked: dict = {}

            async def pick_parent() -> None:
                category_id = selected_category["id"]

                async def on_pick(hit: dict) -> None:
                    picked["id"] = hit["id"]
                    move_target.set_text(f"{hit.get('label')} · {hit.get('type')}")

                await open_object_picker(
                    client=client,
                    token=token,
                    locale=locale,
                    title=t("categories.parent"),
                    on_pick=on_pick,
                    # A node keeps its own taxonomy: only siblings/ancestors of
                    # the same node type are valid parents.
                    target_types=[selected_category.get("type") or nodes["key"]],
                    exclude_ids=[category_id or ""],
                )

            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=move_dialog.close).props("flat")
                ui.button(
                    t("form.pick_in_structure"), icon="account_tree", on_click=pick_parent
                ).props("flat")
                ui.button(
                    t("categories.move"), icon="drive_file_move", on_click=lambda: do_move()
                ).props("flat")

        async def open_move() -> None:
            if not selected_category["id"]:
                return
            picked.clear()
            move_target.set_text(t("grid.bulk_nothing_picked"))
            move_dialog.open()

        async def do_move() -> None:
            category_id = selected_category["id"]
            parent = picked.get("id")
            if not category_id or not parent:
                ui.notify(t("categories.parent_required"), color="warning")
                return
            try:
                await client.reparent_object(token, category_id, parent)
            except CoreError as exc:
                ui.notify(f"{t('categories.move_failed')}: {exc}", color="negative")
                return
            move_dialog.close()
            await reload_tree()
            ui.notify(t("categories.moved"), color="positive")

        # --- delete confirm ----------------------------------------------------
        delete_dialog = ui.dialog()
        with delete_dialog, ui.card().classes("w-[32rem] max-w-[92vw]"):
            ui.label(t("categories.delete_confirm")).classes("text-subtitle1 text-negative")
            ui.label(t("categories.delete_warn")).classes("text-caption text-gray-600")
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                ui.button(t("grid.close"), on_click=delete_dialog.close).props("flat")
                ui.button(
                    t("categories.delete"), icon="delete", on_click=lambda: do_delete()
                ).props("flat color=negative")

        async def open_delete() -> None:
            if not selected_category["id"]:
                return
            delete_dialog.open()

        async def do_delete() -> None:
            category_id = selected_category["id"]
            if not category_id:
                return
            try:
                await client.delete_object(token, category_id)
            except CoreError as exc:
                ui.notify(f"{t('categories.delete_failed')}: {exc}", color="negative")
                return
            delete_dialog.close()
            selected_category.update({"id": None, "label": "", "type": "", "status": ""})
            category_objects.clear()
            category_objects.update(await _category_objects(client, token, locale, nodes["all"]))
            await reload_tree()
            await render_detail()
            ui.notify(t("categories.deleted"), color="positive")

        # --- layout: splitter with tree + detail -----------------------------
        with ui.splitter(value=28, limits=(0, 60)).classes(
            "w-full h-[calc(100vh-210px)]"
        ) as splitter:
            with splitter.before, ui.card().classes("w-full h-full p-0"):
                with (
                    ui.row()
                    .classes("items-center gap-2 w-full px-2 py-1")
                    .style("flex-wrap: nowrap")
                ):
                    taxonomy_select = (
                        ui.select(
                            taxonomies,
                            value=nodes["key"],
                            label=t("categories.taxonomy"),
                        )
                        .props("outline dense")
                        .classes("grow")
                    )
                    ui.button(icon="help_outline").props("flat dense shrink-0").tooltip(
                        t("categories.taxonomy_hint")
                    )
                    taxonomy_select.on_value_change(lambda e: change_taxonomy(e.value))
                with ui.row().classes("items-center gap-2 w-full p-2").style("flex-wrap: nowrap"):
                    ui.button(icon="add", on_click=lambda: open_create(None)).props(
                        "flat dense"
                    ).tooltip(t("categories.new_root"))
                    tree_select = (
                        ui.select(
                            {
                                "all": t("grid.all_trees"),
                                **{r["id"]: r["label"] for r in category_roots},
                            },
                            value="all",
                        )
                        .props("outline dense")
                        .classes("grow")
                    )

                    def on_tree_select(value: str) -> None:
                        selected_tree["id"] = None if value in (None, "all") else value
                        render_tree()

                    tree_select.on_value_change(lambda e: on_tree_select(e.value))
                with ui.row().classes("items-center gap-2 w-full px-2").style("flex-wrap: nowrap"):
                    dimension_select = (
                        ui.select(
                            dimensions,
                            value=dimension["key"],
                            label=t("categories.dimension"),
                        )
                        .props("outline dense")
                        .classes("grow")
                    )
                    ui.button(icon="help_outline").props("flat dense shrink-0").tooltip(
                        t("categories.dimension_hint")
                    )
                    dimension_select.on_value_change(lambda e: change_dimension(e.value))
                # A dimension may be bound to one taxonomy: reflect that in the
                # switcher (disabled + hint) as soon as it exists.
                apply_dimension_taxonomy(dimension["key"])
                with ui.row().classes("items-center gap-2 w-full px-2").style("flex-wrap: nowrap"):
                    filter_input = ui.input(t("grid.filter")).props("outline dense").classes("grow")
                    ui.button(icon="unfold_less", on_click=collapse_all).props(
                        "flat dense shrink-0"
                    ).tooltip(t("grid.collapse_all"))
                host = ui.column().classes("w-full grow").style("min-height: 0; overflow-y: auto")
                filter_input.on_value_change(on_filter_change)
                render_tree()
            with splitter.after, ui.column().classes("w-full h-full pl-3"):
                detail_host = ui.column().classes("w-full h-full")
                await render_detail()
