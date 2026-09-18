"""Shared dropdown filters for the table pages.

Every filter bar above a table needs a way **back to "no filter"**. Two spellings
were in use: a synthetic entry inside the option list (`{"": t("…all…"), …}`) or
nothing at all — in the latter case a user who picked a value once was stuck with
a choice, because Quasar offers no way to unset a `ui.select` without `clearable`
(reported from the field, 2026-09-13).

`filter_select` renders all of them the same way (`filled dense clearable`): the
✕ in the field clears it, which reads as "all". The cleared value is normalised
to `""` (single) or `[]` (multiple) by `normalised`, so a callback never sees
`None` and never stores the string `"None"` as a filter.

`normalised` is pure, so the rule is unit-tested; the widget wrapper is not (it
needs a NiceGUI slot).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from nicegui import ui

#: Value meaning "no selection" for a single-value filter.
ALL = ""


def normalised(value: object, *, multiple: bool = False) -> str | list[str]:
    """The filter value a callback should see (`""`/`[]` = no filter).

    A cleared single select yields `None` and a cleared multiple one an empty
    list; both become the empty spellings so the callbacks can stay simple.
    """
    if multiple:
        entries = value if isinstance(value, (list, tuple, set)) else []
        return [str(entry) for entry in entries if entry not in (None, "")]
    if value in (None, ""):
        return ALL
    return str(value)


def filter_select(
    options: Mapping[str, str] | Sequence[str],
    *,
    on_change: Callable[[Any], object | Awaitable[object]],
    label: str = "",
    value: str | None = None,
    multiple: bool = False,
    classes: str = "",
    tooltip: str = "",
) -> ui.select:
    """A table filter that can be cleared back to "no selection" (✕ = all).

    `value` is `None` for "no filter"; the callback receives `""`/`[]` for a
    cleared field (see `normalised`). A caller that wants an explicit "all"
    entry may keep it in `options` — the ✕ is added either way. `label` is
    optional: the grid's channel filter explains itself with a tooltip only.
    """
    # `ui.select` takes a concrete `list`/`dict`, so a `Mapping`/`Sequence` from a
    # caller is normalised here.
    choices: list[str] | dict[str, str] = (
        dict(options) if isinstance(options, Mapping) else list(options)
    )
    select = ui.select(choices, label=label or None, value=value, multiple=multiple)
    select.props("filled dense clearable").classes(classes)

    async def changed(event) -> None:
        # The wrapper is **async** on purpose: a callback may be a coroutine
        # function (it reloads from the core and touches UI), and NiceGUI only
        # awaits the handler it calls. Scheduling a detached task instead would
        # run the callback *outside* the client's slot context — creating UI or
        # calling `ui.run_javascript` then fails with "the current slot cannot be
        # determined" (verified 2026-09-13 on the grid's channel filter).
        result = on_change(normalised(event.value, multiple=multiple))
        if inspect.isawaitable(result):
            await result

    select.on_value_change(changed)
    if tooltip:
        select.tooltip(tooltip)
    return select
