"""Tests for the `url` attribute type (Phase 9).

The pure helpers live in the shared `meadow.urls` (used by the frontend and the
worker); the core validates URL values
(`crates/pim-core/src/attribute_values.rs`). The frontend mirrors the display
rule: a URL cell is a link that opens in a new tab, image/video URLs open in a
preview dialog instead.
"""

from __future__ import annotations

import pytest

from meadow.frontend.attribute_definitions import ATTR_TYPES
from meadow.frontend.grid import (
    EDITABLE_TYPES,
    MEDIA_FIELD,
    URL_CELL_RENDERER,
    attribute_column_specs,
    is_editable_definition,
    media_markers,
)
from meadow.frontend.i18n import t
from meadow.frontend.search_form import validate_state, widget_kind
from meadow.urls import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    is_valid_url,
    media_kind,
    url_extension,
)


def test_frontend_re_exports_the_shared_url_helpers() -> None:
    """The pages import the helpers from `url_values`; keep that path working."""
    from meadow.frontend import url_values

    assert url_values.media_kind is media_kind
    assert url_values.is_valid_url is is_valid_url
    assert url_values.url_extension is url_extension


def test_url_is_a_known_attribute_type() -> None:
    assert "url" in ATTR_TYPES
    assert "url" in EDITABLE_TYPES
    assert is_editable_definition({"name": "homepage", "type": "url"})
    # Computed URLs stay read-only like every other computed value.
    assert not is_editable_definition({"name": "homepage", "type": "url", "computed": True})


def test_url_extension_ignores_query_and_fragment() -> None:
    assert url_extension("https://cdn.example.com/a/photo.PNG?w=800") == "png"
    assert url_extension("https://example.com/clip.mp4#t=10") == "mp4"
    assert url_extension("https://example.com/folder/") == ""
    assert url_extension("https://example.com") == ""
    assert url_extension(None) == ""


@pytest.mark.parametrize("extension", IMAGE_EXTENSIONS)
def test_image_extension_is_media(extension: str) -> None:
    assert media_kind(f"https://cdn.example.com/pic.{extension}") == "image"


@pytest.mark.parametrize("extension", VIDEO_EXTENSIONS)
def test_video_extension_is_media(extension: str) -> None:
    assert media_kind(f"https://cdn.example.com/clip.{extension}") == "video"


def test_pages_and_unknown_suffixes_are_not_media() -> None:
    for url in (
        "https://example.com/product/1",
        "https://example.com/download.pdf",
        "https://example.com/player?file=video.mp4",
        "https://example.com/",
        "",
        None,
    ):
        assert media_kind(url) is None


def test_valid_url_mirrors_the_core_rule() -> None:
    assert is_valid_url("https://example.com")
    assert is_valid_url("http://localhost:8080/products")
    assert is_valid_url("  https://example.com/path?q=1  ")
    for value in (
        "example.com",
        "/products/1",
        "ftp://example.com/file",
        "javascript:alert(1)",
        "https://",
        "",
        None,
    ):
        assert not is_valid_url(value)


def test_url_columns_render_links() -> None:
    columns, language_fields = attribute_column_specs(
        [
            {"name": "homepage", "label": {"en": "Homepage"}, "type": "url", "multilingual": True},
            {"name": "title", "label": {"en": "Title"}, "type": "text"},
        ],
        "en",
        ["de"],
    )
    by_field = {column["field"]: column for column in columns}
    assert by_field["attr_homepage"][":cellRenderer"] == URL_CELL_RENDERER
    assert by_field["attr_homepage"]["editable"] is True
    # The per-language columns of a URL attribute are links as well.
    assert by_field["attr_homepage__de"][":cellRenderer"] == URL_CELL_RENDERER
    assert ":cellRenderer" not in by_field["attr_title"]
    assert language_fields == {"attr_homepage__de": "homepage"}


def test_media_markers_only_mark_media_cells() -> None:
    row = {
        "attr_homepage": "https://example.com/image.png",
        "attr_manual": "https://example.com/manual.pdf",
        "attr_empty": "",
        "attr_homepage__de": "https://example.com/clip.mp4",
    }
    markers = media_markers(row, set(row), "preview")
    assert markers == {
        "attr_homepage": "preview",
        "attr_homepage__de": "preview",
    }


def test_media_marker_field_name() -> None:
    assert MEDIA_FIELD == "_media"


def test_search_form_treats_url_as_text() -> None:
    field = {
        "key": "attributes.homepage",
        "value_type": "url",
        "operators": ["eq", "not_empty"],
    }
    operator = {"type": "eq", "arity": 1, "multi": False}
    assert widget_kind(field, operator) == "text"
    errors = validate_state(
        {
            "where": {
                "op": "and",
                "conditions": [
                    {"field": "attributes.homepage", "operator": "eq", "value": 42},
                ],
            },
            "limit": 1000,
        },
        {"fields": [field], "operators": [operator], "limits": {"max_conditions": 50}},
    )
    assert errors == ["query.where.conditions[0].value: must be a URL"]


@pytest.mark.parametrize("locale", ["en", "de", "fr"])
def test_url_i18n_keys_exist(locale: str) -> None:
    for key in (
        "url.open",
        "url.preview_hint",
        "url.preview_title",
        "url.open_in_new_tab",
        "url.close",
    ):
        text = t(key, locale=locale)
        assert text and text != key
