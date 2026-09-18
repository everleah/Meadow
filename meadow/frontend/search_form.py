"""Schema-driven search form for saved searches (AND/OR condition tree).

The core describes what can be filtered (`GET /search-fields`: fields with value
type, widget, allowed operators, enum options and languages; the operator table
with `arity`/`multi`; relation definitions) and validates the query on write.
This module turns that description into a tree editor and back into the query
DSL of `saved_search` objects (`where`/`fulltext`/`references`/`sort`/`start`/
`limit`):

* **Pure helpers** (unit-tested, no NiceGUI): state/DSL mapping, operator and
  widget selection, validation (same messages as the core), summary text and the
  list of parts the form does not show.
* **`SearchForm`** renders the group/condition tree plus full-text, references,
  sort and limits, and exposes `read_state()` / `set_state()`.

The JSON expert tab stays available in the page; this module is the guided path
(see `plan/detail/suche-formular.md`).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterable
from functools import partial
from typing import Any

from nicegui import ui

from meadow.frontend.display import resolve_value
from meadow.frontend.i18n import current_locale, t
from meadow.frontend.reference_data import is_reference

#: Value of the "no language chosen" option of a multilingual field.
BASE_LANGUAGE = ""

#: Sections a `SearchForm` can render (in display order).
ALL_SECTIONS = ("conditions", "fulltext", "references", "sort", "limits")

#: Keys of the query DSL (everything else is passed through untouched).
KNOWN_KEYS = ("where", "fulltext", "references", "sort", "start", "limit")

#: Keys the object-list filter (`GET /objects?where=`) evaluates.
OBJECT_LIST_KEYS = ("where", "references", "fulltext")

#: Fallback nesting depth when the catalog is empty.
DEFAULT_MAX_DEPTH = 4
#: Fallback condition limit when the catalog is empty.
DEFAULT_MAX_CONDITIONS = 50
#: Fallback result limit when the catalog is empty.
DEFAULT_LIMIT = 1000
#: Result-limit bounds when the catalog is empty.
DEFAULT_LIMIT_BOUNDS = (1, 10_000)


# ---------------------------------------------------------------------------
# Catalog access
# ---------------------------------------------------------------------------
def fields_of(catalog: dict | None) -> list[dict]:
    """All filterable fields of the catalog."""
    return list((catalog or {}).get("fields") or [])


def limits_of(catalog: dict | None) -> dict:
    """The declared limits (min/max/default/max_depth/max_conditions)."""
    return dict((catalog or {}).get("limits") or {})


def max_depth(catalog: dict | None) -> int:
    """Maximum nesting depth of the condition tree (root group = 1)."""
    return int(limits_of(catalog).get("max_depth") or DEFAULT_MAX_DEPTH)


def max_conditions(catalog: dict | None) -> int:
    """Maximum number of conditions (leaves) in one query."""
    return int(limits_of(catalog).get("max_conditions") or DEFAULT_MAX_CONDITIONS)


def limit_bounds(catalog: dict | None) -> tuple[int, int]:
    """Allowed range of the result limit."""
    limits = limits_of(catalog)
    return (
        int(limits.get("min") or DEFAULT_LIMIT_BOUNDS[0]),
        int(limits.get("max") or DEFAULT_LIMIT_BOUNDS[1]),
    )


def default_limit(catalog: dict | None) -> int:
    """Default result limit of a new query."""
    return int(limits_of(catalog).get("default") or DEFAULT_LIMIT)


def operators_by_name(catalog: dict | None) -> dict[str, dict]:
    """The operator table keyed by operator name."""
    return {
        str(operator.get("type")): operator
        for operator in (catalog or {}).get("operators") or []
        if operator.get("type")
    }


def operator_of(catalog: dict | None, name: str | None) -> dict | None:
    """The declaration of one operator (or ``None``)."""
    return operators_by_name(catalog).get(str(name or ""))


def operator_arity(operator: dict | None) -> int:
    """Number of values an operator takes (0, 1 or 2)."""
    return int((operator or {}).get("arity") or 0)


def is_multi(operator: dict | None) -> bool:
    """Whether the operator takes a list of values (`in`/`not_in`)."""
    return bool((operator or {}).get("multi"))


def needs_value(operator: dict | None) -> bool:
    """Whether the operator takes a value at all (`not_empty` does not)."""
    return operator_arity(operator) > 0


def field_by_key(catalog: dict | None, key: str | None) -> dict | None:
    """Resolves a field key, including multilingual variants.

    ``attributes.name.de`` resolves to the base field ``attributes.name`` when
    ``de`` is one of its declared languages.
    """
    wanted = str(key or "")
    if not wanted:
        return None
    for field in fields_of(catalog):
        if str(field.get("key")) == wanted:
            return field
    base, _, language = wanted.rpartition(".")
    if not base or not language:
        return None
    for field in fields_of(catalog):
        if str(field.get("key")) != base or not field.get("multilingual"):
            continue
        if language in [str(code) for code in field.get("languages") or []]:
            return field
    return None


def split_language(catalog: dict | None, key: str | None) -> str:
    """The language suffix of a field key (``""`` for the base field)."""
    wanted = str(key or "")
    field = field_by_key(catalog, wanted)
    if field is None or str(field.get("key")) == wanted:
        return ""
    return wanted.rsplit(".", 1)[-1]


def with_language(base_key: str, language: str | None) -> str:
    """Appends a language to a base field key (empty language = base field)."""
    if not language:
        return base_key
    return f"{base_key}.{language}"


def field_label(field: dict, locale: str | None = None) -> str:
    """Display label of a field (i18n key for system fields, data label else).

    Attribute labels are data: when they carry no value for the locale (or no
    text at all) the attribute name is used, so nothing shows up empty. One bound
    of a `range` attribute shares the attribute's label, so the translated bound
    is appended (`range_bound`, `plan/detail/suche-formular.md`).
    """
    label_key = field.get("label_key")
    if label_key:
        return t(str(label_key), locale=locale)
    label = field.get("label")
    text = ""
    if isinstance(label, dict):
        text = resolve_value(label, locale or current_locale())
    name = str(field.get("key") or "").removeprefix("attributes.")
    bound = str(field.get("range_bound") or "")
    if bound and name.endswith(f".{bound}"):
        name = name[: -(len(bound) + 1)]
    base = text or name
    if not bound:
        return base
    return f"{base} ({t(f'search_form.bound_{bound}', locale=locale)})"


def field_choices(
    catalog: dict | None,
    extra: str | None = None,
    *,
    locale: str | None = None,
    include_reference: bool = False,
) -> dict[str, str]:
    """Select options for the field picker, grouped by catalog section.

    ``extra`` keeps an unknown/removed field selectable so an existing condition
    is never silently rewritten to another field. Reference data of a
    classification standard (an imported ETIM bundle brings hundreds of fields)
    stays out until the form's switch asks for it — the core keeps such fields
    filterable, only the *offered* list changes; `extra` wins over the filter so
    the field a condition already uses stays selectable.
    """
    labels = {
        str(group.get("key")): t(str(group.get("label_key")), locale=locale)
        for group in (catalog or {}).get("groups") or []
    }
    choices: dict[str, str] = {}
    for field in fields_of(catalog):
        key = str(field.get("key"))
        if is_reference(field) and not include_reference and key != extra:
            continue
        section = labels.get(str(field.get("group")), str(field.get("group")))
        choices[key] = f"{section} · {field_label(field, locale)}"
    if extra and extra not in choices:
        unknown = t("search_form.field_unknown", locale=locale)
        choices[extra] = f"{extra} ({unknown})"
    return choices


def sortable_choices(
    catalog: dict | None,
    extra: str | None = None,
    *,
    locale: str | None = None,
    include_reference: bool = False,
) -> dict[str, str]:
    """Select options for the sort field picker (sortable fields only).

    Same reference-data rule as `field_choices`.
    """
    choices = {
        str(field.get("key")): field_label(field, locale)
        for field in fields_of(catalog)
        if field.get("sortable")
        and (include_reference or not is_reference(field) or str(field.get("key")) == extra)
    }
    if extra and extra not in choices:
        unknown = t("search_form.field_unknown", locale=locale)
        choices[extra] = f"{extra} ({unknown})"
    return choices


def language_choices(catalog: dict | None, *, locale: str | None = None) -> dict[str, str]:
    """Select options for the language of a multilingual field."""
    choices: dict[str, str] = {BASE_LANGUAGE: t("search_form.language_base", locale=locale)}
    for language in (catalog or {}).get("languages") or []:
        code = str(language.get("code") or "")
        if not code:
            continue
        label = resolve_value(language.get("label"), locale or current_locale()) or code
        choices[code] = f"{code} · {label}"
    return choices


def operator_names(catalog: dict | None, field_key: str | None) -> list[str]:
    """Allowed operator names of a field, in catalog order (label free)."""
    field = field_by_key(catalog, field_key)
    if field is None:
        return list(operators_by_name(catalog))
    return [str(name) for name in field.get("operators") or []]


def operator_choices(
    catalog: dict | None, field_key: str | None, *, locale: str | None = None
) -> dict[str, str]:
    """Allowed operators of a field as select options."""
    table = operators_by_name(catalog)
    return {
        name: t(str(table[name].get("label_key")), locale=locale)
        for name in operator_names(catalog, field_key)
        if name in table
    }


def default_operator(catalog: dict | None, field_key: str | None) -> str:
    """The first allowed operator of a field (``eq`` when unknown)."""
    names = operator_names(catalog, field_key)
    if "eq" in names:
        return "eq"
    return names[0] if names else "eq"


def value_choices(
    catalog: dict | None, field_key: str | None, *, locale: str | None = None
) -> dict[str, str] | None:
    """Select options of an enum/status field (``None`` when free text)."""
    field = field_by_key(catalog, field_key)
    if field is None or not field.get("options"):
        return None
    choices: dict[str, str] = {}
    for option in field.get("options") or []:
        value = str(option.get("value") or "")
        if not value:
            continue
        label_key = option.get("label_key")
        if label_key:
            choices[value] = t(str(label_key), locale=locale)
        else:
            label = resolve_value(option.get("label"), locale or current_locale())
            choices[value] = label or value
    return choices or None


def widget_kind(field: dict | None, operator: dict | None) -> str:
    """Which value widget the UI must render for this field/operator pair.

    A field that declares options (`type`, `status`, enums) always gets a select,
    whatever its value type says; otherwise the value type decides.
    """
    if not needs_value(operator):
        return "none"
    if is_multi(operator):
        return "multiselect"
    value_type = str((field or {}).get("value_type") or "string")
    base = {
        "number": "number",
        "boolean": "switch",
        "date": "date",
        "datetime": "date",
        "enum": "select",
        "url": "text",
    }.get(value_type, "select" if (field or {}).get("options") else "text")
    if operator_arity(operator) == 2:
        return f"range-{base}"
    return base


def value_type_of(catalog: dict | None, field_key: str | None) -> str:
    """Value type of a field (`string` when unknown)."""
    field = field_by_key(catalog, field_key)
    return str((field or {}).get("value_type") or "string")


# ---------------------------------------------------------------------------
# Building blocks of the form state
# ---------------------------------------------------------------------------
def new_condition(catalog: dict | None) -> dict:
    """A new, empty condition (first known field, its default operator)."""
    field = fields_of(catalog)
    first = str(field[0].get("key")) if field else ""
    return {
        "field": first,
        "operator": default_operator(catalog, first) if first else "",
        "value": None,
    }


def new_group(op: str = "and") -> dict:
    """A new group with one condition."""
    return {"op": op, "conditions": []}


def new_reference(catalog: dict | None) -> dict:
    """A new reference restriction (first relation type)."""
    relations = (catalog or {}).get("references") or []
    return {
        "type": str(relations[0].get("type")) if relations else "",
        "out": "",
        "label": "",
        "direction": "incoming",
    }


def new_sort(catalog: dict | None) -> dict:
    """A new sort clause (first sortable field)."""
    choices = sortable_choices(catalog)
    return {"field": next(iter(choices), ""), "order": "asc"}


def is_group(node: dict) -> bool:
    """Whether a tree node is a group."""
    return isinstance(node.get("conditions"), list)


def count_conditions(node: dict | None) -> int:
    """Number of conditions (leaves) in a (sub)tree."""
    if not node:
        return 0
    if not is_group(node):
        return 1
    return sum(count_conditions(child) for child in node.get("conditions") or [])


def count_groups(node: dict | None) -> int:
    """Number of groups in a (sub)tree (the root counts as one)."""
    if not node or not is_group(node):
        return 0
    return 1 + sum(count_groups(child) for child in node.get("conditions") or [])


def is_empty_condition(node: dict) -> bool:
    """Whether a condition has no field, no operator and no value."""
    if is_group(node):
        return False
    value = node.get("value")
    return (
        not str(node.get("field") or "").strip()
        and not str(node.get("operator") or "").strip()
        and (value is None or value == "" or value == [])
    )


def prune(node: dict | None) -> dict | None:
    """Drops empty conditions and groups (``None`` when nothing is left)."""
    if not node:
        return None
    if not is_group(node):
        return None if is_empty_condition(node) else node
    children: list[dict] = []
    for child in node.get("conditions") or []:
        kept = prune(child)
        if kept is not None:
            children.append(kept)
    if not children:
        return None
    return {"op": str(node.get("op") or "and"), "conditions": children}


def clean_tree(node: dict | None, catalog: dict | None) -> dict | None:
    """Drops the `value` of operators that do not take one (`not_empty`).

    `eq null`/`ne null` keep their `null` — that is the "has no value" spelling
    of the DSL.
    """
    if not node:
        return None
    if is_group(node):
        return {
            "op": str(node.get("op") or "and"),
            "conditions": [
                cleaned
                for child in node.get("conditions") or []
                if (cleaned := clean_tree(child, catalog)) is not None
            ],
        }
    cleaned = copy.deepcopy(node)
    operator = operator_of(catalog, str(cleaned.get("operator") or ""))
    if operator is not None and not needs_value(operator):
        cleaned.pop("value", None)
    return cleaned


def object_filter(query: dict | None) -> dict:
    """Keeps only the parts `GET /objects?where=` evaluates.

    The grid pages and sorts itself, so `sort`/`start`/`limit` are not part of the
    filter; unknown keys are passed through untouched.
    """
    query = query if isinstance(query, dict) else {}
    return {
        key: value
        for key, value in query.items()
        if key in OBJECT_LIST_KEYS or key not in KNOWN_KEYS
    }


# ---------------------------------------------------------------------------
# State <-> DSL
# ---------------------------------------------------------------------------
def parse_json(text: str | None) -> dict | None:
    """Parses a query document (JSON tab, `?where=`); `None` when not an object."""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def compact_json(query: dict | None) -> str:
    """Serializes a query for a URL/`?where=` parameter (stable, no spaces)."""
    return json.dumps(query or {}, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def default_state(catalog: dict | None) -> dict:
    """The state of a new search: one empty AND-group with an object-type row."""
    field = fields_of(catalog)
    first = str(field[0].get("key")) if field else ""
    conditions = []
    if first:
        conditions.append(
            {
                "field": first,
                "operator": default_operator(catalog, first),
                "value": None,
            }
        )
    return {
        "where": {"op": "and", "conditions": conditions},
        "fulltext": "",
        "references": [],
        "sort": [],
        "start": 0,
        "limit": default_limit(catalog),
    }


def state_from_query(query: dict | None, catalog: dict | None) -> dict:
    """Turns a stored query into editable form state (never drops parts)."""
    query = query if isinstance(query, dict) else {}
    where = query.get("where")
    state: dict[str, Any] = {
        "where": copy.deepcopy(where) if isinstance(where, dict) else new_group(),
        "fulltext": str((query.get("fulltext") or {}).get("query") or ""),
        "references": [],
        "sort": [],
        "start": int(query.get("start") or 0),
        "limit": int(query.get("limit") or default_limit(catalog)),
    }
    for reference in query.get("references") or []:
        if not isinstance(reference, dict):
            continue
        reference_type = str(reference.get("type") or "")
        state["references"].append(
            {
                "type": reference_type,
                "out": str(reference.get("out") or ""),
                "label": str(reference.get("label") or reference.get("out") or ""),
                "direction": str(reference.get("direction") or "incoming"),
            }
        )
    for sort in query.get("sort") or []:
        if not isinstance(sort, dict):
            continue
        state["sort"].append(
            {"field": str(sort.get("field") or ""), "order": str(sort.get("order") or "asc")}
        )
    return state


def query_from_state(state: dict, catalog: dict | None, baseline: dict | None = None) -> dict:
    """Turns form state into the query DSL.

    ``baseline`` is the last stored/JSON-edited query: parts the form does not
    show (unknown top-level keys, `fulltext.fields`/`language`) are preserved so
    switching to the form never silently loses them.
    """
    baseline = baseline if isinstance(baseline, dict) else {}
    query: dict = {}
    for key, value in baseline.items():
        if key not in {"where", "fulltext", "references", "sort", "start", "limit"}:
            query[key] = copy.deepcopy(value)

    where = clean_tree(prune(state.get("where")), catalog)
    if where:
        query["where"] = where

    text = str(state.get("fulltext") or "").strip()
    given = baseline.get("fulltext")
    previous_fulltext = given if isinstance(given, dict) else {}
    if text:
        fulltext = dict(previous_fulltext)
        fulltext["query"] = text
        query["fulltext"] = fulltext
    elif previous_fulltext.get("fields") or previous_fulltext.get("language"):
        query["fulltext"] = copy.deepcopy(previous_fulltext)

    # Partially filled rows stay in the query: they are visible in the form and
    # validation names what is missing (only untouched rows are dropped).
    references = [
        {
            "type": str(reference.get("type") or ""),
            "out": str(reference.get("out") or ""),
            "direction": str(reference.get("direction") or "incoming"),
        }
        for reference in state.get("references") or []
        if str(reference.get("type") or "").strip() or str(reference.get("out") or "").strip()
    ]
    if references:
        query["references"] = references

    sort = [
        {"field": str(item.get("field") or ""), "order": str(item.get("order") or "asc")}
        for item in state.get("sort") or []
        if str(item.get("field") or "").strip()
    ]
    if sort:
        query["sort"] = sort

    if int(state.get("start") or 0) > 0:
        query["start"] = int(state.get("start") or 0)
    query["limit"] = int(state.get("limit") or default_limit(catalog))
    return query


def not_shown_parts(query: dict | None) -> list[str]:
    """Query parts the form does not display (shown as a hint, never removed)."""
    query = query if isinstance(query, dict) else {}
    parts: list[str] = []
    for key in query:
        if key not in {"where", "fulltext", "references", "sort", "start", "limit"}:
            parts.append(f"query.{key}")
    fulltext = query.get("fulltext")
    if isinstance(fulltext, dict):
        if fulltext.get("fields"):
            parts.append("query.fulltext.fields")
        if fulltext.get("language"):
            parts.append("query.fulltext.language")
    return sorted(parts)


# ---------------------------------------------------------------------------
# Validation (mirrors the core; the core stays authoritative)
# ---------------------------------------------------------------------------
def validate_state(state: dict, catalog: dict | None) -> list[str]:
    """Validates the form state, naming the path of every problem.

    Messages match the core's wording so the UI never shows two dialects.
    """
    errors: list[str] = []
    where = state.get("where")
    if isinstance(where, dict):
        counter = [0]
        _check_node(where, "query.where", 1, catalog, errors, counter)
        if counter[0] > max_conditions(catalog):
            _push_once(
                errors, f"query.where: at most {max_conditions(catalog)} conditions are allowed"
            )
    for index, reference in enumerate(state.get("references") or []):
        path = f"query.references[{index}]"
        if not str(reference.get("type") or "").strip():
            _push_once(errors, f"{path}.type: is required")
        if not str(reference.get("out") or "").strip():
            _push_once(errors, f"{path}.out: is required")
    for index, sort in enumerate(state.get("sort") or []):
        path = f"query.sort[{index}]"
        field_key = str(sort.get("field") or "").strip()
        if not field_key:
            _push_once(errors, f"{path}.field: is required")
        else:
            field = field_by_key(catalog, field_key)
            if field is not None and not field.get("sortable"):
                _push_once(errors, f"{path}.field: `{field_key}` cannot be sorted")
        if str(sort.get("order") or "") not in ("asc", "desc"):
            _push_once(errors, f"{path}.order: must be asc or desc")
    low, high = limit_bounds(catalog)
    limit = int(state.get("limit") or 0)
    if limit < low or limit > high:
        _push_once(errors, f"query.limit: must be between {low} and {high}")
    return errors


def _check_node(
    node: dict,
    path: str,
    depth: int,
    catalog: dict | None,
    errors: list[str],
    counter: list[int],
) -> None:
    if not is_group(node):
        counter[0] += 1
        _check_condition(node, path, catalog, errors)
        return
    if node.get("field") or node.get("operator"):
        _push_once(errors, f"{path}: a group must not carry `field` or `operator`")
    if str(node.get("op") or "and") not in ("and", "or"):
        _push_once(errors, f"{path}.op: must be and or or")
    children = [child for child in node.get("conditions") or [] if not _blank_node(child)]
    if not children:
        _push_once(errors, f"{path}: a group needs at least one condition")
        return
    if depth >= max_depth(catalog):
        _push_once(
            errors,
            f"{path}: groups may be nested at most {max_depth(catalog)} levels deep",
        )
        return
    for index, child in enumerate(children):
        _check_node(
            child,
            f"{path}.conditions[{index}]",
            depth + 1,
            catalog,
            errors,
            counter,
        )


def _blank_node(node: dict) -> bool:
    """An untouched row (no field, no operator, no value) is ignored."""
    return not is_group(node) and is_empty_condition(node)


def _check_condition(node: dict, path: str, catalog: dict | None, errors: list[str]) -> None:
    if node.get("op"):
        _push_once(errors, f"{path}: a condition must not carry `op`")
    field_key = str(node.get("field") or "").strip()
    if not field_key:
        _push_once(errors, f"{path}.field: is required")
        return
    operator_name = str(node.get("operator") or "").strip()
    if not operator_name:
        _push_once(errors, f"{path}.operator: is required")
        return
    field = field_by_key(catalog, field_key)
    if field is None:
        _push_once(errors, f"{path}.field: unknown field `{field_key}`")
        return
    table = operators_by_name(catalog)
    operator = table.get(operator_name)
    if operator is None:
        _push_once(errors, f"{path}.operator: unknown operator `{operator_name}`")
        return
    allowed = [str(name) for name in field.get("operators") or []]
    if operator_name not in allowed:
        _push_once(
            errors,
            f"{path}.operator: `{operator_name}` is not allowed for `{field_key}` "
            f"(use {', '.join(allowed)})",
        )
        return
    _check_value(node, operator, field, path, errors)


def _check_value(node: dict, operator: dict, field: dict, path: str, errors: list[str]) -> None:
    value = node.get("value")
    if not needs_value(operator):
        if value not in (None, "", []):
            _push_once(errors, f"{path}.value: `{operator['type']}` does not take a value")
        return
    missing = value is None or value == "" or value == []
    if missing:
        if operator["type"] in ("eq", "ne") and value is None:
            return  # `eq null` means "has no value"
        _push_once(errors, f"{path}.value: is required for `{operator['type']}`")
        return
    if is_multi(operator):
        if not isinstance(value, list):
            _push_once(errors, f"{path}.value: must be a list")
            return
        for item in value:
            _check_scalar(item, field, path, errors)
        return
    if operator_arity(operator) == 2:
        if not isinstance(value, list) or len(value) != 2:
            _push_once(errors, f"{path}.value: must be a list of two values")
            return
        for item in value:
            _check_scalar(item, field, path, errors)
        return
    _check_scalar(value, field, path, errors)


def _check_scalar(value: Any, field: dict, path: str, errors: list[str]) -> None:
    value_type = str(field.get("value_type") or "string")
    ok = {
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
    }.get(value_type, lambda item: isinstance(item, str) and item != "")
    if not ok(value):
        hint = {
            "number": "a number",
            "boolean": "a boolean",
            "date": "an ISO date string",
            "datetime": "an ISO date string",
            "enum": "an option code",
            "url": "a URL",
        }.get(value_type, "a string")
        _push_once(errors, f"{path}.value: must be {hint}")


def _push_once(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def _count_text(key: str, count: int, *, locale: str | None = None, **extra: object) -> str:
    """Translates a count with a singular variant when the count is exactly one."""
    return t(f"{key}_one" if count == 1 else key, locale=locale, count=count, **extra)


def summary_text(
    query: dict | None, catalog: dict | None = None, *, locale: str | None = None
) -> str:
    """One-line summary of a query for the saved-search list."""
    query = query if isinstance(query, dict) else {}
    parts: list[str] = []
    where = query.get("where")
    if isinstance(where, dict) and where.get("conditions"):
        conditions = count_conditions(where)
        groups = max(count_groups(where) - 1, 0)
        operand = t(
            "search_form.op_or" if str(where.get("op")) == "or" else "search_form.op_and",
            locale=locale,
        )
        text = _count_text(
            "search_form.summary_conditions", conditions, locale=locale, operand=operand
        )
        if groups:
            text += " · " + _count_text("search_form.summary_groups", groups, locale=locale)
        parts.append(text)
    fulltext = str((query.get("fulltext") or {}).get("query") or "")
    if fulltext:
        parts.append(t("search_form.summary_fulltext", locale=locale, text=fulltext))
    references = query.get("references") or []
    if references:
        parts.append(_count_text("search_form.summary_references", len(references), locale=locale))
    sorts = query.get("sort") or []
    if sorts:
        parts.append(_count_text("search_form.summary_sort", len(sorts), locale=locale))
    if query.get("limit"):
        parts.append(t("search_form.summary_limit", locale=locale, limit=int(query["limit"])))
    return " · ".join(parts) if parts else t("search_form.summary_empty", locale=locale)


# ---------------------------------------------------------------------------
# UI: condition tree
# ---------------------------------------------------------------------------
class SearchForm:
    """Handle of a rendered search form (tree editor + the flat sections)."""

    def __init__(
        self,
        catalog: dict,
        *,
        on_change: Callable[[], None] | None = None,
        client: Any = None,
        token: str = "",
        sections: Iterable[str] | None = None,
    ) -> None:
        self.catalog = catalog
        self._on_change = on_change or (lambda: None)
        self._client = client
        self._token = token
        self.sections = set(sections) if sections else set(ALL_SECTIONS)
        #: Whether the standard vocabulary is offered in the pickers. The catalog
        #: always carries the reference fields (a stored query must keep
        #: validating) — only what the form *offers* changes.
        self.show_reference = False
        self._root: _GroupEditor | None = None
        self._fulltext: Any = None
        self._limit: Any = None
        self._start: Any = None
        self._references: _ReferenceSection | None = None
        self._sort: _SortSection | None = None
        if "conditions" in self.sections:
            with (
                ui.expansion(t("search_form.section_conditions"), value=True).classes("w-full"),
                ui.column().classes("w-full gap-2 p-2"),
            ):
                self._render_reference_switch()
                self._root = _GroupEditor(self, new_group(), depth=1)
        if "fulltext" in self.sections:
            with (
                ui.expansion(t("search_form.section_fulltext")).classes("w-full"),
                ui.column().classes("w-full gap-1 p-2"),
            ):
                self._fulltext = (
                    ui.input(label=t("search_form.fulltext"), on_change=lambda _: self.changed())
                    .props("filled dense clearable")
                    .classes("w-full")
                    .tooltip(t("search_form.fulltext_hint"))
                )
        if "references" in self.sections:
            with (
                ui.expansion(t("search_form.section_references")).classes("w-full"),
                ui.column().classes("w-full gap-1 p-2"),
            ):
                self._references = _ReferenceSection(self)
        if "sort" in self.sections:
            with (
                ui.expansion(t("search_form.section_sort")).classes("w-full"),
                ui.column().classes("w-full gap-1 p-2"),
            ):
                self._sort = _SortSection(self)
        if "limits" in self.sections:
            with (
                ui.expansion(t("search_form.section_limits"), value=True).classes("w-full"),
                ui.row().classes("w-full items-center gap-4 p-2"),
            ):
                self._limit = (
                    ui.number(
                        label=t("search_form.limit"),
                        min=limit_bounds(catalog)[0],
                        max=limit_bounds(catalog)[1],
                        step=1,
                        value=default_limit(catalog),
                        on_change=lambda _: self.changed(),
                    )
                    .props("filled dense")
                    .classes("w-40")
                )
                self._start = (
                    ui.number(
                        label=t("search_form.start"),
                        min=0,
                        step=1,
                        value=0,
                        on_change=lambda _: self.changed(),
                    )
                    .props("filled dense")
                    .classes("w-40")
                )

    # -- reference data of a classification standard -----------------------
    @property
    def reference_field_count(self) -> int:
        """How many catalog fields are reference data of a standard."""
        return sum(1 for field in fields_of(self.catalog) if is_reference(field))

    def _render_reference_switch(self) -> None:
        """Offers the standard vocabulary in the field pickers (off by default).

        An imported classification standard brings hundreds of filterable fields
        (`attributes.EF…`); they are hidden so "which of my attributes do I
        filter by?" has an answer. The switch only changes the *offered* list —
        the core keeps those fields filterable, so a stored search using one
        still validates and shows its field.
        """
        count = self.reference_field_count
        if not count:
            return
        (
            ui.switch(t("search_form.show_reference").format(count=count))
            .props("dense")
            .classes("self-start")
            .tooltip(t("search_form.show_reference_hint"))
            .on_value_change(lambda event: self.set_show_reference(bool(event.value)))
        )

    def set_show_reference(self, show: bool) -> None:
        """Applies the switch and re-renders the pickers depending on it."""
        self.show_reference = show
        if self._root is not None:
            self._root.render()
        if self._sort is not None:
            self._sort.render()

    # -- state -------------------------------------------------------------
    def read_state(self) -> dict:
        """The current form state (sections that are not rendered stay empty)."""
        return {
            "where": self._root.read() if self._root is not None else new_group(),
            "fulltext": self._fulltext.value if self._fulltext is not None else "",
            "references": self._references.read() if self._references is not None else [],
            "sort": self._sort.read() if self._sort is not None else [],
            "start": int(self._start.value or 0) if self._start is not None else 0,
            "limit": (
                int(self._limit.value or default_limit(self.catalog))
                if self._limit is not None
                else default_limit(self.catalog)
            ),
        }

    def set_state(self, state: dict) -> None:
        """Fills the form from form state (e.g. after JSON edits)."""
        if self._root is not None:
            self._root.node = copy.deepcopy(state.get("where") or new_group())
            self._root.render()
        if self._fulltext is not None:
            self._fulltext.value = str(state.get("fulltext") or "")
        if self._references is not None:
            self._references.set_rows(state.get("references") or [])
        if self._sort is not None:
            self._sort.set_rows(state.get("sort") or [])
        if self._start is not None:
            self._start.value = int(state.get("start") or 0)
        if self._limit is not None:
            self._limit.value = int(state.get("limit") or default_limit(self.catalog))

    def changed(self) -> None:
        """Notifies the page that a value changed."""
        self._on_change()


class _GroupEditor:
    """Recursive editor for one condition group."""

    def __init__(
        self,
        form: SearchForm,
        node: dict,
        *,
        depth: int,
        on_remove: Callable[[], None] | None = None,
    ) -> None:
        self.form = form
        self.node = node
        self.depth = depth
        self.on_remove = on_remove
        self.container = ui.column().classes("w-full gap-1")
        self.render()

    def render(self) -> None:
        """(Re)builds the group and all of its children."""
        self.container.clear()
        with self.container:
            with ui.row().classes("w-full items-center gap-2"):
                ui.select(
                    {
                        "and": t("search_form.op_and"),
                        "or": t("search_form.op_or"),
                    },
                    value=str(self.node.get("op") or "and"),
                    label=t("search_form.group_op"),
                    on_change=lambda event: self._set_op(str(event.value or "and")),
                ).props("dense filled").classes("w-32")
                ui.button(
                    t("search_form.add_condition"),
                    icon="add",
                    on_click=self.add_condition,
                ).props("flat dense")
                if self.depth < max_depth(self.form.catalog):
                    ui.button(
                        t("search_form.add_group"),
                        icon="create_new_folder",
                        on_click=self.add_group,
                    ).props("flat dense")
                if self.on_remove is not None:
                    ui.button(icon="close", on_click=self.on_remove).props(
                        "flat dense color=negative"
                    )
            with ui.column().classes("w-full gap-1 pl-3 border-l-2 border-gray-300"):
                for child in self.node.get("conditions") or []:
                    if is_group(child):
                        _GroupEditor(
                            self.form,
                            child,
                            depth=self.depth + 1,
                            on_remove=partial(self._remove, child),
                        )
                    else:
                        _ConditionRow(
                            self.form,
                            child,
                            on_remove=partial(self._remove, child),
                        )

    def read(self) -> dict:
        """The current subtree."""
        return {
            "op": str(self.node.get("op") or "and"),
            "conditions": [copy.deepcopy(child) for child in self.node.get("conditions") or []],
        }

    def _set_op(self, op: str) -> None:
        self.node["op"] = op
        self.form.changed()

    def _remove(self, child: dict) -> None:
        self.node["conditions"] = [
            entry for entry in self.node.get("conditions") or [] if entry is not child
        ]
        self.render()
        self.form.changed()

    def add_condition(self) -> None:
        """Adds an empty condition row."""
        if count_conditions(self.read()) >= max_conditions(self.form.catalog):
            ui.notify(
                t("search_form.too_many_conditions", count=max_conditions(self.form.catalog)),
                color="warning",
            )
            return
        self.node.setdefault("conditions", []).append(new_condition(self.form.catalog))
        self.render()
        self.form.changed()

    def add_group(self) -> None:
        """Adds a nested group (with one condition) while depth allows."""
        group = new_group("or")
        group["conditions"].append(new_condition(self.form.catalog))
        self.node.setdefault("conditions", []).append(group)
        self.render()
        self.form.changed()


class _ConditionRow:
    """Editor for one condition (field · language · operator · value)."""

    def __init__(
        self,
        form: SearchForm,
        node: dict,
        *,
        on_remove: Callable[[], None],
    ) -> None:
        self.form = form
        self.node = node
        self.on_remove = on_remove
        self.container = ui.row().classes("w-full items-center gap-2")
        self.render()

    def render(self) -> None:
        """(Re)builds the row from the current node."""
        catalog = self.form.catalog
        self.container.clear()
        field_key = str(self.node.get("field") or "")
        with self.container:
            ui.select(
                field_choices(
                    catalog,
                    field_key or None,
                    include_reference=self.form.show_reference,
                ),
                value=field_key or None,
                label=t("search_form.condition_field"),
                on_change=lambda event: self._set_field(str(event.value or "")),
            ).props("dense filled").classes("w-64")
            field = field_by_key(catalog, field_key)
            if field is not None and field.get("multilingual"):
                ui.select(
                    language_choices(catalog),
                    value=split_language(catalog, field_key),
                    label=t("search_form.condition_language"),
                    on_change=lambda event: self._set_language(str(event.value or "")),
                ).props("dense filled").classes("w-36")
            operator_name = str(self.node.get("operator") or "")
            ui.select(
                operator_choices(catalog, field_key),
                value=operator_name or None,
                label=t("search_form.condition_operator"),
                on_change=lambda event: self._set_operator(str(event.value or "")),
            ).props("dense filled").classes("w-44")
            self._render_value(field, operator_name)
            ui.button(icon="delete", on_click=self.on_remove).props("flat dense color=negative")

    # -- value widget ------------------------------------------------------
    def _render_value(self, field: dict | None, operator_name: str) -> None:
        catalog = self.form.catalog
        operator = operator_of(catalog, operator_name)
        kind = widget_kind(field, operator)
        value = self.node.get("value")
        if kind == "none":
            ui.label(t("search_form.no_value")).classes("text-caption text-gray-500")
            return
        if kind == "multiselect":
            options = value_choices(catalog, self.node.get("field"))
            select = ui.select(
                options or {},
                value=list(value) if isinstance(value, list) else [],
                label=t("search_form.condition_value"),
                multiple=True,
                on_change=lambda event: self._set_value(list(event.value or [])),
            ).props("dense filled use-chips")
            if not options:
                select.props('new-value-mode="add-unique"')
            select.classes("w-64")
            return
        if kind.startswith("range-"):
            base = kind.split("-", 1)[1]
            low, high = value if isinstance(value, list) and len(value) == 2 else [None, None]
            with ui.row().classes("items-center gap-1"):
                self._scalar_widget(base, low, "search_form.value_from", self._set_low)
                ui.label("–").classes("text-gray-500")
                self._scalar_widget(base, high, "search_form.value_to", self._set_high)
            return
        self._scalar_widget(kind, value, "search_form.condition_value", self._set_value)

    def _scalar_widget(
        self,
        kind: str,
        value: Any,
        label_key: str,
        setter: Callable[[Any], None],
    ) -> None:
        label = t(label_key)
        if kind == "number":
            ui.number(label=label, value=value, on_change=lambda event: setter(event.value)).props(
                "dense filled"
            ).classes("w-32")
            return
        if kind == "switch":
            ui.switch(label, value=bool(value), on_change=lambda event: setter(bool(event.value)))
            return
        if kind == "select":
            options = value_choices(self.form.catalog, self.node.get("field")) or {}
            ui.select(
                options,
                value=value if value in options else None,
                label=label,
                on_change=lambda event: setter(event.value),
            ).props("dense filled").classes("w-44")
            return
        props = "filled dense clearable"
        if kind == "date":
            props += " type=date"
        ui.input(
            label=label,
            value="" if value is None else str(value),
            on_change=lambda event: setter(event.value),
        ).props(props).classes("w-44")

    # -- mutation ----------------------------------------------------------
    def _set_field(self, key: str) -> None:
        self.node["field"] = key
        allowed = operator_choices(self.form.catalog, key)
        if str(self.node.get("operator") or "") not in allowed:
            self.node["operator"] = default_operator(self.form.catalog, key)
            self.node["value"] = None
        self.render()
        self.form.changed()

    def _set_language(self, language: str) -> None:
        field = field_by_key(self.form.catalog, self.node.get("field"))
        if field is None:
            return
        self.node["field"] = with_language(str(field.get("key")), language)
        self.render()
        self.form.changed()

    def _set_operator(self, name: str) -> None:
        self.node["operator"] = name
        self.node["value"] = None
        self.render()
        self.form.changed()

    def _set_value(self, value: Any) -> None:
        self.node["value"] = _normalize_value(value)
        self.form.changed()

    def _set_low(self, value: Any) -> None:
        current = self.node.get("value")
        low, high = current if isinstance(current, list) and len(current) == 2 else [None, None]
        self.node["value"] = [_normalize_value(value), high]
        self.form.changed()

    def _set_high(self, value: Any) -> None:
        current = self.node.get("value")
        low, high = current if isinstance(current, list) and len(current) == 2 else [None, None]
        self.node["value"] = [low, _normalize_value(value)]
        self.form.changed()


def _normalize_value(value: Any) -> Any:
    """Trims text input and turns integral floats into ints."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


class _ReferenceSection:
    """Rows of reference (relation) restrictions."""

    def __init__(self, form: SearchForm) -> None:
        self.form = form
        self.rows: list[dict] = []
        self.host = ui.column().classes("w-full gap-1")
        ui.button(
            t("search_form.add_reference"),
            icon="add",
            on_click=self.add,
        ).props("flat dense")
        self.render()

    def set_rows(self, rows: list[dict]) -> None:
        self.rows = [copy.deepcopy(row) for row in rows]
        self.render()

    def read(self) -> list[dict]:
        return [copy.deepcopy(row) for row in self.rows]

    def add(self) -> None:
        self.rows.append(new_reference(self.form.catalog))
        self.render()
        self.form.changed()

    def render(self) -> None:
        catalog = self.form.catalog
        relations = (catalog or {}).get("references") or []
        choices = {
            str(relation.get("type")): resolve_value(relation.get("label"), current_locale())
            or str(relation.get("type"))
            for relation in relations
        }
        self.host.clear()
        with self.host:
            for row in self.rows:
                with ui.row().classes("w-full items-center gap-2"):
                    if str(row.get("type")) not in choices:
                        unknown = t("search_form.field_unknown")
                        choices[str(row.get("type"))] = f"{row.get('type')} ({unknown})"
                    ui.select(
                        choices,
                        value=str(row.get("type") or None),
                        label=t("search_form.reference_type"),
                        on_change=lambda event, row=row: self._set_type(row, str(event.value)),
                    ).props("dense filled").classes("w-52")
                    ui.label(str(row.get("label") or t("search_form.reference_none"))).classes(
                        "text-caption flex-grow truncate"
                    )
                    ui.button(
                        icon="account_tree",
                        on_click=lambda row=row: self._pick(row),
                    ).props("flat dense").tooltip(t("search_form.reference_pick"))
                    ui.select(
                        {
                            "incoming": t("search_form.reference_incoming"),
                            "outgoing": t("search_form.reference_outgoing"),
                        },
                        value=str(row.get("direction") or "incoming"),
                        label=t("search_form.reference_direction"),
                        on_change=lambda event, row=row: self._set_direction(row, str(event.value)),
                    ).props("dense filled").classes("w-56")
                    ui.button(icon="delete", on_click=lambda row=row: self.remove(row)).props(
                        "flat dense color=negative"
                    )

    def _set_type(self, row: dict, value: str) -> None:
        row["type"] = value
        self.form.changed()

    def _set_direction(self, row: dict, value: str) -> None:
        row["direction"] = value
        self.form.changed()

    async def _pick(self, row: dict) -> None:
        if self.form._client is None or not self.form._token:
            return
        relation: dict = next(
            (
                entry
                for entry in (self.form.catalog or {}).get("references") or []
                if str(entry.get("type")) == str(row.get("type"))
            ),
            {},
        )

        def _on_pick(hit: dict) -> None:
            row["out"] = str(hit.get("id") or "")
            row["label"] = str(hit.get("label") or hit.get("id") or "")
            self.render()
            self.form.changed()

        from meadow.frontend.object_picker import open_object_picker

        await open_object_picker(
            client=self.form._client,
            token=self.form._token,
            locale=current_locale(),
            title=t("search_form.reference_pick"),
            on_pick=_on_pick,
            target_types=[str(item) for item in relation.get("target_types") or []],
        )

    def remove(self, row: dict) -> None:
        self.rows = [entry for entry in self.rows if entry is not row]
        self.render()
        self.form.changed()


class _SortSection:
    """Rows of sort clauses."""

    def __init__(self, form: SearchForm) -> None:
        self.form = form
        self.rows: list[dict] = []
        self.host = ui.column().classes("w-full gap-1")
        ui.button(t("search_form.add_sort"), icon="add", on_click=self.add).props("flat dense")
        self.render()

    def set_rows(self, rows: list[dict]) -> None:
        self.rows = [copy.deepcopy(row) for row in rows]
        self.render()

    def read(self) -> list[dict]:
        return [copy.deepcopy(row) for row in self.rows]

    def add(self) -> None:
        self.rows.append(new_sort(self.form.catalog))
        self.render()
        self.form.changed()

    def render(self) -> None:
        self.host.clear()
        with self.host:
            for row in self.rows:
                with ui.row().classes("w-full items-center gap-2"):
                    ui.select(
                        sortable_choices(
                            self.form.catalog,
                            str(row.get("field") or None),
                            include_reference=self.form.show_reference,
                        ),
                        value=str(row.get("field") or None),
                        label=t("search_form.sort_field"),
                        on_change=lambda event, row=row: self._set_field(
                            row, str(event.value or "")
                        ),
                    ).props("dense filled").classes("w-64")
                    ui.select(
                        {"asc": t("search_form.sort_asc"), "desc": t("search_form.sort_desc")},
                        value=str(row.get("order") or "asc"),
                        label=t("search_form.sort_order"),
                        on_change=lambda event, row=row: self._set_order(
                            row, str(event.value or "asc")
                        ),
                    ).props("dense filled").classes("w-40")
                    ui.button(icon="delete", on_click=lambda row=row: self.remove(row)).props(
                        "flat dense color=negative"
                    )

    def _set_field(self, row: dict, value: str) -> None:
        row["field"] = value
        self.form.changed()

    def _set_order(self, row: dict, value: str) -> None:
        row["order"] = value
        self.form.changed()

    def remove(self, row: dict) -> None:
        self.rows = [entry for entry in self.rows if entry is not row]
        self.render()
        self.form.changed()
