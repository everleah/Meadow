"""Pure display helpers shared by the frontend pages.

These helpers turn core values into human-readable HTML/text and carry no
NiceGUI or page state, so every page (grid, product form, admin pages) can use
the same rendering. Kept separate from the pages to avoid import cycles.
"""

from __future__ import annotations

import html

#: States offered for a channel release. The vocabulary is the object status
#: state machine (the core accepts any string; `workflow_guard` rules own the
#: transitions) — kept in sync with `form.py`'s status selector.
RELEASE_STATUSES = ("draft", "in_review", "published", "archived")

#: State that counts as "released for the channel" (default of the filters).
DEFAULT_RELEASE_STATUS = "published"


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


def completeness_badge(percent: int) -> str:
    """Returns a coloured badge for a completeness percentage.

    Grey for 0 % (nothing set), orange for 1-99 %, green for 100 %.
    """
    if percent >= 100:
        classes = "bg-positive text-white"
    elif percent >= 1:
        classes = "bg-orange text-white"
    else:
        classes = "bg-grey text-white"
    return f'<span class="px-2 py-0.5 rounded text-xs {classes}">{percent}%</span>'


def release_badges(releases: list[dict] | None, labels: dict[str, str] | None = None) -> str:
    """Returns one chip per channel release (channel label, state in the tooltip).

    ``releases`` is the ``releases`` field of an object (`[{channel, status}]`,
    `?releases=true`), ``labels`` maps channel codes to display names. The chip is
    coloured like a status badge, so a glance shows *for which channel* an object
    is released and in which state.
    """
    labels = labels or {}
    chips: list[str] = []
    for row in releases or []:
        if not isinstance(row, dict):
            continue
        channel = str(row.get("channel") or "")
        status = str(row.get("status") or "")
        if not channel:
            continue
        classes = {
            "published": "bg-primary text-white",
            "draft": "bg-neutral text-gray-600",
            "archived": "bg-info text-white",
        }.get(status, "bg-secondary text-white")
        name = html.escape(labels.get(channel, channel))
        tip = html.escape(f"{channel}: {status}")
        chips.append(
            f'<span class="px-2 py-0.5 mr-1 rounded text-xs {classes}" title="{tip}">{name}</span>'
        )
    return " ".join(chips)


def channel_label(channel: dict | None, locale: str) -> str:
    """Display label of a channel (falls back to its code)."""
    if not isinstance(channel, dict):
        return ""
    return resolve_value(channel.get("label"), locale) or str(channel.get("code") or "")


def channel_options(channels: list[dict], locale: str) -> dict[str, str]:
    """Select options `code -> label` for channel filters and pickers."""
    return {
        str(channel.get("code")): channel_label(channel, locale)
        for channel in channels
        if channel.get("code")
    }
