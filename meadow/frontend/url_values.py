"""URL attribute values: validation, link handling and the media preview.

A `url` attribute stores a web address. The UI shows it as a link that opens in
a new tab — unless it points to an **image or video**, which is previewed in a
NiceGUI dialog instead (the file would otherwise be downloaded or shown bare).

The pure helpers at the top mirror the core's rule
(`crates/pim-core/src/attribute_values.rs`): only absolute `http(s)` URLs are
stored, so the UI never renders a link the browser cannot open. The core stays
authoritative — the helpers here are deliberately lenient (they only decide how
a value is *displayed*); the checks that reject a write live in the core.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from nicegui import ui

from meadow.frontend.i18n import t

#: Extensions shown in the image preview dialog.
IMAGE_EXTENSIONS = (
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "avif",
    "bmp",
    "svg",
    "ico",
)
#: Extensions shown in the video preview dialog.
VIDEO_EXTENSIONS = ("mp4", "webm", "ogv", "ogg", "mov", "m4v", "avi", "mkv")


def url_extension(url: object) -> str:
    """Lowercase file extension of a URL (`''` when it has none).

    Query strings and fragments are ignored, so
    `https://cdn.example.com/a/img.png?w=800` still counts as a PNG.
    """
    path = urlsplit(str(url or "")).path
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def media_kind(url: object) -> str | None:
    """Whether the URL points to an `image`, a `video` or nothing (`None`).

    Decides between the preview dialog and the new tab: only file extensions
    are inspected, a link to a page that *contains* a video keeps opening in
    the browser.
    """
    extension = url_extension(url)
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return None


def is_valid_url(value: object) -> bool:
    """Whether the value is an absolute `http(s)` URL (display-side check).

    Used to enable the "open" affordance next to an input; the core rejects
    everything else on write (`attribute_values::is_valid_url`).
    """
    text = str(value or "").strip()
    if not text:
        return False
    parts = urlsplit(text)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


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
