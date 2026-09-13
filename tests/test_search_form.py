"""Tests for the schema-driven search form of saved searches (`search_form.py`)."""

from __future__ import annotations

import pytest

from meadow.frontend import search_form
from meadow.frontend.i18n import t

CATALOG: dict = {
    "groups": [
        {"key": "system", "label_key": "search_form.group_system"},
        {"key": "attribute", "label_key": "search_form.group_attributes"},
    ],
    "operators": [
        {
            "type": "eq",
            "label_key": "search_form.op_eq",
            "group": "compare",
            "arity": 1,
            "multi": False,
        },
        {
            "type": "ne",
            "label_key": "search_form.op_ne",
            "group": "compare",
            "arity": 1,
            "multi": False,
        },
        {
            "type": "gt",
            "label_key": "search_form.op_gt",
            "group": "compare",
            "arity": 1,
            "multi": False,
        },
        {
            "type": "gte",
            "label_key": "search_form.op_gte",
            "group": "compare",
            "arity": 1,
            "multi": False,
        },
        {
            "type": "lt",
            "label_key": "search_form.op_lt",
            "group": "compare",
            "arity": 1,
            "multi": False,
        },
        {
            "type": "lte",
            "label_key": "search_form.op_lte",
            "group": "compare",
            "arity": 1,
            "multi": False,
        },
        {
            "type": "between",
            "label_key": "search_form.op_between",
            "group": "compare",
            "arity": 2,
            "multi": False,
        },
        {
            "type": "contains",
            "label_key": "search_form.op_contains",
            "group": "text",
            "arity": 1,
            "multi": False,
        },
        {
            "type": "not_empty",
            "label_key": "search_form.op_not_empty",
            "group": "empty",
            "arity": 0,
            "multi": False,
        },
        {
            "type": "is_null",
            "label_key": "search_form.op_is_null",
            "group": "empty",
            "arity": 0,
            "multi": False,
        },
        {
            "type": "in",
            "label_key": "search_form.op_in",
            "group": "list",
            "arity": 1,
            "multi": True,
        },
    ],
    "fields": [
        {
            "key": "type",
            "group": "system",
            "label_key": "search_form.field_type",
            "value_type": "string",
            "widget": "select",
            "options": [{"value": "product", "label": {"en": "Product"}}],
            "operators": ["eq", "ne", "in"],
            "sortable": True,
        },
        {
            "key": "status",
            "group": "system",
            "label_key": "search_form.field_status",
            "value_type": "string",
            "widget": "select",
            "options": [{"value": "published", "label_key": "status.published"}],
            "operators": ["eq", "ne", "in"],
            "sortable": True,
        },
        {
            "key": "created_at",
            "group": "system",
            "label_key": "search_form.field_created_at",
            "value_type": "datetime",
            "widget": "date",
            "operators": ["gte", "lte", "between", "is_null"],
            "sortable": True,
        },
        {
            "key": "attributes.name",
            "group": "attribute",
            "label": {"en": "Name", "de": "Name"},
            "value_type": "string",
            "widget": "text",
            "multilingual": True,
            "languages": ["de", "en"],
            "operators": ["eq", "ne", "contains", "not_empty", "is_null"],
            "sortable": True,
        },
        {
            "key": "attributes.price",
            "group": "attribute",
            "label": {"en": "Price"},
            "value_type": "number",
            "widget": "number",
            "operators": ["eq", "ne", "lt", "lte", "between", "is_null"],
            "sortable": True,
        },
        {
            "key": "attributes.active",
            "group": "attribute",
            "label": {"en": "Active"},
            "value_type": "boolean",
            "widget": "switch",
            "operators": ["eq", "ne"],
            "sortable": False,
        },
        {
            # Reference data of an imported classification standard: offered only
            # behind the form's switch, but always filterable (see `reference_data`).
            "key": "attributes.EF000040",
            "group": "attribute",
            "label": {"en": "Width"},
            "value_type": "number",
            "widget": "number",
            "operators": ["eq", "ne", "lt", "lte", "between", "is_null"],
            "sortable": True,
            "reference_data": True,
        },
        {
            # One bound of a `range` attribute: a plain number field that carries
            # the bound so the label can be composed.
            "key": "attributes.busbar_width.from",
            "group": "attribute",
            "label": {"en": "Busbar width", "de": "Sammelschienenbreite"},
            "value_type": "number",
            "widget": "number",
            "operators": ["eq", "ne", "gte", "lte", "between", "is_null"],
            "sortable": True,
            "range_bound": "from",
        },
    ],
    "languages": [
        {"code": "de", "label": {"en": "German"}},
        {"code": "en", "label": {"en": "English"}},
    ],
    "references": [
        {
            "type": "category_member",
            "label": {"en": "Category member"},
            "role": "category",
            "target_types": ["category"],
        }
    ],
    "limits": {"min": 1, "max": 10000, "default": 1000, "max_depth": 4, "max_conditions": 50},
}


def _leaf(field: str, operator: str, value: object) -> dict:
    return {"field": field, "operator": operator, "value": value}


def test_field_lookup_resolves_multilingual_variants() -> None:
    assert search_form.field_by_key(CATALOG, "attributes.name")["key"] == "attributes.name"  # type: ignore
    assert search_form.field_by_key(CATALOG, "attributes.name.de")["key"] == "attributes.name"  # type: ignore
    assert search_form.field_by_key(CATALOG, "attributes.name.zz") is None
    assert search_form.field_by_key(CATALOG, "attributes.nope") is None
    assert search_form.split_language(CATALOG, "attributes.name.de") == "de"
    assert search_form.split_language(CATALOG, "attributes.name") == ""
    assert search_form.with_language("attributes.name", "en") == "attributes.name.en"
    assert search_form.with_language("attributes.name", "") == "attributes.name"


def test_json_helpers_round_trip() -> None:
    assert search_form.parse_json(None) is None
    assert search_form.parse_json("{oops") is None
    assert search_form.parse_json("[1, 2]") is None
    query = {"where": {"op": "and", "conditions": []}, "limit": 500}
    compact = search_form.compact_json(query)
    assert " " not in compact
    assert search_form.parse_json(compact) == query
    assert search_form.compact_json(None) == "{}"


def test_widget_kind_follows_field_and_operator() -> None:
    name = search_form.field_by_key(CATALOG, "attributes.name")
    price = search_form.field_by_key(CATALOG, "attributes.price")
    active = search_form.field_by_key(CATALOG, "attributes.active")
    type_field = search_form.field_by_key(CATALOG, "type")
    table = search_form.operators_by_name(CATALOG)

    assert search_form.widget_kind(name, table["contains"]) == "text"
    assert search_form.widget_kind(price, table["between"]) == "range-number"
    assert search_form.widget_kind(name, table["not_empty"]) == "none"
    assert search_form.widget_kind(type_field, table["in"]) == "multiselect"
    assert search_form.widget_kind(type_field, table["eq"]) == "select"
    assert search_form.widget_kind(active, table["eq"]) == "switch"
    assert search_form.needs_value(table["not_empty"]) is False
    assert search_form.is_multi(table["in"]) is True


def test_operator_choices_are_limited_to_the_field() -> None:
    assert set(search_form.operator_choices(CATALOG, "attributes.active", locale="en")) == {
        "eq",
        "ne",
    }
    assert search_form.default_operator(CATALOG, "attributes.price") == "eq"
    assert search_form.operator_names(CATALOG, "attributes.price")[0] == "eq"
    # Unknown fields fall back to the full table so nothing is hidden.
    assert set(search_form.operator_choices(CATALOG, "attributes.gone", locale="en")) == set(
        search_form.operators_by_name(CATALOG)
    )


def test_default_state_and_roundtrip() -> None:
    state = search_form.default_state(CATALOG)
    assert state["limit"] == 1000
    assert state["where"]["op"] == "and"
    assert state["where"]["conditions"][0]["field"] == "type"

    query = {
        "where": {
            "op": "and",
            "conditions": [
                _leaf("type", "eq", "product"),
                {
                    "op": "or",
                    "conditions": [
                        _leaf("status", "eq", "published"),
                        _leaf("attributes.price", "between", [10, 50]),
                    ],
                },
            ],
        },
        "fulltext": {"query": "Edelstahl"},
        "references": [{"type": "category_member", "out": "object:cat", "direction": "incoming"}],
        "sort": [{"field": "attributes.name.de", "order": "desc"}],
        "limit": 500,
    }
    state = search_form.state_from_query(query, CATALOG)
    rebuilt = search_form.query_from_state(state, CATALOG, query)
    assert rebuilt["where"] == query["where"]
    assert rebuilt["fulltext"]["query"] == "Edelstahl"
    assert rebuilt["references"] == query["references"]
    assert rebuilt["sort"] == query["sort"]
    assert rebuilt["limit"] == 500


def test_query_from_state_keeps_parts_the_form_does_not_show() -> None:
    baseline = {
        "fulltext": {"query": "alt", "fields": ["attributes.description"], "language": "de"},
        "custom_key": {"a": 1},
        "limit": 100,
    }
    state = search_form.state_from_query(baseline, CATALOG)
    query = search_form.query_from_state(state, CATALOG, baseline)
    assert query["custom_key"] == {"a": 1}
    assert query["fulltext"]["fields"] == ["attributes.description"]
    assert query["fulltext"]["language"] == "de"
    assert search_form.not_shown_parts(baseline) == [
        "query.custom_key",
        "query.fulltext.fields",
        "query.fulltext.language",
    ]

    # An empty form must not invent an empty fulltext restriction.
    empty = search_form.query_from_state(search_form.default_state(CATALOG), CATALOG, {})
    assert "fulltext" not in empty


def test_prune_drops_empty_rows_and_groups() -> None:
    tree = {
        "op": "and",
        "conditions": [
            {"field": "", "operator": "", "value": None},
            {"op": "or", "conditions": [{"field": "", "operator": "", "value": None}]},
            _leaf("type", "eq", "product"),
        ],
    }
    pruned = search_form.prune(tree)
    assert pruned is not None
    assert pruned["conditions"] == [_leaf("type", "eq", "product")]
    assert search_form.prune({"op": "and", "conditions": []}) is None


def test_validate_state_reports_paths() -> None:
    state = {
        "where": {
            "op": "and",
            "conditions": [
                _leaf("attributes.gone", "eq", "x"),
                _leaf("attributes.active", "lt", 1),
                _leaf("attributes.price", "between", [10]),
                _leaf("attributes.name", "contains", 5),
                _leaf("attributes.price", "is_null", "x"),
            ],
        },
        "references": [{"type": "", "out": ""}],
        "sort": [{"field": "attributes.active", "order": "up"}],
        "start": 0,
        "limit": 99999,
    }
    errors = search_form.validate_state(state, CATALOG)
    joined = " | ".join(errors)
    assert "query.where.conditions[0].field: unknown field `attributes.gone`" in joined
    assert "query.where.conditions[1].operator: `lt` is not allowed" in joined
    assert "query.where.conditions[2].value: must be a list of two values" in joined
    assert "query.where.conditions[3].value: must be a string" in joined
    assert "query.where.conditions[4].value: `is_null` does not take a value" in joined
    assert "query.references[0].type: is required" in joined
    assert "query.references[0].out: is required" in joined
    assert "query.sort[0].field: `attributes.active` cannot be sorted" in joined
    assert "query.sort[0].order: must be asc or desc" in joined
    assert "query.limit: must be between 1 and 10000" in joined


def test_incomplete_rows_are_reported_and_untouched_rows_dropped() -> None:
    assert search_form.new_reference(CATALOG)["out"] == ""
    state = search_form.default_state(CATALOG)
    state["references"] = [
        {"type": "category_member", "out": "", "label": "", "direction": "incoming"}
    ]
    query = search_form.query_from_state(state, CATALOG, {})
    assert query["references"] == [{"type": "category_member", "out": "", "direction": "incoming"}]
    assert "query.references[0].out: is required" in search_form.validate_state(state, CATALOG)

    state["references"] = [{"type": "", "out": "", "label": "", "direction": "incoming"}]
    assert "references" not in search_form.query_from_state(state, CATALOG, {})

    state["sort"] = [{"field": "", "order": "asc"}]
    assert "sort" not in search_form.query_from_state(state, CATALOG, {})


def test_clean_tree_drops_values_of_valueless_operators() -> None:
    tree = {
        "op": "and",
        "conditions": [
            {"field": "attributes.name", "operator": "not_empty", "value": None},
            {"field": "attributes.name", "operator": "eq", "value": None},
            {"field": "attributes.price", "operator": "between", "value": [1, 2]},
        ],
    }
    cleaned = search_form.clean_tree(tree, CATALOG)
    assert "value" not in cleaned["conditions"][0]  # type: ignore
    assert cleaned["conditions"][1]["value"] is None  # type: ignore
    assert cleaned["conditions"][2]["value"] == [1, 2]  # type: ignore

    state = search_form.default_state(CATALOG)
    state["where"] = tree
    query = search_form.query_from_state(state, CATALOG, {})
    assert "value" not in query["where"]["conditions"][0]


def test_object_filter_keeps_only_what_the_object_list_uses() -> None:
    query = {
        "where": {"op": "and", "conditions": []},
        "references": [{"type": "category_member", "out": "object:cat"}],
        "fulltext": {"query": "Test"},
        "sort": [{"field": "type", "order": "asc"}],
        "start": 10,
        "limit": 500,
        "custom": 1,
    }
    filtered = search_form.object_filter(query)
    assert set(filtered) == {"where", "references", "fulltext", "custom"}
    assert search_form.object_filter({}) == {}


def test_validate_state_accepts_a_valid_tree() -> None:
    state = {
        "where": {
            "op": "or",
            "conditions": [
                _leaf("type", "eq", "product"),
                _leaf("attributes.price", "between", [10, 50]),
                _leaf("attributes.name.de", "not_empty", None),
                _leaf("created_at", "gte", "2026-01-01"),
            ],
        },
        "references": [{"type": "category_member", "out": "object:cat"}],
        "sort": [{"field": "attributes.name", "order": "asc"}],
        "start": 0,
        "limit": 100,
    }
    assert search_form.validate_state(state, CATALOG) == []


def test_validate_state_rejects_too_deep_and_empty_groups() -> None:
    deep: dict = {"op": "and", "conditions": [_leaf("type", "eq", "product")]}
    for _ in range(4):
        deep = {"op": "and", "conditions": [deep]}
    errors = search_form.validate_state({"where": deep, "limit": 100}, CATALOG)
    assert any("at most 4 levels deep" in error for error in errors)

    empty = search_form.validate_state(
        {"where": {"op": "and", "conditions": []}, "limit": 100}, CATALOG
    )
    assert any("at least one condition" in error for error in empty)


def test_summary_text_counts_conditions_and_groups() -> None:
    query = {
        "where": {
            "op": "or",
            "conditions": [
                _leaf("type", "eq", "product"),
                {
                    "op": "and",
                    "conditions": [_leaf("status", "eq", "draft")],
                },
            ],
        },
        "fulltext": {"query": "Edelstahl"},
        "limit": 500,
    }
    text = search_form.summary_text(query, CATALOG, locale="en")
    assert (
        t(
            "search_form.summary_conditions",
            locale="en",
            count=2,
            operand=t("search_form.op_or", locale="en"),
        )
        in text
    )
    assert t("search_form.summary_groups_one", locale="en", count=1) in text
    assert t("search_form.summary_fulltext", locale="en", text="Edelstahl") in text
    assert t("search_form.summary_limit", locale="en", limit=500) in text
    assert search_form.summary_text({}, CATALOG, locale="en") == t(
        "search_form.summary_empty", locale="en"
    )


def test_summary_text_uses_singular_counts() -> None:
    query = {
        "where": {"op": "and", "conditions": [_leaf("type", "eq", "product")]},
        "references": [{"reference_type": "category_member", "out": "object:cat_1"}],
        "sort": [{"field": "updated_at", "order": "desc"}],
    }
    text = search_form.summary_text(query, CATALOG, locale="en")
    assert t("search_form.summary_conditions_one", locale="en", count=1, operand="and") in text
    assert t("search_form.summary_references_one", locale="en", count=1) in text
    assert t("search_form.summary_sort_one", locale="en", count=1) in text
    assert "1 conditions" not in text
    assert "1 references" not in text
    assert "1 sorts" not in text


def test_language_and_field_choices_fall_back_to_unknown() -> None:
    unknown = t("search_form.field_unknown", locale="en")
    choices = search_form.field_choices(CATALOG, "attributes.removed", locale="en")
    assert choices["attributes.removed"].endswith(f"({unknown})")
    # Attribute labels are data: an empty label map falls back to the name.
    blank = {"key": "attributes.net_price", "label": {}, "value_type": "number"}
    assert search_form.field_label(blank, locale="en") == "net_price"
    assert search_form.field_label(CATALOG["fields"][3], locale="en") == "Name"
    languages = search_form.language_choices(CATALOG, locale="en")
    assert search_form.BASE_LANGUAGE in languages
    assert {"de", "en"} <= set(languages)
    assert search_form.sortable_choices(CATALOG, "attributes.gone", locale="en")[
        "attributes.gone"
    ].endswith(f"({unknown})")


def test_reference_data_is_offered_only_behind_the_switch() -> None:
    """The standard's vocabulary stays out of the pickers until asked for."""
    feature = "attributes.EF000040"
    assert feature not in search_form.field_choices(CATALOG, locale="en")
    assert feature in search_form.field_choices(CATALOG, locale="en", include_reference=True)
    assert feature in search_form.field_choices(CATALOG, feature, locale="en")
    assert feature not in search_form.sortable_choices(CATALOG, locale="en")
    assert feature in search_form.sortable_choices(CATALOG, locale="en", include_reference=True)
    assert feature in search_form.sortable_choices(CATALOG, feature, locale="en")
    # The own attributes are unaffected either way.
    assert "attributes.price" in search_form.field_choices(CATALOG, locale="en")
    assert "attributes.price" in search_form.sortable_choices(CATALOG, locale="en")


def test_a_range_bound_is_labelled_and_typed_as_a_number() -> None:
    """`attributes.<name>.from` is a number field whose label names the bound."""
    low = search_form.field_by_key(CATALOG, "attributes.busbar_width.from")
    assert low is not None
    assert search_form.value_type_of(CATALOG, "attributes.busbar_width.from") == "number"
    assert search_form.split_language(CATALOG, "attributes.busbar_width.from") == ""
    assert search_form.field_label(low, locale="en") == "Busbar width (from)"
    assert search_form.field_label(low, locale="de") == "Sammelschienenbreite (von)"
    between = search_form.operator_of(CATALOG, "between")
    assert search_form.widget_kind(low, between) == "range-number"
    # Without a label the bound is stripped from the name, not glued onto it.
    bare = {"key": "attributes.x.from", "label": {}, "range_bound": "from"}
    assert search_form.field_label(bare, locale="en") == "x (from)"


@pytest.mark.parametrize("locale", ["en", "de", "fr"])
def test_i18n_keys_of_the_catalog_and_form_exist(locale: str) -> None:
    keys = [
        *(
            f"search_form.op_{name}"
            for name in (
                "eq",
                "ne",
                "gt",
                "gte",
                "lt",
                "lte",
                "between",
                "not_between",
                "contains",
                "not_contains",
                "starts_with",
                "ends_with",
                "in",
                "not_in",
                "not_empty",
                "is_null",
            )
        ),
        "search_form.group_system",
        "search_form.group_attributes",
        "search_form.field_type",
        "search_form.field_status",
        "search_form.field_id",
        "search_form.field_created_at",
        "search_form.field_updated_at",
        "search_form.field_source",
        "search_form.tab_form",
        "search_form.tab_json",
        "search_form.group_op",
        "search_form.op_and",
        "search_form.op_or",
        "search_form.add_condition",
        "search_form.add_group",
        "search_form.too_many_conditions",
        "search_form.condition_field",
        "search_form.condition_language",
        "search_form.condition_operator",
        "search_form.condition_value",
        "search_form.value_from",
        "search_form.value_to",
        "search_form.no_value",
        "search_form.language_base",
        "search_form.field_unknown",
        "search_form.bound_from",
        "search_form.bound_to",
        "search_form.section_conditions",
        "search_form.section_fulltext",
        "search_form.section_references",
        "search_form.section_sort",
        "search_form.section_limits",
        "search_form.show_reference",
        "search_form.show_reference_hint",
        "search_form.fulltext",
        "search_form.fulltext_hint",
        "search_form.limit",
        "search_form.start",
        "search_form.add_reference",
        "search_form.reference_type",
        "search_form.reference_none",
        "search_form.reference_pick",
        "search_form.reference_direction",
        "search_form.reference_incoming",
        "search_form.reference_outgoing",
        "search_form.add_sort",
        "search_form.sort_field",
        "search_form.sort_order",
        "search_form.sort_asc",
        "search_form.sort_desc",
        "search_form.preview",
        "search_form.preview_count",
        "search_form.preview_failed",
        "search_form.preview_invalid",
        "search_form.not_shown",
        "search_form.summary_conditions",
        "search_form.summary_groups",
        "search_form.summary_fulltext",
        "search_form.summary_references",
        "search_form.summary_sort",
        "search_form.summary_limit",
        "search_form.summary_empty",
        "saved_searches.col_summary",
        "saved_searches.field_query",
        "saved_searches.query_hint",
        "grid.advanced_open",
        "grid.advanced_title",
        "grid.advanced_hint",
        "grid.advanced_active",
        "grid.advanced_apply",
        "grid.advanced_clear",
        "grid.advanced_load",
        "grid.advanced_save",
        "grid.advanced_save_button",
        "grid.advanced_saved",
        "grid.advanced_save_name_required",
        "grid.advanced_save_failed",
        "grid.advanced_failed",
        "status.draft",
        "status.in_review",
        "status.published",
        "status.archived",
    ]
    for key in keys:
        assert t(key, locale=locale) != key, (locale, key)
