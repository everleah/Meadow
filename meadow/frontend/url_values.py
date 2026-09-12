"""UI side of the `url` attribute type: opening links and previewing media.

The pure URL helpers live in `meadow.urls` (shared with the worker, no NiceGUI);
this module imports them for the pages and adds the NiceGUI parts:

* `open_url` — images/videos in the preview dialog, everything else in a new tab.
* `open_media_dialog` — the dialog with `ui.image`/`ui.video` plus "open in new
  tab"/"close".

`media_kind` stays the single source of truth for "is this an image/video?": the
grid cell renderer receives the decision through the row marker `_media` (see
`grid.py`).
"""

from __future__ import annotations

from nicegui import ui

from meadow.frontend.i18n import t
from meadow.urls import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    is_valid_url,
    media_kind,
    url_extension,
)

__all__ = [
    "IMAGE_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "is_valid_url",
    "media_kind",
    "open_media_dialog",
    "open_url",
    "url_extension",
]


def open_url(url: object) -> None:
    """Opens a URL: images/videos in a dialog, everything else in a new tab.

    A tab opened from a websocket event may be blocked by the browser, so the
    callers offer a real link (`ui.link(..., new_tab=True)`) wherever the user
    expects to leave the page.
    """
    text = str(url or "").strip()
    if not is_valid_url(text):
        return
    if media_kind(text):
        open_media_dialog(text)
        return
    ui.navigate.to(text, new_tab=True)  # noqa: TID251 - plain external link


def open_media_dialog(url: object) -> None:
    """Previews an image or video URL in a dialog.

    The raw file stays reachable through the "open in new tab" button, so a
    format the browser cannot render inline is never a dead end.
    """
    text = str(url or "").strip()
    kind = media_kind(text)
    with (
        ui.dialog() as dialog,
        ui.card().classes("w-[80vw] max-w-[64rem] max-h-[90vh] items-center gap-2"),
    ):
        ui.label(t("url.preview_title")).classes("text-h6")
        ui.label(text).classes("text-caption text-gray-500 break-all text-center")
        if kind == "video":
            ui.video(text).classes("w-full max-h-[65vh]").props("controls")
        else:
            ui.image(text).classes("max-h-[65vh]")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(
                t("url.open_in_new_tab"),
                icon="open_in_new",
                on_click=lambda: ui.navigate.to(text, new_tab=True),
            ).props("flat")
            ui.button(t("url.close"), on_click=dialog.close).props("flat")
    dialog.open()
