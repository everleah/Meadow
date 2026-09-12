"""URL helpers shared by the frontend and the worker (no NiceGUI, no state).

A `url` attribute stores an absolute `http(s)` URL (the core validates it on
write, see `crates/pim-core/src/attribute_values.rs`). These helpers decide how
such a value is *displayed* or exported:

* `media_kind` → is it an image/video (preview dialog in the UI, hyperlink in
  the XLSX export)?
* `is_valid_url` → does it look like an http(s) address at all (the UI disables
  its "open" affordance for values that do not)?

The core stays authoritative: `is_valid_url` is deliberately lenient (the
browser decides what it can open), while the checks that *reject a write* live
in the core.
"""

from __future__ import annotations

from urllib.parse import urlsplit

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

    Only the file extension is inspected: a link to a page that merely
    *contains* a video keeps opening in the browser.
    """
    extension = url_extension(url)
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return None


def is_valid_url(value: object) -> bool:
    """Whether the value is an absolute `http(s)` URL (display-side check).

    Used to enable the "open" affordance next to an input and to decide whether
    an export cell becomes a hyperlink. The core rejects everything else on
    write (`attribute_values::is_valid_url`).
    """
    text = str(value or "").strip()
    if not text:
        return False
    parts = urlsplit(text)
    return parts.scheme in ("http", "https") and bool(parts.netloc)
